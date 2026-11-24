import Link from 'next/link';
import { WaveGlyph } from '@/components/Logo';
import { NavSearch } from '@/components/NavSearch';

export default function NotFound() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <div className="flex justify-center mb-4">
        <WaveGlyph className="text-cyan-500" size={48} />
      </div>
      <h1 className="text-4xl font-bold tracking-tightish text-text-primary">
        Page not found
      </h1>
      <p className="mt-2 text-text-secondary">
        We couldn&apos;t find that page. If you were looking for a spot, search for it:
      </p>
      {/* The nav's search, on the shared list (components/SpotSearchList): a 404 is usually a spot
          URL that changed or was mistyped. */}
      <div className="mt-5 mx-auto max-w-md text-left" data-404-search>
        <NavSearch light />
      </div>
      <div className="mt-6 flex items-center justify-center gap-4 text-sm">
        <Link href="/" className="text-cyan-400 hover:underline">
          Home
        </Link>
        <Link href="/map" className="text-cyan-400 hover:underline">
          Map
        </Link>
        <Link href="/regions" className="text-cyan-400 hover:underline">
          Regions
        </Link>
        <Link href="/blog" className="text-cyan-400 hover:underline">
          Blog
        </Link>
      </div>
    </div>
  );
}
