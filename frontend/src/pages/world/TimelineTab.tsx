/**
 * 时间线面板：剧情时间线（按时代/顺序）+ 角色事件索引。
 */
import { useEffect, useState } from 'react';

import { fetchTimeline } from '@/api/world';
import { Badge, Empty, Modal, SectionCard } from '@/components/ui/aura';
import type { TimelineResponse, ChronicleEvent } from '@/types';

export default function TimelineTab({ seriesId }: { seriesId: string }) {
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedChar, setSelectedChar] = useState<string | null>(null);

  useEffect(() => {
    if (!seriesId) return;
    const ctrl = new AbortController();
    setLoading(true);
    fetchTimeline(seriesId, ctrl.signal)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [seriesId]);

  if (!seriesId) return <Empty icon="⏳" title="选择系列" desc="先在知识库选择系列。" />;
  if (loading) return <div className="py-12 text-center text-muted text-sm">加载中…</div>;
  if (!data || data.exists === false)
    return <Empty icon="⏳" title="暂无时间线" desc="先运行剧情分析生成时间线。" />;

  const events = data.chronicle || [];
  const charSeqs = selectedChar ? (data.by_character || {})[selectedChar] || [] : [];
  const charEvents = events.filter((e) => charSeqs.includes(e.seq));

  return (
    <SectionCard title="剧情时间线" desc={`${events.length} 个事件 · ${data.stats?.character_count || 0} 个角色`}>
      {/* 角色索引 */}
      <div className="flex items-center gap-1.5 flex-wrap mb-4">
        <span className="text-xs text-muted">角色：</span>
        {Object.keys(data.by_character || {}).map((name) => (
          <button
            key={name}
            type="button"
            className={selectedChar === name ? 'chip chip-brand' : 'chip hover:border-brand/50'}
            onClick={() => setSelectedChar(selectedChar === name ? null : name)}
          >
            {name}
            <span className="text-[9px] opacity-60">{(data.by_character || {})[name].length}</span>
          </button>
        ))}
      </div>

      {selectedChar && (
        <div className="mb-4 rounded-xl border border-brand/25 bg-brand-tint p-3">
          <div className="text-xs font-medium text-brand-strong dark:text-brand mb-1.5">
            「{selectedChar}」的剧情线 · {charEvents.length} 个事件
          </div>
          <div className="space-y-1">
            {charEvents.map((e) => (
              <div key={e.seq} className="text-xs text-ink flex gap-2">
                <span className="text-faint tabular-nums shrink-0">#{e.seq}</span>
                <span className="truncate">{e.summary}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 时间线流 */}
      <div className="relative pl-6 space-y-0">
        <div className="absolute left-[7px] top-1 bottom-1 w-px bg-line" />
        {events.map((e: ChronicleEvent) => (
          <div key={e.seq} className="relative pb-4">
            <div className={`absolute -left-6 top-1.5 w-[15px] h-[15px] rounded-full border-2 border-surface ${e.key_event ? 'bg-brand' : 'bg-accent/60'}`} />
            <div className="rounded-xl border border-line bg-surface-2 p-3 card-hover">
              <div className="flex items-center gap-2 flex-wrap mb-1">
                <Badge tone={e.key_event ? 'brand' : 'accent'}>{e.event_type}</Badge>
                {e.story_time?.period && <Badge>{e.story_time.period}</Badge>}
                {e.chapter_title && <span className="text-[10px] text-faint truncate flex-1 text-right">{e.chapter_title}</span>}
              </div>
              <p className="text-sm text-ink leading-relaxed">{e.summary}</p>
              {(e.characters || []).length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {(e.characters || []).slice(0, 6).map((c) => <Badge key={c}>{c}</Badge>)}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      <Modal open={!!selectedChar} onClose={() => setSelectedChar(null)} title={`「${selectedChar || ''}」的剧情线`} width="max-w-lg">
        <div className="space-y-1.5 max-h-[55vh] overflow-y-auto">
          {charEvents.map((e) => (
            <div key={e.seq} className="rounded-lg border border-line bg-surface-2 p-2.5 text-xs text-ink">
              <span className="text-faint tabular-nums mr-1.5">#{e.seq}</span>
              {e.summary}
            </div>
          ))}
        </div>
      </Modal>
    </SectionCard>
  );
}
