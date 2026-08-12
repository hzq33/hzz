"""Character knowledge tool — list / build / merge for the general agent."""

from __future__ import annotations

import logging
from typing import Any

from src.tools.base import BaseTool, ToolResult

logger = logging.getLogger("agent")


class CharacterKbTool(BaseTool):
    """Manage novel character roster, cards, and merges."""

    name: str = "character_kb"
    description: str = (
        "小说角色知识库：列名录/人设卡、按需建卡、合并近音重名、查看建卡任务。"
        "扮演多轮对话请走角色扮演页，不要用本工具冒充会话。"
        "写操作（build/merge/update）可能需人工审批。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "candidates",
                    "get",
                    "build",
                    "job_status",
                    "merge_suggest",
                    "merge",
                    "update",
                ],
                "description": (
                    "list=已建卡/名录；candidates=候选；get=读卡；"
                    "build=建卡；job_status=任务；merge_suggest/merge=合并；update=改卡"
                ),
            },
            "series_id": {
                "type": "string",
                "description": "系列 ID（与 catalog series_id 一致）",
            },
            "name": {
                "type": "string",
                "description": "角色名（get/update/build 单人时）",
            },
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "角色名列表（build/merge）",
            },
            "survivor": {
                "type": "string",
                "description": "合并后保留的规范名（merge）",
            },
            "doc_id": {
                "type": "string",
                "description": "可选：建卡时锁定单卷",
            },
            "job_id": {
                "type": "string",
                "description": "建卡任务 ID（job_status）",
            },
            "force": {
                "type": "boolean",
                "description": "build 时强制重建已有卡",
                "default": False,
            },
            "q": {
                "type": "string",
                "description": "list 名称过滤",
            },
            "personality": {
                "type": "string",
                "description": "性格描述（仅 update：覆盖角色卡 personality 字段）",
            },
            "speaking_style": {
                "type": "string",
                "description": "说话风格描述（仅 update：覆盖角色卡 speaking_style 字段）",
            },
            "background": {
                "type": "string",
                "description": "背景设定（仅 update：覆盖角色卡 background 字段）",
            },
            "min_score": {
                "type": "number",
                "description": "merge_suggest 拼音相似度阈值，默认 0.92",
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
            if action == "list":
                return self._list(kwargs)
            if action == "candidates":
                return self._candidates(kwargs)
            if action == "get":
                return self._get(kwargs)
            if action == "build":
                return await self._build(kwargs)
            if action == "job_status":
                return self._job_status(kwargs)
            if action == "merge_suggest":
                return self._merge_suggest(kwargs)
            if action == "merge":
                return self._merge(kwargs)
            if action == "update":
                return self._update(kwargs)
            return ToolResult.fail(f"Unknown action: {action}")
        except ValueError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            logger.exception("CharacterKbTool error")
            return ToolResult.fail(f"character_kb 执行失败: {e}")

    def _series_ids(self, series_id: str | None) -> list[str]:
        if series_id and series_id.strip():
            return [series_id.strip()]
        from src.application.novel.services.catalog_service import list_catalogs

        ids = [c.series_id for c in list_catalogs() if c.series_id]
        return sorted(set(ids))

    def _list(self, kwargs: dict) -> ToolResult:
        from src.domain.character_card import CharacterCard
        from src.application.novel.services.character_query_service import load_roster

        q = (kwargs.get("q") or "").strip()
        lines = ["## 角色名录", ""]
        total = 0
        for sid in self._series_ids(kwargs.get("series_id")):
            roster = load_roster(sid)
            if not roster or not roster.characters:
                continue
            lines.append(f"### {sid}")
            for entry in roster.characters:
                if q and q not in entry.name and not any(
                    q in a for a in (entry.aliases_observed or [])
                ):
                    continue
                card = CharacterCard.load_for_series(
                    sid, entry.name, character_id=entry.character_id or ""
                )
                flag = "已建卡" if (entry.has_card or card) else "未建卡"
                lines.append(
                    f"- {entry.name} | {flag} | status={entry.status} | "
                    f"对话样本≈{entry.dialogue_count}"
                )
                total += 1
            lines.append("")
        if total == 0:
            return ToolResult.ok("无角色名录。请先导入小说或指定 series_id。")
        lines.append(f"合计 {total} 人。建卡用 action=build + names。")
        return ToolResult.ok("\n".join(lines))

    def _candidates(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        if not sid:
            return ToolResult.fail("candidates 需要 series_id")
        from src.application.novel.services.character_query_service import (
            load_inventory_candidates,
        )

        inv = load_inventory_candidates(sid) or {}
        cands = inv.get("candidates") or []
        lines = [f"## 候选角色 — {sid}", f"共 {len(cands)} 人", ""]
        for c in cands[:40]:
            name = c.get("name") or ""
            lines.append(
                f"- {name} | mentions={c.get('mention_count', 0)} | "
                f"seed={bool(c.get('in_llm_seed'))}"
            )
        if len(cands) > 40:
            lines.append(f"…另有 {len(cands) - 40} 人未列出")
        return ToolResult.ok("\n".join(lines))

    def _get(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        name = (kwargs.get("name") or "").strip()
        if not sid or not name:
            return ToolResult.fail("get 需要 series_id 与 name")
        from src.domain.character_card import CharacterCard

        card = CharacterCard.load_for_series(sid, name)
        if not card:
            return ToolResult.ok(f"未找到人设卡：{sid} / {name}。可用 build 生成。")
        samples = [
            d.get("content", "") for d in (card.sample_dialogues or [])[:3] if d.get("content")
        ]
        lines = [
            f"## {card.name} — {sid}",
            f"personality: {card.personality or '—'}",
            f"speaking_style: {card.speaking_style or '—'}",
            f"background: {(card.background or '—')[:500]}",
            "",
            "样本对白:",
        ]
        if samples:
            lines.extend(f"- {s[:120]}" for s in samples)
        else:
            lines.append("- （无）")
        return ToolResult.ok("\n".join(lines))

    async def _build(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        names = [n.strip() for n in (kwargs.get("names") or []) if n and str(n).strip()]
        if not names and kwargs.get("name"):
            names = [str(kwargs["name"]).strip()]
        if not sid or not names:
            return ToolResult.fail("build 需要 series_id 与 names（或 name）")
        from src.application.novel.services.character_build_service import enqueue_builds

        llm = None
        try:
            from src.application.novel.factory import create_impersonation_service

            svc = create_impersonation_service()
            llm = getattr(svc, "llm_client", None) or getattr(svc, "llm", None)
        except Exception:
            llm = None
        jobs = await enqueue_builds(
            series_id=sid,
            names=names,
            store=self._get_store(),
            doc_id=(kwargs.get("doc_id") or None),
            force=bool(kwargs.get("force")),
            llm_client=llm,
            wait=False,
        )
        lines = [f"已入队建卡 {len(jobs)} 个任务（series={sid}）", ""]
        for job in jobs:
            d = job.to_dict() if hasattr(job, "to_dict") else dict(job)
            lines.append(
                f"- job_id={d.get('job_id')} name={d.get('input_name') or d.get('name')} "
                f"state={d.get('state')}"
            )
        lines.append("用 action=job_status + job_id 查询进度。")
        return ToolResult.ok("\n".join(lines))

    def _job_status(self, kwargs: dict) -> ToolResult:
        from src.application.novel.services.character_build_service import get_job, list_jobs

        job_id = (kwargs.get("job_id") or "").strip()
        if job_id:
            job = get_job(job_id)
            if not job:
                return ToolResult.fail(f"Job not found: {job_id}")
            d = job.to_dict()
            return ToolResult.ok(
                f"job_id={d.get('job_id')} name={d.get('input_name')} "
                f"state={d.get('state')} error={d.get('error') or '—'}"
            )
        sid = (kwargs.get("series_id") or "").strip() or None
        jobs = list_jobs(series_id=sid, limit=20)
        if not jobs:
            return ToolResult.ok("无建卡任务。")
        lines = ["## 建卡任务", ""]
        for job in jobs:
            d = job.to_dict()
            lines.append(
                f"- {d.get('job_id')} | {d.get('input_name')} | {d.get('state')}"
            )
        return ToolResult.ok("\n".join(lines))

    def _merge_suggest(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        if not sid:
            return ToolResult.fail("merge_suggest 需要 series_id")
        from src.application.novel.services.character_merge_service import suggest_merges

        min_score = float(kwargs.get("min_score") or 0.92)
        suggestions = suggest_merges(sid, min_score=min_score)
        if not suggestions:
            return ToolResult.ok(f"{sid}：无合并建议（min_score={min_score}）。")
        lines = [f"## 合并建议 — {sid}", ""]
        for s in suggestions:
            d = s.to_dict() if hasattr(s, "to_dict") else dict(s)
            lines.append(
                f"- survivor={d.get('survivor')} ← {d.get('names') or d.get('merge_names')} "
                f"score={d.get('score')}"
            )
        lines.append("确认后 action=merge + survivor + names。")
        return ToolResult.ok("\n".join(lines))

    def _merge(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        survivor = (kwargs.get("survivor") or "").strip()
        names = [n.strip() for n in (kwargs.get("names") or []) if n and str(n).strip()]
        if not sid or not survivor or not names:
            return ToolResult.fail("merge 需要 series_id、survivor、names")
        from src.application.novel.services.character_merge_service import merge

        if survivor not in names:
            names = [survivor, *names]
        result = merge(series_id=sid, survivor=survivor, merge_names=names)
        d = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        return ToolResult.ok(
            f"已合并 → survivor={d.get('survivor')}；"
            f"merged={d.get('merged_names')}"
        )

    def _update(self, kwargs: dict) -> ToolResult:
        sid = (kwargs.get("series_id") or "").strip()
        name = (kwargs.get("name") or "").strip()
        if not sid or not name:
            return ToolResult.fail("update 需要 series_id 与 name")
        from src.domain.character_card import CharacterCard

        card = CharacterCard.load_for_series(sid, name)
        if not card:
            return ToolResult.fail(f"无人设卡可更新：{sid}/{name}")
        if kwargs.get("personality") is not None:
            card.personality = str(kwargs["personality"])
        if kwargs.get("speaking_style") is not None:
            card.speaking_style = str(kwargs["speaking_style"])
        if kwargs.get("background") is not None:
            card.background = str(kwargs["background"])
        CharacterCard.save_for_series(sid, name, card)
        return ToolResult.ok(f"已更新人设卡：{sid}/{name}")
