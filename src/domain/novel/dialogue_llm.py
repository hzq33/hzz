"""LLM-powered dialogue extraction for Chinese novel text.

Primary path (chapter_first): extract spoken lines + speakers from a full
text window (see DIALOGUE_CHAPTER_EXTRACT_DESIGN.md).

Legacy path: older JSON-array prompt still available via extract_batch().
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from typing import Any

from src.domain.novel.models import BLOCK_DIALOGUE, DialogueTurn, NovelBlock
from src.shared.defaults import DEFAULT_DEEPSEEK_MODEL

logger = logging.getLogger("agent")

# ── Legacy prompt (regex-fallback era) ─────────────────────

_SYSTEM_PROMPT = """你是一个中文小说对话提取器。从给定的小说文本中提取所有对话。

规则：
1. 区分"说出口的对话"和"心理活动/内心独白"。标记 type 为 "spoken" 或 "thought"。
2. 如果说话人明确写出（如 林晚晴："..."、林晚晴道："..."、"..."林晚晴说），提取角色名。
   如果说话人不明确（只有引号没有归属），标记 speaker 为 "未知"。
3. 对话内容保留原文，不要改写、不要缩写、不要合并。
4. 如果原文包含情绪描述（冷冷地说、笑道、怒声、低声等），提取到 mood 字段（冷傲/轻松/愤怒/低沉/犹豫/严肃）。
5. 按对话在原文中出现的顺序排列。

只输出 JSON 数组，不要任何其他文字。每个元素格式：
{"speaker": "角色名", "content": "对话内容", "mood": "情绪或空字符串", "type": "spoken"}"""

_USER_PROMPT_TEMPLATE = """章节：{chapter_title}

小说片段：
{text}

请提取以上文本中的所有对话。只输出 JSON 数组。"""

# ── Chapter-first extract + attribute ──────────────────────

_CHAPTER_SYSTEM = """你是中文轻小说对话抽取+说话人识别器。
任务：从完整原文片段中提取所有真正的角色对话，并标注说话人。

规则：
1. 只提取「」『』“”内真正的台词；不要把拟声描写、叙述性引号当对话（例如「哈」地一声打着哈气 不是台词）。
2. 空台词「————」若只是沉默占位可跳过。
3. speaker 必须是该句引号台词的发出者（说话的人），不是被呼叫的对象。
   - 错误示例：content 为「维鲁多拉大人，您打算做什么」时，speaker 不能是「维鲁多拉」。
   - 正确：speaker 应是发出这句话的角色；无法判断写「未知」。
4. 不要把无引号的旁白/内心独白（如吾/我的叙述）抽成对话。
5. 说话人优先用文中出现的角色真名；前文只用「少女」「少年」等泛称时，若同片段后文明确其真名，可回溯标注真名。
6. 【角色全名映射表】列出了别名→全名的对应关系。speaker 必须使用表中的全名（不要用别名、简称）。
   如果候选说话人/文中出现的名字是别名，请查映射表找到对应的全名输出。
