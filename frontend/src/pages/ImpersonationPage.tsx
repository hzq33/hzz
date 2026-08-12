/**
 * 角色扮演页（Aurora 重新设计）：
 * 角色选择、卷锁定、流式扮演、引用证据抽屉、会话存档管理、重生成/重置。
 */
import { useEffect, useMemo, useRef, useState } from 'react';

import { useSearchParams } from 'react-router-dom';

import { fetchNovels } from '@/api/novels';
import { Badge, Empty, Modal, Spinner, IconButton } from '@/components/ui/aura';
import { useImpersonationSSE } from '@/hooks/useImpersonationSSE';
import { formatMetaLine } from '@/lib/formatUsage';
import { splitEvidenceByRole, evidenceRelevance } from '@/lib/streamReducers';
import { useImpersonationStore } from '@/store/impersonationStore';
import type { NovelVolumeInfo, StoryEvidence } from '@/types';
import ContextMonitor from '@/components/chat/ContextMonitor';

const LOW_SCORE_THRESHOLD = 0.35;

/* ═══════════ 引用证据面板 ═══════════ */

function EvidencePanel({
  title,
  evidence,
  onClose,
}: {
  title: string;
  evidence: StoryEvidence[];
  onClose: () => void;
}) {
  const { fact, style } = splitEvidenceByRole(evidence);
  return (
    <Modal open onClose={onClose} title={title} width="max-w-2xl">
      <div className="space-y-3 max-h-[65vh] overflow-y-auto pr-1">
        {fact.length > 0 ? (
          <>
            <div className="text-xs font-semibold text-brand">事实出处 · {fact.length}</div>
            {fact.map((c, i) => (
              <div key={`fact-${c.block_id || i}`} className="rounded-xl border border-line bg-surface-2 p-3 text-xs space-y-1.5">
                <div className="flex items-center gap-2 text-muted">
                  <Badge tone="brand">{c.channel || 'ref'}</Badge>
                  <span className="truncate">{c.chapter_title || c.doc_id}</span>
                  {evidenceRelevance(c) != null && (
                    <span className="ml-auto tabular-nums">{Math.round(evidenceRelevance(c)! * 100)}%</span>
                  )}
                </div>
                {c.snippet ? <p className="text-ink leading-relaxed">{c.snippet}</p> : null}
              </div>
            ))}
          </>
        ) : null}
        {style.length > 0 ? (
          <>
            <div className="text-xs font-semibold text-accent">口吻参考 · {style.length}</div>
            {style.map((c, i) => (
              <div key={`style-${c.block_id || i}`} className="rounded-xl border border-line bg-surface-2 p-3 text-xs space-y-1">
                <Badge tone="accent">{c.channel || 'style'}</Badge>
                {c.snippet ? <p className="text-ink leading-relaxed">{c.snippet}</p> : null}
              </div>
            ))}
          </>
        ) : null}
      </div>
    </Modal>
  );
}

/* ═══════════ AI 气泡 ═══════════ */

