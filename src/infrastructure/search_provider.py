"""Search provider abstraction — unified web search interface.

Domain code depends on this ABC, not on DuckDuckGo or other implementations.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from typing import ClassVar

import httpx

logger = logging.getLogger("agent")


class SearchProvider(ABC):
    """Abstract base for web search providers.

    Returns structured results: title, url, snippet.
    """

    name: ClassVar[str] = "base"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Search the web and return results."""
        ...


# ── Concrete: Mock ───────────────────────────────────────────


class MockSearchProvider(SearchProvider):
    """Deterministic demo corpus for testing (hash-based selection)."""

    name: ClassVar[str] = "mock"

    _CORPUS = [
        {
            "title": "Understanding Large Language Models",
            "url": "https://example.com/llm-guide",
            "snippet": "A comprehensive guide to large language models covering architecture, training, and applications.",
        },
        {
            "title": "Python AsyncIO Tutorial",
            "url": "https://example.com/async-python",
            "snippet": "Learn how to write asynchronous Python code using the asyncio library.",
        },
        {
            "title": "OpenAI API Documentation",
            "url": "https://platform.openai.com/docs",
            "snippet": "Official documentation for the OpenAI API, including chat completions and function calling.",
        },
        {
            "title": "Agent-Based AI Systems",
            "url": "https://example.com/agent-systems",
            "snippet": "An overview of autonomous AI agent architectures, including planning, tool use, and memory.",
        },
        {
            "title": "Retrieval-Augmented Generation (RAG)",
            "url": "https://example.com/rag-explained",
            "snippet": "How RAG combines retrieval systems with generative models for knowledge-intensive tasks.",
        },
        {
            "title": "Prompt Engineering Guide",
            "url": "https://example.com/prompt-engineering",
            "snippet": "Techniques and patterns for crafting effective prompts for LLMs.",
        },
        {
            "title": "Machine Learning in Production",
            "url": "https://example.com/ml-production",
            "snippet": "Deploying and maintaining ML models in production environments.",
        },
        {
            "title": "Function Calling Best Practices",
            "url": "https://example.com/function-calling",
            "snippet": "Best practices for implementing function calling in AI applications.",
        },
    ]

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        import hashlib
        import random

        seed = int(hashlib.md5(query.encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        selected = rng.sample(self._CORPUS, min(max_results, len(self._CORPUS)))
        return selected


# ── Concrete: DuckDuckGo ─────────────────────────────────────


class _DDGResultParser(HTMLParser):
    """Parse DuckDuckGo HTML search results."""

    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._in_result = False

    def handle_starttag(self, tag: str, attrs: list[tuple]):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if tag == "div" and "result" in cls:
            self._in_result = True
            self._current = {}
        elif self._in_result and tag == "a" and "result__a" in cls:
            href = attrs_dict.get("href", "")
            if href:
                self._current["url"] = href  # type: ignore[index]
        elif self._in_result and "result__snippet" in cls:
            self._current["snippet"] = ""  # type: ignore[index]

    def handle_endtag(self, tag: str):
        if tag == "div" and self._in_result and self._current:
            if self._current.get("title"):
                self.results.append(self._current)
            self._current = None

    def handle_data(self, data: str):
        if not self._current:
            return
        text = data.strip()
        if not text:
            return
        if "title" not in self._current:
            self._current["title"] = text
        elif "snippet" in self._current:
            self._current["snippet"] = (self._current.get("snippet", "") + " " + text).strip()


class DuckDuckGoSearchProvider(SearchProvider):
    """DuckDuckGo free search (HTML endpoint, no API key needed)."""

    name: ClassVar[str] = "duckduckgo"

    DDG_URL = "https://html.duckduckgo.com/html/"

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.DDG_URL,
                    data={"q": query},
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                    },
                )
                response.raise_for_status()
        except Exception as e:
            logger.warning("DDG search failed: %s", e)
            return []

        parser = _DDGResultParser()
        parser.feed(response.text)

        for r in parser.results:
            r["url"] = self._decode_ddg_url(r.get("url", ""))

        return parser.results[:max_results]

    @staticmethod
    def _decode_ddg_url(raw_url: str) -> str:
        if not raw_url or raw_url.startswith("http"):
            return raw_url
        match = re.search(r"uddg=([^&]+)", raw_url)
        if match:
            from urllib.parse import unquote
            return unquote(match.group(1))
        return raw_url
