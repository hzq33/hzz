import { useState, type ReactNode } from 'react';

export function ReasoningFold({
  title = '思考过程',
  defaultOpen = false,
  children,
}: {
  title?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-[11px] text-muted hover:text-ink flex items-center gap-1.5"
      >
        <span className={`transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
        {title}
        <span className="text-faint">{open ? '收起' : '展开'}</span>
      </button>
      {open ? <div className="mt-1.5 text-xs text-muted leading-relaxed">{children}</div> : null}
    </div>
  );
}
