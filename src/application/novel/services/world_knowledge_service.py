"""WorldKnowledgeService — 结构化世界知识建表与查询（应用层）。

把 story_analyses / timelines / lorebooks 的结构化数据（角色关系、事件、时间线、
设定书）索引到 SQLite（``data/world_kb.sqlite``），供 ``world_knowledge`` 工具
按需精确查询。

设计原则（docs/WORLD_KNOWLEDGE_TOOL_DESIGN.md v5）：
- 懒构建：首次查询某系列时从 JSON 源构建，之后复用；源缺失 → 返回空（不报错）
- 只读：本服务不生成任何 LLM 摘要，只搬运已有结构化字段
- 判断回收：工具结果的价值判断由 LLM 后处理完成并上报 metrics，本层不做判断
- 返回带 evidence block_id 指针（不展开原文），原文由 novel_search 取
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")

# 项目根（data/world_kb.sqlite）
from src.domain.novel.series_paths import data_root

_DB_PATH = data_root() / "world_kb.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS world_relations (
    series_id TEXT NOT NULL,
    change_id  TEXT,
    source     TEXT,
    target     TEXT,
    relation_type TEXT,
    polarity   TEXT,
    confidence REAL,
    chapter_order INTEGER,
    doc_id     TEXT,
    chapter_title TEXT,
    story_period TEXT,
    summary    TEXT,
    evidence_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_rel_series ON world_relations(series_id);
CREATE INDEX IF NOT EXISTS idx_rel_src ON world_relations(series_id, source, target);

CREATE TABLE IF NOT EXISTS world_events (
    series_id TEXT NOT NULL,
    event_id  TEXT,
    summary   TEXT,
    event_type TEXT,
    characters TEXT,
    confidence REAL,
    chapter_order INTEGER,
    doc_id     TEXT,
    chapter_title TEXT,
    story_period TEXT,
    evidence_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_ev_series ON world_events(series_id);
CREATE INDEX IF NOT EXISTS idx_ev_char ON world_events(series_id, characters);

CREATE TABLE IF NOT EXISTS world_timeline (
    series_id TEXT NOT NULL,
    seq       INTEGER,
    summary   TEXT,
    event_type TEXT,
    characters TEXT,
    doc_id     TEXT,
    chapter_order INTEGER,
    chapter_title TEXT,
    story_period TEXT,
    key_event  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tl_series ON world_timeline(series_id);

CREATE TABLE IF NOT EXISTS world_lorebook (
    series_id TEXT NOT NULL,
    entry_id  TEXT,
    entity    TEXT,
    keys      TEXT,
    kind      TEXT,
    time_range TEXT,
    seq_from  INTEGER,
    seq_to    INTEGER,
    priority  INTEGER,
    content   TEXT
);
CREATE INDEX IF NOT EXISTS idx_lb_series ON world_lorebook(series_id);
CREATE INDEX IF NOT EXISTS idx_lb_entity ON world_lorebook(series_id, entity);

CREATE TABLE IF NOT EXISTS world_character_events (
    series_id TEXT NOT NULL,
    character TEXT,
    seqs      TEXT
);
CREATE INDEX IF NOT EXISTS idx_ce_series ON world_character_events(series_id);
"""


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ── 构建（从 JSON 源）─────────────────────────────────────────


def _series_has_data(conn: sqlite3.Connection, series_id: str) -> bool:
    for table in ("world_relations", "world_events", "world_timeline", "world_lorebook"):
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE series_id=? LIMIT 1", (series_id,)
        ).fetchone()
        if row is not None:
            return True
    return False


def _build_relations(conn: sqlite3.Connection, series_id: str, snapshot: Any) -> None:
    for rel in list(snapshot.relations or []):
        evidence_ids = []
        for ev in list(rel.evidence or []):
            bid = str(getattr(ev, "block_id", "") or (ev.get("block_id") if isinstance(ev, dict) else "") or "")
            if bid:
                evidence_ids.append(bid)
        story = rel.story_time or {}
        conn.execute(
            "INSERT INTO world_relations (series_id, change_id, source, target, relation_type,"
            " polarity, confidence, chapter_order, doc_id, chapter_title, story_period, summary, evidence_ids)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                series_id,
                str(getattr(rel, "change_id", "") or ""),
                str(getattr(rel, "source", "") or ""),
                str(getattr(rel, "target", "") or ""),
                str(getattr(rel, "relation_type", "") or ""),
                str(getattr(rel, "polarity", "") or ""),
                float(getattr(rel, "confidence", 0.0) or 0.0),
                int(getattr(rel, "chapter_order", -1) or -1),
                str(getattr(rel, "doc_id", "") or ""),
                str(getattr(rel, "chapter_title", "") or ""),
                str(story.get("period") or "") if isinstance(story, dict) else "",
                str(getattr(rel, "summary", "") or ""),
                json.dumps(evidence_ids, ensure_ascii=False),
            ),
        )


