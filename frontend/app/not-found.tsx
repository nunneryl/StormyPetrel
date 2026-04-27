import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <h1 className="text-4xl font-bold text-white">Not found</h1>
      <p className="mt-2 text-slate-400">
        That spot or region isn&apos;t in our database.
      </p>
      <div className="mt-6 flex items-center justify-center gap-3 text-sm">
        <Link href="/" className="text-sea-400 hover:underline">
          Home
        </Link>
        <Link href="/map" className="text-sea-400 hover:underline">
          Map
        </Link>
        <Link href="/regions" className="text-sea-400 hover:underline">
          Regions
        </Link>
      </div>
    </div>
  );
}
