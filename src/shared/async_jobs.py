"""Reusable local async job store + bounded runner.

Used by character on-demand builds and story analysis. Jobs persist as JSON
under data/jobs/{job_type}/ so GET polling survives process restarts; orphan
``running`` jobs are marked failed on runner start.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent")

from src.domain.novel.series_paths import data_root

_JOB_ROOT = data_root() / "jobs"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    state: str = "pending"  # pending|running|done|failed
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    progress: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        return cls(
            job_id=str(data.get("job_id") or ""),
            job_type=str(data.get("job_type") or ""),
            state=str(data.get("state") or "pending"),
            payload=dict(data.get("payload") or {}),
            result=dict(data.get("result") or {}),
            progress=dict(data.get("progress") or {}),
            error=data.get("error"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


class AsyncJobStore:
    """Filesystem-backed job persistence."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else _JOB_ROOT
        self._mem: dict[str, JobRecord] = {}

    def _dir(self, job_type: str) -> Path:
        safe = re_sub_type(job_type)
        d = self.root / safe
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _path(self, job_type: str, job_id: str) -> Path:
        return self._dir(job_type) / f"{job_id}.json"

    def create(
        self,
        job_type: str,
        *,
        payload: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> JobRecord:
        jid = job_id or f"{job_type[:2]}_{uuid.uuid4().hex[:12]}"
        job = JobRecord(
            job_id=jid,
            job_type=job_type,
            state="pending",
            payload=dict(payload or {}),
            created_at=_now(),
            updated_at=_now(),
        )
        self.save(job)
        return job

    def update_progress(self, job_id: str, progress: dict[str, Any], *, job_type: str | None = None) -> JobRecord | None:
        """Persist progress for a running job (polling UX)."""
        job = self.get(job_id, job_type=job_type)
        if job is None:
            return None
        job.progress = dict(progress or {})
        self.save(job)
        return job

    def save(self, job: JobRecord) -> Path:
        job.updated_at = _now()
        self._mem[job.job_id] = job
        path = self._path(job.job_type, job.job_id)
        payload = json.dumps(job.to_dict(), ensure_ascii=False, indent=2)
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
        return path

    def get(self, job_id: str, job_type: str | None = None) -> JobRecord | None:
        if job_id in self._mem:
            return self._mem[job_id]
        if job_type:
            path = self._path(job_type, job_id)
            return self._load_path(path)
        if not self.root.exists():
            return None
        for p in self.root.glob(f"*/{job_id}.json"):
            job = self._load_path(p)
            if job:
                return job
        return None

    def _load_path(self, path: Path) -> JobRecord | None:
        if not path.exists():
            return None
        try:
            job = JobRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            self._mem[job.job_id] = job
            return job
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def list(
        self,
        job_type: str | None = None,
        *,
        series_id: str | None = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        jobs: list[JobRecord] = []
        if job_type:
            d = self._dir(job_type)
            paths = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        elif self.root.exists():
            paths = sorted(self.root.glob("*/*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        else:
            paths = []
        for p in paths:
            job = self._load_path(p)
            if not job:
                continue
            if series_id and job.payload.get("series_id") != series_id:
                continue
            jobs.append(job)
            if len(jobs) >= limit:
                break
        return jobs

    def mark_orphans_failed(self, job_type: str | None = None) -> int:
        """Mark leftover running jobs as failed (process restart recovery)."""
        n = 0
        for job in self.list(job_type, limit=500):
            if job.state == "running":
                job.state = "failed"
                job.error = "orphan_after_restart"
                self.save(job)
                n += 1
        return n

    def cleanup_older_than(self, max_age_hours: float = 72.0) -> int:
        """Delete terminal job files older than ``max_age_hours``.

        Pending/running jobs are never deleted. Returns number of files removed.
        """
        if max_age_hours <= 0 or not self.root.exists():
            return 0
        cutoff = datetime.now(UTC).timestamp() - (max_age_hours * 3600)
        removed = 0
        for path in self.root.glob("*/*.json"):
            try:
                job = self._load_path(path)
                if job is None:
                    continue
                if job.state not in ("done", "failed"):
                    continue
                mtime = path.stat().st_mtime
                # Prefer updated_at when parseable
                try:
                    if job.updated_at:
                        mtime = datetime.fromisoformat(
                            job.updated_at.replace("Z", "+00:00")
                        ).timestamp()
                except ValueError:
                    pass
                if mtime >= cutoff:
                    continue
                path.unlink(missing_ok=True)
                self._mem.pop(job.job_id, None)
                removed += 1
            except OSError:
                continue
        return removed

    def reclaim_expired_leases(self) -> int:
        return 0

    def claim_next_pending(
        self,
        *,
        owner: str,
        lease_sec: float = 600.0,
        job_types: list[str] | None = None,
    ) -> JobRecord | None:
        """Best-effort claim for JSON backend (single-process safe enough for tests)."""
        types = {re_sub_type(t) for t in (job_types or []) if t}
        candidates: list[JobRecord] = []
        for job in self.list(limit=500):
            if job.state != "pending":
                continue
            if types and job.job_type not in types:
                continue
            candidates.append(job)
        if not candidates:
            return None
        candidates.sort(key=lambda j: j.created_at or "")
        job = candidates[0]
        job.state = "running"
        job.error = None
        job.payload = {
            **(job.payload or {}),
            "_lease_owner": owner,
            "_lease_until": _now(),
        }
        self.save(job)
        return job


def re_sub_type(job_type: str) -> str:
    import re
    return re.sub(r"[^\w\-]+", "_", (job_type or "job").strip()) or "job"


JobHandler = Callable[[JobRecord], Awaitable[dict[str, Any]]]


class AsyncJobRunner:
    """Bounded concurrency runner over a job store."""

    def __init__(
        self,
        store: Any | None = None,
        *,
        concurrency: int = 2,
        type_limits: dict[str, int] | None = None,
    ):
        self.store = store or create_job_store()
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._concurrency = max(1, concurrency)
        self._type_limits = {
            str(k): max(1, int(v)) for k, v in dict(type_limits or {}).items()
        }
        self._type_sems: dict[str, asyncio.Semaphore] = {
            jt: asyncio.Semaphore(limit) for jt, limit in self._type_limits.items()
        }
        self._tasks: set[asyncio.Task] = set()
        self._started = False
        self._shutting_down = False

    @property
    def in_flight(self) -> int:
        return len(self._tasks)

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @property
    def type_limits(self) -> dict[str, int]:
        return dict(self._type_limits)

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> int:
        """Idempotent start; marks orphan running jobs failed. Returns orphan count."""
        if self._started:
            return 0
        n = int(self.store.mark_orphans_failed() or 0)
        if n:
            logger.warning("Marked %d orphan running jobs as failed", n)
            try:
                from src.shared.metrics import observe_job_orphans

                observe_job_orphans(n)
            except Exception:
                pass
        self._started = True
        return n

    def enqueue(self, job: JobRecord, handler: JobHandler) -> JobRecord:
        if self._shutting_down:
            job.state = "failed"
            job.error = "cancelled_on_shutdown"
            self.store.save(job)
            return job
        self.start()
        self.store.save(job)
        type_sem = self._type_sems.get(job.job_type)

        async def _run() -> None:
            async with self._sem:
                if type_sem is not None:
                    async with type_sem:
                        await self._execute(job, handler)
                else:
                    await self._execute(job, handler)

        task = asyncio.create_task(_run())
        self._tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            try:
                from src.shared.metrics import set_jobs_in_flight

                set_jobs_in_flight(len(self._tasks))
            except Exception:
                pass

        task.add_done_callback(_done)
        try:
            from src.shared.metrics import set_jobs_in_flight

            set_jobs_in_flight(len(self._tasks))
        except Exception:
            pass
        return job

    async def _execute(self, job: JobRecord, handler: JobHandler) -> None:
        if self._shutting_down:
            job.state = "failed"
            job.error = "cancelled_on_shutdown"
            self.store.save(job)
            return
        job.state = "running"
        job.error = None
        self.store.save(job)
        from src.shared.telemetry import span

        with span(
            "job.run",
            job_id=job.job_id,
            job_type=job.job_type,
            worker_mode="inprocess",
        ):
            try:
                result = await handler(job)
                job.result = dict(result or {})
                # Allow handler to set a more specific terminal state via result["_state"]
                terminal = job.result.pop("_state", None)
                job.state = str(terminal or "done")
                if job.state == "failed" and not job.error:
                    job.error = job.result.get("error") or "failed"
            except asyncio.CancelledError:
                job.state = "failed"
                job.error = "cancelled_on_shutdown"
                raise
            except Exception as e:
                logger.exception("Job %s failed", job.job_id)
                job.state = "failed"
                job.error = str(e)
            finally:
                self.store.save(job)
                try:
                    from src.shared.metrics import observe_job_terminal, set_jobs_in_flight

                    observe_job_terminal(job.job_type, job.state)
                    set_jobs_in_flight(max(0, len(self._tasks) - 1))
                except Exception:
                    pass

    async def shutdown(self, *, grace_sec: float = 15.0) -> int:
        """Cancel in-flight tasks and mark leftover running jobs failed.

        Returns number of store rows marked ``cancelled_on_shutdown``.
        """
        self._shutting_down = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=max(0.1, float(grace_sec)))
        marked = 0
        try:
            for job in self.store.list(limit=500):
                if job.state == "running":
                    job.state = "failed"
                    job.error = "cancelled_on_shutdown"
                    self.store.save(job)
                    marked += 1
                    try:
                        from src.shared.metrics import observe_job_terminal

                        observe_job_terminal(job.job_type, job.state)
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Job shutdown sweep failed: %s", exc)
        try:
            from src.shared.metrics import set_jobs_in_flight

            set_jobs_in_flight(0)
        except Exception:
            pass
        logger.info(
            "Job runner shutdown: cancelled_tasks=%d marked_running=%d",
            len(tasks),
            marked,
        )
        return marked


# Process-wide defaults (lazy)
_default_store: Any | None = None
_default_runner: AsyncJobRunner | None = None


def create_job_store(root: Path | None = None):
    """Create job store: sqlite (default) or json files."""
    import os

    backend = os.getenv("AGENT_JOB_BACKEND", "sqlite").strip().lower()
    if backend in {"json", "file"}:
        return AsyncJobStore(root=root)
    from src.shared.sqlite_job_store import DEFAULT_JOB_DB, SqliteJobStore

    if root is not None:
        return SqliteJobStore(db_path=Path(root) / "jobs.db")
    db_env = os.getenv("AGENT_JOB_DB", "").strip()
    db_path = Path(db_env) if db_env else DEFAULT_JOB_DB
    return SqliteJobStore(db_path=db_path)


def get_job_store():
    global _default_store
    if _default_store is None:
        _default_store = create_job_store()
    return _default_store


def _default_concurrency() -> int:
    import os

    try:
        return max(1, int(os.getenv("AGENT_JOB_CONCURRENCY", "2")))
    except (TypeError, ValueError):
        return 2


def _load_type_limits() -> dict[str, int]:
    """Per-job-type semaphore caps (in addition to global concurrency)."""
    import os

    mapping = {
        "novel_upload": "AGENT_JOB_UPLOAD_CONCURRENCY",
        "story_analysis": "AGENT_JOB_STORY_CONCURRENCY",
        "character_build": "AGENT_JOB_CHARACTER_CONCURRENCY",
    }
    out: dict[str, int] = {}
    for job_type, env_key in mapping.items():
        raw = os.getenv(env_key, "").strip()
        if not raw:
            # Upload is heavy (embed); default single-flight when unset.
            if job_type == "novel_upload":
                out[job_type] = 1
            continue
        try:
            out[job_type] = max(1, int(raw))
        except (TypeError, ValueError):
            continue
    return out


def get_job_runner(concurrency: int | None = None) -> AsyncJobRunner:
    """Return process-wide runner.

    Global concurrency comes from ``AGENT_JOB_CONCURRENCY`` (the ``concurrency``
    kwarg is ignored after the singleton exists, and is ignored in favour of
    the env default when creating it — use typed env caps for upload/story).
    """
    global _default_runner
    if _default_runner is None:
        if concurrency is not None and concurrency != _default_concurrency():
            logger.debug(
                "get_job_runner(concurrency=%s) ignored; using AGENT_JOB_CONCURRENCY=%s",
                concurrency,
                _default_concurrency(),
            )
        _default_runner = AsyncJobRunner(
            get_job_store(),
            concurrency=_default_concurrency(),
            type_limits=_load_type_limits(),
        )
    elif concurrency is not None and concurrency != _default_runner.concurrency:
        logger.debug(
            "get_job_runner(concurrency=%s) ignored; singleton already started",
            concurrency,
        )
    return _default_runner


async def shutdown_job_runner(*, grace_sec: float | None = None) -> int:
    """Shutdown the process-wide runner if it was created."""
    import os

    global _default_runner
    if _default_runner is None:
        return 0
    if grace_sec is None:
        try:
            grace_sec = float(os.getenv("AGENT_JOB_SHUTDOWN_GRACE_SEC", "15"))
        except (TypeError, ValueError):
            grace_sec = 15.0
    return await _default_runner.shutdown(grace_sec=grace_sec)


def reset_job_runner_for_tests() -> None:
    """Drop process-wide store/runner singletons (tests only)."""
    global _default_store, _default_runner
    _default_store = None
    _default_runner = None
