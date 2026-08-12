"""Builtin async job handlers (payload-driven; no FastAPI Request)."""

from __future__ import annotations

import logging
from pathlib import Path

from src.application.jobs.registry import register_handler
from src.shared.async_jobs import JobRecord, get_job_store

logger = logging.getLogger("agent.jobs")


def _progress(job: JobRecord, stage: str, message: str, pct: int) -> None:
    store = get_job_store()
    job.result = {
        **(job.result or {}),
        "progress": {"stage": stage, "message": message, "pct": pct},
    }
    job.progress = {"stage": stage, "message": message, "pct": pct}
    store.save(job)


async def _novel_store():
    from src.application.novel.factory import create_novel_store

    return create_novel_store(backend="lancedb")


def _story_llm(*, endpoint: str | None = None, temperature: float = 0.2, max_tokens: int | None = None):
    try:
        from pathlib import Path

        from src.application.novel.services.story_analysis_service import (
            story_analysis_max_tokens,
        )
        from src.shared.llm_factory import create_shared_llm
        from src.utils.config import load_config

        cfg_path = Path(__file__).resolve().parents[3] / "config.yaml"
        cfg = load_config(str(cfg_path))
        if max_tokens is None:
            max_tokens = story_analysis_max_tokens()
        return create_shared_llm(
            cfg,
            temperature=temperature,
            max_tokens=max_tokens,
            endpoint=endpoint,
        )
    except Exception as exc:
        logger.warning("Story/character LLM unavailable: %s", exc)
        return None


async def handle_novel_upload(job: JobRecord) -> dict:
    from src.application.novel.ingest import ingest_novel

    payload = job.payload or {}
    tmp_path = Path(str(payload.get("tmp_path") or ""))
    filename = str(payload.get("filename") or "upload.txt")

    def on_progress(stage: str, message: str, pct: int) -> None:
        _progress(job, stage, message, pct)

    try:
        if not tmp_path.exists():
            return {"_state": "failed", "error": "upload temp file missing"}
        result = await ingest_novel(
            tmp_path.read_bytes(),
            filename,
            store=await _novel_store(),
            doc_id=payload.get("doc_id"),
            series_id=payload.get("series_id"),
            series_title=payload.get("series_title"),
            volume_no=payload.get("volume_no"),
            generate_qa=bool(payload.get("generate_qa", True)),
            generate_character_llm=bool(payload.get("generate_character_llm", False)),
            force_reindex=bool(payload.get("force_reindex", False)),
            on_progress=on_progress,
        )
        if not result.success:
            return {"_state": "failed", "error": result.error or "ingest failed"}
        # 运行时上传后强制重建共享 keyword 索引（常驻 store 同进程共享同一
        # KeywordsIndex 实例）+ 置 dirty 标志（下次检索重建 LanceDB 连接）——
        # 根治"运行时上传后新书检索失效（IVF_PQ 重建后旧连接不可见新行）"。
        try:
            from src.application.novel.factory import create_novel_store

            refresh_store = create_novel_store(backend="lancedb")
            if hasattr(refresh_store, "ensure_keyword_index"):
                refresh_store.ensure_keyword_index(force=True)
            logger.info(
                "POST-UPLOAD: keyword index refreshed (total=%s)",
                refresh_store._keywords.stats().get("total_ids", 0),
            )
            # 失效常驻 store：下次检索重建新 LanceDB 连接（新书立即可见）
            try:
                from src.api import state as api_state

                api_state.store_dirty = True
            except Exception as inner:  # noqa: BLE001
                logger.warning("api_state dirty flag set failed: %s", inner)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Keyword index refresh after upload failed: %s", exc)
        return {
            "status": "ok",
            "doc_id": result.doc_id,
            "series_id": result.series_id or result.doc_id,
            "source_format": result.source_format,
            "blocks": {
                "narrative": result.narrative_blocks,
                "dialogue": result.dialogue_blocks,
                "qa": result.qa_blocks,
                "character": result.character_blocks,
                "total": result.total_blocks,
            },
            "characters": result.characters,
            "hint": (
                "已索引原文与对话，角色名录已生成。"
                "请勾选角色后调用 POST /api/v1/agent/characters/build 生成人设卡。"
            ),
            "progress": {"stage": "done", "message": "导入完成", "pct": 100},
        }
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


