'use client';

import { useEffect, useMemo, useRef } from 'react';
import type { SpotWithLatest } from '@/lib/types';
import { tierFromStars } from '@/lib/ratings';
import { fmtFt, fmtMph, fmtSec } from '@/lib/formatting';

type LeafletMod = typeof import('leaflet');

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
      if (typeof document !== 'undefined' && !document.getElementById('leaflet-css')) {
        const link = document.createElement('link');
        link.id = 'leaflet-css';
        link.rel = 'stylesheet';
        link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
        link.crossOrigin = '';
        document.head.appendChild(link);
      }
      if (cancelled || !containerRef.current) return;

      // Best-effort geolocation centering: if the user grants quickly we
      // pan there; otherwise the default CONUS view stays.
      const map = L.map(containerRef.current, {
        center: [37.5, -98],
        zoom: 4,
        worldCopyJump: true,
        zoomControl: false,
      });
      L.control.zoom({ position: 'bottomright' }).addTo(map);
      mapRef.current = map;

      L.tileLayer(
        'https://cartodb-basemaps-{s}.global.ssl.fastly.net/dark_all/{z}/{x}/{y}{r}.png',
        {
          attribution:
            '&copy; OpenStreetMap &copy; <a href="https://carto.com/attributions">CARTO</a>',
          maxZoom: 18,
        },
      ).addTo(map);

      for (const s of data) {
        if (s.lat === null || s.lng === null) continue;
        const tier = tierFromStars(s.latest?.stars ?? 0);
        // Two-layer marker: filled disc + soft halo, ring widens on hover.
        const html = `
          <div class="sp-marker" style="
            position:relative; width:18px; height:18px;
          ">
            <div style="
              position:absolute; inset:0; border-radius:50%;
              background:${tier.hex};
              box-shadow:
                0 0 0 2px #0B1426,
                0 0 0 3px ${tier.hex}66,
                0 0 8px ${tier.glow};
            "></div>
          </div>`;
        const icon = L.divIcon({
          className: '',
          html,
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        const m = L.marker([s.lat, s.lng], { icon }).addTo(map);
        const f = s.latest;
        const popupHtml = `
          <div style="font-family:Inter,system-ui,sans-serif;color:#F1F5F9;min-width:200px;">
            <div style="font-weight:700;font-size:14px;color:#F1F5F9;margin-bottom:2px;">
              ${escapeHtml(s.name)}
            </div>
            <div style="font-size:11px;color:#94A3B8;margin-bottom:8px;">
              ${escapeHtml(s.state ?? '')}${s.break_type ? ' · ' + escapeHtml(s.break_type) : ''}
            </div>
            <div style="display:inline-block;background:${tier.hex};color:#0B1426;font-size:10px;font-weight:800;letter-spacing:0.18em;text-transform:uppercase;padding:3px 8px;border-radius:4px;">
              ${tier.label}
            </div>
            <div style="font-size:12px;color:#94A3B8;margin-top:8px;display:flex;gap:8px;font-variant-numeric:tabular-nums;">
              <span style="color:#F1F5F9;font-weight:700;">${escapeHtml(fmtFt(f?.face_ft ?? null))}</span>
              <span>${escapeHtml(fmtSec(f?.tp ?? null))}</span>
              <span>${escapeHtml(fmtMph(f?.wind_speed ?? null))}</span>
            </div>
            <a href="/spot/${encodeURIComponent(s.slug)}" style="display:inline-block;margin-top:8px;color:#38BDF8;font-size:12px;font-weight:600;text-decoration:none;">
              View forecast →
            </a>
          </div>
        `;
        m.bindPopup(popupHtml, { className: 'sp-popup' });
      }

      // Best-effort geolocation pan, fired after markers so the bounds
      // animation feels intentional.
      if (typeof navigator !== 'undefined' && navigator.geolocation) {
        navigator.geolocation.getCurrentPosition(
          (pos) => {
            if (cancelled) return;
            map.flyTo([pos.coords.latitude, pos.coords.longitude], 7, { duration: 1.2 });
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
