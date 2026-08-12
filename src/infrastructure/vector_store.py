"""Vector store abstraction — unified interface for all vector storage backends.

Domain code depends on this ABC, not on concrete implementations.
Supports: FAISS (local), Memory (testing), Qdrant (production), Milvus (future).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class VectorRecord:
    """A single vector record to store or retrieve."""

    vector_id: str
    embedding: list[float]
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """Result of a vector similarity search."""

    vector_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    """Abstract base for all vector store backends.

    All Domain code that needs vector storage depends on this interface,
    NOT on FAISSStore, QdrantStore, etc.

    Usage:
        store = FAISSStore()
        await store.upsert("my_collection", records)
        results = await store.search("my_collection", query_vector, top_k=5)
    """

    name: ClassVar[str] = "base"

    @abstractmethod
    async def upsert(
        self, collection_name: str, records: list[VectorRecord]
    ) -> int:
        """Insert or update vectors. Returns count upserted."""
        ...

    @abstractmethod
    async def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search for nearest neighbors. Returns ranked results."""
        ...

    @abstractmethod
    async def delete(
        self, collection_name: str, vector_ids: list[str]
    ) -> int:
        """Delete vectors by ID. Returns count deleted."""
        ...

    async def stats(self, collection_name: str) -> dict[str, Any]:
        """Return collection statistics (optional, may not be supported)."""
        return {"collection_name": collection_name, "total_vectors": "unknown"}


# ── Concrete: In-Memory (testing / dev) ──────────────────────


class MemoryVectorStore(VectorStore):
    """In-memory vector store for testing and small-scale development."""

    name: ClassVar[str] = "memory"

    def __init__(self):
        self._collections: dict[str, dict[str, VectorRecord]] = {}

    async def upsert(
        self, collection_name: str, records: list[VectorRecord]
    ) -> int:
        if collection_name not in self._collections:
            self._collections[collection_name] = {}
        coll = self._collections[collection_name]
        for r in records:
            coll[r.vector_id] = r
        return len(records)

    async def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        import math

        coll = self._collections.get(collection_name, {})
        if not coll:
            return []

        def cosine_sim(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb) if na and nb else 0.0

        scored = []
        for vid, rec in coll.items():
            score = cosine_sim(query_embedding, rec.embedding)
            scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            SearchResult(
                vector_id=rec.vector_id,
                content=rec.content,
                score=score,
                metadata=rec.metadata,
            )
            for score, rec in scored[:top_k]
        ]

    async def delete(
        self, collection_name: str, vector_ids: list[str]
    ) -> int:
        coll = self._collections.get(collection_name, {})
        cnt = 0
        for vid in vector_ids:
            if vid in coll:
                del coll[vid]
                cnt += 1
        return cnt

    async def stats(self, collection_name: str) -> dict[str, Any]:
        coll = self._collections.get(collection_name, {})
        return {
            "collection_name": collection_name,
            "total_vectors": len(coll),
            "backend": "memory",
        }


# ── Concrete: FAISS (local) ──────────────────────────────────

_FAISS_AVAILABLE = False
try:
    import faiss as _faiss
    import numpy as np

    _FAISS_AVAILABLE = True
except ImportError:
    pass


class FAISSVectorStore(VectorStore):
    """FAISS-based vector store for local / single-machine deployment.

    Uses IndexFlatIP with L2-normalized vectors for cosine similarity.
    """

    name: ClassVar[str] = "faiss"

    def __init__(self, store_dir: str | None = None):
        if not _FAISS_AVAILABLE:
            raise ImportError(
                "faiss-cpu is required for FAISSVectorStore. "
                "Install with: pip install faiss-cpu"
            )
        self._store_dir = store_dir
        # Per-collection state: (faiss.Index, list[VectorRecord], list[str] of ids)
        self._collections: dict[str, tuple] = {}

    def _get_or_create(self, name: str, dim: int):
        if name not in self._collections:
            idx = _faiss.IndexFlatIP(dim)
            self._collections[name] = (idx, [], [])
        return self._collections[name]

    async def upsert(
        self, collection_name: str, records: list[VectorRecord]
    ) -> int:
        if not records:
            return 0
        dim = len(records[0].embedding)
        idx, all_records, all_ids = self._get_or_create(collection_name, dim)

        vecs = np.array([r.embedding for r in records], dtype=np.float32)
        _faiss.normalize_L2(vecs)
        idx.add(vecs)
        all_records.extend(records)
        all_ids.extend(r.vector_id for r in records)
        return len(records)

    async def search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if collection_name not in self._collections:
            return []
        idx, all_records, all_ids = self._collections[collection_name]
        if not all_records:
            return []

        q = np.array([query_embedding], dtype=np.float32)
        _faiss.normalize_L2(q)
        k = min(top_k, len(all_ids))
        scores, indices = idx.search(q, k)

        results = []
        for score, i in zip(scores[0], indices[0]):
            if i < 0 or i >= len(all_records):
                continue
            rec = all_records[i]
            results.append(
                SearchResult(
                    vector_id=rec.vector_id,
                    content=rec.content,
                    score=float(score),
                    metadata=rec.metadata,
                )
            )
        return results

    async def delete(
        self, collection_name: str, vector_ids: list[str]
    ) -> int:
        if collection_name not in self._collections:
            return 0
        # FAISS doesn't support deletion easily; rebuild index
        _idx, all_records, all_ids = self._collections[collection_name]
        kept = [
            (r, i)
            for r, i in zip(all_records, all_ids)
            if i not in vector_ids
        ]
        if not kept:
            self._collections.pop(collection_name, None)
            return len(all_records)

        dim = len(kept[0][0].embedding)
        new_idx = _faiss.IndexFlatIP(dim)
        vecs = np.array([r.embedding for r, _ in kept], dtype=np.float32)
        _faiss.normalize_L2(vecs)
        new_idx.add(vecs)
        self._collections[collection_name] = (
            new_idx,
            [r for r, _ in kept],
            [i for _, i in kept],
        )
        return len(all_records) - len(kept)

    async def stats(self, collection_name: str) -> dict[str, Any]:
        if collection_name not in self._collections:
            return {"collection_name": collection_name, "total_vectors": 0}
        _, records, _ = self._collections[collection_name]
        return {
            "collection_name": collection_name,
            "total_vectors": len(records),
            "backend": "faiss",
        }
