"""On-demand character build runner.

Extracted from the former monolithic ``character_on_demand.py``; logic unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from src.domain.novel.character_on_demand.builder import (
    _MIN_DIALOGUES_DEFAULT,
    distill_single,
    gather_evidence,
    normalize_name,
)
from src.domain.novel.character_on_demand.jobs import (
    _get_job_store,
    _now,
    _save_job,
    _to_job_record,
    get_job,
    list_jobs,
)
from src.domain.novel.character_on_demand.models import CharacterBuildJob
from src.domain.novel.character_on_demand.persist import persist_card
from src.domain.novel.character_roster import load_roster

logger = logging.getLogger("agent")


async def run_build_job(
    job: CharacterBuildJob,
    store,
    *,
    llm_client=None,
    force: bool = False,
    min_dialogues: int = _MIN_DIALOGUES_DEFAULT,
    resolve_character_id: str | None = None,
) -> CharacterBuildJob:
    """Execute full pipeline for one job."""
    try:
        job.state = "normalize"
        _save_job(job)
        norm = normalize_name(
            job.input_name,
            series_id=job.series_id,
            resolve_character_id=resolve_character_id,
        )
        if norm.need_disambiguate:
            job.state = "failed"
            job.error = "need_disambiguate"
            job.flags["candidates"] = norm.candidates
            _save_job(job)
            return job

        job.character_id = norm.character_id
        job.canonical_name = norm.canonical_name
        job.aliases = norm.aliases

        from src.domain.character_card import CharacterCard

        if not force:
            cached = CharacterCard.load_for_series(job.series_id, job.canonical_name, character_id=job.character_id)
            if cached and not getattr(cached, "stale", False):
                job.state = "done"
                job.card_path = str(
                    CharacterCard.cache_path_for(job.series_id, job.canonical_name, character_id=job.character_id)
                )
                job.flags["cache_hit"] = True
                _save_job(job)
                return job
            if cached and getattr(cached, "stale", False):
                # P4: stale 通常由 story_analysis 更新触发——只轻量刷新关系视图，
                # 不重建人设（无 LLM 调用）。无快照/刷新失败才走全重建。
                if cached.refresh_relations(series_id=job.series_id):
                    CharacterCard.save_for_series(
                        job.series_id,
                        job.canonical_name,
                        cached,
                        character_id=job.character_id,
                    )
                    job.state = "done"
                    job.card_path = str(
                        CharacterCard.cache_path_for(job.series_id, job.canonical_name, character_id=job.character_id)
                    )
                    job.flags["cache_hit"] = True
                    job.flags["relations_refreshed"] = True
                    _save_job(job)
                    return job

        job.state = "gather"
        _save_job(job)
        evidence = await gather_evidence(
            store,
            canonical_name=job.canonical_name,
            aliases=job.aliases,
            series_id=job.series_id,
            doc_id=job.doc_id,
        )
        low = evidence.dialogue_hits < min_dialogues

        # L3 deepen when quota ingest left too few samples
        if low and evidence.dialogue_hits >= 0:
            try:
                from src.application.novel.dialogue_pipeline import (
                    _attr_config,
                    deepen_dialogue_from_store,
                )

                attr_cfg = _attr_config()
                if bool(attr_cfg.get("deepen_on_build", True)):
                    deepen_llm = llm_client
                    owned_llm = False
                    if deepen_llm is None:
                        try:
                            from src.application.novel.ingest import _build_shared_llm

                            deepen_llm = _build_shared_llm(
                                temperature=0.0,
                                max_tokens=int(attr_cfg.get("max_output_tokens", 4096)),
                                endpoint="character_inventory",
                            )
                            owned_llm = True
                        except Exception:
                            deepen_llm = None
                    if deepen_llm is not None:
                        deep = await deepen_dialogue_from_store(
                            store,
                            canonical_name=job.canonical_name,
                            aliases=job.aliases,
                            doc_id=job.doc_id,
                            llm_client=deepen_llm,
                            max_calls=int(attr_cfg.get("deepen_max_calls", 8)),
                            config=attr_cfg,
                        )
                        job.flags["deepen"] = deep.meta
                        if owned_llm:
                            try:
                                await deepen_llm.close()
                            except Exception:
                                pass
                        if deep.blocks:
                            evidence = await gather_evidence(
                                store,
                                canonical_name=job.canonical_name,
                                aliases=job.aliases,
                                series_id=job.series_id,
                                doc_id=job.doc_id,
                            )
                            low = evidence.dialogue_hits < min_dialogues
            except Exception as e:
                logger.warning("Dialogue deepen skipped: %s", e)
                job.flags["deepen_error"] = str(e)

        job.evidence = {
            "dialogue_hits": evidence.dialogue_hits,
            "narrative_hits": evidence.narrative_hits,
            "sample_ids": evidence.sample_ids[:30],
        }
        job.flags["low_evidence"] = low
        if evidence.dialogue_hits == 0 and evidence.narrative_hits == 0:
            job.state = "failed"
            job.error = "no_evidence"
            _save_job(job)
            return job

        job.state = "distill"
        _save_job(job)
        profile = await distill_single(
            llm_client,
            canonical_name=job.canonical_name,
            aliases=job.aliases,
            series_id=job.series_id,
            evidence=evidence,
        )

        job.state = "persist"
        _save_job(job)
        roster = load_roster(job.series_id)
        source_docs = list(roster.doc_ids) if roster else ([job.doc_id] if job.doc_id else [])
        _card, path = persist_card(
            series_id=job.series_id,
            character_id=job.character_id,
            canonical_name=job.canonical_name,
            aliases=job.aliases,
            profile=profile,
            evidence=evidence,
            source_doc_ids=[d for d in source_docs if d],
            low_evidence=low,
        )
        job.card_path = str(path)
        job.state = "done"
        _save_job(job)
        return job
    except Exception as e:
        logger.exception("Character build job failed: %s", job.job_id)
        job.state = "failed"
        job.error = str(e)
        _save_job(job)
        return job


async def enqueue_builds(
    *,
    series_id: str,
    names: list[str],
    store,
    doc_id: str | None = None,
    force: bool = False,
    llm_client=None,
    resolve: dict[str, str] | None = None,
    wait: bool = True,
) -> list[CharacterBuildJob]:
    """Create jobs for each selected name.

    Args:
        wait: If True (default, tests/CLI), run sequentially to completion.
            If False, mark pending and schedule on the shared AsyncJobRunner.
    """
    resolve = resolve or {}
    jobs: list[CharacterBuildJob] = []
    for name in names:
        job = CharacterBuildJob(
            job_id=f"cb_{uuid.uuid4().hex[:12]}",
            series_id=series_id,
            doc_id=doc_id,
            input_name=name.strip(),
            created_at=_now(),
            updated_at=_now(),
        )
        _save_job(job)
        jobs.append(job)

        if wait:
            await run_build_job(
                job,
                store,
                llm_client=llm_client,
                force=force,
                resolve_character_id=resolve.get(name) or resolve.get(name.strip()),
            )
        else:
            from src.application.jobs import submit_job

            # Keep CharacterBuildJob and shared JobRecord in sync (same job_id).
            # submit_job persists the JobRecord itself (save + enqueue), so we
            # do not double-save; on failure mark the local job failed so the
            # two stores never silently diverge.
            shared = _to_job_record(job)
            shared.payload = {
                **shared.payload,
                "force": force,
                "resolve_character_id": resolve.get(name) or resolve.get(name.strip()),
            }
            try:
                submit_job(shared)
            except Exception as e:  # noqa: BLE001
                job.state = "failed"
                job.error = f"submit_failed:{e}"
                _save_job(job)
                logger.error(
                    "Failed to submit build job %s (%s): %s", job.job_id, name, e
                )

    return jobs
