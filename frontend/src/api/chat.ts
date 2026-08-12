/** 通用对话链路：chat / stream / history / tools / 审批 */
import { API } from '@/lib/constants';
import { parseJSONSafe, readSSEStream } from '@/lib/sse';
import type {
  ToolInfo,
  StreamEventData,
  ChatMessage,
  PlanEvent,
  StepResultEvent,
} from '@/types';

import { request, ApiError, extractErrorDetail } from './http';

/* ── 工具列表 ── */

export async function fetchTools(): Promise<ToolInfo[]> {
  const data = await request<ToolInfo[]>(API.TOOLS);
  return data ?? [];
}

/* ── HITL 审批 ── */

export async function decideToolApproval(
  approvalId: string,
  approved: boolean,
  reason = '',
): Promise<{ approval_id: string; status: string }> {
  return request(API.TOOL_APPROVE, {
    method: 'POST',
    body: { approval_id: approvalId, approved, reason },
  });
}

export async function fetchApproval(
  approvalId: string,
): Promise<{ approval_id: string; tool_name: string; tool_args?: Record<string, unknown>; status: string }> {
  return request(`${API.TOOL_APPROVE.replace('/approve', '')}/approvals/${approvalId}`);
}

/* ── 流式对话 ── */

export interface StreamCallbacks {
  onEvent: (event: StreamEventData) => void;
  onError: (error: Error) => void;
}

export async function streamChat(
  message: string,
  sessionId: string | null,
  callbacks: StreamCallbacks,
  signal?: AbortSignal,
  novelScope?: { series_id?: string; doc_ids?: string[] },
): Promise<void> {
  const res = await fetch(API.CHAT_STREAM, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      novel_scope: novelScope || undefined,
    }),
    signal,
  });
  if (!res.ok) {
    const detail = await extractErrorDetail(res);
    throw new ApiError(detail, res.status);
  }
  if (!res.body) throw new Error('服务未返回内容');
  try {
    await readSSEStream(
      res.body,
      (jsonStr) => {
        const event = parseJSONSafe<StreamEventData>(jsonStr);
        if (event) callbacks.onEvent(event);
      },
      signal,
    );
  } catch (e) {
    if (signal?.aborted) return;
    if (e instanceof ApiError) throw e;
    throw new Error(e instanceof Error ? e.message : '流式响应中断');
  }
}

/* ── 历史 ── */

export async function fetchHistory(sessionId: string): Promise<ChatMessage[]> {
  const res = await fetch(API.HISTORY(sessionId));
  if (!res.ok) throw new ApiError('无法加载历史', res.status);
  const body = (await res.json()) as { items?: ChatMessage[]; messages?: ChatMessage[] };
  return body.items || body.messages || [];
}

export async function clearHistory(sessionId: string): Promise<void> {
  await fetch(API.HISTORY(sessionId), { method: 'DELETE' });
}

export const clearSession = clearHistory;

/* ── 非流式兜底对话 ── */

export async function chatOnce(
  message: string,
  sessionId?: string | null,
): Promise<{ reply: string; session_id?: string }> {
  return request('/api/v1/agent/chat', {
    method: 'POST',
    body: { message, session_id: sessionId || undefined },
  });
}

/* ── 消息组装工具（plan/step 转 UI） ── */

export function planToText(plan?: PlanEvent): string {
  if (!plan) return '';
  const lines: string[] = [];
  if (plan.goal) lines.push(`目标：${plan.goal}`);
  if (plan.reasoning) lines.push(`思路：${plan.reasoning}`);
  (plan.steps || []).forEach((s, i) => {
    lines.push(`${i + 1}. ${s.description}${s.tool_name ? `（${s.tool_name}）` : ''}`);
  });
  return lines.join('\n');
}

export function stepResultsToText(results: StepResultEvent[]): string {
  return (results || [])
    .map((r) => `步骤${r.step_id} ${r.success ? '✓' : '✗'} ${r.tool_name || ''}: ${(r.output || r.error || '').slice(0, 400)}`)
    .join('\n');
}
