"""Task planner — decomposes user requests into structured execution plans."""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from src.core.memory import ConversationMemory
from src.shared.llm import SharedLLMClient
from src.utils.errors import PlanningError

logger = logging.getLogger("agent")


@dataclass
class Step:
    """A single step in the task plan.

    Attributes:
        id: Unique step identifier (1-indexed).
        description: Human-readable description of the step.
        tool_name: Optional tool to invoke for this step.
        tool_args: Arguments to pass to the tool if tool_name is set.
        depends_on: List of step IDs that must complete before this step.
    """

    id: int
    description: str
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    depends_on: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Step":
        """Create a Step from a dictionary (for JSON deserialization)."""
        return cls(
            id=int(data.get("id", 0)),
            description=str(data.get("description", "")),
            tool_name=data.get("tool_name"),
            tool_args=data.get("tool_args"),
            depends_on=[int(d) for d in data.get("depends_on", [])],
        )


@dataclass
class TaskPlan:
    """A structured plan for completing a user's request.

    Attributes:
        goal: The original user goal.
        steps: Ordered list of Steps to execute.
        reasoning: The planner's reasoning about why this plan was chosen.
    """

    goal: str
    steps: list[Step]
    reasoning: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskPlan":
        """Create a TaskPlan from a dictionary (for JSON deserialization)."""
        return cls(
            goal=str(data.get("goal", "")),
            steps=[Step.from_dict(s) for s in data.get("steps", [])],
            reasoning=str(data.get("reasoning", "")),
        )


PLANNER_SYSTEM_PROMPT = """You are a task planning assistant. Given a user's request and available tools, create a structured execution plan.

Your plan must be a valid JSON object with these fields:
- "goal": A concise restatement of the user's objective.
- "reasoning": Your reasoning about the approach (1-3 sentences).
- "steps": A list of step objects, each containing:
  - "id": Integer step number starting from 1.
  - "description": What this step does.
  - "tool_name": (optional) Name of the tool to use, or null if no tool needed.
  - "tool_args": (optional) Object of arguments for the tool, or null.
  - "depends_on": List of step IDs that must finish before this step, or empty list.

Rules:
1. Steps should be minimal and focused — one concrete action per step.
2. Use tools when appropriate; reasoning-only steps are fine too.
3. Set depends_on correctly. A step should only depend on steps whose outputs it actually needs. Do NOT chain dependencies through unnecessary intermediate steps.
4. Only use tools that are listed as available.
5. DO NOT create conditional "if X fails / if X doesn't exist" fallback steps. Plans are linear: each step runs once. Make a concrete choice (e.g. read README.md directly) and let the executor report failures if they occur.
6. When reading files, prefer known standard paths (e.g. README.md). Do NOT guess alternative filenames like README.txt unless the user explicitly mentions them.
7. Maintain continuity: if the previous assistant message was a character reply from the `rag` tool (marked with [Character: X]), and the user's follow-up is clearly a conversation with that character (not a new topic or a self-introduction), continue using `rag` `chat` with the SAME character. If the user says things like "我是.../我叫..." (introducing themselves) or changes the topic entirely, respond naturally without using rag tools.
8. You have access to the recent conversation history in the messages above. If the user asks about previous turns (e.g. "what did I say earlier", "我刚才说了什么", "我第一次说了什么"), answer directly from the conversation history without using any tools. Do not claim you cannot access the history.
9. Output ONLY the JSON, no other text.

Example for "Introduce this project":
{
  "goal": "Introduce the project based on its files",
  "reasoning": "List the project directory and read the README to summarize the project.",
  "steps": [
    {"id": 1, "description": "List files in the project root", "tool_name": "file_operation", "tool_args": {"operation": "list", "path": "."}, "depends_on": []},
    {"id": 2, "description": "Read README.md to get project overview", "tool_name": "file_operation", "tool_args": {"operation": "read", "path": "README.md"}, "depends_on": []},
    {"id": 3, "description": "Summarize the project introduction", "tool_name": null, "tool_args": null, "depends_on": [1, 2]}
  ]
}"""


