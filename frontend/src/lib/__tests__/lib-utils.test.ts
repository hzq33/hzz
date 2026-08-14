import { describe, expect, it, vi } from 'vitest';

import { normalizeStoryAnalysis } from '@/api/world';
import { API, PHASE_LABELS, QUICK_ACTIONS } from '@/lib/constants';
import { pollJob, UPLOAD_STAGE_LABELS } from '@/lib/pollJob';
import { parseJSONSafe, readSSEStream } from '@/lib/sse';
import {
  evidenceRelevance,
  normalizeImpCitations,
  splitEvidenceByRole,
  type ChatStreamRefs,
  type ChatStoreLike,
} from '@/lib/streamReducers';
import type { ChatMessage } from '@/types';

describe('parseJSONSafe', () => {
  it('parses valid JSON', () => {
    expect(parseJSONSafe<{ a: number }>('{"a":1}')).toEqual({ a: 1 });
  });

  it('returns null for invalid JSON', () => {
    expect(parseJSONSafe('not-json')).toBeNull();
  });
});

describe('readSSEStream', () => {
  it('invokes onData for each data line', async () => {
    const payload = new TextEncoder().encode('data: {"type":"reply_chunk"}\n\n');
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(payload);
        controller.close();
      },
    });
    const onData = vi.fn();
    await readSSEStream(stream, onData);
    expect(onData).toHaveBeenCalledWith('{"type":"reply_chunk"}');
  });
});

describe('pollJob', () => {
  it('returns when the job reaches a terminal state', async () => {
    const fetchJob = vi
      .fn()
      .mockResolvedValueOnce({ state: 'running', progress: { pct: 10 } })
      .mockResolvedValueOnce({ state: 'done', result: { ok: true } });

    const job = await pollJob({
      fetchJob,
      intervalMs: 1,
      onProgress: vi.fn(),
    });

    expect(job.state).toBe('done');
    expect(fetchJob).toHaveBeenCalledTimes(2);
  });

  it('throws when aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      pollJob({
        fetchJob: () => Promise.resolve({ state: 'running' }),
        signal: controller.signal,
      }),
    ).rejects.toThrow(/aborted/i);
  });

  it('aborts during sleep without waiting full interval', async () => {
    const controller = new AbortController();
    const fetchJob = vi.fn().mockResolvedValue({ state: 'running' });
    const pending = pollJob({
      fetchJob,
      intervalMs: 30_000,
      signal: controller.signal,
    });
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchJob.mock.calls.length).toBeLessThanOrEqual(2);
  });

  it('scales upload poll budget for large files', async () => {
    const { uploadPollOptions } = await import('@/lib/pollJob');
    const small = uploadPollOptions(500_000);
    expect(small.maxTries).toBe(15 * 60);
    const big = uploadPollOptions(20 * 1024 * 1024);
    expect(big.maxTries).toBeGreaterThan(small.maxTries);
    expect(big.maxTries).toBeLessThanOrEqual(45 * 60);
  });
});

describe('constants', () => {
  it('exposes chat stream and health endpoints', () => {
    expect(API.CHAT_STREAM).toContain('/chat/stream');
    expect(API.HEALTH).toContain('/health');
    expect(API.HISTORY('abc')).toContain('session_id=abc');
    expect(QUICK_ACTIONS.length).toBeGreaterThan(0);
    expect(QUICK_ACTIONS.some((a) => 'prompt' in a && a.prompt.includes('novel_search'))).toBe(
      true,
    );
    expect(PHASE_LABELS.planning).toBeTruthy();
    expect(UPLOAD_STAGE_LABELS.done).toBe('完成');
  });
});

describe('formatUsage', () => {
  it('formats elapsed and token usage', async () => {
    const { formatMetaLine, formatTokenUsage, formatCostUsd } = await import(
      '@/lib/formatUsage'
    );
    expect(
      formatTokenUsage({ total_tokens: 120, prompt_tokens: 80, completion_tokens: 40 }),
    ).toContain('120 tok');
    expect(formatCostUsd(0.0012)).toContain('$');
    expect(
      formatMetaLine({
        elapsedMs: 1500,
        usage: { total_tokens: 10, cost_usd: 0.0002 },
      }),
    ).toContain('1.5s');
  });
});

