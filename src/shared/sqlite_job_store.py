"""SQLite-backed async job store (WAL) for multi-process polling."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.shared.async_jobs import JobRecord, re_sub_type

logger = logging.getLogger("agent")

from src.domain.novel.series_paths import data_root

DEFAULT_JOB_DB = data_root() / "jobs" / "jobs.db"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SqliteJobStore:
    """Persist jobs in SQLite with the AsyncJobStore method surface."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_JOB_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Compatibility for callers that inspect .root
        self.root = self.db_path.parent
        self._mem: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        state TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        progress_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                cols = {
                    str(r[1])
                    for r in conn.execute("PRAGMA table_info(jobs)").fetchall()
                }
                if "progress_json" not in cols:
                    conn.execute(
                        "ALTER TABLE jobs ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}'"
                    )
                if "lease_owner" not in cols:
                    conn.execute("ALTER TABLE jobs ADD COLUMN lease_owner TEXT")
                if "lease_until" not in cols:
                    conn.execute("ALTER TABLE jobs ADD COLUMN lease_until TEXT")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_type_updated "
                    "ON jobs (job_type, updated_at DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_state_updated "
                    "ON jobs (state, updated_at DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_pending "
                    "ON jobs (state, created_at ASC)"
                )
            finally:
                conn.close()

    def create(
        self,
        job_type: str,
        *,
        payload: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> JobRecord:
        safe_type = re_sub_type(job_type)
        jid = job_id or f"{safe_type[:2]}_{uuid.uuid4().hex[:12]}"
        job = JobRecord(
            job_id=jid,
            job_type=safe_type,
            state="pending",
            payload=dict(payload or {}),
            created_at=_now(),
            updated_at=_now(),
        )
        self.save(job)
        return job

    def save(self, job: JobRecord) -> Path:
        job.job_type = re_sub_type(job.job_type)
        job.updated_at = _now()
        if not job.created_at:
            job.created_at = job.updated_at
        self._mem[job.job_id] = job
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, job_type, state, payload_json, result_json,
                        error, created_at, updated_at, progress_json,
                        lease_owner, lease_until
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                    ON CONFLICT(job_id) DO UPDATE SET
                        job_type=excluded.job_type,
                        state=excluded.state,
                        payload_json=excluded.payload_json,
                        result_json=excluded.result_json,
                        error=excluded.error,
                        updated_at=excluded.updated_at,
                        progress_json=excluded.progress_json,
                        lease_owner=CASE
                            WHEN excluded.state='running' THEN jobs.lease_owner
                            ELSE NULL
                        END,
                        lease_until=CASE
                            WHEN excluded.state='running' THEN jobs.lease_until
                            ELSE NULL
                        END
                    """,
                    (
                        job.job_id,
                        job.job_type,
                        job.state,
                        json.dumps(job.payload or {}, ensure_ascii=False),
                        json.dumps(job.result or {}, ensure_ascii=False),
                        job.error,
                        job.created_at,
                        job.updated_at,
                        json.dumps(job.progress or {}, ensure_ascii=False),
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()
        return self.db_path

    def get(self, job_id: str, job_type: str | None = None) -> JobRecord | None:
        if job_id in self._mem:
            cached = self._mem[job_id]
            if job_type is None or cached.job_type == re_sub_type(job_type):
                return cached
        with self._lock:
            conn = self._connect()
            try:
                if job_type:
                    row = conn.execute(
                        "SELECT * FROM jobs WHERE job_id=? AND job_type=?",
                        (job_id, re_sub_type(job_type)),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT * FROM jobs WHERE job_id=?",
                        (job_id,),
                    ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        job = self._row_to_job(row)
        self._mem[job.job_id] = job
        return job

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        keys = set(row.keys())
        progress = {}
        if "progress_json" in keys:
            try:
                progress = json.loads(row["progress_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                progress = {}
        return JobRecord(
            job_id=str(row["job_id"]),
            job_type=str(row["job_type"]),
            state=str(row["state"]),
            payload=json.loads(row["payload_json"] or "{}"),
            result=json.loads(row["result_json"] or "{}"),
            progress=dict(progress or {}),
            error=row["error"],
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def update_progress(self, job_id: str, progress: dict[str, Any], *, job_type: str | None = None) -> JobRecord | None:
        job = self.get(job_id, job_type=job_type)
        if job is None:
            return None
        job.progress = dict(progress or {})
        self.save(job)
        return job

    def list(
        self,
        job_type: str | None = None,
        *,
        series_id: str | None = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        limit = max(1, int(limit))
        with self._lock:
            conn = self._connect()
            try:
                if job_type:
                    rows = conn.execute(
                        """
                        SELECT * FROM jobs
                        WHERE job_type=?
                        ORDER BY updated_at DESC
                        LIMIT ?
                        """,
                        (re_sub_type(job_type), limit * 3),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM jobs
                        ORDER BY updated_at DESC
                        LIMIT ?
                        """,
                        (limit * 3,),
                    ).fetchall()
            finally:
                conn.close()
        out: list[JobRecord] = []
        for row in rows:
            job = self._row_to_job(row)
            self._mem[job.job_id] = job
            if series_id and job.payload.get("series_id") != series_id:
                continue
            out.append(job)
            if len(out) >= limit:
                break
        return out

    def mark_orphans_failed(self, job_type: str | None = None) -> int:
        n = 0
        for job in self.list(job_type, limit=500):
            if job.state == "running":
                job.state = "failed"
                job.error = "orphan_after_restart"
                self.save(job)
                n += 1
        return n

    def reclaim_expired_leases(self) -> int:
        """Return expired running leases to pending for another worker."""
        now = _now()
        reclaimed = 0
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    """
                    UPDATE jobs
                    SET state='pending', lease_owner=NULL, lease_until=NULL,
                        error=NULL, updated_at=?
                    WHERE state='running'
                      AND lease_until IS NOT NULL
                      AND lease_until < ?
                    """,
                    (now, now),
                )
                reclaimed = int(cur.rowcount or 0)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()
        if reclaimed:
            # Drop stale mem cache entries
            self._mem.clear()
            logger.info("Reclaimed %d expired job leases", reclaimed)
        return reclaimed

    def claim_next_pending(
        self,
        *,
        owner: str,
        lease_sec: float = 600.0,
        job_types: list[str] | None = None,
    ) -> JobRecord | None:
        """Atomically claim the oldest pending job (SQLite BEGIN IMMEDIATE)."""
        self.reclaim_expired_leases()
        owner = (owner or "worker").strip() or "worker"
        lease_sec = max(30.0, float(lease_sec))
        until = datetime.now(UTC).timestamp() + lease_sec
        lease_until = datetime.fromtimestamp(until, tz=UTC).isoformat()
        now = _now()
        types = [re_sub_type(t) for t in (job_types or []) if t]

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                if types:
                    placeholders = ",".join("?" for _ in types)
                    row = conn.execute(
                        f"""
                        SELECT job_id FROM jobs
                        WHERE state='pending' AND job_type IN ({placeholders})
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        tuple(types),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT job_id FROM jobs
                        WHERE state='pending'
                        ORDER BY created_at ASC
                        LIMIT 1
                        """
                    ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                job_id = str(row["job_id"])
                cur = conn.execute(
                    """
                    UPDATE jobs
                    SET state='running', lease_owner=?, lease_until=?,
                        error=NULL, updated_at=?
                    WHERE job_id=? AND state='pending'
                    """,
                    (owner, lease_until, now, job_id),
                )
                if int(cur.rowcount or 0) != 1:
                    conn.execute("COMMIT")
                    return None
                full = conn.execute(
                    "SELECT * FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()

        if full is None:
            return None
        job = self._row_to_job(full)
        self._mem[job.job_id] = job
        return job

    def cleanup_older_than(self, max_age_hours: float = 72.0) -> int:
        if max_age_hours <= 0:
            return 0
        cutoff = datetime.now(UTC).timestamp() - (max_age_hours * 3600)
        removed = 0
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    "SELECT job_id, state, updated_at FROM jobs "
                    "WHERE state IN ('done', 'failed')"
                ).fetchall()
                for row in rows:
                    try:
                        ts = datetime.fromisoformat(
                            str(row["updated_at"]).replace("Z", "+00:00")
                        ).timestamp()
                    except ValueError:
                        continue
                    if ts >= cutoff:
                        continue
                    conn.execute("DELETE FROM jobs WHERE job_id=?", (row["job_id"],))
                    self._mem.pop(str(row["job_id"]), None)
                    removed += 1
                conn.execute("COMMIT")
            except Exception as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                logger.warning("Job cleanup failed: %s", exc)
                return 0
            finally:
                conn.close()
        return removed