def _build_events(conn: sqlite3.Connection, series_id: str, snapshot: Any) -> None:
    for ev in list(snapshot.events or []):
        evidence_ids = []
        for e in list(ev.evidence or []):
            bid = str(getattr(e, "block_id", "") or (e.get("block_id") if isinstance(e, dict) else "") or "")
            if bid:
                evidence_ids.append(bid)
        story = ev.story_time or {}
        conn.execute(
            "INSERT INTO world_events (series_id, event_id, summary, event_type, characters,"
            " confidence, chapter_order, doc_id, chapter_title, story_period, evidence_ids)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                series_id,
                str(getattr(ev, "event_id", "") or ""),
                str(getattr(ev, "summary", "") or ""),
                str(getattr(ev, "event_type", "") or ""),
                json.dumps(list(getattr(ev, "characters", None) or []), ensure_ascii=False),
                float(getattr(ev, "confidence", 0.0) or 0.0),
                int(getattr(ev, "chapter_order", -1) or -1),
                str(getattr(ev, "doc_id", "") or ""),
                str(getattr(ev, "chapter_title", "") or ""),
                str(story.get("period") or "") if isinstance(story, dict) else "",
                json.dumps(evidence_ids, ensure_ascii=False),
            ),
        )


def _build_timeline(conn: sqlite3.Connection, series_id: str, timeline: dict | None) -> None:
    for ev in list((timeline or {}).get("chronicle") or []):
        story = ev.get("story_time") or {}
        conn.execute(
            "INSERT INTO world_timeline (series_id, seq, summary, event_type, characters,"
            " doc_id, chapter_order, chapter_title, story_period, key_event)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                series_id,
                int(ev.get("seq", 0) or 0),
                str(ev.get("summary", "") or ""),
                str(ev.get("event_type", "") or ""),
                json.dumps(list(ev.get("characters") or []), ensure_ascii=False),
                str(ev.get("doc_id", "") or ""),
                int(ev.get("chapter_order", -1) or -1),
                str(ev.get("chapter_title", "") or ""),
                str(story.get("period") or "") if isinstance(story, dict) else "",
                1 if ev.get("key_event") else 0,
            ),
        )
    for char, seqs in ((timeline or {}).get("by_character") or {}).items():
        conn.execute(
            "INSERT INTO world_character_events (series_id, character, seqs) VALUES (?,?,?)",
            (series_id, str(char), json.dumps(list(seqs or []), ensure_ascii=False)),
        )


def _build_lorebook(conn: sqlite3.Connection, series_id: str, lorebook: dict | None) -> None:
    for entry in list((lorebook or {}).get("entries") or []):
        tr = entry.get("time_range") or {}
        conn.execute(
            "INSERT INTO world_lorebook (series_id, entry_id, entity, keys, kind, time_range,"
            " seq_from, seq_to, priority, content) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                series_id,
                str(entry.get("entry_id", "") or ""),
                str(entry.get("entity", "") or ""),
                json.dumps(list(entry.get("keys") or []), ensure_ascii=False),
                str(entry.get("kind", "") or ""),
                json.dumps(tr, ensure_ascii=False),
                int(entry.get("seq_from") or 0),
                int(entry.get("seq_to") or 0),
                int(entry.get("priority", 0) or 0),
                str(entry.get("content", "") or ""),
            ),
        )


