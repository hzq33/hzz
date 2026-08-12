/** Pure stream-event reducers for chat / impersonation hooks. */

import type {
  ApprovalRequiredEvent,
  ChatMessage,
  ImpMessage,
  MemoryStats,
  PlanEvent,
  StepResultEvent,
  StoryEvidence,
  StreamEventData,
} from '@/types';

export type ChatStreamRefs = {
  lastPlan: PlanEvent | null;
  lastStepResults: StepResultEvent[];
  replyStarted: boolean;
  fullReply: string;
};

/** 通用对话 store 的最小接口（与 chatStore 形状兼容） */
export interface ChatStoreLike {
  messages: ChatMessage[];
  setStreamPhase: (phase: string) => void;
  addMessage: (msg: ChatMessage) => void;
  updateLastMessage: (updater: (m: ChatMessage) => ChatMessage) => void;
  setSessionId: (id: string | null) => void;
  setError: (msg: string | null) => void;
  setPendingApproval?: (event: ApprovalRequiredEvent | null) => void;
}

export function applyChatStreamEvent(
  event: StreamEventData,
  refs: ChatStreamRefs,
  store: ChatStoreLike,
): ChatStreamRefs {
  const next: ChatStreamRefs = {
    lastPlan: refs.lastPlan,
    lastStepResults: [...refs.lastStepResults],
    replyStarted: refs.replyStarted,
    fullReply: refs.fullReply,
  };

  switch (event.type) {
    case 'phase':
      store.setStreamPhase(event.phase);
      break;

    case 'plan':
      next.lastPlan = event;
      if (!next.replyStarted) {
        store.addMessage({
          id: crypto.randomUUID(),
          role: 'assistant',
          content: '',
          plan: event,
          stepResults: [],
          timestamp: Date.now(),
        });
      } else {
        store.updateLastMessage((m) => ({ ...m, plan: event }));
      }
      break;

    case 'step_result':
      next.lastStepResults = [...next.lastStepResults, event];
      if (!next.replyStarted && next.lastPlan) {
        const steps = next.lastStepResults;
        store.updateLastMessage((m) => ({ ...m, stepResults: [...steps] }));
      }
      break;

    case 'reply_chunk':
      next.replyStarted = true;
      next.fullReply += event.token;
      if (next.lastPlan) {
        const content = next.fullReply;
        store.updateLastMessage((m) => ({ ...m, content }));
      } else {
        const msgs = store.messages;
        if (msgs.length === 0 || msgs[msgs.length - 1].role !== 'assistant') {
          store.addMessage({
            id: crypto.randomUUID(),
            role: 'assistant',
            content: event.token,
            timestamp: Date.now(),
          });
        } else {
          store.updateLastMessage((m) => ({ ...m, content: m.content + event.token }));
        }
      }
      break;

    case 'done':
      store.updateLastMessage((m) => ({
        ...m,
        content: next.fullReply || m.content,
        elapsed: event.elapsed_ms,
        usage: event.usage,
      }));
      if (event.session_id) {
        store.setSessionId(event.session_id);
      }
      break;

    case 'error':
      store.setError(event.message);
      break;

    case 'approval_required':
      if (typeof store.setPendingApproval === 'function') {
        store.setPendingApproval(event);
      }
      break;
  }

  return next;
}

export type ImpStreamEvent =
  | { type: 'reply_chunk'; token: string; session_id?: string }
  | {
      type: 'citations';
      items?: StoryEvidence[];
      fact?: StoryEvidence[];
      style?: StoryEvidence[];
    }
  | {
      type: 'done';
      session_id?: string;
      max_history_tokens?: number;
      memory_stats?: MemoryStats | null;
      elapsed_ms?: number;
      usage?: {
        prompt_tokens?: number;
        completion_tokens?: number;
        total_tokens?: number;
        cost_usd?: number;
        model?: string;
      };
    }
  | { type: 'error'; message: string };

