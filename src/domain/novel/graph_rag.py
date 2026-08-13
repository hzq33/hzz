"""GraphRAG 全局问答层：社区发现 + LLM 社区摘要 + 全局检索。

两层问答的"全局语义层"：
- 从 story_analysis relations 快照（事实源）+ 可选 CharacterGraph 文件构建关系图
- networkx 模块度贪心做角色社区划分
- 每个社区 LLM 生成摘要（成员 / 核心关系网 / 主题主线 / 关键事件，附章节证据）
- 全局问答时按 query 匹配社区摘要 → 返回全局上下文

数据：data/graph_rag/{series_id}.json（与 story_analysis fingerprint 联动失效）
"""

from __future__ import annotations

import json
import logging
from typing import Any

import networkx as nx

logger = logging.getLogger("agent.graph_rag")

from src.domain.novel.series_paths import data_root

_DATA_ROOT = data_root()
_GRAPH_RAG_DIR = _DATA_ROOT / "graph_rag"
_GRAPH_DIR = _DATA_ROOT / "graphs"

_SUMMARY_MAX_TOKENS = 1024
_SUMMARY_TARGET_CHARS = 400  # 每社区摘要目标长度（字）


def _ensure_dir() -> None:
    _GRAPH_RAG_DIR.mkdir(parents=True, exist_ok=True)


# ── 加载 ────────────────────────────────────────────────


def load_graph_rag(series_id: str) -> dict[str, Any] | None:
    """Load the persisted GraphRAG payload for a series."""
    path = _GRAPH_RAG_DIR / f"{series_id}.json"
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("load_graph_rag(%s) failed: %s", series_id, exc)
        return None


def is_stale(series_id: str) -> bool:
    """graph_rag 是否因 story_analysis 内容变更而失效（fingerprint 不匹配）。"""
    try:
        from src.domain.novel.story_analysis.config import load_analysis

        snap = load_analysis(series_id)
        payload = load_graph_rag(series_id)
        if snap is None or payload is None:
            return True
        return bool(snap.content_fingerprint) and snap.content_fingerprint != payload.get("fingerprint")
    except Exception:  # noqa: BLE001
        return True


# ── 图构建 ─────────────────────────────────────────────


def _relation_graph(snapshot) -> nx.Graph:
    """关系图：story_analysis relations 快照（事实源，带语义）。"""
    G = nx.Graph()
    for rel in (snapshot.relations or []):
        s = str(getattr(rel, "source", "") or "").strip()
        t = str(getattr(rel, "target", "") or "").strip()
        if not s or not t or s == t:
            continue
        rtype = str(getattr(rel, "relation_type", "") or "").strip()
        polarity = str(getattr(rel, "polarity", "") or "neutral")
        conf = float(getattr(rel, "confidence", 0) or 0)
        if G.has_edge(s, t):
            G[s][t]["weight"] += 1
            prev = G[s][t]
            if conf > float(prev.get("confidence", 0) or 0):
                prev.update(relation_type=rtype, polarity=polarity, confidence=conf)
        else:
            G.add_edge(s, t, weight=1, relation_type=rtype, polarity=polarity, confidence=conf)
    return G


def _merge_graph_files(series_id: str, doc_ids: list[str]) -> nx.Graph:
    """合并 data/graphs/{doc_id}.json 的 CharacterGraph 节点/边（若有）。"""
    G = nx.Graph()
    for doc in doc_ids or []:
        if not doc:
            continue
        path = _GRAPH_DIR / f"{doc}.json"
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for n in raw.get("nodes", []):
                nid = str(n.get("id", "") or "").strip()
                if nid:
                    G.add_node(nid)
            for e in raw.get("edges", []):
                s = str(e.get("source", "") or "").strip()
                t = str(e.get("target", "") or "").strip()
                if not s or not t:
                    continue
                w = float(e.get("weight", 1) or 1)
                if G.has_edge(s, t):
                    G[s][t]["weight"] += w
                else:
                    G.add_edge(s, t, weight=w)
        except Exception as exc:  # noqa: BLE001
            logger.debug("graph file merge skipped (%s): %s", doc, exc)
    return G


def detect_communities(G: nx.Graph) -> list[list[str]]:
    """角色社区划分（边权重模块度贪心；无边的孤立节点自成一社区）。"""
    if G.number_of_nodes() == 0:
        return []
    try:
        comms = list(
            nx.algorithms.community.greedy_modularity_communities(G, weight="weight")
        )
        communities = [sorted(c) for c in comms if c]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Community detection failed (%s); using singletons", exc)
        communities = [[n] for n in G.nodes()]
    isolated = [n for n in G.nodes() if G.degree(n) == 0]
    if isolated:
        communities = [c for c in communities if not set(c).issubset(isolated)]
        for n in isolated:
            communities.append([n])
    return communities


# ── 社区摘要（LLM）──────────────────────────────────────


