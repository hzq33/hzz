/**
 * 页面级骨架屏
 * 路由懒加载时使用，消除 Suspense 等待期的白屏
 */
interface PageSkeletonProps {
  /** 行数（默认 6） */
  rows?: number;
  /** 是否包含头部 */
  withHeader?: boolean;
}

export function PageSkeleton({ rows = 6, withHeader = true }: PageSkeletonProps) {
  return (
    <div className="flex flex-1 flex-col">
      {withHeader && (
        <div className="page-header">
          <div className="skeleton h-4 w-24 rounded" />
        </div>
      )}
      <div className="page-body space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div
            key={`row-${i}`} // eslint-disable-line react/no-array-index-key -- 骨架屏为静态装饰元素，索引作为 key 安全
            className="skeleton h-12 rounded-xl"
            style={{ width: `${90 - i * 8}%`, animationDelay: `${i * 80}ms` }}
          />
        ))}
      </div>
    </div>
  );
}
