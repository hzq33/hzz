/**
 * 对话页（Aurora 重新设计）—— 通用 Agent 对话：
 * 流式回复、规划/工具步骤卡片、HITL 审批、小说检索范围限定、快捷入口。
 */
import { useEffect, useRef, useState } from 'react';

import { Link } from 'react-router-dom';

import { decideToolApproval } from '@/api/chat';
import { fetchNovels } from '@/api/novels';
import { Composer } from '@/components/chat/Composer';
import { MessageBubble } from '@/components/chat/MessageBubble';
import { ReasoningFold } from '@/components/chat/ReasoningFold';
import { SessionRail } from '@/components/chat/SessionRail';
import { Badge, Button, PageHeader, Spinner } from '@/components/ui';
import { useSSE } from '@/hooks/useSSE';
import { QUICK_ACTIONS } from '@/lib/constants';
import { formatMetaLine } from '@/lib/formatUsage';
import { useChatStore } from '@/store/chatStore';
import type { ChatMessage, NovelVolumeInfo } from '@/types';

/* ═══════════ Icons ═══════════ */

const I = {
  sparkle: (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5z" /></svg>
  ),
  trash: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
  ),
};

/* ═══════════ 消息气泡 ═══════════ */

function AgentBubble({ message }: { message: ChatMessage }) {
  return (
    <MessageBubble
      role={message.role}
      name={message.role === 'assistant' ? 'Aurora' : undefined}
      content={message.content}
      split={message.role === 'assistant'}
      meta={formatMetaLine(message)}
      footer={message.stepResults?.length ? <StepResults results={message.stepResults} /> : null}
    >
      {message.plan ? <PlanCard plan={message.plan} /> : null}
    </MessageBubble>
  );
}

/* ═══════════ 规划卡片 ═══════════ */

