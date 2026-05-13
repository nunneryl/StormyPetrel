// Server-only cam queries. The Supabase client this imports throws
// if its env vars aren't set, so this file must never be pulled into
// a client bundle. Pure types + UI helpers live in lib/cam-utils.ts
// (which doesn't touch Supabase) and are re-exported below for
// convenience to callers that already import from '@/lib/cams'.
//
// NB: client components must import types/helpers from
// '@/lib/cam-utils' directly — re-exports here STILL trigger the
// supabase module-init when bundled.

import { supabase } from './supabase';
import { CAM_SELECT, type Cam } from './cam-utils';

export type {
  Cam,
  CamProvider,
  CamStatus,
  CamDisplayMode,
} from './cam-utils';
export { providerLabel, camWatchUrl, solarTimesUTC, camDarkness } from './cam-utils';

/** Active cams for one spot, oldest first. */
export async function fetchCamsForSpot(spotSlug: string): Promise<Cam[]> {
  const { data, error } = await supabase
    .from('cams')
    .select(CAM_SELECT)
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
    .select(CAM_SELECT)
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
