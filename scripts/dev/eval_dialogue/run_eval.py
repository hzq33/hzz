"""run_eval.py — 对话检索质量评估（L2：生产检索 + 代理指标）。

对 eval_seed.json 每条 case 跑三类检索：
  A. 通道检索（双模式）— store.search(query, channel=case.channel)
       global: 全库 top-15                     → 评全库检索能力（chat 生产场景）
       in_doc: 全库 top-30 → post-filter 角色作品 → top-5（口吻模仿生产场景）
  B. 路由检索   — NovelRetrieval.search_raw(query)（IntentRouter + rerank），仅报告路由分布
  C. 口吻模仿   — 复刻 ImpersonationRetrievalMixin._retrieve_style_samples：
                 style probe query + filters={"characters"} + doc post-filter + 说话人抽取

hit 判定使用 gold_variants（规范名全部变体，覆盖库内用字差异）。

输出：scripts/dev/eval_dialogue/data/results/<ts>_results.json

用法：
    PYTHONPATH="./venv/Lib/site-packages" ./venv/Scripts/python.exe scripts/dev/eval_dialogue/run_eval.py
"""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

_env = ROOT / ".env"
if _env.exists():
    load_dotenv(_env)

SEED_PATH = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "eval_seed.json"
RESULT_DIR = ROOT / "scripts" / "dev" / "eval_dialogue" / "data" / "results"

# 口吻模仿复刻参数（与 src/core/impersonation/chat.py 一致）
STYLE_TOP_K = 3        # 抽取台词条数上限
STYLE_FETCH_K = 8      # 检索块数
CHANNEL_SEARCH_K = 15  # 全库通道检索 top-k
INDOC_FETCH_K = 30     # 作品内模式：全库检索量 → post-filter
INDOC_K = 5            # 作品内 top-k


def _norm(s: str) -> str:
    return "".join(s.split()).replace("・", "").replace("·", "").replace("、", "").replace("，", "").replace(",", "").replace("—", "")


def style_search_query(character: str, catchphrases: list[str], context: str) -> str:
    """复刻 ImpersonationRetrievalMixin._style_search_query。"""
    ctx = (context or "").strip()
    if ctx and len(ctx) < 20:
        return f"{character} {ctx}"
    if catchphrases:
        return f"{character} {catchphrases[0]}"
    return f"{character} 对话 语气"


def _block_lines(block, speaker_set: set[str] | None = None) -> list[dict]:
    """块内台词（可带说话人过滤）。"""
    out: list[dict] = []
    for d in (block.dialogues or []):
        sp = str(getattr(d, "speaker", "") or "").strip()
        ct = str(getattr(d, "content", "") or "").strip()
        if not ct:
            continue
        if speaker_set is not None and _norm(sp) not in speaker_set:
            continue
        out.append({"speaker": sp, "content": ct})
    return out


def _hit_needles(blob: str, variants: dict[str, list[str]]) -> bool:
    """blob 命中任一 gold 关键词的任一变体。"""
    return any(any(v and v in blob for v in vlist) for vlist in variants.values())


def _speaker_hit_at_k(hits: list, gold_speaker: str, aliases: list[str], k: int = 5) -> float:
    """仅检查目标角色台词是否出现在 TopK 检索块的 dialogues 中。

    解决原 channel_hit_at_5 检查整块文本导致的假阳性问题：
    多角色对话块中任一角色提到 gold 关键词即算命中，但实际检索目的
    是找到目标角色说的台词。本指标精确到 speaker 级。

    Returns:
        1.0 若目标角色台词出现在 TopK；0.0 否则。
    """
    target_names = {_norm(gold_speaker)} if gold_speaker else set()
    for a in aliases or []:
        target_names.add(_norm(a))
    target_names.discard("")
    if not target_names:
        return 0.0
    for h in hits[:k]:
        block = h.get("block", h) if isinstance(h, dict) else h
        if isinstance(block, dict):
            dialogues = block.get("dialogues") or []
        else:
            dialogues = getattr(block, "dialogues", []) or []
        for d in dialogues:
            if isinstance(d, dict):
                sp = _norm(str(d.get("speaker", "") or ""))
            else:
                sp = _norm(str(getattr(d, "speaker", "") or ""))
            if sp in target_names:
                return 1.0
    return 0.0


