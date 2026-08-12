"""Operational probes, metrics, and browser telemetry endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from src.shared.metrics import render_metrics
from src.shared.readiness import check_readiness
from src.utils.auth import get_api_token

router = APIRouter()


@router.get("/api/v1/agent/rag-eval")
async def rag_eval(
    request: Request,
    kind: str | None = None,
    channel: str | None = None,
    q: str | None = None,
    zero_only: bool = False,
    limit: int = 200,
):
    """RAG 检索在线评估：读 trace 日志，返回概览统计 + 逐条检索详情。

    只统计**现存会话**（角色扮演 + 通用助手）产生的检索；无会话归属的旧 trace
    与已删除会话的 trace 一律排除，保证评估反映的是真实、未过期的会话数据。
    """
    try:
        from src.shared.rag_trace import (
            build_case_list,
            filter_traces_for_api,
            load_traces,
            summarize_traces,
        )

        traces = load_traces()
        active_ids = _active_session_ids(request)
        filtered = filter_traces_for_api(
            traces,
            kind=kind,
            channel=channel,
            q=q,
            zero_only=zero_only,
            limit=limit,
            active_session_ids=active_ids,
        )
        return JSONResponse(
            {
                "summary": summarize_traces(filtered),
                "total_available": len(traces),
                "active_sessions": len(active_ids),
                "cases": build_case_list(filtered, limit=limit),
            },
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"summary": {}, "total_available": 0, "cases": [], "error": str(exc)},
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


def _active_session_ids(request: Request) -> set[str]:
    """现存会话 id 集合：角色扮演（内存+磁盘）+ 通用助手（内存+磁盘），无数量上限。"""
    ids: set[str] = set()
    try:
        imp = request.app.state.imp_sessions
        ids.update(imp.all_session_ids())
    except Exception:  # noqa: BLE001
        pass
    try:
        conv = request.app.state.conversation
        ids.update(conv.list_active_session_ids())
    except Exception:  # noqa: BLE001
        pass
    return ids


@router.post("/api/v1/agent/rag-eval/judge")
async def rag_eval_judge(request: Request):
    """对 trace 中非零命中检索跑 LLM judge（DeepSeek 相关度 0-1）。

    需 DEEPSEEK_API_KEY；无 key 或 judge 失败时对应条目 score=null。
    返回逐条评分 + 低分列表，前端展示（人工确认低分项）。
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    limit = int(body.get("limit") or 0)
    concurrency = int(body.get("concurrency") or 3)
    q = (body.get("q") or "").strip() or None
    kind = (body.get("kind") or "").strip() or None

    from src.shared.rag_trace import filter_traces_for_api, load_traces

    traces = filter_traces_for_api(
        load_traces(),
        kind=kind,
        q=q,
        limit=limit or 200,
        active_session_ids=_active_session_ids(request),
    )
    targets = [t for t in traces if t.get("hits")]
    if limit > 0:
        targets = targets[:limit]
    results: list[dict] = []
    try:
        from scripts.dev.eval_dialogue.judge_self import judge_relevance
    except ImportError:
        return {"results": [], "error": "judge_self 不可用（scripts/dev 缺失）"}

    def _judge(t: dict) -> dict:
        # 与 build_case_list 的 query 截断保持一致（[:160]），否则长 query 的
        # LLM 分数在前端按 query 关联 case 时永远匹配不上。
        query = (t.get("query") or "").strip()[:160]
        contexts = [
            (h.get("preview") or "")[:300]
            for h in (t.get("hits") or [])[:5]
            if (h.get("preview") or "").strip()
        ]
        if not query or not contexts:
            return {"query": query, "channel": t.get("channel") or "",
                    "score": 0.0, "reason": "无上下文", "ts": t.get("ts")}
        try:
            r = judge_relevance(query, contexts)
        except Exception as exc:  # noqa: BLE001
            return {"query": query, "channel": t.get("channel") or "",
                    "score": None, "reason": f"judge 失败: {exc}", "ts": t.get("ts")}
        return {"query": query, "channel": t.get("channel") or "",
                "score": r.get("score"), "reason": r.get("reason", ""),
                "ts": t.get("ts"), "preview": contexts[0] if contexts else ""}

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        results = list(ex.map(_judge, targets))
    scored = [r for r in results if r.get("score") is not None]
    low = sorted(
        [r for r in scored if r["score"] < 0.5], key=lambda r: r["score"]
    )
    return {
        "results": results,
        "summary": {
            "judged": len(scored),
            "avg_score": round(sum(r["score"] for r in scored) / len(scored), 3) if scored else 0,
            "low_count": len(low),
        },
        "low": low[:20],
    }


@router.post("/api/v1/monitor/web-vitals")
async def ingest_web_vitals(request: Request):
    try:
        await request.body()
    except Exception:
        pass
    return Response(status_code=204)


@router.get("/api/v1/agent/health/live")
async def health_live():
    return {"status": "ok"}


@router.get("/api/v1/agent/health/ready")
async def health_ready(request: Request):
    token_ok = get_api_token() is not None
    config = request.app.state.load_config()
    payload = check_readiness(
        token_configured=token_ok,
        config_ok=config is not None,
        config_error=request.app.state.get_config_error(),
    )
    # Keep legacy flat fields for existing clients/tests.
    payload["token_configured"] = token_ok
    payload["config_ok"] = config is not None
    payload["error"] = request.app.state.get_config_error()
    if not payload["ready"]:
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/api/v1/agent/health")
async def health(request: Request):
    config = request.app.state.load_config()
    conversation = request.app.state.conversation
    job_meta: dict = {}
    try:
        import os

        from src.shared.async_jobs import get_job_runner

        runner = get_job_runner()
        job_meta = {
            "job_backend": os.getenv("AGENT_JOB_BACKEND", "sqlite"),
            "runner_started": runner.started,
            "concurrency": runner.concurrency,
            "jobs_in_flight": runner.in_flight,
            "type_limits": getattr(runner, "type_limits", {}),
        }
    except Exception:
        pass
    return {
        "status": "ready" if config else "no_config",
        "model": config.model if config else "unavailable",
        "error": request.app.state.get_config_error(),
        "sessions_active": conversation.active_sessions,
        "sessions_max": conversation.max_sessions,
        **job_meta,
        "probes": {
            "live": "/api/v1/agent/health/live",
            "ready": "/api/v1/agent/health/ready",
            "metrics": "/metrics",
        },
    }


@router.get("/metrics")
async def metrics():
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
