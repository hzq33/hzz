"""需求驱动测试套件共享 fixtures（离线、无 LLM/网络、不碰真实数据）。

黑盒原则（docs/REQUIREMENTS.md）：
- 测试从需求 ID 出发，只通过公开 API（TestClient）与公开类/函数验证行为；
- 不 import 私有符号（_ 开头）、不断言实现细节；
- 所有数据写入重定向到临时目录，绝不污染真实 data/。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from src.domain.novel.models import NovelBlock
from src.infrastructure.embedding import MockEmbeddingProvider
from src.infrastructure.novel_store import NovelVectorStore

# 测试固定使用的鉴权 token（client fixture 中强制覆盖环境，保证确定性）
API_TOKEN = "test-token-for-api-tests"


# ── 防污染（autouse）────────────────────────────────────────


@pytest.fixture(autouse=True)
def _disable_rag_trace(monkeypatch):
    """测试环境不写检索 trace（data/traces/），避免污染在线评估数据。"""
    monkeypatch.setenv("RAG_TRACE_ENABLED", "0")


@pytest.fixture(autouse=True)
def _sandbox_data_paths(monkeypatch, tmp_path):
    """所有数据写入重定向到临时目录。

    series_paths._DATA 是 roster/catalog/inventory 等路径函数的唯一数据源；
    部分模块在 import 时把路径常量绑定到真实 data/（模块级缓存），必须一并
    重定向，否则测试会写真实 data/（幽灵数据）。
    需要真实 data 的测试应显式请求真实路径而非依赖默认。
    """
    from src.domain.novel import series_paths

    monkeypatch.setattr(series_paths, "_DATA", tmp_path / "data")
    _redirect_module_dir_constants(monkeypatch, tmp_path / "data")
    return tmp_path / "data"


def _redirect_module_dir_constants(monkeypatch, data_root) -> None:
    """重定向 import 时绑定真实 data/ 的模块级路径常量。"""
    bindings = [
        ("src.api.routers.alias_roster", "DATA_DIR", "rosters"),
        ("src.application.impersonation_sessions", "_DEFAULT_SESSION_DIR", "sessions/imp"),
        ("src.application.novel.entity_resolver", "_ROSTER_DIR", "rosters"),
        ("src.application.novel.entity_resolver", "_CHARACTER_DIR", "characters"),
        ("src.application.novel.redialogue", "_REDIALOGUE_DIR", "redialogue"),
        ("src.application.novel.redialogue", "_INVENTORY_DIR", "inventories"),
        ("src.domain.novel.alias_map", "_ROSTER_DIR", "rosters"),
        ("src.domain.novel.catalog", "_CATALOG_DIR", "catalogs"),
        ("src.domain.novel.character_inventory.candidates", "_INVENTORY_DIR", "inventories"),
        ("src.domain.novel.character_on_demand.jobs", "_JOB_DIR", "character_jobs"),
        ("src.domain.novel.character_roster", "_ROSTER_DIR", "rosters"),
        ("src.domain.novel.dialogue_meta_store", "_META_DIR", "dialogue_meta"),
        ("src.domain.novel.graph_rag", "_GRAPH_RAG_DIR", "graph_rag"),
        ("src.domain.novel.graph_rag", "_GRAPH_DIR", "graphs"),
        ("src.domain.novel.story_analysis.config", "_ANALYSIS_DIR", "story_analyses"),
        ("src.domain.novel.story_analysis.lorebook", "_LOREBOOK_DIR", "lorebooks"),
        ("src.domain.novel.story_analysis.timeline", "_TIMELINE_DIR", "timelines"),
        ("src.shared.llm_config", "_CONFIG_PATH", "llm_config.json"),
    ]
    import importlib

    for module_name, attr, rel in bindings:
        try:
            mod = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - 模块缺依赖时跳过
            continue
        monkeypatch.setattr(mod, attr, data_root / rel)


# ── FakeLLM ──────────────────────────────────────────────────


class FakeLLM:
    """可配置的 LLM 桩：覆盖 achat / achat_stream / achat_with_tools。

    用法：
        FakeLLM(payload="{\"names\": [...]}")            # 成功返回
        FakeLLM(exc=RuntimeError("boom"))                # 调用抛错
        FakeLLM(stream_chunks=["你好", "我是测试角色"])   # 流式
    """

    def __init__(
        self,
        payload: str | None = None,
        *,
        exc: Exception | None = None,
        stream_chunks: list[str] | None = None,
        tool_result: str | None = None,
    ):
        self.payload = payload
        self.exc = exc
        self.stream_chunks = stream_chunks
        self.tool_result = tool_result
        self.calls = 0
        self.last_messages: list | None = None

    async def achat(self, messages: list, **kwargs) -> str:
        self.calls += 1
        self.last_messages = messages
        if self.exc:
            raise self.exc
        return self.payload or "（测试回复）"

    async def achat_stream(self, messages: list, **kwargs):
        self.calls += 1
        self.last_messages = messages
        if self.exc:
            raise self.exc
        for chunk in self.stream_chunks or ["（测试回复）"]:
            yield chunk

    async def achat_with_tools(self, messages: list, **kwargs) -> str:
        self.calls += 1
        self.last_messages = messages
        if self.exc:
            raise self.exc
        # SwarmAgent native 路径期待 ToolLoopResult（含 invocations 列表）
        from src.shared.llm import ToolLoopResult

        return ToolLoopResult(
            content=self.tool_result or self.payload or "（测试回复）",
            messages=[],
            invocations=[],
        )


# ── 合成数据 helpers ─────────────────────────────────────────


def make_block(
    global_id: str,
    doc_id: str,
    block_type: str = "narrative",
    *,
    narrative_text: str = "",
    chapter_title: str = "",
    dialogues: list | None = None,
    characters: list | None = None,
    **extra,
) -> NovelBlock:
    """构造最小 NovelBlock（公开数据类）。"""
    return NovelBlock(
        global_id=global_id,
        doc_id=doc_id,
        block_type=block_type,
        narrative_text=narrative_text,
        chapter_title=chapter_title,
        dialogues=dialogues or [],
        characters=characters or [],
        **extra,
    )


def make_store(*, dimensions: int = 32) -> NovelVectorStore:
    """内存 FAISS 测试库（公开构造，MockEmbedding 确定性向量）。"""
    return NovelVectorStore(
        embedding=MockEmbeddingProvider(dimensions=dimensions),
        dimensions=dimensions,
        backend="faiss",
    )


async def index_blocks(store: NovelVectorStore, blocks: list[NovelBlock]) -> int:
    return await store.index_batch(blocks)


# ── API client（黑盒 HTTP 层）────────────────────────────────


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """注入临时会话/扮演存储 + 空库 store + FakeLLM 的 TestClient。

    覆盖点（均为公开挂载点）：
    - AGENT_API_TOKEN 固定值，鉴权确定性
    - app.state.conversation / imp_sessions → 临时目录
    - app.state.get_imp_store → 空内存 store
    - app.state.create_shared_llm → FakeLLM
    """
    from fastapi.testclient import TestClient

    # 必须先覆盖 token 再 import agent_server：agent_server 模块级校验
    # 弱 token（<16 字符 / 占位符）会 SystemExit；CI 环境的
    # AGENT_API_TOKEN=ci-test-token 会触发拒绝。测试环境不依赖 .env。
    monkeypatch.setenv("AGENT_API_TOKEN", API_TOKEN)

    from agent_server import app
    from src.application.conversation_service import ConversationService
    from src.application.impersonation_sessions import ImpersonationSessionService

    monkeypatch.setattr(
        app.state,
        "imp_sessions",
        ImpersonationSessionService(session_dir=tmp_path / "imp"),
    )
    monkeypatch.setattr(
        app.state,
        "conversation",
        ConversationService(max_sessions=5, session_dir=tmp_path / "chat"),
    )

    store = make_store()
    llm = FakeLLM()

    async def _fake_store():
        return store

    monkeypatch.setattr(app.state, "get_imp_store", _fake_store)
    monkeypatch.setattr(app.state, "create_shared_llm", lambda *a, **kw: llm)
    # 预算持久化：隔离到临时库，避免污染真实 data/budget.db
    from src.shared import session_budget

    monkeypatch.setattr(session_budget, "set_budget_db_path", lambda p=None: None)
    session_budget.set_budget_db_path(tmp_path / "budget.db")
    # 通用 chat 的 Agent 在 src/core/agent.py 模块内 import create_shared_llm，
    # 必须 patch 该模块引用（app.state 的只覆盖路由层路径）
    import src.core.agent as core_agent

    monkeypatch.setattr(core_agent, "create_shared_llm", lambda *a, **kw: llm)
    # 供测试断言 LLM 调用（多轮上下文 / 流式 chunk 等）
    monkeypatch.setattr(app.state, "_test_llm", llm, raising=False)
    return TestClient(app, raise_server_exceptions=False)


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {API_TOKEN}"}