def build_series(series_id: str) -> bool:
    """懒构建：从 JSON 源索引一个系列到 SQLite。返回是否成功（源缺失返回 False）。"""
    from src.domain.novel.story_analysis import load_analysis
    from src.domain.novel.story_analysis.lorebook import load_lorebook
    from src.domain.novel.story_analysis.timeline import load_timeline

    snapshot = load_analysis(series_id)
    timeline = load_timeline(series_id)
    lorebook = load_lorebook(series_id)
    if snapshot is None and timeline is None and lorebook is None:
        return False

    conn = _connect()
    try:
        _ensure_schema(conn)
        if snapshot is not None:
            _build_relations(conn, series_id, snapshot)
            _build_events(conn, series_id, snapshot)
        if timeline is not None:
            _build_timeline(conn, series_id, timeline)
        if lorebook is not None:
            _build_lorebook(conn, series_id, lorebook)
        conn.commit()
        logger.info(
            "World KB built for %s (rels=%s events=%s tl=%s lb=%s)",
            series_id,
            conn.execute("SELECT COUNT(*) FROM world_relations WHERE series_id=?", (series_id,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM world_events WHERE series_id=?", (series_id,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM world_timeline WHERE series_id=?", (series_id,)).fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM world_lorebook WHERE series_id=?", (series_id,)).fetchone()[0],
        )
        return True
    finally:
        conn.close()


def _ensure_series(conn: sqlite3.Connection, series_id: str) -> None:
    if not _series_has_data(conn, series_id):
        conn.close()
        build_series(series_id)
        return _connect()


# ── 查询 ──────────────────────────────────────────────────────


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def _alias_expand(series_id: str, name: str) -> list[str]:
    """名字变体扩展：返回 [name] + alias 表中该实体的全部变体。

    relations/events 里的角色名可能是 利姆露/利姆鲁/利姆路 等不同写法，
    查询词只匹配其中一个会漏行。用 alias 表（canonical + aliases）扩展。
    """
    from src.domain.novel.alias_map import load_alias_map

    variants = {name}
    try:
        amap = load_alias_map(series_id)
    except Exception:  # noqa: BLE001
        amap = None
    if amap is not None:
        for ent in list(amap.entities or []):
            ent_names = {ent.canonical_name, *list(ent.aliases or [])}
            if name in ent_names:
                variants |= ent_names
    return sorted(v for v in variants if v)


def _like_or_clause(column: str, values: list[str]) -> str:
    """构造 (col LIKE ? OR col LIKE ? ...) 片段，返回 (sql_fragment, args)。

    LIKE 值带通配符（%value%），可匹配 JSON 数组字符串（如 characters 列
    存 `["利姆露","维鲁多拉"]`）与精确名。
    """
    frag = "(" + " OR ".join(f"{column} LIKE ?" for _ in values) + ")"
    return frag, [f"%{v}%" for v in values]


def query_relations(
    series_id: str,
    *,
    entity: str | None = None,
    entity2: str | None = None,
    relation_type: str | None = None,
    era: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """关系查询：source/target 任一匹配 entity（entity2 则双向匹配）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        if not _series_has_data(conn, series_id):
            build_series(series_id)
            if not _series_has_data(conn, series_id):
                return []
        sql = "SELECT * FROM world_relations WHERE series_id=?"
        args: list[Any] = [series_id]
        if entity and entity2:
            v1 = _alias_expand(series_id, entity)
            v2 = _alias_expand(series_id, entity2)
            f1, a1 = _like_or_clause("source", v1)
            f2, a2 = _like_or_clause("target", v2)
            sql += f" AND (({f1} AND {f2}) OR ({f2} AND {f1}))"
            args += a1 + a2 + a2 + a1
        elif entity:
            variants = _alias_expand(series_id, entity)
            frag, vargs = _like_or_clause("source", variants)
            sql += f" AND ({frag} OR target LIKE ?)"
            args += vargs + ["%" + entity + "%"]
        if relation_type:
            sql += " AND relation_type=?"
            args.append(relation_type)
        if era:
            sql += " AND story_period=?"
            args.append(era)
        sql += " ORDER BY chapter_order LIMIT ?"
        args.append(max(1, min(int(limit), 50)))
        return _rows_to_dicts(conn.execute(sql, args).fetchall())
    finally:
        conn.close()


def query_events(
    series_id: str,
    *,
    entity: str | None = None,
    era: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        if not _series_has_data(conn, series_id):
            build_series(series_id)
            if not _series_has_data(conn, series_id):
                return []
        sql = "SELECT * FROM world_events WHERE series_id=?"
        args: list[Any] = [series_id]
        if entity:
            variants = _alias_expand(series_id, entity)
            frag, vargs = _like_or_clause("characters", variants)
            sql += f" AND {frag}"
            args += vargs
        if era:
            sql += " AND story_period=?"
            args.append(era)
        sql += " ORDER BY chapter_order LIMIT ?"
        args.append(max(1, min(int(limit), 50)))
        return _rows_to_dicts(conn.execute(sql, args).fetchall())
    finally:
        conn.close()


def query_timeline(
    series_id: str,
    *,
    era: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        if not _series_has_data(conn, series_id):
            build_series(series_id)
            if not _series_has_data(conn, series_id):
                return []
        sql = "SELECT * FROM world_timeline WHERE series_id=?"
        args: list[Any] = [series_id]
        if era:
            sql += " AND story_period=?"
            args.append(era)
        sql += " ORDER BY seq LIMIT ?"
        args.append(max(1, min(int(limit), 50)))
        return _rows_to_dicts(conn.execute(sql, args).fetchall())
    finally:
        conn.close()


def query_character_events(
    series_id: str,
    *,
    character: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        if not _series_has_data(conn, series_id):
            build_series(series_id)
            if not _series_has_data(conn, series_id):
                return []
        sql = "SELECT * FROM world_character_events WHERE series_id=?"
        args: list[Any] = [series_id]
        if character:
            sql += " AND character=?"
            args.append(character)
        sql += " LIMIT ?"
        args.append(max(1, min(int(limit), 50)))
        return _rows_to_dicts(conn.execute(sql, args).fetchall())
    finally:
        conn.close()


def query_lorebook(
    series_id: str,
    *,
    entity: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        _ensure_schema(conn)
        if not _series_has_data(conn, series_id):
            build_series(series_id)
            if not _series_has_data(conn, series_id):
                return []
        sql = "SELECT * FROM world_lorebook WHERE series_id=?"
        args: list[Any] = [series_id]
        if entity:
            sql += " AND (entity=? OR keys LIKE ?)"
            args += [entity, f"%{entity}%"]
        sql += " ORDER BY priority DESC LIMIT ?"
        args.append(max(1, min(int(limit), 50)))
        return _rows_to_dicts(conn.execute(sql, args).fetchall())
    finally:
        conn.close()
