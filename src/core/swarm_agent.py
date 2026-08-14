"""SwarmAgent — LangGraph StateGraph-based streaming orchestrator.

Implements ADR-003: an explicit StateGraph with nodes + conditional edges,
now using ImpersonationAgent (src/) instead of legacy rag/ for roleplay routing.

Default general-chat path uses OpenAI-native tool calling
(``SharedLLMClient.achat_with_tools``). The legacy Planner→Executor path is
kept and enabled with ``AGENT_USE_PLANNER=1``.

Graph:

    START → classify ─┬─ native_tools ──────────────── END   (default)
                      ├─ plan ── execute ── reply ── END     (AGENT_USE_PLANNER=1)
                      ├─ rag_enter ── rag_chat ── END         (enter roleplay)
                      ├─ rag_chat ───────────────── END       (continue roleplay)
                      └─ exit_role ──────────────── END       (leave roleplay)

The frontend SSE contract (StreamEvent types) is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.core.agent import Agent
from src.core.executor import ExecutionResult
from src.core.native_tooling import build_native_messages, execute_tool_safely
from src.core.planner import TaskPlan
from src.core.prompts import reply_prompt_for
from src.shared.llm import ToolInvocation, ToolLoopResult
from src.shared.defaults import max_tool_rounds

logger = logging.getLogger("agent")


# ── StreamEvent (SSE-compatible, unchanged frontend contract) ──────────────


@dataclass
class StreamEvent:
    """Structured event for SSE streaming."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)


# ── Graph state schema ──────────────────────────────────────────────────────


class GraphState(TypedDict, total=False):
    """Mutable state passed between graph nodes."""

    user_input: str
    mode: str  # "native" | "plan" | "rag_enter" | "rag_chat" | "exit_role"
    plan: TaskPlan | None
    plan_error: str | None
    exec_result: ExecutionResult | None
    exec_success: bool
    detected_char: str | None  # roleplay detection from plan+tool results
    reply: str


# ── Routing keywords ────────────────────────────────────────────────────────

_ROLEPLAY_KEYWORDS = [
    "扮演", "角色扮演", "roleplay", "role-play", "cosplay",
    "用.*语气", "模仿.*说话", "假装你是", "你来扮演",
    "以.*的身份", "你现在是",
]
_EXIT_KEYWORDS = [
    "退出角色", "结束扮演", "停止扮演", "退出扮演",
    "exit role", "stop role", "切换角色", "换个角色",
]


def _use_planner() -> bool:
    return os.getenv("AGENT_USE_PLANNER", "").strip().lower() in {"1", "true", "yes", "on"}


# ── SwarmAgent ──────────────────────────────────────────────────────────────


