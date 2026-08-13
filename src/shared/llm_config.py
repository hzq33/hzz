"""LLM 调用点配置服务 — 前端可单独配置每个 LLM 调用点（服务商/模型/key/参数）。

存储：``data/llm_config.json``（运行时配置，前端通过 API 读写，不暴露后端配置文件细节）。
覆盖：各 LLM 调用点创建 client 时读取本服务；未配置项回退 config.yaml / 内置默认。
API key：JSON 明文存储于本地数据目录（单租户可信环境），前端回显掩码 ``sk-***``。

服务商预设：deepseek / openai / moonshot / glm（智谱清言）/ ollama / custom（自定义 base_url）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("agent.llm_config")

from src.domain.novel.series_paths import data_root

_CONFIG_PATH = data_root() / "llm_config.json"

# ── 服务商预设 ───────────────────────────────────────────
PROVIDERS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
    },
    "moonshot": {
        "label": "Moonshot (Kimi)",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k"],
    },
    "glm": {
        "label": "智谱清言 (GLM)",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.7-flash", "GLM-4.5-Flash"],
    },
    "ollama": {
        "label": "Ollama 本地",
        "base_url": "http://localhost:11434/v1",
        "models": ["qwen3:8b", "qwen2.5:7b", "deepseek-r1:8b"],
    },
    "siliconflow": {
        "label": "硅基流动 (SiliconFlow)",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": [
            "Qwen/Qwen3-8B",
            "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
            "Qwen/Qwen2.5-7B-Instruct",
        ],
    },
    "custom": {"label": "自定义", "base_url": "", "models": []},
}

# ── 调用点清单（9 个，按组归类；已合并/删除冗余项）────────────────
# defaults 与 config.yaml / 现状对齐；未配置项回退这些默认。
# 合并说明：
#   agent_chat 吸收 planner / executor / swarm（通用助手共用 client，Planner 默认关）
#   impersonation_chat 吸收 impersonation_service（一次性扮演生成）
#   dialogue_extract 吸收 dialogue_harvest / speaker_attribution / dialogue_deepen
#   character_inventory 吸收 character_ner / character_validate / character_builder / character_on_demand
#   （删除 dead 项：ingest_coordinator / jobs / dialogue_harvest / character_ner / character_validate）
_ENDPOINTS: dict[str, dict[str, Any]] = {
    # ── 对话生成 ──
    "agent_chat": {
        "group": "对话生成", "label": "通用助手 · 对话/计划/执行/Swarm",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.7, "max_tokens": 4096},
    },
    "impersonation_chat": {
        "group": "对话生成", "label": "角色扮演 · 回复生成（含一次性生成）",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.85, "max_tokens": 1024},
    },
    "impersonation_tool": {
        "group": "对话生成", "label": "角色扮演 · 工具决策（低温）",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.3, "max_tokens": 512},
    },
    # ── 检索链路 ──
    "intent_router": {
        "group": "检索链路", "label": "检索 · 意图路由 (LLM 分类)",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.0, "max_tokens": 400},
    },
    "query_rewriter": {
        "group": "检索链路", "label": "检索 · 查询改写（单次完整改写）",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.3, "max_tokens": 800},
    },
    # ── 入库任务 ──
    "dialogue_extract": {
        "group": "入库任务", "label": "入库 · 对话抽取/归因/收割/深化",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.0, "max_tokens": 6144},
    },
    "character_inventory": {
        "group": "入库任务", "label": "入库 · 角色提取（NER/校验/建卡/按需）",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.0, "max_tokens": 2048},
    },
    "character_inventory_normalize": {
        "group": "入库任务", "label": "入库 · 角色归一（全局合并/去噪/拆分裁决）",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.0, "max_tokens": 8192},
    },
    "story_analysis": {
        "group": "入库任务", "label": "入库 · 剧情分析（时间线/伏笔/关系）",
        "defaults": {"model": "deepseek-v4-pro", "temperature": 0.0, "max_tokens": 4096},
    },
    "graph_rag_summary": {
        "group": "入库任务", "label": "入库 · GraphRAG 社区摘要（主线/关系网）",
        "defaults": {"model": "deepseek-v4-flash", "temperature": 0.2, "max_tokens": 1024},
    },
    "qa_generator": {
        "group": "入库任务", "label": "入库 · QA 问答对生成（默认关）",
        "defaults": {"model": "deepseek-v4-pro", "temperature": 0.0, "max_tokens": 2048},
    },
}

DEFAULT_PROVIDER = "deepseek"
DEFAULT_API_KEY_ENV = "DEEPSEEK_API_KEY"

# ── 配置读写 ─────────────────────────────────────────────
_cache: dict[str, dict[str, Any]] | None = None


def _load_raw() -> dict[str, dict[str, Any]]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        if _CONFIG_PATH.exists():
            _cache = json.loads(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        else:
            _cache = {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_config load failed (%s); using empty", exc)
        _cache = {}
    return _cache


def reload() -> None:
    """重新从磁盘加载（配置热更新）。"""
    global _cache
    _cache = None
    _load_raw()


def save(config: dict[str, dict[str, Any]]) -> None:
    """持久化全部配置（前端 PUT 入口）。"""
    global _cache
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _cache = config


def reset() -> None:
    """清空前端覆盖，全部回退默认。"""
    if _CONFIG_PATH.exists():
        _CONFIG_PATH.unlink()
    global _cache
    _cache = None


def mask_key(key: str) -> str:
    """掩码回显：sk-abc123 → sk-***123。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return key[:3] + "***" + key[-4:]


