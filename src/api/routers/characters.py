"""Character discovery, card building, and editing routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.api.errors import raise_internal_error
from src.api.helpers import card_source_meta, list_known_series_ids
from src.api.schemas import (
    CharacterBuildRequest,
    CharacterInfo,
    CharacterMergeRequest,
    CharacterUpdate,
)

logger = logging.getLogger("agent_server")
router = APIRouter(prefix="/api/v1/agent/characters")


def _card_profile(card) -> tuple:
    """从角色卡提取列表页展示字段（roster / candidates / 缓存兜底三处共用）。"""
    samples = [
        d.get("content", "")
        for d in (card.sample_dialogues or [])[:5]
    ]
    personality = card.personality or ""
    speaking_style = card.speaking_style or ""
    background = card.background or ""
    sample_count, source_chapters, source_doc_ids = card_source_meta(card)
    return (
        samples,
        personality,
        speaking_style,
        background,
        sample_count,
        source_chapters,
        source_doc_ids,
    )


@router.get("")
async def list_characters(
    request: Request,
    series_id: str | None = Query(default=None, description="Series / book id for roster"),
    doc_id: str | None = Query(default=None, description="Optional volume filter"),
    q: str | None = Query(default=None, description="Name filter"),
    include_candidates: bool = Query(default=True),
    seed_only: bool = Query(default=False),
):
    """List characters from L1 roster, inventory candidates, and card cache."""
    from src.application.novel.services.character_query_service import (
        load_inventory_candidates,
        load_roster,
    )
    from src.domain.character_card import CharacterCard

    items: list[CharacterInfo] = []
    seen: set[tuple[str, str]] = set()
    series_ids = (
        [series_id]
        if series_id
        else await list_known_series_ids(request.app.state.get_imp_store)
    )

    for sid in series_ids:
        inventory = load_inventory_candidates(sid) if include_candidates else None
        inventory_by_name = {
            candidate.get("name", "").strip(): candidate
            for candidate in (inventory or {}).get("candidates", [])
            if candidate.get("name", "").strip()
        }
        roster = load_roster(sid)
        if roster:
            for entry in roster.characters:
                if q and q not in entry.name and not any(q in alias for alias in entry.aliases_observed):
                    continue
                candidate = inventory_by_name.get(entry.name) or {}
                mention_count = int(entry.mention_count or candidate.get("mention_count") or 0)
                in_llm_seed = bool(candidate.get("in_llm_seed")) if candidate else mention_count > 0
                if seed_only and not in_llm_seed:
                    continue
                card = CharacterCard.load_for_series(
                    sid, entry.name, character_id=entry.character_id or ""
                )
                samples: list[str] = []
                personality = speaking_style = background = ""
                sample_count = 0
                source_chapters: list[str] = []
                source_doc_ids: list[str] = []
                if card:
                    (
                        samples,
                        personality,
                        speaking_style,
                        background,
                        sample_count,
                        source_chapters,
                        source_doc_ids,
                    ) = _card_profile(card)
                seen.add((sid, entry.name))
                items.append(
                    CharacterInfo(
                        name=entry.name,
                        source=sid,
                        series_id=sid,
                        dialogue_count=entry.dialogue_count,
                        mention_count=mention_count,
                        in_llm_seed=in_llm_seed,
                        importance=str(candidate.get("importance") or "supporting"),
                        aliases=list(entry.aliases_observed or candidate.get("aliases") or []),
                        has_card=bool(entry.has_card or card),
                        status=entry.status,
                        character_id=entry.character_id or "",
                        personality=personality,
                        speaking_style=speaking_style,
                        background=background,
                        sample_dialogues=samples,
                        sample_count=sample_count or entry.dialogue_count,
                        source_chapters=source_chapters,
                        source_doc_ids=source_doc_ids,
                    )
                )

        if include_candidates and inventory:
            for candidate in inventory.get("candidates") or []:
                name = (candidate.get("name") or "").strip()
                if not name or (sid, name) in seen:
                    continue
                if q and q not in name and not any(q in alias for alias in candidate.get("aliases") or []):
                    continue
                in_llm_seed = bool(candidate.get("in_llm_seed"))
                if seed_only and not in_llm_seed:
                    continue
                card = CharacterCard.load_for_series(sid, name, character_id="")
                samples: list[str] = []
                personality = speaking_style = background = ""
                sample_count = 0
                source_chapters: list[str] = []
                source_doc_ids: list[str] = []
                if card:
                    (
                        samples,
                        personality,
                        speaking_style,
                        background,
                        sample_count,
                        source_chapters,
                        source_doc_ids,
                    ) = _card_profile(card)
                seen.add((sid, name))
                items.append(
                    CharacterInfo(
                        name=name,
                        source=sid,
                        series_id=sid,
                        mention_count=int(candidate.get("mention_count") or 0),
                        in_llm_seed=in_llm_seed,
                        importance=str(candidate.get("importance") or "supporting"),
                        aliases=list(candidate.get("aliases") or []),
                        has_card=bool(card),
                        status="ready" if card else "candidate",
                        personality=personality,
                        speaking_style=speaking_style,
                        background=background,
                        sample_dialogues=samples,
                        sample_count=sample_count,
                        source_chapters=source_chapters,
                        source_doc_ids=source_doc_ids,
                    )
                )

    if not items:
        cache_dir = CharacterCard._CACHE_DIR
        if cache_dir.exists():
            for cache_file in cache_dir.glob("*.json"):
                try:
                    card = CharacterCard.load(cache_file.stem)
                    if not card or (q and q not in (card.name or "")):
                        continue
                    (
                        samples,
                        personality,
                        speaking_style,
                        background,
                        sample_count,
                        chapters,
                        docs,
                    ) = _card_profile(card)
                    items.append(
                        CharacterInfo(
                            name=card.name or cache_file.stem,
                            source=card.source_work or card.series_id,
                            series_id=card.series_id or "",
                            dialogue_count=len(card.sample_dialogues or []),
                            aliases=list(card.aliases or []),
                            has_card=True,
                            status="ready",
                            character_id=card.character_id or "",
                            personality=personality,
                            speaking_style=speaking_style,
                            background=background,
                            sample_dialogues=samples,
                            sample_count=sample_count,
                            source_chapters=chapters,
                            source_doc_ids=docs,
                        )
                    )
                except Exception:
                    continue

    items.sort(
        key=lambda item: (
            -int(item.mention_count or 0),
            -int(item.dialogue_count or 0),
            item.name,
        )
    )
    return items


@router.get("/candidates")
async def list_character_candidates(
    series_id: str = Query(..., min_length=1),
    q: str | None = Query(default=None),
    min_mentions: int | None = Query(default=None),
):
    from src.domain.character_card import CharacterCard
    from src.application.novel.services.character_query_service import (
        load_inventory_candidates,
    )

    inventory = load_inventory_candidates(series_id)
    if not inventory:
        return {"series_id": series_id, "seed_min_mentions": min_mentions or 3, "candidates": []}
    threshold = int(
        min_mentions if min_mentions is not None else inventory.get("seed_min_mentions") or 3
    )
    candidates = []
    for candidate in inventory.get("candidates") or []:
        name = (candidate.get("name") or "").strip()
        if not name or (
            q and q not in name and not any(q in str(alias) for alias in candidate.get("aliases") or [])
        ):
            continue
        mention_count = int(candidate.get("mention_count") or 0)
        candidates.append(
            {
                "name": name,
                "aliases": list(candidate.get("aliases") or []),
                "mention_count": mention_count,
                "importance": candidate.get("importance") or "supporting",
                "in_llm_seed": mention_count >= threshold,
                "has_card": bool(CharacterCard.load_for_series(series_id, name, character_id="")),
                "series_id": series_id,
            }
        )
    candidates.sort(key=lambda item: (-item["mention_count"], item["name"]))
    return {
        "series_id": series_id,
        "seed_min_mentions": threshold,
        "candidates_total": len(inventory.get("candidates") or []),
        "candidates": candidates,
    }


@router.get("/graph")
async def get_character_graph(
    series_id: str = Query(..., min_length=1),
    doc_id: str | None = Query(default=None, description="Optional volume filter"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    min_weight: int = Query(default=1, ge=1, le=20),
):
    """Aggregate story_analysis relations into a character relationship graph.

    Returns {nodes, edges, stats} for frontend force-directed rendering.
    Each edge merges per-chapter RelationChange records for a character pair:
    weight=count, category=majority standardized type, polarity=best-confidence.
    """
    from src.application.novel.services.story_analysis_service import (
        build_relation_graph,
        load_analysis,
    )

    snapshot = load_analysis(series_id)
    if not snapshot:
        return {
            "series_id": series_id,
            "exists": False,
            "doc_id": doc_id,
            "nodes": [],
            "edges": [],
            "stats": {},
        }
    relations = list(snapshot.relations or [])
    if doc_id:
        relations = [r for r in relations if r.doc_id == doc_id]
    graph = build_relation_graph(
        relations, min_confidence=min_confidence, min_weight=min_weight
    )
    return {
        "series_id": series_id,
        "exists": True,
        "doc_id": doc_id,
        "updated_at": snapshot.updated_at,
        **graph,
    }


@router.post("/build")
async def build_characters(req: CharacterBuildRequest, request: Request):
    from src.application.novel.services.character_build_service import enqueue_builds

    store = await request.app.state.get_imp_store()
    llm = None
    try:
        config = request.app.state.load_config()
        if config is not None:
            llm = request.app.state.create_shared_llm(
                config, temperature=0.3, max_tokens=4096
            )
    except Exception as exc:
        logger.warning("Build LLM unavailable, will use heuristic fallback: %s", exc)
    jobs = await enqueue_builds(
        series_id=req.series_id,
        names=req.names,
        store=store,
        doc_id=req.doc_id,
        force=req.force,
        llm_client=llm,
        resolve=req.resolve,
        wait=req.wait,
    )
    return {"jobs": [job.to_dict() for job in jobs]}


@router.get("/merge-suggestions")
async def character_merge_suggestions(
    series_id: str = Query(..., min_length=1),
    min_score: float = Query(default=0.92, ge=0.5, le=1.0),
):
    """Suggest same-series merges for CN transliteration splits (tone-less pinyin)."""
    from src.application.novel.services.character_merge_service import suggest_merges

    suggestions = suggest_merges(series_id, min_score=min_score)
    return {
        "series_id": series_id,
        "suggestions": [s.to_dict() for s in suggestions],
    }


@router.post("/merge")
async def merge_characters_route(req: CharacterMergeRequest, request: Request):
    """Merge near-duplicate character names into one survivor within a series."""
    from src.application.novel.services.character_merge_service import merge

    names = [n.strip() for n in req.names if n and n.strip()]
    survivor = req.survivor.strip()
    if survivor not in names:
        names = [survivor, *names]
    try:
        result = merge(
            series_id=req.series_id.strip(),
            survivor=survivor,
            merge_names=names,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise_internal_error(
            exc,
            public_detail="Character merge failed",
            log_message="Character merge failed",
        )
    # Invalidate any cached impersonation prompts for absorbed names
    try:
        sessions = request.app.state.imp_sessions
        for n in [result.survivor, *result.merged_names]:
            sessions.invalidate_character(n)
    except Exception:
        pass
    return result.to_dict()


@router.get("/jobs/{job_id}")
async def get_character_job(job_id: str):
    from src.application.novel.services.character_build_service import get_job

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.get("/jobs")
async def list_character_jobs(
    series_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    from src.application.novel.services.character_build_service import list_jobs

    return [job.to_dict() for job in list_jobs(series_id=series_id, limit=limit)]


# ── Alias roster CRUD (must be before /{name} catch-alls) ────────────

@router.get("/roster")
async def get_alias_roster(series_id: str = Query(...)):
    """Get alias roster (canonical + aliases) for a series."""
    from src.api.routers.alias_roster import read_alias

    data = read_alias(series_id)
    if data.get("meta", {}).get("error"):
        raise HTTPException(status_code=404, detail="Roster not found")
    return data


@router.get("/roster/series")
async def list_roster_series():
    """List series with alias rosters (ids + display titles from catalog).

    Display titles come from the catalog so a rename (series_title) is
    reflected here too — otherwise this page keeps showing the old alias
    filename stem.
    """
    from src.api.routers.alias_roster import list_series
    from src.application.novel.services.catalog_service import load_catalog

    ids = list_series()
    titles: dict[str, str] = {}
    for sid in ids:
        catalog = load_catalog(sid)
        titles[sid] = (catalog.series_title or catalog.series_id) if catalog else sid

    return {"series": ids, "titles": titles}


@router.put("/roster")
async def update_alias_roster(
    request: Request,
    series_id: str = Query(...),
    data: dict = Body(...),
):
    """Update alias roster (canonical + aliases) for a series.

    Canonical renames also sync CharacterRoster / inventory / character cards.
    """
    from src.api.routers.alias_roster import read_alias, write_alias
    from src.application.novel.services.character_query_service import sync_alias_roster_save

    try:
        old_data = read_alias(series_id)
        # Optimistic concurrency: client echoes the updated_at it loaded;
        # a mismatch means another session/save changed the roster meanwhile.
        current_updated = str(old_data.get("updated_at") or "")
        base_updated_at = str((data.get("meta") or {}).get("base_updated_at") or "")
        if (
            base_updated_at
            and current_updated
            and base_updated_at != current_updated
        ):
            raise HTTPException(
                status_code=409,
                detail="Roster was modified by another session — reload and retry",
            )
        if old_data.get("meta", {}).get("error"):
            old_data = {"entities": []}
        write_alias(series_id, data)
        sync_stats = sync_alias_roster_save(series_id, old_data, data)
        for item in sync_stats:
            old_n = item.get("old_name") or ""
            new_n = item.get("new_name") or ""
            if not old_n or not new_n:
                continue
            try:
                request.app.state.imp_sessions.invalidate_character(old_n)
            except Exception:  # noqa: BLE001
                pass
            try:
                request.app.state.imp_sessions.invalidate_character(new_n)
            except Exception:  # noqa: BLE001
                pass
        return {
            "message": f"Roster updated ({len(data.get('entities', []))} entities)",
            "renames": sync_stats,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise_internal_error(
            exc,
            public_detail="Roster update failed",
            log_message="Roster update failed",
        )


@router.delete("/{name}")
async def delete_character(
    name: str,
    request: Request,
    series_id: str | None = Query(default=None, description="Series id to scope roster/alias cleanup"),
):
    """Delete a character card + roster + alias entries.

    Removes data/characters/{series}__{name}.json (and {name}.json),
    the roster entry, and the alias entity. Dialogue blocks in LanceDB
    are kept (re-extraction would be needed to drop them).
    """
    from src.domain.character_card import CharacterCard

    removed_files: list[str] = []
    # 1. Character card files (series-scoped or bare name)
    patterns = [f"*__{name}.json"]
    if not series_id:
        patterns.append(f"{name}.json")
    for pat in patterns:
        for p in CharacterCard._CACHE_DIR.glob(pat):
            try:
                p.unlink()
                removed_files.append(str(p))
            except OSError as e:
                logger.warning("Failed to delete card %s: %s", p, e)

    # 2. Roster entry
    roster_removed = False
    if series_id:
        from src.application.novel.services.character_query_service import (
            load_roster,
            save_roster,
        )

        roster = load_roster(series_id)
        if roster:
            before = len(roster.characters)
            roster.characters = [e for e in roster.characters if e.name != name]
            if len(roster.characters) != before:
                roster_removed = True
                save_roster(roster)

    # 3. Alias entity
    alias_removed = False
    if series_id:
        try:
            from src.application.novel.services.character_query_service import (
                load_alias_map,
                save_alias_map,
            )

            amap = load_alias_map(series_id)
            if amap is not None:
                before = len(amap.entities)
                amap.entities = [e for e in amap.entities if e.canonical_name != name]
                if len(amap.entities) != before:
                    alias_removed = True
                    save_alias_map(amap)
        except Exception as e:  # noqa: BLE001
            logger.warning("Alias cleanup skipped for %s: %s", name, e)

    try:
        request.app.state.imp_sessions.invalidate_character(name)
    except Exception as e:  # noqa: BLE001
        logger.warning("Character delete: impersonation cache invalidate failed for %s: %s", name, e)

    if not removed_files and not roster_removed and not alias_removed:
        raise HTTPException(status_code=404, detail=f"Character '{name}' not found")
    return {
        "message": f"Deleted {name}",
        "cards_removed": removed_files,
        "roster_removed": roster_removed,
        "alias_removed": alias_removed,
    }


@router.put("/{name}")
async def update_character(name: str, update: CharacterUpdate, request: Request):
    from src.domain.character_card import CharacterCard

    cache_path = CharacterCard._CACHE_DIR / f"{name}.json"
    card = CharacterCard.load(name) if cache_path.exists() else None
    if card is None:
        if not update.create_if_missing:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Character '{name}' not found. Pass create_if_missing=true "
                    "to create, or ingest a novel first."
                ),
            )
        card = CharacterCard(name=name)
    try:
        for field in (
            "personality",
            "speaking_style",
            "background",
            "catchphrases",
            "source_work",
            "relationships",
        ):
            value = getattr(update, field)
            if value is not None:
                setattr(card, field, value)
        if update.sample_dialogues is not None:
            card.sample_dialogues = [
                {"speaker": name, "content": sample, "context": "手动编辑"}
                if isinstance(sample, str)
                else sample
                for sample in update.sample_dialogues[:8]
            ]
        CharacterCard._save_cache(name, card)
        request.app.state.imp_sessions.invalidate_character(name)
        logger.info("Updated character card: %s", name)
        return {
            "message": f"Updated {name}",
            "prompt": card.to_prompt(),
            "path": str(cache_path),
            "sample_count": len(card.sample_dialogues or []),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise_internal_error(
            exc,
            public_detail="Character update failed",
            log_message="Character update failed",
        )
