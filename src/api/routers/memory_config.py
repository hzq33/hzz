"""记忆与上下文配置：GET 快照 / PUT 保存（写回 config.yaml → memory 段）。"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from src.utils.auth import require_bearer_token

logger = logging.getLogger("agent_server")

router = APIRouter(prefix="/api/v1/agent")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"

# 允许前端修改的 memory 段字段（白名单，避免任意写配置）
_EDITABLE_KEYS = (
    "max_history_tokens",
    "enable_summarization",
    "summarize_keep_turns",
    "summarize_threshold",
)


def _load_memory_section() -> dict:
    import yaml

    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory-config: read config.yaml failed: %s", exc)
        return {}
    return dict((data.get("memory") or {}))


def _save_memory_section(updates: dict) -> bool:
    """Write memory section back to config.yaml, preserving comments poorly.

    用 yaml dump 重写整个文件会丢失注释/其他段格式；这里仅在文件是
    yaml 可解析时做整体重写，失败则静默放弃（配置优先手工维护）。
    """
    import yaml

    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory-config: read for save failed: %s", exc)
        return False
    mem = dict(data.get("memory") or {})
    mem.update(updates)
    data["memory"] = mem
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory-config: write config.yaml failed: %s", exc)
        return False
    return True


@router.get("/memory-config")
async def memory_config_get(request: Request) -> dict:
    require_bearer_token(request)
    try:
        mem = _load_memory_section()
        return {
            "max_history_tokens": int(mem.get("max_history_tokens", 8000)),
            "enable_summarization": bool(mem.get("enable_summarization", False)),
            "summarize_keep_turns": int(mem.get("summarize_keep_turns", 8)),
            "summarize_threshold": float(mem.get("summarize_threshold", 0.8)),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("memory-config GET failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.put("/memory-config")
async def memory_config_put(request: Request) -> dict:
    require_bearer_token(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)

    updates: dict = {}
    try:
        if "max_history_tokens" in body:
            val = int(body["max_history_tokens"])
            if val < 100 or val > 200_000:
                return JSONResponse(
                    {"error": "max_history_tokens must be 100..200000"}, status_code=400
                )
            updates["max_history_tokens"] = val
        if "enable_summarization" in body:
            updates["enable_summarization"] = bool(body["enable_summarization"])
        if "summarize_keep_turns" in body:
            val = int(body["summarize_keep_turns"])
            if val < 1 or val > 100:
                return JSONResponse(
                    {"error": "summarize_keep_turns must be 1..100"}, status_code=400
                )
            updates["summarize_keep_turns"] = val
        if "summarize_threshold" in body:
            val = float(body["summarize_threshold"])
            if val < 0.1 or val > 1.0:
                return JSONResponse(
                    {"error": "summarize_threshold must be 0.1..1.0"}, status_code=400
                )
            updates["summarize_threshold"] = val
    except (TypeError, ValueError):
        return JSONResponse({"error": "invalid numeric value"}, status_code=400)

    if not updates:
        return JSONResponse({"error": "no editable fields provided"}, status_code=400)
    if not _save_memory_section(updates):
        return JSONResponse({"error": "failed to write config.yaml"}, status_code=500)

    # 热生效：无全局缓存，后续新建会话读取 config.yaml 时自动用新配置。
    logger.info("memory-config updated: %s", updates)
    return {"ok": True, **updates}
