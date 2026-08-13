"""Story analysis config, settings resolution, persistence, and name helpers.

Extracted from the former monolithic ``story_analysis.py``; logic unchanged.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.domain.novel.story_analysis.models import StoryAnalysisSnapshot

logger = logging.getLogger("agent")

from src.domain.novel.series_paths import data_root

_ANALYSIS_DIR = data_root() / "story_analyses"
_DEFAULT_MAX_CHAPTERS = 40
_DEFAULT_MAP_CONCURRENCY = 3
_DEFAULT_MAP_MAX_CHARS = 5000
_DEFAULT_MAX_TOKENS = 4096
_DEFAULT_PER_TYPE_CAP = 3
_DEFAULT_SUMMARY_MAX_CHARS = 80
_DEFAULT_REJECT_SUBSTRINGS = ["做法", "众人", "现场", "之类", "方面", "情况"]


def _story_analysis_settings() -> dict[str, Any]:
    """Load optional story_analysis section from config.yaml."""
    try:
        import yaml

        path = Path(__file__).resolve().parents[4] / "config.yaml"
        if not path.is_file():
            return {}
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        section = raw.get("story_analysis")
        return dict(section) if isinstance(section, dict) else {}
    except Exception:
        return {}


def _resolve_run_settings(
    *,
    max_chapters: int | None = None,
    map_concurrency: int | None = None,
    map_max_chars: int | None = None,
    max_tokens: int | None = None,
    per_type_cap: int | None = None,
    extract_relations: bool | None = None,
    extract_events: bool | None = None,
    extract_foreshadows: bool | None = None,
) -> dict[str, Any]:
    cfg = _story_analysis_settings()
    extract_cfg = cfg.get("extract") if isinstance(cfg.get("extract"), dict) else {}
    retry_cfg = cfg.get("map_retry") if isinstance(cfg.get("map_retry"), dict) else {}
    entity_cfg = (
        cfg.get("entity_filter") if isinstance(cfg.get("entity_filter"), dict) else {}
    )
    quota_cfg = (
        cfg.get("chapter_quota") if isinstance(cfg.get("chapter_quota"), dict) else {}
    )
    chapters = int(
        max_chapters
        if max_chapters is not None
        else cfg.get("max_chapters", _DEFAULT_MAX_CHAPTERS)
    )
    concurrency = int(
        map_concurrency
        if map_concurrency is not None
        else cfg.get("map_concurrency", _DEFAULT_MAP_CONCURRENCY)
    )
    max_chars = int(
        map_max_chars
        if map_max_chars is not None
        else cfg.get("map_max_chars", _DEFAULT_MAP_MAX_CHARS)
    )
    tokens = int(
        max_tokens if max_tokens is not None else cfg.get("max_tokens", _DEFAULT_MAX_TOKENS)
    )
    cap = int(
        per_type_cap
        if per_type_cap is not None
        else cfg.get("per_type_cap", _DEFAULT_PER_TYPE_CAP)
    )
    summary_max = int(cfg.get("summary_max_chars", _DEFAULT_SUMMARY_MAX_CHARS))
    want_rel = (
        bool(extract_relations)
        if extract_relations is not None
        else bool(extract_cfg.get("relations", True))
    )
    want_evt = (
        bool(extract_events)
        if extract_events is not None
        else bool(extract_cfg.get("events", True))
    )
    want_fh = (
        bool(extract_foreshadows)
        if extract_foreshadows is not None
        else bool(extract_cfg.get("foreshadows", False))
    )
    if not want_rel and not want_evt and not want_fh:
        want_rel = True
    retry_extract_cfg = (
        retry_cfg.get("retry_extract")
        if isinstance(retry_cfg.get("retry_extract"), dict)
        else {}
    )
    reject = entity_cfg.get("reject_substrings")
    if not isinstance(reject, list) or not reject:
        reject = list(_DEFAULT_REJECT_SUBSTRINGS)
    return {
        "max_chapters": max(1, chapters),
        "map_concurrency": max(1, concurrency),
        "map_max_chars": max(500, max_chars),
        "max_tokens": max(512, tokens),
        "per_type_cap": max(1, min(12, cap)),
        "summary_max_chars": max(20, min(200, summary_max)),
        "extract": {
            "relations": want_rel,
            "events": want_evt,
            "foreshadows": want_fh,
        },
        "map_retry": {
            "enabled": bool(retry_cfg.get("enabled", True)),
            "max_retries": max(0, int(retry_cfg.get("max_retries", 1))),
            "retry_per_type_cap": max(
                1, min(12, int(retry_cfg.get("retry_per_type_cap", 3)))
            ),
            "retry_extract": {
                "relations": bool(retry_extract_cfg.get("relations", True)),
                "events": bool(retry_extract_cfg.get("events", False)),
                "foreshadows": bool(retry_extract_cfg.get("foreshadows", False)),
            },
        },
        "entity_filter": {
            "reject_substrings": [str(x) for x in reject if str(x).strip()],
            "min_name_len": max(1, int(entity_cfg.get("min_name_len", 2))),
            "max_name_len": max(2, int(entity_cfg.get("max_name_len", 16))),
        },
        "balance_by_volume": bool(quota_cfg.get("balance_by_volume", True)),
    }


def story_analysis_max_tokens() -> int:
    """LLM max_tokens for story-analysis build (API / tool)."""
    return int(_resolve_run_settings()["max_tokens"])


def analysis_path(series_id: str) -> Path:
    safe = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", (series_id or "").strip()) or "unknown"
    return _ANALYSIS_DIR / f"{safe}.json"


def load_analysis(series_id: str) -> StoryAnalysisSnapshot | None:
    path = analysis_path(series_id)
    if not path.exists():
        return None
    try:
        return StoryAnalysisSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("Failed to load story analysis %s: %s", path, e)
        return None


def save_analysis(snap: StoryAnalysisSnapshot) -> Path:
    path = analysis_path(snap.series_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    snap.updated_at = datetime.now(UTC).isoformat()
    payload = json.dumps(snap.to_dict(), ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        Path(tmp_name).replace(path)
    except Exception:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    # V5：同步派生编年体时间线（story_time → chronicle），失败不影响快照
    try:
        from src.domain.novel.story_analysis.timeline import build_and_save

        build_and_save(snap, series_id=snap.series_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Timeline build failed for %s: %s", snap.series_id, exc)
    # V5 P2：同步派生时间感知设定书（chronicle → lorebook），失败不影响快照
    try:
        from src.domain.novel.story_analysis.lorebook import build_and_save

        build_and_save(snap.series_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Lorebook build failed for %s: %s", snap.series_id, exc)
    return path


def _build_map_system(
    extract: dict[str, bool],
    *,
    per_type_cap: int,
    summary_max_chars: int = _DEFAULT_SUMMARY_MAX_CHARS,
) -> str:
    """Build map system prompt from extract switches (relations/events/foreshadows)."""
    schema_parts: list[str] = []
    if extract.get("events"):
        schema_parts.append(
            '"events":[{"summary":"...","event_type":"plot|character|world|other",'
            '"characters":["..."],"confidence":0.0到1.0,"evidence_block_ids":["..."],'
            '"story_time":{"year":整数或null,"period":"转生前|转生后|建国前|建国后|魔王时期|大战|结局后|其他","label":"人读文本如：转生后第2年","relative":"原文相对时间如：三年前","confidence":0.0到1.0}}]'
        )
    if extract.get("foreshadows"):
        schema_parts.append(
            '"foreshadows":[{"content":"...","status":"pending|resolved|abandoned",'
            '"related_characters":[],"confidence":0.0到1.0,"evidence_block_ids":["..."]}]'
        )
    if extract.get("relations"):
        schema_parts.append(
            '"relations":[{"source":"...","target":"...","relation_type":"...",'
            '"polarity":"positive|negative|neutral","summary":"...",'
            '"confidence":0.0到1.0,"evidence_block_ids":["..."],'
            '"story_time":{"year":整数或null,"period":"...","label":"...","relative":"...","confidence":0.0到1.0}}]'
        )
    schema_body = ",\n  ".join(schema_parts) if schema_parts else '"relations":[]'
    focus = []
    if extract.get("relations"):
        focus.append("关系变化（对立/结盟/认识/态度，带卷章语境）优先")
    if extract.get("events"):
        focus.append("轻量情节事件（参与角色列表）")
    if extract.get("foreshadows"):
        focus.append("伏笔")
    focus_line = "；".join(focus) or "关系变化"
    return (
        "你是轻小说关系与事件索引器。只根据给定章节原文提取结构化检索线索。\n"
        "规则：\n"
        "1. 不得编造原文未出现的情节；不确定就降低 confidence 或省略。\n"
        "2. 每条必须引用 evidence_block_ids 中出现的 block_id；无证据则不要输出该条。\n"
        "3. 摘要仅作检索跳板；禁止无原文支撑的「后来变成朋友」式概括。\n"
        f"4. 本任务重点：{focus_line}。\n"
        "5. 只输出一个 JSON 对象，不要 markdown 围栏，不要前后解释文字。\n"
        "{\n"
        f"  {schema_body}\n"
        "}\n"
        f"6. 每类最多 {per_type_cap} 条；每条 summary/content 不超过 {summary_max_chars} 字。\n"
        "7. 关系端点必须是人名或明确势力名；禁止「做法/众人/现场/之类」等抽象端点。\n"
        "8. 无内容则给空数组；不要输出未请求的键。\n"
        "9. story_time 是故事内时间（非章节序）：根据原文时间表达（三年前/翌年/数日后/现在）与系列锚点（转生日=故事年1）推断 year；"
        "推断不出 year 给 null；period 选最接近的阶段；label 用人读短文本；relative 填原文相对表达或空。"
        "10. 跨卷时间基准统一：以系列起点为 year 1，同一系列各卷 year 必须同基准。"
    )


_MAP_SYSTEM = _build_map_system(
    {"relations": True, "events": True, "foreshadows": False},
    per_type_cap=_DEFAULT_PER_TYPE_CAP,
)


def _parse_json_object(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _looks_like_cut_json(raw: str) -> bool:
    """True when text looks like truncated JSON (opened brace, incomplete close)."""
    t = (raw or "").strip()
    if not t or "{" not in t:
        return False
    if t.rstrip().endswith("}") or t.rstrip().endswith("]"):
        return False
    return t.count("{") > t.count("}")


def _is_weak_entity_name(
    name: str,
    *,
    reject_substrings: Sequence[str],
    min_name_len: int = 2,
    max_name_len: int = 16,
) -> bool:
    n = (name or "").strip()
    if len(n) < min_name_len or len(n) > max_name_len:
        return True
    return any(s and s in n for s in reject_substrings)


def _load_alias_map(series_id: str) -> dict[str, str]:
    """alias/name -> canonical from roster (soft; empty if unavailable)."""
    out: dict[str, str] = {}
    try:
        from src.domain.novel.character_roster import load_roster

        roster = load_roster(series_id)
        if not roster:
            return out
        for e in roster.characters or []:
            canon = str(getattr(e, "name", "") or "").strip()
            if not canon:
                continue
            out[canon] = canon
            for a in getattr(e, "aliases_observed", None) or []:
                a = str(a or "").strip()
                if a:
                    out[a] = canon
    except Exception:
        return out
    return out


def _canon_name(name: str, alias_map: dict[str, str]) -> str:
    n = (name or "").strip()
    return alias_map.get(n, n) if n else n


def select_chapters_balanced(
    chapters: list,
    *,
    max_chapters: int,
    balance_by_volume: bool = True,
) -> tuple[list, dict[str, int]]:
    """Select up to max_chapters; optionally balance across volumes.

    ``chapters`` is a list of ``(volume, chapter_meta)`` from catalog.ordered_chapters.
    """
    if not chapters:
        return [], {}
    if not balance_by_volume or max_chapters <= 0:
        picked = chapters[:max_chapters]
    else:
        by_doc: dict[str, list] = {}
        for item in chapters:
            vol = item[0]
            by_doc.setdefault(vol.doc_id, []).append(item)
        n_vols = max(1, len(by_doc))
        per = max(1, (max_chapters + n_vols - 1) // n_vols)
        picked = []
        for doc_id in by_doc:
            bucket = by_doc[doc_id][:per]
            picked.extend(bucket)
        # Stable by original order, then trim
        order = {id(x): i for i, x in enumerate(chapters)}
        picked.sort(key=lambda x: order.get(id(x), 10**9))
        picked = picked[:max_chapters]
    per_doc: dict[str, int] = {}
    for vol, _ in picked:
        per_doc[vol.doc_id] = per_doc.get(vol.doc_id, 0) + 1
    return picked, per_doc


