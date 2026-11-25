import { SkeletonLine, SkeletonBlock } from '@/components/Skeleton';

export default function HomeLoading() {
  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6">
      <section className="pt-12 sm:pt-16 pb-10 flex flex-col items-center text-center gap-5">
        <SkeletonLine width="60%" height={16} />
        <SkeletonLine width="100%" height={64} className="rounded-xl" />
        <div className="flex gap-2">
          <SkeletonLine width="80px" height={32} className="rounded-full" />
          <SkeletonLine width="60px" height={32} className="rounded-full" />
          <SkeletonLine width="100px" height={32} className="rounded-full" />
          <SkeletonLine width="80px" height={32} className="rounded-full" />
        </div>
      </section>
      <SkeletonLine width="220px" height={12} className="mb-3" />
      <SkeletonBlock height={500} className="rounded-xl mb-10" />
      <SkeletonLine width="160px" height={12} className="mb-3" />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2.5">
        {Array.from({ length: 12 }).map((_, i) => (
          <SkeletonBlock key={i} height={64} className="rounded-xl" />
        ))}
      </div>
    </div>
  );
}