async def _semantic_overlap(query: str, hits: list, embedder, k: int = 5) -> float | None:
    """query 与 TopK 检索内容的 embedding cosine 相似度均值。

    解决"关键词命中但语义无关"的代理指标盲区。
    当 embedder 为 None 时返回 None（无 embedding 模型时跳过）。
    """
    if not embedder or not hits:
        return None

    texts: list[str] = []
    for h in hits[:k]:
        block = h.get("block", h) if isinstance(h, dict) else h
        if isinstance(block, dict):
            text = (block.get("text") or "") + " " + " ".join(
                d.get("content", "") for d in (block.get("dialogues") or [])
            )
        else:
            text = (getattr(block, "narrative_text", "") or "") + " " + " ".join(
                getattr(d, "content", "") for d in (getattr(block, "dialogues", []) or [])
            )
        texts.append(text.strip()[:500])
    if not any(texts):
        return None
    try:
        all_texts = [query] + texts
        result = await embedder.embed_texts(all_texts)
        if not result.embeddings or len(result.embeddings) < 2:
            return None

        query_vec = result.embeddings[0]
        doc_vecs = result.embeddings[1:]
        sims = []
        for dv in doc_vecs:
            dot = sum(a * b for a, b in zip(query_vec, dv))
            norm_q = math.sqrt(sum(a * a for a in query_vec))
            norm_d = math.sqrt(sum(b * b for b in dv))
            if norm_q > 0 and norm_d > 0:
                sims.append(dot / (norm_q * norm_d))
        return round(sum(sims) / len(sims), 4) if sims else None
    except Exception:
        return None


def _ndcg_at_k(hits: list, gold_variants: dict, k: int = 5) -> float | None:
    """对有 gold 排序的 case 计算 NDCG@K。

    relevance 分级：
    - 命中 gold_variants 的第 1 组（最相关）= 3
    - 命中 gold_variants 的第 2 组（次相关）= 2
    - 命中 gold_variants 的第 3+ 组 = 1
    - 未命中 = 0

    Returns:
        NDCG@K 值，若无 gold_variants 返回 None。
    """
    if not gold_variants:
        return None

    def _gain(blob: str) -> float:
        for idx, (_, vlist) in enumerate(gold_variants.items()):
            if any(v and v in blob for v in vlist):
                return max(3.0 - idx, 1.0)
        return 0.0

    dcg = 0.0
    for i, h in enumerate(hits[:k]):
        blob = _blob(h) if isinstance(h, dict) else ""
        gain = _gain(blob)
        dcg += (2 ** gain - 1) / math.log2(i + 2)

    # 理想排序：所有相关文档排在最前
    ideal_gains = sorted(
        [_gain(_blob(h)) for h in hits], reverse=True
    )[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal_gains) if g > 0)
    if idcg == 0:
        return 0.0
    return round(dcg / idcg, 4)


def _blob(h) -> str:
    b = h["block"]
    return b["text"] + " " + " ".join(d["content"] for d in b["dialogues"])


def _render_block(b, max_len: int = 200) -> dict:
    lines = _block_lines(b)
    text = (b.narrative_text or "") + " " + (b.scene or "") + " " + (b.question or "") + " " + (b.answer or "")
    return {
        "global_id": b.global_id,
        "block_type": b.block_type,
        "doc_id": b.doc_id or "",
        "chapter_title": b.chapter_title or "",
        "text": (text or "").strip()[:max_len],
        "dialogues": lines,
    }


def _in_doc(h: dict, doc_prefix: str) -> bool:
    return bool(doc_prefix) and (h["block"].get("doc_id") or "").startswith(doc_prefix)


