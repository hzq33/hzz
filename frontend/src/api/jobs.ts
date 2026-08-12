/** 作业查询：upload / characters / story-analysis / rag-global / redialogue */
import { API } from '@/lib/constants';
import type { CharacterBuildJobInfo, UploadJobInfo, StoryAnalysis } from '@/types';

import { request } from './http';

export async function fetchUploadJob(jobId: string): Promise<UploadJobInfo> {
  return request(API.UPLOAD_JOB(jobId));
}

export async function fetchCharacterJob(jobId: string): Promise<CharacterBuildJobInfo> {
  return request(API.CHARACTER_JOB(jobId));
}

export async function fetchCharacterJobs(): Promise<{ items?: CharacterBuildJobInfo[] }> {
  return request(API.CHARACTER_JOBS);
}

export async function fetchStoryAnalysisJob(
  jobId: string,
): Promise<{
  job_id: string;
  state: string;
  result?: { analysis?: StoryAnalysis };
  error?: string;
  progress?: { phase?: string; message?: string; chapter_done?: number; chapter_total?: number };
}> {
  return request(API.STORY_ANALYSIS_JOB(jobId));
}

export async function fetchRagGlobalJob(
  jobId: string,
): Promise<{ job_id: string; state: string; result?: unknown; error?: string }> {
  return request(API.RAG_GLOBAL_JOB(jobId));
}

export async function fetchRedialogueJob(
  jobId: string,
): Promise<{ job_id: string; state: string; result?: unknown; error?: string }> {
  return request(`/api/v1/agent/redialogue/jobs/${jobId}`);
}
