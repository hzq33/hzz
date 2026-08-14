import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react';

import { cx } from './cx';

export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label className={cx('field', className)}>
      <span className="input-label mb-0">{label}</span>
      {children}
      {hint ? <span className="text-[10px] text-faint">{hint}</span> : null}
    </label>
  );
}

export function TextInput({
  label,
  hint,
  className,
  ...rest
}: { label?: string; hint?: string } & InputHTMLAttributes<HTMLInputElement>) {
  const input = <input className={cx('input text-xs', className)} {...rest} />;
  if (!label) return input;
  return (
    <Field label={label} hint={hint}>
      {input}
    </Field>
  );
}

export function SelectInput({
  label,
  hint,
  className,
  children,
  ...rest
}: { label?: string; hint?: string; children: ReactNode } & SelectHTMLAttributes<HTMLSelectElement>) {
  const select = (
    <select className={cx('input text-xs', className)} {...rest}>
      {children}
    </select>
  );
  if (!label) return select;
  return (
    <Field label={label} hint={hint}>
      {select}
    </Field>
  );
}

export function TextareaInput({
  label,
  hint,
  className,
  ...rest
}: { label?: string; hint?: string } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const area = <textarea className={cx('input text-xs min-h-[88px] resize-y', className)} {...rest} />;
  if (!label) return area;
  return (
    <Field label={label} hint={hint}>
      {area}
    </Field>
  );
}

export function SettingRow({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="setting-row">
      <span>{label}</span>
      <div className="flex items-center gap-2 shrink-0">{children}</div>
    </div>
  );
}
