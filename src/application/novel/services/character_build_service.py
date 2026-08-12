"""CharacterBuildService — 角色建卡管线的应用层入口（薄封装）。

统一暴露 enqueue（排队/同步构建）、run_from_job（异步执行）与
job 查询（get_job / list_jobs），jobs 层不再 import domain 的私有转换函数。
"""

from __future__ import annotations

from typing import Any


def get_job(job_id: str) -> Any:
    """读取单个建卡任务（CharacterBuildJob | None）。"""
    from src.domain.novel.character_on_demand import get_job as _get

    return _get(job_id)


def list_jobs(series_id: str | None = None, limit: int = 50) -> list[Any]:
    """列出建卡任务（可按系列过滤）。"""
    from src.domain.novel.character_on_demand import list_jobs as _list

    return _list(series_id=series_id, limit=limit)


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
) -> list[Any]:
    """排队（或同步执行）角色建卡，返回 CharacterBuildJob 列表。"""
    from src.domain.novel.character_on_demand import enqueue_builds as _enqueue

    return await _enqueue(
        series_id=series_id,
        names=names,
        store=store,
        doc_id=doc_id,
        force=force,
        llm_client=llm_client,
        resolve=resolve,
        wait=wait,
    )


async def run_job_from_record(
    job_record,
    store,
    *,
    llm_client=None,
    force: bool = False,
    resolve_character_id: str | None = None,
) -> dict[str, Any]:
    """从 JobRecord 恢复建卡任务并执行（jobs 异步入口专用）。

    封装 domain 的 _from_job_record / _save_job / run_build_job，
    失败时标记 failed 并持久化后上抛。
    """
    from src.domain.novel.character_on_demand.jobs import _from_job_record, _save_job
    from src.domain.novel.character_on_demand.runner import run_build_job

    cb = _from_job_record(job_record)
    try:
        await run_build_job(
            cb,
            store,
            llm_client=llm_client,
            force=force,
            resolve_character_id=resolve_character_id,
        )
    except Exception as exc:  # noqa: BLE001 - 失败须落库并上抛给 job 框架
        cb.state = "failed"
        cb.error = str(exc)
        _save_job(cb)
        raise
    return {
        "state": cb.state,
        "canonical_name": cb.canonical_name,
        "character_id": cb.character_id,
        "card_path": cb.card_path,
        "error": cb.error,
        "flags": cb.flags,
        "evidence": cb.evidence,
        "_state": cb.state if cb.state in {"done", "failed"} else "done",
    }
