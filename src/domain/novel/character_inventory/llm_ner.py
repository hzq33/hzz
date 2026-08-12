"""LLM 全量盘点粗召回 — 替代 CLUENER 本地模型的角色名提取。

A/B 实测（败犬 vol01 前 8 章，21 万字符）：
  CLUENER      召回 72.2%  噪声 4（作家名）  长名截断（月之木学）  耗时 40.7s
  LLM 全量盘点  召回 66.7%  噪声 0           长名完整（月之木古都） 耗时 3.8s

设计：
  一次 LLM 调用扫全文，提取全部角色名（说话人 + 被提及者）。
  extract_names_llm   → 名字列表（含防幻觉/坏 JSON 容错）
  mentions_from_names → 在原文定位出现位置，构造 Mention（复用 character_ner.Mention）
                        → 下游 cluster_mentions / _llm_normalize_global 全部复用，零改动。

配置（config.yaml → novel_rag.character_inventory）：
  ner: "llm" | "cluener"   （默认 llm；llm 不可用时降级 cluener）
  llm_max_names: 60
  llm_max_tokens: 2048
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger("agent")

SYSTEM_PROMPT = """你是轻小说实体盘点助手。从章节文本中提取【核心实体】与【实体间关系】，并挂接【属性】。

核心原则：只有能作为关系端点（谁对谁怎样）的名词才是核心实体；其余名词是属性，必须挂在实体上，绝不单独输出。

【核心实体 entities】只两类：
- type="person"：有独立人格/意识的具体个体（有说话、有行为归属、被他人点名）
- type="speaking_skill"：会说话的技能/能力/系统音（有明确名字与真实台词，如史莱姆的「大贤者」「捕食者」）

实体规则：
- 输出纯名：去敬称/称号（"利姆露大人"→"利姆露"；"魔王雷昂"→"雷昂"）
- 同角色出现简称/全名时都列出（合并交给归一）；名字可任意长度（中文/翻译名）
- 不确定的不要编造：没有文本依据的名字不列
- 忽略：他/她/众人/少年 等无具体身份的指代词

【属性 attributes】非端点名词全部作为某实体的 attributes 输出，type 限以下 7 类：
- "role"：身份/泛称/职务（勇者、魔王、长老、商人、老师）
- "race"：种族/物种（矮人、妖精、史莱姆）
- "title"：专有称号（爆焰支配者）
- "location"：国家/城镇/森林/地名（朱拉大森林、东方帝国、坦派斯特）
- "org"：公会/教会/组织（自由公会、冒险者互助会）
- "skill_attr"：非说话的技能/抗性/魔法（对寒抗性、操水术、物理攻击抗性）
- "item"：普通物品/材料（火焰短剑、魔矿石）
属性规则：
- 每个 attribute 的 value 用原文出现的词
- 同一实体的多个属性都列出；不把属性单独列为 entity
- 作者/插画师/注释人物（如作者名）直接排除，不输出

【人物关系 relations】规则：
- source/target 必须是上面 entities 中的 name（同一名字用最长最正式形式）
- 关系仅在实体之间：person↔person、person↔speaking_skill
- relation 用标准分类词优先（师徒/父子/母子/兄妹/恋人/挚友/对手/敌人/主从/同事/同族 等），
  可附自由描述，如"师徒：三上悟是优树的导师"
- evidence 引用本章原文依据（一句话即可，必须真实出现在文本中）
- 同一对实体多种关系可输出多条；不确定的关系不要编造

只输出 JSON 对象（不要 markdown 围栏，不要前后解释文字）：
{"entities": [{"name": "利姆露", "type": "person", "attributes": [{"type": "race", "value": "史莱姆"}]}, {"name": "大贤者", "type": "speaking_skill", "attributes": []}],
 "relations": [{"source": "利姆露", "target": "大贤者", "relation": "主从", "evidence": "「大贤者」的声音在我心中响起"}]}"""


def _dedupe_attrs(attrs: Sequence[dict]) -> list[dict]:
    """属性去重：同 type+value 只保留一次（跨批合并用）。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for a in attrs or []:
        if not isinstance(a, dict):
            continue
        key = (str(a.get("type") or "").strip(), str(a.get("value") or "").strip())
        if key[0] and key[1] and key not in seen:
            seen.add(key)
            out.append({"type": key[0], "value": key[1]})
    return out


