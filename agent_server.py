"""FastAPI wrapper for the modular Agent Framework."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

# Load environment variables before importing application modules.
_PROJECT_ROOT = Path(__file__).parent
_env_file = _PROJECT_ROOT / ".env"
if _env_file.exists():
    try:
        load_dotenv(_env_file, encoding="utf-8-sig")
    except UnicodeDecodeError:
        load_dotenv(_env_file, encoding="utf-16")

from src.api import state as api_state
from src.api.routers.approvals import router as approvals_router
from src.api.routers.characters import router as characters_router
from src.api.routers.chat import router as chat_router
from src.api.routers.impersonation import router as impersonation_router
from src.api.routers.llm_config import router as llm_config_router
from src.api.routers.memory_config import router as memory_config_router
from src.api.routers.novels import router as novels_router
from src.api.routers.ops import router as ops_router
from src.shared.llm_factory import create_shared_llm
from src.shared.logging_config import configure_logging
from src.shared.metrics import Timer, init_metrics, observe_http, set_active_sessions
from src.shared.request_context import reset_request_id, set_request_id
from src.shared.telemetry import (
    init_telemetry,
    instrument_fastapi,
    shutdown_telemetry,
)
from src.utils.auth import get_api_token, parse_cors_origins, require_bearer_token

configure_logging()
logger = logging.getLogger("agent_server")

_SESSION_KEEP = int(os.getenv("AGENT_SESSION_KEEP", "50"))
_JOB_TTL_HOURS = float(os.getenv("AGENT_JOB_TTL_HOURS", "72"))

# Strong refs for background preheat tasks (prevents GC "pending task"
# warnings); each task removes itself on completion.
_PREHEAT_TASKS: set[asyncio.Task] = set()


def _start_novel_store_preheat() -> None:
    """Warm the novel RAG store in a background thread.

    The first post-restart chat otherwise pays ~75s of synchronous init
    (embedding model load + LanceDB cache rebuild + keyword index rebuild).
    Fires into asyncio.to_thread so the event loop is never blocked; failures
    are logged by warm_up_novel_store and the lazy create_novel_store path
    remains as fallback.
    """
    try:
        cfg = yaml.safe_load(
            (_PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8")
        ) or {}
        if not cfg.get("novel_rag", {}).get("preheat", True):
            logger.info("novel_rag.preheat disabled — skipping novel store warm-up")
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Preheat config read failed (%s) — warm-up defaults on", exc)

    from src.application.novel.factory import warm_up_novel_store

    task = asyncio.create_task(asyncio.to_thread(warm_up_novel_store))
    _PREHEAT_TASKS.add(task)
    task.add_done_callback(_PREHEAT_TASKS.discard)
    logger.info("Novel store preheat scheduled in background thread")


def _run_startup_maintenance() -> None:
    """Prune old sessions/jobs and mark orphan running jobs failed on boot."""
    try:
        pruned = _conversation._store.cleanup_old_sessions(keep=_SESSION_KEEP)
        if pruned:
            logger.info("Pruned %d old session files (keep=%d)", pruned, _SESSION_KEEP)
    except Exception as exc:
        logger.warning("Session cleanup failed: %s", exc)
    try:
        pruned_imp = _imp_sessions.prune_persisted_sessions(keep=_SESSION_KEEP)
        if pruned_imp:
            logger.info(
                "Pruned %d old impersonation sessions (keep=%d)",
                pruned_imp,
                _SESSION_KEEP,
            )
    except Exception as exc:
        logger.warning("Impersonation session cleanup failed: %s", exc)
    try:
        from src.shared.async_jobs import get_job_runner, get_job_store

        removed = get_job_store().cleanup_older_than(_JOB_TTL_HOURS)
        if removed:
            logger.info("Pruned %d expired job files (ttl=%.0fh)", removed, _JOB_TTL_HOURS)
        orphans = get_job_runner().start()
        if orphans:
            logger.warning(
                "Marked %d orphan running jobs as failed (orphan_after_restart)",
                orphans,
            )
    except Exception as exc:
        logger.warning("Job startup maintenance failed: %s", exc)
    try:
        from src.shared.tool_approvals import get_approval_service

        stale = get_approval_service().cleanup_older_than(max_age_seconds=3600.0)
        if stale:
            logger.info("Pruned %d stale tool-approval records", stale)
    except Exception as exc:
        logger.warning("Approval cleanup failed: %s", exc)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    init_metrics()
    init_telemetry(service_name="makers-agent")
    _run_startup_maintenance()
    _start_novel_store_preheat()
    yield
    try:
        from src.shared.async_jobs import shutdown_job_runner

        marked = await shutdown_job_runner()
        if marked:
            logger.info(
                "Shutdown marked %d in-flight jobs cancelled_on_shutdown",
                marked,
            )
    except Exception as exc:
        logger.warning("Job runner shutdown failed: %s", exc)
    shutdown_telemetry()


app = FastAPI(
    title="Agent Server",
    description="Web API for the Modular Agent Framework — streaming plan→execute→reply",
    version="1.0.0",
    lifespan=_lifespan,
)
instrument_fastapi(app)

try:
    _cors_origins = parse_cors_origins()
except ValueError as exc:
    logger.error("Invalid CORS_ORIGINS: %s", exc)
    raise SystemExit(f"Invalid CORS_ORIGINS: {exc}") from exc

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

if get_api_token() is None:
    logger.warning(
        "AGENT_API_TOKEN is not set — protected routes will return 503 (fail-closed)"
    )
else:
    _PLACEHOLDER_TOKENS = {
        "change-me-to-a-long-random-token",
        "changeme",
        "your-token-here",
        "test",
        "token",
        "secret",
    }
    if get_api_token().lower() in _PLACEHOLDER_TOKENS or len(get_api_token()) < 16:
        logger.error(
            "AGENT_API_TOKEN is a placeholder/too short — refusing to start. "
            "Generate one with: python -c \"import secrets; "
            "print(secrets.token_urlsafe(32))\""
        )
        raise SystemExit(
            "AGENT_API_TOKEN looks like a placeholder (or <16 chars). Refusing to "
            "start with a weak API boundary token."
        )
    logger.info("AGENT_API_TOKEN configured — Bearer auth enabled")


@app.middleware("http")
async def bearer_auth_middleware(request: Request, call_next):
    """Enforce static Bearer token on all routes except public probes/metrics."""
    try:
        from src.shared.rate_limit import enforce_rate_limit

        enforce_rate_limit(request)
        require_bearer_token(request)
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=dict(exc.headers or {}),
        )
    return await call_next(request)


@app.middleware("http")
async def metrics_and_request_id_middleware(request: Request, call_next):
    """Attach X-Request-ID, bind logging context, and record Prometheus metrics."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    ctx_token = set_request_id(request_id)
    timer = Timer()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        path = request.url.path
        if path not in {"/metrics", "/api/v1/agent/health/live"}:
            observe_http(request.method, path, response.status_code, timer.elapsed())
            try:
                set_active_sessions(_conversation.active_sessions)
            except Exception:
                pass
        return response
    finally:
        reset_request_id(ctx_token)


