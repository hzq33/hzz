"""Structured logging setup for the Agent Server.

Writes to stdout + rotating file (data/logs/server.log by default).

分层设计（对齐 structlog 双渲染思路）：
- **控制台（stdout）**：人类可读——短时间戳 + 按级别着色 + 精简模块名；仅 TTY 时着色
- **文件（server.log）**：完整时间戳 + 无颜色 + session_id/request_id 字段（供 grep 关联排障）
- **JSON 模式**（AGENT_LOG_FORMAT=json）：结构化输出，含 request_id/session_id，供日志聚合

Env vars: AGENT_LOG_LEVEL, AGENT_LOG_FORMAT, AGENT_LOG_DIR, AGENT_LOG_MAX_BYTES.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.shared.request_context import get_request_id, get_session_id

_LOG_DIR = Path(os.getenv("AGENT_LOG_DIR", "data/logs"))
_LOG_MAX_BYTES = int(os.getenv("AGENT_LOG_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB
_LOG_BACKUPS = int(os.getenv("AGENT_LOG_BACKUPS", "5"))

# ── 控制台着色（仅 stdout；ANSI，Windows 10+ 终端 / git-bash 均支持）──
_LEVEL_COLORS = {
    "DEBUG": "\033[90m",        # 灰
    "INFO": "\033[36m",         # 青
    "WARNING": "\033[33m",      # 黄
    "ERROR": "\033[31m",        # 红
    "CRITICAL": "\033[1;31m",   # 亮红
}
_RESET = "\033[0m"


class JsonLogFormatter(logging.Formatter):
    """Emit one JSON object per log record (for log aggregators)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        session_id = get_session_id()
        if session_id:
            payload["session_id"] = session_id
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ContextFilter(logging.Filter):
    """Attach request_id / session_id to record for text formatters."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"  # type: ignore[attr-defined]
        record.session_id = get_session_id() or "-"  # type: ignore[attr-defined]
        return True


class ColoredConsoleFormatter(logging.Formatter):
    """Console formatter: short timestamp + level coloring (TTY only)."""

    def __init__(self, fmt: str, datefmt: str, *, use_color: bool) -> None:
        super().__init__(fmt, datefmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        if self._use_color:
            color = _LEVEL_COLORS.get(record.levelname, "")
            if color:
                record.levelname = f"{color}{record.levelname:<7}{_RESET}"
        return super().format(record)


def _console_formatter(use_json: bool) -> logging.Formatter:
    """Stdout：短时间 + 着色（TTY 时），保持简洁。"""
    if use_json:
        return JsonLogFormatter()
    use_color = bool(sys.stdout.isatty())
    return ColoredConsoleFormatter(
        "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
        "%H:%M:%S",
        use_color=use_color,
    )


def _file_formatter(use_json: bool) -> logging.Formatter:
    """文件：完整时间戳 + request_id/session_id（供 grep 关联），永不染色。"""
    if use_json:
        return JsonLogFormatter()
    return logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s | %(message)s"
        " [req=%(request_id)s session=%(session_id)s]",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _make_handler(
    stream_or_file: Any,
    use_json: bool,
    *,
    add_context: bool = True,
    is_console: bool = False,
) -> logging.Handler:
    handler: logging.Handler
    if isinstance(stream_or_file, (str, Path)):
        path = Path(stream_or_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            str(path),
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUPS,
            encoding="utf-8",
        )
    else:
        handler = logging.StreamHandler(stream_or_file)
    if add_context:
        handler.addFilter(ContextFilter())
    if use_json:
        handler.setFormatter(JsonLogFormatter())
    elif is_console:
        handler.setFormatter(_console_formatter(False))
    else:
        handler.setFormatter(_file_formatter(False))
    return handler


def configure_logging(*, force: bool = False) -> None:
    """Configure root logging once.

    AGENT_LOG_FORMAT=json|text (default text).
    AGENT_LOG_DIR (default data/logs) — set empty to disable file logging.
    """
    root = logging.getLogger()
    if root.handlers and not force:
        return

    level_name = os.getenv("AGENT_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    use_json = (
        os.getenv("AGENT_LOG_FORMAT", "").lower() == "json"
        or os.getenv("AGENT_LOG_JSON", "").lower() in {"1", "true", "yes"}
    )

    # 抑制模型加载进度条刷屏（tqdm 直接写 stderr，不走 logging）
    os.environ.setdefault("TQDM_DISABLE", "1")

    root.handlers.clear()
    root.setLevel(level)

    # Stdout (always, colored + short when text)
    root.addHandler(_make_handler(sys.stdout, use_json, is_console=True))

    # Rotating file (full context fields, never colored)
    log_dir = os.getenv("AGENT_LOG_DIR", "data/logs")
    if log_dir:
        file_handler = _make_handler(
            Path(log_dir) / "server.log",
            use_json,
            add_context=True,
            is_console=False,
        )
        root.addHandler(file_handler)

    # Suppress noisy library loggers
    for noisy in (
        "uvicorn.access",
        "uvicorn.error",
        "httpx",
        "httpcore",
        "openai",
        "lancedb",
        "faiss.loader",          # AVX2 回退警告等启动噪声
        "sentence_transformers", # 模型加载细节
        "transformers",
        "urllib3",
        "requests",
        "opentelemetry",
        "datasets",
        "huggingface_hub",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
