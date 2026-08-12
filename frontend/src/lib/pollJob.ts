export interface JobProgress {
  stage?: string;
  message?: string;
  pct?: number;
}

export interface PollJobOptions<T> {
  fetchJob: () => Promise<{ state: string; error?: string | null; progress?: JobProgress } & T>;
  intervalMs?: number;
  maxTries?: number;
  isTerminal?: (state: string) => boolean;
  onProgress?: (progress: JobProgress | undefined, state: string) => void;
  signal?: AbortSignal;
  /** Shown when maxTries exhausted (default: Job polling timed out). */
  timeoutMessage?: string;
}

const DEFAULT_TERMINAL = new Set(['done', 'failed']);

/** Abortable delay — rejects immediately when signal aborts (no empty wait). */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Polling aborted', 'AbortError'));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('Polling aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Upload/embed can take many minutes on large novels — default poll was ~96s. */
export function uploadPollOptions(fileSizeBytes = 0): {
  intervalMs: number;
  maxTries: number;
  timeoutMessage: string;
} {
  const mb = Math.max(0, fileSizeBytes) / (1024 * 1024);
  // Base 15 min; +3 min per MB after 2MB; cap 45 min.
  const minutes = Math.min(45, Math.max(15, Math.ceil(15 + Math.max(0, mb - 2) * 3)));
  return {
    intervalMs: 1000,
    maxTries: minutes * 60,
    timeoutMessage:
      '导入超时：大文件嵌入可能仍在后台进行，请稍后刷新书目查看；若未出现请重试导入。',
  };
}

/**
 * Character card builds call LLM per name; with global concurrency≈2,
 * N names can take many minutes. Default poll (~96s) is far too short.
 */
export function characterBuildPollOptions(jobCount = 1): {
  intervalMs: number;
  maxTries: number;
  timeoutMessage: string;
} {
  const n = Math.max(1, jobCount);
  // ~3 min per card base, +1.5 min per extra queued card; floor 12 min, cap 45 min.
  const minutes = Math.min(45, Math.max(12, Math.ceil(3 + (n - 1) * 1.5)));
  return {
    intervalMs: 1500,
    maxTries: Math.ceil((minutes * 60) / 1.5),
    timeoutMessage:
      '角色卡生成超时：任务可能仍在后台排队（并发有限），请稍后刷新角色列表查看；未完成可重新勾选生成。',
  };
}

export async function pollJob<T extends object>(
  opts: PollJobOptions<T>,
): Promise<{ state: string; error?: string | null; progress?: JobProgress } & T> {
  const {
    fetchJob,
    intervalMs = 800,
    maxTries = 120,
    isTerminal = (s) => DEFAULT_TERMINAL.has(s),
    onProgress,
    signal,
    timeoutMessage = 'Job polling timed out',
  } = opts;

  let tries = 0;
  while (tries < maxTries) {
    if (signal?.aborted) {
      throw new DOMException('Polling aborted', 'AbortError');
    }
    const job = await fetchJob();
    onProgress?.(job.progress, job.state);
    if (isTerminal(job.state)) {
      return job;
    }
    tries += 1;
    await sleep(intervalMs, signal);
  }
  throw new Error(timeoutMessage);
}

export const UPLOAD_STAGE_LABELS: Record<string, string> = {
  received: '已接收',
  preprocess: '清洗中',
  chapter: '切章中',
  chunk: '切块中',
  embed: '嵌入中',
  roster: '建卡准备',
  done: '完成',
  failed: '失败',
};
