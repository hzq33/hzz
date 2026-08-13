"""Unit tests for HITL approval gate & cleanup."""

import asyncio

import pytest

from src.shared.tool_approvals import (
    PendingApproval,
    ToolApprovalService,
    gate_tool_execution,
    requires_approval,
)


class TestRequiresApproval:
    def test_disabled_hitl(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "0")
        assert requires_approval("execute_code", {}) is False
        assert requires_approval("file_operation", {"operation": "write"}) is False

    def test_execute_code_requires(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        assert requires_approval("execute_code", {}) is True

    def test_file_write_requires(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        assert requires_approval("file_operation", {"operation": "write"}) is True
        assert requires_approval("file_operation", {"operation": "read"}) is False

    def test_novel_admin_actions(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        assert requires_approval("novel_admin", {"action": "delete_volume"}) is True
        assert requires_approval("novel_admin", {"action": "rename_series"}) is True
        assert requires_approval("novel_admin", {"action": "list"}) is False

    def test_character_kb_actions(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        assert requires_approval("character_kb", {"action": "build"}) is True
        assert requires_approval("character_kb", {"action": "read"}) is False


class TestCleanupSkipDecided:
    def _make_rec(self, status: str) -> PendingApproval:
        rec = PendingApproval(
            approval_id="a1", session_id="s", tool_name="t", tool_args={}
        )
        rec.status = status
        rec.created_at = 0  # 很久以前，必然超过 cutoff
        return rec

    def test_skip_decided_keeps_approved(self):
        svc = ToolApprovalService()
        svc._pending["a1"] = self._make_rec("approved")
        svc._events["a1"] = asyncio.Event()
        removed = svc.cleanup_older_than(max_age_seconds=3600, skip_decided=True)
        assert removed == 0
        assert "a1" in svc._pending

    def test_skip_decided_keeps_denied(self):
        svc = ToolApprovalService()
        svc._pending["a1"] = self._make_rec("denied")
        svc._events["a1"] = asyncio.Event()
        removed = svc.cleanup_older_than(max_age_seconds=3600, skip_decided=True)
        assert removed == 0
        assert "a1" in svc._pending

    def test_skip_decided_clears_expired(self):
        svc = ToolApprovalService()
        svc._pending["a1"] = self._make_rec("expired")
        svc._events["a1"] = asyncio.Event()
        removed = svc.cleanup_older_than(max_age_seconds=3600, skip_decided=True)
        assert removed == 1
        assert "a1" not in svc._pending

    def test_full_sweep_clears_decided(self):
        svc = ToolApprovalService()
        svc._pending["a1"] = self._make_rec("approved")
        svc._events["a1"] = asyncio.Event()
        removed = svc.cleanup_older_than(max_age_seconds=3600)  # skip_decided=False
        assert removed == 1
        assert "a1" not in svc._pending


class TestGateToolExecution:
    async def test_no_emit_channel_denies(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        denied = await gate_tool_execution(
            session_id="s", tool_name="execute_code", tool_args={}
        )
        assert denied is not None
        assert "no approval channel" in denied

    async def test_no_hitl_required_allows(self, monkeypatch):
        monkeypatch.setenv("AGENT_TOOL_HITL", "1")
        denied = await gate_tool_execution(
            session_id="s", tool_name="file_operation", tool_args={"operation": "read"}
        )
        assert denied is None
