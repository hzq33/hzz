"""Story analysis map stage — chapter fingerprint, text collection, LLM map.

Extracted from the former monolithic ``story_analysis.py``; logic unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from src.domain.novel.story_analysis.config import (
    _build_map_system,
    _canon_name,
    _load_alias_map,
    _looks_like_cut_json,
    _parse_json_object,
    _resolve_run_settings,
    _DEFAULT_MAX_TOKENS,
    _DEFAULT_PER_TYPE_CAP,
    _DEFAULT_SUMMARY_MAX_CHARS,
    _PROMPT_VERSION,
)
from src.domain.novel.story_analysis.models import StoryEvidence

logger = logging.getLogger("agent")


def _chapter_fingerprint(catalog, doc_id: str | None = None) -> str:
    parts = []
    for vol in catalog.volumes:
        if doc_id and vol.doc_id != doc_id:
            continue
        parts.append(f"{vol.doc_id}:{vol.content_fingerprint}:{len(vol.chapters)}")
    raw = "|".join(parts) + f"|{_PROMPT_VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _collect_chapter_text(
    store,
    *,
    doc_id: str,
    chapter_title: str,
    chapter_order: int,
    max_chars: int = 6000,
) -> tuple[str, list[StoryEvidence]]:
    """Assemble chapter text from narrative (+ light dialogue) blocks."""
    blocks = []
    if hasattr(store, "iter_blocks"):
        for bt in ("narrative", "dialogue"):
            blocks.extend(store.iter_blocks(block_type=bt, doc_id=doc_id) or [])

    # Prefer matching chapter_title; fallback to order via global_id cNNN
    matched = []
    for b in blocks:
        title = getattr(b, "chapter_title", "") or ""
        gid = getattr(b, "global_id", "") or ""
        if title == chapter_title or f"_c{chapter_order:03d}_" in gid:
            matched.append(b)

    if not matched:
        # last resort: any blocks for doc
        matched = [b for b in blocks if getattr(b, "doc_id", "") == doc_id][:20]

    # Stable order by global_id
    matched.sort(key=lambda b: getattr(b, "global_id", "") or "")

    pieces: list[str] = []
    evidence: list[StoryEvidence] = []
    total = 0
    for b in matched:
        text = (getattr(b, "narrative_text", "") or "").strip()
        if not text and getattr(b, "dialogues", None):
            text = " ".join(
                f"{getattr(t, 'speaker', '')}：{getattr(t, 'content', '')}"
                for t in (b.dialogues or [])[:12]
            )
        if not text:
            continue
        snippet = text[:240].replace("\n", " ")
        evidence.append(
            StoryEvidence(
                doc_id=doc_id,
                chapter_order=chapter_order,
                chapter_title=chapter_title,
                block_id=getattr(b, "global_id", "") or "",
                snippet=snippet,
            )
        )
        if total < max_chars:
            take = text[: max(0, max_chars - total)]
            pieces.append(take)
            total += len(take)
    return "\n\n".join(pieces), evidence


async def _map_chapter(
    llm_client,
    *,
    series_id: str,
    doc_id: str,
    chapter_title: str,
    chapter_order: int,
    chapter_text: str,
    evidence_pool: list[StoryEvidence],
    extract: dict[str, bool] | None = None,
    per_type_cap: int = _DEFAULT_PER_TYPE_CAP,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    summary_max_chars: int = _DEFAULT_SUMMARY_MAX_CHARS,
) -> dict[str, Any]:
    modes = extract or {"relations": True, "events": True, "foreshadows": False}
    empty_meta = {
        "parse_failed": False,
        "likely_truncated": False,
        "raw_chars": 0,
        "finish_reason": "",
    }
    empty = {
        "events": [],
        "foreshadows": [],
        "relations": [],
        "_meta": dict(empty_meta),
    }
    if not chapter_text.strip():
        return empty

    id_list = [e.block_id for e in evidence_pool if e.block_id]
    system = _build_map_system(
        modes, per_type_cap=per_type_cap, summary_max_chars=summary_max_chars
    )
    user = (
        f"系列：{series_id}\n"
        f"doc_id：{doc_id}\n"
        f"章节序号：{chapter_order}\n"
        f"章节标题：{chapter_title}\n"
        f"可用 evidence_block_ids：{json.dumps(id_list[:40], ensure_ascii=False)}\n\n"
        f"## 章节原文（截断）\n{chapter_text[:5000]}\n"
    )

    if llm_client is None:
        first = chapter_text.strip().split("。")[0][: summary_max_chars]
        if not first or not modes.get("events"):
            return empty
        return {
            "events": (
                [
                    {
                        "summary": first + "。",
                        "event_type": "plot",
                        "characters": [],
                        "confidence": 0.35,
                        "evidence_block_ids": id_list[:1],
                        "story_time": {
                            "year": None, "period": "", "label": "", "relative": "",
                            "confidence": 0.0,
                        },
                    }
                ]
                if modes.get("events")
                else []
            ),
            "foreshadows": [],
            "relations": [],
            "_meta": dict(empty_meta),
        }

    raw = ""
    finish_reason = ""
    try:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if hasattr(llm_client, "achat_result"):
            result = await llm_client.achat_result(messages, max_tokens=max_tokens)
            raw = getattr(result, "content", "") or ""
            finish_reason = str(getattr(result, "finish_reason", "") or "")
        else:
            # All project LLM clients expose achat/achat_result; sync chat()
            # would return a str (unawaitable) — never used here.
            resp = await llm_client.achat(messages=messages, max_tokens=max_tokens)
            raw = resp if isinstance(resp, str) else getattr(resp, "content", "") or str(resp)
    except Exception as e:
        logger.warning("Story map LLM failed for %s/%s: %s", doc_id, chapter_title, e)
        return {
            **empty,
            "_meta": {
                **empty_meta,
                "parse_failed": True,
            },
        }

    raw_chars = len(raw or "")
    data = _parse_json_object(raw)
    parse_failed = data is None
    truncated_by_reason = finish_reason.lower() == "length"
    truncated_heuristic = parse_failed and (
        _looks_like_cut_json(raw) or raw_chars >= max(200, int(max_tokens * 0.85))
    )
    likely_truncated = truncated_by_reason or truncated_heuristic
    if parse_failed:
        data = {}
    cap = max(1, per_type_cap)
    return {
        "events": list(data.get("events") or [])[:cap] if modes.get("events") else [],
        "foreshadows": (
            list(data.get("foreshadows") or [])[:cap] if modes.get("foreshadows") else []
        ),
        "relations": (
            list(data.get("relations") or [])[:cap] if modes.get("relations") else []
        ),
        "_meta": {
            "parse_failed": parse_failed,
            "likely_truncated": bool(likely_truncated),
            "raw_chars": raw_chars,
            "finish_reason": finish_reason,
        },
    }


async def _map_chapter_with_retry(
    llm_client,
    *,
    series_id: str,
    doc_id: str,
    chapter_title: str,
    chapter_order: int,
    chapter_text: str,
    evidence_pool: list[StoryEvidence],
    extract: dict[str, bool],
    per_type_cap: int,
    max_tokens: int,
    summary_max_chars: int,
    map_retry: dict[str, Any],
) -> dict[str, Any]:
    """Map once; on truncate/parse_fail optionally retry with narrower extract."""
    payload = await _map_chapter(
        llm_client,
        series_id=series_id,
        doc_id=doc_id,
        chapter_title=chapter_title,
        chapter_order=chapter_order,
        chapter_text=chapter_text,
        evidence_pool=evidence_pool,
        extract=extract,
        per_type_cap=per_type_cap,
        max_tokens=max_tokens,
        summary_max_chars=summary_max_chars,
    )
    meta = payload.setdefault("_meta", {})
    meta["retry_attempted"] = False
    meta["retry_success"] = False
    need_retry = bool(meta.get("parse_failed") or meta.get("likely_truncated"))
    if not need_retry or not map_retry.get("enabled"):
        return payload
    max_retries = int(map_retry.get("max_retries", 1) or 0)
    if max_retries < 1:
        return payload

    retry_extract = dict(map_retry.get("retry_extract") or {"relations": True})
    if not any(retry_extract.values()):
        retry_extract = {"relations": True, "events": False, "foreshadows": False}
    retry_cap = int(map_retry.get("retry_per_type_cap", per_type_cap) or per_type_cap)
    meta["retry_attempted"] = True
    meta["retry_attempts"] = 1
    retry_payload = await _map_chapter(
        llm_client,
        series_id=series_id,
        doc_id=doc_id,
        chapter_title=chapter_title,
        chapter_order=chapter_order,
        chapter_text=chapter_text,
        evidence_pool=evidence_pool,
        extract=retry_extract,
        per_type_cap=retry_cap,
        max_tokens=max_tokens,
        summary_max_chars=summary_max_chars,
    )
    rmeta = retry_payload.get("_meta") if isinstance(retry_payload.get("_meta"), dict) else {}
    ok = not rmeta.get("parse_failed")
    if ok:
        retry_payload["_meta"] = {
            **rmeta,
            "retry_attempted": True,
            "retry_success": True,
            "retry_attempts": 1,
            "first_finish_reason": meta.get("finish_reason", ""),
            "first_truncated": bool(meta.get("likely_truncated")),
        }
        return retry_payload

    # Keep first payload shape but mark final failure after retry
    payload["_meta"] = {
        **meta,
        "parse_failed": True,
        "retry_success": False,
        "retry_attempts": 1,
        "retry_finish_reason": rmeta.get("finish_reason", ""),
    }
    return payload


