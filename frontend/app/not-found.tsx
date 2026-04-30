import Link from 'next/link';
import { WaveGlyph } from '@/components/Logo';

export default function NotFound() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <div className="flex justify-center mb-4">
        <WaveGlyph className="text-cyan-500" size={48} />
      </div>
      <h1 className="text-4xl font-bold tracking-tightish text-text-primary">
        Not found
      </h1>
      <p className="mt-2 text-text-secondary">
        That spot, region, or post isn&apos;t in our database.
      </p>
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