def _community_payload(G: nx.Graph, members: list[str], snapshot) -> dict[str, Any]:
    """社区内关系网 + 相关事件证据（供摘要与检索）。"""
    member_set = set(members)
    edges = []
    for u, v, data in G.edges(members, data=True):
        if u not in member_set or v not in member_set:
            continue
        edges.append({
            "source": u,
            "target": v,
            "weight": int(data.get("weight", 1)),
            "relation_type": data.get("relation_type", ""),
            "polarity": data.get("polarity", "neutral"),
            "confidence": round(float(data.get("confidence", 0) or 0), 2),
        })
    edges.sort(key=lambda e: -e["weight"])

    # 社区内角色参与的 events（story_analysis）
    key_events = []
    seen_ev = set()
    for ev in (snapshot.events or []):
        chs = set(ev.characters or [])
        if chs & member_set:
            summary = str(getattr(ev, "summary", "") or "").strip()
            if not summary or summary in seen_ev:
                continue
            seen_ev.add(summary)
            key_events.append({
                "summary": summary,
                "chapter": str(getattr(ev, "chapter_title", "") or ""),
                "confidence": float(getattr(ev, "confidence", 0) or 0),
            })
    key_events.sort(key=lambda e: -e["confidence"])
    return {
        "members": members,
        "core_relations": edges[:12],
        "key_events": key_events[:6],
    }


_SUMMARY_SYSTEM = (
    "你是小说剧情分析助手。根据给定的小说角色社区信息（成员、关系网、关键事件），"
    "用中文生成一段 300-500 字的社区摘要，涵盖：\n"
    "1. 社区成员与核心关系网（谁和谁是什么关系，用关系类型/态度描述）\n"
    "2. 该社区推动的主题主线（这群人的故事围绕什么）\n"
    "3. 关键事件（按时间/重要度概述，引用章节）\n"
    "只输出摘要正文，不要额外解释。"
)


def _summary_user_prompt(payload: dict[str, Any]) -> str:
    members = "、".join(payload["members"])
    lines = [f"社区成员：{members}", "", "核心关系："]
    for e in payload["core_relations"][:10]:
        rtype = e.get("relation_type") or "互动"
        pol = e.get("polarity") or "neutral"
        lines.append(f"- {e['source']} ↔ {e['target']}（{rtype}，态度{'积极' if pol=='positive' else '消极' if pol=='negative' else '中性'}，权重{e['weight']}）")
    lines.append("")
    lines.append("关键事件：")
    for ev in payload["key_events"][:5]:
        lines.append(f"- [{ev.get('chapter') or '?'}] {ev.get('summary')}")
    return "\n".join(lines)


async def _summarize_community(llm_client, payload: dict[str, Any]) -> str:
    """一次 LLM 调用生成社区摘要；失败回退规则摘要。"""
    try:
        messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": _summary_user_prompt(payload)},
        ]
        if hasattr(llm_client, "achat_result"):
            result = await llm_client.achat_result(messages, max_tokens=_SUMMARY_MAX_TOKENS)
            raw = getattr(result, "content", "") or ""
        else:
            resp = await llm_client.achat(messages=messages, max_tokens=_SUMMARY_MAX_TOKENS)
            raw = resp if isinstance(resp, str) else getattr(resp, "content", "") or str(resp)
        text = (raw or "").strip()
        if len(text) >= 20:
            return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Community summary LLM failed: %s", exc)
    # 回退：规则摘要
    return _rule_summary(payload)


def _rule_summary(payload: dict[str, Any]) -> str:
    members = "、".join(payload["members"][:8])
    rels = payload["core_relations"][:5]
    rel_txt = "；".join(
        f"{e['source']}与{e['target']}{e.get('relation_type') or '有互动'}"
        for e in rels
    )
    evs = payload["key_events"][:3]
    ev_txt = "；".join(e["summary"] for e in evs)
    return (
        f"社区成员：{members}。"
        + (f"关系：{rel_txt}。" if rel_txt else "")
        + (f"关键事件：{ev_txt}。" if ev_txt else "")
    )


# ── 构建 / 存储 ─────────────────────────────────────────


