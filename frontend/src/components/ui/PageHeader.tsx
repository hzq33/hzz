import type { ReactNode } from 'react';

export function PageHeader({
  icon,
  title,
  subtitle,
  extra,
  actions,
}: {
  icon?: ReactNode;
  title: string;
  subtitle?: string;
  extra?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="relative z-10 flex items-center gap-3 px-6 py-3.5 border-b border-line bg-surface/60 backdrop-blur-xl shrink-0">
      {icon ? <div className="shrink-0">{icon}</div> : null}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2.5 min-w-0">
          <h2 className="text-sm font-semibold truncate">{title}</h2>
          {extra}
        </div>
        {subtitle ? <p className="text-[11px] text-faint truncate">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-1.5 shrink-0">{actions}</div> : null}
    </header>
  );
}
