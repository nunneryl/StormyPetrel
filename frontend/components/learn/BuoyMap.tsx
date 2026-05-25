'use client';

import { useEffect, useRef } from 'react';

// US buoy reference map — major deep-water + nearshore stations that
// surfers actually read. Same Leaflet/CartoDB chrome as the swell-
// direction and wind maps (dynamic Leaflet import, runtime CSS
// inject, scrollWheelZoom off). No interactivity beyond marker
// popups + permanent station-ID tooltips.

type Buoy = {
  id: string;
  name: string;
  region: string;
  type: 'deep' | 'near';
  lat: number;
  lng: number;
};

const BUOYS: Buoy[] = [
  { id: '46086',    name: 'San Clemente Basin', region: 'SoCal offshore',         type: 'deep', lat: 32.5,  lng: -118.0 },
  { id: 'CDIP 067', name: 'Harvest',            region: 'SoCal nearshore',        type: 'near', lat: 34.45, lng: -120.78 },
  { id: '46059',    name: 'West California',    region: 'NorCal offshore',        type: 'deep', lat: 38.0,  lng: -130.0 },
  { id: 'CDIP 029', name: 'Mavericks',          region: 'NorCal nearshore',       type: 'near', lat: 37.5,  lng: -122.5 },
  { id: '46005',    name: 'West Washington',    region: 'Pacific NW offshore',    type: 'deep', lat: 46.0,  lng: -131.0 },
  { id: '46211',    name: 'Grays Harbor',       region: 'Pacific NW nearshore',   type: 'near', lat: 46.86, lng: -124.24 },
  { id: '41001',    name: 'East Hatteras',      region: 'Mid-Atlantic offshore',  type: 'deep', lat: 34.7,  lng: -72.7 },
  { id: '44025',    name: 'Long Island',        region: 'Northeast nearshore',    type: 'near', lat: 40.25, lng: -73.16 },
  { id: '41002',    name: 'South Hatteras',     region: 'Southeast offshore',     type: 'deep', lat: 32.4,  lng: -75.4 },
  { id: '41010',    name: 'Canaveral East',     region: 'Florida nearshore',      type: 'near', lat: 28.9,  lng: -78.5 },
  { id: '51001',    name: 'NW Hawaii',          region: 'Hawaii offshore',        type: 'deep', lat: 24.5,  lng: -162.0 },
  { id: 'CDIP 098', name: 'Mokapu Point',       region: 'Hawaii nearshore',       type: 'near', lat: 21.42, lng: -157.68 },
];

const LEAFLET_CSS_ID = 'leaflet-css';
const BUOY_TOOLTIP_CSS_ID = 'buoy-map-tooltip-css';

function ensureCss(id: string, href: string) {
  if (typeof document === 'undefined' || document.getElementById(id)) return;
  const link = document.createElement('link');
  link.id = id;
  link.rel = 'stylesheet';
  link.href = href;
  link.crossOrigin = '';
  document.head.appendChild(link);
}

function ensureTooltipCss() {
  if (typeof document === 'undefined' || document.getElementById(BUOY_TOOLTIP_CSS_ID)) return;
  const style = document.createElement('style');
  style.id = BUOY_TOOLTIP_CSS_ID;
  style.textContent = `
    .buoy-map-tooltip {
      background: rgba(255, 255, 255, 0.92) !important;
      border: none !important;
      box-shadow: none !important;
      font-size: 11px !important;
      padding: 1px 5px !important;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace !important;
      color: #0f172a !important;
    }
    .buoy-map-tooltip::before {
      display: none !important;
    }
  `;
  document.head.appendChild(style);
}

export function BuoyMap() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    (async () => {
      const leafletNs = await import('leaflet');
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const L: any = (leafletNs as any).default ?? leafletNs;
      ensureCss(LEAFLET_CSS_ID, 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');
      ensureTooltipCss();
      if (cancelled || !containerRef.current) return;

      const map = L.map(containerRef.current, {
        zoomControl: true,
        attributionControl: true,
        scrollWheelZoom: false,
      });
      mapRef.current = map;

      map.fitBounds(
        [
          [19, -166],
          [50, -65],
        ],
        { padding: [12, 12] },
      );

      L.tileLayer('https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 10,
        minZoom: 2,
        subdomains: 'abcd',
        attribution: '© OpenStreetMap, © CARTO',
      }).addTo(map);

      BUOYS.forEach((b) => {
        const color = b.type === 'deep' ? '#185FA5' : '#D85A30';
        const typeLabel =
          b.type === 'deep' ? "Deep water (what's coming)" : "Nearshore (what's arriving)";
        const marker = L.circleMarker([b.lat, b.lng], {
          radius: 6,
          fillColor: color,
          color: '#FFFFFF',
          weight: 1.5,
          opacity: 1,
          fillOpacity: 0.95,
        }).addTo(map);

        marker.bindPopup(
          `<div style="font-weight:500;font-size:13px;margin-bottom:4px">${b.id} — ${b.name}</div>` +
            `<div style="font-size:12px;color:#64748b;line-height:1.5">${b.region}<br>${typeLabel}</div>`,
        );

        marker.bindTooltip(b.id, {
          permanent: true,
          direction: 'right',
          offset: [8, 0],
          className: 'buoy-map-tooltip',
        });
      });
    })();
    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <div
        ref={containerRef}
        className="w-full rounded-lg border border-ink-600 overflow-hidden"
        style={{ height: 400 }}
      />
      <div className="flex items-center justify-center gap-5 mt-3 text-xs text-text-muted flex-wrap">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: '#185FA5' }} />
          Deep-water buoy (offshore signal)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: '#D85A30' }} />
          Nearshore buoy (arriving signal)
        </span>
      </div>
    </div>
  );
}
