import type { ButtonHTMLAttributes, ReactNode } from 'react';

import { cx } from './cx';

const TONE = {
  primary: 'btn-primary',
  soft: 'btn-soft',
  ghost: 'btn-ghost',
  outline: 'btn-outline',
  danger: 'btn-danger',
} as const;

const SIZE = {
  sm: 'btn-sm',
  md: '',
  lg: 'btn-lg',
} as const;

export function Button({
  tone = 'primary',
  size = 'md',
  className,
  children,
  ...rest
}: {
  tone?: keyof typeof TONE;
  size?: keyof typeof SIZE;
  children: ReactNode;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className={cx(TONE[tone], SIZE[size], className)} {...rest}>
      {children}
    </button>
  );
}
