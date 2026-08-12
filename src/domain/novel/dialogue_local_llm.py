"""Local LLM dialogue extraction via subprocess (venv_qwen).

Uses a separate Python venv with transformers 4.36.2 + torch 2.1.2+cu121
to work around Qwen-1.8B incompatibility with transformers > 4.50.

The subprocess loads the model once (persistent across calls via stdin JSON).
Architecture: main venv (fastapi/lancedb) → subprocess (Qwen-1.8B 4-bit)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from src.domain.novel.models import DialogueTurn

logger = logging.getLogger("agent")

# Paths
_ROOT = Path(__file__).parent.parent.parent.parent  # D:\tools\agent
_VENV_QWEN = _ROOT / "venv_qwen"
_SUBPROCESS_SCRIPT = _ROOT / "src" / "domain" / "novel" / "extract_dialogue_subprocess.py"


class LocalLLMDialogueExtractor:
    """Extract dialogue speakers via subprocess calling Qwen-1.8B 4-bit in venv_qwen.

    Default mode ``official`` aligns with Chat-Haruhi notebook:
    ~600-token chunks + previous-summary chaining.
    """

    MAX_TEXT_LENGTH = 8000  # official mode chunks internally; soft upper bound
    MIN_TEXT_LENGTH = 20    # short attribution windows may be tiny

    def __init__(
        self,
        model_path: str = "models/Haruhi-Dialogue-Speaker-Extract_qwen18",
        device: str = "cuda",
        quantize: str = "4bit",
        mode: str = "official",
        token_per_chunk: int = 600,
    ):
        self._model_path = model_path
        self._device = device
        self._quantize = quantize
        self._mode = mode  # "official" | "single"
        self._token_per_chunk = token_per_chunk
        self._loaded = False
        self._last_meta: dict = {}

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def vram_mb(self) -> int:
        return 1100  # 4-bit estimate

    def load(self):
        """Verify subprocess venv exists. Actual model loads on first call."""
        if not _VENV_QWEN.exists():
            raise RuntimeError(f"venv_qwen not found at {_VENV_QWEN}")
        if not _SUBPROCESS_SCRIPT.exists():
            raise RuntimeError(f"Subprocess script not found at {_SUBPROCESS_SCRIPT}")
        self._loaded = True
        logger.info("LocalLLM subprocess ready: venv=%s", _VENV_QWEN)

    def unload(self):
        """No-op — subprocess manages its own lifecycle."""
        self._loaded = False

    async def extract_batch(
        self,
        chapter_text: str,
        chapter_title: str = "",
        mode: str | None = None,
    ) -> list[DialogueTurn]:
        """Extract dialogue turns via subprocess call."""
        text = chapter_text.strip()
        if len(text) < self.MIN_TEXT_LENGTH:
            return []
        if len(text) > self.MAX_TEXT_LENGTH:
            text = text[: self.MAX_TEXT_LENGTH] + "..."

        use_mode = mode or self._mode
        t0 = time.perf_counter()
        try:
            result = await self._call_subprocess(text, mode=use_mode)
            self._last_meta = {
                "mode": result.get("mode"),
                "chunks": result.get("chunks"),
                "chunk_meta": result.get("chunk_meta"),
                "load_s": result.get("load_s"),
                "gen_s": result.get("gen_s"),
            }
            turns = [
                DialogueTurn(turn=i + 1, speaker=item["speaker"], content=item["content"])
                for i, item in enumerate(result.get("turns", []))
            ]
            elapsed = int((time.perf_counter() - t0) * 1000)
            load_s = result.get("load_s", 0)
            gen_s = result.get("gen_s", 0)
            logger.info(
                "LocalLLM extract[%s]: chapter='%s', turns=%d, chunks=%s, "
                "elapsed=%dms (load=%.1fs, gen=%.1fs)",
                use_mode,
                chapter_title or "?",
                len(turns),
                result.get("chunks"),
                elapsed,
                load_s,
                gen_s,
            )
            return turns
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "LocalLLM subprocess failed for '%s' after %dms: %s",
                chapter_title or "?",
                elapsed,
                e,
            )
            return []

    async def enhance(
        self,
        regex_turns: list[DialogueTurn],
        chapter_text: str,
        chapter_title: str = "",
        unknown_threshold: float = 0.3,
    ) -> list[DialogueTurn]:
        """Enhance regex turns with LLM speaker identification.

        Triggers when unknown/noise speaker rate exceeds ``unknown_threshold``.
        Overwrites 未知 and noise names; keeps reliable regex speakers.
        """
        if not regex_turns:
            return regex_turns
        # threshold < 0 → force enhance (trigger_mode=always)
        if unknown_threshold >= 0 and not needs_speaker_enhance(
            regex_turns, unknown_threshold
        ):
            return regex_turns

        bad = sum(1 for t in regex_turns if is_noise_speaker(t.speaker))
        logger.info(
            "LocalLLM enhance triggered: chapter='%s', noise_rate=%.1f%% (%d/%d)",
            chapter_title or "?",
            100.0 * bad / max(len(regex_turns), 1),
            bad,
            len(regex_turns),
        )

        llm_turns = await self.extract_batch(chapter_text, chapter_title)
        if not llm_turns:
            return regex_turns

        return _merge_turns(regex_turns, llm_turns)

    async def _call_subprocess(self, text: str, mode: str | None = None) -> dict:
        """Call the subprocess with JSON input via temp file, return parsed output."""
        import os
        import subprocess as sp
        import tempfile

        use_mode = mode or self._mode
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            json.dump(
                {
                    "text": text,
                    "model_path": str(_ROOT / self._model_path),
                    "mode": use_mode,
                    "token_per_chunk": self._token_per_chunk,
                },
                tmp,
                ensure_ascii=False,
            )
            tmp.close()

            # Build clean env: strip PYTHONPATH so subprocess uses ONLY its own venv
            clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            clean_env["PYTHONIOENCODING"] = "utf-8"
            clean_env["PYTHONUTF8"] = "1"
            # official multi-chunk needs more wall time (≈10–20s × N chunks)
            timeout = 600 if use_mode == "official" else 180
            result = sp.run(
                [
                    str(_VENV_QWEN / "Scripts" / "python.exe"),
                    "-s",  # disable user site-packages
                    "-X",
                    "utf8",
                    str(_SUBPROCESS_SCRIPT),
                    tmp.name,
                ],
                capture_output=True,
                timeout=timeout,
                env=clean_env,
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[:1000]
            out = result.stdout.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"Subprocess exit={result.returncode}: stderr={err}, stdout={out}"
            )

        raw_out = result.stdout.decode("utf-8", errors="replace")
        if not raw_out.strip():
            # Windows consoles sometimes emit GBK diagnostics on stderr only
            err = result.stderr.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Empty subprocess stdout; stderr={err}")
        try:
            return json.loads(raw_out)
        except json.JSONDecodeError:
            # Retry GBK if model/tools printed non-UTF8 noise before JSON
            try:
                raw_gbk = result.stdout.decode("gbk", errors="replace")
                # Prefer last JSON object in stream
                brace = raw_gbk.rfind("{")
                if brace >= 0:
                    return json.loads(raw_gbk[brace:])
            except Exception:
                pass
            raise RuntimeError(f"Invalid subprocess output: {raw_out[:200]}")


_NOISE_SPEAKERS = {
    "未知", "他", "她", "它", "我", "你", "他们", "她们", "众人", "大家",
    "三人", "两人", "四人", "转身", "轻小", "她要", "隔壁", "少年", "少女",
    "男人", "女人", "那人", "此人", "有人", "谁", "？", "!", "！",
}


def is_noise_speaker(name: str) -> bool:
    """True if regex speaker should be treated as unreliable."""
    try:
        from src.domain.novel.dialogue_span import is_noise_speaker as _span_noise
        return _span_noise(name)
    except Exception:
        s = (name or "").strip()
        if not s or s in _NOISE_SPEAKERS:
            return True
        if len(s) == 1:
            return True
        if len(s) <= 2 and s[-1] in "说道路笑走看叹问喊":
            return True
        return False


def needs_speaker_enhance(turns: list[DialogueTurn], threshold: float = 0.3) -> bool:
    """Trigger Qwen when unknown/noise rate exceeds threshold."""
    if not turns:
        return False
    bad = sum(1 for t in turns if is_noise_speaker(t.speaker))
    return (bad / len(turns)) > threshold


def _merge_turns(
    regex_turns: list[DialogueTurn],
    llm_turns: list[DialogueTurn],
) -> list[DialogueTurn]:
    """Merge regex turns with LLM turns.

    Rules (impersonation-first):
    1. Reliable regex speaker → keep AS-IS
    2. 未知 / noise speaker → overwrite from best LLM content match
    3. NEVER append LLM-only hallucinated turns
    """
    result = []

    for rt in regex_turns:
        if not is_noise_speaker(rt.speaker):
            result.append(rt)
            continue

        best = _best_llm_match(rt.content, llm_turns)
        if best and not is_noise_speaker(best.speaker):
            result.append(DialogueTurn(
                turn=rt.turn,
                speaker=best.speaker,
                content=rt.content,  # regex content is more accurate
                mood=best.mood or rt.mood,
            ))
        elif best and best.speaker != "未知":
            result.append(DialogueTurn(
                turn=rt.turn,
                speaker=best.speaker,
                content=rt.content,
                mood=best.mood or rt.mood,
            ))
        else:
            result.append(rt)

    return result


def _best_llm_match(
    regex_content: str,
    llm_turns: list[DialogueTurn],
) -> DialogueTurn | None:
    """Find the best LLM turn matching a regex content string.

    Tries in order:
    1. Exact content match
    2. Substring match (regex content is a substring of LLM content or vice versa)
    3. Position-based: if regex content length > 10, skip (likely hallucination)
    """
    if not llm_turns:
        return None

    # Strategy 1: exact match
    for lt in llm_turns:
        if lt.content.strip() == regex_content.strip():
            return lt

    # Strategy 2: substring overlap > 50%
    for lt in llm_turns:
        shorter = min(regex_content, lt.content, key=len)
        longer = regex_content if len(regex_content) > len(lt.content) else lt.content
        if len(shorter) >= 3 and shorter in longer:
            return lt

    # Strategy 3: character overlap ratio > 0.5
    rc = set(regex_content)
    for lt in llm_turns:
        lc = set(lt.content)
        if rc and lc:
            overlap = len(rc & lc) / max(len(rc), len(lc))
            if overlap > 0.5:
                return lt

    return None
