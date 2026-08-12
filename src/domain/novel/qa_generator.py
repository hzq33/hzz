"""QA Generator — produce question-answer pairs from narrative chunks.

Uses DeepSeek to generate retrieval-friendly Chinese Q&A, stored as
NovelBlock (block_type=qa). Questions are written for user-like queries
with proper names so the QA channel matches fact questions well.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from src.domain.novel.models import BLOCK_QA, NovelBlock

logger = logging.getLogger("agent")

_GENERIC_QUESTION_RE = re.compile(
    r"^(这段|本文|上文|该段|以下|这段文字|这段内容).*(描述|讲述|说了|写了|讲了|内容|什么)？"
    r"|^发生了什么？"
    r"|^这段话?的?意思是什么？"
    r"|^What (is|does|happened).*",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = """你是小说事实问答出题助手。根据原文片段生成检索友好的中文问答对。

硬性规则：
1. 每条 question 必须包含至少一个专名（角色名/地名/物件名），禁止泛问。
2. 禁止：「这段文字描述了什么」「发生了什么」「本文讲了什么」等空洞问句。
3. answer 最多两句，必须能在原文中直接找到依据，禁止编造。
4. 多条问答覆盖不同事实（人物关系 / 情节事件 / 场景地点），不要换皮重复。
5. 可为同一关键事实再写一条改写问（如「是谁」与「什么身份」），答案一致即可。
6. tags 只能从 character / plot / setting 中选。
7. 只输出 JSON 数组，不要其他文字。

格式：
[{"question":"...","answer":"...","tags":["character"],"names":["专名1"]}]
"""

_USER_TEMPLATE = """请根据下面小说原文生成 {count} 条问答对。

已知可能出现的角色名（优先写入问句）：{known_names}

原文：
{text}
"""


class QAGenerator:
    """QA 通道主实现（ingest generate_qa=True 时生成 QA 对）。

    由 src/application/novel/ingest/blocks.py::build_qa_blocks 调用；
    外部新代码不应直接使用，QA 生成由 ingest 管线统一管理。
    """

    def __init__(self, llm_client=None, known_characters: list[str] | None = None):
        self._llm = llm_client
        self._known_characters = list(known_characters or [])

    async def generate(
        self,
        narrative_block: NovelBlock,
        count: int = 3,
    ) -> list[NovelBlock]:
        text = narrative_block.narrative_text
        if not text or len(text) < 20:
            return []

        known = list(dict.fromkeys(
            (narrative_block.all_person or [])
            + (narrative_block.characters or [])
            + self._known_characters
        ))
        # Keep names that actually appear in this chunk
        known = [n for n in known if n and n in text][:12]

        qa_pairs = await self._gen_with_llm(text, count, known_names=known)

        blocks: list[NovelBlock] = []
        for q, a, tags, names in qa_pairs:
            names = list(dict.fromkeys([*names, *self._extract_names_from_text(q, known)]))
            vec = self._build_vec_text(q, names)
            gid = f"{narrative_block.doc_id}_qa_{uuid.uuid4().hex[:8]}"
            blocks.append(NovelBlock(
                global_id=gid,
                doc_id=narrative_block.doc_id,
                source=narrative_block.source,
                chapter_title=narrative_block.chapter_title,
                block_type=BLOCK_QA,
                question=q,
                answer=a,
                ref_chunk_ids=[narrative_block.global_id] if narrative_block.global_id else [],
                qa_tags=tags,
                vec_text_qa=vec,
                all_person=list(set(
                    (narrative_block.all_person or []) + names
                )),
                token_length=len(q) + len(a),
            ))
        return blocks

    async def _gen_with_llm(
        self,
        text: str,
        count: int,
        known_names: list[str] | None = None,
    ) -> list[tuple]:
        if self._llm is None:
            return self._rule_based_qa(text, count, known_names or [])

        prompt = _USER_TEMPLATE.format(
            count=min(count, 5),
            known_names="、".join(known_names) if known_names else "（从原文推断）",
            text=text[:2000],
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            kwargs = {"temperature": 0.3, "max_tokens": 1024}
            try:
                response = await self._llm.achat(
                    messages,
                    extra_body={"thinking": {"type": "disabled"}},
                    **kwargs,
                )
            except TypeError:
                response = await self._llm.achat(messages, **kwargs)

            qa_list = json.loads(self._extract_json(response or ""))
            results: list[tuple] = []
            for item in qa_list:
                q = str(item.get("question", "")).strip()
                a = str(item.get("answer", "")).strip()
                tags = item.get("tags") or []
                if isinstance(tags, str):
                    tags = [tags]
                names = item.get("names") or []
                if isinstance(names, str):
                    names = [names]
                if not q or not a:
                    continue
                if self.is_generic_question(q):
                    continue
                if not self._has_proper_name(q, known_names or [], names):
                    # Soft reject: still keep if question looks specific enough
                    if len(q) < 8 or q[-2:] == "什么":
                        continue
                results.append((q, a, list(tags), [str(n) for n in names if n]))
                if len(results) >= count:
                    break
            return results or self._rule_based_qa(text, count, known_names or [])
        except Exception as e:
            logger.warning("QA LLM generation failed: %s", e)
            return self._rule_based_qa(text, count, known_names or [])

    @staticmethod
    def is_generic_question(question: str) -> bool:
        q = (question or "").strip()
        if not q:
            return True
        if _GENERIC_QUESTION_RE.match(q):
            return True
        generics = {
            "这段文字描述了什么？",
            "这段描述了什么？",
            "发生了什么？",
            "本文讲了什么？",
            "这段话是什么意思？",
        }
        return q in generics

    @staticmethod
    def _has_proper_name(question: str, known: list[str], names: list) -> bool:
        for n in list(known) + list(names):
            if n and n in question:
                return True
        # Heuristic: 2–4 CJK chars before interrogative often a name
        return bool(re.search(r"[\u4e00-\u9fff]{2,4}(是谁|是什么|为什么|怎么|在哪|何时)", question))

    @staticmethod
    def _extract_names_from_text(text: str, known: list[str]) -> list[str]:
        return [n for n in known if n and n in text]

    @staticmethod
    def _build_vec_text(question: str, names: list[str]) -> str:
        extra = " ".join(n for n in names if n and n not in question)
        return f"{question} {extra}".strip() if extra else question

    @staticmethod
    def _rule_based_qa(
        text: str,
        count: int,
        known_names: list[str] | None = None,
    ) -> list[tuple]:
        """Rule-based QA (tests / LLM fallback). Avoids generic questions."""
        qa_pairs: list[tuple] = []
        names = list(known_names or [])
        if not names:
            names = list(set(re.findall(
                r"([\u4e00-\u9fff]{2,3})(?:独自|忽然|望着|坐在|走在|说道)",
                text,
            )))[:3]
        sentences = [s.strip() for s in re.split(r"[。！？\n]", text) if len(s.strip()) > 6]

        for name in names:
            for s in sentences:
                if name in s:
                    qa_pairs.append((
                        f"{name}在这段情节里做了什么？",
                        s[:80],
                        ["character"],
                        [name],
                    ))
                    break
            if len(qa_pairs) >= count:
                break

        if len(qa_pairs) < count and names and sentences:
            name = names[0]
            qa_pairs.append((
                f"与{name}有关的场景发生在哪里或怎样的环境？",
                sentences[0][:80],
                ["setting"],
                [name],
            ))

        return qa_pairs[:count]

    @staticmethod
    def _extract_json(text: str) -> str:
        match = re.search(r"\[[\s\S]*\]", text or "")
        return match.group(0) if match else "[]"
