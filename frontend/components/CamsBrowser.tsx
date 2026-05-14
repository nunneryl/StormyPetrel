'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
// IMPORTANT: this is a client component, so it must NOT import from
// '@/lib/cams' — that file pulls in the Supabase client and would
// drag the server SDK into the browser bundle. Import pure types +
// helpers from '@/lib/cam-utils' instead.
import type { Cam } from '@/lib/cam-utils';
import {
  camWatchUrl,
  isCamLive,
  providerLabel,
  youtubeChannelUrl,
} from '@/lib/cam-utils';
import { StarRating } from './StarRating';

const PROVIDER_BG: Record<string, string> = {
  youtube:  'bg-red-100 text-red-700',
  surfchex: 'bg-cyan-100 text-cyan-700',
  explore:  'bg-emerald-100 text-emerald-700',
  hdontap:  'bg-violet-100 text-violet-700',
  nysea:    'bg-amber-100 text-amber-700',
  webcam:   'bg-slate-100 text-slate-700',
};

export type CamSpot = {
  slug: string;
  name: string;
  state: string | null;
  stars: number | null;
  face_ft: number | null;
};

export type CamRow = {
  cam: Cam;
  spot: CamSpot | null;
};

// Sort: best conditions first, then alphabetical by spot name. Cams
// without a star reading (e.g. spot was deleted but cam row lingers)
// sink to the bottom so we don't push known-good feeds down.
function sortRows(a: CamRow, b: CamRow): number {
  const sa = a.spot?.stars ?? -1;
  const sb = b.spot?.stars ?? -1;
  if (sa !== sb) return sb - sa;
  const na = a.spot?.name ?? a.cam.cam_name;
  const nb = b.spot?.name ?? b.cam.cam_name;
  return na.localeCompare(nb);
}

export function CamsBrowser({ rows, totalCount }: { rows: CamRow[]; totalCount: number }) {
  const [selectedState, setSelectedState] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  // States with at least one cam, with their counts. Alphabetical so
  // the pill row is scannable.
  const stateCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of rows) {
      const st = r.spot?.state;
      if (!st) continue;
      counts.set(st, (counts.get(st) ?? 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) =>
      a[0].localeCompare(b[0]),
    );
  }, [rows]);

  // Filter (state) + search (spot name OR cam name, case-insensitive
  // substring match). Recompute sort whenever the underlying list
  // changes so the order is stable.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (selectedState && r.spot?.state !== selectedState) return false;
      if (q) {
        const sn = (r.spot?.name ?? '').toLowerCase();
        const cn = r.cam.cam_name.toLowerCase();
        if (!sn.includes(q) && !cn.includes(q)) return false;
      }
      return true;
    });
    return [...out].sort(sortRows);
  }, [rows, selectedState, query]);

  const isFiltered = selectedState !== null || query.trim().length > 0;

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <FilterPill
          active={selectedState === null}
          onClick={() => setSelectedState(null)}
          label="All"
          count={rows.length}
        />
        {stateCounts.map(([state, n]) => (
          <FilterPill
            key={state}
            active={selectedState === state}
            onClick={() => setSelectedState(state)}
            label={state}
            count={n}
          />
        ))}
      </div>

      <div className="relative">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by spot or cam name…"
          className="w-full rounded-xl border border-ink-600 bg-white px-4 py-2.5 pl-9 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-cyan-500 transition"
        />
        <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
      </div>

      <div className="text-xs text-text-secondary tabular-nums">
        {isFiltered
          ? `Showing ${filtered.length} of ${totalCount} cams`
          : `${totalCount} live feeds`}
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-xl border border-ink-600 bg-white p-8 text-center text-text-muted text-sm">
          No cams match your filter.
        </div>
      ) : (
        // Single grid with mixed heights — embed cards are taller
        // because of the thumbnail; align-items:start prevents the
        // shorter link cards from stretching to fill the row.
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 items-start">
          {filtered.map((r) =>
            r.cam.display_mode === 'embed' ? (
              <EmbedCard key={r.cam.id} row={r} />
            ) : (
              <LinkCard key={r.cam.id} row={r} />
            ),
          )}
        </div>
      )}
    </>
  );
}

