import { useState } from 'react';

import { cx } from '@/components/ui/cx';
import type { ImpersonationSessionSummary } from '@/types';

export type RailSession = Pick<
  ImpersonationSessionSummary,
  'session_id' | 'title' | 'preview' | 'updated_at'
> & { active?: boolean };

function formatWhen(value?: string | null): string {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function SessionRail({
  sessions,
  activeId,
  onNew,
  onSelect,
  onRename,
  onDelete,
}: {
  sessions: RailSession[];
  activeId?: string | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename?: (id: string, title: string) => void;
  onDelete?: (id: string) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  return (
    <aside className="session-rail">
      <button type="button" className="session-rail-new" onClick={onNew}>
        + 新对话
      </button>
      <div className="flex-1 overflow-y-auto py-1">
        {sessions.length === 0 ? (
          <p className="px-3 py-6 text-center text-[11px] text-faint">还没有会话</p>
        ) : (
          sessions.map((s) => (
            <div
              key={s.session_id}
              className={cx('session-rail-item group', (s.active || s.session_id === activeId) && 'is-active')}
            >
              {editing === s.session_id ? (
                <input
                  autoFocus
                  className="input text-[11px] py-1"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={() => {
                    const title = draft.trim();
                    if (title && onRename) onRename(s.session_id, title);
                    setEditing(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                    if (e.key === 'Escape') setEditing(null);
                  }}
                />
              ) : (
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => onSelect(s.session_id)}
                  onDoubleClick={() => {
                    if (!onRename) return;
                    setEditing(s.session_id);
                    setDraft(s.title);
                  }}
                >
                  <div className="session-rail-title">{s.title || '未命名'}</div>
                  <div className="flex items-center justify-between gap-1">
                    <span className="truncate text-[10px] text-faint">{s.preview || ''}</span>
                    <span className="shrink-0 text-[10px] text-faint">{formatWhen(s.updated_at)}</span>
                  </div>
                </button>
              )}
              {onDelete ? (
                <button
                  type="button"
                  className="opacity-0 group-hover:opacity-100 text-faint hover:text-danger px-1"
                  title="删除"
                  onClick={() => onDelete(s.session_id)}
                >
                  ×
                </button>
              ) : null}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