/** Merge SSE fact/style/items into a flat list with role tags. */
export function normalizeImpCitations(event: {
  items?: StoryEvidence[];
  fact?: StoryEvidence[];
  style?: StoryEvidence[];
}): StoryEvidence[] {
  if (event.fact || event.style) {
    const fact = (event.fact || []).map((c) => ({ ...c, role: c.role || ('fact' as const) }));
    const style = (event.style || []).map((c) => ({ ...c, role: c.role || ('style' as const) }));
    return [...fact, ...style];
  }
  return (event.items || []).map((c) => {
    if (c.role) return c;
    const inferred: 'fact' | 'style' =
      c.channel === 'dialogue' ? 'style' : 'fact';
    return { ...c, role: inferred };
  });
}

export function splitEvidenceByRole(items: StoryEvidence[]): {
  fact: StoryEvidence[];
  style: StoryEvidence[];
} {
  const fact: StoryEvidence[] = [];
  const style: StoryEvidence[] = [];
  for (const c of items || []) {
    const role = c.role || (c.channel === 'dialogue' ? 'style' : 'fact');
    if (role === 'style') style.push(c);
    else fact.push(c);
  }
  fact.sort((a, b) => (evidenceRelevance(b) ?? -1) - (evidenceRelevance(a) ?? -1));
  return { fact, style };
}

/** Vector similarity for UI %; ignores tiny RRF-like leftovers. */
export function evidenceRelevance(c?: StoryEvidence | null): number | undefined {
  if (!c) return undefined;
  if (c.similarity != null && Number.isFinite(c.similarity)) {
    return c.similarity;
  }
  if (c.score != null && Number.isFinite(c.score) && c.score >= 0.15) {
    return c.score;
  }
  return undefined;
}

/** 扮演 store 的最小接口（与 impersonationStore 形状兼容） */
export interface ImpStoreLike {
  messages: ImpMessage[];
  addMessage: (msg: ImpMessage) => void;
  updateLastMessage: (updater: (m: ImpMessage) => ImpMessage) => void;
  setSessionId: (id: string | null) => void;
  setMaxHistoryTokens: (tokens: number) => void;
  setMemoryStats: (stats: MemoryStats | null) => void;
  setError: (msg: string | null) => void;
}

export function applyImpersonationStreamEvent(
  event: ImpStreamEvent,
  fullReply: string,
  citations: StoryEvidence[],
  store: ImpStoreLike,
): { fullReply: string; citations: StoryEvidence[] } {
  let nextReply = fullReply;
  let nextCitations = citations;

  if (event.type === 'citations') {
    nextCitations = normalizeImpCitations(event);
    return { fullReply: nextReply, citations: nextCitations };
  }

  if (event.type === 'reply_chunk') {
    nextReply += event.token;
    const lastMsg = store.messages[store.messages.length - 1];
    if (lastMsg && lastMsg.role === 'user') {
      store.addMessage({
        id: crypto.randomUUID(),
        role: 'assistant',
        content: event.token,
        citations: nextCitations,
        timestamp: Date.now(),
      });
      if (event.session_id) {
        store.setSessionId(event.session_id);
      }
    } else {
      store.updateLastMessage((m) => ({
        ...m,
        content: m.content + event.token,
        citations: nextCitations.length ? nextCitations : m.citations,
      }));
    }
    return { fullReply: nextReply, citations: nextCitations };
  }

  if (event.type === 'done') {
    store.updateLastMessage((m) => ({
      ...m,
      content: nextReply || m.content,
      citations: nextCitations.length ? nextCitations : m.citations,
      elapsed: event.elapsed_ms ?? m.elapsed,
      usage: event.usage ?? m.usage,
    }));
    if (event.session_id) {
      store.setSessionId(event.session_id);
    }
    if (event.max_history_tokens != null) {
      store.setMaxHistoryTokens(event.max_history_tokens);
    }
    if (event.memory_stats != null) {
      store.setMemoryStats(event.memory_stats);
    }
    return { fullReply: nextReply, citations: nextCitations };
  }

  if (event.type === 'error') {
    store.setError(event.message);
  }

  return { fullReply: nextReply, citations: nextCitations };
}
