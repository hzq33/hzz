"""Unified logging setup for the agent framework."""

import logging
import sys


def setup_logger(
    name: str = "agent",
    level: str = "INFO",
    log_file: str | None = None,
) -> logging.Logger:
    """Initialize and configure a unified logger with console and optional file output.

    Args:
        name: Logger name. Defaults to "agent".
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_file: Optional file path for log output. If None, only console logging.

    Returns:
        A configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    # Prevent duplicate handlers if logger was already configured
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
