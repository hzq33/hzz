/**
 * 剧情分析面板：关系/事件索引构建（含作业轮询）+ 事件/伏笔/关系展示。
 */
import { useCallback, useEffect, useState } from 'react';

import { fetchStoryAnalysisJob } from '@/api/jobs';
import { fetchStoryAnalysis, buildStoryAnalysis, normalizeStoryAnalysis } from '@/api/world';
import { Badge, Empty, SectionCard, Spinner } from '@/components/ui/aura';
import { toUserErrorMessage } from '@/lib/errors';
import { pollJob } from '@/lib/pollJob';
import type { StoryAnalysis, StoryEvent, RelationChange, ForeshadowItem } from '@/types';

export default function StoryTab({
  seriesId,
  docId,
  onMessage,
  onError,
}: {
  seriesId: string;
  docId?: string;
  onMessage: (msg: string) => void;
  onError: (msg: string) => void;
}) {
  const [story, setStory] = useState<StoryAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!seriesId) return;
    setLoading(true);
    try {
      const data = await fetchStoryAnalysis(seriesId, docId);
      setStory(data);
    } catch {
      setStory(null);
    } finally {
      setLoading(false);
    }
  }, [seriesId, docId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleBuild = async (force = false) => {
    if (!seriesId) return;
    setBusy(true);
    onError('');
    try {
      const res = await buildStoryAnalysis({ series_id: seriesId, doc_id: docId, force, wait: false });
      if (res.analysis) {
        const a = normalizeStoryAnalysis(res.analysis);
        setStory(a);
        onMessage((a?.events?.length || a?.relations?.length) ? '关系与事件索引已就绪' : '索引任务完成，但未抽出有效线索，请强制重跑');
        return;
      }
      if (!res.job_id) {
        onMessage('未返回任务 ID');
        return;
      }
      onMessage('正在生成关系与事件索引…');
      const job = await pollJob({
        fetchJob: () => fetchStoryAnalysisJob(res.job_id!),
        intervalMs: 1000,
        maxTries: 600,
        onProgress: (p) => onMessage((p as { message?: string })?.message || '正在生成关系与事件索引…'),
      });
      if (job.state === 'done') {
        const analysis = normalizeStoryAnalysis(job.result?.analysis) || (await fetchStoryAnalysis(seriesId, docId));
        setStory(analysis);
        const empty = !(analysis?.events?.length || analysis?.relations?.length);
        onMessage(empty ? '索引完成但未抽出有效线索 → 请强制重跑' : '关系与事件索引完成');
        return;
      }
      if (job.state === 'failed') throw new Error(job.error || 'story analysis failed');
      onMessage('轮询超时：任务可能仍在后台运行，请稍后刷新');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (/timed out|polling timed out/i.test(msg)) {
        onMessage('轮询超时：任务可能仍在后台运行，请稍后刷新');
        return;
      }
      onError(toUserErrorMessage(err, '关系与事件索引失败'));
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <div className="py-12 text-center text-muted text-sm">加载中…</div>;

  const hasData = story && story.exists !== false;

  return (
    <SectionCard
      title="剧情分析"
      desc="关系 / 事件 / 伏笔的结构化索引，驱动关系图谱、时间线与全局问答"
      actions={
        <div className="flex gap-2">
          <button type="button" className="btn-ghost btn-sm" disabled={busy || !seriesId} onClick={() => void handleBuild()}>
            {busy ? <Spinner size={12} className="text-brand" /> : null}
            {story?.stats?.cache_hit ? '重新索引' : '生成索引'}
          </button>
          <button type="button" className="btn-soft btn-sm" disabled={busy || !seriesId} onClick={() => void handleBuild(true)}>
            强制重跑
          </button>
        </div>
      }
    >
      {!seriesId ? (
        <Empty icon="🌐" title="选择系列" desc="先在知识库导入小说并选择系列。" />
      ) : !hasData ? (
        <Empty icon="🧩" title="尚未生成剧情索引" desc="点击右上「生成索引」构建关系与事件（LLM 分析，可能需要数分钟）。" />
      ) : (
        <div className="space-y-5">
          {/* 事件 */}
          <div>
            <div className="text-xs font-semibold text-ink mb-2">事件 · {(story?.events || []).length}</div>
            <div className="grid gap-2 md:grid-cols-2">
              {(story?.events || []).map((ev: StoryEvent) => (
                <div key={ev.event_id} className="rounded-xl border border-line bg-surface-2 p-3 space-y-1.5 card-hover">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge tone="brand">{ev.event_type || '事件'}</Badge>
                    {ev.confidence != null && <span className="text-[10px] text-faint">{Math.round(ev.confidence * 100)}%</span>}
                    {ev.chapter_title && <span className="text-[10px] text-faint truncate flex-1 text-right">{ev.chapter_title}</span>}
                  </div>
                  <p className="text-sm text-ink leading-relaxed">{ev.summary}</p>
                  {(ev.characters || []).length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {(ev.characters || []).slice(0, 6).map((c) => <Badge key={c}>{c}</Badge>)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* 关系 */}
          <div>
            <div className="text-xs font-semibold text-ink mb-2">关系变化 · {(story?.relations || []).length}</div>
            <div className="space-y-1.5">
              {(story?.relations || []).map((r: RelationChange) => (
                <div key={r.change_id} className="rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 flex items-start gap-2.5 card-hover">
                  <div className="text-sm text-ink">
                    <span className="font-semibold">{r.source}</span>
                    <span className="text-faint mx-1.5">→</span>
                    <span className="font-semibold">{r.target}</span>
                    {r.relation_type && <Badge tone="accent" >{r.relation_type}</Badge>}
                    {r.polarity && (
                      <Badge tone={r.polarity === 'positive' ? 'ok' : r.polarity === 'negative' ? 'danger' : 'neutral'}>{r.polarity}</Badge>
                    )}
                  </div>
                  {r.summary && <span className="text-xs text-muted leading-relaxed">{r.summary}</span>}
                </div>
              ))}
            </div>
          </div>

          {/* 伏笔 */}
          {(story?.foreshadows || []).length > 0 && (
            <div>
              <div className="text-xs font-semibold text-ink mb-2">伏笔 · {(story?.foreshadows || []).length}</div>
              <div className="space-y-1.5">
                {(story?.foreshadows || []).map((f: ForeshadowItem) => (
                  <div key={f.foreshadow_id} className="rounded-xl border border-warn/25 bg-warn/8 px-3.5 py-2.5 text-sm text-ink leading-relaxed">
                    {f.content}
                    <div className="mt-1 flex items-center gap-2">
                      <Badge tone="warn">{f.status || '伏笔'}</Badge>
                      {f.introduced_chapter != null && <span className="text-[10px] text-faint">第 {f.introduced_chapter} 章引入</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}
