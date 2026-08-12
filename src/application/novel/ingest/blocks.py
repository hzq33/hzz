"""Ingest Phase 3 — content structuring: narrative/dialogue/qa/character blocks.

Extracted from the former monolithic ``ingest.py``; each stage is an
independent function so it can be unit-tested in isolation.

Stages (all mutate/return the shared ``Phase3Context``):
  3a  narrative blocks (Parent/Child hierarchy)
  3a2 character inventory (CLUENER + LLM normalize)
  3b  dialogue blocks (span recall + short-window LLM attribution)
  3c  QA blocks (character-overlap narrative sources)
  3d  character blocks + roster (L1 default; batch LLM optional)
  3e  character relationship graph
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.application.novel.ingest.convert import (
    _build_shared_llm,
    _local_llm_config,
    _qa_config,
    _select_narrative_for_qa,
)

logger = logging.getLogger("agent")


class IngestAbortError(Exception):
    """Fatal ingest failure — abort pipeline with an error result."""


@dataclass
class Phase3Context:
    """Shared state produced/consumed across Phase 3 stages."""

    narrative_blocks: list = field(default_factory=list)
    narrative_parents: list = field(default_factory=list)
    dialogue_blocks: list = field(default_factory=list)
    qa_blocks: list = field(default_factory=list)
    character_blocks: list = field(default_factory=list)
    roster_names: list[str] = field(default_factory=list)
    inventory_result: Any = None
    inventory_candidates: list[str] = field(default_factory=list)
    graph: Any = None
    cleaner: Any = None
    all_blocks: list = field(default_factory=list)


def narrative_known_characters(inventory_result, series_id: str) -> list[str]:
    """Full character name list for narrative all_person tagging.

    Inventory full candidates (not just the LLM seed) ∪ series-level
    alias-map entities — so narrative blocks are tagged at chunk time with
    the complete name set, including pure-narrative characters that never
    speak. Order preserved, deduped.
    """
    names: list[str] = []
    if inventory_result is not None:
        chars = getattr(inventory_result, "characters", None) or []
        for c in chars:
            n = str(getattr(c, "canonical_name", "") or "").strip()
            if n:
                names.append(n)
    try:
        from src.domain.novel.alias_map import load_alias_map

        amap = load_alias_map(series_id)
        if amap is not None:
            for e in getattr(amap, "entities", None) or []:
                n = str(getattr(e, "canonical_name", "") or "").strip()
                if n:
                    names.append(n)
    except Exception as e:
        logger.debug("series alias map unavailable for narrative tagging: %s", e)
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


async def build_narrative_blocks(
    document, doc_id: str, mime_type: str, known_characters: list[str] | None = None
) -> tuple[list, list, Any]:

    # 3a. Narrative blocks (Parent/Child hierarchy when enabled)
    narrative_blocks: list = []
    narrative_parents: list = []
    try:
        from src.domain.novel.chunker import MDCleaner, chunk_narrative_for_ingest

        cleaner = MDCleaner()
        from src.application.novel.factory import _load_raw_config
        nr = _load_raw_config().get("novel_rag", {})
        hier_cfg = dict(nr.get("narrative_hierarchy") or {})
        # Default hierarchy on when section present; allow explicit enabled:false
        if "enabled" not in hier_cfg:
            hier_cfg["enabled"] = True

        for chapter_index, chapter in enumerate(document.chapters):
            cleaned = cleaner.clean(chapter.text, doc_id=doc_id)
            cleaned.chapter_title = chapter.title
            cleaned.source_prefix = f"《{document.title}》" if document.title else ""
            blocks = chunk_narrative_for_ingest(
                cleaned,
                doc_id=doc_id,
                chapter_index=chapter_index,
                hierarchy=hier_cfg,
                flat_chunk_size=int(nr.get("chunk_size", 500)),
                flat_overlap=int(nr.get("chunk_overlap", 50)),
                known_characters=known_characters,
            )
            for b in blocks:
                b.chapter_title = chapter.title
            narrative_blocks.extend(blocks)

        narrative_parents = [
            b for b in narrative_blocks if (getattr(b, "granularity", "") or "") != "child"
        ]
        if not narrative_parents:
            narrative_parents = list(narrative_blocks)

        if not narrative_blocks:
            raise IngestAbortError(
                "No narrative blocks extracted — document may be too short"
            )
        n_child = sum(1 for b in narrative_blocks if getattr(b, "granularity", "") == "child")
        n_parent = sum(1 for b in narrative_blocks if getattr(b, "granularity", "") == "parent")
        logger.info(
            "Extracted %d narrative blocks (%d parent, %d child) from %d chapters",
            len(narrative_blocks),
            n_parent,
            n_child,
            document.total_chapters,
        )
        return narrative_blocks, narrative_parents, cleaner
    except IngestAbortError:
        raise
    except Exception as e:
        logger.exception("Narrative chunking failed")
        raise IngestAbortError(f"Narrative chunking failed: {e}") from e




async def build_inventory(document, series_id: str, doc_id: str) -> tuple[Any, list[str]]:

    # 3a2. Character inventory (CLUENER + LLM normalize) — before speaker attribution
    inventory_result = None
    inventory_candidates: list[str] = []
    try:
        from src.domain.novel.character_inventory import (
            _inventory_config,
            build_character_inventory,
            persist_inventory_candidates,
            persist_relations,
            seed_names_from_inventory,
        )

        inv_cfg = _inventory_config()
        if inv_cfg.get("enabled", True) and inv_cfg.get("sync_on_ingest", True):
            # LLM full-scan inventory: 12 万字符输入 + 全局归一输出，需更大超时裕量
            inv_llm = _build_shared_llm(
                temperature=0.0, max_tokens=2048, timeout=240.0, endpoint="character_inventory",
            )
            # 归一用强模型（DeepSeek 默认）：合并/去噪/拆分的复杂裁决
            normalize_llm = _build_shared_llm(
                temperature=0.0, max_tokens=4096, timeout=240.0,
                endpoint="character_inventory_normalize",
            )
            inventory_result = await build_character_inventory(
                document,
                series_id=series_id,
                llm_client=inv_llm,
                normalize_llm_client=normalize_llm,
                config=inv_cfg,
            )
            # Full candidates persisted; LLM seed = median (or fixed) frequency filter
            try:
                persist_inventory_candidates(
                    series_id=series_id,
                    doc_id=doc_id,
                    inventory=inventory_result,
                )
            except Exception as pe:
                logger.warning("Inventory persist failed: %s", pe)
            if inventory_result.relations:
                try:
                    persist_relations(series_id, inventory_result.relations)
                except Exception as pe:
                    logger.warning("Relations persist failed: %s", pe)
            inventory_candidates = seed_names_from_inventory(inventory_result)
            if inv_llm is not None:
                try:
                    await inv_llm.close()
                except Exception:
                    pass
            if normalize_llm is not None:
                try:
                    await normalize_llm.close()
                except Exception:
                    pass
            logger.info(
                "Inventory candidates=%d seed=%d meta=%s",
                len(inventory_result.characters),
                len(inventory_candidates),
                inventory_result.meta,
            )
    except Exception as e:
        logger.warning("Character inventory skipped: %s", e)
    return inventory_result, inventory_candidates




async def build_dialogue_blocks(document, doc_id: str, narrative_parents: list, inventory_result, inventory_candidates: list[str], series_id: str, cleaner) -> list:

    # 3b. Dialogue blocks — span召回 + 短窗LLM归因（默认）；可关回旧正则路径
    dialogue_blocks: list = []
    try:
        from src.application.novel.dialogue_pipeline import (
            _attr_config,
            extract_dialogue_for_document,
        )

        attr_cfg = _attr_config()
        use_attr = bool(attr_cfg.get("enabled", True))

        if use_attr:
            llm_client = None
            provider = str(attr_cfg.get("provider") or "cloud_chapter").lower()
            from src.application.novel.dialogue_pipeline import _provider_needs_llm

            if _provider_needs_llm(provider) and provider != "off":
                # chapter / cloud extract needs larger completion budget
                max_tok = int(attr_cfg.get("max_output_tokens", 4096))
                llm_client = _build_shared_llm(
                    temperature=0.0, max_tokens=max_tok, endpoint="dialogue_extract",
                )
            inv_chars = None
            if inventory_result is not None and inventory_result.characters:
                # Quota needs full inventory (with mention_count) so TopN → main works.
                # Seed list stays frequency-filtered for the LLM candidate prompt only.
                inv_chars = list(inventory_result.characters)
            # Always union series sidecar candidates: cluster_fallback may miss stable
            # canonicals (利姆露) that already exist on disk with rich aliases.
            if series_id:
                try:
                    from src.domain.novel.character_inventory import (
                        load_inventory_candidates,
                        merge_volume_and_series_for_quota,
                    )

                    persisted = load_inventory_candidates(series_id) or {}
                    cands = list(persisted.get("candidates") or [])
                    if cands or inv_chars:
                        before = len(inv_chars or [])
                        inv_chars = merge_volume_and_series_for_quota(inv_chars, cands)
                        logger.info(
                            "Dialogue quota inventory merged volume=%d series=%d → %d",
                            before,
                            len(cands),
                            len(inv_chars),
                        )
                    if not inventory_candidates:
                        inventory_candidates = [
                            str(n).strip()
                            for n in (persisted.get("seed_names") or [])
                            if str(n).strip()
                        ]
                except Exception as pe:
                    logger.debug("persisted inventory merge skipped: %s", pe)
            pipe = await extract_dialogue_for_document(
                document,
                doc_id,
                ref_narrative_id=narrative_parents[0].global_id if narrative_parents else "",
                llm_client=llm_client,
                volume_seed=inventory_candidates or None,
                inventory_characters=inv_chars,
                config=attr_cfg,
            )
            dialogue_blocks = list(pipe.blocks)
            if llm_client is not None:
                try:
                    await llm_client.close()
                except Exception:
                    pass
            logger.info(
                "Dialogue attribution pipeline: blocks=%d meta=%s",
                len(dialogue_blocks),
                pipe.meta,
            )
            try:
                from src.domain.novel.dialogue_meta_store import save_dialogue_meta

                save_dialogue_meta(doc_id, pipe.meta)
            except Exception as e:
                logger.warning("Failed to persist dialogue meta for %s: %s", doc_id, e)

        if not dialogue_blocks:
            # Legacy regex path (also used when attribution disabled or produced nothing)
            from src.domain.novel.dialogue import DialogueExtractor
            from src.domain.novel.models import NovelBlock

            if not use_attr:
                logger.info("Dialogue attribution disabled — using legacy DialogueExtractor")
            else:
                logger.info("Attribution produced 0 blocks — legacy DialogueExtractor fallback")

            rx = DialogueExtractor()
            for chapter_index, chapter in enumerate(document.chapters):
                cleaned = cleaner.clean(chapter.text, doc_id=doc_id)
                cleaned.chapter_title = chapter.title
                if cleaned.dialogue_blocks:
                    for i, block_text in enumerate(cleaned.dialogue_blocks):
                        dialogue_blocks.append(rx.extract_to_block(
                            block_text,
                            scene=cleaned.scenes[i] if i < len(cleaned.scenes) else "",
                            doc_id=doc_id, chapter_title=chapter.title,
                            ref_narrative_id=narrative_parents[0].global_id if narrative_parents else "",
                            chapter_index=chapter_index,
                            block_index=i,
                        ))
                else:
                    turns = rx.extract(chapter.text, chapter_title=chapter.title, doc_id=doc_id)
                    if turns:
                        speakers = list({t.speaker for t in turns if t.speaker != "未知"})
                        all_d = " ".join(t.content for t in turns)
                        dialogue_blocks.append(NovelBlock(
                            global_id=f"{doc_id}_c{chapter_index:03d}_d0000",
                            doc_id=doc_id,
                            chapter_title=chapter.title,
                            block_type="dialogue",
                            dialogues=turns,
                            characters=speakers,
                            vec_text_dialogue=all_d,
                            all_person=speakers,
                            token_length=len(all_d),
                        ))

            # Legacy Qwen enhance only when attribution is off
            local_cfg = _local_llm_config()
            if (not use_attr) and local_cfg.get("enabled") and dialogue_blocks:
                try:
                    from src.domain.novel.dialogue_local_llm import (
                        LocalLLMDialogueExtractor,
                        is_noise_speaker,
                    )
                    dlg_cfg = local_cfg.get("dialogue") or {}
                    threshold = float(dlg_cfg.get("unknown_rate_threshold", 0.3))
                    trigger = dlg_cfg.get("trigger_mode", "unknown_rate")
                    if trigger != "never":
                        extractor = LocalLLMDialogueExtractor(
                            model_path=local_cfg.get(
                                "model_path",
                                "models/Haruhi-Dialogue-Speaker-Extract_qwen18",
                            ),
                            device=local_cfg.get("device", "cuda"),
                            quantize=local_cfg.get("quantize", "4bit"),
                            mode="official",
                        )
                        extractor.load()
                        chapter_texts = {c.title: c.text for c in document.chapters}
                        enhanced = 0
                        for block in dialogue_blocks:
                            turns = list(block.dialogues or [])
                            if not turns:
                                continue
                            noise_rate = sum(
                                1 for t in turns if is_noise_speaker(t.speaker)
                            ) / len(turns)
                            if trigger == "always" or noise_rate > threshold:
                                text = chapter_texts.get(block.chapter_title) or "\n".join(
                                    t.content for t in turns
                                )
                                new_turns = await extractor.enhance(
                                    turns,
                                    text,
                                    chapter_title=block.chapter_title or "",
                                    unknown_threshold=(
                                        -1.0 if trigger == "always" else threshold
                                    ),
                                )
                                if new_turns is not turns:
                                    block.dialogues = new_turns
                                    block.characters = list({
                                        t.speaker for t in new_turns
                                        if not is_noise_speaker(t.speaker)
                                    })
                                    enhanced += 1
                        logger.info(
                            "Qwen speaker enhance: %d/%d dialogue blocks updated",
                            enhanced, len(dialogue_blocks),
                        )
                        extractor.unload()
                except Exception as e:
                    logger.warning("LocalLLM dialogue enhance skipped: %s", e)

        # DeepSeek fallback only when still nothing
        if not dialogue_blocks:
            from src.domain.novel.dialogue_llm import LLMDialogueExtractor
            llm_extractor = _build_shared_llm(
                temperature=0.0, max_tokens=2048, endpoint="dialogue_extract",
            )
            if llm_extractor is not None:
                logger.info("No dialogue blocks — DeepSeek full extract fallback")
                llm_de = LLMDialogueExtractor(llm_extractor)
                for chapter in document.chapters:
                    try:
                        blocks = await llm_de.extract_batch_to_blocks(
                            chapter.text,
                            chapter_title=chapter.title,
                            doc_id=doc_id,
                        )
                        if blocks:
                            dialogue_blocks.extend(blocks)
                    except Exception as e:
                        logger.warning(
                            "DeepSeek dialogue failed for '%s': %s", chapter.title, e,
                        )
                await llm_extractor.close()

        logger.info("Extracted %d dialogue blocks", len(dialogue_blocks))
        return dialogue_blocks
    except Exception as e:
        logger.warning("Dialogue extraction failed: %s", e)
        return []




async def build_qa_blocks(narrative_parents: list, dialogue_blocks: list, generate_qa: bool) -> list:

    # 3c. QA blocks — 角色相关 narrative 优先，经 ref 指向叙事
    qa_blocks: list = []
    if generate_qa:
        try:
            from src.domain.novel.dialogue_span import is_noise_speaker as _is_noise
            from src.domain.novel.qa_generator import QAGenerator

            qa_cfg = _qa_config()
            if not qa_cfg.get("enabled", True):
                logger.info("QA generation disabled by config")
            else:
                prelim_names: list[str] = []
                for block in dialogue_blocks:
                    for turn in (block.dialogues or []):
                        sp = turn.speaker
                        if sp and not _is_noise(sp) and sp not in prelim_names:
                            prelim_names.append(sp)

                max_blocks = int(qa_cfg.get("max_source_blocks", 30))
                per_block = int(qa_cfg.get("per_block", 2))
                prefer = bool(qa_cfg.get("prefer_character_overlap", True))
                sources = _select_narrative_for_qa(
                    narrative_parents,
                    prelim_names,
                    max_blocks=max_blocks,
                    prefer_character_overlap=prefer,
                )

                qa_llm = _build_shared_llm(
                    temperature=0.3, max_tokens=1024, endpoint="qa_generator",
                )
                qa_gen = QAGenerator(llm_client=qa_llm, known_characters=prelim_names)
                for b in sources:
                    qa_blocks.extend(await qa_gen.generate(b, count=per_block))

                logger.info(
                    "Generated %d QA blocks from %d/%d narrative parents (llm=%s, names=%d)",
                    len(qa_blocks),
                    len(sources),
                    len(narrative_parents),
                    qa_llm is not None,
                    len(prelim_names),
                )
        except Exception as e:
            logger.warning("QA generation skipped: %s", e)
    return qa_blocks




async def build_character_blocks(document, doc_id: str, series_id: str, inventory_result, dialogue_blocks: list, narrative_blocks: list, persona_llm, generate_character_llm: bool) -> tuple[list, list[str]]:

    # 3d. Character — default: L1 roster only (no batch LLM).
    # Set generate_character_llm=True for legacy unified extraction + character blocks.
    character_blocks: list = []
    roster_names: list[str] = []
    try:
        from src.application.novel.dialogue_pipeline import _attr_config
        from src.domain.novel.character_roster import (
            build_roster_from_dialogue_blocks,
            save_roster,
        )
        from src.domain.novel.dialogue_span import is_noise_speaker

        roster_min = float((_attr_config() or {}).get("roster_min", 0.5))

        if inventory_result is not None and inventory_result.characters:
            from src.domain.novel.character_inventory import persist_inventory_roster

            roster = persist_inventory_roster(
                series_id=series_id,
                doc_id=doc_id,
                inventory=inventory_result,
                dialogue_blocks=dialogue_blocks,
            )
            roster_names = [e.name for e in roster.characters]
            logger.info(
                "Roster L1 from inventory for series=%s doc=%s: %d characters",
                series_id, doc_id, len(roster_names),
            )
        else:
            roster = build_roster_from_dialogue_blocks(
                series_id=series_id,
                doc_id=doc_id,
                dialogue_blocks=dialogue_blocks,
                narrative_blocks=narrative_blocks,
                min_dialogues=3,
                min_confidence=roster_min,
                is_noise_speaker=is_noise_speaker,
            )
            save_roster(roster)
            roster_names = [e.name for e in roster.characters]
            try:
                from src.domain.novel.alias_map import build_alias_map_from_roster
                build_alias_map_from_roster(series_id, roster)
            except Exception as e:
                logger.warning("AliasMap build skipped: %s", e)
            logger.info(
                "Roster L1 written for series=%s doc=%s: %d candidates",
                series_id, doc_id, len(roster_names),
            )

        if not generate_character_llm:
            logger.info(
                "Skipping batch character LLM (generate_character_llm=false); "
                "use POST /characters/build for on-demand cards"
            )
        else:
            # Collect characters from dialogue blocks (skip noise speakers)
            char_dialogues: dict[str, dict] = {}
            char_narratives: dict[str, list[str]] = {}
            for block in dialogue_blocks:
                for turn in block.dialogues:
                    sp = turn.speaker
                    if is_noise_speaker(sp):
                        continue
                    if sp not in char_dialogues:
                        char_dialogues[sp] = {"dialogues": [], "count": 0}
                    char_dialogues[sp]["dialogues"].append(turn.content)
                    char_dialogues[sp]["count"] += 1

            MIN_DIALOGUES = 3
            main_chars = {
                name: info for name, info in char_dialogues.items()
                if info["count"] >= MIN_DIALOGUES
            }
            logger.info(
                "Characters: %d total -> %d main (>=%d dialogues), filtered %d marginal",
                len(char_dialogues), len(main_chars), MIN_DIALOGUES,
                len(char_dialogues) - len(main_chars),
            )
            char_dialogues = main_chars

            for block in narrative_blocks:
                text = " ".join(block.vec_text_narrative.split() if hasattr(block, 'vec_text_narrative') else [])
                if not text:
                    continue
                for name in char_dialogues:
                    if name in text and name not in char_narratives:
                        char_narratives[name] = []
                    if name in text:
                        idx = text.find(name)
                        ctx = text[max(0, idx-30):idx+len(name)+30].replace("\n", " ")
                        char_narratives.setdefault(name, []).append(f"...{ctx}...")

            if char_dialogues and persona_llm:
                try:
                    from src.domain.novel.character_unified import extract_characters_unified
                    logger.info(
                        "Unified character extraction: %d characters via DeepSeek",
                        len(char_dialogues),
                    )
                    profiles = await extract_characters_unified(
                        persona_llm, doc_id, char_dialogues,
                        narratives=char_narratives if char_narratives else None,
                    )
                    logger.info("Unified extraction returned %d profiles", len(profiles))
                except Exception as e:
                    logger.warning("Unified character extraction failed: %s", e)
                    profiles = {}
            else:
                profiles = {}

            from src.domain.novel.character_builder import CharacterBuilder
            from src.domain.novel.models import (
                BLOCK_CHARACTER,
                NovelBlock,
                PersonalityProfile,
            )
            builder = CharacterBuilder()

            for name, info in char_dialogues.items():
                profile = profiles.get(name, {})

                if profile and "traits" in profile:
                    traits = profile.get("traits", {})
                    pp = PersonalityProfile(
                        traits={k: round(float(v), 2) for k, v in traits.items()
                                if isinstance(v, (int, float))},
                        speech_patterns=None,
                        catchphrases=profile.get("catchphrases", []),
                        emotional_tendencies=profile.get("emotional_tendencies", ""),
                    )
                else:
                    pp = builder._fallback_personality_profile(
                        name,
                        narrative_snippets=char_narratives.get(name, []),
                        dialogue_contents=info["dialogues"],
                    )

                personality = builder._profile_to_legacy_personality(pp) if pp else ""
                speech_style = builder._profile_to_legacy_speech_style(pp) if pp else ""

                from src.domain.character_card import _curate_dialogue_samples
                raw_samples = [
                    {"speaker": name, "content": d, "context": ""}
                    for d in info["dialogues"]
                    if isinstance(d, str) and d.strip()
                ]
                curated = _curate_dialogue_samples(raw_samples, name, max_n=8)
                sample_texts = [s["content"] for s in curated] or info["dialogues"][:5]

                vec_parts = [name]
                if personality:
                    vec_parts.append(f"性格：{personality}")
                if speech_style:
                    vec_parts.append(f"说话风格：{speech_style}")
                vec_parts.extend(sample_texts[:3])
                vec_text = " ".join(vec_parts)

                block = NovelBlock(
                    global_id=f"{doc_id}_char_{name}",
                    doc_id=doc_id,
                    source=f"《{doc_id}》角色",
                    block_type=BLOCK_CHARACTER,
                    character_name=name,
                    personality=personality,
                    speech_style=speech_style,
                    personality_profile=pp,
                    sample_dialogues=sample_texts,
                    vec_text_character=vec_text,
                    token_length=len(vec_text),
                )
                character_blocks.append(block)

            logger.info("Built %d character blocks", len(character_blocks))
    except Exception as e:
        logger.warning("Character / roster building failed: %s", e)
    return character_blocks, roster_names




def _load_series_alias_map(doc_id: str) -> dict[str, list[str]] | None:
    """从 series roster/alias.json 构建 canonical → aliases 映射。

    图谱说话人归一依赖它把短名（八奈见/白玉同学）映射到 canonical，
    避免同一角色分裂成多个节点。文件缺失/为空时返回 None（保持旧行为）。
    """
    try:
        from src.api.helpers import series_id_from_doc_id
        from src.api.routers.alias_roster import read_alias

        series_id = series_id_from_doc_id(doc_id)
        if not series_id:
            return None
        data = read_alias(series_id)
        alias_map: dict[str, list[str]] = {}
        for e in data.get("entities") or []:
            canon = str(e.get("canonical_name") or "").strip()
            if canon:
                alias_map[canon] = [
                    str(a).strip()
                    for a in (e.get("aliases") or [])
                    if str(a).strip()
                ]
        return alias_map or None
    except Exception as exc:
        logger.warning("Series alias map load failed for %s: %s", doc_id, exc)
        return None


async def build_graph(character_blocks: list, dialogue_blocks: list, narrative_blocks: list, doc_id: str) -> Any:

    # 3e. Character relationship graph (dialogue/narrative co-occurrence + 事实源关系边)
    # 节点回退自 dialogue 说话人 / narrative all_person，不依赖 character_blocks
    # （generate_character_llm 默认关，character_blocks 通常为空）
    graph = None
    if dialogue_blocks or narrative_blocks:
        try:
            from src.infrastructure.character_graph import CharacterGraph
            from src.application.novel.query_parse import series_id_from_doc_id

            alias_map = _load_series_alias_map(doc_id)
            # P2: 若该系列已跑 story_analysis，关系边从事实源构建（type='relation'）
            relations = None
            try:
                from src.domain.novel.story_analysis.config import load_analysis

                snap = load_analysis(series_id_from_doc_id(doc_id))
                relations = list(snap.relations or []) if snap else None
            except Exception:
                relations = None
            graph = CharacterGraph().build(
                character_blocks=character_blocks,
                dialogue_blocks=dialogue_blocks,
                narrative_blocks=narrative_blocks,
                alias_map=alias_map,
                relations=relations,
            )
            # Persist for later queries
            save_path = f"data/graphs/{doc_id}.json"
            graph.save(save_path)
            logger.info("Built character graph (%s): %d nodes, %d edges → %s",
                doc_id, graph.graph.number_of_nodes(),
                graph.graph.number_of_edges(), save_path)
        except Exception as e:
            logger.exception("Character graph build failed (doc_id=%s): %s", doc_id, e)
    return graph