async def run_channel_search(retrieval, query: str, channel: str, doc_prefix: str) -> dict:
    """路径 A 双模式通道检索。

    global: 全库 top-15（不传 doc_prefix）
    in_doc: 全库 top-30 → post-filter 角色作品 → top-5
            当 doc_prefix 提供时，优先用 doc_prefix 做 LanceDB 前过滤
            （避免全库扫描），失败则回退到后过滤。
    """
    # global 模式：全库检索，不传 doc_prefix
    hits = await retrieval.store.search(query, channel=channel, top_k=CHANNEL_SEARCH_K)
    global_hits = [{"score": float(h.score), "block": _render_block(h.block)} for h in hits]

    # in_doc 模式：优先 doc_prefix 前过滤（LanceDB WHERE doc_id LIKE 'prefix%'）
    in_doc_hits: list = []
    if doc_prefix:
        try:
            # 用 doc_prefix 前过滤，减少全库扫描
            pref_hits = await retrieval.store.search(
                query,
                channel=channel,
                top_k=INDOC_FETCH_K,
                filters={"doc_prefix": doc_prefix},
            )
            in_doc_hits = [{"score": float(h.score), "block": _render_block(h.block)} for h in pref_hits]
        except Exception:
            # 前过滤不支持时回退到后过滤
            in_doc_hits = [h for h in global_hits if _in_doc(h, doc_prefix)]
    else:
        in_doc_hits = global_hits

    return {"global": global_hits, "in_doc": in_doc_hits[:INDOC_K]}


async def run_routed_search(retrieval, query: str) -> tuple[dict, list]:
    """路径 B：路由分布报告——仅调 router.aclassify，不跑完整 search_raw。

    原实现调 search_raw（rewrite + HyDE + 多变体检索 + rerank），但只取
    primary_channel 和 channel_weights，每 case 浪费 ~16 次 search + 2 次 LLM。
    改为只调 router.aclassify，与 search_raw 入口的路由逻辑一致。
    """
    # 获取 available_characters（与 search_raw 一致的来源）
    try:
        chars = retrieval.store.list_characters(None)
    except Exception:
        chars = None

    # 若有 EntityResolver，产出 QueryContext 传给 router（与 search_raw 一致）
    query_context = None
    if retrieval.entity_resolver:
        try:
            query_context = retrieval.entity_resolver.resolve(query)
        except Exception:
            pass

    if hasattr(retrieval.router, "aclassify"):
        intent = await retrieval.router.aclassify(query, chars, query_context=query_context)
    else:
        intent = retrieval.router.classify(query, chars)

    return (
        {"primary_channel": intent.primary_channel, "channels": sorted(intent.channel_weights.keys())},
        [],  # B 路不再产出 hits，hits 由 A 路和 C 路覆盖
    )


async def run_style_search(retrieval, case: dict, chars_section: dict) -> dict:
    """路径 C：口吻模仿窄检索（复刻 impersonation style 检索 + doc post-filter）。"""
    char = case.get("character") or ""
    if not char or case.get("channel") != "dialogue":
        return {"skipped": True, "turns": [], "blocks": []}

    info = chars_section.get(char, {})
    speakers = set(info.get("speakers") or [])
    aliases = info.get("aliases") or [char]
    catchphrases = info.get("catchphrases") or []
    doc_prefix = case.get("doc_prefix") or ""

    query = style_search_query(char, catchphrases, case["query"])
    hits = await retrieval.store.search(
        query,
        channel="dialogue",
        top_k=STYLE_FETCH_K,
        filters={"characters": aliases} if aliases else None,
    )
    if not hits:
        # 与 impersonation 一致：LIKE 预过滤过严时去掉 filters 重试
        hits = await retrieval.store.search(query, channel="dialogue", top_k=STYLE_FETCH_K)

    # doc post-filter（模拟生产 doc_id 过滤）
    if doc_prefix:
        filtered = [h for h in hits if (getattr(h.block, "doc_id", "") or "").startswith(doc_prefix)]
        if filtered:
            hits = filtered

    blocks = [{"score": float(h.score), "block": _render_block(h.block)} for h in hits]
    turns: list[dict] = []
    seen_norms: set[str] = set()
    for h in hits:
        for t in _block_lines(h.block, speaker_set=speakers):
            n = _norm(t["content"])
            if not n or n in seen_norms:
                continue
            seen_norms.add(n)
            turns.append({"speaker": t["speaker"], "content": t["content"], "score": float(h.score)})
            if len(turns) >= STYLE_TOP_K:
                break
        if len(turns) >= STYLE_TOP_K:
            break

    all_turn_count = sum(len(_block_lines(h.block)) for h in hits)
    return {
        "skipped": False,
        "style_query": query,
        "filters_used": bool(aliases),
        "doc_filtered": bool(doc_prefix),
        "turns": turns,
        "blocks": blocks,
        "all_turn_count": all_turn_count,
    }


