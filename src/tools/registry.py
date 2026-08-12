"""Tool registry — singleton-based plugin system for tool management."""

import importlib
import logging

from src.tools.base import BaseTool
from src.utils.errors import ToolNotFoundError

logger = logging.getLogger("agent")


class ToolRegistry:
    """Registry for managing tool plugins.

    Supports registration, lookup, deregistration, and runtime dynamic imports.
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance.

        Args:
            tool: A BaseTool instance to register.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        if tool.name in self._tools:
            logger.warning(
                "Tool '%s' is already registered. Overwriting.", tool.name
            )
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def register_many(self, tools: list[BaseTool]) -> None:
        """Register multiple tools at once.

        Args:
            tools: List of BaseTool instances.
        """
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry by name.

        Args:
            name: The tool name to remove.

        Raises:
            ToolNotFoundError: If the tool is not registered.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' not found in registry.", tool_name=name)
        del self._tools[name]
        logger.info("Unregistered tool: %s", name)

    def get(self, name: str) -> BaseTool:
        """Retrieve a registered tool by name.

        Args:
            name: The tool name.

        Returns:
            The BaseTool instance.

        Raises:
            ToolNotFoundError: If the tool is not registered.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' not found in registry.", tool_name=name)
        return self._tools[name]

    def list_all(self) -> list[BaseTool]:
        """Return all registered tools.

        Returns:
            A list of all BaseTool instances currently registered.
        """
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        """Return the names of all registered tools.

        Returns:
            A list of tool name strings.
        """
        return list(self._tools.keys())

    def get_openai_functions(self) -> list[dict]:
        """Get all registered tools in OpenAI function-calling format.

        Returns:
            A list of dicts suitable for the OpenAI `tools` parameter.
        """
        return [tool.to_openai_function() for tool in self._tools.values()]

    def has(self, name: str) -> bool:
        """Check if a tool is registered.

        Args:
            name: Tool name to check.

        Returns:
            True if the tool is registered.
        """
        return name in self._tools

    def load_plugin(self, module_path: str, class_name: str) -> None:
        """Dynamically import and register a tool plugin from an external module.

        Args:
            module_path: Dotted Python module path (e.g. 'my_package.my_tool').
            class_name: Name of the BaseTool subclass to instantiate.

        Raises:
            ToolNotFoundError: If the module or class cannot be loaded.
        """
        try:
            module = importlib.import_module(module_path)
            tool_cls = getattr(module, class_name)
            tool_instance = tool_cls()
            if not isinstance(tool_instance, BaseTool):
                raise TypeError(f"{class_name} is not a subclass of BaseTool.")
            self.register(tool_instance)
            logger.info("Loaded plugin tool '%s' from %s.%s", tool_instance.name, module_path, class_name)
        except (ImportError, AttributeError) as e:
            raise ToolNotFoundError(
                f"Failed to load plugin '{module_path}.{class_name}': {e}",
                tool_name=f"{module_path}.{class_name}",
            ) from e

    def clear(self) -> None:
        """Remove all registered tools."""
        self._tools.clear()
        logger.info("Cleared all registered tools.")
