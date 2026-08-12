/** 角色管线链路：列表 / 候选 / 建卡 / 合并 / 图谱 / 名录 / 更新 / 删除 */
import { API } from '@/lib/constants';
import type {
  CharacterInfo,
  CharacterGraph,
  CharacterBuildJobInfo,
  DisambiguationCandidate,
} from '@/types';

import { request, qs } from './http';

export async function fetchCharacters(params?: {
  series_id?: string;
  doc_id?: string;
  q?: string;
  include_candidates?: boolean;
  seed_only?: boolean;
  signal?: AbortSignal;
}): Promise<CharacterInfo[]> {
  const data = await request<CharacterInfo[]>(`${API.CHARACTERS}${qs({
    series_id: params?.series_id,
    doc_id: params?.doc_id,
    q: params?.q,
    include_candidates: params?.include_candidates === false ? 'false' : undefined,
    seed_only: params?.seed_only ? 'true' : undefined,
  })}`, { signal: params?.signal });
  return data ?? [];
}

export interface CharacterCandidate {
  name: string;
  aliases: string[];
  mention_count: number;
  importance: string;
  in_llm_seed: boolean;
  has_card: boolean;
  series_id: string;
}

export async function fetchCharacterCandidates(params: {
  series_id: string;
  q?: string;
  min_mentions?: number;
}): Promise<{
  series_id: string;
  seed_min_mentions: number;
  candidates_total: number;
  candidates: CharacterCandidate[];
}> {
  return request(`${API.CHARACTERS_CANDIDATES}${qs({
    series_id: params.series_id,
    q: params.q,
    min_mentions: params.min_mentions,
  })}`);
}

export async function updateCharacter(
  name: string,
  fields: {
    personality?: string;
    speaking_style?: string;
    background?: string;
    catchphrases?: string[];
    sample_dialogues?: string[];
    create_if_missing?: boolean;
  },
): Promise<{ message: string; prompt: string; path?: string; sample_count?: number }> {
  return request(`${API.CHARACTERS}/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: fields,
  });
}

export async function deleteCharacter(
  name: string,
  seriesId?: string,
): Promise<{ message: string; cards_removed?: string[]; roster_removed?: boolean; alias_removed?: boolean }> {
  const q = seriesId ? `?series_id=${encodeURIComponent(seriesId)}` : '';
  return request(`${API.CHARACTERS}/${encodeURIComponent(name)}${q}`, { method: 'DELETE' });
}

export async function buildCharacters(body: {
  series_id: string;
  names: string[];
  doc_id?: string;
  force?: boolean;
  wait?: boolean;
  resolve?: Record<string, string>;
}): Promise<{ jobs: CharacterBuildJobInfo[] }> {
  return request(API.CHARACTERS_BUILD, {
    method: 'POST',
    body: { wait: false, ...body },
  });
}

export type CharacterMergeSuggestion = {
  names: string[];
  survivor: string;
  score: number;
  reason: string;
};

export async function fetchMergeSuggestions(
  seriesId: string,
  minScore = 0.92,
  signal?: AbortSignal,
): Promise<{ series_id: string; suggestions: CharacterMergeSuggestion[] }> {
  return request(`${API.CHARACTERS_MERGE_SUGGESTIONS}${qs({
    series_id: seriesId,
    min_score: String(minScore),
  })}`, { signal });
}

export async function mergeCharacters(body: {
  series_id: string;
  survivor: string;
  names: string[];
}): Promise<{
  series_id: string;
  survivor: string;
  merged_names: string[];
  aliases: string[];
  character_id: string;
  message?: string;
}> {
  return request(API.CHARACTERS_MERGE, { method: 'POST', body });
}

export async function fetchCharacterGraph(
  seriesId: string,
  opts?: { docId?: string; minConfidence?: number; minWeight?: number },
  signal?: AbortSignal,
): Promise<CharacterGraph> {
  return request(`${API.CHARACTERS_GRAPH}${qs({
    series_id: seriesId,
    doc_id: opts?.docId,
    min_confidence: opts?.minConfidence,
    min_weight: opts?.minWeight,
  })}`, { signal });
}

/* ── 名录 Roster ── */

export interface RosterEntity {
  canonical_name: string;
  aliases: string[];
  importance: string;
  mention_count: number;
  dialogue_count?: number;
}

export async function fetchRoster(
  seriesId?: string,
): Promise<{ entities?: RosterEntity[]; series_id?: string }> {
  return request(`${API.CHARACTERS}/roster${qs({ series_id: seriesId })}`);
}

export async function fetchRosterSeries(): Promise<{ series: Array<{ series_id: string; title?: string }> }> {
  return request(`${API.CHARACTERS}/roster/series`);
}

export async function updateRoster(
  seriesId: string,
  entities: Array<{ canonical_name: string; importance?: string; aliases?: string[] }>,
): Promise<{ message: string; updated: number }> {
  return request(`${API.CHARACTERS}/roster`, {
    method: 'PUT',
    body: { series_id: seriesId, entities },
  });
}

export type { DisambiguationCandidate };
