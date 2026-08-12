/**
 * 角色管线面板：搜索 / 多选 / 建卡（含作业轮询与歧义消解）/ 异写合并 / 人设卡展示编辑。
 */
import { useState } from 'react';

import { updateCharacter, deleteCharacter, mergeCharacters } from '@/api/characters';
import { Badge, Empty, Modal, Spinner } from '@/components/ui/aura';
import type { CharacterInfo, DisambiguationCandidate } from '@/types';

/* ── 状态徽章 ── */

function statusLabel(c: CharacterInfo, jobState?: string): string {
  if (jobState === 'running' || jobState === 'queued' || jobState === 'processing') return '生成中…';
  if (jobState === 'failed') return '生成失败';
  if (jobState === 'need_disambiguate') return '待消歧';
  if (c.has_card) return c.status || '已建卡';
  return '未建卡';
}

function statusTone(c: CharacterInfo, jobState?: string): 'brand' | 'ok' | 'warn' | 'danger' | 'neutral' {
  if (jobState === 'failed') return 'danger';
  if (jobState === 'running' || jobState === 'queued') return 'warn';
  if (c.has_card) return 'ok';
  return 'neutral';
}

/* ── 人设卡（简化展示 + 编辑） ── */

function CharacterCard({
  character,
  expanded,
  onToggle,
  onUpdated,
  onDeleted,
  busy,
}: {
  character: CharacterInfo;
  expanded: boolean;
  onToggle: () => void;
  onUpdated: () => void;
  onDeleted: () => void;
  busy: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ personality: '', speaking_style: '', background: '' });

  const startEdit = () => {
    setDraft({
      personality: character.personality || '',
      speaking_style: character.speaking_style || '',
      background: character.background || '',
    });
    setEditing(true);
  };

  const save = async () => {
    try {
      await updateCharacter(character.name, {
        personality: draft.personality,
        speaking_style: draft.speaking_style,
        background: draft.background,
      });
      setEditing(false);
      onUpdated();
    } catch {
      /* 错误由上层捕获 */
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface-2 overflow-hidden">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between px-3 py-2 text-xs text-muted hover:text-ink transition-colors"
      >
        <span>人设卡 {expanded ? '▾' : '▸'}</span>
        <span className="flex gap-1.5">
          {!busy && (
            <>
              <button
                type="button"
                className="hover:text-brand"
                onClick={(e) => { e.stopPropagation(); startEdit(); }}
              >
                编辑
              </button>
              <button
                type="button"
                className="hover:text-danger"
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm(`删除角色「${character.name}」的人设卡？`)) {
                    void deleteCharacter(character.name, character.series_id).then(onDeleted);
                  }
                }}
              >
                删除
              </button>
            </>
          )}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-2">
          {editing ? (
            <div className="space-y-2">
              <div>
                <label className="input-label">性格</label>
                <textarea value={draft.personality} onChange={(e) => setDraft({ ...draft, personality: e.target.value })} rows={2} className="input" />
              </div>
              <div>
                <label className="input-label">说话风格</label>
                <textarea value={draft.speaking_style} onChange={(e) => setDraft({ ...draft, speaking_style: e.target.value })} rows={2} className="input" />
              </div>
              <div>
                <label className="input-label">背景</label>
                <textarea value={draft.background} onChange={(e) => setDraft({ ...draft, background: e.target.value })} rows={3} className="input" />
              </div>
              <div className="flex justify-end gap-2">
                <button type="button" className="btn-ghost btn-sm" onClick={() => setEditing(false)}>取消</button>
                <button type="button" className="btn-primary btn-sm" onClick={() => void save()}>保存</button>
              </div>
            </div>
          ) : (
            <>
              {character.personality && <Field label="性格" value={character.personality} />}
              {character.speaking_style && <Field label="说话风格" value={character.speaking_style} />}
              {character.background && <Field label="背景" value={character.background} />}
              {(character.catchphrases || []).length > 0 && (
                <Field label="口头禅" value={(character.catchphrases || []).join(' / ')} />
              )}
              {(character.sample_dialogues || []).length > 0 && (
                <div>
                  <div className="text-[10px] text-faint mb-1">台词样本</div>
                  <div className="space-y-1">
                    {(character.sample_dialogues || []).slice(0, 3).map((d) => (
                      <div key={`sd-${d.slice(0, 10)}`} className="text-xs text-muted bg-surface rounded-lg px-2.5 py-1.5">「{d}」</div>
                    ))}
                  </div>
                </div>
              )}
              {!character.personality && !character.speaking_style && !character.background && (
                <div className="text-xs text-faint">（卡为空，可编辑填写）</div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-faint mb-0.5">{label}</div>
      <div className="text-xs text-ink leading-relaxed">{value}</div>
    </div>
  );
}

/* ── 角色面板 ── */

export default function CharactersPanel({
  seriesId,
  characters,
  jobStates,
  building,
  merging,
  mergeSuggestions,
  disambiguation,
  onBuild,
  onBuildName,
  onDisambiguate,
  onAcceptSuggestion,
  onRefresh,
  onError,
  onMessage,
}: {
  seriesId: string;
  characters: CharacterInfo[];
  jobStates: Record<string, string>;
  building: boolean;
  merging: boolean;
  mergeSuggestions: Array<{ names: string[]; survivor: string; score: number; reason: string }>;
  disambiguation: { inputName: string; candidates: DisambiguationCandidate[] } | null;
  onBuild: (names: string[]) => void;
  onBuildName: (name: string) => void;
  onDisambiguate: (name: string, characterId: string) => void;
  onAcceptSuggestion: (s: { names: string[]; survivor: string; score: number; reason: string }) => void;
  onRefresh: () => void;
  onError: (msg: string) => void;
  onMessage: (msg: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const filtered = characters.filter((c) => {
    if (seriesId && (c.series_id || c.source) !== seriesId) return false;
    if (!query.trim()) return true;
    const q = query.trim();
    return c.name.includes(q) || (c.aliases || []).some((a) => a.includes(q));
  });

  const toggleSelect = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };
  const toggleExpand = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const exactHit = characters.find((c) => c.name === query.trim() || (c.aliases || []).includes(query.trim()));
  const canBuildQuery = !!query.trim() && (!exactHit || !exactHit.has_card);
  const busy = building || merging;

  const handleBuildSelected = () => {
    if (selected.size === 0) return;
    onBuild([...selected]);
    setSelected(new Set());
  };

  const pickSurvivor = (names: string[]) => {
    const ranked = names
      .map((n) => characters.find((c) => c.name === n))
      .filter((c): c is CharacterInfo => Boolean(c))
      .sort(
        (a, b) =>
          (b.mention_count || 0) - (a.mention_count || 0) ||
          (b.dialogue_count || 0) - (a.dialogue_count || 0) ||
          a.name.localeCompare(b.name, 'zh'),
      );
    return ranked[0]?.name || names[0];
  };

  const handleMerge = async (names: string[], survivorHint?: string) => {
    if (!seriesId || names.length < 2) return;
    const survivor = survivorHint || pickSurvivor(names);
    const label = names.filter((n) => n !== survivor).join('、');
    if (!confirm(`将「${label}」合并到「${survivor}」？别名会保留。`)) return;
    try {
      await mergeCharacters({ series_id: seriesId, survivor, names });
      setSelected(new Set());
      onMessage(`已合并为「${survivor}」`);
      onRefresh();
    } catch (err) {
      onError(err instanceof Error ? err.message : '合并失败');
    }
  };

  if (!seriesId) {
    return (
      <Empty icon="🎭" title="先导入或选择系列" desc="上传小说后会生成候选角色名单；可勾选角色生成人设卡。" />
    );
  }

  return (
    <div className="space-y-3">
      {mergeSuggestions.length > 0 && (
        <div className="rounded-xl border border-warn/25 bg-warn/8 px-3.5 py-2.5 space-y-2">
          <div className="text-xs font-medium text-warn">发现 {mergeSuggestions.length} 组疑似中译异写（同一人多名）</div>
          {mergeSuggestions.slice(0, 5).map((s) => (
            <div key={s.names.join('|')} className="flex items-center gap-2 flex-wrap text-xs">
              <span className="flex-1 min-w-0 text-muted">
                {s.names.join(' / ')} <span className="text-warn ml-1">→ {s.survivor}</span>
                <span className="text-faint ml-1">({Math.round(s.score * 100)}%)</span>
              </span>
              <button type="button" className="btn-ghost btn-sm text-warn" disabled={busy} onClick={() => onAcceptSuggestion(s)}>
                合并
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索候选角色 / 别名" className="input text-sm flex-1 min-w-[160px]" />
        {canBuildQuery && (
          <button type="button" className="btn-ghost btn-sm" disabled={busy} onClick={() => onBuildName(exactHit?.name || query.trim())}>
            生成「{exactHit?.name || query.trim()}」
          </button>
        )}
        <button type="button" className="btn-ghost btn-sm" onClick={() => setSelected(new Set(characters.map((c) => c.name)))}>全选</button>
        <button type="button" className="btn-ghost btn-sm" onClick={() => setSelected(new Set())}>清空</button>
        <button type="button" className="btn-ghost btn-sm" onClick={onRefresh}>刷新</button>
        <button
          type="button"
          className="btn-ghost btn-sm text-warn"
          disabled={busy || selected.size < 2}
          onClick={() => void handleMerge([...selected])}
        >
          合并选中 ({selected.size})
        </button>
        <button type="button" className="btn-primary btn-sm" disabled={busy || selected.size === 0} onClick={handleBuildSelected}>
          {building ? <Spinner size={12} className="text-white" /> : null}
          生成选中角色 ({selected.size})
        </button>
      </div>

      {filtered.length === 0 ? (
        <Empty icon="🎭" title="暂无候选角色" desc="请先导入小说。上传会保存候选名单，频次达标者进入 LLM 参考名单。" />
      ) : (
        <div className="space-y-2">
          {filtered.map((c) => (
            <div key={`${c.series_id || c.source}-${c.name}`} className="flex gap-2.5 items-start animate-fade-in">
              <input
                type="checkbox"
                className="mt-4 ml-1.5 accent-[rgb(var(--brand))]"
                checked={selected.has(c.name)}
                onChange={() => toggleSelect(c.name)}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1.5 px-1 flex-wrap">
                  <span className="text-sm font-semibold text-ink mr-0.5">{c.name}</span>
                  <Badge tone={statusTone(c, jobStates[c.name])}>{statusLabel(c, jobStates[c.name])}</Badge>
                  {typeof c.mention_count === 'number' && c.mention_count > 0 && <Badge>提及 {c.mention_count}</Badge>}
                  {c.importance === 'main' && <Badge tone="accent">主角</Badge>}
                  {c.importance === 'extra' && <Badge>路人</Badge>}
                  {c.in_llm_seed && <Badge tone="accent">LLM参考</Badge>}
                  {!c.has_card && (
                    <button type="button" className="text-[10px] text-brand hover:underline" disabled={busy} onClick={() => onBuildName(c.name)}>
                      生成卡
                    </button>
                  )}
                  {(c.aliases || []).slice(0, 3).map((a) => <Badge key={a}>{a}</Badge>)}
                </div>
                <CharacterCard
                  character={c}
                  expanded={expanded.has(c.name)}
                  onToggle={() => toggleExpand(c.name)}
                  onUpdated={onRefresh}
                  onDeleted={onRefresh}
                  busy={busy}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 歧义消解 */}
      <Modal
        open={!!disambiguation}
        onClose={() => undefined}
        title={`「${disambiguation?.inputName || ''}」可能对应多个角色`}
        width="max-w-md"
      >
        <div className="space-y-2">
          <p className="text-xs text-muted">请选择正确的角色以继续生成人设卡：</p>
          {(disambiguation?.candidates || []).map((c) => (
            <button
              key={c.character_id}
              type="button"
              className="w-full text-left rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 hover:border-brand/50 hover:shadow-card-hover transition-all"
              onClick={() => {
                if (!disambiguation) return;
                onDisambiguate(disambiguation.inputName, c.character_id);
              }}
            >
              <div className="text-sm font-medium text-ink">{c.canonical_name}</div>
              {c.dialogue_count != null && <div className="text-[11px] text-muted mt-0.5">台词数 {c.dialogue_count}</div>}
            </button>
          ))}
        </div>
      </Modal>
    </div>
  );
}
