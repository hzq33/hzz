/**
 * 别名名录面板：查看/编辑系列角色名录（规范名、别名、重要度），带乐观锁保存。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchRoster, fetchRosterSeries, updateRoster, type RosterEntity } from '@/api/characters';
import { Badge, Empty, SectionCard } from '@/components/ui/aura';

export default function RosterPanel({
  seriesId,
  onError,
  onMessage,
}: {
  seriesId: string;
  onError: (msg: string) => void;
  onMessage: (msg: string) => void;
}) {
  const [series, setSeries] = useState<string[]>([]);
  const [roster, setRoster] = useState<RosterEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingAlias, setEditingAlias] = useState<{ name: string; index: number } | null>(null);
  const [aliasDraft, setAliasDraft] = useState('');
  const [newAliasName, setNewAliasName] = useState('');
  const seqRef = useRef(0);

  useEffect(() => {
    void fetchRosterSeries()
      .then((d) => setSeries((d.series || []).map((s) => s.series_id)))
      .catch(() => onError('无法加载系列列表'));
  }, [onError]);

  const load = useCallback(async (sid: string) => {
    const seq = ++seqRef.current;
    if (!sid) {
      setRoster([]);
      return;
    }
    setLoading(true);
    try {
      const d = await fetchRoster(sid);
      if (seq !== seqRef.current) return;
      setRoster(d.entities || []);
    } catch {
      if (seq === seqRef.current) onError('无法加载名录');
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    void load(seriesId);
  }, [load, seriesId]);

  const save = async () => {
    if (!seriesId) return;
    setSaving(true);
    try {
      const res = await updateRoster(seriesId, roster);
      onMessage(`名录已保存（更新 ${res.updated} 条）`);
    } catch (err) {
      onError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const patchEntity = (idx: number, patch: Partial<RosterEntity>) => {
    setRoster((prev) => prev.map((e, i) => (i === idx ? { ...e, ...patch } : e)));
  };

  const removeAlias = (idx: number, aliasIdx: number) => {
    patchEntity(idx, { aliases: (roster[idx]?.aliases || []).filter((_, i) => i !== aliasIdx) });
  };

  const addAlias = (idx: number) => {
    const name = newAliasName.trim();
    if (!name) return;
    const cur = roster[idx]?.aliases || [];
    if (!cur.includes(name)) patchEntity(idx, { aliases: [...cur, name] });
    setNewAliasName('');
  };

  return (
    <SectionCard
      title="别名名录"
      desc="规范角色名与别名映射，供实体解析与检索过滤使用"
      actions={
        seriesId ? (
          <button type="button" className="btn-primary btn-sm" disabled={saving} onClick={() => void save()}>
            {saving ? '保存中…' : '保存'}
          </button>
        ) : undefined
      }
    >
      {series.length === 0 ? (
        <Empty icon="🗂️" title="暂无名录数据" desc="导入小说并构建角色名录后此处可用。" />
      ) : seriesId ? (
        <div className="space-y-2.5">
          {loading ? (
            <div className="text-xs text-muted py-8 text-center">加载中…</div>
          ) : roster.length === 0 ? (
            <Empty icon="🗂️" title="该系列暂无名录" />
          ) : (
            roster.map((e, i) => (
              <div key={e.canonical_name} className="rounded-xl border border-line bg-surface-2 p-3.5 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-ink">{e.canonical_name}</span>
                  {e.mention_count != null && <Badge>提及 {e.mention_count}</Badge>}
                  <select
                    value={e.importance || 'supporting'}
                    onChange={(ev) => patchEntity(i, { importance: ev.target.value })}
                    className="bg-surface border border-line rounded-lg px-2 py-0.5 text-xs text-ink focus:outline-none focus:border-brand/50"
                  >
                    <option value="main">主角</option>
                    <option value="supporting">配角</option>
                    <option value="extra">路人</option>
                  </select>
                </div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {(e.aliases || []).map((a, ai) => (
                    <span key={a} className="inline-flex items-center gap-1 chip">
                      {editingAlias?.name === e.canonical_name && editingAlias.index === ai ? (
                        <input
                          autoFocus
                          value={aliasDraft}
                          onChange={(ev) => setAliasDraft(ev.target.value)}
                          onBlur={() => {
                            if (aliasDraft.trim()) patchEntity(i, {
                              aliases: (roster[i]?.aliases || []).map((x, xi) => (xi === ai ? aliasDraft.trim() : x)),
                            });
                            setEditingAlias(null);
                          }}
                          onKeyDown={(ev) => {
                            if (ev.key === 'Enter') {
                              if (aliasDraft.trim()) patchEntity(i, {
                                aliases: (roster[i]?.aliases || []).map((x, xi) => (xi === ai ? aliasDraft.trim() : x)),
                              });
                              setEditingAlias(null);
                            }
                          }}
                          className="w-24 bg-surface border border-line rounded px-1.5 py-0.5 text-xs"
                        />
                      ) : (
                        <button
                          type="button"
                          className="text-faint hover:text-brand"
                          onClick={() => { setEditingAlias({ name: e.canonical_name, index: ai }); setAliasDraft(a); }}
                        >
                          {a}
                        </button>
                      )}
                      <button type="button" className="text-faint hover:text-danger" onClick={() => removeAlias(i, ai)}>×</button>
                    </span>
                  ))}
                  <span className="inline-flex items-center gap-1">
                    <input
                      value={newAliasName}
                      onChange={(ev) => setNewAliasName(ev.target.value)}
                      onKeyDown={(ev) => {
                        if (ev.key === 'Enter') addAlias(i);
                      }}
                      placeholder="+ 别名"
                      className="w-20 bg-surface border border-line rounded-lg px-2 py-1 text-xs placeholder-faint focus:outline-none focus:border-brand/50"
                    />
                    <button type="button" className="text-brand text-xs" onClick={() => addAlias(i)}>添加</button>
                  </span>
                </div>
              </div>
            ))
          )}
        </div>
      ) : (
        <Empty icon="🗂️" title="选择系列" desc="先在书目页选择系列，再查看名录。" />
      )}
    </SectionCard>
  );
}
