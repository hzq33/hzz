"""ImpersonationAgent chat core mixin — conversation loop, tool loop, messages.

Extracted from the former monolithic ``impersonation_agent.py``; logic unchanged.
Mixin methods share instance state (``self._card`` / ``self._store`` / ``self._llm``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from src.core.impersonation.models import Citation
from src.tools.base import ToolResult

logger = logging.getLogger("agent")


_TOOL_AWARE_PROMPT = """
## 工具使用规则
你可以调用工具来获取真实世界的信息，帮助回答用户的问题。
规则：
1. 当用户问的问题超出你的原著知识范围（天气、新闻、时间、计算等），请调用工具查找。
2. 调用工具后，把信息自然地融入你的对话中，用你的说话风格回复。
3. 不要让用户察觉你在"查资料"——你应该表现得像是本来就了解这些信息。
4. 不要直接说"根据搜索结果"——用你自己的方式转述。
"""


_POSTPROCESS_PROMPT = """你是轻小说角色扮演系统的**检索后处理分析器**。你的任务是：先分析工具提供的世界知识（角色关系/事件发展），再结合检索到的原文片段，输出主回复模型需要的结构化数据。

当前角色：{character}

## 工具查询结果（角色关系 + 事件发展）
{tool_knowledge}

## 检索原文片段（事实依据，以原文为准）
{original_text}

## 用户问题
{user_input}