function PlanCard({ plan }: { plan: NonNullable<ChatMessage['plan']> }) {
  return (
    <div className="rounded-xl border border-brand/25 bg-brand-tint dark:bg-brand/10 p-3.5 text-xs space-y-2 animate-scale-in">
      {plan.goal ? (
        <div className="font-medium text-brand-strong dark:text-brand">🎯 {plan.goal}</div>
      ) : null}
      {plan.reasoning ? (
        <ReasoningFold title="推理">{plan.reasoning}</ReasoningFold>
      ) : null}
      {plan.steps?.length ? (
        <ol className="space-y-1.5">
          {plan.steps.map((s) => (
            <li key={s.id} className="flex items-start gap-2">
              <span className="w-4 h-4 rounded-full bg-brand/15 text-brand-strong dark:text-brand text-[10px] flex items-center justify-center mt-0.5 shrink-0">
                {s.id}
              </span>
              <span className="text-ink">{s.description}</span>
              {s.tool_name ? <Badge tone="accent">{s.tool_name}</Badge> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function StepResults({ results }: { results: NonNullable<ChatMessage['stepResults']> }) {
  const [open, setOpen] = useState(false);
  const ok = results.filter((r) => r.success).length;
  return (
    <div className="mt-2.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-[11px] text-muted hover:text-ink flex items-center gap-1.5"
      >
        <span className={`w-1.5 h-1.5 rounded-full ${ok === results.length ? 'bg-ok' : 'bg-warn'}`} />
        工具执行 {ok}/{results.length} · {open ? '收起' : '展开'}
      </button>
      {open ? (
        <div className="mt-1.5 space-y-1.5">
          {results.map((r) => (
            <div key={r.step_id} className="rounded-lg bg-surface-2 border border-line p-2.5 text-[11px]">
              <div className="flex items-center gap-1.5 text-muted">
                <span className={r.success ? 'text-ok' : 'text-danger'}>{r.success ? '✓' : '✗'}</span>
                <span className="font-medium">{r.tool_name || `步骤 ${r.step_id}`}</span>
              </div>
              {r.output ? <pre className="mt-1.5 text-ink/80 whitespace-pre-wrap break-all max-h-40 overflow-y-auto font-mono">{r.output.slice(0, 600)}</pre> : null}
              {r.error ? <div className="mt-1.5 text-danger">{r.error}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ═══════════ 审批横幅 ═══════════ */

function ApprovalBanner() {
  const pending = useChatStore((s) => s.pendingApproval);
  const setPending = useChatStore((s) => s.setPendingApproval);
  const setError = useChatStore((s) => s.setError);
  const [busy, setBusy] = useState(false);

  if (!pending) return null;
  const decide = async (approved: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      await decideToolApproval(pending.approval_id, approved);
      setPending(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '审批请求失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-3 rounded-xl border border-warn/30 bg-warn/8 px-4 py-3 text-sm animate-slide-up">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium text-warn">需要确认高风险工具：{pending.tool_name}</div>
          {pending.tool_args ? (
            <div className="mt-1 font-mono text-[11px] text-muted break-all truncate">
              {JSON.stringify(pending.tool_args).slice(0, 180)}
            </div>
          ) : null}
        </div>
        <div className="flex gap-2 shrink-0">
          <button type="button" className="btn-danger btn-sm" disabled={busy} onClick={() => void decide(false)}>
            拒绝
          </button>
          <button type="button" className="btn-primary btn-sm" disabled={busy} onClick={() => void decide(true)}>
            允许
          </button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════ 检索范围选择 ═══════════ */

function NovelScopeSelector() {
  const novelScope = useChatStore((s) => s.novelScope);
  const setNovelScope = useChatStore((s) => s.setNovelScope);
  const [volumes, setVolumes] = useState<NovelVolumeInfo[]>([]);

  useEffect(() => {
    let alive = true;
    fetchNovels()
      .then((v) => alive && setVolumes(v))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const seriesOptions = [...new Set(volumes.map((v) => v.series_id).filter(Boolean))].sort();
  const currentSeries = novelScope?.series_id || '';
  const volumeOptions = currentSeries ? volumes.filter((v) => v.series_id === currentSeries) : [];
  if (seriesOptions.length === 0) return null;

  const selCls = 'bg-surface-2 border border-line rounded-lg px-2 py-1 text-xs text-ink focus:outline-none focus:border-brand/50';

  return (
    <div className="flex items-center gap-1.5 ml-2" title="限定小说检索范围（novel_scope）">
      <select
        value={currentSeries}
        onChange={(e) => {
          const sid = e.target.value;
          if (!sid) return setNovelScope(null);
          const firstDoc = volumes.find((v) => v.series_id === sid)?.doc_id;
          setNovelScope({ series_id: sid, doc_ids: firstDoc ? [firstDoc] : [] });
        }}
        className={selCls}
      >
        <option value="">全部检索范围</option>
        {seriesOptions.map((s) => (
          <option key={s} value={s}>{s}</option>
        ))}
      </select>
      {currentSeries && volumeOptions.length > 1 && (
        <select
          value={novelScope?.doc_ids?.[0] || ''}
          onChange={(e) =>
            setNovelScope({ series_id: currentSeries, doc_ids: e.target.value ? [e.target.value] : [] })
          }
          className={selCls}
        >
          <option value="">全部卷</option>
          {volumeOptions.map((v) => (
            <option key={v.doc_id} value={v.doc_id}>{v.volume_title || v.title || v.doc_id}</option>
          ))}
        </select>
      )}
      {currentSeries && (
        <button type="button" onClick={() => setNovelScope(null)} className="text-faint hover:text-danger px-1" title="清除检索范围">
          ×
        </button>
      )}
    </div>
  );
}

/* ═══════════ 欢迎页 ═══════════ */

function Welcome() {
  const setInput = useChatStore((s) => s.setInput);
  return (
    <div className="flex flex-col items-center justify-center min-h-full py-12 px-4 animate-fade-in">
      <div className="relative">
        <div className="w-20 h-20 rounded-3xl bg-gradient-to-br from-brand via-brand-strong to-accent flex items-center justify-center shadow-elevated shadow-brand/30 animate-float">
          <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z" />
            <path d="M8 14v2a4 4 0 0 0 8 0v-2" />
            <circle cx="9.5" cy="9.5" r="1" fill="white" stroke="none" />
            <circle cx="14.5" cy="9.5" r="1" fill="white" stroke="none" />
          </svg>
        </div>
        <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full bg-ok border-2 border-surface" />
      </div>

      <h2 className="mt-6 text-2xl font-bold tracking-tight">
        你好，我是 <span className="bg-gradient-to-r from-brand to-accent bg-clip-text text-transparent">Aurora Agent</span>
      </h2>
      <p className="mt-2 text-sm text-muted max-w-md text-center leading-relaxed">
        通用助手可搜索、管文件、查工作台；主打能力是
        <span className="text-ink font-medium"> 小说入库 → 建角色卡 → 沉浸扮演</span>。
      </p>

      <div className="mt-8 grid grid-cols-2 gap-3 w-full max-w-lg">
        {QUICK_ACTIONS.map((q) => {
          const href = 'href' in q ? q.href : undefined;
          return (
            <button
              key={q.label}
              type="button"
              onClick={() => {
                if (href) {
                  window.location.hash = href;
                  return;
                }
                setInput('prompt' in q ? q.prompt : q.label);
              }}
              className="group flex items-center gap-2.5 px-4 py-3 rounded-xl border border-line bg-surface text-sm text-muted font-medium hover:border-brand/40 hover:text-brand hover:shadow-card-hover transition-all duration-200"
            >
              <span className="text-base">{q.icon}</span>
              <span>{q.label}</span>
            </button>
          );
        })}
      </div>

      <Link to="/library" className="mt-6 text-xs text-brand hover:underline">
        还没导入小说？前往知识库 →
      </Link>
    </div>
  );
}

/* ═══════════ 页面 ═══════════ */

export default function ChatPage() {
  const messages = useChatStore((s) => s.messages);
  const input = useChatStore((s) => s.input);
  const loading = useChatStore((s) => s.loading);
  const error = useChatStore((s) => s.error);
  const streamPhase = useChatStore((s) => s.streamPhase);
  const setInput = useChatStore((s) => s.setInput);
  const archives = useChatStore((s) => s.archives);
  const sessionId = useChatStore((s) => s.sessionId);
  const persistCurrent = useChatStore((s) => s.persistCurrent);
  const startNewChat = useChatStore((s) => s.startNewChat);
  const loadArchive = useChatStore((s) => s.loadArchive);
  const deleteArchive = useChatStore((s) => s.deleteArchive);
  const { send, clearSession, abort } = useSSE();
  const bottomRef = useRef<HTMLDivElement>(null);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [railOpen, setRailOpen] = useState(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!loading && messages.length > 0) persistCurrent();
  }, [loading, messages.length, persistCurrent]);

  const isEmpty = messages.length === 0;
  const railSessions = archives.map((a) => ({
    session_id: a.id,
    title: a.title,
    preview: a.messages.find((m) => m.role === 'user')?.content?.slice(0, 40),
    updated_at: new Date(a.updatedAt).toISOString(),
    active: a.id === sessionId || a.sessionId === sessionId,
  }));

  const handleSend = () => {
    const names = attachments.map((f) => f.name);
    const note = names.length
      ? `\n\n（附件：${names.join('、')}。文件需在知识库入库后才能检索。）`
      : '';
    const text = `${input.trim()}${note}`.trim();
    if (!text) return;
    setAttachments([]);
    void send(text);
  };

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      <div className="pointer-events-none absolute -top-32 -right-32 w-96 h-96 rounded-full bg-brand/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-32 -left-32 w-96 h-96 rounded-full bg-accent/10 blur-3xl" />

      <PageHeader
        icon={<span className="text-brand">{I.sparkle}</span>}
        title="通用助手"
        extra={
          <>
            {!isEmpty && <Badge>{messages.length}</Badge>}
            <NovelScopeSelector />
          </>
        }
        actions={
          <>
            <Button tone="ghost" size="sm" onClick={() => setRailOpen((v) => !v)}>
              会话
            </Button>
            <Button tone="ghost" size="sm" onClick={startNewChat}>
              新对话
            </Button>
            {!isEmpty && (
              <button
                type="button"
                onClick={() => {
                  if (confirm('确定要清除当前会话？')) void clearSession();
                }}
                className="flex items-center gap-1.5 text-xs text-faint hover:text-danger transition-colors px-2.5 py-1.5 rounded-lg hover:bg-danger/10"
              >
                {I.trash}
                清除会话
              </button>
            )}
          </>
        }
      />

      <div className="flex min-h-0 flex-1">
        {railOpen ? (
          <SessionRail
            sessions={railSessions}
            activeId={sessionId}
            onNew={startNewChat}
            onSelect={loadArchive}
            onDelete={(id) => {
              if (confirm('删除这条存档？')) deleteArchive(id);
            }}
          />
        ) : null}

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-y-auto relative z-0">
            {isEmpty ? (
              <Welcome />
            ) : (
              <div className="max-w-[820px] mx-auto py-6 px-6 space-y-5">
                {messages.map((msg) => (
                  <AgentBubble key={msg.id} message={msg} />
                ))}
                {loading && streamPhase ? (
                  <div className="flex items-center gap-2.5 text-xs text-muted pl-11 animate-fade-in">
                    <Spinner size={14} className="text-brand" />
                    <span>
                      {streamPhase === 'planning' && '正在规划…'}
                      {streamPhase === 'tool_calling' && '正在调用工具…'}
                      {streamPhase === 'executing' && '正在执行…'}
                      {streamPhase === 'replying' && '正在生成回复…'}
                      {streamPhase === 'plan_failed' && '规划失败，尝试直接回复…'}
                    </span>
                  </div>
                ) : null}
                {error ? (
                  <div className="ml-11 rounded-xl border border-danger/30 bg-danger/8 px-4 py-2.5 text-sm text-danger animate-fade-in">
                    {error}
                  </div>
                ) : null}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <div className="relative z-10 shrink-0 px-6 pb-5 pt-2 bg-gradient-to-t from-bg via-bg/90 to-transparent">
            <div className="max-w-[820px] mx-auto">
              <ApprovalBanner />
              <Composer
                value={input}
                onChange={setInput}
                onSend={handleSend}
                onStop={abort}
                loading={loading}
                allowAttach
                attachments={attachments}
                onAttachmentsChange={setAttachments}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
