/**
 * The /map basemap: which raster tiles Leaflet loads, and the attribution they require.
 *
 * WHY THIS EXISTS. CARTO stopped serving its raster basemaps without an API key: a keyless
 * request to basemaps.cartocdn.com now returns a tile watermarked "API KEY REQUIRED", which is
 * what /map showed. A key is free (non-commercial up to 5M tile requests a month, commercial up
 * to 1M; carto.com/basemaps/apikey) and goes on the tile URL as `?key=`.
 *
 * THE KEY IS PUBLIC BY DESIGN. It is read from NEXT_PUBLIC_CARTO_BASEMAPS_KEY, which Next
 * inlines into the browser bundle at BUILD time (set it, then redeploy). Every tile request
 * carries it, so restrict it to the site's domains in the CARTO dashboard.
 *
 * WITHOUT A KEY THE MAP STILL WORKS. It falls back to OpenStreetMap's own standard tiles, which
 * need no key. Their tile usage policy allows a website's normal map traffic with visible
 * attribution, and forbids bulk or heavy use; it is the fallback, not the plan. The look
 * changes (OSM's colour style instead of CARTO's light grey), the markers do not.
 */

export type Basemap = {
  url: string;
  options: { attribution: string; maxZoom: number; subdomains?: string };
};

/** OpenStreetMap's data licence (ODbL) requires this credit on any map built from it. */
const OSM_CREDIT = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

export function basemap(cartoKey: string | undefined | null): Basemap {
  const key = (cartoKey ?? '').trim();
  if (key) {
    return {
      // {s} rotates over CARTO's a-d hosts; {r} is Leaflet's "@2x" on a retina screen.
      url: `https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png?key=${encodeURIComponent(key)}`,
      options: {
        attribution: `${OSM_CREDIT} &copy; <a href="https://carto.com/attributions">CARTO</a>`,
        maxZoom: 19,
        subdomains: 'abcd',
      },
    };
  }
  return {
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    options: { attribution: OSM_CREDIT, maxZoom: 19 },
  };
}
