"""Custom exception hierarchy for the agent framework."""


class AgentError(Exception):
    """Base exception for all agent framework errors."""

    def __init__(self, message: str, *args: object) -> None:
        super().__init__(message, *args)
        self.message = message


class ConfigurationError(AgentError):
    """Raised when configuration is invalid or missing required fields."""

    def __init__(self, message: str, missing_key: str = "") -> None:
        super().__init__(message)
        self.missing_key = missing_key


class ToolNotFoundError(AgentError):
    """Raised when a requested tool is not found in the registry."""

    def __init__(self, message: str, tool_name: str = "") -> None:
        super().__init__(message)
        self.tool_name = tool_name


class ToolExecutionError(AgentError):
    """Raised when a tool fails during execution."""

    def __init__(
        self,
        message: str,
        tool_name: str = "",
        original_error: str = "",
    ) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.original_error = original_error


class PlanningError(AgentError):
    """Raised when task planning fails."""

    def __init__(self, message: str, user_input: str = "") -> None:
        super().__init__(message)
        self.user_input = user_input


class ExecutionError(AgentError):
    """Raised when task execution fails after exhausting retries."""

    def __init__(
        self,
        message: str,
        step_id: int = -1,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.step_id = step_id
        self.retry_count = retry_count


class MemoryError(AgentError):
    """Raised when memory operations fail."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
