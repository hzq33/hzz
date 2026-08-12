"""Story analysis tool — timeline / foreshadow / relations for the agent."""

from __future__ import annotations

import logging
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")


class StoryAnalysisTool(BaseTool):
    """Read or build on-demand story analysis snapshots."""

    name: str = "story_analysis"
    description: str = (
        "小说剧情脉络：读取或生成时间线事件、伏笔、人物关系（带原文证据）。"
        "get=读已有快照；build=触发分析（可能需审批）；job_status=查异步任务。"
        "需要 series_id；可选 doc_id 锁单卷。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "build", "job_status"],
                "description": "get=读取；build=生成；job_status=任务状态",
            },
            "series_id": {
                "type": "string",
                "description": "系列 ID",
            },
            "doc_id": {
                "type": "string",
                "description": "可选：限定单卷",
            },
            "job_id": {
                "type": "string",
                "description": "build 返回的任务 ID",
            },
            "wait": {
                "type": "boolean",
                "description": "build 时是否同步等待（默认 false，异步）",
                "default": False,
            },
        },
        "required": ["action"],
    }

    def __init__(self, store=None):
        self._store = store

    def _get_store(self):
        if self._store is not None:
            return self._store
        from src.application.novel.factory import create_novel_store

        self._store = create_novel_store()
        return self._store

    def inject_store(self, store) -> None:
        """注入共享 store（上传新书后由 server 统一广播，保证索引一致）。"""
        self._store = store

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            self.validate_args(kwargs)
            action = kwargs["action"]
            if action == "get":
                return self._get(kwargs)
            if action == "build":
                return await self._build(kwargs)
            if action == "job_status":
                return self._job_status(kwargs)
            return ToolResult.fail(f"Unknown action: {action}")
        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.exception("StoryAnalysisTool error")
            return ToolResult.fail(f"story_analysis 执行失败: {e}")

    def _get(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        if not sid:
            return ToolResult.fail("get 需要 series_id")
        from src.application.novel.services.story_analysis_service import load_analysis

        snapshot = load_analysis(sid)
        if not snapshot:
            return ToolResult.ok(
                f"{sid}：尚无剧情分析。可用 action=build 生成。"
            )
        data = snapshot.to_dict()
        doc_id = (kwargs.get("doc_id") or "").strip()
        events = data.get("events") or []
        fores = data.get("foreshadows") or []
        rels = data.get("relations") or []
        if doc_id:
            events = [e for e in events if e.get("doc_id") == doc_id]
            rels = [r for r in rels if r.get("doc_id") == doc_id]
            fores = [
                f
                for f in fores
                if f.get("introduced_doc_id") == doc_id
                or f.get("resolved_doc_id") == doc_id
            ]
        lines = [
            f"## 关系与事件索引 — {sid}",
            f"events={len(events)} relations={len(rels)} foreshadows={len(fores)}",
            "",
            "### 事件（最多 12）",
        ]
        for e in events[:12]:
            lines.append(
                f"- [{e.get('chapter_title') or e.get('doc_id')}] "
                f"{(e.get('summary') or e.get('title') or '')[:120]}"
            )
        if fores:
            lines.append("")
            lines.append("### 伏笔（最多 8，可选）")
            for f in fores[:8]:
                lines.append(
                    f"- {(f.get('description') or f.get('content') or '')[:120]}"
                )
        lines.append("")
        lines.append("### 关系变化（最多 8）")
        for r in rels[:8]:
            src = r.get("source") or ""
            tgt = r.get("target") or ""
            kind = r.get("relation_type") or ""
            summary = (r.get("summary") or "")[:80]
            label = f"{src}-{tgt}" if src or tgt else "?"
            extra = f" ({kind})" if kind else ""
            lines.append(f"- {label}{extra}: {summary}")
        return ToolResult.ok("\n".join(lines))

    async def _build(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        if not sid:
            return ToolResult.fail("build 需要 series_id")
        import uuid

        from src.application.novel.services.story_analysis_service import run_story_analysis
        from src.shared.async_jobs import JobRecord

        store = self._get_store()
        llm = None
        try:
            from src.application.novel.factory import create_impersonation_service

            svc = create_impersonation_service()
            llm = getattr(svc, "llm_client", None) or getattr(svc, "llm", None)
        except Exception:
            llm = None

        doc_id = (kwargs.get("doc_id") or "").strip() or None
        wait = bool(kwargs.get("wait"))
        if wait:
            snapshot = await run_story_analysis(
                series_id=sid,
                store=store,
                llm_client=llm,
                doc_id=doc_id,
            )
            data = snapshot.to_dict() if snapshot else {}
            n = len(data.get("events") or [])
            return ToolResult.ok(f"分析完成 series={sid} events≈{n}。可用 get 查看。")

        job = JobRecord(
            job_id=f"sa_{uuid.uuid4().hex[:12]}",
            job_type="story_analysis",
            state="pending",
            payload={"series_id": sid, "doc_id": doc_id, "force": False},
        )
        from src.application.jobs import submit_job

        submit_job(job)
        return ToolResult.ok(
            f"已入队剧情分析 job_id={job.job_id} series={sid}。"
            "用 action=job_status 查询。"
        )

    def _job_status(self, kwargs: dict) -> ToolResult:
        job_id = (kwargs.get("job_id") or "").strip()
        if not job_id:
            return ToolResult.fail("job_status 需要 job_id")
        from src.shared.async_jobs import get_job_store

        job = get_job_store().get(job_id, job_type="story_analysis")
        if not job:
            return ToolResult.fail(f"Job not found: {job_id}")
        d = job.to_dict() if hasattr(job, "to_dict") else dict(job)
        prog = d.get("progress") or {}
        prog_msg = prog.get("message") if isinstance(prog, dict) else ""
        extra = f" progress={prog_msg}" if prog_msg else ""
        return ToolResult.ok(
            f"job_id={d.get('job_id')} state={d.get('state')}{extra} "
            f"result={d.get('result') or d.get('error') or '—'}"
        )
