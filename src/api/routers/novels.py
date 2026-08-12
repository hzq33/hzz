"""Novel upload, catalog management, and story-analysis routes."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from src.api.errors import raise_internal_error
from src.api.schemas import SeriesRenameRequest, StoryAnalysisRequest

logger = logging.getLogger("agent_server")
router = APIRouter(prefix="/api/v1/agent")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_UPLOAD_TMP = _PROJECT_ROOT / "data" / "upload_tmp"


# 上传临时文件保留时长：超过即视为孤儿（导入中断/失败遗留），上传时惰性清理。
_UPLOAD_TMP_MAX_AGE_SECONDS = 24 * 3600


def _cleanup_stale_uploads() -> None:
    """删除 upload_tmp 下超过 24h 的残留文件（导入中断或旧版本遗留）。"""
    try:
        now = time.time()
        for p in _UPLOAD_TMP.glob("up_*") if _UPLOAD_TMP.exists() else []:
            try:
                if now - p.stat().st_mtime > _UPLOAD_TMP_MAX_AGE_SECONDS:
                    p.unlink(missing_ok=True)
                    logger.info("Cleaned stale upload temp: %s", p.name)
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass
_UPLOAD_JOB_TYPE = "novel_upload"


def _upload_max_bytes() -> int:
    """Max accepted upload size (env AGENT_UPLOAD_MAX_MB, default 200MB)."""
    try:
        return max(1, int(os.getenv("AGENT_UPLOAD_MAX_MB", "200"))) * 1024 * 1024
    except ValueError:
        return 200 * 1024 * 1024


def _sanitize_upload_filename(raw: str | None) -> str:
    """Sanitize a client-provided filename to a safe basename.

    Strips directory components (path traversal) and any characters that
    could break the tmp path (path separators, control chars, reserved
    Windows names like NUL). Falls back to a generic name when empty.
    """
    name = (raw or "").strip()
    # Strip any directory components (both separators; also handles backslash
    # used as escape inside multipart filenames).
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    # Keep letters/digits/unicode/._- and full-width ！ (common in series titles).
    name = re.sub(r"[^\w\u4e00-\u9fff.\-！]", "_", name)
    name = name.strip(". ")[:200] or "upload.txt"
    # Guard Windows reserved device names (NUL, CON, PRN, AUX, COM1-9, LPT1-9).
    stem = name.rsplit(".", 1)[0].upper()
    if stem in {"NUL", "CON", "PRN", "AUX"} or re.match(r"^(COM|LPT)[1-9]$", stem):
        name = "_" + name
    return name


def _reject_oversized_upload(request: Request, content: bytes) -> None:
    """Reject uploads exceeding the configured limit (413).

    Kept for callers that already hold the full payload in memory (e.g. tests);
    the upload endpoint itself now streams to disk with an incremental size
    check (see ``upload_novel``) so large files never sit fully in RAM.
    """
    limit = _upload_max_bytes()
    # Cheap pre-check via Content-Length header when present.
    try:
        declared = int(request.headers.get("content-length") or 0)
        if declared > limit:
            raise HTTPException(status_code=413, detail="Upload too large")
    except ValueError:
        pass
    if len(content) > limit:
        raise HTTPException(status_code=413, detail="Upload too large")


def _upload_job_progress(job, stage: str, message: str, pct: int) -> None:
    from src.shared.async_jobs import get_job_store

    store = get_job_store()
    job.result = {
        **(job.result or {}),
        "progress": {"stage": stage, "message": message, "pct": pct},
    }
    store.save(job)


async def _run_upload_job(
    job,
    get_imp_store: Callable[[], Awaitable[object]],
) -> dict:
    from src.application.novel.ingest import ingest_novel

    payload = job.payload or {}
    tmp_path = Path(str(payload.get("tmp_path") or ""))
    filename = str(payload.get("filename") or "upload.txt")

    def on_progress(stage: str, message: str, pct: int) -> None:
        _upload_job_progress(job, stage, message, pct)

    try:
        if not tmp_path.exists():
            return {"_state": "failed", "error": "upload temp file missing"}
        result = await ingest_novel(
            tmp_path.read_bytes(),
            filename,
            store=await get_imp_store(),
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


@router.post("/upload")
async def upload_novel(
    request: Request,
    file: UploadFile = File(...),
    series_id: str | None = Query(default=None),
    series_title: str | None = Query(default=None),
    doc_id: str | None = Query(default=None),
    volume_no: int | None = Query(default=None),
    generate_qa: str | None = Query(default=None),
    generate_character_llm: str | None = Query(default=None),
    force_reindex: str | None = Query(default=None, description="内容指纹命中时仍强制重跑全管线"),
    wait: str | None = Query(default=None),
):
    """Upload a novel for asynchronous indexing, or synchronously when requested.

    FastAPI 0.139 + multipart 存在 bug：Query 声明的字段在 multipart 表单里收不到
    （bool 恒为默认值）。这里用 str 接收 + 手动合并 query/form 取值，兼容两种调用。
    """
    # 手动从 query + multipart form 合并取值（绕过框架 bug）
    async def _field(name: str) -> str | None:
        qv = request.query_params.get(name)
        if qv is not None:
            return str(qv)
        try:
            form = await request.form()
            fv = form.get(name)
            if fv is not None:
                return str(fv)
        except Exception:  # noqa: BLE001 - 非表单请求
            pass
        return None

    _TRUE = {"1", "true", "yes", "on"}
    _gqa = await _field("generate_qa")
    _gcl = await _field("generate_character_llm")
    _fri = await _field("force_reindex")
    _wait = await _field("wait")
    generate_qa = True if _gqa is None else (_gqa.strip().lower() in _TRUE)
    generate_character_llm = False if _gcl is None else (_gcl.strip().lower() in _TRUE)
    force_reindex = False if _fri is None else (_fri.strip().lower() in _TRUE)
    wait = False if _wait is None else (_wait.strip().lower() in _TRUE)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")
    filename = _sanitize_upload_filename(file.filename)

    # ── 流式落盘 + 增量大小校验 ──
    # 旧实现 `content = await file.read()` 会把最大 200MB 的整个文件驻留内存；
    # 现在按 1MB 分块写入临时文件，边写边累计字节数，超限 413 并清理。
    limit = _upload_max_bytes()
    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > limit:
        await file.close()
        raise HTTPException(status_code=413, detail="Upload too large")

    _UPLOAD_TMP.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_uploads()
    tmp_path = _UPLOAD_TMP / f"up_{uuid.uuid4().hex[:12]}_{filename}"
    total = 0
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(status_code=413, detail="Upload too large")
                out.write(chunk)
    except HTTPException:
        tmp_path.unlink(missing_ok=True)
        await file.close()
        raise
    finally:
        await file.close()

    if wait:
        try:
            from src.application.novel.ingest import ingest_novel

            content = tmp_path.read_bytes()
            result = await ingest_novel(
                content,
                filename,
                store=await request.app.state.get_imp_store(),
                doc_id=doc_id,
                series_id=series_id,
                series_title=series_title,
                volume_no=volume_no,
                generate_qa=generate_qa,
                generate_character_llm=generate_character_llm,
                force_reindex=force_reindex,
            )
            if not result.success:
                raise HTTPException(status_code=400, detail=result.error)
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
                "roster": [{"name": name} for name in result.characters[:50]],
                "characters": result.characters,
                "hint": (
                    "已索引原文与对话，角色名录已生成。"
                    "请勾选角色后调用 POST /api/v1/agent/characters/build 生成人设卡。"
                ),
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise_internal_error(
                exc,
                public_detail="Novel upload failed",
                log_message="Novel upload failed",
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    from src.application.jobs import submit_job
    from src.shared.async_jobs import get_job_store

    job_store = get_job_store()
    job = job_store.create(
        _UPLOAD_JOB_TYPE,
        payload={
            "filename": filename,
            "tmp_path": str(tmp_path),
            "series_id": series_id,
            "series_title": series_title,
            "doc_id": doc_id,
            "volume_no": volume_no,
            "generate_qa": generate_qa,
            "generate_character_llm": generate_character_llm,
            "force_reindex": force_reindex,
        },
    )
    job.result = {"progress": {"stage": "received", "message": "已接收文件", "pct": 5}}
    job_store.save(job)
    submit_job(job)
    return {"job_id": job.job_id, "state": job.state, "progress": job.result["progress"]}


@router.get("/upload/jobs/{job_id}")
async def get_upload_job(job_id: str):
    from src.shared.async_jobs import get_job_store

    job = get_job_store().get(job_id, _UPLOAD_JOB_TYPE)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    output = {
        "job_id": job.job_id,
        "state": job.state,
        "error": job.error,
        "progress": (job.result or {}).get("progress"),
    }
    if job.state == "done":
        output.update(
            {key: value for key, value in (job.result or {}).items() if key != "progress"}
        )
    return output


@router.get("/novels")
async def list_novels(
    request: Request,
    series_id: str | None = Query(default=None),
):
    from src.application.novel.services.catalog_service import (
        ensure_series_title,
        list_catalogs,
        load_catalog,
    )

    if series_id:
        catalog = load_catalog(series_id)
        catalogs = [ensure_series_title(catalog)] if catalog else []
    else:
        catalogs = [ensure_series_title(catalog) for catalog in list_catalogs()]
    items = [
        {
            "series_id": catalog.series_id,
            "series_title": catalog.series_title or catalog.series_id,
            "doc_id": volume.doc_id,
            "volume_no": volume.volume_no,
            "volume_title": volume.volume_title,
            "title": volume.title,
            "source_format": volume.source_format,
            "indexed_at": volume.indexed_at,
            "block_counts": volume.block_counts,
            "chapter_count": len(volume.chapters),
            "needs_reindex": volume.needs_reindex,
            "reindex_reason": volume.reindex_reason,
        }
        for catalog in catalogs
        for volume in catalog.volumes
    ]
    # 孤儿卷：lance 有数据但 catalog 未收录 → 前端不可见、无法删除的残留。
    orphan_doc_ids: list[str] = []
    try:
        store = await request.app.state.get_imp_store()
        from src.application.novel.services.catalog_service import find_orphan_doc_ids

        orphan_doc_ids = find_orphan_doc_ids(list(store.doc_ids() or []))
    except Exception:  # noqa: BLE001
        pass
    return {"items": items, "orphan_doc_ids": orphan_doc_ids}


@router.patch("/novels/series")
async def rename_novel_series(
    req: SeriesRenameRequest,
    series_id: str = Query(..., min_length=1, description="Series id to rename"),
):
    from src.application.novel.services.catalog_service import rename_series

    catalog = rename_series(series_id, req.series_title)
    if not catalog:
        raise HTTPException(status_code=404, detail="Series not found")
    return {"series_id": catalog.series_id, "series_title": catalog.series_title}


@router.delete("/novels/{doc_id}")
async def delete_novel(
    doc_id: str,
    request: Request,
    series_id: str | None = Query(default=None),
):
    from src.api.helpers import series_id_from_doc_id
    from src.application.novel.services.catalog_service import (
        delete_volume_from_catalog,
        list_catalogs,
        load_catalog,
        purge_series_artifacts,
    )

    store = await request.app.state.get_imp_store()
    deleted = await store.delete_by_doc_id(doc_id)
    resolved_series_id = series_id
    if not resolved_series_id:
        for catalog in list_catalogs():
            if any(volume.doc_id == doc_id for volume in catalog.volumes):
                resolved_series_id = catalog.series_id
                break
    if not resolved_series_id:
        resolved_series_id = series_id_from_doc_id(doc_id) or None

    catalog_after = None
    if resolved_series_id:
        catalog_after = delete_volume_from_catalog(resolved_series_id, doc_id)

    # If this was the last volume (or catalog already gone), drop sidecars so
    # Knowledge / Impersonation / Alias Monitor stop showing ghost series & characters.
    purge_stats: dict = {}
    if resolved_series_id:
        remaining_volumes = bool(catalog_after and catalog_after.volumes)
        if not remaining_volumes:
            # Also check live store doc_ids in case catalog was already empty
            try:
                remaining_volumes = any(
                    series_id_from_doc_id(str(d)) == resolved_series_id
                    for d in (store.doc_ids() or [])
                )
            except Exception:
                remaining_volumes = False
        # Purge when catalog is gone OR last volume just removed (catalog_after is None)
        if not remaining_volumes:
            purge_stats = purge_series_artifacts(resolved_series_id)

    return {
        "deleted_blocks": deleted,
        "doc_id": doc_id,
        "series_id": resolved_series_id,
        "purged": purge_stats,
    }


@router.get("/story-analysis")
async def get_story_analysis(
    series_id: str = Query(...),
    doc_id: str | None = Query(default=None),
):
    from src.application.novel.services.story_analysis_service import load_analysis

    snapshot = load_analysis(series_id)
    if not snapshot:
        return {
            "series_id": series_id,
            "exists": False,
            "events": [],
            "foreshadows": [],
            "relations": [],
        }
    data = snapshot.to_dict()
    if doc_id:
        data["events"] = [event for event in data["events"] if event.get("doc_id") == doc_id]
        data["relations"] = [
            relation for relation in data["relations"] if relation.get("doc_id") == doc_id
        ]
        data["foreshadows"] = [
            item
            for item in data["foreshadows"]
            if item.get("introduced_doc_id") == doc_id or item.get("resolved_doc_id") == doc_id
        ]
    data["exists"] = True
    return data


@router.get("/timeline")
async def get_timeline(series_id: str = Query(...)):
    """V5：读取编年体时间线（chronicle/by_character/by_era）。"""
    from src.application.novel.services.story_analysis_service import load_timeline

    data = load_timeline(series_id)
    if not data:
        return {"series_id": series_id, "exists": False, "chronicle": [], "by_character": {}, "by_era": []}
    data["exists"] = True
    return data


@router.get("/lorebook")
async def get_lorebook(series_id: str = Query(...)):
    """V5：读取时间感知设定书（entries）。"""
    from src.application.novel.services.story_analysis_service import load_lorebook

    data = load_lorebook(series_id)
    if not data:
        return {"series_id": series_id, "exists": False, "entries": []}
    data["exists"] = True
    return data


@router.post("/story-analysis/build")
async def build_story_analysis(req: StoryAnalysisRequest, request: Request):
    from src.application.novel.services.story_analysis_service import (
        run_story_analysis,
        story_analysis_max_tokens,
    )
    from src.shared.async_jobs import JobRecord

    store = await request.app.state.get_imp_store()
    llm = None
    max_tokens = story_analysis_max_tokens()
    try:
        config = request.app.state.load_config()
        if config is not None:
            llm = request.app.state.create_shared_llm(
                config, temperature=0.2, max_tokens=max_tokens, endpoint="story_analysis",
            )
    except Exception as exc:
        logger.warning("Story analysis LLM unavailable: %s", exc)

    run_kwargs = {
        "series_id": req.series_id,
        "store": store,
        "llm_client": llm,
        "doc_id": req.doc_id,
        "force": req.force,
        "max_chapters": req.max_chapters,
        "extract_foreshadows": req.extract_foreshadows,
    }

    if req.wait:
        try:
            snapshot = await run_story_analysis(**run_kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "state": "done",
            "analysis": {**snapshot.to_dict(), "exists": True},
            "cache_hit": snapshot.stats.get("cache_hit"),
        }

    job = JobRecord(
        job_id=f"sa_{uuid.uuid4().hex[:12]}",
        job_type="story_analysis",
        state="pending",
        payload={
            "series_id": req.series_id,
            "doc_id": req.doc_id,
            "force": req.force,
            "max_chapters": req.max_chapters,
            "extract_foreshadows": req.extract_foreshadows,
        },
    )
    from src.application.jobs import submit_job

    submit_job(job)
    return {"job_id": job.job_id, "state": job.state, "series_id": req.series_id}


@router.get("/story-analysis/jobs/{job_id}")
async def get_story_analysis_job(job_id: str):
    from src.shared.async_jobs import get_job_store

    job = get_job_store().get(job_id, job_type="story_analysis")
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


# ── GraphRAG 全局问答层 ─────────────────────────────────


@router.get("/rag-global")
async def get_rag_global(
    series_id: str = Query(..., min_length=1),
    query: str | None = Query(default=None, description="可选：返回全局检索上下文"),
):
    """读取 GraphRAG 全局层（社区摘要）；带 query 时返回全局问答上下文。"""
    from src.application.novel.services.graph_rag_service import (
        format_global_context,
        is_stale,
        load_graph_rag,
    )

    payload = load_graph_rag(series_id)
    if payload is None:
        return {
            "series_id": series_id,
            "exists": False,
            "hint": "尚未构建 GraphRAG。请先运行 story-analysis build（会联动构建）或 POST /rag-global/build。",
        }
    resp = {
        "series_id": series_id,
        "exists": True,
        "stale": is_stale(series_id),
        "updated_at": payload.get("updated_at", ""),
        "global_overview": payload.get("global_overview", ""),
        "communities": payload.get("communities", []),
    }
    if query:
        resp["context"] = format_global_context(series_id, query)
    return resp


class RagGlobalBuildRequest(BaseModel):
    series_id: str = Field(..., min_length=1)
    force: bool = False
    wait: bool = False


@router.post("/rag-global/build")
async def build_rag_global(req: RagGlobalBuildRequest, request: Request):
    from src.application.jobs import submit_job
    from src.shared.async_jobs import JobRecord

    if req.wait:
        from src.application.novel.services.graph_rag_service import build_graph_rag
        from src.application.novel.services.story_analysis_service import load_analysis

        snapshot = load_analysis(req.series_id)
        if snapshot is None:
            raise HTTPException(
                status_code=400,
                detail="story_analysis not found; run /story-analysis/build first",
            )
        llm = request.app.state.create_shared_llm(
            request.app.state.load_config(),
            temperature=0.2,
            max_tokens=1024,
            endpoint="graph_rag_summary",
        )
        result = await build_graph_rag(
            series_id=req.series_id,
            snapshot=snapshot,
            llm_client=llm,
            force=req.force,
        )
        return {
            "state": "done",
            "series_id": req.series_id,
            "communities": len(result.get("communities") or []),
        }

    job = JobRecord(
        job_id=f"gr_{uuid.uuid4().hex[:12]}",
        job_type="graph_rag",
        state="pending",
        payload={"series_id": req.series_id, "force": req.force},
    )
    submit_job(job)
    return {"job_id": job.job_id, "state": job.state, "series_id": req.series_id}


@router.get("/rag-global/jobs/{job_id}")
async def get_rag_global_job(job_id: str):
    from src.shared.async_jobs import get_job_store

    job = get_job_store().get(job_id, job_type="graph_rag")
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@router.post("/novels/{doc_id}/redialogue")
async def redialogue_novel(
    doc_id: str,
    request: Request,
    wait: bool = Query(default=True),
    write_back: bool = Query(default=False),
    sample_n: int = Query(default=0, ge=0, le=500),
):
    """脱离 ingest 主链路，单独重跑某卷的对话提取/归因。

    依赖 series inventory（缺失 → 409，提示先跑 rebuild_inventory.py）。
    - wait=true：同步执行，返回结果摘要
    - wait=false：提交 job，轮询 /redialogue/jobs/{job_id}
    - write_back=true：替换该 doc 的 dialogue blocks（narrative 不动，旧 blocks 备份进结果文件）
    """
    from src.application.novel.redialogue import (
        DocNotFoundError,
        InventoryMissingError,
        load_series_inventory,
        run_redialogue,
    )

    series_id = doc_id.split("__", 1)[0] if "__" in doc_id else doc_id
    seed, chars = load_series_inventory(series_id)
    if not seed and not chars:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "series inventory not found",
                "detail": f"请先运行: python scripts/dev/rebuild_inventory.py {series_id}",
                "missing_file": f"data/inventories/{series_id}.json",
            },
        )

    llm = None
    try:
        config = request.app.state.load_config()
        if config is not None:
            llm = request.app.state.create_shared_llm(
                config, temperature=0.0, max_tokens=6144, endpoint="dialogue_extract",
            )
    except Exception as exc:  # noqa: BLE001 - LLM 不可用时由 run_redialogue 降级
        logger.warning("Redialogue LLM unavailable: %s", exc)

    if wait:
        try:
            result = await run_redialogue(
                doc_id,
                write_back=write_back,
                sample_n=sample_n,
                llm_client=llm,
            )
        except (InventoryMissingError, DocNotFoundError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise_internal_error(exc, public_detail="redialogue failed")
        return {
            "doc_id": result.doc_id,
            "chapters": result.chapters,
            "meta": result.meta,
            # 只暴露文件名，不泄露服务器绝对路径
            "result_file": (
                Path(str(result.result_path)).name if result.result_path else ""
            ),
            "written_back": result.written_back,
            "deleted_blocks": result.deleted_blocks,
            "new_blocks": result.new_blocks,
            "llm_calls": result.llm_calls,
            "turns": result.turns,
            "blocks": result.blocks,
        }

    from src.application.jobs import submit_job
    from src.shared.async_jobs import JobRecord

    job = JobRecord(
        job_id=f"rd_{uuid.uuid4().hex[:12]}",
        job_type="redialogue",
        state="pending",
        payload={
            "doc_id": doc_id,
            "write_back": write_back,
            "sample_n": sample_n,
        },
    )
    submit_job(job)
    return {"job_id": job.job_id, "state": job.state, "doc_id": doc_id}


@router.get("/redialogue/jobs/{job_id}")
async def get_redialogue_job(job_id: str):
    from src.shared.async_jobs import get_job_store

    job = get_job_store().get(job_id, job_type="redialogue")
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()
