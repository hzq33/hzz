"""Query rewriter — LLM 多变体改写（3 变体 + 原查询保底）。

用户口语 query → DeepSeek 改写为 3 个不同角度的检索查询：
- 变体A：实体补全版（全名/别名/关系对象，命中实体关系）
- 变体B：意图定向版（关系/时间/事件意图定向，命中事件时间线）
- 变体C：事件发展版（故事脉络/剧情推进）
外加原查询保底，总计最多 4 个变体，跨变体 RRF 融合。
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING

from src.shared.defaults import DEFAULT_DEEPSEEK_BASE_URL, DEFAULT_DEEPSEEK_MODEL

if TYPE_CHECKING:
    from src.application.novel.entity_resolver import QueryContext

logger = logging.getLogger("agent.query_rewriter")


def observe_rag_fallback(reason: str) -> None:
    """RAG 降级计数（prometheus 未初始化时 no-op）。"""
    try:
        from src.shared.metrics import observe_rag_fallback as _obs

        _obs(reason=reason)
    except Exception:
        pass


_PROMPT = """你是轻小说角色扮演助手的查询改写器。用户对一个角色说话，系统需要从轻小说原文中检索相关内容来生成回复。

将用户的原始消息改写为 **3 个不同角度的检索查询**，每行一个：
1. 变体A：实体补全版——补充角色的全名、别名、关系对象，用具体名词替代指代词（如"他"→"亚瑟·卡恩"），适合命中实体关系
2. 变体B：意图定向版——按问题意图改写：关系类→"X 与 Y 的关系"，时间类→"X 在何时/某时期"，事件类→"X 经历了什么/某事件经过"，适合命中事件/时间线
3. 变体C：事件发展版——聚焦故事发展脉络，补全事件关键词和背景，适合命中剧情推进

要求：
- 三个变体必须**角度不同**（不要三个相似的改写）
- 都保留原始意图，都是完整句子，适合向量检索
- 不要分点、不要编号、不要引号，只输出三行文本

当前对话角色：{character}
已知角色列表：{known_characters}
{alias_hints}
原始用户消息：{query}

