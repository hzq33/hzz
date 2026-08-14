import { IconSearch } from './icons';

export function SearchField({
  value,
  onChange,
  placeholder = '搜索…',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="relative">
      <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint">
        {IconSearch}
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="h-8 w-full rounded-lg border border-line/60 bg-surface-2/70 pl-8 pr-3 text-xs text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none focus:ring-1 focus:ring-brand/20"
      />
    </div>
  );
}
