import { SkeletonLine, SkeletonCard, SkeletonBlock } from '@/components/Skeleton';

export default function HomeLoading() {
  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6">
      <section className="py-12 sm:py-20 flex flex-col items-center text-center gap-4">
        <SkeletonLine width="60%" height={48} />
        <SkeletonLine width="40%" height={48} />
        <SkeletonLine width="70%" height={20} />
        <SkeletonLine width="80%" height={56} className="rounded-xl mt-2" />
      </section>
      <SkeletonLine width="180px" height={12} className="mb-3" />
      <div className="flex gap-3 overflow-x-auto -mx-4 px-4 pb-2 mb-8">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="w-64 shrink-0">
            <SkeletonCard />
          </div>
        ))}
      </div>
      <SkeletonLine width="160px" height={12} className="mb-3" />
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {Array.from({ length: 9 }).map((_, i) => (
          <SkeletonBlock key={i} height={88} className="rounded-xl" />
        ))}
      </div>
    </div>
  );
}
