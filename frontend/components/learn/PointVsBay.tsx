'use client';

import { useState } from 'react';

// Plan-view diagram showing how coastline shape focuses (point) or
// spreads (bay) wave energy. Pure SVG, no chart library — the
// imagery is stylised, not numerically simulated.
//
// Top of canvas = offshore. Bottom = the coastline. Parallel crests
// cross the top, ray lines drop toward shore and bend to match the
// geometry. Toggle between modes via the pill row.

type Mode = 'point' | 'bay';

const COPY: Record<Mode, { headline: string; body: string }> = {
  point: {
    headline: 'Energy concentrates',
    body:
      'Wave rays bend toward the shallow promontory. Lines bunch together at the tip, so the breaking wave at the point is bigger than the same swell on the open beaches either side.',
  },
  bay: {
    headline: 'Energy spreads',
    body:
      'Inside the bay, wave rays diverge across the wider arc of coast. The same offshore swell delivers less energy per yard of beach — soft, smaller waves overall unless a reef inside the bay re-focuses it.',
  },
};

export function PointVsBay() {
  const [mode, setMode] = useState<Mode>('point');
  return (
    <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="text-[11px] uppercase tracking-widest2 font-bold text-text-secondary">
          Coastline shape
        </div>
        <div className="inline-flex rounded-full border border-ink-600 p-0.5 text-[11px] font-bold">
          <ToggleBtn active={mode === 'point'} onClick={() => setMode('point')}>
            Point / headland
          </ToggleBtn>
          <ToggleBtn active={mode === 'bay'} onClick={() => setMode('bay')}>
            Bay / cove
          </ToggleBtn>
        </div>
      </div>

      <div className="rounded-lg border border-ink-600 bg-ink-900/60 p-3">
        <svg
          viewBox="0 0 400 260"
          width="100%"
          className="block"
          aria-label={`Wave ray diagram for ${mode}`}
        >
          <defs>
            <marker
              id="arrowhead"
              markerWidth="6"
              markerHeight="6"
              refX="5"
              refY="3"
              orient="auto"
            >
              <path d="M0,0 L6,3 L0,6 z" fill="#0369A1" />
            </marker>
          </defs>

          <text x="200" y="14" fontSize="9" fill="#94A3B8" textAnchor="middle"
                letterSpacing="0.18em">
            OFFSHORE
          </text>

          {/* Incoming parallel wave crests — identical in both modes;
              the geometry below them is what changes. */}
          {[26, 38, 50, 62, 74].map((y) => (
            <line
              key={y}
              x1="20"
              y1={y}
              x2="380"
              y2={y}
              stroke="#0369A1"
              strokeOpacity={0.35}
              strokeWidth={1.2}
            />
          ))}

          {mode === 'point' ? <PointRays /> : <BayRays />}
          {mode === 'point' ? <PointCoast /> : <BayCoast />}

          <text x="200" y="252" fontSize="9" fill="#94A3B8" textAnchor="middle"
                letterSpacing="0.18em">
            SHORE
          </text>
        </svg>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-4">
        {(['point', 'bay'] as Mode[]).map((m) => {
          const active = m === mode;
          return (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`text-left rounded-lg border p-3.5 transition ${
                active
                  ? 'border-cyan-500 bg-cyan-50'
                  : 'border-ink-600 bg-white hover:border-ink-500 opacity-70'
              }`}
            >
              <div className="text-[10px] uppercase tracking-widest2 font-bold text-text-secondary">
                {m === 'point' ? 'Point / headland' : 'Bay / cove'}
              </div>
              <div className="mt-0.5 text-sm font-bold text-text-primary">
                {COPY[m].headline}
              </div>
              <p className="mt-1.5 text-sm text-text-secondary leading-relaxed">
                {COPY[m].body}
              </p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// --- SVG primitives ---------------------------------------------------------

function PointRays() {
  // Rays start evenly spaced offshore and bend to converge on the
  // point's tip near the bottom center. Outer rays angle inward.
  const tipX = 200;
  const tipY = 200;
  const startY = 84;
  const startXs = [60, 110, 160, 200, 240, 290, 340];
  return (
    <g>
      {startXs.map((sx) => (
        <RayPath key={sx} startX={sx} startY={startY} endX={tipX} endY={tipY} />
      ))}
    </g>
  );
}

function BayRays() {
  // Rays diverge once they enter the concave bay — each one ends
  // along the curving shore roughly proportional to where it started.
  const startY = 84;
  const startXs = [80, 130, 175, 200, 225, 270, 320];
  const endpoints: Array<[number, number]> = [
    [40, 200],
    [115, 222],
    [175, 232],
    [200, 234],
    [225, 232],
    [285, 222],
    [360, 200],
  ];
  return (
    <g>
      {startXs.map((sx, i) => (
        <RayPath
          key={sx}
          startX={sx}
          startY={startY}
          endX={endpoints[i][0]}
          endY={endpoints[i][1]}
        />
      ))}
    </g>
  );
}

function RayPath({
  startX, startY, endX, endY,
}: { startX: number; startY: number; endX: number; endY: number }) {
  // Slight quadratic bend so the ray reads as a refracting wave path.
  const cx = startX + (endX - startX) * 0.3;
  const cy = startY + (endY - startY) * 0.6;
  return (
    <path
      d={`M ${startX} ${startY} Q ${cx} ${cy} ${endX} ${endY}`}
      stroke="#0369A1"
      strokeWidth={1.5}
      fill="none"
      strokeLinecap="round"
      markerEnd="url(#arrowhead)"
      opacity={0.85}
    />
  );
}

function PointCoast() {
  return (
    <g>
      <path
        d="M 0 230 L 0 260 L 400 260 L 400 230 L 240 230 L 200 195 L 160 230 Z"
        fill="#FEF3C7"
        stroke="#D6A24F"
        strokeWidth={1.2}
      />
      <text x="200" y="220" fontSize="9" fill="#7C5712" textAnchor="middle"
            fontWeight={700}>
        Point
      </text>
    </g>
  );
}

function BayCoast() {
  return (
    <g>
      <path
        d="M 0 230 L 0 260 L 400 260 L 400 230 C 320 205 240 232 200 234 C 160 232 80 205 0 230 Z"
        fill="#FEF3C7"
        stroke="#D6A24F"
        strokeWidth={1.2}
      />
      <text x="200" y="245" fontSize="9" fill="#7C5712" textAnchor="middle"
            fontWeight={700}>
        Bay
      </text>
    </g>
  );
}

function ToggleBtn({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1 transition ${
        active
          ? 'bg-cyan-500 text-white'
          : 'text-text-secondary hover:text-text-primary'
      }`}
    >
      {children}
    </button>
  );
}