async def build_graph_rag(
    series_id: str,
    *,
    snapshot,
    llm_client=None,
    force: bool = False,
    on_progress=None,
) -> dict[str, Any]:
    """构建社区摘要并持久化 data/graph_rag/{series_id}.json。"""
    def _progress(stage: str, message: str, pct: int) -> None:
        if on_progress:
            try:
                on_progress(stage, message, pct)
            except Exception:
                pass

    payload = load_graph_rag(series_id)
    if (
        payload
        and not force
        and payload.get("fingerprint") == getattr(snapshot, "content_fingerprint", "")
    ):
        return payload

    _progress("graph_rag", "构建关系图与社区划分", 20)
    G = _relation_graph(snapshot)
    # 合并图谱文件节点（补充纯叙事角色；关系边以快照为准）
    gfiles = _merge_graph_files(series_id, getattr(snapshot, "doc_ids", None) or [])
    if G.number_of_nodes() == 0:
        G = gfiles
    else:
        G.add_nodes_from(gfiles.nodes())

    communities = detect_communities(G)
    _progress("graph_rag", f"社区划分完成（{len(communities)} 个）", 45)

    comm_payloads = [
        _community_payload(G, members, snapshot) for members in communities
    ]

    summaries: list[dict[str, Any]] = []
    for i, cp in enumerate(comm_payloads):
        summary = await _summarize_community(llm_client, cp)
        summaries.append({**cp, "id": i, "summary": summary})
        _progress(
            "graph_rag",
            f"社区摘要 {i + 1}/{len(comm_payloads)}",
            50 + int(45 * (i + 1) / max(1, len(comm_payloads))),
        )

    # 全局总览（可选：跨社区主线；社区少时跳过避免重复）
    global_overview = ""
    if len(summaries) > 1 and llm_client is not None:
        try:
            from src.application.novel.retrieval import _clip  # noqa: F401

            overview_user = (
                "以下是一本小说的角色社区摘要。请用 200 字以内概述全书主线与核心矛盾：\n\n"
                + "\n\n".join(f"【社区{i}】{s['summary']}" for i, s in enumerate(summaries))
            )
            messages = [
                {"role": "system", "content": "你是小说分析助手，输出中文主线概述（200 字以内）。"},
                {"role": "user", "content": overview_user},
            ]
            if hasattr(llm_client, "achat_result"):
                r = await llm_client.achat_result(messages, max_tokens=800)
                global_overview = (getattr(r, "content", "") or "").strip()
            else:
                resp = await llm_client.achat(messages=messages, max_tokens=800)
                global_overview = (resp if isinstance(resp, str) else getattr(resp, "content", "") or str(resp)).strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Global overview LLM failed: %s", exc)

    result = {
        "series_id": series_id,
        "fingerprint": getattr(snapshot, "content_fingerprint", "") or "",
        "updated_at": getattr(snapshot, "updated_at", "") or "",
        "global_overview": global_overview,
        "communities": summaries,
    }
    _ensure_dir()
    path = _GRAPH_RAG_DIR / f"{series_id}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    _progress("graph_rag", "完成", 100)
    logger.info("GraphRAG built (%s): %d communities → %s", series_id, len(summaries), path)
    return result


# ── 全局检索 ────────────────────────────────────────────


def search_global(
    series_id: str,
    query: str,
    *,
    top_communities: int = 2,
) -> str:
    """GraphRAG 全局问答：query 匹配社区摘要 → 全局上下文文本。

    无额外 LLM 调用（摘要预生成）；社区数少，关键词 + 角色名匹配即可。
    """
    payload = load_graph_rag(series_id)
    if not payload or not payload.get("communities"):
        return ""

    q = (query or "").strip()
    # 匹配：query 中出现的角色名 + 摘要关键词
    scored: list[tuple[float, dict]] = []
    for c in payload["communities"]:
        score = 0.0
        members = [str(m) for m in (c.get("members") or [])]
        summary = str(c.get("summary") or "")
        for m in members:
            if m and m in q:
                score += 2.0
        # 摘要与 query 的关键词重叠（2-gram 简单匹配）
        q_chars = {q[i : i + 2] for i in range(max(0, len(q) - 1))}
        if q_chars and summary:
            overlap = sum(1 for big in q_chars if big in summary)
            score += overlap / len(q_chars)
        # 社区规模权重（弱）
        score += min(1.0, len(members) / 8.0)
        scored.append((score, c))

    scored.sort(key=lambda x: -x[0])
    top = [c for _, c in scored[:top_communities] if _ > 0]

    lines: list[str] = []
    if payload.get("global_overview"):
        lines.append(f"【全书主线】{payload['global_overview']}")
    for c in top:
        members = "、".join(str(m) for m in (c.get("members") or []))
        lines.append(f"【角色社区·{'、'.join(str(m) for m in (c.get('members') or [])[:3])}等】")
        lines.append(f"成员：{members}")
        if c.get("summary"):
            lines.append(f"概述：{c['summary']}")
        rels = c.get("core_relations") or []
        if rels:
            rel_txt = "；".join(
                f"{e.get('source')}↔{e.get('target')}"
                + (f"（{e.get('relation_type')}）" if e.get("relation_type") else "")
                for e in rels[:6]
            )
            lines.append(f"关系：{rel_txt}")
    if not top and payload.get("global_overview"):
        return lines[0]
    if not lines:
        return ""
    return "\n".join(lines)


def format_global_context(series_id: str, query: str) -> str:
    """包装为检索隔离格式（与 _format_context 一致）。"""
    body = search_global(series_id, query)
    if not body:
        return ""
    return (
        "【全局剧情参考 — 来自剧情分析，可能含推测，仅作参考】\n"
        f"<search_results>\n{body}\n</search_results>"
    )
