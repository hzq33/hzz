import { create } from 'zustand';

import { fetchCharacters as fetchCharactersApi } from '@/api/characters';
import {
  deleteImpersonationSession,
  fetchImpersonationHistory,
  listImpersonationSessions,
  renameImpersonationSession,
} from '@/api/impersonation';
import { API } from '@/lib/constants';
import { toUserErrorMessage } from '@/lib/errors';
import type { CharacterInfo as ApiCharacterInfo, ImpMessage, ImpersonationSessionSummary, MemoryStats } from '@/types';

export interface CharacterInfo {
  name: string;
  source: string;
  dialogue_count: number;
  series_id?: string;
  has_card?: boolean;
  status?: string;
}

function isPlayableCharacter(c: ApiCharacterInfo): boolean {
  return Boolean(c.has_card) || c.status === 'ready';
}

function toStoreCharacter(c: ApiCharacterInfo): CharacterInfo {
  return {
    name: c.name,
    source: c.source || c.series_id || '',
    dialogue_count: c.dialogue_count || 0,
    series_id: c.series_id,
    has_card: c.has_card,
    status: c.status,
  };
}

export type { ImpMessage } from '@/types';

const SAFE_SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function isSafeSessionId(id: string | null | undefined): boolean {
  const value = (id || '').trim();
  return Boolean(value) && SAFE_SESSION_ID.test(value) && value !== '.' && value !== '..';
}

function loadImpSessionId(): string | null {
  try {
    const id = localStorage.getItem('imp_session_id');
    if (isSafeSessionId(id)) return id;
    if (id) localStorage.removeItem('imp_session_id');
    return null;
  } catch {
    return null;
  }
}

function saveImpSessionId(id: string | null): void {
  try {
    if (id && isSafeSessionId(id)) localStorage.setItem('imp_session_id', id);
    else localStorage.removeItem('imp_session_id');
  } catch {
    /* noop */
  }
}

interface ImpersonationState {
  characters: CharacterInfo[];
  character: string | null;
  messages: ImpMessage[];
  sessionId: string | null;
  docId: string | null;
  input: string;
  loading: boolean;
  error: string | null;
  maxHistoryTokens: number | null;
  memoryStats: MemoryStats | null;
  sessions: ImpersonationSessionSummary[];
  sessionsLoading: boolean;

  fetchCharacters: (seriesId?: string, signal?: AbortSignal) => Promise<void>;
  setCharacter: (name: string) => void;
  setDocId: (id: string | null) => void;
  setInput: (v: string) => void;
  setError: (v: string | null) => void;
  setLoading: (v: boolean) => void;
  setSessionId: (id: string | null) => void;
  setMaxHistoryTokens: (n: number | null) => void;
  setMemoryStats: (stats: MemoryStats | null) => void;
  addMessage: (msg: ImpMessage) => void;
  updateLastMessage: (updater: (msg: ImpMessage) => ImpMessage) => void;
  removeLastAssistant: () => void;
  resetSession: () => Promise<void>;
  clearAll: () => void;
  refreshSessions: () => Promise<void>;
  startNewSession: () => void;
  loadSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, title: string) => Promise<void>;
  deleteSession: (sessionId: string) => Promise<void>;
}

export const useImpersonationStore = create<ImpersonationState>((set, get) => ({
  characters: [],
  character: null,
  messages: [],
  sessionId: loadImpSessionId(),
  docId: null,
  input: '',
  loading: false,
  error: null,
  maxHistoryTokens: null,
  memoryStats: null,
  sessions: [],
  sessionsLoading: false,

  fetchCharacters: async (seriesId?: string, signal?: AbortSignal) => {
    try {
      const chars = await fetchCharactersApi({
        series_id: seriesId,
        include_candidates: false,
        signal,
      });
      const playable = chars.filter(isPlayableCharacter).map(toStoreCharacter);
      const current = get().character;
      const stillValid = current && playable.some((c) => c.name === current);
      set({
        characters: playable,
        character: stillValid ? current : playable[0]?.name || null,
      });
    } catch {
      if (signal?.aborted) return;
      set({ characters: [], character: null });
    }
  },

  setCharacter: (name) => {
    const prev = get().character;
    if (prev === name) return;
    saveImpSessionId(null);
    set({ character: name, messages: [], sessionId: null, error: null });
  },

  setDocId: (id) => set({ docId: id }),
  setInput: (v) => set({ input: v }),
  setError: (v) => set({ error: v }),
  setLoading: (v) => set({ loading: v }),
  setMaxHistoryTokens: (n) => set({ maxHistoryTokens: n }),
  setMemoryStats: (stats) => set({ memoryStats: stats }),
  setSessionId: (id) => {
    saveImpSessionId(id);
    set({ sessionId: id });
  },

  addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),

  updateLastMessage: (updater) =>
    set((s) => {
      const copy = [...s.messages];
      if (copy.length > 0) copy[copy.length - 1] = updater(copy[copy.length - 1]);
      return { messages: copy };
    }),

  removeLastAssistant: () =>
    set((s) => {
      const copy = [...s.messages];
      for (let i = copy.length - 1; i >= 0; i -= 1) {
        if (copy[i].role === 'assistant') {
          copy.splice(i, 1);
          break;
        }
      }
      return { messages: copy };
    }),

  resetSession: async () => {
    const id = get().sessionId;
    if (id) {
      try {
        await fetch(`${API.IMP_RESET}?session_id=${id}`, { method: 'POST' });
      } catch { /* non-critical */ }
    }
    saveImpSessionId(null);
    set({ messages: [], sessionId: null, error: null, loading: false });
    void get().refreshSessions();
  },

  clearAll: () => {
    saveImpSessionId(null);
    set({
      character: null,
      messages: [],
      sessionId: null,
      error: null,
      loading: false,
    });
  },

  refreshSessions: async () => {
    set({ sessionsLoading: true });
    try {
      const items = await listImpersonationSessions();
      set({ sessions: items, sessionsLoading: false });
    } catch (err) {
      set({
        sessionsLoading: false,
        error: toUserErrorMessage(err, '无法加载存档列表'),
      });
    }
  },

  startNewSession: () => {
    saveImpSessionId(null);
    set({ messages: [], sessionId: null, error: null, input: '' });
  },

  loadSession: async (sessionId: string) => {
    set({ loading: true, error: null });
    try {
      const history = await fetchImpersonationHistory(sessionId);
      const messages: ImpMessage[] = (history.messages || [])
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({
          id: crypto.randomUUID(),
          role: m.role as 'user' | 'assistant',
          content: m.content || '',
          timestamp: Date.now(),
        }));
      saveImpSessionId(sessionId);
      set({
        sessionId,
        character: history.character,
        docId: history.doc_id || null,
        messages,
        loading: false,
        error: null,
      });
    } catch (err) {
      set({
        loading: false,
        error: toUserErrorMessage(err, '读档失败，请稍后重试'),
      });
    }
  },

  renameSession: async (sessionId: string, title: string) => {
    try {
      await renameImpersonationSession(sessionId, title);
      await get().refreshSessions();
    } catch (err) {
      set({ error: toUserErrorMessage(err, '重命名失败') });
    }
  },

  deleteSession: async (sessionId: string) => {
    try {
      await deleteImpersonationSession(sessionId);
      if (get().sessionId === sessionId) {
        saveImpSessionId(null);
        set({ sessionId: null, messages: [] });
      }
      await get().refreshSessions();
    } catch (err) {
      set({ error: toUserErrorMessage(err, '删除存档失败') });
    }
  },
}));
