"""Dialogue extraction — structured multi-turn dialogue from novel text.

Detects and parses dialogue patterns in Chinese novel text, producing
dialogue-type NovelBlock records for the dialogue channel.

Supports 5 common Chinese novel dialogue patterns:
1. 角色名："对话内容"
2. 角色名 — 对话内容
3. "对话内容" — 角色名 说/道/问
4. "对话内容" (继承上一轮说话人)
5. 角色名说/道/问："对话内容"
"""

from __future__ import annotations

import re
import uuid

from src.domain.novel.models import BLOCK_DIALOGUE, DialogueTurn, NovelBlock

# ── Dialogue detection patterns ────────────────────────────

# Pattern 1: 「对话内容」 or "对话内容"
_QUOTED_DIALOGUE = re.compile(r'[""「」『』](.+?)[""「」『』]')

# Pattern 2: 角色名："对话内容"
_NAMED_DIALOGUE = re.compile(r'^(.{1,8})[：:]\s*[""「」](.+?)[""「」]')

# Pattern 3: "对话内容" — 角色名 说/道/问/答
_PATTERN_POSTFIX = re.compile(
    r'[""「」](.+?)[""「」]\s*[—\-]\s*(.{1,8})\s*(?:说|道|问|答|喊|叫|吼|叹|低语|喃喃|轻声道)'
)

# Pattern 4a: 角色名 - 对话内容 (markdown dialogue format)
_PATTERN_DASH = re.compile(r'^(.{1,8})\s*[——\-]\s*(.+)$')
# Pattern 4b: 「对话」——角色名 (Japanese light novel postfix)
_PATTERN_JA_POSTFIX = re.compile(
    r'[「](.+?)[」]\s*[——\-]\s*(.{1,8})(?:\s|$)'
)

# ── Japanese light novel patterns ──

# JP Pattern A: Name[はがも]「quote」 (with or without と言った)
# Covers: 八奈見は「知らない」 / 八奈見は「知らない」と言った / 八奈見「知らない」
_JP_NAMED_QUOTE = re.compile(
    r'(.{1,10}?)(?:さん|くん|ちゃん|さま|様|氏|先輩|君)?\s*(?:[はがのも]\s*)?[「](.+?)[」]'
)

# JP Pattern B: 「quote」(+ optional と/って) Name (postfix attribution)
# Covers: 「知らない」と八奈見さん / 「知らない」八奈見 / 「負けた」って温水
_JP_POSTFIX_QUOTE = re.compile(
    r'[「](.+?)[」]\s*(?:と|って|、|\s)?\s*(.{1,10}?)(?:さん|くん|ちゃん|さま|様|氏|先輩|君)?(?=\s|$|[。、)）はがをのにだと]|\n)'
)

# JP Pattern C: 一人称自言自语「quote」 / （quote）
_JP_MONOLOGUE = re.compile(r'[（(]([^）)]{2,50})[）)]')

# ── Original Chinese patterns ──

# Pattern 5: 角色名说/道/问："对话内容"
_NAMED_SPEAK = re.compile(
    r'^(.{1,8})\s*(?:说|道|问|答|喊|叫|吼|叹|笑道|冷笑道|低声说|轻声道|喃喃道)[：:]\s*[""「」](.+?)[""「」]'
)

# Mood detection patterns (inline action/emotion descriptors)
_MOOD_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'冷冷[地]?(?:说|道|问|笑|看|哼)'), "冷傲"),
    (re.compile(r'冷笑|冷哼|嗤笑'), "嘲讽"),
    (re.compile(r'温柔[地]?(?:说|道|笑|看)'), "温柔"),
    (re.compile(r'怒[声]?[地]?(?:说|道|喝|吼)'), "愤怒"),
    (re.compile(r'笑[着]?[地]?(?:说|道)'), "轻松"),
    (re.compile(r'叹[气]?[地]?(?:说|道)'), "感慨"),
    (re.compile(r'低声|喃喃|轻声道'), "低沉"),
    (re.compile(r'大声|吼|喊|喝'), "激动"),
    (re.compile(r'犹豫|迟疑'), "犹豫"),
    (re.compile(r'认真|严肃'), "严肃"),
]

# Default speaker used when no speaker can be identified
_DEFAULT_SPEAKER = "未知"

# Characters that should be filtered out as speaker names
_INVALID_SPEAKERS = {
    "未知", "他", "她", "它", "我", "你", "他们", "她们", "众人", "大家",
    "俺", "僕", "私", "あたし", "わたし", "彼", "彼女", "あいつ", "こいつ",
}

# Japanese honorific suffixes to strip (JP originals + CN-translated equivalents)
_HONORIFICS = re.compile(
    r'(さん|くん|ちゃん|さま|様|先輩|先生|君|氏|[たさ]ん'
    r'|同学|小姐|先生|前辈|学弟|学妹|殿下|大人)$'
)


