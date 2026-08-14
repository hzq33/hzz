import { cx } from './cx';

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        'relative h-5 w-9 shrink-0 rounded-full border transition-colors',
        checked ? 'bg-brand border-brand' : 'bg-surface-3 border-line',
        disabled && 'opacity-40 cursor-not-allowed',
      )}
    >
      <span
        className={cx(
          'absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white transition-transform',
          checked ? 'left-4' : 'left-0.5',
        )}
      />
    </button>
  );
}