只输出 3 行改写后的查询，每行一个，不要任何其他内容。
"""

# alias_hints 段落（当 QueryContext 提供时注入）
_ALIAS_HINTS_SECTION = """
已知称谓映射（query 中的称谓 → 规范角色名，改写时用规范名展开）：
{alias_hints}
"""

_REWRITE_MODEL = DEFAULT_DEEPSEEK_MODEL
_REWRITE_MAX_TOKENS = 800
_REWRITE_TEMPERATURE = 0.3


def _build_client():
    """Build async OpenAI client for query rewriting."""
    from openai import AsyncOpenAI

    ep = {}
    try:
        from src.shared.llm_config import get_endpoint_config

        ep = get_endpoint_config("query_rewriter")
    except Exception:  # noqa: BLE001
        pass
    api_key = (ep.get("api_key") or "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    base_url = (ep.get("base_url") or "").strip() or os.getenv(
        "DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL
    ).strip().rstrip("/")
    return AsyncOpenAI(
        api_key=api_key,
        base_url=f"{base_url}/v1" if not base_url.endswith(("/v1", "/v4")) else base_url,
    )


class QueryRewriter:
    """LLM-based query rewriter — 3 变体 + 原查询保底。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        variants: int | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ):
        self.enabled = enabled
        ep = {}
        try:
            from src.shared.llm_config import get_endpoint_config

            ep = get_endpoint_config("query_rewriter")
        except Exception:  # noqa: BLE001
            pass
        # variants 参数保留兼容旧配置调用方；实际固定输出 3 变体
        self._model = model or ep.get("model") or _REWRITE_MODEL
        self._max_tokens = max_tokens or ep.get("max_tokens") or _REWRITE_MAX_TOKENS
        self._temperature = temperature or float(ep.get("temperature", _REWRITE_TEMPERATURE))
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = _build_client()
        return self._client

    @staticmethod
    def _clean_rewritten(raw: str) -> str:
        """清洗 LLM 输出：去思维链 / 代码块 / 首尾引号。"""
        text = (raw or "").strip()
        if not text:
            return ""
        text = re.sub(r"^.*?</think>\s*", "", text, flags=re.DOTALL).strip()
        text = re.sub(r"^```(?:json|text)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'", "“", "”", "「", "」"}:
            text = text[1:-1].strip()
        return text

    @staticmethod
    def _split_variants(raw: str) -> list[str]:
        """按行拆分 LLM 输出的多变体（每行一个查询）。"""
        text = (raw or "").strip()
        if not text:
            return []
        # 去思维链（DeepSeek 推理模型可能前置 </think> 包裹）
        text = re.sub(r"^.*?</think>\s*", "", text, flags=re.DOTALL).strip()
        # 去 markdown 代码块围栏
        text = re.sub(r"^```(?:json|text)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
        out: list[str] = []
        for line in text.splitlines():
            line = line.strip().lstrip("-*0123456789.、 ")
            line = line.strip()
            if not line:
                continue
            # 去首尾成对引号
            if len(line) >= 2 and line[0] == line[-1] and line[0] in {'"', "'", "“", "”", "「", "」"}:
                line = line[1:-1].strip()
            if line and line not in out:
                out.append(line)
        return out

    async def rewrite(
        self,
        query: str,
        *,
        character: str = "",
        known_characters: list[str] | None = None,
        query_context: QueryContext | None = None,
    ) -> list[str]:
        """将用户 query 改写为多变体（3 LLM 变体 + 原查询保底）。

        Returns:
            最多 4 个变体的列表（[original, v1, v2, v3] 去重去空）；
            LLM 失败 / 输出为空时回退 [original]。
        """
        if not self.enabled or not query or not query.strip():
            return [query] if query else []

        original = query.strip()

        # 超短 query（< 3 字符）改写无收益
        if len(original) < 3:
            return [original]

        alias_section = ""
        if query_context and query_context.alias_hints:
            alias_section = _ALIAS_HINTS_SECTION.format(alias_hints=query_context.alias_hints)
        chars_str = ", ".join(known_characters or []) or "（未知）"
        prompt = _PROMPT.format(
            character=character or "（未指定）",
            known_characters=chars_str,
            alias_hints=alias_section,
            query=original,
        )

        try:
            client = self._get_client()
            from src.shared.llm_config import thinking_extra_body
            extra = thinking_extra_body("query_rewriter")
            resp = await client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                **( {"extra_body": extra} if extra else {} ),
            )
            raw = resp.choices[0].message.content or ""
            finish = resp.choices[0].finish_reason or ""

            rewritten = self._clean_rewritten(raw) if raw.strip() else ""
            if not rewritten:
                if finish == "length":
                    logger.warning(
                        "Query rewrite empty output (finish=length, reasoning model "
                        "exhausted token budget). Falling back to original: %s",
                        original[:40],
                    )
                else:
                    logger.warning("Query rewrite returned empty content")
                observe_rag_fallback("query_rewrite_empty")
                return [original]

            # ── 解析多行输出为变体列表 ──
            variants = self._split_variants(rewritten)
            if not variants:
                observe_rag_fallback("query_rewrite_empty")
                return [original]
            # 原查询保底 + LLM 变体（去重、去空），召回多样性最大化
            merged: list[str] = []
            for v in [original] + variants:
                v = (v or "").strip()
                if v and v not in merged:
                    merged.append(v)
            logger.debug(
                "Query rewrite: %r → %d variants", original[:40], len(merged)
            )
            return merged[:4]

        except Exception as exc:
            logger.warning("Query rewrite failed (%s); falling back to original", exc)
            observe_rag_fallback("query_rewrite_error")
            return [original]
