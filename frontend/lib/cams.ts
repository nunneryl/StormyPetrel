import { supabase } from './supabase';

export type CamProvider = 'youtube' | 'surfchex' | 'explore';
export type CamStatus = 'active' | 'offline' | 'pending';

export type Cam = {
  id: number;
  spot_slug: string | null;
  cam_name: string;
  provider: CamProvider;
  channel_id: string | null;
  iframe_url: string | null;
  resolved_video_id: string | null;
  embed_url: string | null;
  attribution: string | null;
  attribution_url: string | null;
  status: CamStatus;
  last_resolved_at: string | null;
  last_checked_at: string | null;
};

const _SELECT =
  'id, spot_slug, cam_name, provider, channel_id, iframe_url, resolved_video_id, embed_url, attribution, attribution_url, status, last_resolved_at, last_checked_at';

/** Active cams for one spot, oldest first. The spot page renders the
 *  first one; we filter on status='active' here so pending/offline
 *  rows don't trigger an "offline" pane on a spot whose cam hasn't
 *  been verified yet — same gate the badge + /cams page use. */
export async function fetchCamsForSpot(spotSlug: string): Promise<Cam[]> {
  const { data, error } = await supabase
    .from('cams')
    .select(_SELECT)
    .eq('spot_slug', spotSlug)
    .eq('status', 'active')
    .order('id');
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchCamsForSpot', error);
    return [];
  }
  return (data ?? []) as Cam[];
}

/** All active cams across the site, used for /cams and the cam-badge
 *  join on listings. */
export async function fetchAllActiveCams(): Promise<Cam[]> {
  const { data, error } = await supabase
    .from('cams')
    .select(_SELECT)
    .eq('status', 'active')
    .order('spot_slug');
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchAllActiveCams', error);
    return [];
  }
  return (data ?? []) as Cam[];
}

/** Lightweight (spot_slug, status) set used to badge spot listings —
 *  saves dragging the whole iframe/embed payload across the boundary
 *  just to know "does this spot have a cam?". */
export async function fetchCamSlugSet(): Promise<Set<string>> {
  const { data, error } = await supabase
    .from('cams')
    .select('spot_slug')
    .eq('status', 'active')
    .not('spot_slug', 'is', null);
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchCamSlugSet', error);
    return new Set();
  }
  const out = new Set<string>();
  for (const row of (data ?? []) as { spot_slug: string | null }[]) {
    if (row.spot_slug) out.add(row.spot_slug);
  }
  return out;
}

const PROVIDER_LABEL: Record<CamProvider, string> = {
  youtube:  'YouTube',
  surfchex: 'SurfChex',
  explore:  'Explore.org',
};
export function providerLabel(p: CamProvider): string {
  return PROVIDER_LABEL[p] ?? p;
}

/**
 * Rough sunrise / sunset (UTC) using a NOAA-style approximation. Good
 * to ~5 minutes at temperate latitudes, which is plenty for "cam may
 * be dark" messaging. Returns hours past UTC midnight; null at the
 * polar circles where the sun doesn't rise/set.
 */
export function solarTimesUTC(
  date: Date,
  lat: number,
  lng: number,
): { sunriseUtcH: number | null; sunsetUtcH: number | null } {
  const start = Date.UTC(date.getUTCFullYear(), 0, 0);
  const dayOfYear = Math.floor((date.getTime() - start) / 86_400_000);
  const decl =
    23.45 * Math.sin((2 * Math.PI * (dayOfYear + 284)) / 365);
  const latRad = (lat * Math.PI) / 180;
  const declRad = (decl * Math.PI) / 180;
  const cosH = -Math.tan(latRad) * Math.tan(declRad);
  if (cosH > 1 || cosH < -1) {
    return { sunriseUtcH: null, sunsetUtcH: null };
  }
  const hourAngleH = (Math.acos(cosH) * 180) / Math.PI / 15;
  const solarNoonUtcH = 12 - lng / 15;
  return {
    sunriseUtcH: solarNoonUtcH - hourAngleH,
    sunsetUtcH: solarNoonUtcH + hourAngleH,
  };
}

/** Format an "hours past UTC midnight" value as a local clock string
 *  ("6:42am") at the spot's longitude. Approximate — uses 15°/hour as
 *  the timezone offset, which is fine for "sunrise at ~X" messaging. */
function fmtLocalClock(utcHours: number, lng: number): string {
  let h = utcHours + lng / 15;
  h = ((h % 24) + 24) % 24;
  const hh = Math.floor(h);
  const mm = Math.round((h - hh) * 60);
  const period = hh < 12 ? 'am' : 'pm';
  const display = hh % 12 === 0 ? 12 : hh % 12;
  const pad = mm.toString().padStart(2, '0');
  return `${display}:${pad}${period}`;
}

/** Decide whether to show the "cam may be dark — sunrise at X" hint,
 *  and produce the matching label. Computed server-side so the page
 *  doesn't need a hydration shim. */
export function camDarkness(
  lat: number | null,
  lng: number | null,
  now: Date = new Date(),
): { isDark: boolean; sunriseLabel: string | null } {
  if (lat === null || lng === null) {
    return { isDark: false, sunriseLabel: null };
  }
  const { sunriseUtcH, sunsetUtcH } = solarTimesUTC(now, lat, lng);
  if (sunriseUtcH === null || sunsetUtcH === null) {
    return { isDark: false, sunriseLabel: null };
  }
  const nowUtcH = now.getUTCHours() + now.getUTCMinutes() / 60;
  const isDark = nowUtcH < sunriseUtcH || nowUtcH > sunsetUtcH;
  if (!isDark) return { isDark: false, sunriseLabel: null };
  // If we're past sunset today, the next sunrise is on the next UTC day.
  const nextSunriseUtcH =
    nowUtcH > sunsetUtcH ? sunriseUtcH + 24 : sunriseUtcH;
  return {
    isDark: true,
    sunriseLabel: fmtLocalClock(nextSunriseUtcH, lng),
  };
}
