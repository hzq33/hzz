#!/usr/bin/env python3
"""CLI entry point for the Modular Agent Framework.

Supports single-query mode and interactive REPL mode.

Usage:
    python main.py --config config.yaml --interactive
    python main.py --config config.yaml --query "What is Python?"
    python main.py --config config.yaml -q "Search for AI news" -i
"""

import argparse
import asyncio
import io
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Fix encoding for Windows terminals (gbk -> utf-8)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

from src.core.agent import Agent
from src.tools.bootstrap import register_builtin_tools
from src.tools.registry import ToolRegistry
from src.utils.config import AgentConfig, load_config, validate_config
from src.utils.logger import setup_logger



def _load_plugins(registry: ToolRegistry, config: AgentConfig) -> None:
    """Load external tool plugins from configuration.

    Each plugin entry should be in the format 'module.path:ClassName'.

    Args:
        registry: The ToolRegistry instance.
        config: The AgentConfig with plugin settings.
    """
    logger = logging.getLogger("agent")
    for plugin_spec in config.tools.plugins:
        try:
            if ":" in plugin_spec:
                module_path, class_name = plugin_spec.split(":", 1)
                registry.load_plugin(module_path.strip(), class_name.strip())
            else:
                logger.warning("Invalid plugin spec '%s'. Expected 'module:Class'.", plugin_spec)
        except Exception as e:
            logger.error("Failed to load plugin '%s': %s", plugin_spec, e)


async def _run_query(agent: Agent, query: str) -> None:
    """Run a single query and print the result.

    Args:
        agent: The initialized Agent.
        query: The user's query string.
    """
    try:
        response = await agent.run(query)
        print(f"\n{response}\n")
    except Exception as e:
        logging.getLogger("agent").exception("Query failed")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


async def main() -> None:
    """Parse CLI arguments and start the agent."""
    # Load .env file if present (handle encoding issues gracefully)
    try:
        load_dotenv(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            load_dotenv(encoding="utf-16")
        except Exception:
            print(
                "Warning: Failed to load .env file (encoding issue). "
                "Ensure it is saved as UTF-8.",
                file=sys.stderr,
            )

    parser = argparse.ArgumentParser(
        description="Modular Agent Framework — CLI Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --config config.yaml --interactive
  python main.py --config config.yaml -q "What is machine learning?"
  python main.py --config config.yaml -q "Search for Python tutorials" -i
        """,
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml).",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Start in interactive REPL mode.",
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default=None,
        help="Single query to execute (non-interactive mode).",
    )

    args = parser.parse_args()

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Configuration file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    config = load_config(str(config_path))
    validate_config(config)

    # Setup logger
    setup_logger(
        name="agent",
        level=config.logging.level,
        log_file=config.logging.file,
    )
    logger = logging.getLogger("agent")
    logger.info("Starting Modular Agent Framework")

    # Determine working directory
    working_dir = str(config_path.parent.absolute())

    # Create agent
    agent = Agent(config)

    # Register built-in tools (single entry point)
    register_builtin_tools(agent.tool_registry, config, working_dir=working_dir)

    # Load plugins
    _load_plugins(agent.tool_registry, config)

    logger.info(
        "Agent ready: %d tools registered.",
        len(agent.tool_registry.list_all()),
    )

    try:
        if args.interactive:
            # Interactive mode (with optional initial query)
            if args.query:
                await _run_query(agent, args.query)
            await agent.run_interactive()
        elif args.query:
            # Single query mode
            await _run_query(agent, args.query)
        else:
            # Default: interactive mode
            await agent.run_interactive()
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
