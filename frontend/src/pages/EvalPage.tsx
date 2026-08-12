/**
 * 评估中心页（Aurora 重新设计）—— RAG 在线评估 + LLM 判定回收。
 */
import { useCallback, useEffect, useState } from 'react';

import { fetchRagEval, judgeRagEval } from '@/api/eval';
import { Badge, Empty, SectionCard, Spinner, StatCard } from '@/components/ui/aura';
import { toUserErrorMessage } from '@/lib/errors';
import type { RagEvalResponse, RagJudgeResponse, RagEvalCase } from '@/types';

const CHANNEL_LABELS: Record<string, string> = {
  narrative: '叙事', dialogue: '对话', character: '角色', qa: 'QA',
};

export default function EvalPage() {
  const [data, setData] = useState<RagEvalResponse | null>(null);
  const [judge, setJudge] = useState<RagJudgeResponse | null>(null);
  const [judging, setJudging] = useState(false);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<{ q?: string; channel?: string; zeroOnly?: boolean }>({});

  const load = useCallback(async (f: typeof filter) => {
    setError('');
    try {
      const resp = await fetchRagEval({
        kind: undefined,
        channel: f.channel,
        q: f.q || undefined,
        zero_only: f.zeroOnly,
        limit: 300,
      });
      setData(resp);
      setJudge(null);
    } catch (e) {
      setError(toUserErrorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load({});
  }, [load]);

  const runJudge = async () => {
    setJudging(true);
    setError('');
    try {
      const resp = await judgeRagEval({ limit: 100, concurrency: 3, q: filter.q, kind: undefined });
      setJudge(resp);
    } catch (e) {
      setError(toUserErrorMessage(e));
    } finally {
      setJudging(false);
    }
  };

  const s = data?.summary;
  const scoreByQuery = new Map((judge?.results ?? []).map((r) => [r.query, r]));

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      <div className="pointer-events-none absolute -top-32 -right-32 w-96 h-96 rounded-full bg-brand/10 blur-3xl" />

      <header className="relative z-10 flex items-center gap-3 px-6 py-3.5 border-b border-line bg-surface/60 backdrop-blur-xl shrink-0">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-brand to-accent flex items-center justify-center shadow-sm shadow-brand/30">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 15l3.5-3.5" /><path d="M20.3 18a10 10 0 1 0-16.6 0" /><circle cx="12" cy="15" r="1" /></svg>
        </div>
        <div className="flex-1">
          <h2 className="text-sm font-semibold">评估中心</h2>
          <p className="text-[11px] text-faint">现存会话检索复盘 · 结构信号 + LLM 判定</p>
        </div>
        <button type="button" className="btn-primary btn-sm" disabled={judging} onClick={() => void runJudge()}>
          {judging ? <Spinner size={12} className="text-white" /> : null}
          {judging ? 'LLM 评分中…' : judge ? '重新 LLM 评分' : 'LLM 自动评分'}
        </button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5 relative z-0">
        <div className="max-w-4xl mx-auto space-y-4">
          {error && (
            <div className="rounded-xl border border-danger/30 bg-danger/8 px-4 py-2.5 text-sm text-danger">{error}</div>
          )}

          {data?.active_sessions != null && (
            <p className="text-[11px] text-faint">当前现存会话 {data.active_sessions} 个 · 仅统计现存会话产生的检索</p>
          )}

          {/* 概览统计 */}
          {s && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard label="检索总数" value={s.total ?? 0} />
              <StatCard label="零命中" value={`${s.zero_hit ?? 0} (${Math.round((s.zero_hit_rate ?? 0) * 100)}%)`} tone={s.zero_hit_rate && s.zero_hit_rate > 0.3 ? 'danger' : 'ok'} />
              <StatCard label="平均命中" value={(s.avg_hits ?? 0).toFixed(1)} tone="brand" />
              <StatCard label="平均耗时" value={`${Math.round(s.avg_ms ?? 0)}ms`} tone="accent" />
            </div>
          )}

          {/* 过滤器 */}
          <div className="flex items-center gap-2 flex-wrap">
            <input
              value={filter.q ?? ''}
              onChange={(e) => setFilter({ ...filter, q: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && void load(filter)}
              placeholder="按 query 过滤…"
              className="input text-sm w-56"
            />
            <select
              value={filter.channel ?? ''}
              onChange={(e) => {
                const f = { ...filter, channel: e.target.value || undefined };
                setFilter(f);
                void load(f);
              }}
              className="input text-sm w-36"
            >
              <option value="">全部通道</option>
              {Object.entries(CHANNEL_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-muted cursor-pointer">
              <input
                type="checkbox"
                className="accent-[rgb(var(--brand))]"
                checked={filter.zeroOnly ?? false}
                onChange={(e) => {
                  const f = { ...filter, zeroOnly: e.target.checked || undefined };
                  setFilter(f);
                  void load(f);
                }}
              />
              只看零命中
            </label>
            <button type="button" className="btn-ghost btn-sm" onClick={() => void load(filter)}>刷新</button>
          </div>

          {/* LLM 判定结果 */}
          {judge && (
            <SectionCard title="LLM 判定结果" desc={`${judge.summary?.judged ?? 0} 条已评分 · 平均 ${(judge.summary?.avg_score ?? 0).toFixed(2)} · 低分 ${judge.summary?.low_count ?? 0} 条`}>
              {(judge.low ?? []).length > 0 && (
                <div className="space-y-1.5 mb-3">
                  {(judge.low ?? []).slice(0, 5).map((r) => (
                    <div key={`low-${r.query}`} className="rounded-lg border border-warn/25 bg-warn/8 px-3 py-2 text-xs">
                      <span className="text-warn font-medium">{r.query}</span>
                      <span className="ml-2 text-faint">[{CHANNEL_LABELS[r.channel] || r.channel}]</span>
                      <span className="ml-2 text-muted">得分 {r.score ?? '—'}</span>
                      <div className="text-muted mt-0.5 truncate">{r.reason}</div>
                    </div>
                  ))}
                </div>
              )}
            </SectionCard>
          )}

          {/* 案例列表 */}
          <SectionCard title="检索案例" desc={`${data?.total_available ?? 0} 条可评估记录`}>
            {!data || (data.cases ?? []).length === 0 ? (
              <Empty icon="📊" title="暂无检索记录" desc="开始对话后，检索链路的 trace 会在此汇总。" />
            ) : (
              <div className="space-y-2">
                {(data.cases ?? []).map((c: RagEvalCase) => {
                  const j = scoreByQuery.get(c.query);
                  return (
                    <div key={`case-${c.query}-${c.ts}`} className="rounded-xl border border-line bg-surface-2 p-3 space-y-1.5 card-hover">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge tone={c.zero_hit ? 'danger' : 'ok'}>{c.zero_hit ? '零命中' : `${c.hit_count} 命中`}</Badge>
                        {c.channel && <Badge>{CHANNEL_LABELS[c.channel] || c.channel}</Badge>}
                        {c.query_variants != null && <Badge>{c.query_variants} 变体</Badge>}
                        {c.elapsed_ms != null && <span className="text-[10px] text-faint">{c.elapsed_ms}ms</span>}
                        {j && (
                          <Badge tone={j.score != null && j.score >= 0.7 ? 'ok' : 'warn'}>
                            LLM {j.score?.toFixed(2) ?? '—'}
                          </Badge>
                        )}
                      </div>
                      <div className="text-sm text-ink font-medium">{c.query}</div>
                      {(c.hits ?? []).slice(0, 3).map((h) => (
                        <div key={`hit-${h.global_id}`} className="flex items-center gap-2 text-[11px] text-muted">
                          <span className="text-faint shrink-0 font-mono">{h.chapter_title || h.block_type}</span>
                          <span className="truncate">{h.preview}</span>
                        </div>
                      ))}
                      {j?.reason && <div className="text-[11px] text-faint truncate">{j.reason}</div>}
                    </div>
                  );
                })}
              </div>
            )}
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
