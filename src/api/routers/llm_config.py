"""LLM 调用点配置 API — 前端「设置」页读写 + 连接测试。

不暴露后端配置细节：前端只通过本 API 操作（GET 快照 / PUT 保存 / POST 测试）。
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.shared.llm_config import (
    endpoints_snapshot,
    get_endpoint_config,
    provider_list,
    reload,
    save,
)
from src.utils.auth import require_bearer_token

logger = logging.getLogger("agent.llm_config_api")

router = APIRouter(prefix="/api/v1/agent")


@router.get("/llm-config")
async def llm_config_get(request: Request) -> dict:
    require_bearer_token(request)
    try:
        return {
            "providers": provider_list(),
            "endpoints": endpoints_snapshot(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("llm-config GET failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.put("/llm-config")
async def llm_config_put(request: Request) -> dict:
    require_bearer_token(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)

    try:
        current = {}
        try:
            from src.shared.llm_config import _load_raw

            current = _load_raw() or {}
        except Exception:  # noqa: BLE001
            current = {}

        # 支持两种形态：{endpoint: config} 或 {endpoint, config} 单项
        if "endpoint" in body and "config" in body:
            key = str(body["endpoint"])
            entry = body["config"]
            if not isinstance(entry, dict):
                return JSONResponse({"error": "config must be an object"}, status_code=400)
            current[key] = _sanitize_entry(entry, current.get(key))
        else:
            for key, entry in (body or {}).items():
                if isinstance(entry, dict):
                    current[str(key)] = _sanitize_entry(entry, current.get(str(key)))

        save(current)
        reload()
        return {"ok": True, "endpoints": endpoints_snapshot()}
    except Exception as exc:  # noqa: BLE001
        logger.exception("llm-config PUT failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/llm-config/test")
async def llm_config_test(request: Request) -> dict:
    require_bearer_token(request)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    endpoint = str((body or {}).get("endpoint") or "")
    if not endpoint:
        return JSONResponse({"error": "endpoint required"}, status_code=400)

    try:
        cfg = get_endpoint_config(endpoint)
        # 若本次请求带覆盖配置（测试未保存的改动），优先用请求里的
        if isinstance((body or {}).get("config"), dict):
            c = (body or {})["config"]
            provider = str(c.get("provider") or cfg["provider"])
            from src.shared.llm_config import PROVIDERS

            cfg = {
                "provider": provider,
                "base_url": str(c.get("base_url") or cfg["base_url"])
                or PROVIDERS.get(provider, {}).get("base_url", ""),
                "model": str(c.get("model") or cfg["model"]),
                "api_key": str(c.get("api_key") or cfg["api_key"]),
                "temperature": float(c.get("temperature", cfg["temperature"])),
                "max_tokens": int(c.get("max_tokens", cfg["max_tokens"])),
                "enabled": bool(c.get("enabled", cfg.get("enabled", True))),
            }
        return await _test_connection(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("llm-config test failed")
        return {"ok": False, "error": str(exc)}


def _sanitize_entry(entry: dict, current_entry: dict | None = None) -> dict:
    """只保留合法字段，丢弃未知键（防前端传脏数据）。

    api_key 特殊处理：GET 快照只回显掩码（``sk-***123`` / ``***``），若前端把
    快照原样 PUT 回来，掩码/空值不得覆盖真实 key —— 只有非掩码的非空值才允许更新。
    """
    allowed = {
        "provider", "base_url", "model",
        "api_key", "temperature", "max_tokens", "enabled", "thinking",
    }
    cleaned = {k: v for k, v in entry.items() if k in allowed}
    if "api_key" in cleaned:
        key = str(cleaned["api_key"] or "").strip()
        if not key or "***" in key:
            # 掩码/空：保留已存真实 key；无旧值时删掉字段，避免写入空串覆盖
            old = (current_entry or {}).get("api_key")
            if old:
                cleaned["api_key"] = old
            else:
                cleaned.pop("api_key", None)
    return cleaned


async def _test_connection(cfg: dict) -> dict:
    """用配置发一次最小 chat completion 验证连通性（不保存）。"""
    import asyncio

    from openai import AsyncOpenAI

    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip()
    if not base:
        return {"ok": False, "error": "未配置 base_url（请选择服务商或填自定义地址）"}
    if not key:
        return {"ok": False, "error": "未配置 API key"}
    if not model:
        return {"ok": False, "error": "未配置模型名"}

    # base_url 已含版本路径（/v1、/v4 等）则直接用，否则补 /v1（OpenAI 兼容惯例）
    if not re.search(r"/v\d+$", base):
        base = base + "/v1"

    client = AsyncOpenAI(api_key=key, base_url=base)
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=4,
                temperature=0,
            ),
            timeout=20,
        )
        return {"ok": True, "model": model, "reply": (resp.choices[0].message.content or "")[:50]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    finally:
        await client.close()
