import { useEffect, useRef, useState, type ReactNode } from 'react';

import { cx } from './cx';

export interface MenuItem {
  id: string;
  label: string;
  danger?: boolean;
  onClick: () => void;
}

export function Dropdown({
  align = 'right',
  items,
  children,
}: {
  align?: 'left' | 'right';
  items: MenuItem[];
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  return (
    <div ref={ref} className="relative no-drag">
      <div onClick={() => setOpen((v) => !v)}>{children}</div>
      {open ? (
        <div className={cx('menu-panel absolute mt-1', align === 'left' ? 'left-0' : 'right-0')}>
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={cx('menu-item', item.danger && 'text-danger')}
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="menu-panel fixed z-[80]"
      style={{ left: x, top: y }}
    >
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={cx('menu-item', item.danger && 'text-danger')}
          onClick={() => {
            onClose();
            item.onClick();
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