async def handle_story_analysis(job: JobRecord) -> dict:
    from src.application.novel.services.story_analysis_service import run_story_analysis

    store = await _novel_store()
    llm = _story_llm()
    job_store = get_job_store()

    async def on_progress(prog: dict) -> None:
        job.progress = dict(prog or {})
        job_store.save(job)

    snapshot = await run_story_analysis(
        series_id=job.payload["series_id"],
        store=store,
        llm_client=llm,
        doc_id=job.payload.get("doc_id"),
        force=bool(job.payload.get("force")),
        max_chapters=job.payload.get("max_chapters"),
        extract_foreshadows=job.payload.get("extract_foreshadows"),
        on_progress=on_progress,
    )
    # 联动构建 GraphRAG 全局层（社区摘要），失败不阻断 story-analysis 结果
    try:
        from src.application.novel.services.graph_rag_service import build_graph_rag

        gr_llm = _story_llm(endpoint="graph_rag_summary", temperature=0.2, max_tokens=1024)
        await build_graph_rag(
            series_id=job.payload["series_id"],
            snapshot=snapshot,
            llm_client=gr_llm,
            force=False,
            on_progress=on_progress,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("GraphRAG build after story analysis failed: %s", exc)
    return {
        "analysis": {**snapshot.to_dict(), "exists": True},
        "cache_hit": snapshot.stats.get("cache_hit"),
    }


async def handle_graph_rag(job: JobRecord) -> dict:
    """独立构建 GraphRAG 全局层（POST /rag-global/build 触发）。"""
    from src.application.novel.services.graph_rag_service import build_graph_rag
    from src.application.novel.services.story_analysis_service import load_analysis

    series_id = str(job.payload.get("series_id") or "")
    snapshot = load_analysis(series_id) if series_id else None
    if snapshot is None:
        return {"_state": "failed", "error": "story_analysis not found; run /story-analysis/build first"}
    job_store = get_job_store()

    async def on_progress(prog: dict) -> None:
        job.progress = dict(prog or {})
        job_store.save(job)

    llm = _story_llm(endpoint="graph_rag_summary", temperature=0.2, max_tokens=1024)
    result = await build_graph_rag(
        series_id=series_id,
        snapshot=snapshot,
        llm_client=llm,
        force=bool(job.payload.get("force", False)),
        on_progress=on_progress,
    )
    return {
        "ok": True,
        "series_id": series_id,
        "communities": len(result.get("communities") or []),
        "has_overview": bool(result.get("global_overview")),
    }


async def handle_character_build(job: JobRecord) -> dict:
    from src.application.novel.services.character_build_service import (
        run_job_from_record,
    )

    store = await _novel_store()
    llm = _story_llm(endpoint="character_inventory")
    return await run_job_from_record(
        job,
        store,
        llm_client=llm,
        force=bool(job.payload.get("force", False)),
        resolve_character_id=job.payload.get("resolve_character_id"),
    )


register_handler("novel_upload", handle_novel_upload)
register_handler("story_analysis", handle_story_analysis)
register_handler("graph_rag", handle_graph_rag)
register_handler("character_build", handle_character_build)


def _dialogue_llm():
    """对话提取专用 LLM（temperature=0 确定性归因，endpoint 区分调用点）。"""
    try:
        from pathlib import Path

        from src.shared.llm_factory import create_shared_llm
        from src.utils.config import load_config

        cfg_path = Path(__file__).resolve().parents[3] / "config.yaml"
        cfg = load_config(str(cfg_path))
        return create_shared_llm(
            cfg, temperature=0.0, max_tokens=6144, endpoint="dialogue_extract",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dialogue LLM unavailable: %s", exc)
        return None


async def handle_redialogue(job: JobRecord) -> dict:
    from src.application.novel.redialogue import (
        DocNotFoundError,
        InventoryMissingError,
        run_redialogue,
    )

    payload = job.payload or {}
    doc_id = str(payload.get("doc_id") or "")
    write_back = bool(payload.get("write_back"))
    sample_n = int(payload.get("sample_n") or 0)
    if not doc_id:
        return {"_state": "failed", "error": "doc_id required"}

    llm = _dialogue_llm()
    try:
        result = await run_redialogue(
            doc_id,
            write_back=write_back,
            sample_n=sample_n,
            llm_client=llm,
        )
    except (InventoryMissingError, DocNotFoundError) as exc:
        return {"_state": "failed", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("redialogue job failed for %s", doc_id)
        return {"_state": "failed", "error": str(exc)}
    finally:
        if llm is not None:
            try:
                await llm.close()
            except Exception:
                pass

    return {
        "_state": "done",
        "doc_id": result.doc_id,
        "chapters": result.chapters,
        "meta": result.meta,
        "result_file": result.result_path,
        "written_back": result.written_back,
        "deleted_blocks": result.deleted_blocks,
        "new_blocks": result.new_blocks,
        "llm_calls": result.llm_calls,
        "turns": result.turns,
        "blocks": result.blocks,
    }


register_handler("redialogue", handle_redialogue)
