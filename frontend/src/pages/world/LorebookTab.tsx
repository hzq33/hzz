/**
 * 设定书面板：实体/关系条目（Lorebook）。
 */
import { useEffect, useState } from 'react';

import { fetchLorebook } from '@/api/world';
import { Badge, Empty, SectionCard } from '@/components/ui/aura';
import type { LorebookResponse, LorebookEntry } from '@/types';

export default function LorebookTab({ seriesId }: { seriesId: string }) {
  const [data, setData] = useState<LorebookResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!seriesId) return;
    const ctrl = new AbortController();
    setLoading(true);
    fetchLorebook(seriesId, ctrl.signal)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [seriesId]);

  if (!seriesId) return <Empty icon="📖" title="选择系列" desc="先在知识库选择系列。" />;
  if (loading) return <div className="py-12 text-center text-muted text-sm">加载中…</div>;
  if (!data || data.exists === false)
    return <Empty icon="📖" title="暂无设定书" desc="先运行剧情分析，设定书将随世界体系自动生成。" />;

  const entries = data.entries || [];
  const entityEntries = entries.filter((e) => e.kind === 'entity');
  const relationEntries = entries.filter((e) => e.kind === 'relation');

  return (
    <SectionCard
      title="设定书"
      desc={`${entries.length} 条设定 · ${data.stats?.entity_count || 0} 个实体 · ${data.stats?.event_count || 0} 个事件`}
    >
      {entries.length === 0 ? (
        <Empty icon="📖" title="暂无条目" />
      ) : (
        <div className="space-y-5">
          {entityEntries.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-ink mb-2">实体 · {entityEntries.length}</div>
              <div className="grid gap-2 md:grid-cols-2">
                {entityEntries.map((entry: LorebookEntry) => (
                  <EntryCard key={entry.entry_id} entry={entry} />
                ))}
              </div>
            </div>
          )}
          {relationEntries.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-ink mb-2">关系 · {relationEntries.length}</div>
              <div className="grid gap-2 md:grid-cols-2">
                {relationEntries.map((entry: LorebookEntry) => (
                  <EntryCard key={entry.entry_id} entry={entry} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}

function EntryCard({ entry }: { entry: LorebookEntry }) {
  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3 space-y-1.5 card-hover">
      <div className="flex items-center gap-2 flex-wrap">
        <Badge tone={entry.kind === 'entity' ? 'brand' : 'accent'}>
          {entry.kind === 'entity' ? entry.entity : `${entry.entity} ↔ ${entry.counterpart || '?'}`}
        </Badge>
        {entry.time_range?.era && <Badge>{entry.time_range.era}</Badge>}
        {entry.priority > 0 && <span className="text-[10px] text-faint ml-auto">P{entry.priority}</span>}
      </div>
      <p className="text-xs text-ink leading-relaxed">{entry.content}</p>
      {(entry.keys || []).slice(0, 4).length > 0 && (
        <div className="flex flex-wrap gap-1">
          {(entry.keys || []).slice(0, 4).map((k) => <Badge key={k}>{k}</Badge>)}
        </div>
      )}
    </div>
  );
}
