import type { ReactNode } from 'react';

import { useTheme } from '@/hooks/useTheme';

import { cx } from './cx';
import { IconClose, IconMinimize, IconMoon, IconSun } from './icons';

export function WinButton({
  label,
  onClick,
  danger,
  children,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className={danger ? 'win-btn-close' : 'win-btn'}
    >
      {children}
    </button>
  );
}

export function WindowControls({
  showTheme = false,
}: {
  showTheme?: boolean;
}) {
  const theme = useTheme();
  return (
    <div className="flex items-center gap-1 no-drag">
      {showTheme ? (
        <WinButton
          label={theme.resolved === 'dark' ? '切换到浅色' : '切换到深色'}
          onClick={theme.toggle}
        >
          {theme.resolved === 'dark' ? IconSun : IconMoon}
        </WinButton>
      ) : null}
      <WinButton label="最小化" onClick={() => window.aurora?.minimizeWindow()}>
        {IconMinimize}
      </WinButton>
      <WinButton danger label="关闭" onClick={() => window.aurora?.closeWindow()}>
        {IconClose}
      </WinButton>
    </div>
  );
}

export function Titlebar({
  icon,
  title,
  subtitle,
  meta,
  trailing,
  showTheme,
}: {
  icon?: ReactNode;
  title: string;
  subtitle?: string;
  /** Status capsule, like Cyrene's chat__title-meta */
  meta?: ReactNode;
  trailing?: ReactNode;
  showTheme?: boolean;
}) {
  return (
    <header className="titlebar">
      {icon ? <div className="shrink-0">{icon}</div> : null}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-semibold text-ink truncate">{title}</span>
          {meta ? <div className="no-drag shrink-0">{meta}</div> : null}
        </div>
        {subtitle ? <p className="text-[10px] leading-tight text-faint truncate">{subtitle}</p> : null}
      </div>
      {trailing ? <div className="no-drag flex items-center gap-1">{trailing}</div> : null}
      <WindowControls showTheme={showTheme} />
    </header>
  );
}

export function StatusMeta({
  online,
  label,
}: {
  online: boolean;
  label: string;
}) {
  return (
    <span className="title-meta">
      <span className={cx('h-1.5 w-1.5 rounded-full', online ? 'bg-ok' : 'bg-warn animate-pulse-dot')} />
      {label}
    </span>
  );
}

export function WindowShell({
  variant = 'feature',
  children,
}: {
  variant?: 'panel' | 'chat' | 'feature';
  children: ReactNode;
}) {
  const bg =
    variant === 'panel' ? 'bg-aurora-panel' : variant === 'chat' ? 'bg-aurora-chat' : 'bg-aurora';
  return (
    <div
      className={cx(
        'relative flex h-screen w-full flex-col overflow-hidden',
        variant === 'panel' && 'select-none',
        bg,
      )}
    >
      {children}
    </div>
  );
}
