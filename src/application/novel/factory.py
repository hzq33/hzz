"""Novel RAG factory — build stores and services from project config.

Single entry point for creating NovelVectorStore, NovelRetrieval, and
ImpersonationService with the correct config-driven embedding provider.
Eliminates manual Qwen3/Mock plumbing in demo scripts and tools.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any


from src.application.novel.impersonation import ImpersonationService
from src.application.novel.retrieval import NovelRetrieval
from src.infrastructure.embedding import (
    EmbeddingProvider,
    MockEmbeddingProvider,
    OpenAIEmbeddingProvider,
    Qwen3EmbeddingProvider,
)
from src.infrastructure.novel_store import NovelVectorStore

logger = logging.getLogger(__name__)

# ── Process-level caches for heavyweight resources ─────────
# Embedding models take seconds to load on CUDA and keyword indexes take
# seconds to rebuild over thousands of blocks; both are read-mostly and
# safe to reuse across store instances. Keyed by config/lance path so
# multiple configs or test temp dirs never share state.
_EMBEDDING_CACHE: dict[str, EmbeddingProvider] = {}
_KEYWORD_CACHE: dict[str, Any] = {}
# Reranker 模型（cross-encoder）同样重：跨 NovelRetrieval 实例复用，避免每会话重复加载。
# Key: (provider, model_path)。top_n 由调用方 rerank 时传参，不参与 key。
_RERANKER_CACHE: dict[tuple, Any] = {}

# ── Per-key build locks ─────────────────────────────────────
# The cache lookups above are check-then-build, not atomic: a background
# preheat thread and a concurrent first user request can both miss the cache
# and build the heavyweight resources twice (two model loads, two keyword
# rebuilds). A per-key lock serializes the first build; later calls hit the
# cache with zero lock contention beyond the guard's dict access.
_BUILD_LOCKS_GUARD = threading.Lock()
_BUILD_LOCKS: dict[tuple, threading.Lock] = {}


def _build_lock(key: tuple) -> threading.Lock:
    """Return the per-key build lock, creating it once under a guard."""
    with _BUILD_LOCKS_GUARD:
        lock = _BUILD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _BUILD_LOCKS[key] = lock
        return lock


def reset_factory_caches() -> None:
    """Drop process-level caches (embedding provider / keyword index).

    Exposed for tests and for reload paths that must rebuild from scratch.
    """
    _EMBEDDING_CACHE.clear()
    _KEYWORD_CACHE.clear()
    _RERANKER_CACHE.clear()


def _load_raw_config(path: str = "config.yaml") -> dict:
    """Load raw YAML config (no env var substitution), cached by mtime."""
    from src.shared.defaults import load_yaml_cached

    return load_yaml_cached(path) or {}


def _substitute_env(value: str) -> str:
    """Replace ${VAR} with environment variable (delegates to shared impl)."""
    from src.shared.defaults import resolve_env_placeholders

    return resolve_env_placeholders(value)


def create_embedding_provider(
    config_path: str = "config.yaml",
) -> EmbeddingProvider:
    """Build an EmbeddingProvider from config.yaml novel_rag section.

    Reads 'novel_rag.embedding_provider':
        - "qwen3"  → Qwen3EmbeddingProvider
        - "openai" → OpenAIEmbeddingProvider
        - "mock"   → MockEmbeddingProvider (fallback)
    """
    cfg = _load_raw_config(config_path)
    nr = cfg.get("novel_rag", {})
    provider = nr.get("embedding_provider", "mock")

    cached = _EMBEDDING_CACHE.get(config_path)
    if cached is not None:
        return cached

    # 检查-构建非原子：并发直接调用（不经 create_novel_store 的锁路径）
    # 会重复加载模型。per-key 锁串行化首次构建。
    lock = _build_lock((config_path,))
    with lock:
        cached = _EMBEDDING_CACHE.get(config_path)
        if cached is not None:
            return cached

        if provider == "qwen3":
            model_path = nr.get("qwen3_model_path", "models/Qwen3-Embedding-0.6B")
            built = Qwen3EmbeddingProvider(model_path=model_path, use_fp16=False)
        elif provider == "openai":
            agent_cfg = cfg.get("agent", {})
            built = OpenAIEmbeddingProvider(
                api_key=_substitute_env(agent_cfg.get("api_key", "")),
                base_url=_substitute_env(agent_cfg.get("base_url", "")),
            )
        else:
            built = MockEmbeddingProvider(dimensions=768)
        _EMBEDDING_CACHE[config_path] = built
        return built


def create_novel_store(
    config_path: str = "config.yaml",
    *,
    backend: str | None = None,
    lance_path: str | None = None,
    hybrid: bool | None = None,
) -> NovelVectorStore:
    """Build a NovelVectorStore with config-driven embedding.

    When ``novel_rag.hybrid_search`` is true (default), wraps the store in
    ``HierarchicalNovelStore`` for keyword + vector hybrid retrieval.

    The heavy first-build path (embedding model load, keyword index rebuild)
    is serialized per config/backend/path so a background preheat thread and a
    concurrent user request can never build the same resources twice.
    """
    cfg = _load_raw_config(config_path)
    nr = cfg.get("novel_rag", {})
    resolved_backend = (backend or nr.get("backend") or "lancedb").lower()
    if resolved_backend not in ("lancedb", "faiss"):
        resolved_backend = "lancedb"
    resolved_path = lance_path or nr.get("lance_path") or "./data/novel_lance"

    lock = _build_lock((config_path, resolved_backend, resolved_path))
    with lock:
        return _create_novel_store_impl(
            config_path,
            nr=nr,
            resolved_backend=resolved_backend,
            resolved_path=resolved_path,
            hybrid=hybrid,
        )


def _create_novel_store_impl(
    config_path: str,
    *,
    nr: dict,
    resolved_backend: str,
    resolved_path: str,
    hybrid: bool | None,
) -> NovelVectorStore:
    """Build the store assuming the caller already holds the per-key lock."""
    embedding = create_embedding_provider(config_path)
    store = NovelVectorStore(
        embedding=embedding,
        backend=resolved_backend,
        lance_path=resolved_path,
    )

    use_hybrid = nr.get("hybrid_search", True) if hybrid is None else hybrid
    if use_hybrid:
        from src.infrastructure.hierarchical_store import HierarchicalNovelStore
        from src.infrastructure.keyword_index import KeywordsIndex

        # Reuse the in-memory keyword index across store instances (per lance
        # path). Delete clears it (self-healing via ensure_keyword_index) and
        # ingest updates it incrementally, so reuse is safe — and avoids
        # rebuilding thousands of blocks on every agent/store creation.
        kw = _KEYWORD_CACHE.get(resolved_path)
        if kw is None:
            kw = KeywordsIndex()
            _KEYWORD_CACHE[resolved_path] = kw
        store = HierarchicalNovelStore(  # type: ignore[return-value]
            vector_store=store,
            keyword_index=kw,
            default_top_k=int(nr.get("default_top_k", 5)),
        )
        # 内存关键词索引重启后为空 → 从向量库重建，避免 hybrid 关键词路静默失效
        store.ensure_keyword_index()
        return store
    return store


def warm_up_novel_store(config_path: str = "config.yaml") -> NovelVectorStore | None:
    """Background preheat entry point: build the store if not already cached.

    Never raises — a failed warm-up logs and returns None, leaving the lazy
    ``create_novel_store`` path as fallback (status quo behaviour).
    """
    try:
        store = create_novel_store(config_path)
        # 吸收历史 unindexed 行（上次导入/中断未重建的 IVF_PQ 索引）
        try:
            lance = getattr(getattr(store, "_vectors", None), "_lance", None)
            if lance is not None and hasattr(lance, "ensure_vector_indices"):
                res = lance.ensure_vector_indices()
                if any(res.values()):
                    logger.info("Preheat: vector indices ensured %s", res)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Preheat vector index ensure failed: %s", exc)
        logger.info("Novel store warm-up complete (%s)", type(store).__name__)
        return store
    except Exception as exc:  # noqa: BLE001
        logger.warning("Novel store warm-up failed (lazy init will retry): %s", exc)
        return None


def create_novel_retrieval(
    config_path: str = "config.yaml",
    store: NovelVectorStore | None = None,
) -> NovelRetrieval:
    """Build NovelRetrieval with defaults from config."""
    if store is None:
        store = create_novel_store(config_path)
    cfg = _load_raw_config(config_path)
    nr = cfg.get("novel_rag", {})
    top_k = nr.get("default_top_k", 5)
    weights = nr.get("multi_channel_weights") or None

    # ── Intent router: regex (default) or LLM ──
    routing_cfg = nr.get("routing", {}) or {}
    router_mode = str(routing_cfg.get("mode", "regex") or "regex").strip().lower()
    if router_mode == "llm":
        from src.application.novel.llm_router import LLMIntentRouter
        router = LLMIntentRouter(
            enabled=True,
            model=routing_cfg.get("model") or None,
            max_tokens=int(routing_cfg.get("max_tokens", 0) or 0) or None,
            temperature=float(routing_cfg.get("temperature", 0) or 0) or None,
        )
    else:
        from src.application.novel.intent_router import IntentRouter
        router = IntentRouter(mixed_weights=weights)

    from src.infrastructure.reranker import (
        IdentityReranker,
        KeywordOverlapReranker,
        resolve_reranker,
    )

    enabled = bool(nr.get("reranker_enabled", True))
    # Env override: NOVEL_RERANKER_ENABLED=0|1
    env_flag = os.getenv("NOVEL_RERANKER_ENABLED", "").strip().lower()
    if env_flag in {"0", "false", "no", "off"}:
        enabled = False
    elif env_flag in {"1", "true", "yes", "on"}:
        enabled = True

    # Provider: NOVEL_RERANKER_PROVIDER wins (CI sets keyword)
    provider = str(nr.get("reranker_provider", "auto") or "auto")
    env_provider = os.getenv("NOVEL_RERANKER_PROVIDER", "").strip()
    if env_provider:
        provider = env_provider
    elif os.getenv("CI", "").strip().lower() in {"1", "true", "yes"}:
        # GitHub Actions / CI: never pull GPU weights
        provider = "keyword"

    reranker = resolve_reranker(
        enabled=enabled,
        provider=provider,
        model_path=str(
            nr.get("reranker_model_path", "models/Qwen3-Reranker-0.6B")
            or "models/Qwen3-Reranker-0.6B"
        ),
        top_n=int(nr.get("reranker_top_n", top_k) or top_k),
    )

    # 进程级复用 reranker（模型权重只加载一次），避免每个 impersonation 会话重复加载
    if not isinstance(reranker, (IdentityReranker, KeywordOverlapReranker)):
        cache_key = (provider, str(reranker.model_path) if getattr(reranker, "model_path", None) else model_path)
        cached = _RERANKER_CACHE.get(cache_key)
        if cached is None:
            with _build_lock(("reranker", cache_key)):
                cached = _RERANKER_CACHE.get(cache_key)
                if cached is None:
                    _RERANKER_CACHE[cache_key] = reranker
                    cached = reranker
        reranker = cached

    # ── Query rewriter ──
    qr_cfg = nr.get("query_rewrite", {}) or {}
    from src.application.novel.query_rewriter import QueryRewriter
    query_rewriter = QueryRewriter(
        enabled=bool(qr_cfg.get("enabled", True)),
        # variants 已废弃（单次完整改写），传参仅兼容旧配置
        variants=int(qr_cfg.get("variants", 3) or 3),
        model=qr_cfg.get("model") or None,
        max_tokens=int(qr_cfg.get("max_tokens", 0) or 0) or None,
        temperature=float(qr_cfg.get("temperature", 0) or 0) or None,
    )

    # ── Entity resolver ──
    # 默认开启，可在 config novel_rag.entity_resolver.enabled: false 关闭
    er_cfg = nr.get("entity_resolver", {}) or {}
    entity_resolver = None
    if er_cfg.get("enabled", True):
        from src.application.novel.entity_resolver import EntityResolver
        entity_resolver = EntityResolver()

    return NovelRetrieval(
        store,
        router=router,
        top_k=top_k,
        reranker=reranker,
        graph_enrich=bool(nr.get("graph_enrich", True)),
        query_rewriter=query_rewriter,
        entity_resolver=entity_resolver,
    )



def create_impersonation_service(
    config_path: str = "config.yaml",
    store: NovelVectorStore | None = None,
    llm_client: Any = None,
) -> ImpersonationService:
    """Build ImpersonationService, optionally with LLM."""
    if store is None:
        store = create_novel_store(config_path)

    if llm_client is None:
        cfg = _load_raw_config(config_path)
        nr = cfg.get("novel_rag", {})
        agent_cfg = cfg.get("agent", {})
        # Try to build SharedLLMClient from novel_rag config
        api_key = _substitute_env(nr.get("impersonation_api_key", ""))
        base_url = _substitute_env(nr.get("impersonation_base_url", ""))
        # 前端 llm-config 覆盖（impersonation_chat）
        try:
            from src.shared.llm_config import get_endpoint_config

            _ep = get_endpoint_config("impersonation_chat")
            if _ep.get("api_key"):
                api_key = _ep["api_key"]
            if _ep.get("base_url"):
                base_url = _ep["base_url"]
        except Exception:  # noqa: BLE001
            pass
        if base_url:
            from src.shared.llm import SharedLLMClient
            model = nr.get("impersonation_model") or agent_cfg.get("model", "deepseek-v4-flash")
            try:
                from src.shared.llm_config import get_endpoint_config as _g

                _ep = _g("impersonation_chat")
                if _ep.get("model"):
                    model = _ep["model"]
            except Exception:  # noqa: BLE001
                pass
            fallback_model = agent_cfg.get("fallback_model", "")
            fallback = None
            if fallback_model:
                fallback = {
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": fallback_model,
                }
            llm_client = SharedLLMClient(
                primary={
                    "base_url": base_url,
                    "api_key": api_key,
                    "model": model,
                },
                fallback=fallback,
                temperature=0.8,
                max_tokens=1024,
            )

    return ImpersonationService(store, llm_client=llm_client)


def create_hierarchical_store(
    config_path: str = "config.yaml",
) -> HierarchicalNovelStore:
    """Build a HierarchicalNovelStore with keyword coarse filter + vector search.

    Wraps NovelVectorStore — same API, two-stage retrieval.
    """
    from src.infrastructure.hierarchical_store import HierarchicalNovelStore
    from src.infrastructure.keyword_index import KeywordsIndex

    vector_store = create_novel_store(config_path)
    return HierarchicalNovelStore(
        vector_store=vector_store,
        keyword_index=KeywordsIndex(),
        default_top_k=5,
    )