class TaskPlanner:
    """Generates structured TaskPlan from user input using LLM function calling.

    Uses the configured LLM to decompose user requests into ordered steps.
    """

    def __init__(
        self,
        llm_client: AsyncOpenAI | SharedLLMClient,
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> None:
        """Initialize the planner.

        Args:
            llm_client: SharedLLMClient (preferred, enables failover) or AsyncOpenAI.
            model: Model identifier for planning calls (used when client is AsyncOpenAI).
            temperature: LLM temperature for planning.
            max_tokens: Maximum response tokens.
        """
        self._shared: SharedLLMClient | None = (
            llm_client if isinstance(llm_client, SharedLLMClient) else None
        )
        self._client: AsyncOpenAI | None = (
            None if self._shared is not None else llm_client  # type: ignore[assignment]
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def plan(
        self,
        user_input: str,
        memory: ConversationMemory,
        available_tools: list[dict[str, Any]] | None = None,
    ) -> TaskPlan:
        """Generate a task plan from the user's input.

        Args:
            user_input: The user's raw request string.
            memory: Current conversation memory for context.
            available_tools: List of tool schemas the planner can use.

        Returns:
            A TaskPlan with structured steps.

        Raises:
            PlanningError: If planning fails or produces invalid output.
        """
        logger.info("Planning for: '%s'", user_input[:100])

        messages: list[dict[str, str]] = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        ]

        # Include recent conversation context (exclude tool messages — DeepSeek
        # requires tool_call_id on tool messages, which we don't track)
        history = memory.get_last_n(6)
        for msg in history:
            if msg["role"] == "tool":
                continue
            messages.append({"role": msg["role"], "content": msg["content"]})  # type: ignore[arg-type]

        # Add available tools info
        if available_tools:
            tools_desc = json.dumps(available_tools, indent=2)
            messages.append({
                "role": "system",
                "content": f"Available tools:\n{tools_desc}",
            })

        messages.append({"role": "user", "content": user_input})

        try:
            if self._shared is not None:
                raw_output = await self._shared.achat(
                    messages,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
            else:
                assert self._client is not None
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
                raw_output = response.choices[0].message.content or ""

            logger.debug("Planner raw output: %s", raw_output[:500])

            plan = self._parse_plan(raw_output, user_input)
            logger.info(
                "Plan generated: goal='%s', steps=%d",
                plan.goal,
                len(plan.steps),
            )
            return plan

        except PlanningError:
            raise
        except Exception as e:
            logger.error("Planning failed: %s", e)
            raise PlanningError(
                f"Failed to generate plan: {e}",
                user_input=user_input,
            ) from e

    def _parse_plan(self, raw_output: str, user_input: str) -> TaskPlan:
        """Parse the LLM's JSON output into a TaskPlan.

        Robust extraction: handles code fences, extra text before/after JSON,
        trailing commas, and common deepseek-v4-flash quirks.

        Args:
            raw_output: Raw text from the LLM response.
            user_input: Original user input (for error context).

        Returns:
            A validated TaskPlan.

        Raises:
            PlanningError: If parsing or validation fails.
        """
        json_str = self._extract_json(raw_output)

        # Try multiple parse strategies
        data = None
        errors = []

        # Strategy 1: direct parse
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            errors.append(f"direct: {e}")

        # Strategy 2: strip trailing commas (common LLM mistake)
        if data is None:
            try:
                cleaned = re.sub(r",\s*([}\]])", r"\1", json_str)
                data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                errors.append(f"trailing-comma: {e}")

        # Strategy 3: regex extract the first complete JSON object
        if data is None:
            try:
                m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', json_str, re.DOTALL)
                if m:
                    data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError) as e:
                errors.append(f"regex: {e}")

        if data is None:
            raise PlanningError(
                f"Failed to parse plan JSON after 3 strategies. "
                f"Errors: {'; '.join(errors)}. Raw: {raw_output[:300]}",
                user_input=user_input,
            )

        if not isinstance(data, dict):
            raise PlanningError(
                f"Plan is not a JSON object: {raw_output[:200]}",
                user_input=user_input,
            )

        if "steps" not in data:
            raise PlanningError(
                "Plan is missing 'steps' field.",
                user_input=user_input,
            )

        if not data.get("goal"):
            data["goal"] = user_input

        try:
            plan = TaskPlan.from_dict(data)
        except Exception as e:
            raise PlanningError(
                f"Invalid plan structure: {e}",
                user_input=user_input,
            ) from e

        # Validate step IDs
        step_ids = {s.id for s in plan.steps}
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    raise PlanningError(
                        f"Step {step.id} depends on non-existent step {dep}.",
                        user_input=user_input,
                    )

        if not plan.steps:
            raise PlanningError(
                "Plan contains no steps.",
                user_input=user_input,
            )

        return plan

    @staticmethod
    def _extract_json(raw_output: str) -> str:
        """Extract the JSON block from LLM output, handling common formats."""
        text = raw_output.strip()

        # Remove markdown code fences
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        # If output starts with {, it's likely pure JSON
        if text.startswith("{"):
            return text

        # Try to find the first { and last }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return text[start:end + 1]

        return text