class SwarmAgent:
    """LangGraph StateGraph orchestrator — routes to Agent or ImpersonationAgent."""

    def __init__(self, agent: Agent, session_id: str | None = None, *,
                 store=None):  # NovelVectorStore for ImpersonationAgent
        self.agent = agent
        self.session_id = session_id or "default"
        self._store = store
        self._imp_agent: Any = None          # ImpersonationAgent (was _rag_engine)
        self._rag_character: str | None = None
        self._event_queue: asyncio.Queue | None = None
        self._novel_scope: Any = None        # per-turn retrieval scope (series/doc_ids)

        self._checkpointer = MemorySaver()
        self._graph = self._build_graph().compile(checkpointer=self._checkpointer)
        logger.info("SwarmAgent initialized (StateGraph): session=%s", self.session_id)

    # ── Graph construction ──────────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(GraphState)
        graph.add_node("classify", self._classify_node)
        graph.add_node("native_tools", self._native_tools_node)
        graph.add_node("plan", self._plan_node)
        graph.add_node("execute", self._execute_node)
        graph.add_node("reply", self._reply_node)
        graph.add_node("rag_enter", self._rag_enter_node)
        graph.add_node("rag_chat", self._rag_chat_node)
        graph.add_node("exit_role", self._exit_role_node)

        graph.add_edge(START, "classify")
        graph.add_conditional_edges(
            "classify",
            lambda s: s.get("mode", "native"),
            {
                "native": "native_tools",
                "plan": "plan",
                "rag_enter": "rag_enter",
                "rag_chat": "rag_chat",
                "exit_role": "exit_role",
            },
        )
        graph.add_conditional_edges(
            "plan",
            lambda s: "reply" if s.get("plan_error") else "execute",
            {"execute": "execute", "reply": "reply"},
        )
        graph.add_edge("execute", "reply")
        graph.add_edge("reply", END)
        graph.add_edge("native_tools", END)
        graph.add_edge("rag_enter", "rag_chat")
        graph.add_edge("rag_chat", END)
        graph.add_edge("exit_role", END)
        return graph

    # ── Nodes ───────────────────────────────────────────────────────────────

    async def _classify_node(self, state: GraphState) -> dict[str, Any]:
        """Cascade: active imp → exit/roleplay regex → native tools (or planner)."""
        user_input = state["user_input"]

        if self._imp_agent is not None:
            return {"mode": "exit_role" if _is_exit_cmd(user_input) else "rag_chat"}

        explicit_char = _extract_char(user_input, self._store)
        if explicit_char:
            ok = await _enter_impersonation(self, explicit_char)
            if ok:
                return {"mode": "rag_enter", "detected_char": explicit_char}
            logger.warning(
                "Impersonation init failed for '%s'; falling back to general mode.",
                explicit_char,
            )

        return {"mode": "plan" if _use_planner() else "native"}

    async def _native_tools_node(self, state: GraphState) -> dict[str, Any]:
        """Default path: OpenAI-native tool calling + streamed reply."""
        user_input = state["user_input"]
        self.agent.memory.add_message("user", user_input)
        self.agent.refresh_system_prompt_for_tools()

        await self._emit("phase", {"phase": "tool_calling"})
        await self._emit("plan", {
            "goal": user_input[:200],
            "reasoning": "Native tool calling (OpenAI tools protocol)",
            "steps": [],
        })

        tools = self.agent.tool_registry.get_openai_functions()
        # role 过滤 + 检索范围注入 + 摘要注入（见 build_native_messages）
        messages = build_native_messages(
            memory=self.agent.memory,
            scope_note=self._scope_prompt(),
        )

        plan_steps: list[dict[str, Any]] = []

        async def execute_tool(name: str, args: dict) -> str:
            return await execute_tool_safely(
                name,
                args,
                registry=self.agent.tool_registry,
                session_id=self.session_id,
                emit=self._emit,
                adjust_args=self._adjust_tool_args,
            )

        async def on_tool(inv: ToolInvocation) -> None:
            step_id = len(plan_steps) + 1
            desc = f"{inv.name}({_brief_args(inv.arguments)})"
            plan_steps.append({
                "id": step_id,
                "description": desc,
                "tool_name": inv.name,
            })
            await self._emit("plan", {
                "goal": user_input[:200],
                "reasoning": "Native tool calling (OpenAI tools protocol)",
                "steps": list(plan_steps),
            })
            await self._emit("step_result", {
                "step_id": step_id,
                "success": inv.success,
                "output": (inv.result or "")[:500],
                "error": None if inv.success else (inv.result or "")[:300],
                "retries": 0,
            })

        try:
            loop_result: ToolLoopResult = await self.agent._shared_llm.achat_with_tools(
                messages,
                tools=tools,
                execute_tool=execute_tool,
                on_tool=on_tool,
                max_rounds=max_tool_rounds(),
                temperature=self.agent.config.temperature,
                max_tokens=self.agent.config.max_tokens,
            )
        except Exception as e:
            logger.exception("Native tool loop failed")
            await self._emit("error", {"phase": "tool_calling", "message": str(e)})
            err = f"工具调用失败: {e}"
            self.agent.memory.add_message("assistant", err)
            await self._emit("reply_chunk", {"token": err})
            return {"reply": err}

        detected = _detect_roleplay_from_invocations(loop_result.invocations)
        if detected:
            ok = await _enter_impersonation(self, detected)
            if ok:
                msg = f"(已切换至 {detected} 角色扮演模式。)"
                self.agent.memory.add_message("assistant", msg)
                await self._emit("reply_chunk", {"token": msg})
                return {"reply": msg, "detected_char": detected}

        await self._emit("phase", {"phase": "replying"})
        full_reply = loop_result.content or ""
        if not full_reply and loop_result.invocations:
            # Model returned empty after tools — ask for a plain summary stream.
            summary_msgs = [
                m for m in loop_result.messages
                if m.get("role") in ("system", "user", "assistant", "tool")
            ]
            try:
                async for token in self.agent._shared_llm.achat_stream(
                    messages=summary_msgs
                ):
                    full_reply += token
                    await self._emit("reply_chunk", {"token": token})
            except Exception as e:
                logger.error("Native reply stream failed: %s", e)
                full_reply = full_reply or f"(回复生成失败: {e})"
                await self._emit("reply_chunk", {"token": full_reply})
        else:
            # Emit final content (chunked for SSE UX).
            if full_reply:
                for piece in _chunk_text(full_reply, 48):
                    await self._emit("reply_chunk", {"token": piece})
                    await asyncio.sleep(0)
            else:
                full_reply = "（模型未返回内容）"
                await self._emit("reply_chunk", {"token": full_reply})

        self.agent.memory.add_message("assistant", full_reply)
        # 上下文压缩（异步）：SSE 长对话超阈值时折叠最早轮次，与 Agent 路径一致
        try:
            await self.agent.maybe_compact()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Context compaction skipped: %s", exc)
        return {"reply": full_reply, "detected_char": detected}

    async def _plan_node(self, state: GraphState) -> dict[str, Any]:
        user_input = state["user_input"]
        self.agent.memory.add_message("user", user_input)
        await self._emit("phase", {"phase": "planning"})
        try:
            tools = self.agent.tool_registry.get_openai_functions()
            plan = await self.agent.planner.plan(
                user_input=user_input, memory=self.agent.memory, available_tools=tools)
            await self._emit("plan", {
                "goal": plan.goal, "reasoning": plan.reasoning,
                "steps": [{"id": s.id, "description": s.description, "tool_name": s.tool_name}
                          for s in plan.steps],
            })
            return {"plan": plan, "plan_error": None}
        except Exception as e:
            logger.warning("Planning failed: %s", e)
            await self._emit("phase", {"phase": "plan_failed", "message": str(e)})
            return {"plan": None, "plan_error": str(e)}

    async def _execute_node(self, state: GraphState) -> dict[str, Any]:
        plan = state["plan"]
        await self._emit("phase", {"phase": "executing"})
        exec_result = ExecutionResult(success=False, step_results=[], final_output="")
        try:
            self.agent.executor.session_id = self.session_id
            self.agent.executor.emit = self._emit
            exec_result = await self.agent.executor.execute(
                plan=plan, memory=self.agent.memory, working_memory=self.agent.working_memory)
            for sr in exec_result.step_results:
                await self._emit("step_result", {
                    "step_id": sr.step_id, "success": sr.success,
                    "output": (sr.output or "")[:300], "error": sr.error, "retries": sr.retries,
                })
        except Exception as e:
            logger.error("Execution failed: %s", e)
            await self._emit("error", {"phase": "execution", "message": str(e)})

        detected = _detect_roleplay(plan, exec_result)
        if detected:
            ok = await _enter_impersonation(self, detected)
            if not ok:
                detected = None
        return {"exec_result": exec_result, "exec_success": exec_result.success, "detected_char": detected}

    async def _reply_node(self, state: GraphState) -> dict[str, Any]:
        await self._emit("phase", {"phase": "replying"})
        full_reply = ""
        if state.get("plan_error"):
            async for token in _stream_direct_reply(self):
                full_reply += token
                await self._emit("reply_chunk", {"token": token})
        else:
            plan = state["plan"]
            exec_result = state["exec_result"]
            detected = state.get("detected_char")
            if detected:
                msg = f"(已切换至 {detected} 角色扮演模式。)"
                full_reply = msg
                await self._emit("reply_chunk", {"token": msg})
            else:
                async for token in _stream_reply(self, plan, exec_result, state.get("exec_success", True)):
                    full_reply += token
                    await self._emit("reply_chunk", {"token": token})
        self.agent.memory.add_message("assistant", full_reply)
        return {"reply": full_reply}

    async def _rag_enter_node(self, state: GraphState) -> dict[str, Any]:
        """No-op: ImpersonationAgent initialized in _classify_node."""
        return {}

    async def _rag_chat_node(self, state: GraphState) -> dict[str, Any]:
        """Stream a character reply via ImpersonationAgent.

        注意：扮演对话只写入 ImpersonationAgent 自己的 memory，不写通用
        agent.memory——避免双 memory 消息不平衡、以及扮演内容污染通用会话
        历史（普通对话看不到扮演内容，扮演内容也由扮演链路持久化）。
        """
        user_input = state["user_input"]
        char = self._rag_character or "角色"
        await self._emit("phase", {"phase": "replying", "message": f"Replying as {char}..."})

        full = ""
        if self._imp_agent:
            try:
                async for token in self._imp_agent.chat_stream(user_input):
                    full += token
                    await self._emit("reply_chunk", {"token": token})
            except Exception as e:
                logger.error("Impersonation chat failed: %s", e)
                full = f"(角色回复失败: {e})"
                await self._emit("reply_chunk", {"token": full})
        else:
            full = "(角色 Agent 未就绪)"
            await self._emit("reply_chunk", {"token": full})
        return {"reply": full}

    async def _exit_role_node(self, state: GraphState) -> dict[str, Any]:
        """Leave roleplay mode, return to normal planning."""
        char = self._rag_character
        self._rag_character = None
        self._imp_agent = None
        reply = f"好的，已退出 {char} 的角色扮演。回到普通对话。"
        await self._emit("reply_chunk", {"token": reply})
        return {"reply": reply}

    # ── Event emission ──────────────────────────────────────────────────────

    def _scope_prompt(self) -> str:
        """Build the retrieval-scope note injected into the tool-calling prompt."""
        if self._novel_scope is None:
            return ""
        scope = self._novel_scope
        parts: list[str] = ["【当前检索范围】"]
        if getattr(scope, "series_id", None):
            parts.append(f"当前作品/系列：{scope.series_id}。")
        doc_ids = list(getattr(scope, "doc_ids", None) or [])
        if doc_ids:
            parts.append("允许检索的卷：" + ", ".join(doc_ids[:10]))
        parts.append(
            "使用 novel_search 时必须传 series（或 doc_id）参数限定在上述范围内，"
            "禁止检索其他作品的内容。"
        )
        return "\n".join(parts)

    def _apply_scope_to_tool_args(self, args: dict) -> dict:
        """Force novel_search args inside the current retrieval scope.

        - series_id 存在：注入 series（除非显式 doc_id 已属于其他系列）
        - doc_ids 单卷：注入 doc_id；多卷：交给 series 级隔离（系列内已安全）
        """
        scope = self._novel_scope
        if scope is None:
            return args
        action = str(args.get("action") or "search")
        if action not in ("search", "impersonate"):
            return args
        out = dict(args)
        series_id = getattr(scope, "series_id", None)
        doc_ids = list(getattr(scope, "doc_ids", None) or [])
        if series_id:
            cur_doc = str(out.get("doc_id") or "")
            if not cur_doc:
                out.setdefault("series", series_id)
            elif not cur_doc.startswith(str(series_id)):
                # 显式 doc_id 属于其他系列：覆盖为当前 scope 的系列（强制隔离）
                out["series"] = str(series_id)
        elif doc_ids:
            if not out.get("doc_id"):
                if len(doc_ids) == 1:
                    out["doc_id"] = doc_ids[0]
                else:
                    out["series"] = _series_of_doc_id(doc_ids[0])
        return out

    def _adjust_tool_args(self, name: str, args: dict) -> dict:
        """execute_tool_safely 的参数修正回调：强制 novel_search 限定检索范围。

        即使 LLM 未传 series/doc_id，也自动限定（根治跨作品污染）。
        """
        if name == "novel_search" and self._novel_scope is not None:
            return self._apply_scope_to_tool_args(args)
        return args

    async def _emit(self, event_type: str, data: dict) -> None:
        if self._event_queue is not None:
            await self._event_queue.put(StreamEvent(type=event_type, data=data))

    # ── Main entry ──────────────────────────────────────────────────────────

    async def run_stream(self, user_input: str, novel_scope=None) -> AsyncGenerator[StreamEvent]:
        """Stream SSE events from the StateGraph execution.

        Client disconnect / generator aclose cancels the underlying graph task
        so LLM work does not continue after the SSE pipe is gone.

        ``novel_scope``（可选）限定本次会话的 novel 检索范围（series/doc_ids），
        根治跨作品检索污染；每轮调用自动清空。
        """
        from src.shared.telemetry import span

        t_start = time.perf_counter()
        self._novel_scope = novel_scope
        self._event_queue = asyncio.Queue()
        config = {"configurable": {"thread_id": self.session_id}}
        graph_task: asyncio.Task | None = None

        def _done_payload() -> dict[str, Any]:
            elapsed = int((time.perf_counter() - t_start) * 1000)
            extra: dict[str, Any] = {}
            if self._rag_character:
                extra = {"rag_mode": True, "character": self._rag_character}
            usage = None
            try:
                usage = getattr(self.agent, "_shared_llm", None)
                usage = usage.last_usage if usage is not None else None
            except Exception:
                usage = None
            if usage:
                extra["usage"] = usage
            return {"elapsed_ms": elapsed, "session_id": self.session_id, **extra}

        async def _cancel_graph() -> None:
            if graph_task is None or graph_task.done():
                return
            graph_task.cancel()
            try:
                await graph_task
            except (asyncio.CancelledError, Exception):
                pass

        with span(
            "swarm.run_stream",
            session_id=self.session_id,
            input_chars=len(user_input or ""),
            rag_character=self._rag_character or "",
        ):
            try:
                graph_task = asyncio.create_task(self._invoke_graph(user_input, config))
                while not graph_task.done() or not self._event_queue.empty():
                    try:
                        event = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
                        yield event
                    except TimeoutError:
                        continue
                await graph_task
                yield StreamEvent("done", _done_payload())
            except asyncio.CancelledError:
                logger.info(
                    "Swarm stream cancelled session_id=%s",
                    self.session_id,
                )
                raise
            except Exception as e:
                logger.exception("Swarm stream error")
                yield StreamEvent("error", {"message": str(e)})
                yield StreamEvent("done", _done_payload())
            finally:
                await _cancel_graph()
                self._event_queue = None
                self._novel_scope = None

    async def _invoke_graph(self, user_input: str, config: dict) -> dict:
        return await self._graph.ainvoke({"user_input": user_input}, config)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _is_exit_cmd(user_input: str) -> bool:
    return any(re.search(k, user_input, re.IGNORECASE) for k in _EXIT_KEYWORDS)


