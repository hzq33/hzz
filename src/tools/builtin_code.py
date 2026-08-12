"""Built-in code execution tool — isolated subprocess sandbox.

Runs user Python in a child process (``python -I``) so timeouts can kill the
process, stdout/stderr stay process-local, and the parent interpreter is not
mutated. Source-level import filters remain as defense in depth.

Optional Unix resource limits (CPU seconds / address space) via env — see
``EXECUTE_CODE_TIMEOUT``, ``EXECUTE_CODE_CPU_SECONDS``, ``EXECUTE_CODE_MAX_MEM_MB``.
Default remains disabled at the registry (``EXECUTE_CODE_ENABLED``).
"""

from __future__ import annotations

import asyncio
import ast
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")

_DANGEROUS_BUILTINS: set[str] = {
    "__import__",
    "open",
    "compile",
    "eval",
    "exec",
    "input",
    "breakpoint",
    "memoryview",
    "globals",
    "locals",
    "vars",
}

_DANGEROUS_IMPORT_ROOTS = (
    "os",
    "subprocess",
    "sys",
    "shutil",
    "socket",
    "ctypes",
    "pathlib",
    "importlib",
    "builtins",
    "pty",
    "signal",
    "multiprocessing",
    "threading",
    "resource",
    "fcntl",
    "mmap",
)


def _timeout_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("EXECUTE_CODE_TIMEOUT", "30")))
    except ValueError:
        return 30.0


def _cpu_seconds() -> int:
    try:
        return max(1, int(os.getenv("EXECUTE_CODE_CPU_SECONDS", "20")))
    except ValueError:
        return 20


def _max_mem_bytes() -> int:
    try:
        mb = int(os.getenv("EXECUTE_CODE_MAX_MEM_MB", "256"))
    except ValueError:
        mb = 256
    return max(64, mb) * 1024 * 1024


def _child_runner_source() -> str:
    """Python source executed inside the isolated child process."""
    cpu = _cpu_seconds()
    mem = _max_mem_bytes()
    return (
        "import builtins as _b\n"
        "import sys\n"
        "try:\n"
        "    import resource as _resource\n"
        "    if sys.platform != 'win32':\n"
        f"        _resource.setrlimit(_resource.RLIMIT_CPU, ({cpu}, {cpu}))\n"
        "        try:\n"
        f"            _resource.setrlimit(_resource.RLIMIT_AS, ({mem}, {mem}))\n"
        "        except (ValueError, OSError):\n"
        "            pass\n"
        "except Exception:\n"
        "    pass\n"
        f"_BLOCK = {repr(_DANGEROUS_BUILTINS)}\n"
        "_safe = {n: getattr(_b, n) for n in dir(_b) if n not in _BLOCK}\n"
        "_path = sys.argv[1]\n"
        '_ns = {"__name__": "__main__", "__builtins__": _safe}\n'
        "try:\n"
        '    with open(_path, "r", encoding="utf-8") as _f:\n'
        "        _src = _f.read()\n"
        '    exec(compile(_src, "<sandbox>", "exec"), _ns, _ns)\n'
        "except SystemExit:\n"
        "    raise\n"
        "except BaseException:\n"
        "    import traceback\n"
        "    traceback.print_exc()\n"
        "    raise SystemExit(1)\n"
    )


