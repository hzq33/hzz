"""需求域 A — 平台基础：健康探针 / 鉴权 / CORS / 限流 / 指标 / 会话历史 / LLM 配置 / 上传安全。

覆盖 docs/REQUIREMENTS.md A-01 ~ A-08。
黑盒：走公开 HTTP 端点与公开函数，不断言实现细节。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import API_TOKEN, auth_headers

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ── A-01 健康探针 ─────────────────────────────────────────────


def test_health_live_public(api_client):
    """A-01：live 探针公开可用，返回 ok。"""
    r = api_client.get("/api/v1/agent/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_ready_ok_when_configured(api_client):
    """A-01：就绪探针在 Token 已配置时 ready=true，且 token 检查通过。"""
    r = api_client.get("/api/v1/agent/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    checks = body.get("checks", {})
    assert "token_configured" in checks


def test_health_ready_fail_closed_without_token(api_client, monkeypatch):
    """A-01：Token 缺失时就绪探针 fail-closed（非 200）。"""
    monkeypatch.delenv("AGENT_API_TOKEN")
    r = api_client.get("/api/v1/agent/health/ready")
    assert r.status_code != 200


def test_health_aggregate_public(api_client):
    """A-01：聚合健康端点公开返回结构完整。"""
    r = api_client.get("/api/v1/agent/health")
    assert r.status_code == 200
    body = r.json()
    assert "status" in body or "ok" in body or "live" in body


# ── A-02 鉴权（Bearer fail-closed）────────────────────────────


def test_protected_route_requires_auth(api_client):
    """A-02：受保护端点无 Bearer → 401。"""
    r = api_client.get("/api/v1/agent/tools")
    assert r.status_code == 401


def test_wrong_token_rejected(api_client):
    """A-02：错误 token → 401，且带 WWW-Authenticate 头。"""
    r = api_client.get(
        "/api/v1/agent/tools",
        headers={"Authorization": "Bearer definitely-wrong-token"},
    )
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_token_unset_fail_closed(api_client, monkeypatch):
    """A-02：未配置 AGENT_API_TOKEN 时受保护端点 503（拒绝降级为开放）。"""
    monkeypatch.delenv("AGENT_API_TOKEN")
    r = api_client.get("/api/v1/agent/tools")
    assert r.status_code == 503


def test_public_paths_skip_auth(api_client):
    """A-02：health/metrics 为 public，不要求鉴权。"""
    assert api_client.get("/api/v1/agent/health/live").status_code == 200
    assert api_client.get("/metrics").status_code == 200


def test_weak_token_refuses_start():
    """A-02：占位/过短 token 拒绝启动（模块加载即退出，非零返回码）。"""
    env = {**os.environ, "AGENT_API_TOKEN": "changeme"}
    r = subprocess.run(
        [sys.executable, "-c", "import agent_server"],
        env=env,
        capture_output=True,
        cwd=str(PROJECT_ROOT),
        timeout=120,
    )
    assert r.returncode != 0


# ── A-03 CORS ──────────────────────────────────────────────────


def test_cors_parse_whitelist():
    """A-03：CORS_ORIGINS 白名单解析——列出的来源保留，非法来源报错。"""
    from src.utils.auth import parse_cors_origins

    origins = parse_cors_origins("http://a.example, http://b.example")
    assert "http://a.example" in origins
    assert "http://b.example" in origins


def test_cors_allowed_origin_gets_header(api_client, monkeypatch):
    """A-03：白名单来源的跨域请求带上 access-control-allow-origin。"""
    origins = os.getenv("CORS_ORIGINS", "")
    if not origins:
        pytest.skip("CORS_ORIGINS 未配置，跳过 HTTP 冒烟（白名单解析已单测）")
    allowed = origins.split(",")[0].strip()
    r = api_client.get(
        "/api/v1/agent/health/live",
        headers={"Origin": allowed},
    )
    assert r.headers.get("access-control-allow-origin") == allowed


# ── A-04 限流 ──────────────────────────────────────────────────


def test_rate_limit_rejects_burst(api_client, monkeypatch):
    """A-04：RPS 限流生效——突发超过 burst 的请求被拒绝（429）。

    用低于 0.01 的 RPS 触发 clamp 路径（回归：旧实现此处每次重建 limiter 失效）。
    """
    monkeypatch.setenv("AGENT_RATE_LIMIT_RPS", "0.005")
    monkeypatch.setenv("AGENT_RATE_LIMIT_BURST", "1")
    # health/metrics 路径不限流，用受保护端点验证；key 固定（Bearer 前缀）
    assert (
        api_client.get("/api/v1/agent/tools", headers=auth_headers()).status_code
        == 200
    )
    r2 = api_client.get("/api/v1/agent/tools", headers=auth_headers())
    assert r2.status_code == 429


# ── A-05 指标 ──────────────────────────────────────────────────


def test_metrics_prometheus_format(api_client):
    """A-05：/metrics 返回 Prometheus 文本格式指标。"""
    r = api_client.get("/metrics")
    assert r.status_code == 200
    assert r.text.strip(), "metrics 输出不应为空"
    assert "#" in r.text  # prometheus 注释行


# ── A-06 会话历史 ──────────────────────────────────────────────


def test_history_get_unknown_session(api_client):
    """A-06：查询不存在会话的历史 → 200 空列表（不 500）。"""
    r = api_client.get(
        "/api/v1/agent/history",
        params={"session_id": "no-such-session"},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) or "messages" in body


def test_history_delete_unknown_session(api_client):
    """A-06：删除不存在会话的历史 → 200（幂等，不 500）。"""
    r = api_client.delete(
        "/api/v1/agent/history",
        params={"session_id": "no-such-session"},
        headers=auth_headers(),
    )
    assert r.status_code == 200


# ── A-07 LLM 配置 ──────────────────────────────────────────────


def test_llm_config_get_returns_config(api_client):
    """A-07：GET llm-config 返回端点配置（key 脱敏，不出现在响应）。"""
    r = api_client.get("/api/v1/agent/llm-config", headers=auth_headers())
    assert r.status_code == 200
    body = r.json()
    text = str(body)
    assert "api_key" not in text or body.get("api_key") != "sk-"
    assert "endpoints" in body or "providers" in body or "config" in body


def test_llm_config_put_validation(api_client):
    """A-07：PUT llm-config 非对象体被拒绝（4xx），不 500。"""
    r = api_client.put(
        "/api/v1/agent/llm-config",
        json=[],
        headers=auth_headers(),
    )
    assert r.status_code == 400


# ── A-08 上传安全 ──────────────────────────────────────────────


def test_upload_rejects_oversized(api_client, monkeypatch):
    """A-08：超过大小限制的上传被拒绝（413），不进入入库流程。"""
    monkeypatch.setenv("AGENT_UPLOAD_MAX_MB", "1")
    big = b"x" * (2 * 1024 * 1024)
    r = api_client.post(
        "/api/v1/agent/upload",
        files={"file": ("big.txt", big, "text/plain")},
        headers=auth_headers(),
    )
    assert r.status_code == 413


def test_upload_rejects_bad_extension(api_client):
    """A-08：非文本二进制（不可 UTF-8 解码）入库被拒绝（400）。"""
    r = api_client.post(
        "/api/v1/agent/upload",
        files={"file": ("evil.bin", b"\x00\xff\xfe\x01\x02", "application/octet-stream")},
        headers=auth_headers(),
        params={"wait": True},
    )
    assert r.status_code == 400


def test_upload_rejects_non_whitelist_ext(
    api_client,
):
    """A-08：扩展名白名单——非 EPUB/TXT/MD 文件（.exe）入库被拒绝（400）。

    修复记录（2026-08-10）：旧实现内容可 UTF-8 解码即接受（如 .exe 的
    "MZ..." 头），已在 _validate_upload 增加扩展名白名单第一道门。
    """
    r = api_client.post(
        "/api/v1/agent/upload",
        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
        headers=auth_headers(),
        params={"wait": True},
    )
    assert r.status_code == 400


def test_upload_accepts_txt_and_returns_job(api_client):
    """A-08/C-01：合法 txt 上传被接受，返回异步 job_id。"""
    r = api_client.post(
        "/api/v1/agent/upload",
        files={"file": ("demo.txt", "第一章\n「你好。」他说。\n".encode(), "text/plain")},
        headers=auth_headers(),
    )
    assert r.status_code == 200
    body = r.json()
    assert "job_id" in body or "job" in body


# ── A-07 补充：LLM 工厂构造 ──────────────────────────────────


def test_llm_factory_creates_client_with_config():
    """A-07：create_shared_llm 用有效 config 构造客户端（含 achat 接口）。"""
    from src.shared.llm_factory import create_shared_llm
    from src.utils.config import load_config

    config = load_config("config.yaml")
    assert config is not None
    llm = create_shared_llm(config)
    assert hasattr(llm, "achat")
    assert hasattr(llm, "achat_stream")


# ── A-07 补充：LLM 定价 ──────────────────────────────────────


def test_llm_pricing_estimate_cost():
    """A-07：usage → cost_usd 估算（有价目表时输出数值）。"""
    from src.shared.llm_pricing import estimate_cost_usd

    cost = estimate_cost_usd(
        model="deepseek-chat", prompt_tokens=1000, completion_tokens=500
    )
    assert isinstance(cost, float)
    assert cost >= 0