def _brief_args(args: dict) -> str:
    parts = []
    for k, v in list(args.items())[:4]:
        s = str(v)
        if len(s) > 40:
            s = s[:40] + "…"
        parts.append(f"{k}={s}")
    return ", ".join(parts)

def _series_of_doc_id(doc_id: str) -> str:
    """Strip the ``__volNN`` suffix from a doc_id to get its series_id."""
    import re
    return re.sub(
        r"__vol\d+$", "", (doc_id or "").strip(), flags=re.IGNORECASE
    ) or (doc_id or "")


def _chunk_text(text: str, size: int) -> list[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def _extract_char(user_input: str, store=None) -> str | None:
    """Extract character name from an explicit roleplay request.

    Two-pass strategy:
    1. Match against known characters from NovelVectorStore (most reliable).
    2. Regex fallback with stoplist.
    """
    has_kw = any(re.search(k, user_input, re.IGNORECASE) for k in _ROLEPLAY_KEYWORDS)
    if not has_kw:
        return None

    if store is not None:
        try:
            for name in (store.list_characters() or []):
                if name in user_input:
                    return name
        except Exception:
            pass

    stop = r'和|与|跟|的|了|在|是|来|去|说|聊|讲话|对话|聊天|，|。|,|\.|\s'
    patterns = [
        rf'扮演\s*([^\s]{{2,20}}?)(?:{stop}|$)',
        r'以\s*([^\s]{2,20}?)\s*(?:的|语气|身份)',
        r'(?:用|模仿)\s*([^\s]{2,20}?)\s*(?:的|语气|方式)',
        rf'假装你是\s*([^\s]{{2,20}}?)(?:{stop}|$)',
        rf'你现在是\s*([^\s]{{2,20}}?)(?:{stop}|$)',
    ]
    for p in patterns:
        m = re.search(p, user_input)
        if m:
            c = m.group(1).strip()
            if c and c not in {'我', '你', '他', '她', '它', '自己', '一个', '角色', '别人'}:
                return c
    return None


async def _enter_impersonation(swarm: SwarmAgent, character: str) -> bool:
    """Initialize ImpersonationAgent for a character (replaces legacy _enter_rag)."""
    try:
        from src.core.impersonation_agent import create_impersonation_agent

        store = swarm._store
        if store is None:
            # 与 API 层共享同一个 NovelVectorStore（api_state.imp_store）：
            # 1) 避免每次进入扮演都新建 LanceDB 连接 / embedding / keyword 索引；
            # 2) 上传新书后 api_state 会置 store_dirty 并重建共享 store，
            #    扮演下次进入自然拿到新 store —— 否则 swarm 持有的旧连接
            #    永远检索不到运行时上传的新书（隐藏同步 bug）。
            from src.api import state as api_state

            store = api_state.imp_store
            if store is None:
                from src.application.novel.factory import create_novel_store

                store = create_novel_store()
                api_state.imp_store = store
            swarm._store = store

        config = swarm.agent.config
        from src.shared.llm_factory import create_shared_llm

        llm = create_shared_llm(
            config, temperature=0.85, max_tokens=1024, endpoint="agent_chat",
        )

        shared_tools = []
        for name in ("web_search", "novel_search", "execute_code"):
            if swarm.agent.tool_registry.has(name):
                shared_tools.append(swarm.agent.tool_registry.get(name))

        swarm._imp_agent = await create_impersonation_agent(
            character=character, store=store, llm=llm, tools=shared_tools)
        swarm._rag_character = character
        logger.info("Entered impersonation mode: character=%s tools=%d",
            character, len(shared_tools))
        return True
    except Exception as e:
        logger.warning("Impersonation init failed for %s: %s", character, e)
        return False


def _detect_roleplay_from_invocations(invocations: list[ToolInvocation]) -> str | None:
    for inv in invocations:
        if inv.name in ("novel_search", "rag") and inv.arguments.get("action") == "impersonate":
            char = inv.arguments.get("character")
            if char:
                return str(char)
    return None


def _detect_roleplay(plan: TaskPlan | None, exec_result: Any) -> str | None:
    """Detect implicit roleplay intent from plan + execution results."""
    if plan is None:
        return None
    goal = plan.goal + " " + " ".join(s.description for s in plan.steps)
    is_rp = any(re.search(k, goal, re.IGNORECASE) for k in _ROLEPLAY_KEYWORDS)
    if not is_rp:
        return None
    for s in plan.steps:
        if s.tool_name in ("rag", "novel_search") and s.tool_args:
            c = s.tool_args.get("character")
            if c:
                return c
    return None


# ── Streaming helpers (legacy planner path) ─────────────────────────────────


async def _stream_reply(swarm, plan, exec_result, success):
    tools_used = any(s.tool_name for s in plan.steps)
    summaries, failed = [], []
    for sr in getattr(exec_result, 'step_results', []):
        (summaries if getattr(sr, 'success', True) else failed).append(
            f"Step {getattr(sr, 'step_id', '?')}: {getattr(sr, 'output', '')[:200]}")
    sp = reply_prompt_for(tools_used=tools_used, success=success)
    # Deep-copy each message dict — get_messages() shares dict objects with
    # memory; mutating them here would corrupt the session's system prompt
    # and user messages.
    msgs = [dict(m) for m in swarm.agent.memory.get_messages() if m.get("role") != "tool"]
    if msgs and msgs[0]["role"] == "system":
        msgs[0]["content"] = sp
    else:
        msgs.insert(0, {"role": "system", "content": sp})
    if tools_used:
        ctx = f"Goal: {plan.goal}\n"
        if summaries: ctx += "\nSucceeded:\n" + "\n".join(summaries)
        if failed: ctx += "\n\nFailed:\n" + "\n".join(failed)
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "user":
                msgs[i]["content"] = f"[Execution context]\n{ctx}\n\n[User]\n{msgs[i]['content']}"
                break
    try:
        async for t in swarm.agent._shared_llm.achat_stream(messages=msgs):
            yield t
    except Exception as e:
        logger.error("Reply stream failed: %s", e)


async def _stream_direct_reply(swarm):
    msgs = [m for m in swarm.agent.memory.get_messages() if m.get("role") != "tool"]
    try:
        async for t in swarm.agent._shared_llm.achat_stream(messages=msgs):
            yield t
    except Exception as e:
        logger.error("Direct reply stream failed: %s", e)
