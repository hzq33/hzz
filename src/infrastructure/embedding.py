"""Embedding provider abstraction — unified text-to-vector interface.

Domain code depends on this ABC, not on concrete model implementations.
Supports: Qwen3 (local), OpenAI (API), Mock (deterministic testing).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

logger = logging.getLogger("agent")


@dataclass
class EmbeddingResult:
    """Result of a text embedding operation."""

    embeddings: list[list[float]]
    dimensions: int = 0
    model_name: str = ""

    def __post_init__(self):
        if self.embeddings and not self.dimensions:
            self.dimensions = len(self.embeddings[0])


class EmbeddingProvider(ABC):
    """Abstract base for text embedding providers.

    All Domain code depends on this interface, not on Qwen3 or OpenAI.
    """

    name: ClassVar[str] = "base"

    @abstractmethod
    async def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        """Generate embeddings for a list of texts."""
        ...

    async def embed_text(self, text: str) -> list[float]:
        """Convenience: embed a single text."""
        result = await self.embed_texts([text])
        return result.embeddings[0] if result.embeddings else []


# ── Concrete: Mock (testing) ─────────────────────────────────


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic character-n-gram embeddings for testing.

    Same input always produces the same output. Texts sharing characters /
    bigrams score higher cosine similarity (a crude semantic proxy), so
    retrieval tests can rely on keyword-ish matching. Values are in normal
    float range — unlike raw-hash-byte floats, which are mostly subnormal
    (1e-40..1e-70) and get rejected by LanceDB ANN search.
    """

    name: ClassVar[str] = "mock"

    def __init__(self, dimensions: int = 384):
        self.dimensions = dimensions

    @staticmethod
    def _feature_index(token: str, dims: int) -> int:
        import hashlib

        h = hashlib.md5(token.encode(), usedforsecurity=False).digest()
        return int.from_bytes(h[:4], "little") % dims

    async def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        embeddings = []
        for text in texts:
            vec = [0.0] * self.dimensions
            # Unigram + bigram counts hashed into the vector (char-level).
            for ch in text:
                vec[self._feature_index(ch, self.dimensions)] += 1.0
            for i in range(max(0, len(text) - 1)):
                vec[self._feature_index(text[i : i + 2], self.dimensions)] += 1.0
            # Normalize to unit length.
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            embeddings.append(vec)
        return EmbeddingResult(
            embeddings=embeddings,
            dimensions=self.dimensions,
            model_name="mock",
        )


# ── Concrete: OpenAI ─────────────────────────────────────────


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible embedding API (text-embedding-3-small, etc.)."""

    name: ClassVar[str] = "openai"

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or "not-needed"
        self.base_url = base_url
        self.model = model
        self.dimensions = dimensions
        self.timeout = timeout
        self._client: Any = None

    def _build_client(self) -> Any:
        """Lazily build (and reuse) the async client.

        ``trust_env=False`` 禁用系统代理：与 LLM 层一致，避免 Windows 系统代理
        （Clash 等）把请求转发到本地端口导致 Connection error。
        """
        from openai import AsyncOpenAI

        client_kwargs = {
            "api_key": self.api_key,
            "timeout": self.timeout,
            "max_retries": 0,
        }
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        try:
            import httpx

            client_kwargs["http_client"] = httpx.AsyncClient(
                timeout=self.timeout, trust_env=False
            )
        except Exception:  # noqa: BLE001 - 不支持时回退默认客户端
            pass
        return AsyncOpenAI(**client_kwargs)

    async def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        if self._client is None:
            self._client = self._build_client()
        resp = await self._client.embeddings.create(
            model=self.model,
            input=texts,
        )
        embeddings = [d.embedding for d in resp.data]
        return EmbeddingResult(
            embeddings=embeddings,
            dimensions=len(embeddings[0]) if embeddings else 0,
            model_name=self.model,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


# ── Concrete: Qwen3 (local) ──────────────────────────────────

_QWEN3_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer

    _QWEN3_AVAILABLE = True
except ImportError:
    pass


class Qwen3EmbeddingProvider(EmbeddingProvider):
    """Local Qwen3-Embedding model via sentence-transformers.

    Model is loaded once and reused across calls.
    Default: Qwen3-Embedding-0.6B (1024-dim).
    """

    name: ClassVar[str] = "qwen3"

    def __init__(self, model_path: str, device: str = "auto", use_fp16: bool = True):
        if not _QWEN3_AVAILABLE:
            raise ImportError(
                "sentence-transformers is required for Qwen3EmbeddingProvider. "
                "Install with: pip install sentence-transformers"
            )
        self.model_path = model_path
        self._device = device
        self._use_fp16 = use_fp16
        self._dim = 1024  # Qwen3-Embedding-0.6B hidden_size=1024
        # Resolve device: auto → cuda if available, else cpu
        import torch
        if self._device == "auto":
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        # Pre-load model
        self._model: SentenceTransformer = SentenceTransformer(
            self.model_path, device=self._device,
        )
        if self._use_fp16 and self._device == "cuda":
            self._model.half()

    async def embed_texts(self, texts: list[str]) -> EmbeddingResult:
        import asyncio

        import torch

        # Cap per-text length to avoid attention OOM on 4GB GPUs
        max_chars = 1500
        safe_texts = [
            (t[:max_chars] if isinstance(t, str) and len(t) > max_chars else (t or ""))
            for t in texts
        ]

        def _encode_chunked(items: list[str], batch_size: int) -> list[list[float]]:
            out: list[list[float]] = []
            bs = max(1, batch_size)
            i = 0
            while i < len(items):
                chunk = items[i : i + bs]
                try:
                    # 正常路径不调 empty_cache（同步显存整理很贵）；
                    # 仅 OOM 降级时才清理（见 except 分支）
                    vecs = self._model.encode(
                        chunk,
                        normalize_embeddings=True,
                        show_progress_bar=False,
                        batch_size=min(bs, len(chunk)),
                    )
                    out.extend(vecs.tolist())
                    i += len(chunk)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    if bs > 1:
                        bs = max(1, bs // 2)
                        logger.warning(
                            "Embedding CUDA OOM — retry with batch_size=%d", bs
                        )
                        continue
                    # Last resort: encode one text on CPU
                    logger.warning("Embedding CUDA OOM on single text — CPU fallback")
                    cpu_model = self._model
                    try:
                        cpu_model.to("cpu")
                        vec = cpu_model.encode(
                            chunk,
                            normalize_embeddings=True,
                            show_progress_bar=False,
                            batch_size=1,
                        )
                        out.extend(vec.tolist())
                    finally:
                        try:
                            cpu_model.to(self._device)
                        except Exception:
                            pass
                        torch.cuda.empty_cache()
                    i += len(chunk)
            return out

        loop = asyncio.get_event_loop()
        # 4GB 卡 fp16 模型 ~1.2GB 显存；batch 16 峰值可控，OOM 自动减半降级
        embeddings = await loop.run_in_executor(
            None, lambda: _encode_chunked(safe_texts, 16)
        )
        actual_dim = len(embeddings[0]) if embeddings else 0
        return EmbeddingResult(
            embeddings=embeddings,
            dimensions=actual_dim,
            model_name="qwen3-embedding",
        )
