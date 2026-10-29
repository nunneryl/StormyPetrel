import { fmtClock } from './spotTime.ts';

// Pure types + helpers shared between server queries and client UI.
// Deliberately does NOT import the Supabase client — keeping this
// file Supabase-free is what lets <CamsBrowser> and other client
// components depend on it without dragging the server SDK (and its
// missing-env throw) into the browser bundle.

export type CamProvider =
  | 'youtube'
  | 'surfchex'
  | 'explore'
  | 'hdontap'
  | 'nysea'
  | 'webcam';
export type CamStatus = 'active' | 'offline' | 'pending';
export type CamDisplayMode = 'embed' | 'link';

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
  display_mode: CamDisplayMode;
  status: CamStatus;
  last_resolved_at: string | null;
  last_checked_at: string | null;
};

export const CAM_SELECT =
  'id, spot_slug, cam_name, provider, channel_id, iframe_url, resolved_video_id, embed_url, attribution, attribution_url, display_mode, status, last_resolved_at, last_checked_at';

const PROVIDER_LABEL: Record<CamProvider, string> = {
  youtube:  'YouTube',
  surfchex: 'SurfChex',
  explore:  'Explore.org',
  hdontap:  'HDOnTap',
  nysea:    'Skudin Surf',
  webcam:   'Live Cam',
};
export function providerLabel(p: CamProvider): string {
  return PROVIDER_LABEL[p] ?? p;
}

/** External URL to point a "Watch live on X" link at. For surfchex
 *  the iframe_url IS the cam's first-party page (better target than
 *  the generic surfchex.com root in attribution_url); everyone else
 *  uses attribution_url directly. */
export function camWatchUrl(cam: Cam): string | null {
  if (cam.provider === 'surfchex' && cam.iframe_url) return cam.iframe_url;
  return cam.attribution_url ?? cam.iframe_url ?? null;
}

/** Direct URL to the cam's source YouTube channel, used as the
 *  fallback link when a YouTube stream is currently offline. */
export function youtubeChannelUrl(cam: Cam): string | null {
  if (cam.provider !== 'youtube' || !cam.channel_id) return null;
  return `https://www.youtube.com/channel/${cam.channel_id}`;
}

/**
 * Is this cam currently broadcasting? YouTube cams are gated on
 * `resolved_video_id` (the resolver nulls it out when no live stream
 * is found), so a row stays in the registry as status='active' even
 * while the channel is dark. Non-YouTube cams use the original
 * status + embed_url check.
 */
export function isCamLive(cam: Cam): boolean {
  if (cam.provider === 'youtube') {
    return Boolean(cam.resolved_video_id);
  }
  if (cam.display_mode === 'link') {
    // Link-mode rows don't iframe anything — being "active" is the
    // signal that the external page is worth pointing visitors at.
    return cam.status === 'active';
  }
  return cam.status === 'active' && Boolean(cam.embed_url);
}

/**
 * Sunrise and sunset (UTC) on *date*'s UTC day, from NOAA's general solar position formulas:
 * the declination and the equation of time from the fractional year, and the sun's centre
 * 0.833 degrees below the horizon (refraction and the sun's radius). Good to a few minutes at
 * these latitudes. The approximation this replaced (2026-10-29) left out the equation of time
 * and refraction and put late-October sunrises about 20 minutes late. Returns hours past UTC
 * midnight; null at the polar circles where the sun doesn't rise/set.
 */
export function solarTimesUTC(
  date: Date,
  lat: number,
  lng: number,
): { sunriseUtcH: number | null; sunsetUtcH: number | null } {
  const start = Date.UTC(date.getUTCFullYear(), 0, 1);
  const dayOfYear = Math.floor((date.getTime() - start) / 86_400_000) + 1;
  const g = ((2 * Math.PI) / 365) * (dayOfYear - 1);          // the fractional year at noon
  const eqTimeMin =
    229.18 *
    (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) -
      0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const decl =
    0.006918 - 0.399912 * Math.cos(g) + 0.070257 * Math.sin(g) -
    0.006758 * Math.cos(2 * g) + 0.000907 * Math.sin(2 * g) -
    0.002697 * Math.cos(3 * g) + 0.00148 * Math.sin(3 * g);
  const latRad = (lat * Math.PI) / 180;
  const cosH =
    Math.cos((90.833 * Math.PI) / 180) / (Math.cos(latRad) * Math.cos(decl)) -
    Math.tan(latRad) * Math.tan(decl);
  if (cosH > 1 || cosH < -1) {
    return { sunriseUtcH: null, sunsetUtcH: null };
  }
  const haDeg = (Math.acos(cosH) * 180) / Math.PI;
  return {
    sunriseUtcH: (720 - 4 * (lng + haDeg) - eqTimeMin) / 60,
    sunsetUtcH: (720 - 4 * (lng - haDeg) - eqTimeMin) / 60,
  };
}

const DAY_MS = 86_400_000;

/** Decide whether to show the "cam may be dark — sunrise at X" hint,
 *  and produce the matching label. Computed server-side so the page
 *  doesn't need a hydration shim.
 *
 *  DARK means outside every daylight span from yesterday's, today's and tomorrow's sunrise
 *  to the same day's sunset (UTC days). Comparing the UTC hour with one UTC day's sunrise and
 *  sunset, as this did until 2026-10-29, called a Hawaii or West Coast cam dark through the
 *  late afternoon: there the sunset falls on the next UTC day. The sunrise is shown on the
 *  spot's own clock (*timeZone*, lib/spotTime.ts), not a longitude estimate, which was over
 *  an hour off at Jacksonville and ignored daylight saving. */
export function camDarkness(
  lat: number | null,
  lng: number | null,
  timeZone: string,
  now: Date = new Date(),
): { isDark: boolean; sunriseLabel: string | null } {
  if (lat === null || lng === null) {
    return { isDark: false, sunriseLabel: null };
  }
  const base = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const t = now.getTime();
  const spans: { rise: number; set: number }[] = [];
  for (const k of [-1, 0, 1, 2]) {
    const day = base + k * DAY_MS;
    const { sunriseUtcH, sunsetUtcH } = solarTimesUTC(new Date(day), lat, lng);
    if (sunriseUtcH === null || sunsetUtcH === null) {
      return { isDark: false, sunriseLabel: null };
    }
    spans.push({ rise: day + sunriseUtcH * 3_600_000, set: day + sunsetUtcH * 3_600_000 });
  }
  if (spans.some((s) => s.rise <= t && t <= s.set)) {
    return { isDark: false, sunriseLabel: null };
  }
  const next = Math.min(...spans.map((s) => s.rise).filter((r) => r > t));
  return {
    isDark: true,
    sunriseLabel: fmtClock(new Date(next).toISOString(), timeZone),
  };
}
