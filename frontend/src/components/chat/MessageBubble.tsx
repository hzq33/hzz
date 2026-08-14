import type { ReactNode } from 'react';

import { Avatar } from '@/components/ui/Avatar';
import { cx } from '@/components/ui/cx';

import { MarkdownBody } from './MarkdownBody';
import { splitReplySegments } from './splitReply';

export function MessageBubble({
  role,
  name,
  content,
  isStreaming,
  meta,
  split = false,
  markdown = true,
  footer,
  children,
}: {
  role: 'user' | 'assistant';
  name?: string;
  content: string;
  isStreaming?: boolean;
  meta?: string | null;
  split?: boolean;
  markdown?: boolean;
  footer?: ReactNode;
  children?: ReactNode;
}) {
  const isUser = role === 'user';
  const segments =
    !isUser && split && !isStreaming ? splitReplySegments(content) : content ? [content] : [];

  if (isUser) {
    return (
      <div className="flex justify-end gap-3 animate-slide-up">
        <div className="max-w-[78%] bg-gradient-to-br from-brand to-brand-strong text-white rounded-2xl rounded-tr-sm px-4 py-3 shadow-md shadow-brand/20">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">{content}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start gap-3 animate-slide-up">
      {name ? <Avatar name={name} size="sm" className="mt-1" /> : null}
      <div className="max-w-[78%] min-w-0 space-y-2">
        {segments.map((seg, i) => {
          const last = i === segments.length - 1;
          return (
            <div
              key={`${seg.slice(0, 16)}-${seg.length}`}
              className={cx(
                'glass-message rounded-2xl rounded-tl-sm px-4 py-3 text-sm leading-relaxed break-words',
              )}
            >
              {name && i === 0 ? (
                <div className="mb-2 text-xs font-semibold text-accent">{name}</div>
              ) : null}
              {i === 0 ? children : null}
              {seg ? (
                markdown ? (
                  <MarkdownBody>{seg}</MarkdownBody>
                ) : (
                  <p className="whitespace-pre-wrap text-ink">{seg}</p>
                )
              ) : null}
              {isStreaming && last ? (
                <span className="inline-block w-1.5 h-4 ml-0.5 bg-accent/70 animate-pulse align-text-bottom rounded-sm" />
              ) : null}
              {last && footer}
              {last && meta && !isStreaming ? (
                <div className="mt-1.5 text-[10px] text-faint">{meta}</div>
              ) : null}
            </div>
          );
        })}
        {segments.length === 0 ? (
          <div className="glass-message rounded-2xl rounded-tl-sm px-4 py-3">
            {children}
            {isStreaming ? (
              <span className="inline-block w-1.5 h-4 bg-accent/70 animate-pulse rounded-sm" />
            ) : null}
            {footer}
          </div>
        ) : null}
      </div>
    </div>
  );
}
