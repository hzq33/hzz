/** 角色扮演链路：impersonate chat/stream / sessions / reset / regenerate / history */
import { API } from '@/lib/constants';
import type {
  ImpersonationSessionSummary,
  ImpersonationHistory,
  MemoryStats,
} from '@/types';

import { request, ApiError, extractErrorDetail } from './http';

export interface ImpersonateResponse {
  reply: string;
  character: string;
  session_id: string;
  citations: Array<Record<string, unknown>>;
  memory_stats?: MemoryStats | null;
}

export async function impersonateChat(
  character: string,
  message: string,
  sessionId?: string | null,
  docId?: string | null,
): Promise<ImpersonateResponse> {
  return request<ImpersonateResponse>('/api/v1/agent/impersonate/chat', {
    method: 'POST',
    body: {
      character,
      message,
      session_id: sessionId || undefined,
      doc_id: docId || undefined,
    },
  });
}

export async function impersonateReset(
  character: string,
  sessionId?: string | null,
): Promise<{ session_id: string; message: string }> {
  return request('/api/v1/agent/impersonate/reset', {
    method: 'POST',
    body: { character, session_id: sessionId || undefined },
  });
}

export async function impersonateRegenerate(
  character: string,
  sessionId?: string | null,
): Promise<ImpersonateResponse> {
  return request('/api/v1/agent/impersonate/regenerate', {
    method: 'POST',
    body: { character, session_id: sessionId || undefined },
  });
}

/* ── 会话管理 ── */

export async function listImpersonationSessions(
  limit = 50,
): Promise<ImpersonationSessionSummary[]> {
  const body = await request<{ items?: ImpersonationSessionSummary[] }>(
    `${API.IMP_SESSIONS}?limit=${limit}`,
  );
  return body.items || [];
}

export async function fetchImpersonationHistory(
  sessionId: string,
): Promise<ImpersonationHistory> {
  return request(API.IMP_HISTORY(sessionId));
}

export async function renameImpersonationSession(
  sessionId: string,
  title: string,
): Promise<{ session_id: string; title: string }> {
  return request(API.IMP_SESSION(sessionId), {
    method: 'PATCH',
    body: { title },
  });
}

export async function deleteImpersonationSession(sessionId: string): Promise<void> {
  const res = await fetch(API.IMP_SESSION(sessionId), { method: 'DELETE' });
  if (!res.ok) {
    const detail = await extractErrorDetail(res);
    throw new ApiError(detail, res.status);
  }
}
