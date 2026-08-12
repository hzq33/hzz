/** 评估中心链路：RAG 在线评估 / LLM 判定回收 */
import type { RagEvalResponse, RagJudgeResponse } from '@/types';

import { request, qs } from './http';

export async function fetchRagEval(params?: {
  kind?: string;
  channel?: string;
  q?: string;
  zero_only?: boolean;
  limit?: number;
}): Promise<RagEvalResponse> {
  return request(`/api/v1/agent/rag-eval${qs(params)}`, {});
}

export async function judgeRagEval(body?: {
  limit?: number;
  concurrency?: number;
  q?: string;
  kind?: string;
}): Promise<RagJudgeResponse> {
  return request('/api/v1/agent/rag-eval/judge', {
    method: 'POST',
    body: body ?? {},
  });
}
