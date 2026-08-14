import { useCallback, useEffect, useRef } from 'react';

import { cx } from '@/components/ui/cx';
import { IconPaperclip, IconSend, IconStop } from '@/components/ui/icons';

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  loading,
  disabled,
  placeholder = '输入消息…（Enter 发送，Shift+Enter 换行）',
  allowAttach = false,
  attachments = [],
  onAttachmentsChange,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  onStop: () => void;
  loading: boolean;
  disabled?: boolean;
  placeholder?: string;
  allowAttach?: boolean;
  attachments?: File[];
  onAttachmentsChange?: (files: File[]) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    ref.current?.focus();
  }, []);

  const resize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  useEffect(() => {
    resize();
  }, [value, resize]);

  const canSend = (value.trim().length > 0 || attachments.length > 0) && !loading && !disabled;
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && canSend) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div className="space-y-1.5">
      {attachments.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 px-1">
          {attachments.map((f, i) => (
            <span key={`${f.name}-${f.lastModified}-${f.size}`} className="cite-chip">
              {f.name}
              <button
                type="button"
                className="text-faint hover:text-danger"
                onClick={() => onAttachmentsChange?.(attachments.filter((_, idx) => idx !== i))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="relative flex items-end gap-2 bg-surface rounded-2xl border border-line p-2 shadow-card transition-all duration-200 focus-within:border-brand/50 focus-within:shadow-glow">
        {allowAttach ? (
          <>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                const next = Array.from(e.target.files || []);
                if (next.length) onAttachmentsChange?.([...attachments, ...next].slice(0, 8));
                e.target.value = '';
              }}
            />
            <button
              type="button"
              title="添加附件（文件名会随消息发送；入库请走知识库）"
              className="shrink-0 w-9 h-9 rounded-xl flex items-center justify-center text-muted hover:bg-surface-3 hover:text-ink"
              onClick={() => fileRef.current?.click()}
              disabled={loading || disabled}
            >
              {IconPaperclip}
            </button>
          </>
        ) : null}
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            resize();
          }}
          onKeyDown={onKey}
          placeholder={disabled ? '正在加载…' : placeholder}
          disabled={loading || disabled}
          rows={1}
          className="flex-1 bg-transparent border-none outline-none text-sm text-ink placeholder-faint resize-none py-2 px-2 min-h-[20px] max-h-[160px] disabled:opacity-60"
        />
        <button
          type="button"
          onClick={loading ? onStop : onSend}
          disabled={!loading && !canSend}
          title={loading ? '停止生成' : '发送'}
          className={cx(
            'shrink-0 w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-200',
            loading
              ? 'bg-surface-3 text-ink hover:bg-surface-3/80 active:scale-95'
              : canSend
                ? 'bg-gradient-to-br from-brand to-accent text-white hover:shadow-glow-sm active:scale-95'
                : 'bg-surface-3 text-faint cursor-not-allowed',
          )}
        >
          {loading ? IconStop : IconSend}
        </button>
      </div>
    </div>
  );
}