function EmbedCard({ row }: { row: CamRow }) {
  const { cam, spot } = row;
  const providerCls = PROVIDER_BG[cam.provider] ?? 'bg-ink-800 text-text-secondary';
  const live = isCamLive(cam);
  const channelUrl = youtubeChannelUrl(cam);
  const thumb =
    cam.provider === 'youtube' && cam.resolved_video_id
      ? `https://img.youtube.com/vi/${cam.resolved_video_id}/hqdefault.jpg`
      : null;
  return (
    <Link
      href={spot ? `/spot/${spot.slug}` : '/cams'}
      className="group rounded-xl border border-ink-600 bg-white shadow-card hover:border-cyan-500 transition overflow-hidden flex flex-col"
    >
      <div className="relative w-full bg-ink-900" style={{ paddingTop: '56.25%' }}>
        {thumb ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={thumb}
              alt={cam.cam_name}
              className="absolute inset-0 w-full h-full object-cover"
              loading="lazy"
            />
            {!live && (
              // Dim the stale thumbnail when the stream has gone
              // dark since the last resolver tick so it doesn't
              // misrepresent live conditions.
              <div className="absolute inset-0 bg-black/55" aria-hidden />
            )}
          </>
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-text-muted">
            <CameraIcon size={32} />
          </div>
        )}
        {!live && (
          <span className="absolute top-2 left-2 inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 bg-white/90 text-text-secondary shadow-card">
            Currently offline
          </span>
        )}
      </div>
      <div className="p-4 space-y-2 grow">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="font-bold text-text-primary group-hover:text-cyan-600 truncate">
              {spot?.name ?? cam.spot_slug}
            </div>
            <div className="text-xs text-text-secondary truncate">{cam.cam_name}</div>
          </div>
          <span
            className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 ${providerCls}`}
          >
            {providerLabel(cam.provider)}
          </span>
        </div>
        <RatingFaceRow stars={spot?.stars ?? null} faceFt={spot?.face_ft ?? null} />
        <span className="block text-xs font-bold text-cyan-600 group-hover:underline">
          Watch live →
        </span>
      </div>
    </Link>
  );
}

function LinkCard({ row }: { row: CamRow }) {
  const { cam, spot } = row;
  const providerCls = PROVIDER_BG[cam.provider] ?? 'bg-ink-800 text-text-secondary';
  const watchUrl = camWatchUrl(cam);
  return (
    <article className="rounded-xl border border-ink-600 bg-white shadow-card hover:border-cyan-500 transition p-3.5 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <Link
          href={spot ? `/spot/${spot.slug}` : '/cams'}
          className="font-bold text-text-primary hover:text-cyan-600 truncate min-w-0"
        >
          {spot?.name ?? cam.spot_slug}
        </Link>
        <span
          className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 ${providerCls}`}
        >
          {providerLabel(cam.provider)}
        </span>
      </div>
      <div className="text-xs text-text-secondary truncate">{cam.cam_name}</div>
      <RatingFaceRow stars={spot?.stars ?? null} faceFt={spot?.face_ft ?? null} />
      {watchUrl && (
        <a
          href={watchUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block text-xs font-bold text-cyan-600 hover:underline"
        >
          Watch live →
        </a>
      )}
    </article>
  );
}

function RatingFaceRow({
  stars,
  faceFt,
}: {
  stars: number | null;
  faceFt: number | null;
}) {
  if (stars === null && faceFt === null) return null;
  return (
    <div className="flex items-center gap-2 text-xs">
      {stars !== null && <StarRating score={stars} size="xs" />}
      {faceFt !== null && (
        <span className="font-bold text-text-primary tabular-nums">
          {faceFt.toFixed(1)}ft
        </span>
      )}
    </div>
  );
}

function FilterPill({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-bold transition ${
        active
          ? 'bg-cyan-500 text-white'
          : 'bg-ink-800 text-text-secondary hover:text-text-primary hover:bg-ink-700'
      }`}
    >
      {label}
      <span
        className={`tabular-nums ${
          active ? 'text-white/70' : 'text-text-muted'
        }`}
      >
        {count}
      </span>
    </button>
  );
}

function SearchIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <circle cx="11" cy="11" r="7" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function CameraIcon({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M23 7l-7 5 7 5V7z" />
      <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
    </svg>
  );
}
