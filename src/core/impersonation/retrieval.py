"""ImpersonationAgent retrieval mixin — RAG context retrieval methods.

Extracted from the former monolithic ``impersonation_agent.py``; logic unchanged.
Mixin methods share instance state (``self._store`` / ``self._card`` / ``self.character``).
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.impersonation.models import Citation, _hit_to_citation


def _legacy_fallback_enabled() -> bool:
    """config impersonation.legacy_fallback（默认 false）：完整链路异常时是否回退旧轻链路。"""
    try:
        from src.application.novel.factory import _load_raw_config

        return bool((_load_raw_config().get("impersonation") or {}).get("legacy_fallback", False))
    except Exception:  # noqa: BLE001
        return False

logger = logging.getLogger("agent")


class ImpersonationRetrievalMixin:
    """Style / fact / relation-event / narrative retrieval methods."""


    def _style_name_set(self) -> set[str]:
        names = {self.character}
        aliases = getattr(self._card, "aliases", None) or []
        for a in aliases:
            a = (a or "").strip()
            if len(a) >= 2:
                names.add(a)
        return {n for n in names if n}

    def _speaker_matches_style(self, speaker: str) -> bool:
        sp = (speaker or "").strip()
        if not sp:
            return False
        for name in self._style_name_set():
            if sp == name or name in sp:
                return True
        return False

    @staticmethod
    def _norm_style_text(text: str) -> str:
        return "".join((text or "").split())

    def _card_sample_norms(self) -> set[str]:
        norms: set[str] = set()
        for d in getattr(self._card, "sample_dialogues", None) or []:
            content = d.get("content") if isinstance(d, dict) else str(d)
            norm = self._norm_style_text(content or "")
            if norm:
                norms.add(norm)
        return norms

    def _style_search_query(self, context: str) -> str:
        """Prefer a style probe over fact/plot user wording."""
        ctx = (context or "").strip()
        # Short chit-chat may still use user text; long/plotty queries use fixed probe.
        if ctx and len(ctx) < 20:
            return f"{self.character} {ctx}"
        phrases = (
            getattr(self._card, "structured_catchphrases", None)
            or getattr(self._card, "catchphrases", None)
            or []
        )
        if phrases:
            return f"{self.character} {phrases[0]}"
        return f"{self.character} 对话 语气"

    def _extract_character_style_turns(
        self, hits: list[Any], *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Keep only turns spoken by the impersonated character (aliases included)."""
        limit = self._STYLE_TOP_K if limit is None else limit
        card_norms = self._card_sample_norms()
        seen_norms: set[str] = set()
        out: list[dict[str, Any]] = []
        for hit in hits or []:
            block = getattr(hit, "block", None) or hit
            score = float(getattr(hit, "score", 0.0) or 0.0)
            block_id = str(getattr(block, "global_id", "") or "")
            doc_id = str(getattr(block, "doc_id", "") or "")
            scene = str(
                getattr(block, "scene", "")
                or getattr(block, "chapter_title", "")
                or getattr(block, "source", "")
                or ""
            )
            for idx, t in enumerate(getattr(block, "dialogues", None) or []):
                sp = str(getattr(t, "speaker", "") or "").strip()
                ct = str(getattr(t, "content", "") or "").strip()
                if not ct or not self._speaker_matches_style(sp):
                    continue
                norm = self._norm_style_text(ct)
                if not norm or norm in seen_norms or norm in card_norms:
                    continue
                seen_norms.add(norm)
                out.append({
                    "speaker": sp,
                    "content": ct,
                    "score": score,
                    "doc_id": doc_id,
                    "block_id": f"{block_id}#t{idx}" if block_id else f"style_t{len(out)}",
                    "chapter_title": scene,
                })
                if len(out) >= limit:
                    return out
        return out

    def _style_hit_mentions_character(self, hit: Any) -> bool:
        """Legacy block-level mention check (style_mode=legacy_block)."""
        name = (self.character or "").strip()
        if not name:
            return True
        block = getattr(hit, "block", None) or hit
        scene = str(getattr(block, "scene", "") or "")
        if name in scene:
            return True
        for t in getattr(block, "dialogues", None) or []:
            sp = str(getattr(t, "speaker", "") or "")
            ct = str(getattr(t, "content", "") or "")
            if name in sp or name in ct:
                return True
        return False

    def _filter_style_hits(self, hits: list[Any]) -> list[Any]:
        """Legacy block filter (style_mode=legacy_block)."""
        kept: list[Any] = []
        for hit in hits or []:
            score = float(getattr(hit, "score", 0.0) or 0.0)
            mentions = self._style_hit_mentions_character(hit)
            if self._STYLE_REQUIRE_CHARACTER_MENTION:
                if not mentions:
                    continue
            elif not mentions and score < self._STYLE_MIN_SCORE:
                continue
            kept.append(hit)
        return kept

    async def _retrieve_style_samples(self, context: str) -> str:
        """Retrieve character-owned dialogue turns as style anchors.

        Card samples already live in the system prompt. Dynamic style uses a
        speaker-filtered turn pool (not whole multi-speaker blocks).
        """
        from src.application.novel.qa_expand import looks_like_fact_question

        mode = (self._STYLE_MODE or "pool_turn").strip().lower()
        if mode in {"off", "none", "card_only"}:
            return ""
        if self._STYLE_SKIP_ON_FACT_QUESTION and looks_like_fact_question(context or ""):
            return ""

        if mode == "legacy_block":
            return await self._retrieve_style_samples_legacy(context)

        name_list = sorted(self._style_name_set())
        query = self._style_search_query(context)
        try:
            hits = await self._store.search(
                query,
                channel="dialogue",
                top_k=self._STYLE_FETCH_K,
                doc_id=self.doc_id,
                filters={"characters": name_list} if name_list else None,
            )
        except Exception as e:
            logger.warning("Style sample retrieval failed: %s", e)
            return ""

        turns = self._extract_character_style_turns(hits or [])
        if not turns:
            # Retry without metadata filter if LIKE prefilter was too strict.
            try:
                hits = await self._store.search(
                    query,
                    channel="dialogue",
                    top_k=self._STYLE_FETCH_K,
                    doc_id=self.doc_id,
                )
            except Exception as e:
                logger.warning("Style sample fallback retrieval failed: %s", e)
                return ""
            turns = self._extract_character_style_turns(hits or [])

        if not turns:
            return ""

        cites = [
            Citation(
                channel="dialogue",
                score=float(t["score"]),
                similarity=float(t["score"]) if float(t["score"]) >= 0.15 else None,
                doc_id=str(t["doc_id"]),
                block_id=str(t["block_id"]),
                chapter_title=str(t["chapter_title"]),
                snippet=f"[{t['speaker']}] {t['content']}"[:400],
                role="style",
            )
            for t in turns
        ]
        self._append_citations(cites, role="style")

        lines = ["## 口吻参考（仅说话方式，勿当设定事实）"]
        for i, t in enumerate(turns, 1):
            lines.append(f"{i}. [{t['speaker']}] {t['content']}")
        return "\n".join(lines)

    async def _retrieve_style_samples_legacy(self, context: str) -> str:
        """Previous block-level style retrieval (rollback via style_mode=legacy_block)."""
        try:
            query = f"{self.character} {context}" if context else self.character
            hits = await self._store.search(
                query,
                channel="dialogue",
                top_k=self._STYLE_TOP_K,
                doc_id=self.doc_id,
            )
        except Exception as e:
            logger.warning("Style sample retrieval failed: %s", e)
            return ""

        hits = self._filter_style_hits(hits or [])
        citations = [_hit_to_citation(h, channel="dialogue") for h in hits]
        self._append_citations(citations, role="style")

        lines = []
        if hits:
            lines.append("## 口吻参考（仅说话方式，勿当设定事实）")
            for i, hit in enumerate(hits, 1):
                block = hit.block
                scene = block.scene or "未知场景"
                lines.append(f"\n样本 {i} — {scene}")
                for t in block.dialogues[:5]:
                    lines.append(f"  [{t.speaker}] {t.content}")
        return "\n".join(lines)

    async def _retrieve_fact_context(self, context: str) -> str:
        """全量接入完整检索链路（生产主线，用户拍板）。

        完整链路 = EntityResolver → QueryRewrite(LLM 5 变体) → LLM 意图路由
                  → 多变体 × 多通道混合检索 → 跨变体 RRF → BGE rerank。

        完整链路不可用（未注入 / 异常）时，按 config impersonation.legacy_fallback
        （默认 false）决定是否回退旧轻链路（单通道 store.search）；关闭时返回空。
        """
        if self._retrieval is not None:
            try:
                return await self._retrieve_fact_full_chain(context)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Full-chain fact retrieval failed (%s); falling back to legacy light chain",
                    exc,
                )
                if not _legacy_fallback_enabled():
                    return ""
        return await self._retrieve_fact_context_legacy(context)

    async def _retrieve_fact_full_chain(self, context: str) -> str:
        """完整链路实现：search_raw → Citation(role=fact) → 角色扮演友好注入文本。"""
        from src.domain.novel.models import (
            BLOCK_CHARACTER,
            BLOCK_DIALOGUE,
            BLOCK_NARRATIVE,
            BLOCK_QA,
        )

        query = (context or "").strip() or self.character
        # 已知角色 = 扮演角色 + 角色卡别名（供 Rewrite 补全名 / Router 提取角色）
        available = sorted(self._style_name_set())
        series_id = ""
        if self._card:
            series_id = str(getattr(self._card, "series_id", "") or "").strip()

        intent, hits = await self._retrieval.search_raw(
            query,
            doc_id=self.doc_id,
            available_characters=available or None,
            series_id=series_id or None,
        )
        if not hits:
            return ""

        # ── Parent 上下文展开：命中 Child/小块 → 展开到同章 Parent ±邻居，
        # 让扮演拿到大段原文（hierarchy 双粒度已入库；此前 search_raw 不展开，
        # 角色只能看到 ~200 字碎片）。引用仍保留原始命中块（精准定位），
        # 注入文本使用展开上下文。──
        expanded_narr: dict[str, str] = {}
        try:
            from src.application.novel.narrative_expand import expand_narrative_hits

            narr_hits = [
                h for h in hits
                if getattr(getattr(h, "block", None), "block_type", "") == BLOCK_NARRATIVE
            ]
            if narr_hits:
                for ex in expand_narrative_hits(
                    self._store,
                    narr_hits,
                    radius=1,
                    max_expanded_chars=3500,
                    chapter_hard_boundary=True,
                ):
                    if ex.text and ex.primary_id:
                        expanded_narr[ex.primary_id] = ex.text
        except Exception:  # noqa: BLE001 - 展开失败不影响原始引用
            logger.debug("Narrative parent expand failed; using raw blocks", exc_info=True)

        # ── Citation（前端事实依据展示，role=fact）──
        cites = [
            _hit_to_citation(h, channel=h.channel or None)
            for h in hits[:8]
            if getattr(h, "block", None) is not None
        ]
        if cites:
            self._append_citations(cites, role="fact")

        # ── 注入文本（保留角色扮演友好的「## 原著参考」风格）──
        lines = [
            "## 原著参考（检索原文）",
            "以下内容仅供参考；结论以原文为准，原文未写明的细节请明确表示不确定。",
        ]
        seen_refs: set[str] = set()
        for i, h in enumerate(hits[:6], 1):
            block = getattr(h, "block", None)
            if block is None:
                continue
            source = str(
                getattr(block, "source", "")
                or getattr(block, "chapter_title", "")
                or getattr(block, "scene", "")
                or getattr(block, "source_work", "")
                or f"第{i}段"
            )
            btype = getattr(block, "block_type", "")
            lines.append(f"\n{source}：")

            if btype == BLOCK_NARRATIVE:
                # 优先用展开后的 Parent 上下文（大段原文）；无展开时回退原始块
                expanded = expanded_narr.get(str(getattr(block, "global_id", "") or ""))
                text = (expanded or str(getattr(block, "narrative_text", "") or "")).strip()
                lines.append(text[:3500] if text else "（空）")
            elif btype == BLOCK_DIALOGUE:
                dialogues = list(getattr(block, "dialogues", None) or [])[:6]
                scene = str(getattr(block, "scene", "") or "")
                if scene:
                    lines.append(f"场景: {scene}")
                for t in dialogues:
                    sp = str(getattr(t, "speaker", "") or "")
                    ct = str(getattr(t, "content", "") or "")
                    lines.append(f"  [{sp}] {ct}" if sp else f"  {ct}")
            elif btype == BLOCK_CHARACTER:
                try:
                    from src.application.novel.character_channel_index import (
                        format_relation_event_clue,
                        is_relation_event_block,
                    )
                except Exception:  # noqa: BLE001
                    is_relation_event_block = None
                    format_relation_event_clue = None
                if is_relation_event_block and is_relation_event_block(block):
                    clue = (
                        format_relation_event_clue(block, clip=300)
                        if format_relation_event_clue
                        else ""
                    )
                    lines.append(f"线索: {clue}" if clue else "（关系/事件索引）")
                    for rid in list(getattr(block, "ref_chunk_ids", None) or [])[:4]:
                        rid = str(rid or "").strip()
                        if not rid or rid in seen_refs:
                            continue
                        seen_refs.add(rid)
                        try:
                            narr = self._store.get_block(rid)
                        except Exception:  # noqa: BLE001
                            narr = None
                        if narr is None or not getattr(narr, "narrative_text", None):
                            continue
                        lines.append(
                            "原文[%s]: %s"
                            % (
                                str(
                                    getattr(narr, "chapter_title", "")
                                    or getattr(narr, "source", "")
                                    or rid
                                ),
                                str(narr.narrative_text)[:400],
                            )
                        )
                else:
                    name = str(
                        getattr(block, "character_name", "")
                        or (getattr(block, "characters", None) or [""])[0]
                        or ""
                    )
                    text = str(
                        getattr(block, "personality", "")
                        or getattr(block, "narrative_text", "")
                        or ""
                    )
                    lines.append(f"角色: {name}" if name else "（角色块）")
                    if text:
                        lines.append(text[:400])
            elif btype == BLOCK_QA:
                q = str(getattr(block, "question", "") or "")
                a = str(getattr(block, "answer", "") or "")
                lines.append(f"Q: {q}" if q else "")
                lines.append(f"A: {a[:600]}" if a else "")
            else:
                text = str(
                    getattr(block, "narrative_text", "")
                    or getattr(block, "question", "")
                    or ""
                )
                lines.append((text or "（无文本）")[:400])

        return "\n".join(lines)

    async def _retrieve_fact_context_legacy(self, context: str) -> str:
        """旧分型轻链路（完整链路关闭/不可用时的回退）。

        Prefer relation/event index for relationship questions; else QA→narrative.
        """
        from src.application.novel.character_channel_index import looks_like_relation_question
        from src.application.novel.qa_expand import (
            format_expanded_hits,
            looks_like_fact_question,
            search_qa_with_narratives,
        )

        if looks_like_relation_question(context or ""):
            try:
                rel_text = await self._retrieve_relation_event_context(context)
                if rel_text:
                    return rel_text
                # Dual-entity / relation ask with no evidence → refuse (NO_FACT_HINT).
                return ""
            except Exception as e:
                logger.warning("Relation/event fact retrieval failed: %s", e)

        # Fact questions: prefer user wording so character name does not dominate.
        if looks_like_fact_question(context or ""):
            query = (context or "").strip() or self.character
        else:
            query = f"{self.character} {context}" if context else self.character

        if looks_like_fact_question(context or ""):
            try:
                expanded = await search_qa_with_narratives(
                    self._store,
                    query,
                    top_k=self._NARRATIVE_TOP_K,
                    doc_id=self.doc_id,
                )
                if expanded and (
                    any(h.narratives for h in expanded)
                    or any(getattr(h.qa, "answer", None) for h in expanded)
                ):
                    cites: list[Citation] = []
                    for h in expanded:
                        cites.append(_hit_to_citation(
                            type("H", (), {"block": h.qa, "score": h.score, "channel": "qa"})(),
                            channel="qa",
                        ))
                        for narr in h.narratives:
                            cites.append(_hit_to_citation(
                                type("H", (), {
                                    "block": narr,
                                    "score": h.score,
                                    "channel": "narrative",
                                })(),
                                channel="narrative",
                            ))
                    self._append_citations(cites, role="fact")
                    return format_expanded_hits(query, expanded, excerpt_chars=300)
            except Exception as e:
                logger.warning("QA→narrative expand failed, fallback narrative: %s", e)

        return await self._retrieve_narrative_context(context)

    async def _retrieve_relation_event_context(self, context: str) -> str:
        """Search character-channel relation/event clues, then expand narrative evidence."""
        from src.application.novel.character_channel_index import (
            block_covers_entities,
            entities_mentioned,
            format_relation_event_clue,
            is_relation_event_block,
        )

        query = (context or "").strip() or self.character
        search_q = query
        if self.character and self.character not in search_q:
            search_q = f"{self.character} {query}"

        known: list[str] = []
        try:
            known = list(self._store.list_characters(doc_id=self.doc_id) or [])
        except Exception:
            known = []
        if self.character and self.character not in known:
            known.append(self.character)
        if self._card:
            for alias in getattr(self._card, "aliases", None) or []:
                if alias and alias not in known:
                    known.append(alias)
            name = getattr(self._card, "name", "") or ""
            if name and name not in known:
                known.append(name)

        entities = entities_mentioned(query, known)
        if self.character and self.character not in entities:
            entities = [self.character] + entities

        try:
            hits = await self._store.search(
                search_q,
                channel="character",
                top_k=max(5, self._NARRATIVE_TOP_K * 2),
                doc_id=self.doc_id,
            )
        except Exception as e:
            logger.warning("Character-channel relation search failed: %s", e)
            return ""

        filtered = []
        for hit in hits or []:
            block = getattr(hit, "block", None)
            if block is None or not is_relation_event_block(block):
                continue
            if not block_covers_entities(block, entities):
                continue
            filtered.append(hit)

        if not filtered:
            return ""

        lines = [
            "## 原著参考（关系/事件线索 → 原文）",
            "以下线索仅供定位；结论必须以原文为依据。线索未覆盖的时间线/关系演变请明确不确定。",
        ]
        cites: list[Citation] = []
        seen_refs: set[str] = set()
        evidence_found = False

        for hit in filtered[:4]:
            block = hit.block
            lines.append(f"\n线索: {format_relation_event_clue(block, clip=240)}")
            clue_cite = _hit_to_citation(hit, channel="character", excerpt=240)
            cites.append(clue_cite)
            clue_sim = clue_cite.similarity
            for rid in list(getattr(block, "ref_chunk_ids", None) or [])[:5]:
                rid = str(rid or "").strip()
                if not rid or rid in seen_refs:
                    continue
                seen_refs.add(rid)
                narr = None
                try:
                    narr = self._store.get_block(rid)
                except Exception:
                    narr = None
                if narr is None or not getattr(narr, "narrative_text", None):
                    continue
                evidence_found = True
                source = (
                    getattr(narr, "chapter_title", "")
                    or getattr(narr, "source", "")
                    or rid
                )
                text = (narr.narrative_text or "")[:1200]
                lines.append(f"\n原文[{source}]:\n{text}")
                cites.append(Citation(
                    channel="narrative",
                    score=float(clue_sim) if clue_sim is not None else 0.0,
                    similarity=clue_sim,
                    doc_id=str(getattr(narr, "doc_id", "") or self.doc_id or ""),
                    block_id=str(getattr(narr, "global_id", "") or rid),
                    chapter_title=str(source),
                    snippet=text[:400],
                    role="fact",
                ))

        if not evidence_found:
            # Clues without narrative evidence are not enough to answer.
            return ""

        self._append_citations(cites, role="fact")
        return "\n".join(lines)

    async def _retrieve_narrative_context(self, context: str) -> str:
        from src.application.novel.qa_expand import looks_like_fact_question

        try:
            if looks_like_fact_question(context or ""):
                query = (context or "").strip() or self.character
            else:
                query = f"{self.character} {context}" if context else self.character
            hits = await self._store.search(
                query,
                channel="narrative",
                top_k=self._NARRATIVE_TOP_K,
                doc_id=self.doc_id,
            )
        except Exception as e:
            logger.warning("Narrative context retrieval failed: %s", e)
            return ""

        if not hits:
            return ""

        try:
            from src.application.novel.narrative_expand import expand_narrative_hits

            expanded = expand_narrative_hits(self._store, hits, radius=1, max_expanded_chars=3000)
        except Exception:
            expanded = None

        lines = ["## 原著参考（叙事原文）"]
        cites: list[Citation] = []
        if expanded:
            # 按 primary_id 对齐展开项与原始命中，避免展开去重/合并后
            # 与 hits 按位置错位（旧实现按索引 i-1 对齐，数量不一致时引用错乱）
            hit_by_id = {
                getattr(getattr(h, "block", None), "global_id", ""): h for h in hits
            }
            for i, ex in enumerate(expanded, 1):
                source = ex.chapter_title or f"第{i}段"
                text = (ex.text or "")[:1200]
                lines.append(f"\n{source}:\n{text}")
                hit = hit_by_id.get(getattr(ex, "primary_id", ""))
                if hit is not None:
                    cites.append(
                        _hit_to_citation(hit, channel="narrative", excerpt=400)
                    )
                else:
                    # 无对应原始命中（合并产生的新展开块）：用展开块自身元数据
                    ex_score = float(getattr(ex, "score", 0.0) or 0.0)
                    cites.append(Citation(
                        channel="narrative",
                        score=ex_score,
                        similarity=ex_score if ex_score > 0 else None,
                        doc_id=str(getattr(ex, "doc_id", "") or ""),
                        block_id=str(getattr(ex, "primary_id", "") or ""),
                        chapter_title=str(source),
                        snippet=text[:400],
                        role="fact",
                    ))
        else:
            for i, hit in enumerate(hits, 1):
                block = hit.block
                source = block.source or f"第{i}段"
                text = block.narrative_text[:1200]
                lines.append(f"\n{source}:\n{text}")
                cites.append(_hit_to_citation(hit, channel="narrative"))
        self._append_citations(cites, role="fact")
        return "\n".join(lines)


# ── Factory ─────────────────────────────────────────────────

