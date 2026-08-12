/**
 * Aurora UI 基础组件（新设计系统）。
 * 语义色全部走 CSS 变量（深/浅自适应），不写死颜色值。
 */
import type { ReactNode, ButtonHTMLAttributes } from 'react';

/* ── Spinner ── */

export function Spinner({ size = 16, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/* ── Badge ── */

export type BadgeTone = 'brand' | 'ok' | 'warn' | 'danger' | 'accent' | 'neutral';

const badgeToneClass: Record<BadgeTone, string> = {
  brand: 'chip-brand',
  ok: 'chip-ok',
  warn: 'chip-warn',
  danger: 'chip-danger',
  accent: 'chip-accent',
  neutral: 'chip',
};

export function Badge({ tone = 'neutral', children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={badgeToneClass[tone]}>{children}</span>;
}

/* ── Empty State ── */

export function Empty({
  icon = '📭',
  title,
  desc,
  action,
}: {
  icon?: string;
  title: string;
  desc?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center animate-fade-in">
      <div className="text-4xl opacity-70">{icon}</div>
      <div className="text-sm font-medium text-ink">{title}</div>
      {desc ? <div className="text-xs text-muted max-w-sm leading-relaxed">{desc}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/* ── Skeleton ── */

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} />;
}

export function SkeletonList({ rows = 4, height = 'h-14' }: { rows?: number; height?: string }) {
  return (
    <div className="flex flex-col gap-2.5 animate-fade-in">
      {Array.from({ length: rows }).map((_, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <Skeleton key={`sk-${i}`} className={`${height} w-full`} />
      ))}
    </div>
  );
}

/* ── Section Card ── */

export function SectionCard({
  title,
  desc,
  icon,
  actions,
  children,
  className = '',
}: {
  title?: string;
  desc?: string;
  icon?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card p-5 animate-slide-up ${className}`}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 mb-4">
          <div className="flex items-center gap-2.5">
            {icon ? <span className="text-brand">{icon}</span> : null}
            <div>
              {title ? <h3 className="text-sm font-semibold text-ink">{title}</h3> : null}
              {desc ? <p className="text-xs text-muted mt-0.5">{desc}</p> : null}
            </div>
          </div>
          {actions ? <div className="flex items-center gap-2 shrink-0">{actions}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

/* ── Stat Card ── */

export function StatCard({
  label,
  value,
  tone = 'brand',
  icon,
}: {
  label: string;
  value: string | number;
  tone?: BadgeTone;
  icon?: ReactNode;
}) {
  const toneText: Record<BadgeTone, string> = {
    brand: 'text-brand',
    ok: 'text-ok',
    warn: 'text-warn',
    danger: 'text-danger',
    accent: 'text-accent',
    neutral: 'text-ink',
  };
  return (
    <div className="card p-4 flex flex-col gap-1.5 card-hover">
      <div className="flex items-center gap-1.5 text-xs text-muted">
        {icon}
        <span>{label}</span>
      </div>
      <div className={`text-2xl font-bold tabular-nums ${toneText[tone]}`}>{value}</div>
    </div>
  );
}

/* ── Tabs ── */

export interface TabItem {
  key: string;
  label: ReactNode;
  count?: number;
}

export function Tabs({
  items,
  active,
  onChange,
  className = '',
}: {
  items: TabItem[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}) {
  return (
    <div className={`flex items-center gap-1 p-1 rounded-xl bg-surface-2 border border-line w-fit ${className}`}>
      {items.map((t) => (
        <button
          key={`tab-${t.key}`}
          type="button"
          onClick={() => onChange(t.key)}
          className={active === t.key ? 'tab-active' : 'tab'}
        >
          {t.label}
          {t.count != null ? (
            <span className="ml-1.5 text-[10px] px-1.5 py-0.5 rounded-full bg-brand/15 text-brand-strong dark:text-brand">
              {t.count}
            </span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

/* ── Modal ── */

export function Modal({
  open,
  onClose,
  title,
  children,
  width = 'max-w-lg',
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  width?: string;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-sm animate-fade-in"
      onClick={onClose}
    >
      <div
        className={`card w-full ${width} p-5 max-h-[85vh] overflow-y-auto animate-scale-in shadow-elevated`}
        onClick={(e) => e.stopPropagation()}
      >
        {title ? (
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-base font-semibold text-ink">{title}</h3>
            <button type="button" onClick={onClose} className="btn-ghost btn-sm" aria-label="关闭">
              ✕
            </button>
          </div>
        ) : null}
        {children}
      </div>
    </div>
  );
}

/* ── Confirm ── */

export function ConfirmDialog({
  open,
  title,
  message,
  confirmText = '确认',
  danger,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal open={open} onClose={onCancel} title={title} width="max-w-sm">
      <p className="text-sm text-muted leading-relaxed">{message}</p>
      <div className="flex justify-end gap-2 mt-5">
        <button type="button" className="btn-ghost" onClick={onCancel}>
          取消
        </button>
        <button type="button" className={danger ? 'btn-danger' : 'btn-primary'} onClick={onConfirm}>
          {confirmText}
        </button>
      </div>
    </Modal>
  );
}

/* ── Progress ── */

export function ProgressBar({
  pct,
  tone = 'brand',
  label,
}: {
  pct: number;
  tone?: 'brand' | 'ok' | 'warn';
  label?: string;
}) {
  const toneBg: Record<string, string> = {
    brand: 'bg-brand',
    ok: 'bg-ok',
    warn: 'bg-warn',
  };
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex-1 h-1.5 rounded-full bg-surface-3 overflow-hidden">
        <div
          className={`h-full rounded-full ${toneBg[tone]} transition-all duration-500`}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
        />
      </div>
      {label ? <span className="text-xs text-muted tabular-nums w-10 text-right">{label}</span> : null}
    </div>
  );
}

/* ── IconButton ── */

export function IconButton({
  children,
  label,
  ...rest
}: { children: ReactNode; label: string } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className="p-1.5 rounded-lg text-muted hover:text-ink hover:bg-surface-3 transition-colors"
      {...rest}
    >
      {children}
    </button>
  );
}
