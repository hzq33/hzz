import { useCallback, useEffect, useRef } from 'react';

import { API } from '@/lib/constants';
import { toUserErrorMessage } from '@/lib/errors';
import { parseJSONSafe, readSSEStream } from '@/lib/sse';
import {
  applyImpersonationStreamEvent,
  type ImpStreamEvent,
} from '@/lib/streamReducers';
import { useImpersonationStore } from '@/store/impersonationStore';
import type { StoryEvidence } from '@/types';

async function extractErrorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`;
  } catch {
    return `HTTP ${res.status}`;
  }
}

async function runImpStream(
  url: string,
  body: object,
  controller: AbortController,
) {
  let fullReply = '';
  let citations: StoryEvidence[] = [];

  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: controller.signal,
  });

  if (!res.ok) {
    const detail = await extractErrorDetail(res);
    throw new Error(detail);
  }
  if (!res.body) throw new Error('服务未返回内容');

  await readSSEStream(
    res.body,
    (jsonStr) => {
      const event = parseJSONSafe<ImpStreamEvent>(jsonStr);
      if (!event) return;
      const next = applyImpersonationStreamEvent(
        event,
        fullReply,
        citations,
        useImpersonationStore.getState(),
      );
      fullReply = next.fullReply;
      citations = next.citations;
    },
    controller.signal,
  );
}

export function useImpersonationSSE() {
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const send = useCallback(async (text: string) => {
    const store = useImpersonationStore.getState();
    if (store.loading || !store.character) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    store.setInput('');
    store.setError(null);
    store.setLoading(true);

    store.addMessage({
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    });

    try {
      const safeSessionId =
        store.sessionId &&
        /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(store.sessionId)
          ? store.sessionId
          : null;
      if (store.sessionId && !safeSessionId) {
        store.setSessionId(null);
      }
      await runImpStream(
        API.IMP_CHAT_STREAM,
        {
          character: store.character,
          message: text,
          session_id: safeSessionId,
          doc_id: store.docId || undefined,
        },
        controller,
      );
    } catch (e) {
      if (controller.signal.aborted) return;
      const message = toUserErrorMessage(e, '请求失败，请稍后重试');
      useImpersonationStore.getState().setError(message);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      const state = useImpersonationStore.getState();
      if (state.sessionId && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(state.sessionId)) {
        try {
          localStorage.setItem('imp_session_id', state.sessionId);
        } catch {
          /* noop */
        }
      }
      state.setLoading(false);
    }
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    useImpersonationStore.getState().setLoading(false);
  }, []);

  const regenerate = useCallback(async () => {
    const store = useImpersonationStore.getState();
    if (store.loading || !store.character || !store.sessionId) return;

    const lastUser = [...store.messages].reverse().find((m) => m.role === 'user');
    if (!lastUser) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    store.setError(null);
    store.setLoading(true);
    store.removeLastAssistant();

    try {
      await runImpStream(
        API.IMP_REGENERATE,
        {
          character: store.character,
          session_id: store.sessionId,
          doc_id: store.docId || undefined,
        },
        controller,
      );
    } catch (e) {
      if (controller.signal.aborted) return;
      const message = toUserErrorMessage(e, '重新生成失败，请稍后重试');
      useImpersonationStore.getState().setError(message);
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      useImpersonationStore.getState().setLoading(false);
    }
  }, []);

  return { send, regenerate, abort };
}
