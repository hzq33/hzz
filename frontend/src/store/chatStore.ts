import { create } from 'zustand';

import type { ApprovalRequiredEvent, ChatMessage, AgentPhase } from '@/types';

function loadSessionId(): string | null {
  try {
    const id = localStorage.getItem('agent_session_id');
    if (id && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(id) && id !== '.' && id !== '..') {
      return id;
    }
    if (id) localStorage.removeItem('agent_session_id');
    return null;
  } catch {
    return null;
  }
}

function saveSessionId(id: string | null): void {
  try {
    if (id) {
      localStorage.setItem('agent_session_id', id);
    } else {
      localStorage.removeItem('agent_session_id');
    }
  } catch {
    // localStorage may be unavailable
  }
}

interface ChatState {
  /* ── Persistent State ── */
  messages: ChatMessage[];
  sessionId: string | null;

  /* ── Transient State ── */
  input: string;
  loading: boolean;
  error: string | null;
  streamPhase: AgentPhase | null;
  pendingApproval: ApprovalRequiredEvent | null;
  /** 检索范围（novel_scope）：按系列/卷限定通用助手的检索。 */
  novelScope: { series_id?: string; doc_ids?: string[] } | null;

  /* ── Actions ── */
  setInput: (v: string) => void;
  setError: (v: string | null) => void;
  setLoading: (v: boolean) => void;
  setStreamPhase: (p: AgentPhase | null) => void;
  setPendingApproval: (a: ApprovalRequiredEvent | null) => void;
  setNovelScope: (s: { series_id?: string; doc_ids?: string[] } | null) => void;
  addMessage: (msg: ChatMessage) => void;
  updateLastMessage: (updater: (msg: ChatMessage) => ChatMessage) => void;
  clearMessages: () => void;
  setSessionId: (id: string | null) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  sessionId: loadSessionId(),
  input: '',
  loading: false,
  error: null,
  streamPhase: null,
  pendingApproval: null,
  novelScope: null,

  setInput: (v) => set({ input: v }),
  setError: (v) => set({ error: v }),
  setLoading: (v) => set({ loading: v }),
  setStreamPhase: (p) => set({ streamPhase: p }),
  setPendingApproval: (a) => set({ pendingApproval: a }),
  setNovelScope: (s) => set({ novelScope: s }),

  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

  updateLastMessage: (updater) =>
    set((s) => {
      const copy = [...s.messages];
      if (copy.length > 0) {
        copy[copy.length - 1] = updater(copy[copy.length - 1]);
      }
      return { messages: copy };
    }),

  clearMessages: () => {
    saveSessionId(null);
    set({
      messages: [],
      sessionId: null,
      error: null,
      loading: false,
      streamPhase: null,
      pendingApproval: null,
    });
  },

  setSessionId: (id) => {
    saveSessionId(id);
    set({ sessionId: id });
  },
}));
