/**
 * 世界体系页（Aurora 重新设计）—— Tab：剧情分析 / 时间线 / 设定书 / 全局问答。
 */
import { useEffect, useState } from 'react';

import { fetchNovels } from '@/api/novels';
import { Tabs } from '@/components/ui/aura';
import type { NovelVolumeInfo } from '@/types';

import GlobalTab from './world/GlobalTab';
import LorebookTab from './world/LorebookTab';
import StoryTab from './world/StoryTab';
import TimelineTab from './world/TimelineTab';

type TabKey = 'story' | 'timeline' | 'lorebook' | 'global';

export default function WorldPage() {
  const [novels, setNovels] = useState<NovelVolumeInfo[]>([]);
  const [seriesId, setSeriesId] = useState('');
  const [tab, setTab] = useState<TabKey>('story');
  const [toast, setToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchNovels()
      .then((v) => {
        setNovels(v);
        if (!seriesId && v[0]?.series_id) setSeriesId(v[0].series_id);
      })
      .catch(() => {});
  }, [seriesId]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  };

  const seriesOptions = [...new Set(novels.map((v) => v.series_id).filter(Boolean))].sort();

  const tabs = [
    { key: 'story' as TabKey, label: '剧情分析' },
    { key: 'timeline' as TabKey, label: '时间线' },
    { key: 'lorebook' as TabKey, label: '设定书' },
    { key: 'global' as TabKey, label: '全局问答' },
  ];

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      <div className="pointer-events-none absolute -top-32 -right-32 w-96 h-96 rounded-full bg-accent/10 blur-3xl" />

      <header className="relative z-10 flex items-center gap-3 px-6 py-3.5 border-b border-line bg-surface/60 backdrop-blur-xl shrink-0 flex-wrap">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-accent to-brand flex items-center justify-center shadow-sm shadow-accent/30">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" /></svg>
        </div>
        <h2 className="text-sm font-semibold">世界体系</h2>
        {seriesOptions.length > 0 && (
          <select
            value={seriesId}
            onChange={(e) => setSeriesId(e.target.value)}
            className="bg-surface-2 border border-line rounded-xl px-2.5 py-1.5 text-xs text-ink focus:outline-none focus:border-brand/50 max-w-[220px]"
          >
            {seriesOptions.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        <Tabs items={tabs} active={tab} onChange={(k) => setTab(k as TabKey)} className="ml-2" />
      </header>

      {(toast || error) && (
        <div className="relative z-10 px-6 pt-2">
          {toast && (
            <div className="rounded-xl border border-ok/30 bg-ok/8 px-4 py-2 text-sm text-ok animate-slide-up">
              {toast}
              <button type="button" className="ml-2 text-ok/60 hover:text-ok" onClick={() => setToast(null)}>×</button>
            </div>
          )}
          {error && (
            <div className="rounded-xl border border-danger/30 bg-danger/8 px-4 py-2 text-sm text-danger animate-slide-up flex items-center justify-between">
              <span>{error}</span>
              <button type="button" className="text-danger/70 hover:text-danger" onClick={() => setError(null)}>关闭</button>
            </div>
          )}
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-6 py-5 relative z-0">
        <div className="max-w-4xl mx-auto">
          {tab === 'story' && <StoryTab seriesId={seriesId} onMessage={showToast} onError={(m) => setError(m)} />}
          {tab === 'timeline' && <TimelineTab seriesId={seriesId} />}
          {tab === 'lorebook' && <LorebookTab seriesId={seriesId} />}
          {tab === 'global' && <GlobalTab seriesId={seriesId} />}
        </div>
      </div>
    </div>
  );
}
