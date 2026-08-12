import { type ReactNode } from 'react';

export function LoadingState({ rows = 4 }: { rows?: number }) {
  return (
    <div className="flex-1 flex items-center justify-center p-8">
      <div className="space-y-3 w-full max-w-xs">
        {Array.from({ length: rows }).map((_, i) => (
          <div
            key={`skel-${i}`} // eslint-disable-line react/no-array-index-key -- 骨架屏为静态装饰元素，索引作为 key 安全
            className="skeleton h-4"
            style={{ width: `${85 - i * 12}%`, animationDelay: `${i * 100}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex-1 flex items-center justify-center p-6 animate-fade-in">
      <div className="text-center space-y-4 max-w-xs">
        <div className="w-14 h-14 rounded-2xl bg-red-100 flex items-center justify-center mx-auto">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-red-500">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        </div>
        <p className="text-sm text-slate-600">{message}</p>
        {onRetry && (
          <button onClick={onRetry} className="btn-ghost text-xs">
            重试
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="py-16 text-center space-y-3 animate-fade-in">
      {icon ? (
        <div className="text-5xl mb-2">{icon}</div>
      ) : (
        <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mx-auto mb-2">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" className="text-slate-300">
            <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
            <polyline points="13 2 13 9 20 9" />
          </svg>
        </div>
      )}
      <p className="text-base font-medium text-slate-500">{title}</p>
      {description && <p className="text-sm text-slate-400 max-w-xs mx-auto">{description}</p>}
      {action && <div className="pt-2">{action}</div>}
    </div>
  );
}

export function FileUploadButton({
  accept = '.epub,.txt,.md',
  label = '上传文件',
  onUpload,
  uploading = false,
}: {
  accept?: string;
  label?: string;
  onUpload: (file: File) => Promise<void>;
  uploading?: boolean;
}) {
  return (
    <label
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border cursor-pointer
        transition-all duration-200 font-medium
        ${uploading
          ? 'bg-slate-100 text-slate-400 border-slate-200 cursor-wait'
          : 'bg-brand-50 text-brand-600 border-brand-200 hover:bg-brand-100 hover:border-brand-300'
        }`}
    >
      {uploading ? (
        <>
          <span className="w-3 h-3 border-2 border-slate-300 border-t-brand-500 rounded-full animate-spin" />
          导入中...
        </>
      ) : (
        <>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="17 8 12 3 7 8" />
            <line x1="12" y1="3" x2="12" y2="15" />
          </svg>
          {label}
        </>
      )}
      <input
        type="file"
        accept={accept}
        className="hidden"
        disabled={uploading}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) {
            void onUpload(file).then(() => {
              e.target.value = '';
            });
          }
        }}
      />
    </label>
  );
}