def strip_honorific(name: str) -> str:
    """公开入口：去除日文敬称（八奈見さん → 八奈見）。

    dev 脚本验证敬称处理时使用，不直接 import 私有实现。
    """
    return _strip_honorific(name)


def _strip_honorific(name: str) -> str:
    """Strip Japanese honorifics: 八奈見さん → 八奈見."""
    return _HONORIFICS.sub('', name.strip()).strip()


class DialogueExtractor:
    """规则抽取实现——作为 dialogue_pipeline 的降级路径（LLM 失败/禁用时回退）。

    主路径：src.application.novel.dialogue_pipeline.extract（LLMDialogueExtractor）；
    本类由 ingest/blocks.py 在 attribution 禁用或产出 0 块时回退调用，
    保证离线/LLM 故障下对话仍可入库。外部新代码应走 dialogue_pipeline。
    """

    def extract(
        self,
        text: str,
        scene: str = "",
        scene_detail: str = "",
        doc_id: str = "",
        chapter_title: str = "",
    ) -> list[DialogueTurn]:
        """Extract dialogue turns from text.

        Args:
            text: Raw dialogue block text (from 【dialogue】 marker or auto-detected).
            scene: Scene description.
            scene_detail: Environmental/action description.
            doc_id: Document/book identifier.
            chapter_title: Chapter title for metadata.

        Returns:
            List of DialogueTurn objects.
        """
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        turns: list[DialogueTurn] = []
        current_speaker = _DEFAULT_SPEAKER

        line_idx = 0
        for line in lines:
            turn = self._parse_line(line, current_speaker)
            if turn:
                # Detect mood from the full line
                turn.mood = self._detect_mood(line)
                # Clean speaker name
                turn.speaker = self._clean_speaker(turn.speaker)
                # Context inference: if speaker still unknown, try narrative context
                if turn.speaker == _DEFAULT_SPEAKER:
                    context = ""
                    for prev_line in lines[max(0, line_idx - 5):line_idx]:
                        context += prev_line
                    inferred = self._infer_speaker_from_context(context)
                    if inferred and inferred not in _INVALID_SPEAKERS:
                        turn.speaker = inferred
                    else:
                        # Inference failed — fall back to last speaker so
                        # consecutive same-speaker dialogue isn't lost.
                        turn.speaker = current_speaker
                if turn.speaker != _DEFAULT_SPEAKER:
                    current_speaker = turn.speaker
                turns.append(turn)
            line_idx += 1

        # Re-number turns
        for i, t in enumerate(turns):
            t.turn = i + 1

        return turns

    def _infer_speaker_from_context(self, text_before: str) -> str:
            """Infer speaker from the narrative sentence right before a 「quote.

            Chinese-translated Japanese LNs often separate speaker from quote:
              八奈见咬紧嘴唇，垂下脸。 ← narrative (speaker = 八奈见)
              「别说了，没关系。」     ← quote (no explicit attribution)
            """
            # Find last sentence-ending punctuation before the quote
            sentences = text_before.split('。')
            if not sentences:
                return ""
            # Look at last 2 sentences for a named character
            for s in reversed(sentences[-3:]):
                s = s.strip()
                if not s or len(s) < 4:
                    continue
                # Common patterns: 八奈见[动词], 温水[动词]
                # Anchor name to a non-CJK boundary (start, punctuation, quote, space)
                # to avoid mid-sentence false matches like "快的步伐走" → "快的步伐"
                m = re.search(
                    r'(?:^|[^\u4e00-\u9fff])([\u4e00-\u9fff]{2,3})'
                    r'(?:同学|小姐|先生|女生|男生)?'
                    r'(?:咬|抬|站|坐|低|看|说|道|哭|笑|叹|转|垂|望|走|跑|停|问|答|叫|喊|冲|瞪|跪|爬|倒|僵|呆'
                    r'|踩|拍|拉|推|抱|踢|跳|甩|挽|抚|抹|擦|捂'
                    r'|呢喃|低声|孱弱|坚强|缓缓|连忙|径自|继续|默默)',
                    s)
                if m:
                    name = m.group(1).strip()
                    if name not in _INVALID_SPEAKERS and len(name) >= 2:
                        return name
            return ""

    def extract_to_block(
        self,
        block_text: str,
        scene: str = "",
        doc_id: str = "",
        chapter_title: str = "",
        ref_narrative_id: str = "",
        scene_detail: str = "",
        chapter_index: int = 0,
        block_index: int = 0,
        ) -> NovelBlock:
        """Extract dialogue and produce a NovelBlock directly.

        Returns:
            NovelBlock with block_type=dialogue, populated dialogues and vec_text.
        """
        turns = self.extract(block_text, scene, scene_detail, doc_id, chapter_title)

        speakers = list({t.speaker for t in turns if t.speaker != _DEFAULT_SPEAKER})
        all_dialogue = " ".join(t.content for t in turns)

        # Build dialogue style text: scene description + all dialogue content
        style_text_parts = []
        if scene:
            style_text_parts.append(scene)
        if scene_detail:
            style_text_parts.append(scene_detail)
        style_text_parts.append(all_dialogue)
        style_text = " ".join(style_text_parts)

        if doc_id:
            gid = f"{doc_id}_c{chapter_index:03d}_d{block_index:04d}"
        else:
            gid = f"c{chapter_index:03d}_d{block_index:04d}_{uuid.uuid4().hex[:6]}"

        block = NovelBlock(
            global_id=gid,
            doc_id=doc_id,
            source=f"《{doc_id}》{chapter_title}" if doc_id and chapter_title else "",
            chapter_title=chapter_title,
            block_type=BLOCK_DIALOGUE,
            scene=scene,
            scene_detail=scene_detail,
            characters=speakers,
            dialogues=turns,
            style_tags=_infer_style_tags(turns),
            ref_narrative_id=ref_narrative_id,
            vec_text_dialogue=style_text,
            all_person=speakers,
            token_length=len(style_text),
        )
        return block

    def _parse_line(
        self, line: str, last_speaker: str
    ) -> DialogueTurn | None:
        """Parse a single line into a DialogueTurn.

        Tries patterns: JP (only if kana present) → CN named → colon → dash → postfix → generic.
        """
        has_kana = bool(re.search(r'[\u3040-\u309f\u30a0-\u30ff]', line))

        # JP patterns only activate when line contains kana (hiragana/katakana)
        if has_kana:
            # JP Pattern A
            m = _JP_NAMED_QUOTE.search(line)
            if m:
                name = _strip_honorific(m.group(1).strip())
                return DialogueTurn(turn=0, speaker=name, content=m.group(2).strip())

            # JP Pattern B
            m = _JP_POSTFIX_QUOTE.search(line)
            if m:
                name = _strip_honorific(m.group(2).strip())
                return DialogueTurn(turn=0, speaker=name, content=m.group(1).strip())

            # JP Pattern C
            m = _JP_MONOLOGUE.search(line)
            if m and last_speaker and last_speaker != "未知":
                return DialogueTurn(turn=0, speaker=last_speaker, content=m.group(1).strip())

            # JP Pattern D
            qm = _QUOTED_DIALOGUE.search(line)
            if qm:
                jp_m = _JP_POSTFIX_QUOTE.search(line)
                if jp_m:
                    name = _strip_honorific(jp_m.group(2).strip())
                    if name and name not in _INVALID_SPEAKERS:
                        return DialogueTurn(turn=0, speaker=name, content=jp_m.group(1).strip())

        # CN Pattern 5: 角色名说/道/问："quote" (most specific)
        m = _NAMED_SPEAK.match(line)
        if m:
            return DialogueTurn(
                turn=0,
                speaker=m.group(1).strip(),
                content=m.group(2).strip(),
            )

        # Pattern 1: Name："quote"
        m = _NAMED_DIALOGUE.match(line)
        if m:
            return DialogueTurn(
                turn=0,
                speaker=m.group(1).strip(),
                content=m.group(2).strip(),
            )

        # Pattern 2: Name — quote
        m = _PATTERN_DASH.match(line)
        if m:
            return DialogueTurn(
                turn=0,
                speaker=m.group(1).strip(),
                content=m.group(2).strip(),
            )

        # Pattern 3: "quote" — said Name
        m = _PATTERN_POSTFIX.match(line)
        if m:
            return DialogueTurn(
                turn=0,
                speaker=m.group(2).strip(),
                content=m.group(1).strip(),
            )

        # Pattern 4: Generic quoted text — defer to context inference.
        # Return _DEFAULT_SPEAKER so extract() can try _infer_speaker_from_context
        # first (essential for CN-translated JP light novels where speaker sits on
        # a separate narrative line). Falls back to last_speaker if inference fails.
        qm = _QUOTED_DIALOGUE.findall(line)
        if qm:
            content = " ".join(qm)
            return DialogueTurn(
                turn=0,
                speaker=_DEFAULT_SPEAKER,
                content=content,
            )

        return None

    def _detect_mood(self, line: str) -> str:
        """Detect mood/emotion from inline action descriptors in a dialogue line."""
        for pattern, mood in _MOOD_PATTERNS:
            if pattern.search(line):
                return mood
        return ""

    def _clean_speaker(self, name: str) -> str:
        """Clean and validate a speaker name.

        Removes artifacts like trailing punctuation, action verbs,
        and filters out generic pronouns and invalid names.
        """
        if not name:
            return _DEFAULT_SPEAKER

        # Remove trailing punctuation and artifacts
        name = re.sub(r'[：:—\-、，。！？\s]+$', "", name).strip()

        # Remove common action/verb suffixes that get incorrectly captured
        # e.g. "林晚晴冷哼一声" → "林晚晴"
        # Order matters: longer/more specific patterns should come first,
        # but we apply ALL patterns (not just the first) for best cleanup.
        _SUFFIX_PATTERNS = [
            r'也.*$', r'愣了一下$', r'愣了愣$', r'愣住了$', r'愣了一下$',
            r'松开.*$', r'松了口.*$', r'松了一口.*$',
            r'冷哼.*$', r'冷笑.*$', r'笑了笑$', r'笑了$', r'大笑$',
            r'皱[眉眉头]$', r'点了点[头]$', r'点头$', r'摇头$',
            r'瞪了.*$', r'白了.*$', r'看着.*$', r'看向.*$',
            r'低下[头]$', r'抬起头$', r'取出.*$', r'摆摆[手]$',
            r'叹了口气$', r'叹气$', r'大怒$',
            r'脸色.*$', r'想了想$', r'深吸一口.*$',
            r'转过.*$', r'后退.*$', r'上前.*$', r'站起身.*$',
        ]
        prev = None
        while prev != name:
            prev = name
            for suffix in _SUFFIX_PATTERNS:
                cleaned = re.sub(suffix, '', name).strip()
                if cleaned and len(cleaned) >= 2 and cleaned != name:
                    name = cleaned
                    break  # restart loop with cleaned name

        # Filter out invalid speakers
        if name in _INVALID_SPEAKERS:
            return _DEFAULT_SPEAKER

        # ── Final pass: strip trailing single-char noise ──
        # Handles cases like "林晚晴冷" → "林晚晴", "林震天笑" → "林震天"
        # that the multi-char suffix patterns above didn't catch because
        # the speaker extractor only captured the first char of the verb.
        _NOISE_SUFFIX_CHARS = set("冷笑大说道：喊道看叹走跑停回问答叫瞪笑指拉推抱")
        if len(name) >= 3 and name[-1] in _NOISE_SUFFIX_CHARS:
            trimmed = name[:-1]
            if re.match(r'^[\u4e00-\u9fff]{2,4}$', trimmed) and trimmed not in _INVALID_SPEAKERS:
                name = trimmed

        # Must be 1-8 Chinese characters
        if not re.match(r'^[\u4e00-\u9fff]{1,8}$', name):
            # Allow names with · (foreign names like 亚瑟·潘德拉贡)
            if "·" in name and len(name) <= 15:
                return name
            return _DEFAULT_SPEAKER

        return name


