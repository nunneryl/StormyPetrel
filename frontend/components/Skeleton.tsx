// Reusable loading skeleton blocks. Animation is in globals.css
// (.skeleton class). Use these in route loading.tsx files so a
// route transition shows the page silhouette before the data arrives.

export function SkeletonLine({
  width = '100%',
  height = 12,
  className = '',
}: {
  width?: string;
  height?: number;
  className?: string;
}) {
  return <div className={`skeleton ${className}`} style={{ width, height }} />;
}

export function SkeletonBlock({
  height = 80,
  className = '',
}: {
  height?: number;
  className?: string;
}) {
  return <div className={`skeleton ${className}`} style={{ height, width: '100%' }} />;
}

export function SkeletonCard({ className = '' }: { className?: string }) {
  return (
    <div className={`rounded-xl border border-ink-600 bg-ink-800/60 p-3 ${className}`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0 space-y-1.5">
          <SkeletonLine width="60%" height={14} />
          <SkeletonLine width="40%" height={10} />
        </div>
        <SkeletonLine width="56px" height={18} className="shrink-0" />
      </div>
      <div className="flex items-center gap-3 mt-3">
        <SkeletonLine width="48px" height={16} />
        <SkeletonLine width="32px" height={12} />
        <SkeletonLine width="32px" height={12} />
      </div>
    </div>
  );
}
