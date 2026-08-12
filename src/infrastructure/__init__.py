"""Infrastructure layer — abstract interfaces and concrete adapters.

This package provides:
- VectorStore — FAISS / LanceDB / memory backends
- EmbeddingProvider — Qwen3 / OpenAI / Mock
- Reranker — Qwen3 / keyword
- SearchProvider — DuckDuckGo / Mock
- FileStorage — Local filesystem ABC（预留；当前 ingest 不经此路径）
"""
