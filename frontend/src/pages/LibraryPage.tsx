/**
 * 知识库页（Aurora 重新设计）—— Tab：书目 / 角色 / 图谱 / 名录。
 * 职责：小说导入与书目管理、角色管线（建卡/合并/歧义消解）、关系图谱、别名名录。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { fetchCharacters, buildCharacters, fetchMergeSuggestions, type CharacterMergeSuggestion } from '@/api/characters';
import { fetchCharacterJob } from '@/api/jobs';
import { fetchNovels, fetchOrphanDocIds } from '@/api/novels';
import { RelationshipGraph } from '@/components/knowledge/RelationshipGraph';
import { Tabs } from '@/components/ui/aura';
import { toUserErrorMessage } from '@/lib/errors';
import { characterBuildPollOptions, pollJob } from '@/lib/pollJob';
import type { CharacterInfo, NovelVolumeInfo, CharacterBuildJobInfo, DisambiguationCandidate } from '@/types';

import BooksPanel from './library/BooksPanel';
import CharactersPanel from './library/CharactersPanel';
import RosterPanel from './library/RosterPanel';


type TabKey = 'books' | 'characters' | 'graph' | 'roster';

function extractDisambiguation(job: CharacterBuildJobInfo): { inputName: string; candidates: DisambiguationCandidate[] } | null {
  const flags = job.flags as { candidates?: DisambiguationCandidate[] } | undefined;
  const cands = flags?.candidates || [];
  if (cands.length > 0) return { inputName: job.input_name, candidates: cands };
  return null;
}

export default function LibraryPage() {
  const [tab, setTab] = useState<TabKey>('books');
  const [novels, setNovels] = useState<NovelVolumeInfo[]>([]);
  const [characters, setCharacters] = useState<CharacterInfo[]>([]);
  const [orphanDocIds, setOrphanDocIds] = useState<string[]>([]);
  const [seriesId, setSeriesId] = useState('');
  const [docId, setDocId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [jobStates, setJobStates] = useState<Record<string, string>>({});
  const [building, setBuilding] = useState(false);
  const [merging, setMerging] = useState(false);
  const [mergeSuggestions, setMergeSuggestions] = useState<CharacterMergeSuggestion[]>([]);
  const [disambiguationQueue, setDisambiguationQueue] = useState<{ inputName: string; candidates: DisambiguationCandidate[] }[]>([]);
  const pollRef = useRef(0);
  const jobAbortRef = useRef<AbortController | null>(null);

  const beginJobAbort = () => {
    jobAbortRef.current?.abort();
    const ac = new AbortController();
    jobAbortRef.current = ac;
    return ac.signal;
  };

  useEffect(() => {
    return () => {
      pollRef.current += 1;
      jobAbortRef.current?.abort();
    };
  }, []);

  const seriesOptions = useMemo(
    () => [...new Set(novels.map((n) => n.series_id).filter(Boolean))].sort(),
    [novels],
  );

  void seriesOptions;

  const reload = useCallback(async (sid?: string) => {
    const [vols, chars] = await Promise.all([
      fetchNovels(sid),
      fetchCharacters(sid ? { series_id: sid } : {}),
    ]);
    setNovels(vols);
    setCharacters(chars);
    if (!sid && !seriesId) {
      const first = vols[0]?.series_id || chars[0]?.series_id || chars[0]?.source || '';
      if (first) setSeriesId(first);
    }
  }, [seriesId]);

  useEffect(() => {
    const ac = new AbortController();
    reload(undefined)
      .catch((err) => {
        if (!ac.signal.aborted) setError(toUserErrorMessage(err, '加载失败：请检查后端服务'));
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    fetchOrphanDocIds(ac.signal).then(setOrphanDocIds).catch(() => {});
    return () => ac.abort();
  }, [reload]);

  useEffect(() => {
    if (!seriesId) return;
    const ac = new AbortController();
    fetchCharacters({ series_id: seriesId, signal: ac.signal })
      .then(setCharacters)
      .catch(() => {});
    fetchMergeSuggestions(seriesId, 0.92, ac.signal)
      .then((res) => setMergeSuggestions(res.suggestions || []))
      .catch(() => {});
    return () => ac.abort();
  }, [seriesId]);

  const runBuildJobs = async (targets: string[], resolve?: Record<string, string>) => {
    if (!seriesId) return;
    const { jobs } = await buildCharacters({
      series_id: seriesId,
      names: targets,
      doc_id: docId || undefined,
      force: false,
      wait: false,
      resolve,
    });
    const map: Record<string, string> = {};
    jobs.forEach((j) => { map[j.input_name] = j.state; });
    setJobStates((prev) => ({ ...prev, ...map }));

    const token = ++pollRef.current;
    const signal = beginJobAbort();
    const poll = characterBuildPollOptions(jobs.length);
    const finals = await Promise.all(
      jobs.map(async (j) => {
        if (token !== pollRef.current) return j;
        if (j.state === 'done' || j.state === 'failed') return j;
        return pollJob({
          fetchJob: () => fetchCharacterJob(j.job_id),
          signal,
          intervalMs: poll.intervalMs,
          maxTries: poll.maxTries,
          timeoutMessage: poll.timeoutMessage,
          onProgress: (_p, state) => {
            if (token === pollRef.current) setJobStates((prev) => ({ ...prev, [j.input_name]: state }));
          },
        });
      }),
    );

    const needs: { inputName: string; candidates: DisambiguationCandidate[] }[] = [];
    finals.forEach((j) => {
      if (!j) return;
      const req = extractDisambiguation(j);
      if (req) {
        needs.push(req);
        setJobStates((prev) => ({ ...prev, [j.input_name]: 'need_disambiguate' }));
      }
    });
    if (needs.length > 0) {
      setDisambiguationQueue((prev) => [...prev, ...needs]);
      setToast(`「${needs[0].inputName}」存在多个可能角色，请选择后再继续生成`);
    }
  };

  const handleBuild = async (names: string[], resolve?: Record<string, string>) => {
    if (!seriesId || names.length === 0) return;
    setBuilding(true);
    setError(null);
    try {
      await runBuildJobs(names, resolve);
      await reload(seriesId);
    } catch (err) {
      setError(toUserErrorMessage(err, '角色卡生成失败'));
    } finally {
      setBuilding(false);
    }
  };

  const handleBuildName = async (name: string) => {
    // 若存在待消歧队列，携带 resolve 重跑（由 CharactersPanel 歧义选择触发）
    const head = disambiguationQueue[0];
    let resolve: Record<string, string> | undefined;
    if (head && head.inputName === name) {
      resolve = { [name]: '' };
      setDisambiguationQueue((prev) => prev.slice(1));
    }
    await handleBuild([name], resolve);
  };

  const handleMergeDone = async () => {
    // 合并逻辑在 CharactersPanel 内直接调 mergeCharacters；此处仅刷新建议
    setMerging(true);
    try {
      await reload(seriesId);
      const res = await fetchMergeSuggestions(seriesId, 0.92);
      setMergeSuggestions(res.suggestions || []);
    } finally {
      setMerging(false);
    }
  };

  void handleMergeDone;

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 4000);
  }, []);

  const tabs = [
    { key: 'books' as TabKey, label: '书目', count: novels.length },
    { key: 'characters' as TabKey, label: '角色', count: characters.length },
    { key: 'graph' as TabKey, label: '关系图谱' },
    { key: 'roster' as TabKey, label: '别名名录' },
  ];

  return (
    <div className="flex flex-col h-full relative overflow-hidden">
      <div className="pointer-events-none absolute -top-32 -right-32 w-96 h-96 rounded-full bg-brand/10 blur-3xl" />

      {/* Header */}
      <header className="relative z-10 flex items-center gap-3 px-6 py-3.5 border-b border-line bg-surface/60 backdrop-blur-xl shrink-0 flex-wrap">
        <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-brand to-accent flex items-center justify-center shadow-sm shadow-brand/30">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
        </div>
        <h2 className="text-sm font-semibold">知识库</h2>
        <Tabs items={tabs} active={tab} onChange={(k) => setTab(k as TabKey)} className="ml-2" />
        {loading && <span className="text-xs text-faint ml-auto">加载中…</span>}
      </header>

      {/* Toast / Error */}
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

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto px-6 py-5 relative z-0">
        <div className="max-w-4xl mx-auto">
          {tab === 'books' && (
            <BooksPanel
              novels={novels}
              seriesId={seriesId}
              docId={docId}
              onChanged={() => void reload(seriesId)}
              onSelectSeries={(sid) => setSeriesId(sid)}
              onSelectDoc={(did) => setDocId(did)}
              onMessage={showToast}
              onError={setError}
            />
          )}
          {tab === 'characters' && (
            <CharactersPanel
              seriesId={seriesId}
              characters={characters}
              jobStates={jobStates}
              building={building}
              merging={merging}
              mergeSuggestions={mergeSuggestions}
              disambiguation={disambiguationQueue[0] || null}
              onBuild={(names) => void handleBuild(names)}
              onBuildName={(name) => void handleBuildName(name)}
              onDisambiguate={(name, characterId) => {
                setDisambiguationQueue((prev) => prev.slice(1));
                void handleBuild([name], { [name]: characterId });
              }}
              onAcceptSuggestion={(s) => void handleBuild(s.names)}
              onRefresh={() => void reload(seriesId)}
              onError={setError}
              onMessage={showToast}
            />
          )}
          {tab === 'graph' && (
            <div className="card p-5">
              <div className="text-sm font-semibold text-ink mb-1">关系图谱</div>
              <div className="text-xs text-muted mb-4">基于剧情分析的关系/事件数据构建的角色关系网络</div>
              {seriesId ? <RelationshipGraph seriesId={seriesId} docId={docId || undefined} /> : <div className="text-xs text-faint py-10 text-center">请先在「书目」选择系列</div>}
            </div>
          )}
          {tab === 'roster' && (
            <RosterPanel seriesId={seriesId} onError={setError} onMessage={showToast} />
          )}
          {orphanDocIds.length > 0 && (
            <div className="mt-4 rounded-xl border border-warn/25 bg-warn/8 px-4 py-2.5 text-xs text-warn">
              存在 {orphanDocIds.length} 个孤儿卷（向量库有数据但书目未收录）：{orphanDocIds.slice(0, 3).join('、')}
              {orphanDocIds.length > 3 ? ' 等' : ''}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