function BotBubble({
  character,
  content,
  citations,
  meta,
  onOpenEvidence,
}: {
  character: string;
  content: string;
  citations?: StoryEvidence[];
  meta?: string | null;
  onOpenEvidence: (title: string, evidence: StoryEvidence[]) => void;
}) {
  const items = citations || [];
  const { fact, style } = splitEvidenceByRole(items);
  const factScores = fact.map((c) => evidenceRelevance(c)).filter((s): s is number => s != null);
  const hasLowConfidence =
    fact.length === 0 ||
    (factScores.length > 0 && factScores.every((s) => s < LOW_SCORE_THRESHOLD));

  return (
    <div className="max-w-[78%] bg-surface border border-line rounded-2xl rounded-tl-sm px-4 py-3 shadow-soft animate-slide-up">
      <div className="flex items-center gap-1.5 mb-2">
        <div className="w-5 h-5 rounded-md bg-gradient-to-br from-accent to-brand flex items-center justify-center">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5"><circle cx="12" cy="12" r="10" /></svg>
        </div>
        <span className="text-xs font-semibold text-accent">{character}</span>
      </div>

      <p className="whitespace-pre-wrap text-sm leading-relaxed text-ink">{content}</p>

      <div className="mt-2.5 pt-2.5 border-t border-line space-y-1.5">
        {items.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => onOpenEvidence(content.slice(0, 60), items)}
              className="text-[11px] text-brand hover:text-brand-strong font-medium"
            >
              事实出处 ({fact.length})
            </button>
            {style.length > 0 && (
              <button
                type="button"
                onClick={() => onOpenEvidence(content.slice(0, 60), items)}
                className="text-[11px] text-muted hover:text-ink"
              >
                口吻参考 ({style.length})
              </button>
            )}
            <div className="flex flex-wrap gap-1 ml-auto">
              {fact.slice(0, 3).map((c, i) => (
                <Badge key={`${c.block_id || i}`}>
                  {c.channel || 'ref'}
                  {evidenceRelevance(c) != null ? ` · ${Math.round(evidenceRelevance(c)! * 100)}%` : ' · 已命中'}
                </Badge>
              ))}
            </div>
          </div>
        ) : (
          <span className="text-[10px] text-faint">无检索命中</span>
        )}
        {fact.length === 0 && items.length > 0 && (
          <p className="text-[10px] text-warn">无事实锚点，可能含模型推理</p>
        )}
        {hasLowConfidence && fact.length > 0 && (
          <p className="text-[10px] text-warn">事实相关分偏低，可能含模型推理</p>
        )}
        {items.length === 0 && (
          <p className="text-[10px] text-warn">可能为模型推理，无原文锚点</p>
        )}
        {meta ? <p className="text-[10px] text-faint">{meta}</p> : null}
      </div>
    </div>
  );
}

/* ═══════════ 会话存档抽屉 ═══════════ */

