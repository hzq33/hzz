/**
 * GraphRAG 全局问答面板：全局摘要构建、全局问答、角色社区。
 */
import { useState } from 'react';

import { fetchRagGlobal, buildRagGlobal } from '@/api/world';
import { Badge, Empty, SectionCard, Spinner } from '@/components/ui/aura';
import { toUserErrorMessage } from '@/lib/errors';
import type { RagGlobalResponse } from '@/types';

export default function GlobalTab({ seriesId }: { seriesId: string }) {
  const [data, setData] = useState<RagGlobalResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [ctx, setCtx] = useState('');
  const [asking, setAsking] = useState(false);
  const [building, setBuilding] = useState(false);
  const [err, setErr] = useState('');

  const load = async (withQuery?: string) => {
    if (!seriesId) return;
    setLoading(true);
    try {
      const d = await fetchRagGlobal(seriesId, withQuery);
      setData(d);
      if (withQuery) setCtx(d.context ?? '');
    } catch (e) {
      setErr(toUserErrorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  const ask = async () => {
    if (!query.trim()) return;
    setAsking(true);
    setErr('');
    try {
      await load(query.trim());
    } finally {
      setAsking(false);
    }
  };

  const build = async () => {
    setBuilding(true);
    setErr('');
    try {
      await buildRagGlobal(seriesId, { wait: true });
      setCtx('');
      await load();
    } catch (e) {
      setErr(toUserErrorMessage(e));
    } finally {
      setBuilding(false);
    }
  };

  if (!seriesId) return <Empty icon="🌍" title="选择系列" desc="先在知识库选择系列。" />;
  if (loading && !data) return <div className="py-12 text-center text-muted text-sm">加载中…</div>;

  if (!data || data.exists === false) {
    return (
      <SectionCard title="GraphRAG 全局问答" desc="基于全书的关系网络与事件脉络回答主线级问题">
        <Empty
          icon="🌍"
          title="尚未构建全局摘要"
          desc={data?.hint ?? '请先运行剧情分析（会自动联动生成），或直接点击下方按钮构建。'}
          action={
            <button type="button" className="btn-primary" disabled={building} onClick={() => void build()}>
              {building ? <Spinner size={13} className="text-white" /> : null}
              {building ? '构建中…' : '立即构建全局摘要'}
            </button>
          }
        />
        {err && <div className="mt-3 rounded-xl border border-danger/30 bg-danger/8 px-3.5 py-2 text-sm text-danger">{err}</div>}
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title="GraphRAG 全局问答"
      desc="全书主线、整体关系网、跨章节主题"
      actions={
        data.stale ? (
          <button type="button" className="btn-soft btn-sm" disabled={building} onClick={() => void build()}>
            {building ? '重建中…' : '重新构建'}
          </button>
        ) : undefined
      }
    >
      {data.stale && (
        <div className="mb-3 rounded-xl border border-warn/25 bg-warn/8 px-3.5 py-2 text-xs text-warn">
          剧情内容已变更，全局摘要可能过期，建议重新构建。
        </div>
      )}
      {err && <div className="mb-3 rounded-xl border border-danger/30 bg-danger/8 px-3.5 py-2 text-sm text-danger">{err}</div>}

      {/* 问答 */}
      <div className="flex items-center gap-2 mb-4">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void ask()}
          placeholder="提问：这本书的主线是什么？谁和谁关系最紧密？…"
          className="input flex-1"
        />
        <button type="button" className="btn-primary" disabled={asking || !query.trim()} onClick={() => void ask()}>
          {asking ? <Spinner size={13} className="text-white" /> : null}
          {asking ? '查询中…' : '全局问答'}
        </button>
      </div>
      {ctx && (
        <div className="mb-4 rounded-xl bg-gradient-to-br from-brand-tint to-accent-soft border border-brand/20 p-4 text-sm text-ink leading-relaxed whitespace-pre-wrap animate-fade-in">
          {ctx}
        </div>
      )}

      {data.global_overview && (
        <div className="mb-4">
          <div className="text-sm font-semibold text-ink mb-2">📖 全书主线</div>
          <p className="text-sm text-muted leading-relaxed">{data.global_overview}</p>
        </div>
      )}

      {(data.communities ?? []).length > 0 && (
        <div>
          <div className="text-sm font-semibold text-ink mb-2">🗂 角色社区 · {(data.communities ?? []).length} 个</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {(data.communities ?? []).map((c) => (
              <div key={c.id} className="rounded-xl border border-line bg-surface-2 p-3.5 space-y-2 card-hover">
                <div className="flex flex-wrap gap-1">
                  {(c.members || []).slice(0, 6).map((m) => <Badge tone="accent" key={m}>{m}</Badge>)}
                  {(c.members || []).length > 6 && <span className="text-[10px] text-faint self-center">等 {c.members.length} 人</span>}
                </div>
                <p className="text-xs text-ink leading-relaxed">{c.summary}</p>
                {(c.core_relations || []).slice(0, 4).length > 0 && (
                  <div className="text-[11px] text-muted space-y-0.5">
                    {(c.core_relations || []).slice(0, 4).map((r) => (
                      <div key={`rel-${r.source}-${r.target}`}>
                        ↔ <span className="text-ink">{r.source}</span> × <span className="text-ink">{r.target}</span>
                        {r.relation_type ? <span className="text-faint">（{r.relation_type}）</span> : null}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </SectionCard>
  );
}
