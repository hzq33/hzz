/** 世界体系链路：剧情分析 / 时间线 / 设定书 / GraphRAG / 对话重抽 */
import { API } from '@/lib/constants';
import type {
  StoryAnalysis,
  TimelineResponse,
  LorebookResponse,
  RagGlobalResponse,
} from '@/types';

import { request, qs } from './http';

/** Ensure UI empty-state sees completed snapshots. */
export function normalizeStoryAnalysis(
  analysis: StoryAnalysis | null | undefined,
): StoryAnalysis | null {
  if (!analysis) return null;
  if (analysis.exists === false) return analysis;
  if (analysis.exists === true) return analysis;
  const hasBody =
    (analysis.events?.length ?? 0) > 0 ||
    (analysis.foreshadows?.length ?? 0) > 0 ||
    (analysis.relations?.length ?? 0) > 0 ||
    Boolean(analysis.updated_at) ||
    Boolean((analysis as { content_fingerprint?: string }).content_fingerprint) ||
    Boolean(analysis.series_id);
  return { ...analysis, exists: hasBody };
}

export async function fetchStoryAnalysis(
  seriesId: string,
  docId?: string,
  signal?: AbortSignal,
): Promise<StoryAnalysis> {
  const data = await request<StoryAnalysis>(
    `${API.STORY_ANALYSIS}${qs({ series_id: seriesId, doc_id: docId })}`,
    { signal },
  );
  return normalizeStoryAnalysis(data)!;
}

export async function buildStoryAnalysis(body: {
  series_id: string;
  doc_id?: string;
  force?: boolean;
  wait?: boolean;
  max_chapters?: number;
}): Promise<{ job_id?: string; state?: string; analysis?: StoryAnalysis }> {
  return request(API.STORY_ANALYSIS_BUILD, {
    method: 'POST',
    body: { wait: false, ...body },
  });
}

export async function fetchTimeline(
  seriesId: string,
  signal?: AbortSignal,
): Promise<TimelineResponse> {
  return request(`${API.TIMELINE}${qs({ series_id: seriesId })}`, { signal });
}

export async function fetchLorebook(
  seriesId: string,
  signal?: AbortSignal,
): Promise<LorebookResponse> {
  return request(`${API.LOREBOOK}${qs({ series_id: seriesId })}`, { signal });
}

export async function fetchRagGlobal(
  seriesId: string,
  query?: string,
  signal?: AbortSignal,
): Promise<RagGlobalResponse> {
  return request(`${API.RAG_GLOBAL}${qs({ series_id: seriesId, query })}`, { signal });
}

export async function buildRagGlobal(
  seriesId: string,
  opts: { force?: boolean; wait?: boolean; signal?: AbortSignal } = {},
): Promise<{ job_id?: string; state?: string; communities?: number }> {
  return request(API.RAG_GLOBAL_BUILD, {
    method: 'POST',
    body: { series_id: seriesId, force: !!opts.force, wait: !!opts.wait },
    signal: opts.signal,
  });
}

/** 对话重抽（redialogue）：提交后轮询作业 */
export async function redialogue(
  docId: string,
  opts?: { mode?: string; force?: boolean; signal?: AbortSignal },
): Promise<{ job_id?: string; state?: string; message?: string }> {
  return request(`/api/v1/agent/novels/${encodeURIComponent(docId)}/redialogue`, {
    method: 'POST',
    body: { mode: opts?.mode, force: !!opts?.force },
    signal: opts?.signal,
  });
}
