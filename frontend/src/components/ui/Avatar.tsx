import { cx } from './cx';

const AVATAR_COLORS = [
  'from-indigo-500 to-purple-500',
  'from-blue-500 to-cyan-500',
  'from-green-500 to-teal-500',
  'from-orange-500 to-red-500',
  'from-pink-500 to-rose-500',
  'from-violet-500 to-fuchsia-500',
  'from-amber-500 to-orange-500',
  'from-emerald-500 to-green-500',
];

// eslint-disable-next-line react-refresh/only-export-components
export function avatarGradient(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash << 5) - hash + name.charCodeAt(i);
    hash |= 0;
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

const SIZE_CLASS = {
  sm: 'h-5 w-5 text-[10px] rounded-md',
  md: 'h-8 w-8 text-sm rounded-full',
  lg: 'h-9 w-9 text-sm rounded-full',
  xl: 'h-20 w-20 text-3xl rounded-3xl',
} as const;

export function Avatar({
  name,
  size = 'md',
  className,
}: {
  name: string;
  size?: keyof typeof SIZE_CLASS;
  className?: string;
}) {
  return (
    <div
      className={cx(
        'flex shrink-0 items-center justify-center bg-gradient-to-br font-semibold text-white shadow-sm',
        SIZE_CLASS[size],
        avatarGradient(name || '?'),
        className,
      )}
    >
      {(name || '?').charAt(0)}
    </div>
  );
}
