"""Tool abstractions: base class and result container."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Result of a tool execution.

    Attributes:
        success: Whether the tool ran to completion without error.
        output: The tool's stdout / primary output.
        error: Error message if success is False.
    """

    success: bool
    output: str
    error: str | None = None

    @classmethod
    def ok(cls, output: str) -> "ToolResult":
        """Create a successful result."""
        return cls(success=True, output=output)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        """Create a failed result."""
        return cls(success=False, output="", error=error)


class BaseTool(ABC):
    """Abstract base class for all tools.

    Every tool must define:
        - name: Unique identifier string.
        - description: Human-readable description (shown to the LLM for selection).
        - parameters: JSON Schema describing the tool's input parameters.

    Subclasses must implement `execute(**kwargs) -> ToolResult`.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with the given keyword arguments.

        Args:
            **kwargs: Parameters matching the tool's parameter schema.

        Returns:
            ToolResult indicating success or failure.
        """
        ...

    def to_openai_function(self) -> dict[str, Any]:
        """Convert this tool into the OpenAI function-calling JSON schema.

        Returns:
            A dictionary compatible with OpenAI's ChatCompletion `tools` parameter.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def validate_args(self, kwargs: dict[str, Any]) -> None:
        """Basic validation of tool arguments against the JSON Schema.

        Checks required fields are present. Override for custom validation.

        Args:
            kwargs: The keyword arguments passed to execute().

        Raises:
            ValueError: If a required parameter is missing.
        """
        required = self.parameters.get("required", [])
        for req in required:
            if req not in kwargs:
                raise ValueError(
                    f"Missing required parameter '{req}' for tool '{self.name}'."
                )
