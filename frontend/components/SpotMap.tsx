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
      // Leaflet's CSS isn't bundled by the JS module; pull it in once on mount.
      if (typeof document !== 'undefined' && !document.getElementById('leaflet-css')) {
        const link = document.createElement('link');
        link.id = 'leaflet-css';
        link.rel = 'stylesheet';
        link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
        link.crossOrigin = '';
        document.head.appendChild(link);
      }
      if (cancelled || !containerRef.current) return;

      const map = L.map(containerRef.current, {
        center: [37.5, -98],
        zoom: 4,
        worldCopyJump: true,
      });
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
        const html = `<div style="
          width:14px;height:14px;border-radius:50%;
          background:${tier.hex};
          border:2px solid #04080f;
          box-shadow:0 0 0 1px ${tier.hex}80;
        "></div>`;
        const icon = L.divIcon({
          className: '',
          html,
          iconSize: [14, 14],
          iconAnchor: [7, 7],
        });
        const m = L.marker([s.lat, s.lng], { icon }).addTo(map);
        const f = s.latest;
        const popupHtml = `
          <div style="font-family:system-ui,sans-serif;color:#e5edf5;background:#0a1220;padding:6px 8px;border-radius:4px;min-width:160px;">
            <div style="font-weight:700;color:#fff;margin-bottom:2px;">${escapeHtml(s.name)}</div>
            <div style="font-size:11px;color:#8aa3c0;margin-bottom:6px;">${escapeHtml(s.state ?? '')}</div>
            <div style="display:inline-block;background:${tier.hex};color:#fff;font-size:10px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;padding:2px 6px;border-radius:3px;">
              ${tier.label}
            </div>
            <div style="font-size:12px;color:#cbd5e1;margin-top:6px;">
              ${fmtFt(f?.face_ft ?? null)} · ${fmtSec(f?.tp ?? null)} · ${fmtMph(f?.wind_speed ?? null)}
            </div>
            <a href="/spot/${encodeURIComponent(s.slug)}" style="display:inline-block;margin-top:6px;color:#3da9d7;font-size:12px;text-decoration:underline;">
              View forecast →
            </a>
          </div>
        `;
        m.bindPopup(popupHtml, { className: 'sp-popup' });
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

  return <div ref={containerRef} className="h-[calc(100vh-4rem)] w-full" />;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