def _parse_names(raw: str) -> list[dict[str, Any]]:
    """容错解析 LLM 输出（code fence → 纯 JSON → 首尾 {} 截取）。

    兼容三种格式（V4 实体本体）：
      - 新：{"entities": [{"name": "利姆露", "type": "person", "attributes": [...]}, ...]}
      - 旧新混合：{"names": [{"name": "八奈见", "type": "角色"}, ...]}
      - 旧：{"names": ["八奈见", "温水"]} → type 默认 "person"
    返回：
      [{"name", "type", "attributes"}]；attributes 为 [{"type", "value"}] 列表（可能为空）。
    """
    text = (raw or "").strip()
    if not text:
        return []
    data: dict[str, Any] | None = None
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    candidates = [m.group(1).strip() if m else None, text if text.startswith("{") else None]
    for cand in candidates:
        if not cand:
            continue
        try:
            data = json.loads(cand)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        m2 = re.search(r"\{.*\}", text, re.DOTALL)
        if m2:
            try:
                data = json.loads(m2.group())
            except json.JSONDecodeError:
                data = None
    if not isinstance(data, dict):
        return []

    def _norm_attrs(raw_attrs: Any) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for a in raw_attrs or []:
            if not isinstance(a, dict):
                continue
            at = str(a.get("type") or "").strip()
            av = str(a.get("value") or "").strip()
            if at and av:
                out.append({"type": at, "value": av})
        return out

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # V4：entities[]（带 attributes）优先
    items = (data or {}).get("entities") or (data or {}).get("names") or []
    for n in items:
        if isinstance(n, dict):
            name = str(n.get("name") or "").strip()
            typ = str(n.get("type") or "person").strip() or "person"
            attrs = _norm_attrs(n.get("attributes"))
        else:
            name = str(n).strip()
            typ = "person"
            attrs = []
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "type": typ, "attributes": attrs})
    return out


def _parse_relations(raw: str) -> list[dict[str, str]]:
    """解析联合输出中的 relations（同 _parse_names 的容错路径）。

    Returns:
        [{"source", "target", "relation", "evidence"}]；缺失字段补空串。
    """
    text = (raw or "").strip()
    if not text:
        return []
    data: dict[str, Any] | None = None
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    candidates = [m.group(1).strip() if m else None, text if text.startswith("{") else None]
    for cand in candidates:
        if not cand:
            continue
        try:
            data = json.loads(cand)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        m2 = re.search(r"\{.*\}", text, re.DOTALL)
        if m2:
            try:
                data = json.loads(m2.group())
            except json.JSONDecodeError:
                data = None
    out: list[dict[str, str]] = []
    for r in (data or {}).get("relations") or []:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source") or "").strip()
        tgt = str(r.get("target") or "").strip()
        rel = str(r.get("relation") or "").strip()
        ev = str(r.get("evidence") or "").strip()
        if src and tgt and src != tgt:
            out.append({"source": src, "target": tgt, "relation": rel, "evidence": ev})
    return out