def _infer_style_tags(turns: list[DialogueTurn]) -> list[str]:
    """Infer style tags from dialogue characteristics.

    Uses simple heuristics: sentence length, punctuation patterns, mood keywords.
    For production, replace with LLM-based inference.
    """
    tags: list[str] = []
    if not turns:
        return tags

    avg_len = sum(len(t.content) for t in turns) / len(turns)
    all_content = " ".join(t.content for t in turns)
    all_moods = " ".join(t.mood for t in turns if t.mood)

    # Length-based
    if avg_len < 8:
        tags.append("寡言")
    elif avg_len > 40:
        tags.append("健谈")

    # Mood-based (from detected moods)
    if all_moods:
        mood_tag_map = {
            "冷傲": ["冷冷", "冷哼", "嗤笑"],
            "嘲讽": ["冷笑", "无聊", "蠢"],
            "温柔": ["温柔", "别担心", "没关系"],
            "愤怒": ["怒", "放肆", "大胆"],
            "轻松": ["笑道", "笑了笑"],
            "感慨": ["叹", "罢了"],
            "低沉": ["低声", "喃喃"],
            "激动": ["大声", "吼", "喊"],
            "严肃": ["认真", "严肃"],
        }
        for tag, keywords in mood_tag_map.items():
            if any(kw in all_moods or kw in all_content for kw in keywords):
                tags.append(tag)

    # Punctuation-based
    question_ratio = all_content.count("？") / max(len(turns), 1)
    if question_ratio > 0.5:
        tags.append("反问")

    exclaim_ratio = all_content.count("！") / max(len(turns), 1)
    if exclaim_ratio > 0.3:
        tags.append("感叹")

    # Style-based keywords
    style_map = {
        "古风": ["在下", "公子", "姑娘", "罢了", "便是", "令尊", "家师", "晚辈"],
        "毒舌": ["蠢", "白痴", "无聊", "你以为", "油嘴滑舌"],
        "热血": ["我一定要", "绝不", "拼了", "冲", "放肆"],
    }
    for tag, keywords in style_map.items():
        if any(kw in all_content for kw in keywords):
            tags.append(tag)

    return list(set(tags)) if tags else ["未分类"]