请分析并输出以下结构化数据（JSON 格式，不要其他内容）：
{{
  "relation_context": "一句话概括该角色在用户问题涉及的关系/事件中的位置（基于工具知识，不含推测）",
  "event_timeline": "该问题涉及的事件发展脉络（基于工具知识，按时间顺序简述，不含推测）",
  "kept_snippets": [保留的原文片段序号（与原文片段对应，仅保留与问题相关的）],
  "answerable": true/false,
  "reason": "简要说明：现有信息能否回答用户问题，缺什么"
}}
要求：
- relation_context / event_timeline 只写工具知识里有的，没有就写"（工具无此信息）"
- kept_snippets 基于原文相关性判断
- answerable=false 时说明缺什么信息
"""


class ImpersonationChatMixin:
    """Chat / tool-loop / message-building methods."""

    # Class constants referenced by mixin methods (defined here; subclass sees them).
    _STYLE_SAMPLE_TURNS: int = 3
    _STYLE_TOP_K: int = 3
    _STYLE_FETCH_K: int = 8
    _STYLE_MODE: str = "pool_turn"
    _STYLE_SKIP_ON_FACT_QUESTION: bool = True
    _STYLE_MIN_SCORE: float = 0.45
    _STYLE_REQUIRE_CHARACTER_MENTION: bool = True
    _NARRATIVE_TOP_K: int = 3
    _MAX_TOOL_ROUNDS: int = 3
    _FACT_GROUNDING_HINT = (
        "设定冲突时以「原著参考」为准；参考未写明的细节（外貌、关系、经历等）"
        "请明确表示不确定，禁止编造。\n"
        "知识边界：你只能知道【亲身经历或听说】的事情。工具/检索查到的信息"
        "若发生在你不在场、别人私事、或你不可能知道的时间地点——即使查到了，"
        "也要以角色身份表示不知道或不完全清楚，不要暴露你不该知道的信息。"
    )
    _NO_FACT_HINT = (
        "## 注意\n"
        "本次未检索到可靠原著事实片段。涉及设定/外貌/关系时请明确表示不确定，"
        "禁止编造原文未写明的细节。"
    )


    def _setup_system_prompt(self):
        prompt = self._card.to_prompt()
        self.memory.set_system_message(prompt + _TOOL_AWARE_PROMPT)

    async def maybe_compact(self) -> bool:
        """上下文压缩：token 估算超阈值时，把最早轮次折叠为摘要。

        每轮 chat 结束后调用；压缩失败静默降级（下轮重试），不影响回复。
        委托 src.core.compaction.compact_memory（先摘要成功再删除）。
        """
        if not getattr(self, "enable_summarization", False):
            return False
        from src.core.compaction import compact_memory

        return await compact_memory(
            mem=self.memory,
            llm=self._llm,
            character=self.character,
            summarize_threshold=self.summarize_threshold,
            keep_turns=self.summarize_keep_turns,
        )

    def _append_citations(
        self, items: list[Citation], *, role: str | None = None
    ) -> None:
        seen = {(c.channel, c.block_id) for c in self._last_citations}
        for c in items:
            if role:
                c.role = role
            key = (c.channel, c.block_id)
            if key in seen:
                continue
            seen.add(key)
            self._last_citations.append(c)

    def _citations_event_payload(self) -> dict[str, Any]:
        fact = [c.to_evidence() for c in self.get_last_fact_citations()]
        style = [c.to_evidence() for c in self.get_last_style_citations()]
        return {
            "type": "citations",
            "fact": fact,
            "style": style,
            # Compat: flat list keeps fact first so old UIs are less wrong.
            "items": fact + style,
        }

    async def chat(self, user_input: str) -> str:
        """One turn of character dialogue, with optional tool calls."""
        self._turn_count += 1
        self._last_citations = []
        self.memory.add_message("user", user_input)

        reply = await self._chat_with_tools(user_input)

        self.memory.add_message("assistant", reply)
        logger.debug(
            "Impersonation turn %d: char=%s, reply_len=%d citations=%d",
            self._turn_count, self.character, len(reply), len(self._last_citations),
        )
        # 上下文压缩（异步）：超阈值时把最早轮次折叠为摘要
        try:
            await self.maybe_compact()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Context compaction skipped: %s", exc)
        return reply

    async def chat_stream(self, user_input: str) -> AsyncGenerator[str]:
        """Streaming chat (token-only). Prefer ``iter_chat_events`` for citations."""
        async for event in self.iter_chat_events(user_input):
            if event.get("type") == "reply_chunk":
                yield str(event.get("token") or "")

    async def iter_chat_events(
        self, user_input: str
    ) -> AsyncGenerator[dict[str, Any]]:
        """Stream structured events: citations first, then reply_chunk tokens."""
        self._turn_count += 1
        self._last_citations = []
        self.memory.add_message("user", user_input)

        tool_facts, final_messages = await self._prepare_final_messages(user_input)
        yield self._citations_event_payload()

        reply_parts: list[str] = []
        try:
            async for token in self._llm.achat_stream(final_messages, temperature=0.85):
                reply_parts.append(token)
                yield {"type": "reply_chunk", "token": token}
        except asyncio.CancelledError:
            logger.info(
                "Impersonation stream cancelled char=%s partial_tokens=%d",
                self.character,
                len(reply_parts),
            )
            # Drop orphan user turn if no assistant reply was completed.
            if not reply_parts:
                self.pop_last_user()
            raise

        reply = "".join(reply_parts)
        self.memory.add_message("assistant", reply)
        logger.debug(
            "Impersonation stream turn %d: char=%s tools=%d reply_len=%d citations=%d",
            self._turn_count,
            self.character,
            len(tool_facts),
            len(reply),
            len(self._last_citations),
        )
        # 上下文压缩（异步）：超阈值时把最早轮次折叠为摘要
        try:
            await self.maybe_compact()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Context compaction skipped: %s", exc)

    def pop_last_assistant(self) -> str | None:
        """Remove the last assistant message (for regenerate). Returns content or None."""
        msgs = self.memory.get_messages()
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "assistant":
                content = msgs[i].get("content") or ""
                # ConversationMemory may not expose remove — rebuild without that msg
                kept = msgs[:i] + msgs[i + 1 :]
                self.memory.clear()
                for m in kept:
                    role = m.get("role")
                    if role == "system":
                        self.memory.set_system_message(m.get("content") or "")
                    elif role in ("user", "assistant"):
                        self.memory.add_message(role, m.get("content") or "")
                if self._turn_count > 0:
                    self._turn_count -= 1
                return content
        return None

    def pop_last_user(self) -> str | None:
        """Remove the last user message. Returns content or None."""
        msgs = self.memory.get_messages()
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                content = msgs[i].get("content") or ""
                kept = msgs[:i] + msgs[i + 1 :]
                self.memory.clear()
                for m in kept:
                    role = m.get("role")
                    if role == "system":
                        self.memory.set_system_message(m.get("content") or "")
                    elif role in ("user", "assistant"):
                        self.memory.add_message(role, m.get("content") or "")
                return content
        return None

    def reset(self) -> None:
        """Clear conversation history, preserve persona + tools."""
        self.memory.clear()
        self._setup_system_prompt()
        self._turn_count = 0
        self._last_citations = []
        logger.info("ImpersonationAgent reset: character=%s", self.character)

    def get_history(self) -> list[dict]:
        return [m for m in self.memory.get_messages() if m.get("role") in ("user", "assistant")]

    async def _chat_with_tools(self, user_input: str) -> str:
        """Core chat loop: LLM → maybe tool_call → execute → LLM → reply."""
        _facts, final_messages = await self._prepare_final_messages(user_input)
        # LLM 后处理（检索后、主回复前）：先调 world_knowledge 分析角色关系/
        # 事件发展，再结合原文片段分析，输出主回复需要的结构化数据。
        try:
            post_facts = await self._llm_postprocess_facts(user_input, final_messages)
            if post_facts:
                final_messages.append({"role": "system", "content": post_facts})
        except Exception as exc:  # noqa: BLE001 - 后处理失败不影响主链路
            logger.warning("LLM postprocess failed: %s", exc)
        # 判断回收：主 LLM 回答后附带 verdict JSON（非流式路径专用，
        # 流式 iter_chat_events 不走本方法，避免 verdict 污染 token 流）
        try:
            from src.core.impersonation.verdict import verdict_instruction

            final_messages.append({"role": "user", "content": verdict_instruction()})
        except Exception:  # noqa: BLE001
            pass
        reply = await self._llm.achat(final_messages, temperature=0.85)
        return await self._postprocess_reply(reply)

    async def _llm_postprocess_facts(
        self, user_input: str, final_messages: list[dict]
    ) -> str:
        """LLM 后处理（检索后、主回复前）：工具分析 + 原文结合 + 结构化输出。

        流程：
        1. 主动调 world_knowledge 工具——分析【当前角色的关系网和事件发展】
           （relations + character_events，代码层执行，不依赖 LLM 自觉）
        2. LLM 结合工具结果 + 检索原文片段，输出主回复需要的结构化数据
           （JSON：保留片段、关系/时间标注、可答性）——即"返回回复模型
           需要的所有数据"
        3. 输出注入 final_messages，主回复模型基于它回答（主提示词不变）

        设计要点（用户拍板）：
        - 工具调用放后处理节点，不改主回复提示词
        - 原文仍是事实依据；工具知识（关系/事件脉络）作标注补充
        - 判断结果同时供 verdict 回收（answerable 等）
        """
        if not self._card:
            return ""
        series_id = str(getattr(self._card, "series_id", "") or "").strip()
        if not series_id or not self.character:
            return ""
        # 1. 主动调 world_knowledge：角色关系 + 事件发展
        tool_knowledge: list[str] = []
        try:
            tool = self.tool_registry.get("world_knowledge")
            if tool is not None:
                for qtype, kwargs in (
                    ("relations", {"entity": self.character}),
                    ("character_events", {"entity": self.character}),
                ):
                    res = await tool.execute(
                        query_type=qtype, series_id=series_id, limit=8, **kwargs
                    )
                    if res.success and res.output and "无匹配" not in res.output:
                        tool_knowledge.append(
                            f"## 世界知识（{qtype}）\n{res.output[:800]}"
                        )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Postprocess world_knowledge failed: %s", exc)
        # 2. 提取检索原文片段（final_messages 里 fact context）
        fact_text = ""
        for m in final_messages:
            content = str(m.get("content") or "")
            if content.startswith("## 原著参考") or "参考以下" in content:
                fact_text = content
                break
        if not fact_text and not tool_knowledge:
            return ""
        # 3. LLM 后处理：结合工具 + 原文，输出结构化判断
        prompt = _POSTPROCESS_PROMPT.format(
            character=self.character,
            tool_knowledge="\n\n".join(tool_knowledge) if tool_knowledge else "（无）",
            original_text=fact_text[:3000] if fact_text else "（无）",
            user_input=user_input,
        )
        try:
            reply = await self._llm.achat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=600,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Postprocess LLM failed: %s", exc)
            return ""
        if not reply or not reply.strip():
            return ""
        return (
            "## LLM 后处理结果（关系/事件脉络 + 原文分析，回答时参考）\n"
            + reply.strip()
        )

    async def _postprocess_reply(self, reply: str) -> str:
        """主 LLM 回复后处理：解析 verdict 回收指标，返回剥离 verdict 的干净回复。"""
        try:
            from src.core.impersonation.verdict import parse_verdict, report_verdict

            clean, verdict = parse_verdict(reply)
            if verdict:
                report_verdict(verdict)
                return clean
        except Exception:  # noqa: BLE001 - 后处理失败不影响回复
            pass
        return reply

    async def _prepare_final_messages(
        self, user_input: str
    ) -> tuple[list[str], list[dict]]:
        """Run Phase1 tool loop and build Phase2 messages that include tool facts."""
        api_messages = self._build_api_messages(user_input)
        tools = self.tool_registry.get_openai_functions()
        tool_facts: list[str] = []

        if not tools:
            # No tools: build the final (retrieval-augmented) messages exactly once.
            # The former eager ``_build_messages`` at the top was discarded on the
            # tool path anyway, so deferring it here avoids a second full RAG pass
            # (style samples + fact context + rerank) per character turn.
            messages = await self._build_messages(user_input)
            return tool_facts, messages

        async def _execute(name: str, args: dict) -> str:
            from src.shared.tool_approvals import gate_tool_execution

            tool = self.tool_registry.get(name)
            if tool is None:
                return f"Error: tool '{name}' not found"
            denied = await gate_tool_execution(
                session_id=getattr(self, "session_id", "") or "",
                tool_name=name,
                tool_args=args or {},
            )
            if denied:
                return f"Error: {denied}"
            result: ToolResult = await tool.execute(**args)
            if result.success:
                return (result.output or "")[:800]
            return f"Error: {result.error}"

        tool_temp = 0.3
        try:
            from src.shared.llm_config import get_endpoint_config

            tool_temp = float(
                get_endpoint_config("impersonation_tool").get("temperature", 0.3)
            )
        except Exception:  # noqa: BLE001
            pass
        loop = await self._llm.achat_with_tools(
            api_messages,
            tools=tools,
            execute_tool=_execute,
            max_rounds=self._MAX_TOOL_ROUNDS,
            temperature=tool_temp,
            max_tokens=1024,
        )
        for inv in loop.invocations:
            tool_facts.append(f"[{inv.name}] {inv.result}")
            logger.info(
                "Impersonation tool: char=%s tool=%s args=%s → %s",
                self.character,
                inv.name,
                str(inv.arguments)[:80],
                inv.result[:80],
            )

        final_messages = await self._build_messages(user_input)
        if tool_facts:
            facts_blob = "\n".join(f"- {f}" for f in tool_facts)
            final_messages.append({
                "role": "system",
                "content": (
                    "你刚才通过工具获得了以下事实信息，请用角色语气自然融入回复，"
                    "不要提及查资料或工具：\n" + facts_blob
                ),
            })
        else:
            final_messages.append({
                "role": "system",
                "content": "现在用你的角色语气自然地回复用户。",
            })
        return tool_facts, final_messages

    # ── Message builders ────────────────────────────────────

    async def _build_messages(self, user_input: str) -> list[dict]:
        """Build messages for LLM: system + history + RAG context."""
        messages: list[dict] = [
            {"role": "system", "content": self.memory.get_messages()[0]["content"]
             if self.memory.get_messages() else self._card.to_prompt() + _TOOL_AWARE_PROMPT},
        ]

        # V5 P3：时间感知 Lorebook 动态注入（关键词 + 当前故事时间激活）
        try:
            from src.core.impersonation._lorebook import (
                activate_entries,
                format_entries_block,
                infer_current_time,
            )

            if self._lorebook_entries:
                # 从用户消息 + 最近历史检测当前故事时段（era 关键词，确定性）
                history = self.memory.get_messages()
                recent = [m for m in history if m.get("role") in ("user", "assistant")][-6:]
                ctx_text = (user_input or "") + "\n" + "\n".join(
                    str(m.get("content") or "") for m in recent
                )
                current_time = infer_current_time(ctx_text)
                active = activate_entries(
                    self._lorebook_entries,
                    user_input=user_input,
                    character=self.character,
                    current_time=current_time,
                )
                block = format_entries_block(active)
                if block:
                    messages[0] = {
                        "role": "system",
                        "content": messages[0]["content"] + "\n\n" + block,
                    }
        except Exception as exc:  # noqa: BLE001 - Lorebook 失败不影响主链路
            logger.debug("Lorebook inject failed: %s", exc)

        history = self.memory.get_messages()
        user_agent = [m for m in history if m.get("role") in ("user", "assistant")]
        history_lines = []
        for m in user_agent[-10:]:
            role_label = "用户" if m["role"] == "user" else self.character
            history_lines.append(f"{role_label}: {m['content']}")
        if history_lines:
            messages.append({"role": "user", "content": "## 当前对话\n" + "\n".join(history_lines)})

        # 上下文压缩摘要：较早轮次已被折叠，注入 system 块保证角色不遗忘已确认事实
        summary = self.memory.get_summary()
        if summary:
            messages.append({
                "role": "system",
                "content": (
                    "## 更早的对话摘要（已确认事实，回答时不得与之矛盾；"
                    "摘要之外未提及的细节不要自行补充）\n" + summary
                ),
            })

        if self._turn_count <= self._STYLE_SAMPLE_TURNS:
            samples_text = await self._retrieve_style_samples(user_input)
            if samples_text:
                messages.append({"role": "user", "content": samples_text})

        from src.application.novel.qa_expand import looks_like_fact_question

        fact_text = await self._retrieve_fact_context(user_input)
        if fact_text:
            messages.append({"role": "user", "content": fact_text})
            messages.append({
                "role": "system",
                "content": self._FACT_GROUNDING_HINT,
            })
        elif looks_like_fact_question(user_input or ""):
            messages.append({"role": "user", "content": self._NO_FACT_HINT})

        return messages

    def _build_api_messages(self, user_input: str) -> list[dict]:
        """Build OpenAI-compatible messages for tool-calling."""
        history = self.memory.get_messages()
        msgs = [m for m in history if m.get("role") in ("system", "user", "assistant")]
        # The current user turn was already appended to memory by chat() /
        # iter_chat_events() before this is called — appending it again would
        # send the user message twice to the tool-decision LLM. Defensive
        # dedupe keeps this correct even if a future caller skips the memory add.
        if not msgs or not (
            msgs[-1].get("role") == "user" and msgs[-1].get("content") == user_input
        ):
            msgs.append({"role": "user", "content": user_input})
        return msgs

