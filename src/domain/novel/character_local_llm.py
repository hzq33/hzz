"""Local LLM character personality/speech-style extraction via subprocess.

Reuses the same venv_qwen subprocess infrastructure as dialogue_local_llm.py.
For each character, sends narrative snippets + dialogue samples to Qwen-1.8B
and parses structured JSON output.

Architecture: main venv → subprocess (venv_qwen / Qwen-1.8B 4-bit)
"""

from __future__ import annotations

import json
import logging
import subprocess as sp
import tempfile
from pathlib import Path

logger = logging.getLogger("agent")

_ROOT = Path(__file__).parent.parent.parent.parent
_VENV_QWEN = _ROOT / "venv_qwen"
_SUBPROCESS_SCRIPT = _ROOT / "src" / "domain" / "novel" / "chat_subprocess.py"

# ── Character analysis prompt (Chinese, tuned for Qwen-1.8B) ──

_CHARACTER_SYSTEM_PROMPT = (
    "你是一个小说角色分析助手。根据给定的角色名和相关叙事片段，分析角色的特征。\n\n"
    "用 JSON 格式回复：\n"
    '{"personality": "性格关键词（2-3个，用顿号分隔）", '
    '"speaking_style": "说话风格描述（简洁）", '
    '"is_character": true/false, '
    '"role_hint": "主角/主要配角/次要配角/路人"}'
)

_CHARACTER_USER_TEMPLATE = "角色名：{name}\n相关叙事片段：\n{snippets}\n对话样本：\n{dialogues}"


class LocalLLMCharacterExtractor:
    """Extract character personality/speech-style via subprocess."""

    def __init__(
        self,
        model_path: str = "models/Haruhi-Dialogue-Speaker-Extract_qwen18",
    ):
        self._model_path = model_path

    async def extract_personality(
        self,
        name: str,
        narrative_snippets: list[str],
        dialogue_contents: list[str],
    ) -> dict:
        """Extract personality traits from narrative + dialogue context.

        Returns: {"personality": str, "speaking_style": str, "is_character": bool}
        """
        snippets_text = "\n".join(s for s in (narrative_snippets or [])[:5] if s)[:500]
        dialogues_text = "\n".join(d for d in (dialogue_contents or [])[:5] if d)[:300]

        if not snippets_text and not dialogues_text:
            return {}

        prompt = _CHARACTER_USER_TEMPLATE.format(
            name=name,
            snippets=snippets_text or "（无）",
            dialogues=dialogues_text or "（无）",
        )

        raw = await self._call(prompt, system=_CHARACTER_SYSTEM_PROMPT)
        if not raw:
            return {}
        return self._parse_character_response(raw)

    async def filter_characters(
        self,
        names: list[str],
    ) -> list[str]:
        """Filter a candidate name list to only real character names."""
        prompt = (
            "以下是从小说中提取的候选角色名列表。请过滤掉不是真实人名的条目（如动词、形容词、数量词等），"
            "只返回真实的小说角色名。用JSON数组回复：\n"
            f"{json.dumps(names[:50], ensure_ascii=False)}"
        )
        raw = await self._call(prompt, system="你是一个文学编辑助手。只返回JSON数组。")
        if not raw:
            return names  # on failure, keep all

        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [n for n in data if isinstance(n, str)]
        except json.JSONDecodeError:
            import re
            m = re.search(r'\[.*?\]', raw, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
        return names

    async def _call(self, prompt: str, system: str = "") -> str | None:
        """Call chat subprocess with prompt, return raw response."""
        import os

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            json.dump(
                {
                    "text": prompt,
                    "system": system,
                    "model_path": str(_ROOT / self._model_path),
                },
                tmp, ensure_ascii=False,
            )
            tmp.close()

            clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            result = sp.run(
                [
                    str(_VENV_QWEN / "Scripts" / "python.exe"),
                    "-s",
                    str(_SUBPROCESS_SCRIPT),
                    tmp.name,
                ],
                capture_output=True,
                timeout=120,
                env=clean_env,
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        if result.returncode != 0:
            logger.warning(
                "Chat subprocess failed: exit=%d, stderr=%s",
                result.returncode,
                result.stderr.decode("utf-8", errors="replace")[:200],
            )
            return None

        try:
            data = json.loads(result.stdout.decode("utf-8"))
            if data.get("error"):
                logger.warning("Chat subprocess error: %s", data["error"])
                return None
            return data.get("response", "")
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _parse_character_response(raw: str) -> dict:
        """Parse the model's JSON response into a character profile dict."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        import re
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return {}
