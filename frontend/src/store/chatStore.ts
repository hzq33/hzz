import { create } from 'zustand';

import type { ApprovalRequiredEvent, ChatMessage, AgentPhase } from '@/types';

const ARCHIVES_KEY = 'agent_archives';

export interface AgentArchive {
  id: string;
  title: string;
  messages: ChatMessage[];
  sessionId: string | null;
  updatedAt: number;
}

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
    if (id) localStorage.setItem('agent_session_id', id);
    else localStorage.removeItem('agent_session_id');
  } catch {
    /* localStorage may be unavailable */
  }
}

function loadArchives(): AgentArchive[] {
  try {
    const raw = localStorage.getItem(ARCHIVES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as AgentArchive[];
    return Array.isArray(parsed) ? parsed.slice(0, 30) : [];
  } catch {
    return [];
  }
}

function saveArchives(items: AgentArchive[]): void {
  try {
    localStorage.setItem(ARCHIVES_KEY, JSON.stringify(items.slice(0, 30)));
  } catch {
    /* noop */
  }
}

function snapshot(
  messages: ChatMessage[],
  sessionId: string | null,
  activeArchiveId: string | null,
): AgentArchive | null {
  if (!messages.length) return null;
  const firstUser = messages.find((m) => m.role === 'user');
  return {
    id: sessionId || activeArchiveId || crypto.randomUUID(),
    title: (firstUser?.content || '新对话').slice(0, 28),
    messages,
    sessionId,
    updatedAt: Date.now(),
  };
}

interface ChatState {
  messages: ChatMessage[];
  sessionId: string | null;
  activeArchiveId: string | null;
  archives: AgentArchive[];
  input: string;
  loading: boolean;
  error: string | null;
  streamPhase: AgentPhase | null;
  pendingApproval: ApprovalRequiredEvent | null;
  novelScope: { series_id?: string; doc_ids?: string[] } | null;

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
  persistCurrent: () => void;
  startNewChat: () => void;
  loadArchive: (id: string) => void;
  deleteArchive: (id: string) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  sessionId: loadSessionId(),
  activeArchiveId: null,
  archives: loadArchives(),
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
      if (copy.length > 0) copy[copy.length - 1] = updater(copy[copy.length - 1]);
      return { messages: copy };
    }),

  clearMessages: () => {
    saveSessionId(null);
    set({
      messages: [],
      sessionId: null,
      activeArchiveId: null,
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

  persistCurrent: () => {
    const { messages, sessionId, activeArchiveId, archives } = get();
    const snap = snapshot(messages, sessionId, activeArchiveId);
    if (!snap) return;
    const next = [snap, ...archives.filter((a) => a.id !== snap.id)].slice(0, 30);
    saveArchives(next);
    set({ archives: next });
  },

  startNewChat: () => {
    get().persistCurrent();
    get().clearMessages();
  },

  loadArchive: (id) => {
    const { archives } = get();
    const item = archives.find((a) => a.id === id);
    if (!item) return;
    get().persistCurrent();
    saveSessionId(item.sessionId);
    set({
      messages: item.messages,
      sessionId: item.sessionId,
      activeArchiveId: id,
      error: null,
      loading: false,
      streamPhase: null,
      pendingApproval: null,
    });
  },

  deleteArchive: (id) => {
    const next = get().archives.filter((a) => a.id !== id);
    saveArchives(next);
    set({ archives: next });
  },
}));