function SessionDrawer({
  open,
  onClose,
  sessions,
  currentSessionId,
  loading,
  onNewSession,
  onSelect,
  onRename,
  onDelete,
  onRefresh,
}: {
  open: boolean;
  onClose: () => void;
  sessions: Array<{ session_id: string; character: string; title: string; message_count: number; updated_at?: string | null }>;
  currentSessionId: string | null;
  loading: boolean;
  onNewSession: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onRefresh: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  return (
    <Modal open={open} onClose={onClose} title="扮演存档" width="max-w-xl">
      <div className="flex items-center justify-between mb-3">
        <div className="flex gap-2">
          <button type="button" className="btn-primary btn-sm" onClick={onNewSession}>
            + 新会话
          </button>
          <button type="button" className="btn-ghost btn-sm" onClick={onRefresh}>
            刷新
          </button>
        </div>
        {loading && <Spinner size={14} className="text-brand" />}
      </div>
      <div className="space-y-2 max-h-[55vh] overflow-y-auto">
        {sessions.length === 0 && !loading ? (
          <Empty icon="💾" title="暂无存档" desc="开始对话后会自动创建会话。" />
        ) : (
          sessions.map((s) => (
            <div
              key={s.session_id}
              className={`group flex items-center gap-3 rounded-xl border p-3 transition-all duration-200 cursor-pointer ${
                s.session_id === currentSessionId
                  ? 'border-brand/50 bg-brand-tint'
                  : 'border-line bg-surface-2 hover:border-brand/30 hover:shadow-card-hover'
              }`}
              onClick={() => onSelect(s.session_id)}
            >
              <div className="flex-1 min-w-0">
                {editing === s.session_id ? (
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => {
                      if (draft.trim()) onRename(s.session_id, draft.trim());
                      setEditing(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        if (draft.trim()) onRename(s.session_id, draft.trim());
                        setEditing(null);
                      }
                    }}
                    className="input input-sm"
                  />
                ) : (
                  <>
                    <div className="text-sm font-medium text-ink truncate">
                      {s.character} · {s.title || '未命名会话'}
                    </div>
                    <div className="text-[11px] text-muted mt-0.5">
                      {s.message_count} 条消息
                      {s.updated_at ? ` · ${new Date(s.updated_at).toLocaleString('zh-CN')}` : ''}
                    </div>
                  </>
                )}
              </div>
              {editing !== s.session_id && (
                <div className="opacity-0 group-hover:opacity-100 flex gap-1 transition-opacity">
                  <IconButton
                    label="重命名"
                    onClick={() => {
                      setEditing(s.session_id);
                      setDraft(s.title);
                    }}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></svg>
                  </IconButton>
                  <IconButton label="删除" onClick={() => onDelete(s.session_id)}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                  </IconButton>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </Modal>
  );
}

/* ═══════════ 页面 ═══════════ */

export default function ImpersonationPage() {
  const {
    characters, character, setCharacter,
    messages, input, setInput,
    loading, error, setError,
    docId, setDocId, maxHistoryTokens, sessionId,
    memoryStats,
    sessions, sessionsLoading,
    fetchCharacters, resetSession,
    refreshSessions, startNewSession, loadSession, renameSession, deleteSession,
  } = useImpersonationStore();
  const { send, regenerate, abort } = useImpersonationSSE();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const [charsLoaded, setCharsLoaded] = useState(false);
  const [volumes, setVolumes] = useState<NovelVolumeInfo[]>([]);
  const [evidenceItem, setEvidenceItem] = useState<{ title: string; evidence: StoryEvidence[] } | null>(null);
  const [sessionOpen, setSessionOpen] = useState(false);
  const [searchParams] = useSearchParams();
  const deepLinkCharacter = searchParams.get('character');
  const deepLinkSeries = searchParams.get('series_id');

  useEffect(() => {
    const ac = new AbortController();
    const { signal } = ac;
    void fetchCharacters(deepLinkSeries || undefined, signal).finally(() => {
      if (!signal.aborted) setCharsLoaded(true);
    });
    void fetchNovels(undefined, signal).then(setVolumes).catch(() => {});
    void refreshSessions();
    return () => ac.abort();
  }, [fetchCharacters, deepLinkSeries, refreshSessions]);

  // 深链：从知识库携带角色/系列跳转
  useEffect(() => {
    if (!charsLoaded || characters.length === 0) return;
    const want = deepLinkCharacter;
    if (!want) return;
    const hit = characters.find((c) => c.name === want);
    if (hit && character !== hit.name) setCharacter(hit.name);
    if (hit) {
      const series = hit.series_id || hit.source;
      if (series && !(deepLinkSeries && volumes.some((v) => v.series_id === deepLinkSeries))) {
        const curVol = volumes.find((v) => v.doc_id === docId);
        if (!curVol || curVol.series_id !== series) {
          const firstVol = volumes.find((v) => v.series_id === series);
          setDocId(firstVol ? firstVol.doc_id : null);
        }
      }
    }
  }, [charsLoaded, characters, character, setCharacter, volumes, setDocId, deepLinkCharacter, deepLinkSeries, docId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!loading && sessionId && messages.length > 0) void refreshSessions();
  }, [loading, sessionId, messages.length, refreshSessions]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || loading || !character) return;
    void send(text);
  };

  const activeCharacter = character || (charsLoaded && characters.length > 0 ? characters[0].name : null);
  const charMeta = useMemo(
    () => characters.find((c) => c.name === activeCharacter),
    [characters, activeCharacter],
  );
  const seriesVolumes = volumes.filter(
    (v) => !charMeta?.series_id || v.series_id === charMeta.series_id || v.series_id === charMeta.source,
  );

  const syncDocIdForCharacter = (c: { name: string; series_id?: string; source?: string }) => {
    const series = c.series_id || c.source;
    if (!series) return setDocId(null);
    const curVol = volumes.find((v) => v.doc_id === docId);
    if (curVol && curVol.series_id === series) return;
    const firstVol = volumes.find((v) => v.series_id === series);
    setDocId(firstVol ? firstVol.doc_id : null);
  };

  const hasAssistant = messages.some((m) => m.role === 'assistant');
  const selCls = 'bg-surface-2 border border-line rounded-xl px-3 py-1.5 text-sm text-ink focus:outline-none focus:border-brand/50 cursor-pointer';

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      <div className="pointer-events-none absolute -top-32 -left-32 w-96 h-96 rounded-full bg-accent/10 blur-3xl" />

      {/* Header */}
      <header className="relative z-10 flex items-center gap-3 px-6 py-3.5 border-b border-line bg-surface/60 backdrop-blur-xl shrink-0 flex-wrap">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-accent to-brand flex items-center justify-center shrink-0 shadow-sm shadow-accent/30">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="10" /><path d="M8 14s1.5 2 4 2 4-2 4-2" /><line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" /></svg>
        </div>
        <select value={character || ''} onChange={(e) => {
          const next = characters.find((c) => c.name === e.target.value);
          if (!next) return;
          setCharacter(e.target.value);
          syncDocIdForCharacter(next);
        }} className={`${selCls} max-w-[200px]`} title="切换角色">
          {!charsLoaded && <option value="">加载中…</option>}
          {characters.length === 0 && charsLoaded && <option value="">暂无已建卡角色</option>}
          {characters.map((c) => (
            <option key={`${c.series_id || c.source}-${c.name}`} value={c.name}>
              {c.name}
              {c.source || c.series_id ? ` — ${c.source || c.series_id}` : ''}
            </option>
          ))}
        </select>
        {seriesVolumes.length > 0 && (
          <select value={docId || ''} onChange={(e) => setDocId(e.target.value || null)} className={`${selCls} text-xs max-w-[150px]`} title="锁定检索卷">
            <option value="">全部卷</option>
            {seriesVolumes.map((v) => (
              <option key={v.doc_id} value={v.doc_id}>{v.volume_title || v.title || v.doc_id}</option>
            ))}
          </select>
        )}
        <div className="flex items-center gap-1.5 ml-auto">
          {maxHistoryTokens != null && (
            <span className="text-[10px] text-faint hidden md:inline" title="会话记忆上限">
              记忆 ~{maxHistoryTokens} tokens
            </span>
          )}
          <button type="button" className="btn-ghost btn-sm" onClick={() => { setSessionOpen(true); void refreshSessions(); }}>
            存档{sessions.length > 0 ? ` (${sessions.length})` : ''}
          </button>
          {hasAssistant && (
            <button type="button" className="btn-ghost btn-sm" disabled={loading || !sessionId} onClick={() => void regenerate()}>
              重生成
            </button>
          )}
          <IconButton label="重置对话" onClick={() => void resetSession()}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" /></svg>
          </IconButton>
        </div>
      </header>

      {/* 消息区 */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {memoryStats ? (
          <div className="max-w-3xl mx-auto mb-3">
            <ContextMonitor stats={memoryStats} maxHistoryTokens={maxHistoryTokens} />
          </div>
        ) : null}
        {messages.length === 0 ? (
          <div className="max-w-lg mx-auto pt-16">
            <Empty
              icon={activeCharacter ? '🎭' : '👤'}
              title={activeCharacter ? `开始与 ${activeCharacter} 对话` : '选择一个角色'}
              desc={
                characters.length === 0
                  ? '还没有已建卡角色。请先前往「知识库」导入小说并生成角色卡。'
                  : '角色将按照原著设定与你交谈，回复可展开查看原文出处。可从「存档」读取历史会话。'
              }
            />
            {characters.length === 0 && volumes.length === 0 && (
              <p className="text-center text-xs text-muted mt-2">
                提示：先导入小说，再到知识库 → 角色管线勾选角色建卡。
              </p>
            )}
          </div>
        ) : (
          <div className="max-w-[820px] mx-auto space-y-5">
            {messages.map((msg) =>
              msg.role === 'user' ? (
                <div key={msg.id} className="flex justify-end animate-slide-up">
                  <div className="max-w-[78%] bg-gradient-to-br from-brand to-brand-strong text-white rounded-2xl rounded-tr-sm px-4 py-3 shadow-md shadow-brand/20">
                    <p className="whitespace-pre-wrap text-sm leading-relaxed">{msg.content}</p>
                  </div>
                </div>
              ) : (
                <div key={msg.id} className="flex justify-start">
                  <BotBubble
                    character={activeCharacter || ''}
                    content={msg.content}
                    citations={msg.citations}
                    meta={formatMetaLine({ elapsedMs: msg.elapsed, usage: msg.usage })}
                    onOpenEvidence={(title, evidence) => setEvidenceItem({ title, evidence })}
                  />
                </div>
              ),
            )}
            {loading && (
              <div className="flex items-center gap-2 pl-1 text-muted animate-fade-in">
                <Spinner size={16} className="text-accent" />
                <span className="text-xs">{activeCharacter} 正在思考…</span>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* 错误 */}
      {error && (
        <div className="relative z-10 px-6 pb-2">
          <div className="max-w-[820px] mx-auto rounded-xl border border-danger/30 bg-danger/8 px-4 py-2.5 text-sm text-danger flex items-center justify-between animate-slide-up">
            <span>{error}</span>
            <button type="button" onClick={() => setError(null)} className="text-danger/70 hover:text-danger text-xs font-medium">关闭</button>
          </div>
        </div>
      )}

      {/* 输入区 */}
      <div className="relative z-10 shrink-0 px-6 pb-5 pt-2 bg-gradient-to-t from-bg via-bg/90 to-transparent">
        <div className="max-w-[820px] mx-auto">
          <div className="relative flex items-end gap-2 bg-surface rounded-2xl border border-line p-2 shadow-card transition-all focus-within:border-accent/50 focus-within:shadow-glow">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && input.trim() && !loading && activeCharacter) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={
                !charsLoaded ? '加载角色列表中…' :
                !activeCharacter ? '请先选择角色（需先在知识库建卡）' :
                `对 ${activeCharacter} 说点什么…（Enter 发送）`
              }
              disabled={loading || !charsLoaded || !activeCharacter}
              rows={1}
              className="flex-1 bg-transparent border-none outline-none text-sm text-ink placeholder-faint resize-none py-2 px-2 min-h-[20px] max-h-[160px] disabled:opacity-60"
            />
            <button
              type="button"
              onClick={loading ? abort : handleSend}
              disabled={!loading && (!input.trim() || !activeCharacter)}
              className={`shrink-0 w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-200 ${
                loading
                  ? 'bg-surface-3 text-ink active:scale-95'
                  : input.trim() && activeCharacter
                    ? 'bg-gradient-to-br from-accent to-brand text-white hover:shadow-glow-sm active:scale-95'
                    : 'bg-surface-3 text-faint cursor-not-allowed'
              }`}
              title={loading ? '停止' : '发送'}
            >
              {loading ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="3" /></svg>
              ) : (
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" /></svg>
              )}
            </button>
          </div>
        </div>
      </div>

      <SessionDrawer
        open={sessionOpen}
        onClose={() => setSessionOpen(false)}
        sessions={sessions}
        currentSessionId={sessionId}
        loading={sessionsLoading}
        onNewSession={() => { startNewSession(); setSessionOpen(false); }}
        onSelect={(id) => { void loadSession(id).then(() => setSessionOpen(false)); }}
        onRename={(id, title) => { void renameSession(id, title); }}
        onDelete={(id) => { void deleteSession(id); }}
        onRefresh={() => { void refreshSessions(); }}
      />

      {evidenceItem && (
        <EvidencePanel
          title={evidenceItem.title}
          evidence={evidenceItem.evidence}
          onClose={() => setEvidenceItem(null)}
        />
      )}
    </div>
  );
}
