/**
 * 默认错误兜底 UI
 * - 严肃但温和的视觉，避免用户恐慌
 * - 提供错误摘要（开发模式展开完整堆栈）
 * - 提供「重试当前操作」与「刷新页面」两个动作
 */
import { useState } from 'react';

import { config } from '@/lib/config';

interface ErrorFallbackProps {
  error: Error;
  onRetry?: () => void;
  /** 是否显示堆栈（默认仅开发模式） */
  showStack?: boolean;
}

export function ErrorFallback({ error, onRetry, showStack }: ErrorFallbackProps) {
  const [expanded, setExpanded] = useState(false);
  const shouldShowStack = showStack ?? config.isDev;

  const handleReload = (): void => {
    if (typeof window !== 'undefined') {
      window.location.reload();
    }
  };

  return (
    <div
      role="alert"
      className="flex min-h-[320px] flex-1 items-center justify-center p-8 animate-fade-in"
    >
      <div className="w-full max-w-md space-y-5 text-center">
        {/* 图标 */}
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-red-50">
          <svg
            width="32"
            height="32"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            className="text-red-500"
          >
            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
        </div>

        {/* 文案 */}
        <div className="space-y-2">
          <h2 className="text-base font-semibold text-slate-700">
            出错了，页面遇到一些问题
          </h2>
          <p className="text-sm text-slate-500">
            不用担心，您可以尝试重试，或刷新页面继续使用。
          </p>
        </div>

        {/* 错误摘要 */}
        <div className="rounded-xl border border-red-100 bg-red-50/50 p-3 text-left">
          <p className="font-mono text-xs text-red-700 break-all">
            {error.name}: {error.message}
          </p>
          {shouldShowStack && error.stack && (
            <>
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="mt-2 text-[10px] text-red-500 hover:text-red-700 underline"
              >
                {expanded ? '收起堆栈' : '展开堆栈'}
              </button>
              {expanded && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-white/60 p-2 font-mono text-[10px] leading-relaxed text-slate-600">
                  {error.stack}
                </pre>
              )}
            </>
          )}
        </div>

        {/* 动作 */}
        <div className="flex items-center justify-center gap-2 pt-2">
          {onRetry && (
            <button type="button" onClick={onRetry} className="btn-ghost text-xs">
              重试
            </button>
          )}
          <button type="button" onClick={handleReload} className="btn-primary text-xs">
            刷新页面
          </button>
        </div>
      </div>
    </div>
  );
}