def compute_metrics(case: dict, ch: dict, routed: dict, style: dict, style_speakers: set[str]) -> dict:
    variants = case.get("gold_variants") or {}
    has_gold = bool(variants)
    m: dict = {}

    # 全库通道检索
    g_blobs = [_blob(h) for h in ch["global"]]
    m["channel_hit_at_5"] = int(_hit_needles(" ".join(g_blobs[:5]), variants)) if has_gold else None
    m["channel_hit_at_15"] = int(_hit_needles(" ".join(g_blobs), variants)) if has_gold else None

    # speaker 级精确命中（解决原 channel_hit_at_5 整块文本假阳性）
    gold_speaker = case.get("character") or ""
    gold_aliases = list(style_speakers) if style_speakers else [gold_speaker]
    m["speaker_hit_at_5"] = _speaker_hit_at_k(ch["global"], gold_speaker, gold_aliases, k=5) if gold_speaker else None
    m["speaker_hit_at_15"] = _speaker_hit_at_k(ch["global"], gold_speaker, gold_aliases, k=15) if gold_speaker else None

    # NDCG@5（对有 gold_variants 的 case 计算排序质量）
    m["ndcg_at_5"] = _ndcg_at_k(ch["global"], variants, k=5) if has_gold else None

    # 语义重叠（query 与 TopK 检索内容的 embedding 相似度均值）
    # 由 main() 在 compute_metrics 后异步填充（需要 embedder 调用）
    m["semantic_overlap_at_5"] = None

    # 作品内通道检索
    i_blobs = [_blob(h) for h in ch["in_doc"]]
    m["indoc_hit_at_5"] = int(_hit_needles(" ".join(i_blobs), variants)) if has_gold else None
    m["indoc_coverage"] = round(len(ch["in_doc"]) / INDOC_K, 3)  # 作品内块覆盖（0=作品内无块被召回）

    # 路由：仅报告分布（不当作质量指标）
    m["routed_channel"] = routed["intent"]["primary_channel"]
    m["routed_channels"] = routed["intent"]["channels"]

    # 口吻模仿
    if style.get("skipped"):
        m["speaker_hit"] = None
        m["speaker_block_coverage"] = None
    else:
        m["speaker_hit"] = int(bool(style["turns"]))
        # 块级覆盖率：top-k 检索块中含目标角色台词的块占比（整章块下句级纯度失真）
        m["speaker_block_coverage"] = round(
            sum(1 for b in style["blocks"] if any(_norm(t["speaker"]) in style_speakers for t in b["block"]["dialogues"]))
            / max(1, len(style["blocks"])),
            3,
        ) if style["blocks"] else None

    # 相关性代理：作品内 top-1 命中
    m["rel_proxy"] = m["indoc_hit_at_5"] if has_gold else None
    return m


