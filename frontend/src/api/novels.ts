/** 小说导入与书目链路：upload / novels / 系列改名 / 删除 */
import { API } from '@/lib/constants';
import { pollJob, uploadPollOptions } from '@/lib/pollJob';
import type { NovelVolumeInfo, UploadJobInfo } from '@/types';

import { request, ApiError, extractErrorDetail, qs } from './http';
import { fetchUploadJob } from './jobs';

export async function fetchNovels(
  seriesId?: string,
  signal?: AbortSignal,
): Promise<NovelVolumeInfo[]> {
  const url = seriesId
    ? `${API.NOVELS}?series_id=${encodeURIComponent(seriesId)}`
    : API.NOVELS;
  const res = await fetch(url, { signal });
  if (!res.ok) throw new ApiError('无法加载书目列表', res.status);
  const body = (await res.json()) as { items?: NovelVolumeInfo[] };
  return body.items || [];
}

export async function fetchOrphanDocIds(signal?: AbortSignal): Promise<string[]> {
  const res = await fetch(API.NOVELS, { signal });
  if (!res.ok) return [];
  const body = (await res.json()) as { orphan_doc_ids?: string[] };
  return body.orphan_doc_ids || [];
}

export interface UploadResult {
  status: string;
  doc_id: string;
  series_id: string;
  characters?: string[];
  roster?: Array<{ name: string }>;
  blocks?: Record<string, number>;
  hint?: string;
  job_id?: string;
}

export async function uploadNovel(
  file: File,
  opts?: {
    series_id?: string;
    series_title?: string;
    doc_id?: string;
    volume_no?: number;
    generate_qa?: boolean;
    generate_character_llm?: boolean;
  },
  onProgress?: (progress: UploadJobInfo['progress'], state: string) => void,
  signal?: AbortSignal,
): Promise<UploadResult> {
  const params: Record<string, unknown> = {
    series_id: opts?.series_id,
    series_title: opts?.series_title,
    doc_id: opts?.doc_id,
    volume_no: opts?.volume_no,
    generate_qa: opts?.generate_qa ?? false,
    generate_character_llm: opts?.generate_character_llm ?? false,
  };
  const form = new FormData();
  form.append('file', file);
  const res = await fetch(`${API.UPLOAD}${qs(params)}`, {
    method: 'POST',
    body: form,
    signal,
  });
  if (!res.ok) {
    const detail = await extractErrorDetail(res);
    throw new ApiError(detail, res.status);
  }
  const body = (await res.json()) as UploadJobInfo & {
    status?: string;
    doc_id?: string;
    series_id?: string;
    characters?: string[];
    roster?: Array<{ name: string }>;
    blocks?: Record<string, number>;
    hint?: string;
  };

  if (body.job_id) {
    const poll = uploadPollOptions(file.size);
    const final = await pollJob({
      fetchJob: () => fetchUploadJob(body.job_id),
      onProgress: (p, state) => onProgress?.(p, state),
      intervalMs: poll.intervalMs,
      maxTries: poll.maxTries,
      timeoutMessage: poll.timeoutMessage,
      signal,
    });
    if (final.state === 'failed') {
      throw new ApiError(final.error || '导入失败', 400);
    }
    const f = final as unknown as UploadJobInfo & {
      roster?: Array<{ name: string }>;
      blocks?: Record<string, number>;
    };
    return {
      status: f.status || 'ok',
      doc_id: f.doc_id || '',
      series_id: f.series_id || '',
      characters: f.characters,
      roster: f.roster,
      blocks: f.blocks,
      hint: f.hint,
      job_id: body.job_id,
    };
  }

  return body as UploadResult;
}

export async function deleteNovelVolume(
  docId: string,
  seriesId?: string,
): Promise<{ deleted_blocks: number; doc_id: string; series_id?: string }> {
  const q = seriesId ? `?series_id=${encodeURIComponent(seriesId)}` : '';
  return request(`${API.DELETE_NOVEL(docId)}${q}`, { method: 'DELETE' });
}

export async function renameSeries(
  seriesId: string,
  seriesTitle: string,
): Promise<{ series_id: string; series_title: string }> {
  return request(API.NOVEL_SERIES(seriesId), {
    method: 'PATCH',
    body: { series_title: seriesTitle },
  });
}