describe('streamReducers', () => {
  it('applies reply_chunk and done for chat', async () => {
    const { applyChatStreamEvent } = await import('@/lib/streamReducers');
    const messages: ChatMessage[] = [];
    const store: ChatStoreLike = {
      messages,
      setStreamPhase: vi.fn(),
      setError: vi.fn(),
      setSessionId: vi.fn(),
      addMessage: (m: ChatMessage) => {
        messages.push(m);
      },
      updateLastMessage: (fn) => {
        const last = messages[messages.length - 1];
        if (last) Object.assign(last, fn(last));
      },
    };
    let refs: ChatStreamRefs = {
      lastPlan: null,
      lastStepResults: [],
      replyStarted: false,
      fullReply: '',
    };
    refs = applyChatStreamEvent(
      { type: 'reply_chunk', token: 'Hi' },
      refs,
      store,
    );
    expect(refs.fullReply).toBe('Hi');
    refs = applyChatStreamEvent(
      {
        type: 'done',
        elapsed_ms: 12,
        usage: { total_tokens: 3, cost_usd: 0.0001 },
      },
      refs,
      store,
    );
    expect(messages.at(-1)?.content).toBe('Hi');
  });

  it('stores approval_required on chat store', async () => {
    const { applyChatStreamEvent } = await import('@/lib/streamReducers');
    const setPendingApproval = vi.fn();
    const store: ChatStoreLike = {
      messages: [] as ChatMessage[],
      setStreamPhase: vi.fn(),
      setError: vi.fn(),
      setSessionId: vi.fn(),
      setPendingApproval,
      addMessage: vi.fn(),
      updateLastMessage: vi.fn(),
    };
    const refs = {
      lastPlan: null,
      lastStepResults: [],
      replyStarted: false,
      fullReply: '',
    };
    applyChatStreamEvent(
      {
        type: 'approval_required',
        approval_id: 'appr_x',
        tool_name: 'execute_code',
        tool_args: { code: 'print(1)' },
      },
      refs,
      store,
    );
    expect(setPendingApproval).toHaveBeenCalledWith(
      expect.objectContaining({ approval_id: 'appr_x', tool_name: 'execute_code' }),
    );
  });
});

describe('normalizeStoryAnalysis', () => {
  it('preserves explicit exists=false', () => {
    expect(
      normalizeStoryAnalysis({ series_id: 's1', exists: false, events: [] }),
    ).toEqual({ series_id: 's1', exists: false, events: [] });
  });

  it('marks job payloads missing exists as ready when body present', () => {
    const out = normalizeStoryAnalysis({
      series_id: 's1',
      events: [{ event_id: 'e1', summary: '开场' }],
      updated_at: '2026-07-28T00:00:00Z',
    });
    expect(out?.exists).toBe(true);
  });
});

describe('impersonation citation split', () => {
  it('normalizeImpCitations prefers fact/style and tags roles', () => {
    const items = normalizeImpCitations({
      fact: [{ block_id: 'n1', channel: 'narrative', score: 0.8 }],
      style: [{ block_id: 'd1', channel: 'dialogue', score: 0.6 }],
    });
    expect(items.map((c) => c.block_id)).toEqual(['n1', 'd1']);
    expect(items[0].role).toBe('fact');
    expect(items[1].role).toBe('style');
  });

  it('splitEvidenceByRole exposes fact for badges', () => {
    const { fact, style } = splitEvidenceByRole([
      { block_id: 'd1', channel: 'dialogue', role: 'style', score: 0.9 },
      { block_id: 'n1', channel: 'narrative', role: 'fact', score: 0.5 },
      { block_id: 'n2', channel: 'narrative', role: 'fact', score: 0.8 },
    ]);
    expect(fact.map((c) => c.block_id)).toEqual(['n2', 'n1']);
    expect(style.map((c) => c.block_id)).toEqual(['d1']);
  });

  it('evidenceRelevance prefers similarity and ignores RRF-scale scores', () => {
    expect(evidenceRelevance({ score: 0.03, similarity: 0.74 })).toBe(0.74);
    expect(evidenceRelevance({ score: 0.03 })).toBeUndefined();
    expect(evidenceRelevance({ score: 0.8 })).toBe(0.8);
  });
});
