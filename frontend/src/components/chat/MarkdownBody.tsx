import ReactMarkdown from 'react-markdown';
import rehypeSanitize from 'rehype-sanitize';

const mdComponents = {
  pre: ({ children }: { children?: React.ReactNode }) => (
    <pre className="bg-surface-3 text-ink rounded-xl p-3.5 my-2 overflow-x-auto text-xs leading-relaxed border border-line font-mono">
      {children}
    </pre>
  ),
  code: ({ className, children }: { className?: string; children?: React.ReactNode }) => {
    if (!className)
      return (
        <code className="bg-brand/10 text-brand-strong dark:text-brand px-1.5 py-0.5 rounded-md text-[0.8em] font-mono">
          {children}
        </code>
      );
    return <code className="text-xs font-mono">{children}</code>;
  },
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-brand underline decoration-brand/40 hover:decoration-brand transition-colors"
    >
      {children}
    </a>
  ),
  ul: ({ children }: { children?: React.ReactNode }) => (
    <ul className="list-disc list-outside ml-5 my-2 space-y-1">{children}</ul>
  ),
  ol: ({ children }: { children?: React.ReactNode }) => (
    <ol className="list-decimal list-outside ml-5 my-2 space-y-1">{children}</ol>
  ),
};

export function MarkdownBody({ children }: { children: string }) {
  return (
    <ReactMarkdown rehypePlugins={[rehypeSanitize]} components={mdComponents}>
      {children}
    </ReactMarkdown>
  );
}
