'use client';

import { useEffect, useMemo, useRef } from 'react';
import type { SpotWithLatest } from '@/lib/types';
import { tierFromStars } from '@/lib/ratings';
import { fmtFt, fmtMph, fmtSec } from '@/lib/formatting';

type LeafletMod = typeof import('leaflet');
type ClusterMod = typeof import('leaflet.markercluster');

// Stylesheet IDs so we only inject the Leaflet CSS bundles once across
// the whole app — repeated route mounts wouldn't add duplicate <link>s.
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
      const L = (await import('leaflet')) as unknown as LeafletMod;
      // markercluster augments L on import (it ships as a Leaflet plugin
      // that mutates the L global). Importing it for its side effects
      // is enough — we don't reference the module's exports directly.
      await import('leaflet.markercluster');

      ensureCss(LEAFLET_CSS_ID, 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');
      ensureCss(
        LEAFLET_CLUSTER_CSS_ID,
        'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.css',
      );
      ensureCss(
        LEAFLET_CLUSTER_DEFAULT_CSS_ID,
        'https://unpkg.com/leaflet.markercluster@1.5.3/dist/MarkerCluster.Default.css',
      );

      if (cancelled || !containerRef.current) return;

      const map = L.map(containerRef.current, {
        center: [37.5, -98],
        zoom: 4,
        worldCopyJump: true,
        zoomControl: false,
      });
      L.control.zoom({ position: 'bottomright' }).addTo(map);
      mapRef.current = map;

      // CartoDB Positron — clean light theme, more readable than the
      // generic 'voyager' tiles for our colored markers.
      L.tileLayer(
        'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        {
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
          maxZoom: 19,
          subdomains: 'abcd',
        },
      ).addTo(map);

      // Cluster group — at low zoom, nearby spots collapse into a
      // numbered circle colored by the *best* (highest-rating) spot in
      // the cluster, so the eye finds active regions immediately.
      type LWithCluster = typeof L & {
        markerClusterGroup: (opts?: unknown) => unknown;
      };
      const clusterGroup = (L as LWithCluster).markerClusterGroup({
        showCoverageOnHover: false,
        spiderfyOnMaxZoom: true,
        disableClusteringAtZoom: 9,
        maxClusterRadius: 50,
        iconCreateFunction: (cluster: { getAllChildMarkers: () => Array<{ options: { spotStars?: number } }> }) => {
          const children = cluster.getAllChildMarkers();
          const bestStars = children.reduce(
            (m, c) => Math.max(m, c.options.spotStars ?? 0),
            0,
          );
          const tier = tierFromStars(bestStars);
          const count = children.length;
          const size =
            count < 10 ? 32 : count < 50 ? 38 : count < 200 ? 44 : 50;
          const html = `
            <div style="
              width:${size}px;height:${size}px;border-radius:50%;
              background:${tier.hex};
              color:${tier.label === 'FAIR' || tier.label === 'FAIR TO GOOD' ? '#0F172A' : '#FFFFFF'};
              display:flex; align-items:center; justify-content:center;
              font-weight:800; font-size:${size <= 32 ? 12 : 13}px;
              box-shadow:
                0 0 0 2px #FFFFFF,
                0 0 0 3px ${tier.hex}66,
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
      }) as unknown as {
        addLayer: (m: unknown) => void;
        addTo: (m: unknown) => unknown;
      };

      // Per-spot markers
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      type LMarkerWithStars = any;
      for (const s of data) {
        if (s.lat === null || s.lng === null) continue;
        const tier = tierFromStars(s.latest?.stars ?? 0);
        const html = `
          <div class="sp-marker" style="position:relative; width:18px; height:18px;">
            <div style="
              position:absolute; inset:0; border-radius:50%;
              background:${tier.hex};
              border: 2px solid #0F172A;
              box-shadow: 0 1px 3px rgba(15, 23, 42, 0.35);
            "></div>
          </div>`;
        const icon = L.divIcon({
          className: '',
          html,
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        const m = L.marker([s.lat, s.lng], {
          icon,
          // Custom property the cluster uses to find the best rating.
          // Cast keeps TS happy — Leaflet doesn't type custom options.
          spotStars: s.latest?.stars ?? 0,
        } as LMarkerWithStars);
        const f = s.latest;
        const fg = tier.label === 'FAIR' || tier.label === 'FAIR TO GOOD' ? '#0F172A' : '#FFFFFF';
        const popupHtml = `
          <div style="font-family:Inter,system-ui,sans-serif;color:#0F172A;min-width:200px;">
            <div style="font-weight:700;font-size:14px;color:#0F172A;margin-bottom:2px;">
              ${escapeHtml(s.name)}
            </div>
            <div style="font-size:11px;color:#475569;margin-bottom:8px;">
              ${escapeHtml(s.state ?? '')}${s.break_type ? ' · ' + escapeHtml(s.break_type) : ''}
            </div>
            <div style="display:inline-block;background:${tier.hex};color:${fg};font-size:10px;font-weight:800;letter-spacing:0.18em;text-transform:uppercase;padding:3px 8px;border-radius:4px;">
              ${tier.label}
            </div>
            <div style="font-size:12px;color:#475569;margin-top:8px;display:flex;gap:8px;font-variant-numeric:tabular-nums;">
              <span style="color:#0F172A;font-weight:700;">${escapeHtml(fmtFt(f?.face_ft ?? null))}</span>
              <span>${escapeHtml(fmtSec(f?.tp ?? null))}</span>
              <span>${escapeHtml(fmtMph(f?.wind_speed ?? null))}</span>
            </div>
            <a href="/spot/${encodeURIComponent(s.slug)}" style="display:inline-block;margin-top:8px;color:#0284C7;font-size:12px;font-weight:600;text-decoration:none;">
              View forecast →
            </a>
          </div>
        `;
        m.bindPopup(popupHtml, { className: 'sp-popup' });
        clusterGroup.addLayer(m);
      }
      clusterGroup.addTo(map);

      // Best-effort geolocation — pan to the user's nearest coast at
      // zoom 8 (close enough to read individual spots, wide enough to
      // see the regional clustering). Silent failure on deny / timeout.
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

  // Edge-to-edge: fill the viewport below the 3.5rem nav bar. No
  // page-level padding wrapping the map.
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
