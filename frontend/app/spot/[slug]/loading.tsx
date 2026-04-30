import { SkeletonLine, SkeletonBlock } from '@/components/Skeleton';

export default function SpotLoading() {
  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-5 sm:py-7 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-2 min-w-0 flex-1">
          <SkeletonLine width="80px" height={10} />
          <SkeletonLine width="240px" height={32} />
          <SkeletonLine width="180px" height={14} />
        </div>
        <SkeletonLine width="120px" height={36} className="rounded-md" />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <SkeletonBlock key={i} height={92} className="rounded-xl" />
        ))}
      </div>

      <SkeletonBlock height={200} className="rounded-xl" />

      <div>
        <SkeletonLine width="120px" height={12} className="mb-2" />
        <SkeletonBlock height={420} className="rounded-xl" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <SkeletonBlock height={220} className="rounded-xl" />
        <SkeletonBlock height={220} className="rounded-xl" />
        <SkeletonBlock height={220} className="rounded-xl" />
      </div>
    </div>
  );
}
