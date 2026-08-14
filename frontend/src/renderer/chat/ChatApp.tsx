/**
 * 角色聊天窗口 —— 接 impersonationStore + 会话侧栏。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { fetchCharacters } from '@/api/characters';
import { CitationChips } from '@/components/chat/CitationChips';
import { Composer } from '@/components/chat/Composer';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { SessionRail } from '@/components/chat/SessionRail';
import { Avatar, StatusMeta, Titlebar, WindowShell } from '@/components/ui';
import { useImpersonationSSE } from '@/hooks/useImpersonationSSE';
import { formatMetaLine } from '@/lib/formatUsage';
import { useImpersonationStore } from '@/store/impersonationStore';
import type { CharacterInfo } from '@/types';

function TypingIndicator({ character }: { character: string }) {
  return (
    <div className="flex justify-start animate-fade-in">
      <div className="max-w-[78%] glass-message rounded-2xl rounded-tl-sm px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">{character} 正在思考</span>
          <div className="flex gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-brand/60 animate-pulse" />
            <span className="w-1.5 h-1.5 rounded-full bg-brand/60 animate-pulse" style={{ animationDelay: '200ms' }} />
            <span className="w-1.5 h-1.5 rounded-full bg-brand/60 animate-pulse" style={{ animationDelay: '400ms' }} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Welcome({ character }: { character: string }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-full py-12 px-4 animate-fade-in">
      <div className="relative">
        <Avatar name={character} size="xl" className="shadow-elevated shadow-brand/40 animate-float" />
        <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full bg-ok border-2 border-surface" />
      </div>
      <h2 className="mt-6 text-2xl font-bold tracking-tight">
        与 <span className="bg-gradient-to-r from-brand to-accent bg-clip-text text-transparent">{character}</span> 开始对话
      </h2>
      <p className="mt-2 text-sm text-muted max-w-md text-center leading-relaxed">
        角色将按照原著设定与你交谈。可从左侧切换或新建会话。
      </p>
    </div>
  );
}

function withAttachments(text: string, files: File[]): string {
  const names = files.map((f) => f.name).filter(Boolean);
  if (!names.length) return text;
  const note = `（附件：${names.join('、')}。文件需在知识库入库后才能检索。）`;
  return text.trim() ? `${text.trim()}\n\n${note}` : note;
}

export function ChatApp() {
  const character = useImpersonationStore((s) => s.character);
  const messages = useImpersonationStore((s) => s.messages);
  const input = useImpersonationStore((s) => s.input);
  const setInput = useImpersonationStore((s) => s.setInput);
  const loading = useImpersonationStore((s) => s.loading);
  const error = useImpersonationStore((s) => s.error);
  const setError = useImpersonationStore((s) => s.setError);
  const sessionId = useImpersonationStore((s) => s.sessionId);
  const sessions = useImpersonationStore((s) => s.sessions);
  const setCharacter = useImpersonationStore((s) => s.setCharacter);
  const refreshSessions = useImpersonationStore((s) => s.refreshSessions);
  const loadSession = useImpersonationStore((s) => s.loadSession);
  const startNewSession = useImpersonationStore((s) => s.startNewSession);
  const renameSession = useImpersonationStore((s) => s.renameSession);
  const deleteSession = useImpersonationStore((s) => s.deleteSession);

  const { send, abort } = useImpersonationSSE();
  const [characterInfo, setCharacterInfo] = useState<CharacterInfo | null>(null);
  const [initializing, setInitializing] = useState(true);
  const [railOpen, setRailOpen] = useState(true);
  const [attachments, setAttachments] = useState<File[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    const init = async () => {
      let id: string | null = null;
      try {
        id = (await window.aurora?.getCharacterId()) ?? null;
      } catch {
        /* noop */
      }
      if (!id) id = new URLSearchParams(window.location.search).get('characterId');
      if (!id) {
        setError('未指定角色 ID');
        setInitializing(false);
        return;
      }
      setCharacter(id);
      try {
        const [chars] = await Promise.all([fetchCharacters({ q: id }), refreshSessions()]);
        setCharacterInfo(chars.find((c) => c.name === id) ?? null);
        const mine = useImpersonationStore.getState().sessions.filter((s) => s.character === id);
        const current = useImpersonationStore.getState().sessionId;
        const hit = mine.find((s) => s.session_id === current) ?? mine[0];
        if (hit) await loadSession(hit.session_id);
      } catch {
        /* 角色信息失败不阻塞 */
      } finally {
        setInitializing(false);
      }
    };
    void init();
  }, [loadSession, refreshSessions, setCharacter, setError]);

  useEffect(() => {
    if (!loading && sessionId && messages.length > 0) void refreshSessions();
  }, [loading, sessionId, messages.length, refreshSessions]);

  const displayName = characterInfo?.name || character || '角色';
  const sourceText = characterInfo?.source_work || characterInfo?.source || characterInfo?.series_id;
  const lastMsg = messages[messages.length - 1];
  const isStreamingLast = loading && lastMsg?.role === 'assistant' && !lastMsg.content;
  const mySessions = useMemo(
    () => sessions.filter((s) => !character || s.character === character),
    [sessions, character],
  );

  const handleSend = useCallback(() => {
    const text = withAttachments(input, attachments);
    if (!text.trim()) return;
    setAttachments([]);
    void send(text).then(() => {
      const state = useImpersonationStore.getState();
      if (state.error) return;
      const preview = state.messages.at(-1)?.content ?? text;
      window.aurora?.notifyMessageReceived?.(preview.slice(0, 80));
    });
  }, [attachments, input, send]);

  return (
    <WindowShell variant="chat">
      <div className="pointer-events-none absolute -top-32 -right-32 w-96 h-96 rounded-full bg-accent/10 blur-3xl" />

      <Titlebar
        icon={<Avatar name={displayName} size="lg" />}
        title={displayName}
        subtitle={sourceText}
        meta={<StatusMeta online={!loading} label={loading ? '正在对话' : '在线'} />}
        trailing={
          <button
            type="button"
            className="win-btn text-[11px] w-auto px-2"
            onClick={() => setRailOpen((v) => !v)}
            title="会话列表"
          >
            会话
          </button>
        }
      />

      <div className="flex min-h-0 flex-1">
        {railOpen ? (
          <SessionRail
            sessions={mySessions}
            activeId={sessionId}
            onNew={startNewSession}
            onSelect={(id) => void loadSession(id)}
            onRename={(id, title) => void renameSession(id, title)}
            onDelete={(id) => {
              if (confirm('删除这个会话？')) void deleteSession(id);
            }}
          />
        ) : null}

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto relative z-0">
            {initializing ? (
              <div className="flex flex-col items-center justify-center min-h-full">
                <div className="w-8 h-8 rounded-full border-2 border-brand/20 border-t-brand animate-spin" />
                <p className="mt-3 text-xs text-muted">正在加载...</p>
              </div>
            ) : messages.length === 0 ? (
              <Welcome character={displayName} />
            ) : (
              <div className="max-w-[820px] mx-auto py-4 px-4 space-y-4">
                {messages.map((msg, idx) => (
                  <MessageBubble
                    key={msg.id}
                    role={msg.role}
                    name={msg.role === 'assistant' ? displayName : undefined}
                    content={msg.content}
                    isStreaming={loading && idx === messages.length - 1 && msg.role === 'assistant'}
                    split={msg.role === 'assistant' && !(loading && idx === messages.length - 1)}
                    meta={formatMetaLine({ elapsedMs: msg.elapsed, usage: msg.usage })}
                    footer={
                      msg.citations?.length ? <CitationChips citations={msg.citations} /> : null
                    }
                  />
                ))}
                {isStreamingLast ? <TypingIndicator character={displayName} /> : null}
                {error ? (
                  <div className="rounded-xl border border-danger/30 bg-danger/8 px-4 py-2.5 text-sm text-danger flex items-center justify-between">
                    <span>{error}</span>
                    <button type="button" onClick={() => setError(null)} className="text-xs">
                      关闭
                    </button>
                  </div>
                ) : null}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          {!initializing ? (
            <div className="relative z-10 shrink-0 px-4 pb-4 pt-2 bg-gradient-to-t from-bg via-bg/90 to-transparent">
              <Composer
                value={input}
                onChange={setInput}
                onSend={handleSend}
                onStop={abort}
                loading={loading}
                disabled={!character}
                allowAttach
                attachments={attachments}
                onAttachmentsChange={setAttachments}
              />
            </div>
          ) : null}
        </div>
      </div>
    </WindowShell>
  );
}
