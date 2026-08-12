import { useCallback, useEffect, useRef } from 'react';

import { streamChat, clearSession as apiClearSession } from '@/api/chat';
import { toUserErrorMessage } from '@/lib/errors';
import {
  applyChatStreamEvent,
  type ChatStreamRefs,
} from '@/lib/streamReducers';
import { useChatStore } from '@/store/chatStore';
import type { StreamEventData } from '@/types';

export interface UseSSEReturn {
  send: (text: string) => Promise<void>;
  clearSession: () => Promise<void>;
  abort: () => void;
}

export function useSSE(): UseSSEReturn {
  const refs = useRef<ChatStreamRefs>({
    lastPlan: null,
    lastStepResults: [],
    replyStarted: false,
    fullReply: '',
  });
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    useChatStore.getState().setLoading(false);
    useChatStore.getState().setStreamPhase(null);
  }, []);

  const send = useCallback(async (text: string) => {
    const store = useChatStore.getState();
    if (store.loading) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    refs.current = {
      lastPlan: null,
      lastStepResults: [],
      replyStarted: false,
      fullReply: '',
    };

    store.setInput('');
    store.setError(null);
    store.setPendingApproval(null);
    store.setLoading(true);
    store.setStreamPhase('planning');

    store.addMessage({
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    });

    try {
      await streamChat(
        text,
        store.sessionId,
        {
          onEvent: (event: StreamEventData) => {
            refs.current = applyChatStreamEvent(
              event,
              refs.current,
              useChatStore.getState(),
            );
          },
          onError: (err) => {
            useChatStore.getState().setError(toUserErrorMessage(err));
          },
        },
        controller.signal,
        store.novelScope || undefined,
      );
    } catch (e) {
      if (controller.signal.aborted) return;
      const store2 = useChatStore.getState();
      const message = toUserErrorMessage(e, '请求失败，请稍后重试');
      store2.setError(message);
      store2.addMessage({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: `出错了：${message}`,
        timestamp: Date.now(),
      });
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
      useChatStore.getState().setLoading(false);
      useChatStore.getState().setStreamPhase(null);
    }
  }, []);

  const clearSession = useCallback(async () => {
    abort();
    const id = useChatStore.getState().sessionId;
    if (id) {
      try {
        await apiClearSession(id);
      } catch {
        // Session clear failure is non-critical
      }
    }
    useChatStore.getState().clearMessages();
  }, [abort]);

  return { send, clearSession, abort };
}
