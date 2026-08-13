"""Unit tests for HITL approval SQLite persistence (restart-safe).

模拟「重启」：旧实例写入 DB 后，用新实例（内存为空）验证记录可从 DB
读回 / pending 被标记 expired。DB 路径由 conftest 的 ``_sandbox_data_paths``
自动重定向到临时目录，不污染真实 data/。
"""

from src.shared.tool_approvals import ToolApprovalService


class TestApprovalPersistence:
    async def test_request_persists_pending(self):
        svc = ToolApprovalService()
        rec = await svc.request(
            session_id="s1", tool_name="execute_code", tool_args={"code": "print(1)"}
        )
        loaded = svc._load_db(rec.approval_id)
        assert loaded is not None
        assert loaded.status == "pending"
        assert loaded.session_id == "s1"
        assert loaded.tool_name == "execute_code"
        assert loaded.tool_args == {"code": "print(1)"}

    async def test_decide_updates_db(self):
        svc = ToolApprovalService()
        rec = await svc.request(session_id="s1", tool_name="execute_code", tool_args={})
        decided = await svc.decide(rec.approval_id, approved=True, reason="allow")
        assert decided.status == "approved"
        loaded = svc._load_db(rec.approval_id)
        assert loaded.status == "approved"
        assert loaded.reason == "allow"

    async def test_get_falls_back_to_db_after_restart(self):
        # svc1 写入并审批；svc2（新实例，内存空）从 DB 读回
        svc1 = ToolApprovalService()
        rec = await svc1.request(session_id="s1", tool_name="execute_code", tool_args={})
        await svc1.decide(rec.approval_id, approved=True)

        svc2 = ToolApprovalService()
        loaded = svc2.get(rec.approval_id)
        assert loaded is not None
        assert loaded.status == "approved"

    async def test_restart_expires_pending(self):
        # 重启前遗留 pending；重启后（新实例）应被标记 expired 而非继续挂起
        svc1 = ToolApprovalService()
        rec = await svc1.request(session_id="s1", tool_name="execute_code", tool_args={})

        svc2 = ToolApprovalService()
        expired = svc2.expire_stale_pending()
        assert expired == 1
        loaded = svc2.get(rec.approval_id)
        assert loaded.status == "expired"
        assert "restart" in loaded.reason

    async def test_get_unknown_returns_none(self):
        svc = ToolApprovalService()
        assert svc.get("appr_nonexistent") is None

    async def test_cleanup_removes_old_db_records(self):
        svc = ToolApprovalService()
        rec = await svc.request(session_id="s1", tool_name="execute_code", tool_args={})
        await svc.decide(rec.approval_id, approved=False, reason="deny")
        # 强制把记录时间戳拨到很久以前，然后清理
        svc._pending[rec.approval_id].created_at = 0
        svc._persist(svc._pending[rec.approval_id])
        svc.cleanup_older_than(max_age_seconds=3600.0)
        # 内存与 DB 都被清掉
        assert rec.approval_id not in svc._pending
        assert svc._load_db(rec.approval_id) is None