async def extract_names_llm(
    text: str,
    llm_client: Any,
    *,
    max_names: int = 60,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """一次 LLM 调用：联合提取说话人名单 + 人物关系。

    Returns:
        {"names": [{"name", "type"}], "relations": [{"source", "target", "relation", "evidence"}]}
        失败/不可解析 → 空列表（调用方决定降级）。
    """
    if llm_client is None or not (text or "").strip():
        return {"names": [], "relations": []}
    # 输入上限保护：12 万字符（128K context 余量，避免超窗）
    body = text if len(text) <= 120_000 else text[:120_000]
    try:
        raw = await llm_client.achat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": body + "\n\n请输出 JSON。"},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort, caller degrades
        logger.warning("LLM full-scan extraction failed: %s", exc)
        return {"names": [], "relations": []}
    names = _parse_names(raw or "")
    if len(names) > max_names:
        names = names[:max_names]
    relations = _parse_relations(raw or "")
    return {"names": names, "relations": relations}


def chapter_names_from_inventory(
    chapter_text: str,
    names: Sequence[str],
    *,
    min_len: int = 2,
) -> list[str]:
    """从 inventory 名单筛出本章实际出现的名字（canonical + aliases 已展开）。

    替代每章 harvest LLM 调用：名单 ∩ 本章文本定位（零 LLM 调用）。
    复用 mentions_from_names 的长名优先逻辑；返回按出现顺序去重的名字。
    """
    if not chapter_text or not names:
        return []
    mentions = mentions_from_names(chapter_text, list(names), min_len=min_len)
    out: list[str] = []
    for m in mentions:
        n = str(getattr(m, "text", "") or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def _group_chapters_by_chars(
    chapters: Sequence[Any],
    batch_chars: int,
) -> list[list[Any]]:
    """按累计字符数把章节分成若干批（每批若干完整章，带标题拼接）。

    章边界是安全边界：角色名在一章内完整出现，不会跨批切半。
    每批至少 1 章（单章超限时单独成批，不截断章内文本）。
    """
    batches: list[list[Any]] = []
    cur: list[Any] = []
    cur_chars = 0
    for ch in chapters:
        t = (getattr(ch, "text", None) or "").strip()
        title = (getattr(ch, "title", None) or "").strip()
        ch_len = len(t) + len(title) + 8
        if cur and cur_chars + ch_len > batch_chars:
            batches.append(cur)
            cur = []
            cur_chars = 0
        cur.append(ch)
        cur_chars += ch_len
    if cur:
        batches.append(cur)
    return batches or [[]]


def _join_chapters(chapters: Sequence[Any]) -> str:
    """章节拼接（与 _document_text 一致的【标题】格式）。"""
    parts: list[str] = []
    for ch in chapters:
        t = (getattr(ch, "text", None) or "").strip()
        if not t:
            continue
        title = (getattr(ch, "title", None) or "").strip()
        parts.append(f"【{title}】\n{t}" if title else t)
    return "\n\n".join(parts)


async def extract_names_by_chapter_batches(
    chapters: Sequence[Any],
    llm_client: Any,
    *,
    batch_chars: int = 60000,
    max_names: int = 60,
    max_tokens: int = 4096,
    concurrency: int = 1,
) -> dict[str, Any]:
    """按章分批联合提取：说话人名单 + 人物关系。

    设计：
      - 每批若干完整章（≤batch_chars 字符），带【标题】拼接，语义完整
      - 批次间并发（concurrency，默认 1 串行；NVIDIA 免费端点 4 并发无 429）
      - 章边界零切半：名字在一章内完整出现
      - names：跨批去重（同名字保留全部 type 标注，归一按多数裁决）
      - relations：跨批按（source,target 排序对）合并——evidence 累积多章、
        first_chapter 取最早出现章节、chapter_count 累加出现批次的章数

    Returns:
        {
          "names": [{"name", "types": [...]}],
          "relations": [{"source", "target", "relation",
                          "evidence": [str,...], "first_chapter": int, "chapter_count": int}],
        }
    """
    chapters = list(chapters or [])
    if not chapters:
        return {"names": [], "relations": []}
    batches = _group_chapters_by_chars(chapters, max(1, int(batch_chars)))
    by_name: dict[str, set[str]] = {}
    attrs_by_name: dict[str, list[dict]] = {}
    rel_map: dict[tuple[str, str], dict[str, Any]] = {}
    total = len(batches)

    # 并发执行各批；每批结果带序号，保证合并顺序稳定（first_chapter 语义）
    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _run_batch(bi: int, batch: Sequence[Any]) -> tuple[int, dict]:
        async with sem:
            text = _join_chapters(batch)
            if not text.strip():
                return bi, {"names": [], "relations": []}
            try:
                res = await extract_names_llm(
                    text,
                    llm_client,
                    max_names=max_names,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - 单批失败不阻断后续批
                logger.warning("Batch %d/%d extraction failed: %s", bi, total, exc)
                res = {"names": [], "relations": []}
            return bi, res

    batch_results = await asyncio.gather(
        *[_run_batch(bi, b) for bi, b in enumerate(batches, 1)]
    )
    for bi, res in batch_results:
        text = _join_chapters(batches[bi - 1])
        # 批的起始章节号（1-based，供关系时间戳）
        first_ci = chapters.index(batches[bi - 1][0]) + 1
        for d in res.get("names") or []:
            name = (d.get("name") or "").strip()
            typ = (d.get("type") or "person").strip() or "person"
            if name:
                by_name.setdefault(name, set()).add(typ)
                # 跨批合并属性（去重：同 type+value 只留一次）
                for a in d.get("attributes") or []:
                    if not isinstance(a, dict):
                        continue
                    at = str(a.get("type") or "").strip()
                    av = str(a.get("value") or "").strip()
                    if at and av:
                        attrs_by_name.setdefault(name, []).append({"type": at, "value": av})
        for r in res.get("relations") or []:
            src = (r.get("source") or "").strip()
            tgt = (r.get("target") or "").strip()
            rel = (r.get("relation") or "").strip()
            ev = (r.get("evidence") or "").strip()
            if not src or not tgt or src == tgt:
                continue
            key = tuple(sorted((src, tgt)))
            item = rel_map.setdefault(
                key,
                {
                    "source": src,
                    "target": tgt,
                    "relation": "",
                    "evidence": [],
                    "first_chapter": first_ci,
                    "chapter_count": 0,
                },
            )
            if rel and not item["relation"]:
                item["relation"] = rel
            if ev and ev not in item["evidence"]:
                item["evidence"].append(ev)
            item["first_chapter"] = min(item["first_chapter"], first_ci)
            item["chapter_count"] += 1
        logger.info(
            "Inventory batch %d/%d: chars=%d names=%d rels=%d cum_names=%d cum_rels=%d",
            bi,
            total,
            len(text),
            len(res.get("names") or []),
            len(res.get("relations") or []),
            len(by_name),
            len(rel_map),
        )
    return {
        "names": [
            {
                "name": n,
                "types": sorted(ts),
                "attributes": _dedupe_attrs(attrs_by_name.get(n, [])),
            }
            for n, ts in sorted(by_name.items())
        ],
        "relations": [
            {
                "source": v["source"],
                "target": v["target"],
                "relation": v["relation"],
                "evidence": v["evidence"][:8],
                "first_chapter": v["first_chapter"],
                "chapter_count": v["chapter_count"],
            }
            for _, v in sorted(rel_map.items(), key=lambda kv: (-kv[1]["chapter_count"], kv[0]))
        ],
    }


def mentions_from_names(
    text: str,
    names: list[str],
    *,
    min_len: int = 2,
) -> list[Any]:
    """把名字列表定位到原文出现位置，构造 character_ner.Mention。

    长名优先：短名出现位置若与已记录区间重叠则跳过（避免"八奈见"吞掉
    "八奈见杏菜"的完整区间导致 count/evidence 失真）。

    Returns:
        list[Mention]（source="llm"）。
    """
    from src.domain.novel.character_ner import Mention

    if not text or not names:
        return []
    occupied: list[tuple[int, int]] = []
    mentions: list[Any] = []
    unique = sorted(
        {n.strip() for n in names if n and n.strip()},
        key=lambda n: (-len(n), n),
    )
    for name in unique:
        if len(name) < min_len:
            continue
        start = 0
        while True:
            i = text.find(name, start)
            if i < 0:
                break
            end = i + len(name)
            overlaps = any(not (end <= a or b <= i) for a, b in occupied)
            if not overlaps:
                occupied.append((i, end))
                mentions.append(Mention(name, i, end, "llm", 1.0))
            start = end
    return mentions
