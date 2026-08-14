import type { MouseEvent } from 'react';

import { Avatar } from './Avatar';
import { cx } from './cx';

export type ContactStatus = 'online' | 'in-chat' | 'offline';

const STATUS_DOT: Record<ContactStatus, string> = {
  online: 'bg-green-500',
  'in-chat': 'bg-blue-500',
  offline: 'bg-gray-400',
};

// eslint-disable-next-line react-refresh/only-export-components
export function formatContactTime(ts: number): string {
  if (!ts) return '';
  const d = new Date(ts);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function ContactRow({
  name,
  preview,
  time,
  unread = 0,
  status = 'online',
  delayMs = 0,
  onClick,
  onContextMenu,
}: {
  name: string;
  preview?: string;
  time?: number;
  unread?: number;
  status?: ContactStatus;
  delayMs?: number;
  onClick: () => void;
  onContextMenu?: (e: MouseEvent) => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onContextMenu={onContextMenu}
      className="group flex w-full items-center gap-2.5 border-l-2 border-transparent px-3 py-2 animate-slide-in-left transition-all duration-200 hover:bg-surface-2/80 hover:border-brand/40 active:scale-[0.98]"
      style={{ animationDelay: `${delayMs}ms` }}
    >
      <div className="relative shrink-0">
        <Avatar name={name} size="lg" />
        <span
          className={cx(
            'absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-surface group-hover:border-surface-2',
            STATUS_DOT[status],
          )}
        />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1.5">
          <span className="truncate text-sm font-medium text-ink">{name}</span>
          {time ? (
            <span className="shrink-0 text-[10px] tabular-nums text-faint">
              {formatContactTime(time)}
            </span>
          ) : null}
        </div>
        <div className="mt-0.5 flex items-center justify-between gap-1.5">
          <span className="truncate text-xs text-muted">{preview || '\u00A0'}</span>
          {unread > 0 ? (
            <span className="flex h-4 min-w-[16px] shrink-0 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-semibold tabular-nums text-white">
              {unread > 99 ? '99+' : unread}
            </span>
          ) : null}
        </div>
      </div>
    </button>
  );
}
