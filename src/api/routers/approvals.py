"""Tool approval (HITL) endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.shared.tool_approvals import get_approval_service

router = APIRouter(prefix="/api/v1/agent")


class ApprovalDecision(BaseModel):
    approval_id: str = Field(..., description="Pending approval id")
    approved: bool = Field(..., description="True to allow tool execution")
    reason: str = Field("", description="Optional note")
    session_id: str | None = Field(
        default=None,
        description="Optional session binding — when provided it must match the "
        "session that requested the approval (frontend echoes it from the "
        "approval_required SSE event)",
    )


@router.post("/tools/approve")
async def decide_tool_approval(body: ApprovalDecision):
    svc = get_approval_service()
    rec = svc.get(body.approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    # 会话绑定校验：调用方声明了 session_id 时必须与发起审批的会话一致，
    # 防止同 token 下其他会话/页面误批他人发起的 HITL 请求。
    # （未传 session_id 保持向后兼容，不做强制。）
    if (
        body.session_id
        and rec.session_id
        and body.session_id != rec.session_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Session mismatch: this approval belongs to a different session",
        )
    decided = await svc.decide(
        body.approval_id, approved=body.approved, reason=body.reason
    )
    if decided is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return {
        "approval_id": decided.approval_id,
        "status": decided.status,
        "tool_name": decided.tool_name,
        "reason": decided.reason,
    }


@router.get("/tools/approvals/{approval_id}")
async def get_tool_approval(approval_id: str):
    rec = get_approval_service().get(approval_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return rec.to_public_dict()
