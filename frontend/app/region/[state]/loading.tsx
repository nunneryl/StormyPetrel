import { SkeletonLine, SkeletonCard } from '@/components/Skeleton';

export default function RegionLoading() {
  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-7 space-y-6">
      <div className="space-y-2">
        <SkeletonLine width="80px" height={10} />
        <SkeletonLine width="220px" height={32} />
        <SkeletonLine width="160px" height={14} />
      </div>
      <div className="flex items-center gap-2">
        <SkeletonLine width="80px" height={28} className="rounded-full" />
        <SkeletonLine width="80px" height={28} className="rounded-full" />
        <SkeletonLine width="80px" height={28} className="rounded-full" />
      </div>
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    </div>
  );
}