def _sanitize_env() -> dict[str, str]:
    """Minimal env for the child — drop API keys and large PYTHONPATH."""
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in keep or upper.startswith("PATHEXT"):
            env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _blocked_import(code: str) -> str | None:
    """AST 级 import 过滤：提取所有 Import/ImportFrom 根模块并查黑名单。

    相比旧子串匹配（"import os"），AST 解析天然忽略空白变体，
    可拦截 ``import  os``/``import\tos``/``from    os`` 等绕过写法。
    注意：这只是纵深防御——``__import__`` 内省链、``().__class__`` 继承链
    等仍可构造逃逸，真正的主防线是默认禁用 + HITL + 子进程隔离。
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        # 语法错误交给子进程正常报错，不阻断
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = (alias.name or "").split(".")[0]
                if root in _DANGEROUS_IMPORT_ROOTS:
                    return alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                return f"relative-import (level={node.level})"
            module = node.module or ""
            root = module.split(".")[0]
            if root in _DANGEROUS_IMPORT_ROOTS:
                return module
    return None


class ExecuteCodeTool(BaseTool):
    """Execute Python code in an isolated subprocess sandbox."""

    name: str = "execute_code"
    description: str = (
        "在独立子进程沙箱中执行 Python 代码。返回 stdout，超时可强杀进程。"
        "常见危险导入（os/sys/subprocess 等）已做 AST 过滤；但沙箱不构成强隔离"
        "（恶意代码仍可能构造逃逸），请仅在可信场景使用，高风险操作需人工审批。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
            "language": {
                "type": "string",
                "description": "编程语言，目前仅支持 'python'",
                "default": "python",
                "enum": ["python"],
            },
        },
        "required": ["code"],
    }

    _MAX_CODE_BYTES: int = 64_000
    _MAX_OUTPUT_CHARS: int = 32_000
    # Tests may monkey-patch this on the instance; default follows env.
    _TIMEOUT_SECONDS: float = 30.0

    def __init__(self) -> None:
        self._TIMEOUT_SECONDS = _timeout_seconds()

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            self.validate_args(kwargs)
            code: str = kwargs["code"]
            language: str = kwargs.get("language", "python")

            if language != "python":
                return ToolResult.fail(
                    f"Unsupported language: {language}. Only 'python' is supported."
                )
            if len(code.encode("utf-8", errors="replace")) > self._MAX_CODE_BYTES:
                return ToolResult.fail(
                    f"Code exceeds maximum size of {self._MAX_CODE_BYTES} bytes."
                )

            blocked = _blocked_import(code)
            if blocked:
                return ToolResult.fail(
                    f"Import of '{blocked}' is not allowed in the sandbox "
                    "for security reasons."
                )

            logger.info("Executing code in subprocess (%d chars)", len(code))
            return await self._run_subprocess(code)

        except ValueError as e:
            logger.error("Code execution validation error: %s", e)
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.error("Code execution tool error: %s", e)
            from src.utils.errors import ToolExecutionError

            raise ToolExecutionError(
                f"Code execution tool failed: {e}",
                tool_name=self.name,
                original_error=str(e),
            ) from e

    async def _run_subprocess(self, code: str) -> ToolResult:
        tmp_dir = tempfile.mkdtemp(prefix="agent_code_")
        code_path = Path(tmp_dir) / "user_code.py"
        runner_path = Path(tmp_dir) / "_runner.py"
        timeout = self._TIMEOUT_SECONDS
        try:
            code_path.write_text(code, encoding="utf-8")
            runner_path.write_text(_child_runner_source(), encoding="utf-8")

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(runner_path),
                str(code_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp_dir,
                env=_sanitize_env(),
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=float(timeout),
                )
            except TimeoutError:
                await self._kill(proc)
                logger.warning("Code execution timed out after %ss", timeout)
                return ToolResult.fail(
                    f"Code execution timed out after {timeout} seconds."
                )

            stdout = (stdout_b or b"").decode("utf-8", errors="replace")
            stderr = (stderr_b or b"").decode("utf-8", errors="replace")
            if len(stdout) > self._MAX_OUTPUT_CHARS:
                stdout = stdout[: self._MAX_OUTPUT_CHARS] + "\n...[truncated]"
            if len(stderr) > self._MAX_OUTPUT_CHARS:
                stderr = stderr[: self._MAX_OUTPUT_CHARS] + "\n...[truncated]"

            if proc.returncode not in (0, None):
                detail = stderr.strip() or stdout.strip() or f"exit code {proc.returncode}"
                return ToolResult.fail(f"Error during execution:\n{detail}")

            output = stdout
            if not output.strip():
                output = "(code executed successfully with no output)"
            return ToolResult.ok(output)
        finally:
            for path in (code_path, runner_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                Path(tmp_dir).rmdir()
            except OSError:
                pass

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (TimeoutError, ProcessLookupError):
            pass