async def main() -> None:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    cases = seed["cases"]
    chars_section = seed["characters"]

    n_dlg = sum(1 for c in cases if c["channel"] == "dialogue")
    print(f"加载评估集：{len(cases)} case（dialogue {n_dlg} / narrative {len(cases) - n_dlg}）", flush=True)
    print("装配生产检索管道（LanceDB + Qwen3 embedding + reranker）…", flush=True)

    from src.application.novel.factory import create_novel_retrieval

    retrieval = create_novel_retrieval()
    inner = getattr(retrieval.store, "_vectors", retrieval.store)
    print(f"  embedding: {type(inner.embedding).__name__}", flush=True)
    print(f"  reranker:  {type(retrieval.reranker).__name__} / top_n={retrieval.reranker.top_n if hasattr(retrieval.reranker, 'top_n') else '?'}", flush=True)
    print(f"  top_k:     {retrieval.top_k}", flush=True)
    print(f"  entity_resolver: {type(retrieval.entity_resolver).__name__ if retrieval.entity_resolver else 'None'}", flush=True)

    t0 = time.time()
    results = []
    routed_dist = {}
    for i, case in enumerate(cases, 1):
        q = case["query"]
        ch = case["channel"]
        channel_res = await run_channel_search(retrieval, q, ch, case.get("doc_prefix") or "")
        routed_intent, routed_hits = await run_routed_search(retrieval, q)
        style = await run_style_search(retrieval, case, chars_section)
        style_speakers = set((chars_section.get(case.get("character") or "", {}) or {}).get("speakers") or [])
        metrics = compute_metrics(case, channel_res, {"intent": routed_intent, "hits": routed_hits}, style, style_speakers)
        # 语义重叠：用 A 路 global hits 的 embedder 计算（async 调用）
        metrics["semantic_overlap_at_5"] = await _semantic_overlap(
            q, channel_res["global"], inner.embedding, k=5
        )
        routed_dist[metrics["routed_channel"]] = routed_dist.get(metrics["routed_channel"], 0) + 1

        results.append(
            {
                "case_id": case["id"],
                "query": q,
                "channel": ch,
                "intent": case["intent"],
                "character": case.get("character") or "",
                "gold_keywords": case.get("gold_keywords") or [],
                "gold_variants": case.get("gold_variants") or {},
                "doc_prefix": case.get("doc_prefix") or "",
                "metrics": metrics,
                "channel_search": channel_res,
                "routed": {"intent": routed_intent, "hits": routed_hits},
                "style": style,
            }
        )
        if i % 10 == 0 or i == len(cases):
            print(f"  [{i}/{len(cases)}] {time.time()-t0:.0f}s elapsed", flush=True)
        elif i % 5 == 0:
            print(f"  [{i}/{len(cases)}] {time.time()-t0:.0f}s", flush=True)

    def rate(key: str, subset=None):
        rs = results if subset is None else [r for r in results if r["channel"] == subset]
        vals = [r["metrics"][key] for r in rs if r["metrics"][key] is not None and isinstance(r["metrics"][key], (int, float))]
        return (sum(vals) / len(vals), len(vals), len(rs)) if vals else (None, 0, len(rs))

    print("\n=== 代理指标汇总（L2）===")
    for key, label in [
        ("channel_hit_at_5", "全库通道top5命中"),
        ("channel_hit_at_15", "全库通道top15命中"),
        ("speaker_hit_at_5", "speaker级top5命中"),
        ("speaker_hit_at_15", "speaker级top15命中"),
        ("ndcg_at_5", "NDCG@5排序质量"),
        ("semantic_overlap_at_5", "语义重叠@5均值"),
        ("indoc_hit_at_5", "作品内top5命中"),
        ("indoc_coverage", "作品内块覆盖(均值)"),
        ("speaker_hit", "口吻角色台词命中"),
        ("speaker_block_coverage", "口吻块级覆盖(均值)"),
    ]:
        v, n, tot = rate(key)
        vd, nd, totd = rate(key, "dialogue")
        vn, nn, totn = rate(key, "narrative")
        print(f"  {label:<14} 全部={v if v is None else f'{v:.2%}'} ({n}/{tot}) | dialogue={vd if vd is None else f'{vd:.2%}'} ({nd}/{totd}) | narrative={vn if vn is None else f'{vn:.2%}'} ({nn}/{totn})")

    print(f"\n路由分布（IntentRouter primary_channel）：{dict(sorted(routed_dist.items()))}")

    # 失败 case 初筛（作品内 top5 未命中且有关键词）
    print("\n=== 疑似失败 case（作品内top5未命中且有关键词）===")
    for r in results:
        m = r["metrics"]
        if r["gold_keywords"] and m["indoc_hit_at_5"] == 0:
            cov = m["indoc_coverage"]
            tag = "作品内无块被召回" if cov == 0 else f"覆盖{cov:.0%}但未命中"
            print(f"  {r['case_id']:<16} [{r['channel']}] {tag} | kws={r['gold_keywords'][:2]} | {r['query'][:30]}")

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=str(ROOT)).stdout.strip()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    payload = {
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "git_sha": sha,
            "embedding": type(inner.embedding).__name__,
            "reranker": type(retrieval.reranker).__name__,
            "top_k": retrieval.top_k,
            "lance_path": "data/novel_lance",
            "seed_version": seed.get("version"),
            "case_count": len(cases),
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "seed": {"version": seed.get("version"), "built_from": seed.get("built_from")},
        "results": results,
    }
    out_path = RESULT_DIR / f"{ts}_results.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果缓存：{out_path}")


if __name__ == "__main__":
    asyncio.run(main())
