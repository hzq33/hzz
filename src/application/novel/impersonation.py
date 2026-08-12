"""Character impersonation — full pipeline for mimicking novel characters.

Orchestrates the 7-step workflow:
  1. Intent classification → "imitate"
  2. Search dialogue channel for character style samples
  3. Pull companion scene descriptions via ref_narrative_id
  4. Extract speech patterns (sentence length, punctuation, fillers)
  5. Build structured prompt (system persona + style samples + scene context)
  6. Call LLM to generate in-character dialogue/thoughts
  7. Return both generated text and source references
"""

from __future__ import annotations

from src.application.novel.intent_router import IntentRouter
from src.domain.novel.models import BLOCK_DIALOGUE
from src.infrastructure.novel_store import NovelVectorStore


class ImpersonationService:
    """Generate in-character dialogue imitating novel personas.

    Usage:
        svc = ImpersonationService(store, llm_client)
        result = await svc.impersonate(
            character="苏瑶",
            scene="雨夜重逢",
            style="清冷",
        )
        print(result.generated_text)
    """

    _PROMPT_TEMPLATE = (
        "你是一个小说角色扮演生成器。根据以下信息，生成一段符合角色人设的原创内容。\n\n"
        "## 角色设定\n"
        "角色名：{character}\n"
        "说话风格：{style_tags}\n"
        "要求：{user_request}\n\n"
        "## 场景描写（原文）\n"
        "{scene_context}\n\n"
        "## 角色历史对话样本\n"
        "{dialogue_samples}\n\n"
        "## 要求\n"
        "- 请生成贴合角色性格的对话或内心独白\n"
        "- 用词、句式、停顿习惯必须与样本一致\n"
        "- 不要脱离原著设定，不要添加原著没有的情节\n"
        "- 只输出角色说的话（如果指定了多角色则包含双方对话）\n"
    )

    def __init__(
        self,
        store: NovelVectorStore,
        llm_client=None,
        router: IntentRouter | None = None,
    ):
        self.store = store
        self.llm = llm_client
        self.router = router or IntentRouter()

    async def impersonate(
        self,
        character: str,
        user_request: str = "",
        scene_hint: str = "",
        style: str = "",
        doc_id: str | None = None,
        max_samples: int = 5,
    ) -> dict:
        """Generate character-consistent dialogue or inner monologue.

        Args:
            character: Target character name (e.g. "苏瑶").
            user_request: What to generate (e.g. "写一段雨夜重逢对话").
            scene_hint: Scene context keyword (e.g. "雨夜").
            style: Desired style (e.g. "清冷", "温柔").
            doc_id: Optional book filter.
            max_samples: Max dialogue samples to retrieve.

        Returns:
            dict with keys: generated_text, samples, scene_context, sources
        """
        # 1. Search dialogue channel for character's style samples
        query = f"{character} {scene_hint} {style}".strip()
        if not query:
            query = f"{character} 对话"

        hits = await self.store.search(
            query,
            channel=BLOCK_DIALOGUE,
            doc_id=doc_id,
            top_k=max_samples,
        )

        # 2. Collect dialogue samples and scene references
        samples: list[dict] = []
        scene_contexts: list[str] = []
        sources: list[str] = []
        style_tags_all: list[str] = []

        for hit in hits:
            block = hit.block
            samples.append({
                "scene": block.scene,
                "dialogues": [{"speaker": t.speaker, "content": t.content} for t in block.dialogues],
            })
            style_tags_all.extend(block.style_tags)
            if block.source:
                sources.append(block.source)

            # Pull scene context from referenced narrative block
            if block.ref_narrative_id:
                ref_block = self.store.get_block(block.ref_narrative_id)
                if ref_block and ref_block.narrative_text:
                    scene_contexts.append(ref_block.narrative_text[:300])
                elif block.scene_detail:
                    scene_contexts.append(block.scene_detail)
            elif block.scene_detail:
                scene_contexts.append(block.scene_detail)

        # 3. Analyze speech patterns
        speech_patterns = self._analyze_speech(samples)

        # 4. Build prompt
        dialogue_samples_text = self._format_samples(samples)
        scene_context_text = "\n---\n".join(scene_contexts[:3]) or "无额外场景描写"
        style_text = ", ".join(set(style_tags_all)) or speech_patterns.get("style_summary", "未分类")

        prompt = self._PROMPT_TEMPLATE.format(
            character=character,
            style_tags=style_text,
            user_request=user_request or f"以{character}的语气生成一段对话",
            scene_context=scene_context_text,
            dialogue_samples=dialogue_samples_text,
        )

        # 5. Call LLM (or return prompt for manual use)
        if self.llm:
            try:
                generated = await self.llm.achat(
                    messages=[
                        {"role": "system", "content": "你是一个小说角色扮演生成器。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.8,
                    max_tokens=1024,
                )
            except Exception:
                generated = f"[LLM 调用失败]\n\n{prompt}"
        else:
            generated = (
                "[未配置 LLM 客户端 — 以下为构建的 prompt]\n\n"
                "=" * 40 + "\n" + prompt + "\n" + "=" * 40
            )

        return {
            "generated_text": generated,
            "samples": samples,
            "scene_context": scene_context_text,
            "sources": list(set(sources)),
            "style": style_text,
            "speech_patterns": speech_patterns,
        }

    # ── Speech Pattern Analysis ─────────────────────────

    @staticmethod
    def _analyze_speech(samples: list[dict]) -> dict:
        """Analyze character speech patterns from dialogue samples."""
        all_turns: list[str] = []
        for s in samples:
            for d in s.get("dialogues", []):
                all_turns.append(d.get("content", ""))

        if not all_turns:
            return {"avg_sentence_length": 0, "style_summary": "无数据"}

        lengths = [len(t) for t in all_turns]
        avg = sum(lengths) / len(lengths)

        # Style heuristics
        if avg < 8:
            style = "寡言少语"
        elif avg < 20:
            style = "简洁克制"
        elif avg > 40:
            style = "健谈外向"
        else:
            style = "正常"

        return {
            "avg_sentence_length": round(avg, 1),
            "total_turns": len(all_turns),
            "style_summary": style,
            "sample_turns": all_turns[:3],
        }

    @staticmethod
    def _format_samples(samples: list[dict]) -> str:
        """Format dialogue samples for prompt."""
        lines = []
        for i, s in enumerate(samples[:5], 1):
            scene = s.get("scene", "未知场景")
            lines.append(f"\n样本 {i} — 场景: {scene}")
            for d in s.get("dialogues", [])[:5]:
                lines.append(f"  [{d['speaker']}] {d['content']}")
        return "\n".join(lines)