# Runtime state and compatibility aliases used by tests and integrations.
api_state.reset_runtime_state()
CONFIG_PATH = api_state.CONFIG_PATH
_config_error: Optional[str] = None
_event_bus = api_state.event_bus
_conversation = api_state.conversation
_imp_sessions = api_state.imp_sessions
_imp_store = None


def _load_config():
    global _config_error
    api_state.CONFIG_PATH = CONFIG_PATH
    config = api_state.load_config()
    _config_error = api_state.config_error
    return config


def _require_config():
    return api_state.require_config()


def _get_tools_info() -> list:
    return api_state.get_tools_info()


async def _get_imp_store():
    """Lazy-initialize the shared NovelVectorStore for impersonation."""
    global _imp_store
    if api_state.store_dirty:
        # 上传新书后：重建 store（新 LanceDB 连接，读最新 IVF_PQ 索引；
        # keyword 索引已在 upload job 内强制重建并共享）→ 新书立即可检索。
        _imp_store = None
        api_state.store_dirty = False
        logger.info("Novel store dirty flag consumed — rebuilding on next access")
    if _imp_store is None and api_state.imp_store is not None:
        _imp_store = api_state.imp_store
    if _imp_store is None:
        from src.application.novel.factory import create_novel_store

        _imp_store = create_novel_store(backend="lancedb")
        # Dev convenience only: empty store may seed a bundled sample novel.
        # Default OFF — set AGENT_AUTO_INDEX_TEST_NOVEL=1 to enable.
        if (
            _imp_store.block_count() == 0
            and os.getenv("AGENT_AUTO_INDEX_TEST_NOVEL", "").strip().lower()
            in {"1", "true", "yes", "on"}
        ):
            await _try_index_test_novel(_imp_store)
        from src.tools.builtin_novel import inject_store as _novel_inject

        _novel_inject(_imp_store)
        # 统一广播：全部持 store 的工具共享同一实例（上传新书后索引一致）
        try:
            from src.application.tool_store_broadcast import broadcast_store

            broadcast_store(_imp_store)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Store broadcast failed: %s", exc)
    api_state.imp_store = _imp_store
    return _imp_store


async def _try_index_test_novel(store):
    """Optionally index a bundled sample novel when the store is empty.

    Controlled by AGENT_AUTO_INDEX_TEST_NOVEL (default disabled). Prefer
    uploading via the Knowledge UI in normal use.
    """
    candidates = [
        _PROJECT_ROOT / "data" / "测试小说.md",
        _PROJECT_ROOT / "tests" / "test_novel_data" / "镜湖风云录.md",
    ]
    test_novel = next((path for path in candidates if path.exists()), None)
    if not test_novel:
        return
    try:
        from src.application.novel.ingest import clean_doc_id, ingest_novel

        result = await ingest_novel(
            test_novel.read_bytes(),
            test_novel.name,
            doc_id=clean_doc_id(test_novel.stem),
            store=store,
            generate_qa=bool(os.getenv("DEEPSEEK_API_KEY")),
        )
        logger.info(
            "Auto-indexed: %d blocks (%dn/%dd/%dq), %d characters",
            result.total_blocks,
            result.narrative_blocks,
            result.dialogue_blocks,
            result.qa_blocks,
            len(result.characters or []),
        )
        from src.domain.character_card import CharacterCard

        for name in result.characters or []:
            try:
                await CharacterCard.build(name, store, force_rebuild=True)
            except Exception:
                pass
        logger.info("Built %d character cards", len(result.characters or []))
    except Exception as exc:
        logger.warning("Failed to auto-index test novel: %s", exc)


# Router dependencies are late-bound to preserve monkeypatch points above.
app.state.conversation = _conversation
app.state.imp_sessions = _imp_sessions
app.state.load_config = lambda: _load_config()
app.state.require_config = lambda: _require_config()
app.state.get_tools_info = lambda: _get_tools_info()
app.state.get_config_error = lambda: api_state.config_error
app.state.get_imp_store = lambda: _get_imp_store()
app.state.create_shared_llm = create_shared_llm

app.include_router(ops_router)
app.include_router(chat_router)
app.include_router(approvals_router)
app.include_router(impersonation_router)
app.include_router(llm_config_router)
app.include_router(memory_config_router)
app.include_router(characters_router)
app.include_router(novels_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("agent_server:app", host="0.0.0.0", port=8080, reload=True)
