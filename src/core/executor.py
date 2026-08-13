"""Task executor — executes plan steps with retry logic and dependency ordering."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Union

from openai import AsyncOpenAI

from src.core.memory import ConversationMemory, WorkingMemory
from src.core.planner import Step, TaskPlan
from src.tools.base import ToolResult
from src.tools.registry import ToolRegistry
from src.utils.errors import ToolNotFoundError

logger = logging.getLogger("agent")

# SharedLLMClient is optional at type-check time to avoid circular imports in tests.
LLMClientLike = Union[AsyncOpenAI, Any]


@dataclass
class StepResult:
    """Result of executing a single step.

    Attributes:
        step_id: The step's identifier.
        success: Whether the step completed successfully.
        output: The tool output or text result.
        error: Error message if success is False.
        retries: Number of retries attempted.
    """

    step_id: int
    success: bool
    output: str = ""
    error: str | None = None
    retries: int = 0


@dataclass
class ExecutionResult:
    """Aggregate result of executing a full TaskPlan.

    Attributes:
        success: True if all steps completed successfully.
        step_results: Per-step results in execution order.
        final_output: Aggregated human-readable output.
    """

    success: bool
    step_results: list[StepResult] = field(default_factory=list)
    final_output: str = ""

    @property
    def failed_steps(self) -> list[StepResult]:
        """Return steps that did not succeed."""
        return [r for r in self.step_results if not r.success]


class TaskExecutor:
    """Executes a TaskPlan step-by-step with retry and adaptive re-planning.

    Handles:
        - Dependency-ordered execution (using topological ordering).
        - Exponential backoff retries on failure.
        - Automatic storage of results in WorkingMemory.
        - Optional LLM-driven dynamic step adjustment.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        llm_client: Any,
        model: str = "deepseek-v4-flash",
        max_retries: int = 3,
    ) -> None:
        """Initialize the executor.

        Args:
            tool_registry: The tool registry for looking up tools.
            llm_client: AsyncOpenAI client or SharedLLMClient (failover-aware).
            model: Model to use for re-planning calls.
            max_retries: Maximum retry attempts per step.
        """
        self._registry = tool_registry
        self._llm = llm_client
        self._model = model
        self._max_retries = max_retries
        self.session_id: str = ""
        self.emit = None  # optional async (event_type, data) for HITL SSE

    @property
    def _llm_client(self) -> AsyncOpenAI:
        """Resolve the live AsyncOpenAI client after SharedLLMClient failover."""
        client = self._llm
        async_client = getattr(client, "async_client", None)
        if async_client is not None and async_client is not client:
            return async_client
        return client

    async def execute(
        self,
        plan: TaskPlan,
        memory: ConversationMemory,
        working_memory: WorkingMemory,
    ) -> ExecutionResult:
        """Execute a task plan.

        Args:
            plan: The TaskPlan to execute.
            memory: ConversationMemory for context.
            working_memory: WorkingMemory for inter-step data sharing.

        Returns:
            An ExecutionResult summarizing all step outcomes.

        Raises:
            ExecutionError: If a critical failure prevents execution.
        """
        logger.info("Executing plan: %d steps", len(plan.steps))
        step_results: list[StepResult] = []
        completed: dict[int, StepResult] = {}

        # Sort steps by dependency order (topological-ish)
        ordered_steps = self._topological_sort(plan.steps)

        for step in ordered_steps:
            logger.info("Executing step %d: %s", step.id, step.description)

            # Wait for dependencies
            for dep_id in step.depends_on:
                if dep_id not in completed:
                    logger.warning(
                        "Step %d: dependency step %d not completed — skipping",
                        step.id,
                        dep_id,
                    )
                    continue
                dep_result = completed[dep_id]
                if not dep_result.success:
                    logger.warning(
                        "Step %d: dependency step %d failed — marking as skipped",
                        step.id,
                        dep_id,
                    )
                    result = StepResult(
                        step_id=step.id,
                        success=False,
                        error=f"Dependency step {dep_id} failed.",
                    )
                    step_results.append(result)
                    completed[step.id] = result
                    break
            else:
                # All dependencies OK — execute the step
                result = await self._execute_step_with_retry(step, working_memory)
                step_results.append(result)
                completed[step.id] = result

            # Store result in working memory
            if result.success:
                working_memory.set(f"step_{step.id}_output", result.output)
            else:
                working_memory.set(f"step_{step.id}_error", result.error)

            # Add tool result to conversation memory
            if result.output:
                memory.add_message(
                    "tool",
                    f"Step {step.id} ({step.description}):\n{result.output[:500]}",
                )

        all_success = all(r.success for r in step_results)
        final_output = self._build_final_output(plan, step_results, all_success)

        logger.info(
            "Execution complete: success=%s, steps=%d, failed=%d",
            all_success,
            len(step_results),
            len([r for r in step_results if not r.success]),
        )

        return ExecutionResult(
            success=all_success,
            step_results=step_results,
            final_output=final_output,
        )

    async def _execute_step_with_retry(
        self,
        step: Step,
        working_memory: WorkingMemory,
    ) -> StepResult:
        """Execute a single step with exponential backoff retry.

        Args:
            step: The step to execute.
            working_memory: WorkingMemory for resolving references.

        Returns:
            A StepResult indicating success or failure.
        """
        last_error: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                if step.tool_name:
                    # Resolve tool args with working memory values
                    resolved_args = self._resolve_args(step.tool_args or {}, working_memory)

                    # Look up and execute the tool
                    tool = self._registry.get(step.tool_name)
                    from src.shared.tool_approvals import gate_tool_execution

                    denied = await gate_tool_execution(
                        session_id=self.session_id or "",
                        tool_name=step.tool_name,
                        tool_args=resolved_args or {},
                        emit=self.emit,
                    )
                    if denied:
                        tool_result = ToolResult.fail(denied)
                    else:
                        tool_result = await tool.execute(**resolved_args)

                    if tool_result.success:
                        logger.info(
                            "Step %d: tool '%s' succeeded (attempt %d)",
                            step.id,
                            step.tool_name,
                            attempt + 1,
                        )
                        return StepResult(
                            step_id=step.id,
                            success=True,
                            output=tool_result.output,
                            retries=attempt,
                        )
                    else:
                        last_error = tool_result.error or "Unknown tool error"
                        logger.warning(
                            "Step %d: tool '%s' returned failure (attempt %d): %s",
                            step.id,
                            step.tool_name,
                            attempt + 1,
                            last_error,
                        )
                else:
                    # Reasoning-only step
                    return StepResult(
                        step_id=step.id,
                        success=True,
                        output=f"Completed: {step.description}",
                        retries=0,
                    )

            except ToolNotFoundError as e:
                logger.error("Step %d: tool not found: %s", step.id, step.tool_name)
                return StepResult(
                    step_id=step.id,
                    success=False,
                    error=str(e),
                    retries=attempt,
                )
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    "Step %d: execution error (attempt %d/%d): %s",
                    step.id,
                    attempt + 1,
                    self._max_retries + 1,
                    e,
                )

            # Exponential backoff before retry
            if attempt < self._max_retries:
                delay = 2 ** attempt  # 1s, 2s, 4s, ...
                logger.debug("Retrying step %d in %.1fs...", step.id, delay)
                await asyncio.sleep(delay)

        # All retries exhausted — return failure instead of raising
        # (so the executor can continue with other independent steps)
        logger.error(
            "Step %d: all %d retries exhausted. Last error: %s",
            step.id,
            self._max_retries,
            last_error,
        )
        return StepResult(
            step_id=step.id,
            success=False,
            error=f"{last_error} (after {self._max_retries + 1} attempts)",
            retries=self._max_retries,
        )

    def _resolve_args(
        self,
        args: dict[str, Any],
        working_memory: WorkingMemory,
    ) -> dict[str, Any]:
        """Resolve argument references to WorkingMemory values.

        Supports `$step_N_output` and `$step_N_error` references.

        Args:
            args: Raw tool arguments from the plan.
            working_memory: The working memory instance.

        Returns:
            A dict with resolved values.
        """
        resolved: dict[str, Any] = {}
        for key, value in args.items():
            if isinstance(value, str) and value.startswith("$"):
                # Reference to working memory
                resolved[key] = working_memory.get(value[1:], value)
            else:
                resolved[key] = value
        return resolved

    def _topological_sort(self, steps: list[Step]) -> list[Step]:
        """Sort steps so that dependencies come before dependents.

        Uses a simple greedy approach: repeatedly emit steps whose
        dependencies are already satisfied.

        Args:
            steps: The list of steps to sort.

        Returns:
            Steps in dependency order.
        """
        remaining = list(steps)
        sorted_steps: list[Step] = []
        emitted_ids: set = set()

        while remaining:
            # Find a step whose dependencies are all satisfied
            for i, step in enumerate(remaining):
                if all(dep in emitted_ids for dep in step.depends_on):
                    sorted_steps.append(step)
                    emitted_ids.add(step.id)
                    remaining.pop(i)
                    break
            else:
                # Circular dependency or invalid dependency reference
                # Just emit remaining in order
                logger.warning(
                    "Possible circular dependency detected in steps. "
                    "Emitting remaining %d steps in original order.",
                    len(remaining),
                )
                sorted_steps.extend(remaining)
                break

        return sorted_steps

    def _build_final_output(
        self,
        plan: TaskPlan,
        step_results: list[StepResult],
        all_success: bool,
    ) -> str:
        """Build a human-readable summary of execution.

        Args:
            plan: The original task plan.
            step_results: Results from executing steps.
            all_success: Whether all steps passed.

        Returns:
            A formatted multi-line string.
        """
        lines = [
            f"Goal: {plan.goal}",
            f"Status: {'SUCCESS' if all_success else 'PARTIAL FAILURE'}",
            "=" * 50,
        ]
        for r in step_results:
            status = "✓" if r.success else "✗"
            lines.append(f"  [{status}] Step {r.step_id}: {r.output[:200]}")
            if r.error:
                lines.append(f"       Error: {r.error[:200]}")
        return "\n".join(lines)