7. 优先从【候选说话人】中选择；文中出现且不在名单中的真名也可使用；无法判断写「未知」。
8. 不要臆造未出现角色；不要改写 content。
9. 严格按原文顺序。
10. 只输出 JSON：
{"dialogues":[{"speaker":"...","content":"...","confidence":0.0}]}
"""


class LLMDialogueExtractor:
    """LLM-powered dialogue extraction.

    - ``extract_window``: chapter-first path (object JSON + speakers).
    - ``extract_batch``: legacy array JSON (kept for callers / fallback).
    """

    MAX_TEXT_LENGTH = 8000  # soft cap; pipeline should chunk before calling
    MIN_TEXT_LENGTH = 20

    def __init__(
        self,
        llm_client,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ):
        self._llm = llm_client
        self._max_tokens = max(512, int(max_tokens))
        self._temperature = float(temperature)
        self.api_calls = 0
        self.truncated_responses = 0  # finish_reason=length / unclosed-JSON outputs

    async def extract_window(
        self,
        text: str,
        *,
        chapter_title: str = "",
        candidates: Sequence[str] | None = None,
        max_tokens: int | None = None,
        alias_map_text: str = "",
    ) -> list[dict[str, Any]]:
        """Extract dialogues+speakers from one text window.

        Returns list of dicts: speaker / content / confidence.
        """
        text = (text or "").strip()
        if len(text) < self.MIN_TEXT_LENGTH:
            return []
        if len(text) > self.MAX_TEXT_LENGTH:
            text = text[: self.MAX_TEXT_LENGTH]

        cands = [c for c in (candidates or []) if c and str(c).strip()]
        cand_line = "、".join(cands[:20]) if cands else "（无预置，请从文中识别）"
        user = _build_window_user(chapter_title, cand_line, alias_map_text, text)

        t0 = time.perf_counter()
        try:
            raw, finish = await self._achat_result(
                _CHAPTER_SYSTEM, user, max_tokens=max_tokens
            )
            self.api_calls += 1
            turns = _parse_dialogues_object(raw or "")
            truncated = finish == "length" or _looks_truncated(raw)
            if truncated:
                self.truncated_responses += 1
                logger.warning(
                    "LLM extract truncated for '%s' (finish=%s); retrying half window",
                    chapter_title or "?",
                    finish or "unclosed-json",
                )
                half = text[: len(text) // 2]
                if len(half) >= self.MIN_TEXT_LENGTH:
                    user2 = _build_window_user(chapter_title, cand_line, alias_map_text, half)
                    raw2, finish2 = await self._achat_result(
                        _CHAPTER_SYSTEM, user2, max_tokens=max_tokens
                    )
                    self.api_calls += 1
                    turns2 = _parse_dialogues_object(raw2 or "")
                    if finish2 == "length" or _looks_truncated(raw2):
                        self.truncated_responses += 1
                    merged = _merge_turn_dicts(turns, turns2)
                    elapsed = int((time.perf_counter() - t0) * 1000)
                    logger.info(
                        "LLM chapter extract (half retry): title='%s', turns=%d "
                        "(first=%d + half=%d), elapsed=%dms",
                        chapter_title or "?",
                        len(merged),
                        len(turns),
                        len(turns2),
                        elapsed,
                    )
                    return merged
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "LLM chapter extract: title='%s', turns=%d, elapsed=%dms",
                chapter_title or "?",
                len(turns),
                elapsed,
            )
            return turns
        except Exception as e:
            self.api_calls += 1
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "LLM chapter extract failed for '%s' after %dms: %s",
                chapter_title or "?",
                elapsed,
                e,
            )
            return []

    async def extract_batch(
        self,
        chapter_text: str,
        chapter_title: str = "",
    ) -> list[DialogueTurn]:
        """Legacy: extract dialogues from a chapter in one LLM call."""
        text = chapter_text.strip()
        if len(text) < self.MIN_TEXT_LENGTH:
            return []

        if len(text) > self.MAX_TEXT_LENGTH:
            text = text[: self.MAX_TEXT_LENGTH] + "..."

        prompt = _USER_PROMPT_TEMPLATE.format(
            chapter_title=chapter_title or "未知章节",
            text=text,
        )

        t0 = time.perf_counter()
        try:
            raw = await self._achat(_SYSTEM_PROMPT, prompt, max_tokens=2048)
            self.api_calls += 1
            turns = self._parse_response(raw)
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.info(
                "LLM dialogue extraction: chapter='%s', turns=%d, elapsed=%dms",
                chapter_title,
                len(turns),
                elapsed,
            )
            return turns
        except Exception as e:
            self.api_calls += 1
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.warning(
                "LLM dialogue extraction failed for '%s' after %dms: %s",
                chapter_title,
                elapsed,
                e,
            )
            return []

    async def extract_batch_to_blocks(
        self,
        chapter_text: str,
        chapter_title: str = "",
        doc_id: str = "",
    ) -> list[NovelBlock]:
        turns = await self.extract_batch(chapter_text, chapter_title)
        if not turns:
            return []
        return [_turns_to_block(turns, doc_id, chapter_title)]

    async def _achat_result(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
    ) -> tuple[str, str]:
        """Chat returning ``(content, finish_reason)`` so callers can detect
        token truncation (``finish_reason == "length"``)."""
        tokens = int(max_tokens or self._max_tokens)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if hasattr(self._llm, "achat_result"):
            result = await self._llm.achat_result(
                messages,
                temperature=self._temperature,
                max_tokens=tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
            return result.content, str(result.finish_reason or "")
        if hasattr(self._llm, "achat"):
            return (
                await self._llm.achat(
                    messages,
                    temperature=self._temperature,
                    max_tokens=tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                ),
                "",
            )
        response = await self._llm.chat.completions.create(
            model=self._resolve_model(),
            messages=messages,
            temperature=self._temperature,
            max_tokens=tokens,
        )
        choice = response.choices[0]
        return (
            choice.message.content or "[]",
            str(getattr(choice, "finish_reason", "") or ""),
        )

    def _resolve_model(self) -> str:
        """Model for the raw client fallback path (config-driven, not hardcoded)."""
        try:
            from src.application.novel.factory import _load_raw_config

            cfg = _load_raw_config()
            model = (cfg.get("agent") or {}).get("model", "")
            if model:
                return str(model)
        except Exception:
            pass
        return DEFAULT_DEEPSEEK_MODEL

    async def _achat(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
    ) -> str:
        content, _ = await self._achat_result(system, user, max_tokens=max_tokens)
        return content

    @staticmethod
    def _parse_response(raw: str) -> list[DialogueTurn]:
        if not raw or not raw.strip():
            return []

        try:
            data = json.loads(raw)
            return _json_to_turns(data)
        except json.JSONDecodeError:
            pass

        m = re.search(r"```(?:json)?\s*\n?(\[.*?\])\n?```", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                return _json_to_turns(data)
            except json.JSONDecodeError:
                pass

        items = []
        for m in re.finditer(
            r'\{\s*"speaker"\s*:\s*"([^"]*)"\s*,\s*'
            r'"content"\s*:\s*"([^"]*)"\s*'
            r'(?:,\s*"mood"\s*:\s*"([^"]*)")?'
            r'(?:,\s*"type"\s*:\s*"([^"]*)")?'
            r".*?\}",
            raw,
            re.DOTALL,
        ):
            items.append(
                {
                    "speaker": m.group(1),
                    "content": m.group(2),
                    "mood": m.group(3) or "",
                    "type": m.group(4) or "spoken",
                }
            )

        if items:
            return _json_to_turns(items)

        logger.debug("LLM dialogue response unparseable: %s", raw[:200])
        return []


def _build_window_user(
    chapter_title: str, cand_line: str, alias_map_text: str, text: str
) -> str:
    """Assemble the user prompt for one extraction window (text injected)."""
    user_parts = [
        f"章节：{chapter_title or '未知'}",
    ]
    if alias_map_text:
        user_parts.append(f"【角色全名映射表（别名→全名）】{alias_map_text}")
    user_parts.append(f"【候选说话人】{cand_line}")
    user_parts.append("")
    user_parts.append(f"【原文】\n{text}")
    user_parts.append("\n请输出 JSON。")
    return "\n".join(user_parts)


def _looks_truncated(raw: str) -> bool:
    """Heuristic: output looks cut mid-JSON (unclosed brace/bracket).

    Counts opening vs closing ``{``/``[`` — a response whose JSON structure
    has more open than close brackets is almost certainly truncated at the
    token limit. Tolerates a natural-language tail after a complete object
    (``{"dialogues":[]} 后附说明`` stays balanced).
    """
    s = (raw or "").strip()
    if not s:
        return False
    opens = s.count("{") + s.count("[")
    closes = s.count("}") + s.count("]")
    return opens > closes


def _merge_turn_dicts(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge two extraction results, deduping by (speaker, content)."""
    merged = list(first)
    seen = {(t.get("speaker"), t.get("content")) for t in merged}
    for t in second:
        key = (t.get("speaker"), t.get("content"))
        if key not in seen:
            seen.add(key)
            merged.append(t)
    return merged


def _parse_dialogues_object(raw: str) -> list[dict[str, Any]]:
    """Parse {"dialogues":[...]} or bare list into dict turns."""
    raw = (raw or "").strip()
    if not raw:
        return []
    data: Any = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
            except json.JSONDecodeError:
                data = None
        if data is None:
            m2 = re.search(r"\[.*\]", raw, re.DOTALL)
            if m2:
                try:
                    data = json.loads(m2.group())
                except json.JSONDecodeError:
                    return []

    items: list = []
    if isinstance(data, dict):
        items = list(data.get("dialogues") or data.get("results") or [])
    elif isinstance(data, list):
        items = data
    else:
        return []

    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        speaker = str(item.get("speaker") or item.get("said_by") or "未知").strip() or "未知"
        try:
            conf = float(item.get("confidence") if item.get("confidence") is not None else 0.8)
        except (TypeError, ValueError):
            conf = 0.8
        out.append({"speaker": speaker, "content": content, "confidence": conf})
    return out


def _json_to_turns(data: list[dict]) -> list[DialogueTurn]:
    if not isinstance(data, list):
        return []
    turns = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        content = item.get("content", "").strip()
        if not content:
            continue
        speaker = item.get("speaker", "未知").strip() or "未知"
        turns.append(
            DialogueTurn(
                turn=i + 1,
                speaker=speaker,
                content=content,
                mood=item.get("mood", "").strip(),
            )
        )
    return turns


def _turns_to_block(
    turns: list[DialogueTurn],
    doc_id: str,
    chapter_title: str,
) -> NovelBlock:
    import uuid

    speakers = list({t.speaker for t in turns if t.speaker != "未知"})
    all_dialogue = " ".join(t.content for t in turns)
    style_tags = _infer_tags_from_turns(turns)

    return NovelBlock(
        global_id=f"{doc_id}_dialogue_llm_{uuid.uuid4().hex[:8]}",
        doc_id=doc_id,
        source=f"《{doc_id}》{chapter_title}" if doc_id else "",
        chapter_title=chapter_title,
        block_type=BLOCK_DIALOGUE,
        scene=chapter_title,
        characters=speakers,
        dialogues=turns,
        style_tags=style_tags,
        vec_text_dialogue=all_dialogue,
        all_person=speakers,
        token_length=len(all_dialogue),
    )


def _infer_tags_from_turns(turns: list[DialogueTurn]) -> list[str]:
    tags = []
    if not turns:
        return ["未分类"]

    avg_len = sum(len(t.content) for t in turns) / len(turns)
    if avg_len < 8:
        tags.append("寡言")
    elif avg_len > 40:
        tags.append("健谈")

    moods = [t.mood for t in turns if t.mood]
    if moods:
        from collections import Counter

        top_mood = Counter(moods).most_common(1)[0][0]
        tags.append(top_mood)

    return tags if tags else ["未分类"]
