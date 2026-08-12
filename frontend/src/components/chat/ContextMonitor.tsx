/**
 * 上下文窗口监控条：显示当前会话记忆用量（tokens 估算 / 上限）、已压缩轮数、
 * 压缩摘要预览。数据来自扮演响应的 memory_stats。
 */
import { useState } from 'react';

import type { MemoryStats } from '@/types';

function pct(stats: MemoryStats | null): number {
  if (!stats) return 0;
  const max = stats.max_tokens ?? 0;
  const used = stats.tokens_est ?? 0;
  if (!max || max <= 0) return 0;
  return Math.min(100, Math.round((used / max) * 100));
}

export default function ContextMonitor({
  stats,
  maxHistoryTokens,
}: {
  stats: MemoryStats | null;
  maxHistoryTokens: number | null;
}) {
  const [open, setOpen] = useState(false);

  const used = stats?.tokens_est ?? 0;
  const max = stats?.max_tokens ?? maxHistoryTokens ?? 0;
  const percent = pct(stats);
  const summarized = stats?.summarized_turns ?? 0;
  const excerpt = stats?.summary_excerpt?.trim();

  const tone =
    percent >= 95 ? 'bg-red-500' : percent >= 80 ? 'bg-amber-500' : 'bg-brand';

  if (!max) return null;

  return (
    <div className="rounded-xl border border-line bg-surface-2 px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[11px] text-muted">
          <span className="font-semibold text-ink">上下文窗口</span>
          <span className="tabular-nums">
            {used.toLocaleString()} / {max.toLocaleString()} tokens
          </span>
          {summarized > 0 && (
            <span className="rounded-full bg-brand/10 px-2 py-0.5 text-brand">
              已压缩 {summarized} 轮
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-32 overflow-hidden rounded-full bg-line">
            <div
              className={`h-full rounded-full transition-all ${tone}`}
              style={{ width: `${Math.max(2, percent)}%` }}
            />
          </div>
          <span className="w-8 text-right text-[11px] tabular-nums text-muted">
            {percent}%
          </span>
          {excerpt ? (
            <button
              onClick={() => setOpen((v) => !v)}
              className="text-[11px] text-brand hover:underline"
            >
              {open ? '收起摘要' : '查看摘要'}
            </button>
          ) : null}
        </div>
      </div>
      {open && excerpt ? (
        <div className="mt-2 border-t border-line pt-2 text-[11px] leading-relaxed text-muted">
          <div className="mb-1 font-semibold text-ink">更早对话摘要（已确认事实）</div>
          <pre className="whitespace-pre-wrap font-sans">{excerpt}</pre>
        </div>
      ) : null}
    </div>
  );
}
