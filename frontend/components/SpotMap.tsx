'use client';

import { useEffect, useMemo, useRef } from 'react';
import type { SpotWithLatest } from '@/lib/types';
import { classifyWind, tierFromStars, windQualityLabel } from '@/lib/ratings';
import {
  degToCardinal,
  fmtFt,
  fmtSec,
  msToMph,
  pickSwell,
} from '@/lib/formatting';

const LEAFLET_CSS_ID = 'leaflet-css';
const LEAFLET_CLUSTER_CSS_ID = 'leaflet-markercluster-css';
const LEAFLET_CLUSTER_DEFAULT_CSS_ID = 'leaflet-markercluster-default-css';

function ensureCss(id: string, href: string) {
  if (typeof document === 'undefined' || document.getElementById(id)) return;
  const link = document.createElement('link');
  link.id = id;
  link.rel = 'stylesheet';
  link.href = href;
  link.crossOrigin = '';
  document.head.appendChild(link);
}

export function SpotMap({ spots }: { spots: SpotWithLatest[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<unknown>(null);
  const data = useMemo(() => spots, [spots]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    let cleanup: (() => void) | null = null;
    let cancelled = false;

    (async () => {
      // Step 1 — load Leaflet + CSS. Inject CSS BEFORE creating the map
      // so the tile pane has correct positioning from the first paint.
      //
      // Webpack wraps CJS modules in an ES namespace with the actual
      // exports under `.default`. The leaflet.markercluster plugin
      // mutates whatever `require('leaflet')` returns (the inner CJS
      // exports), which is the SAME object as `.default` on our
      // namespace — but NOT the same as the namespace itself. So we
      // must hold the .default reference, otherwise on a cold first
      // load `L.markerClusterGroup` reads as undefined and we silently
      // fall back to plain markers. On warm reloads webpack's cache
      // happens to surface the patched property on the namespace too,
      // which is why the bug only appeared on the very first visit.
      const leafletNs = await import('leaflet');
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const L: any = (leafletNs as any).default ?? leafletNs;
      ensureCss(LEAFLET_CSS_ID, 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');

      // Step 2 — load the markercluster plugin AND wait for the side
      // effect to land on L before continuing. Wrapped in try/catch
      // so a CDN / network hiccup with the plugin doesn't kill markers.
      let clusterAvailable = false;
      try {
        await import('leaflet.markercluster');
        ensureCss(
          LEAFLET_CLUSTER_CSS_ID,
          'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css',
        );
        ensureCss(
          LEAFLET_CLUSTER_DEFAULT_CSS_ID,
          'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css',
        );
        // Belt-and-suspenders: if for some reason the plugin patched a
        // different L (older webpack interop, transitive dep dedup, etc.)
        // try lifting it off window.L before giving up.
        if (typeof L.markerClusterGroup !== 'function') {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const winL = (typeof window !== 'undefined' ? (window as any).L : null);
          if (winL && typeof winL.markerClusterGroup === 'function') {
            L.markerClusterGroup = winL.markerClusterGroup;
            L.MarkerClusterGroup = winL.MarkerClusterGroup;
          }
        }
        clusterAvailable = typeof L.markerClusterGroup === 'function';
        if (!clusterAvailable) {
          // eslint-disable-next-line no-console
          console.warn('SpotMap: markercluster module loaded but markerClusterGroup is missing on L');
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('SpotMap: markercluster plugin failed to load; rendering plain markers', err);
      }

      if (cancelled || !containerRef.current) return;

      // Default view tuned for the viewport. On a narrow phone the
      // CONUS-only zoom 4 cuts the west coast off the right edge, so
      // we zoom out one step and shift the center slightly north so
      // both coasts plus Hawaii / Puerto Rico fit comfortably.
      const isNarrow =
        typeof window !== 'undefined' && window.innerWidth < 640;
      const map = L.map(containerRef.current, {
        center: isNarrow ? [38, -98] : [37.5, -98],
        zoom: isNarrow ? 3 : 4,
        worldCopyJump: true,
        zoomControl: false,
      });
      L.control.zoom({ position: 'bottomright' }).addTo(map);
      mapRef.current = map;

      L.tileLayer(
        'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        {
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
          maxZoom: 19,
          subdomains: 'abcd',
        },
      ).addTo(map);

      // Cluster group (optional — falls back to plain markers if the
      // plugin didn't load). The cluster's own CSS mistakenly hides
      // children that aren't real Leaflet objects, so we ALWAYS create
      // markers as real L.marker instances and either add to cluster
      // or directly to the map.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const clusterGroup: any = clusterAvailable
        ? L.markerClusterGroup({
            showCoverageOnHover: false,
            // No spider fan-out — clicking a leaf cluster should always
            // just zoom further in, never explode into lines + markers.
            spiderfyOnMaxZoom: false,
            zoomToBoundsOnClick: true,
            // Smaller cluster radius (default 80) so groups break apart
            // sooner as you zoom in. Pair with disableClusteringAtZoom=13
            // so by typical neighborhood-zoom every spot is its own dot.
            maxClusterRadius: 40,
            disableClusteringAtZoom: 13,
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            iconCreateFunction: (cluster: any) => {
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              const children: any[] = cluster.getAllChildMarkers();
              const bestStars = children.reduce(
                (m, c) => Math.max(m, (c.options?.spotStars as number) ?? 0),
                0,
              );
              const tier = tierFromStars(bestStars);
              const count = children.length;
              const size =
                count < 10 ? 32 : count < 50 ? 38 : count < 200 ? 44 : 50;
              const fg =
                tier.label === 'FAIR' || tier.label === 'FAIR TO GOOD'
                  ? '#0F172A'
                  : '#FFFFFF';
              const html = `
                <div style="
                  width:${size}px;height:${size}px;border-radius:50%;
                  background:${tier.hex};
                  color:${fg};
                  display:flex; align-items:center; justify-content:center;
                  font-weight:800; font-size:${size <= 32 ? 12 : 13}px;
                  border: 2px solid #FFFFFF;
                  box-shadow:
                    0 0 0 1px rgba(15,23,42,0.18),
                    0 6px 14px -4px rgba(15,23,42,0.25);
                  font-family: Inter, system-ui, sans-serif;
                  font-variant-numeric: tabular-nums;
                ">${count}</div>
              `;
              return L.divIcon({
                html,
                className: 'sp-cluster',
                iconSize: [size, size],
                iconAnchor: [size / 2, size / 2],
              });
            },
          })
        : null;

      // Per-spot markers. Always real L.marker instances — added either
      // to the cluster (if available) or directly to the map.
      let markersAdded = 0;
      for (const s of data) {
        if (s.lat === null || s.lng === null) continue;
        const tier = tierFromStars(s.latest?.stars ?? 0);
        // 8px filled radius (16px diameter) with a 2px white ring
        // around it for contrast against both light land and water
        // basemaps; box-sizing:border-box keeps the colored fill at
        // exactly 16px regardless of the border.
        const html = `
          <div style="
            width:20px; height:20px; border-radius:50%;
            background:${tier.hex};
            border: 2px solid #FFFFFF;
            box-sizing: border-box;
            box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.35), 0 1px 3px rgba(15, 23, 42, 0.35);
          "></div>
        `;
        const icon = L.divIcon({
          className: '',
          html,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const m = L.marker([s.lat, s.lng], {
          icon,
          spotStars: s.latest?.stars ?? 0,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any);

        m.bindPopup(buildPopupHtml(s), { className: 'sp-popup' });

        if (clusterGroup) {
          clusterGroup.addLayer(m);
        } else {
          m.addTo(map);
        }
        markersAdded += 1;
      }
      if (clusterGroup) {
        clusterGroup.addTo(map);
      }
      // eslint-disable-next-line no-console
      console.info(`SpotMap: rendered ${markersAdded} spots (clustered=${!!clusterGroup})`);

      // Geolocation pan — best-effort, silent on deny / timeout.
      if (typeof navigator !== 'undefined' && navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            if (cancelled) return;
            map.setView([pos.coords.latitude, pos.coords.longitude], 8, {
              animate: true,
            });
          },
          () => undefined,
          { timeout: 4000, maximumAge: 600_000 },
        );
      }

      cleanup = () => {
        map.remove();
        mapRef.current = null;
      };
    })();

    return () => {
      cancelled = true;
      if (cleanup) cleanup();
    };
  }, [data]);

  return <div ref={containerRef} className="h-[calc(100vh-3.5rem)] w-full" />;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Inline SVG arrow rotated to a meteorological direction. Mirrors the
// CompassArrow React component but emits raw HTML so it can be embedded
// in a Leaflet popup. `deg` is the FROM bearing (NWS convention); the
// arrow points the way the energy travels (deg + 180).
function arrowSvg(deg: number, color: string, size = 11): string {
  const rotated = (deg + 180) % 360;
  return `
    <svg width="${size}" height="${size}" viewBox="0 0 24 24"
      style="transform: rotate(${rotated}deg); display:inline-block; vertical-align:-1px; color:${color};">
      <path d="M12 3 L18 18 L12 14 L6 18 Z" fill="currentColor" stroke="currentColor"
        stroke-width="0.6" stroke-linejoin="round" />
    </svg>`;
}

function buildPopupHtml(s: SpotWithLatest): string {
  const f = s.latest;
  const tier = tierFromStars(f?.stars ?? 0);
  const fg =
    tier.label === 'FAIR' || tier.label === 'FAIR TO GOOD' ? '#0F172A' : '#FFFFFF';

  const swellDir = pickSwell(f?.swell_dp ?? null, f?.dp ?? null);
  const swellPeriod = pickSwell(f?.swell_tp ?? null, f?.tp ?? null);
  const swellArrow =
    swellDir !== null && swellDir !== undefined
      ? `${arrowSvg(swellDir, '#0369A1')}&nbsp;${escapeHtml(degToCardinal(swellDir))}`
      : '';

  const windMph = msToMph(f?.wind_speed ?? null);
  const windDir = f?.wind_dir ?? null;
  const windQ = classifyWind(windDir, s.offshore_wind_deg);
  const windQLabel = windQualityLabel(windQ);
  const windParts: string[] = [];
  if (windDir !== null && windDir !== undefined) {
    windParts.push(`${arrowSvg(windDir, '#15803D')}`);
  }
  if (windMph !== null) {
    windParts.push(`${windMph.toFixed(0)} mph`);
  }

  const tideArrow =
    s.tide_trend === 'rising' ? '↑' : s.tide_trend === 'falling' ? '↓' : '';
  const tideLevel = f?.tide_level_ft;

  const subtitleParts: string[] = [];
  if (s.state) subtitleParts.push(escapeHtml(s.state));
  if (s.break_type) subtitleParts.push(escapeHtml(s.break_type));

  const conditionsLine = `
    <div style="font-size:12px;color:#0F172A;margin-top:8px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-variant-numeric:tabular-nums;">
      <span style="font-weight:700;">${escapeHtml(fmtFt(f?.face_ft ?? null))}</span>
      <span style="color:#475569;">${escapeHtml(fmtSec(swellPeriod))}</span>
      ${swellArrow ? `<span style="color:#0369A1;display:inline-flex;align-items:center;gap:3px;">${swellArrow}</span>` : ''}
    </div>
  `;

  const windLine =
    windParts.length === 0 && !windQLabel
      ? ''
      : `
    <div style="font-size:12px;color:#475569;margin-top:4px;display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-variant-numeric:tabular-nums;">
      <span style="color:#15803D;display:inline-flex;align-items:center;gap:3px;">${windParts.join(' ')}</span>
      ${windQLabel ? `<span>· ${escapeHtml(windQLabel)}</span>` : ''}
    </div>
  `;

  const tideLine =
    tideLevel === null || tideLevel === undefined
      ? ''
      : `
    <div style="font-size:12px;color:#475569;margin-top:4px;font-variant-numeric:tabular-nums;">
      Tide: <span style="color:#0F172A;font-weight:600;">${tideLevel.toFixed(1)}ft</span>${tideArrow ? ` <span style="color:#0F172A;">${tideArrow}</span>` : ''}
    </div>
  `;

  return `
    <div style="font-family:Inter,system-ui,sans-serif;color:#0F172A;min-width:220px;">
      <div style="font-weight:700;font-size:14px;margin-bottom:2px;">
        ${escapeHtml(s.name)}
      </div>
      <div style="font-size:11px;color:#475569;margin-bottom:8px;">
        ${subtitleParts.join(' · ')}
      </div>
      <div style="display:inline-block;background:${tier.hex};color:${fg};font-size:10px;font-weight:800;letter-spacing:0.18em;text-transform:uppercase;padding:3px 8px;border-radius:4px;">
        ${tier.label}
      </div>
      ${conditionsLine}
      ${windLine}
      ${tideLine}
      <a href="/spot/${encodeURIComponent(s.slug)}" style="display:inline-block;margin-top:10px;color:#0284C7;font-size:12px;font-weight:600;text-decoration:none;">
        View forecast →
      </a>
    </div>
  `;
}
