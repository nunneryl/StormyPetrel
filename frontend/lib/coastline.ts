/**
 * The land around a spot for the swell compass: one SVG path from frontend/data/coastlines.json
 * (pipeline/coastline_paths.py: GSHHG within 25 km, radius 100 units, north up, keyed by slug).
 *
 * SERVER ONLY. The file holds every spot's path (about 0.9 MB); only the spot page (a server
 * component) imports this, and it hands the one path down as a string. lib/swellWindowCompass.test.mts
 * fails if a 'use client' module imports it.
 */
import coastlines from '../data/coastlines.json';

type Entry = { lat: number; lng: number; d: string };
const SPOTS = (coastlines as { spots: Record<string, Entry> }).spots;

/** The spot's land path, or null when the file has none for it. */
export function coastlinePath(slug: string): string | null {
  const e = SPOTS[slug];
  return e && e.d ? e.d : null;
}
