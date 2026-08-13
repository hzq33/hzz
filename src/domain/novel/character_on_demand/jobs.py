"""Character build job store — SQLite-backed job records.

Extracted from the former monolithic ``character_on_demand.py``; logic unchanged.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.domain.novel.character_on_demand.models import CharacterBuildJob

logger = logging.getLogger("agent")

_JOB_TYPE = "character_build"
from src.domain.novel.series_paths import data_root

_JOB_DIR = data_root() / "character_jobs"  # legacy JSON fallback
_JOBS: dict[str, CharacterBuildJob] = {}
_JOB_STORE_OVERRIDE: Any = None  # tests inject a temp SqliteJobStore / AsyncJobStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _get_job_store():
    if _JOB_STORE_OVERRIDE is not None:
        return _JOB_STORE_OVERRIDE
    from src.shared.async_jobs import get_job_store

    return get_job_store()


def _to_job_record(job: CharacterBuildJob):
    from src.shared.async_jobs import JobRecord

    return JobRecord(
        job_id=job.job_id,
        job_type=_JOB_TYPE,
        state=job.state,
        payload={
            "series_id": job.series_id,
            "doc_id": job.doc_id,
            "input_name": job.input_name,
            "character_id": job.character_id,
            "canonical_name": job.canonical_name,
            "aliases": list(job.aliases or []),
        },
        result={
            "evidence": dict(job.evidence or {}),
            "flags": dict(job.flags or {}),
            "card_path": job.card_path,
        },
        error=job.error,
        created_at=job.created_at or _now(),
        updated_at=job.updated_at or _now(),
    )


def _from_job_record(rec) -> CharacterBuildJob:
    payload = dict(rec.payload or {})
    result = dict(rec.result or {})
    return CharacterBuildJob(
        job_id=rec.job_id,
        series_id=str(payload.get("series_id") or ""),
        doc_id=payload.get("doc_id"),
        input_name=str(payload.get("input_name") or ""),
        character_id=str(payload.get("character_id") or ""),
        canonical_name=str(payload.get("canonical_name") or ""),
        aliases=list(payload.get("aliases") or []),
        state=str(rec.state or "pending"),
        evidence=dict(result.get("evidence") or {}),
        flags=dict(result.get("flags") or {}),
        error=rec.error,
        card_path=result.get("card_path"),
        created_at=str(rec.created_at or ""),
        updated_at=str(rec.updated_at or ""),
    )


def _save_job(job: CharacterBuildJob) -> None:
    job.updated_at = _now()
    if not job.created_at:
        job.created_at = job.updated_at
    _JOBS[job.job_id] = job
    try:
        _get_job_store().save(_to_job_record(job))
    except Exception as exc:
        logger.warning("Shared job store save failed for %s: %s", job.job_id, exc)
        # Legacy fallback so local tooling still has a file when store is unavailable.
        _JOB_DIR.mkdir(parents=True, exist_ok=True)
        path = _JOB_DIR / f"{job.job_id}.json"
        path.write_text(
            json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )


def get_job(job_id: str) -> CharacterBuildJob | None:
    """Load job; prefer shared store so runner failures are visible to pollers."""
    try:
        rec = _get_job_store().get(job_id, _JOB_TYPE)
        if rec is not None:
            job = _from_job_record(rec)
            _JOBS[job_id] = job
            return job
    except Exception as exc:
        logger.debug("Shared job store get failed for %s: %s", job_id, exc)

    if job_id in _JOBS:
        return _JOBS[job_id]

    path = _JOB_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        job = CharacterBuildJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
        _JOBS[job_id] = job
        return job
    except Exception:
        return None


def list_jobs(series_id: str | None = None, limit: int = 50) -> list[CharacterBuildJob]:
    out: list[CharacterBuildJob] = []
    seen: set[str] = set()
    try:
        for rec in _get_job_store().list(_JOB_TYPE, series_id=series_id, limit=limit):
            job = _from_job_record(rec)
            _JOBS[job.job_id] = job
            if job.job_id in seen:
                continue
            seen.add(job.job_id)
            out.append(job)
        return out
    except Exception as exc:
        logger.debug("Shared job store list failed: %s", exc)

    jobs = list(_JOBS.values())
    if _JOB_DIR.exists():
        for p in sorted(_JOB_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            jid = p.stem
            if jid not in _JOBS:
                j = get_job(jid)
                if j:
                    jobs.append(j)
    if series_id:
        jobs = [j for j in jobs if j.series_id == series_id]
    for j in sorted(jobs, key=lambda x: x.updated_at or x.created_at, reverse=True):
        if j.job_id in seen:
            continue
        seen.add(j.job_id)
        out.append(j)
        if len(out) >= limit:
            break
    return out