def _effective_api_key(endpoint_key: str, entry: dict[str, Any] | None) -> str:
    """生效 key：前端覆盖 > 环境变量（DEEPSEEK_API_KEY）。"""
    if entry and entry.get("api_key"):
        return str(entry["api_key"])
    import os

    return os.getenv(DEFAULT_API_KEY_ENV, "").strip()


def get_endpoint_config(endpoint: str) -> dict[str, Any]:
    """返回某调用点的生效配置（默认 + 覆盖合并）。供各 LLM 调用点创建 client 使用。"""
    spec = _ENDPOINTS.get(endpoint)
    if spec is None:
        logger.warning("Unknown llm endpoint %r; returning defaults", endpoint)
        spec = {
            "group": "未分类",
            "label": endpoint,
            "defaults": {"model": "deepseek-v4-flash", "temperature": 0.7, "max_tokens": 2048},
        }
    raw = _load_raw().get(endpoint) or {}
    defaults = dict(spec["defaults"])
    provider = str(raw.get("provider") or DEFAULT_PROVIDER)
    base_url = str(raw.get("base_url") or "")
    if not base_url:
        base_url = PROVIDERS.get(provider, {}).get("base_url", "")
    return {
        "provider": provider,
        "base_url": base_url,
        "model": str(raw.get("model") or defaults.get("model") or "deepseek-v4-flash"),
        "api_key": _effective_api_key(endpoint, raw),
        "temperature": float(raw.get("temperature", defaults.get("temperature", 0.7))),
        "max_tokens": int(raw.get("max_tokens", defaults.get("max_tokens", 2048))),
        "enabled": bool(raw.get("enabled", True)),
        # 思考模式："auto"（跟随 DeepSeek 自动禁用）|"on"（启用）|"off"（禁用）
        "thinking": str(raw.get("thinking") or "auto"),
    }


def endpoints_snapshot() -> list[dict[str, Any]]:
    """给前端 GET：全部调用点 + 当前生效配置（key 掩码）。"""
    raw = _load_raw()
    out: list[dict[str, Any]] = []
    for key, spec in _ENDPOINTS.items():
        entry = raw.get(key) or {}
        defaults = spec["defaults"]
        provider = str(entry.get("provider") or DEFAULT_PROVIDER)
        api_key = _effective_api_key(key, entry)
        out.append(
            {
                "key": key,
                "group": spec["group"],
                "label": spec["label"],
                "config": {
                    "provider": provider,
                    "model": str(entry.get("model") or defaults.get("model")),
                    "api_key_masked": mask_key(api_key),
                    "has_api_key": bool(api_key),
                    "temperature": float(
                        entry.get("temperature", defaults.get("temperature", 0.7))
                    ),
                    "max_tokens": int(
                        entry.get("max_tokens", defaults.get("max_tokens", 2048))
                    ),
                    "enabled": bool(entry.get("enabled", True)),
                    "thinking": str(entry.get("thinking") or "auto"),
                },
            }
        )
    return out


def provider_list() -> list[dict[str, Any]]:
    return [
        {
            "key": k,
            "label": v["label"],
            "base_url": v.get("base_url", ""),
            "models": list(v.get("models") or []),
        }
        for k, v in PROVIDERS.items()
    ]


def thinking_extra_body(endpoint: str) -> dict | None:
    """DeepSeek 思考模式 extra_body（供直连 AsyncOpenAI 的调用点使用）。

    - "on"  → 返回 None（启用思考，不注入禁用指令）
    - "off" / "auto" → 返回 {"thinking": {"type": "disabled"}}（禁用）
    失败回退禁用（DeepSeek 默认行为）。
    """
    try:
        ep = get_endpoint_config(endpoint)
        th = str(ep.get("thinking") or "auto").strip().lower()
        if th == "on":
            return None
    except Exception:  # noqa: BLE001
        pass
    return {"thinking": {"type": "disabled"}}
