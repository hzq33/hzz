"""Readiness checks for durable storage and process configuration."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _probe_writable_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".ready_{uuid.uuid4().hex[:8]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, "ok"
    except OSError as exc:
        return False, str(exc)


def _probe_sqlite(db_path: Path) -> tuple[bool, str]:
    import sqlite3

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS _ready_probe (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO _ready_probe(id) VALUES (1) ON CONFLICT DO NOTHING")
            conn.execute("DELETE FROM _ready_probe WHERE id=1")
            conn.commit()
        finally:
            conn.close()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def _lance_path_from_config() -> Path:
    raw = "./data/novel_lance"
    try:
        import yaml

        cfg_path = _PROJECT_ROOT / "config.yaml"
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            novel = data.get("novel_rag") or {}
            raw = novel.get("lance_path") or raw
    except Exception:
        pass
    path = Path(str(raw))
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def _novel_rag_config() -> dict[str, Any]:
    try:
        import yaml

        cfg_path = _PROJECT_ROOT / "config.yaml"
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            return dict(data.get("novel_rag") or {})
    except Exception:
        pass
    return {}


def _resolve_model_path(raw: str) -> Path:
    path = Path(str(raw or "").strip() or ".")
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def _weight_check(label: str, raw_path: str) -> dict[str, Any]:
    path = _resolve_model_path(raw_path)
    present = path.exists()
    return {
        "ok": True,  # soft — never blocks ready
        "label": label,
        "path": str(path),
        "present": present,
        "degraded": not present,
    }


def check_readiness(
    *,
    token_configured: bool,
    config_ok: bool,
    config_error: str | None = None,
) -> dict[str, Any]:
    """Return readiness payload with per-check status.

    Hard checks gate ``ready`` / HTTP 503. Soft checks (job runtime, embed
    weights) only set ``degraded`` / warning fields — still 200 when hard ok.
    """
    checks: dict[str, Any] = {
        "token_configured": {"ok": token_configured},
        "config_ok": {"ok": config_ok, "error": config_error},
    }

    data_root = _PROJECT_ROOT / "data"
    ok, err = _probe_writable_dir(data_root)
    checks["data_dir_writable"] = {"ok": ok, "path": str(data_root), "error": None if ok else err}

    session_backend = os.getenv("AGENT_SESSION_BACKEND", "sqlite").strip().lower()
    if session_backend in {"json", "file"}:
        sess_dir = _PROJECT_ROOT / "data" / "sessions"
        ok, err = _probe_writable_dir(sess_dir)
        checks["session_store"] = {
            "ok": ok,
            "backend": "json",
            "path": str(sess_dir),
            "error": None if ok else err,
        }
    else:
        db_env = os.getenv("AGENT_SESSION_DB", "").strip()
        db_path = Path(db_env) if db_env else (_PROJECT_ROOT / "data" / "sessions" / "sessions.db")
        if not db_path.is_absolute():
            db_path = _PROJECT_ROOT / db_path
        ok, err = _probe_sqlite(db_path)
        checks["session_store"] = {
            "ok": ok,
            "backend": "sqlite",
            "path": str(db_path),
            "error": None if ok else err,
        }

    job_backend = os.getenv("AGENT_JOB_BACKEND", "sqlite").strip().lower()
    if job_backend in {"json", "file"}:
        job_dir = _PROJECT_ROOT / "data" / "jobs"
        ok, err = _probe_writable_dir(job_dir)
        checks["job_store"] = {
            "ok": ok,
            "backend": "json",
            "path": str(job_dir),
            "error": None if ok else err,
        }
    else:
        job_db_env = os.getenv("AGENT_JOB_DB", "").strip()
        job_db = Path(job_db_env) if job_db_env else (_PROJECT_ROOT / "data" / "jobs" / "jobs.db")
        if not job_db.is_absolute():
            job_db = _PROJECT_ROOT / job_db
        ok, err = _probe_sqlite(job_db)
        checks["job_store"] = {
            "ok": ok,
            "backend": "sqlite",
            "path": str(job_db),
            "error": None if ok else err,
        }

    lance = _lance_path_from_config()
    ok, err = _probe_writable_dir(lance)
    checks["lance_writable"] = {"ok": ok, "path": str(lance), "error": None if ok else err}

    # Soft: in-flight / leftover running rows (warning only — do not 503)
    jobs_running = 0
    runner_started = False
    jobs_in_flight = 0
    concurrency = None
    try:
        from src.shared import async_jobs as aj

        store = aj.get_job_store()
        for job in store.list(limit=500):
            if job.state == "running":
                jobs_running += 1
        runner = aj._default_runner
        if runner is not None:
            runner_started = bool(runner.started)
            jobs_in_flight = int(runner.in_flight)
            concurrency = int(runner.concurrency)
    except Exception as exc:
        checks["job_runtime"] = {
            "ok": True,
            "warning": True,
            "error": str(exc),
            "jobs_running_count": 0,
            "jobs_in_flight": 0,
            "runner_started": False,
        }
    else:
        checks["job_runtime"] = {
            "ok": True,
            "warning": jobs_running > 0,
            "jobs_running_count": jobs_running,
            "jobs_in_flight": jobs_in_flight,
            "runner_started": runner_started,
            "concurrency": concurrency,
            "job_backend": job_backend if job_backend not in {"json", "file"} else "json",
        }

    # Soft: local embed / reranker weights
    nr = _novel_rag_config()
    embed_raw = str(nr.get("qwen3_model_path") or "models/Qwen3-Embedding-0.6B")
    rerank_raw = str(nr.get("reranker_model_path") or "models/Qwen3-Reranker-0.6B")
    checks["embed_weights"] = _weight_check("embed", embed_raw)
    checks["reranker_weights"] = _weight_check("reranker", rerank_raw)

    hard_keys = (
        "token_configured",
        "config_ok",
        "data_dir_writable",
        "session_store",
        "job_store",
        "lance_writable",
    )
    ready = all(checks[k].get("ok") for k in hard_keys)
    degraded = any(
        bool(item.get("degraded") or item.get("warning"))
        for key, item in checks.items()
        if key not in hard_keys
    )

    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "degraded": degraded,
        "checks": checks,
        "jobs_running_count": jobs_running,
        "jobs_in_flight": jobs_in_flight,
    }
