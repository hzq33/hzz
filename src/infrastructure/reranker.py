"""Reranker abstraction — re-rank search results for relevance.

Domain code depends on this ABC, not on concrete model implementations.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

_RERANKER_AVAILABLE = False
try:
    import torch
    from transformers import AutoModelForCausalLM, AutoModelForSequenceClassification, AutoTokenizer
    _RERANKER_AVAILABLE = True
except ImportError:
    pass

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


class Reranker(ABC):
    """Abstract base for re-ranking search results."""

    name: ClassVar[str] = "base"

    @abstractmethod
    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[int]:
        """Re-rank documents for a query. Returns indices sorted by relevance."""
        ...


class IdentityReranker(Reranker):
    """No-op reranker that returns documents as-is (for testing)."""

    name: ClassVar[str] = "identity"

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[int]:
        n = top_n or len(documents)
        return list(range(min(n, len(documents))))


class KeywordOverlapReranker(Reranker):
    """Deterministic lexical reranker for CI / fallback when Qwen3 weights are absent.

    Scores each document by overlap of query tokens (CJK chars + latin words).
    Stable for quality gates; not a substitute for cross-encoder quality.
    """

    name: ClassVar[str] = "keyword"

    def __init__(self, top_n: int = 5) -> None:
        self.top_n = max(1, int(top_n))

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[int]:
        n = top_n or self.top_n
        if not documents:
            return []
        q = self._tokens(query)
        if not q:
            return list(range(min(n, len(documents))))
        scored: list[tuple[int, float, int]] = []
        for i, doc in enumerate(documents):
            d = self._tokens(doc)
            overlap = len(q & d)
            # Prefer denser overlap; break ties by original rank (stable).
            density = overlap / max(len(d), 1)
            scored.append((i, float(overlap) + 0.01 * density, i))
        scored.sort(key=lambda x: (-x[1], x[2]))
        return [idx for idx, _, _ in scored[: min(n, len(scored))]]


class BGEReranker(Reranker):
    """BGE cross-encoder reranker (bge-reranker-v2-m3) for relevance scoring.

    True cross-encoder: query and document are jointly encoded and a single
    relevance logit is produced (sigmoid → score). Significantly stronger
    than the Qwen3 yes/no probability approach on Chinese retrieval
    (MIRACL multilingual SOTA in its size class).
    """

    name: ClassVar[str] = "bge"

    MAX_LEN = 512

    def __init__(self, model_path: str, device: str = "auto", top_n: int = 5):
        if not _RERANKER_AVAILABLE:
            raise ImportError(
                "transformers is required for BGEReranker. "
                "Install with: pip install transformers"
            )
        self.model_path = model_path
        self.top_n = max(1, int(top_n))
        self._model = None
        self._tokenizer = None
        self._device = device

    def _load_model(self):
        if self._model is None:
            if self._device == "auto":
                self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_path,
                torch_dtype=torch.float16 if self._device == "cuda" else torch.float32,
            ).to(self._device)
            self._model.eval()

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[int]:
        import asyncio

        self._load_model()
        n = top_n or self.top_n
        if not documents:
            return []

        loop = asyncio.get_event_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: self._score_batch(query, documents),
        )
        scored = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in scored[:n]]

    def _score_batch(self, query: str, documents: list[str]) -> list[float]:
        """Batch cross-encoder scoring (one forward pass per batch)."""
        scores: list[float] = []
        batch_size = 16
        for i in range(0, len(documents), batch_size):
            batch = documents[i : i + batch_size]
            inputs = self._tokenizer(
                [query] * len(batch),
                batch,
                return_tensors="pt",
                max_length=self.MAX_LEN,
                truncation=True,
                padding=True,
            ).to(self._device)
            with torch.no_grad():
                logits = self._model(**inputs).logits
            # bge-reranker-v2-m3 outputs a single regression logit per pair
            logits = logits.view(-1).float()
            scores.extend(torch.sigmoid(logits).tolist())
        return scores


def resolve_reranker(
    *,
    enabled: bool,
    provider: str = "auto",
    model_path: str = "models/Qwen3-Reranker-0.6B",
    top_n: int = 5,
) -> Reranker:
    """Build a reranker from config knobs (fail-open to keyword/identity).

    Providers:
      - ``identity`` / off: no-op
      - ``keyword``: deterministic lexical overlap (CI-safe)
      - ``qwen3``: local Qwen3-Reranker weights (falls back to keyword if missing)
      - ``bge``: local BGE cross-encoder (bge-reranker-v2-m3, Chinese SOTA)
      - ``auto``: bge when weights exist, else qwen3, else keyword
    """
    import logging

    log = logging.getLogger("agent.reranker")
    if not enabled:
        return IdentityReranker()

    choice = (provider or "auto").strip().lower()
    if choice in {"off", "none", "identity"}:
        return IdentityReranker()
    if choice in {"keyword", "lexical", "heuristic"}:
        return KeywordOverlapReranker(top_n=top_n)

    want_bge = choice in {"auto", "bge", "bge-m3"}
    want_qwen = choice in {"auto", "qwen3", "qwen"}

    # Prefer BGE (stronger on Chinese), then Qwen3, then keyword fallback.
    if want_bge:
        bge_path = Path(model_path.replace("Qwen3-Reranker-0.6B", "bge-reranker-v2-m3"))
        if _RERANKER_AVAILABLE and bge_path.exists():
            try:
                reranker = BGEReranker(model_path=str(bge_path), top_n=top_n)
                log.info("Reranker: BGE (%s)", bge_path)
                return reranker
            except Exception as exc:
                log.warning("BGE reranker load failed (%s); trying next provider", exc)
        elif want_bge and not bge_path.exists():
            log.warning(
                "BGE reranker weights missing at %s; trying next provider. "
                "Download: huggingface-cli download BAAI/bge-reranker-v2-m3",
                bge_path,
            )

    path = Path(model_path)
    if want_qwen and _RERANKER_AVAILABLE and path.exists():
        try:
            reranker = Qwen3Reranker(model_path=str(path), top_n=top_n)
            log.info("Reranker: Qwen3 (%s)", path)
            return reranker
        except Exception as exc:
            log.warning("Qwen3 reranker load failed (%s); falling back to keyword", exc)
    elif want_qwen and not path.exists():
        log.warning(
            "Qwen3 reranker weights missing at %s; using KeywordOverlapReranker. "
            "See docs/RERANKER.md",
            path,
        )
    elif want_qwen and not _RERANKER_AVAILABLE:
        log.warning(
            "transformers/torch unavailable for Qwen3 reranker; using keyword. "
            "See docs/RERANKER.md"
        )

    if choice in {"qwen3", "qwen", "auto", "bge", "bge-m3"}:
        return KeywordOverlapReranker(top_n=top_n)
    return IdentityReranker()


class Qwen3Reranker(Reranker):
    """Qwen3-Reranker model for relevance scoring.

    Uses a yes/no probability approach to score query-document relevance.
    """

    name: ClassVar[str] = "qwen3"

    def __init__(self, model_path: str, device: str = "auto", top_n: int = 3):
        if not _RERANKER_AVAILABLE:
            raise ImportError(
                "transformers is required for Qwen3Reranker. "
                "Install with: pip install transformers"
            )
        self.model_path = model_path
        self.top_n = top_n
        self._model = None
        self._tokenizer = None
        self._device = device

    def _load_model(self):
        if self._model is None:
            if self._device == "auto":
                self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_path)
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                torch_dtype=torch.float16 if self._device == "cuda" else torch.float32,
            ).to(self._device)
            self._model.eval()

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[int]:
        import asyncio

        self._load_model()
        n = top_n or self.top_n

        if not documents:
            return []

        loop = asyncio.get_event_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: self._score_batch(query, documents),
        )
        scored = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in scored[:n]]

    def _score_batch(self, query: str, documents: list[str]) -> list[float]:
        scores = []
        for doc in documents:
            score = self._score_single(query, doc)
            scores.append(score)
        return scores

    def _score_single(self, query: str, document: str) -> float:
        prompt = (
            f"<|im_start|>system\n"
            f"Judge whether the document meets the query requirements.\n"
            f"Output 'yes' or 'no' only.<|im_end|>\n"
            f"<|im_start|>user\n"
            f"Query: {query}\nDocument: {document}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        inputs = self._tokenizer(  # type: ignore[union-attr]
            prompt, return_tensors="pt", truncation=True, max_length=512
        ).to(self._device)

        with torch.no_grad():
            outputs = self._model(**inputs)  # type: ignore[union-attr]
            logits = outputs.logits[0, -1, :]
            # Get probability of "yes" token
            yes_id = self._tokenizer.encode("yes", add_special_tokens=False)[0]  # type: ignore[union-attr]
            no_id = self._tokenizer.encode("no", add_special_tokens=False)[0]  # type: ignore[union-attr]
            yes_logit = logits[yes_id].item()
            no_logit = logits[no_id].item()
            # Softmax over yes/no
            import math
            exp_yes = math.exp(yes_logit)
            exp_no = math.exp(no_logit)
            return exp_yes / (exp_yes + exp_no)
