"""RAG retrieval trace — JSONL 检索调用日志（在线评估数据源）。

记录每次检索的关键信息到 ``data/traces/rag_trace.jsonl``，供
``/api/v1/agent/rag-eval``（前端 /rag-eval 页）复盘：用户在对话几轮后，
查看每次检索的 query / 路由 / scope / 命中原文，人工复核检索质量。

会话归属：检索发生在请求上下文（``src/shared/request_context`` 的
session_id ContextVar，由 chat / impersonate 路由绑定）时，记录自动附带
``session_id`` 字段。评估 API 只返回**现存会话**的 trace（无归属旧数据、
已删除会话的数据一律不展示），保证评估反映真实、未过期的会话数据。

设计：
- 追加写 JSONL（每行一个 dict），进程重启不丢
- 线程/异步安全（每次 open-append-close，写入原子）
- 开关 RAG_TRACE_ENABLED（默认开）；目录 RAG_TRACE_DIR（默认 data/traces）
- 轮转：超过 RAG_TRACE_MAX_ENTRIES 时把旧文件重命名为 .old（保留最近窗口）
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("agent.rag_trace")

_lock = threading.Lock()


def _trace_dir() -> Path:
    env = os.getenv("RAG_TRACE_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "traces"


def _enabled() -> bool:
    flag = os.getenv("RAG_TRACE_ENABLED", "").strip().lower()
    if flag:
        return flag in {"1", "true", "yes", "on"}
    return True


def _max_entries() -> int:
    try:
        return max(100, int(os.getenv("RAG_TRACE_MAX_ENTRIES", "20000")))
    except ValueError:
        return 20000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def append_trace(entry: dict) -> None:
    """Append one retrieval trace record (best-effort, never raises)."""
    if not _enabled():
        return
    if not entry:
        return
    record = {"ts": _now_iso(), **entry}
    # 记录会话归属：仅当请求上下文存在会话时附加，供在线评估按现存会话过滤。
    try:
        from src.shared.request_context import get_session_id

        sid = get_session_id()
        if sid:
            record["session_id"] = sid
    except Exception:  # noqa: BLE001
        pass
    try:
        d = _trace_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "rag_trace.jsonl"
        with _lock:
            if path.exists() and _line_count(path) >= _max_entries():
                _rotate(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("RAG trace append failed: %s", exc)


def _line_count(path: Path) -> int:
    try:
        return sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
    except OSError:
        return 0


def _rotate(path: Path) -> None:
    old = path.with_suffix(".jsonl.old")
    try:
        old.unlink(missing_ok=True)
        path.rename(old)
        logger.info("RAG trace rotated → %s", old)
    except OSError as exc:
        logger.debug("RAG trace rotate failed: %s", exc)


def hit_preview(block, *, max_len: int = 120) -> str:
    """Render a block to a one-line preview for trace/review."""
    parts: list[str] = []
    text = getattr(block, "narrative_text", None) or ""
    if text:
        parts.append(text)
    scene = getattr(block, "scene", None) or ""
    if scene:
        parts.append(scene)
    for d in getattr(block, "dialogues", None) or []:
        if getattr(d, "content", "").strip():
            parts.append(f"{getattr(d, 'speaker', '')}: {d.content}")
    q = getattr(block, "question", None) or ""
    if q:
        parts.append(f"Q: {q}")
    a = getattr(block, "answer", None) or ""
    if a:
        parts.append(f"A: {a}")
    blob = " ".join(parts).strip().replace("\n", " ")
    return blob[:max_len] + ("…" if len(blob) > max_len else "")


# ── Trace 分析（供 API 与复盘脚本复用）────────────────────────

_CHANNEL_LABEL = {
    "narrative": "叙事",
    "dialogue": "对话",
    "qa": "QA",
    "character": "角色",
    "unknown": "?",
}


def load_traces(path: str | Path | None = None) -> list[dict]:
    """Read trace jsonl into a list of records."""
    p = Path(path) if path else _trace_dir() / "rag_trace.jsonl"
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        with p.open(encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return out


def _filters_match(t: dict, *, kind: str | None, channel: str | None, q: str | None,
                   zero_only: bool, limit: int) -> bool:
    if kind and t.get("kind") != kind:
        return False
    if channel and t.get("channel") != channel and t.get("primary_channel") != channel:
        return False
    if q:
        query = (t.get("query") or "").lower()
        if q.lower() not in query:
            return False
    if zero_only and not t.get("zero_hit"):
        return False
    return True


def summarize_traces(traces: list[dict]) -> dict:
    """概览统计：零命中率 / scope 覆盖率 / 通道分布 / 平均耗时等。"""
    n = len(traces)
    kinds: dict[str, int] = {}
    channels: dict[str, int] = {}
    zero = 0
    scoped = 0
    total_hits = 0
    total_ms = 0
    ms_count = 0
    variants_sum = 0
    variants_count = 0
    for t in traces:
        k = t.get("kind") or "unknown"
        kinds[k] = kinds.get(k, 0) + 1
        ch = t.get("channel") or t.get("primary_channel") or "unknown"
        channels[ch] = channels.get(ch, 0) + 1
        if t.get("zero_hit"):
            zero += 1
        flt = t.get("filters") or {}
        if t.get("doc_id") or flt.get("series") or flt.get("doc_ids"):
            scoped += 1
        total_hits += len(t.get("hits") or [])
        ms = t.get("elapsed_ms")
        if ms:
            total_ms += int(ms)
            ms_count += 1
        v = t.get("query_variants")
        if v:
            variants_sum += int(v)
            variants_count += 1
    return {
        "total": n,
        "zero_hit": zero,
        "zero_hit_rate": round(zero / n, 3) if n else 0,
        "scoped": scoped,
        "scoped_rate": round(scoped / n, 3) if n else 0,
        "avg_hits": round(total_hits / n, 2) if n else 0,
        "avg_ms": round(total_ms / ms_count) if ms_count else 0,
        "avg_variants": round(variants_sum / variants_count, 2) if variants_count else 1,
        "kinds": kinds,
        "channels": channels,
        "channel_labels": _CHANNEL_LABEL,
    }


def build_case_list(traces: list[dict], *, limit: int = 200) -> list[dict]:
    """逐条详情（前端展示用）。"""
    out: list[dict] = []
    for t in traces[:limit]:
        hits = []
        for h in (t.get("hits") or [])[:6]:
            hits.append(
                {
                    "global_id": h.get("global_id", ""),
                    "block_type": h.get("block_type", ""),
                    "doc_id": h.get("doc_id", ""),
                    "chapter_title": h.get("chapter_title", ""),
                    "score": h.get("score"),
                    "preview": (h.get("preview") or "")[:160],
                }
            )
        out.append(
            {
                "ts": t.get("ts", ""),
                "kind": t.get("kind", ""),
                "query": (t.get("query") or "")[:160],
                "channel": t.get("channel") or t.get("primary_channel") or "",
                "doc_id": t.get("doc_id") or "",
                "series_id": t.get("series_id") or "",
                "filters": t.get("filters") or {},
                "hit_count": len(t.get("hits") or []),
                "zero_hit": bool(t.get("zero_hit")),
                "elapsed_ms": t.get("elapsed_ms"),
                "query_variants": t.get("query_variants"),
                "hits": hits,
            }
        )
    return out


def filter_traces_for_api(
    traces: list[dict],
    *,
    kind: str | None = None,
    channel: str | None = None,
    q: str | None = None,
    zero_only: bool = False,
    limit: int = 200,
    active_session_ids: set[str] | None = None,
) -> list[dict]:
    """按前端参数过滤（与复盘脚本同语义）。

    ``active_session_ids`` 非 None 时，只保留归属现存会话的 trace；
    无会话归属（旧数据 / 非会话上下文检索）或会话已删除的条目一律排除，
    保证评估页只反映“现存会话的真实检索数据”。
    """
    kept: list[dict] = []
    for t in traces:
        if not _filters_match(
            t, kind=kind, channel=channel, q=q, zero_only=zero_only, limit=limit
        ):
            continue
        if active_session_ids is not None:
            sid = t.get("session_id")
            if not sid or sid not in active_session_ids:
                continue
        kept.append(t)
    return kept[:limit]
