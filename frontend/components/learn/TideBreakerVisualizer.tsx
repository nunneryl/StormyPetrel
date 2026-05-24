'use client';

import { useState } from 'react';

// Reef cross-section. The tide slider raises and lowers the water
// surface; the breakpoint marker walks along the reef as the depth
// at which the wave finds its 78%-of-depth break ratio shifts.
export function TideBreakerVisualizer() {
  const [tide, setTide] = useState(0);

  const waterY = 170 - tide * 10;
  const breakDepth = 51;
  const breakpointFloorY = waterY + breakDepth;

  let breakpointX: number;
  if (breakpointFloorY <= 100) breakpointX = 640;
  else if (breakpointFloorY >= 320) breakpointX = 40;
  else {
    const t = Math.sqrt((breakpointFloorY - 100) / 220);
    breakpointX = 640 - 600 * t;
  }

  let breakerShape: string;
  if (tide < 1) breakerShape = 'Hollow';
  else if (tide < 3) breakerShape = 'Peeling';
  else breakerShape = 'Soft';

  const tideDisplay = (tide > 0 ? '+' : '') + tide.toFixed(1) + ' ft';

  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <svg viewBox="0 0 680 320" className="block w-full h-auto" role="img">
        <title>Tide and breakpoint</title>
        <desc>
          Reef cross-section. The tide slider raises and lowers the water
          surface, and the breakpoint marker moves along the reef.
        </desc>
        <rect
          x="40"
          y={waterY}
          width="600"
          height={310 - waterY}
          fill="#7DB8E8"
          fillOpacity="0.35"
        />
        <path
          d="M40,310 L40,320 L100,278 L200,218 L300,171 L400,135 L500,112 L600,101 L640,100 L640,310 Z"
          fill="#C9C6BC"
          stroke="#888780"
          strokeWidth="1"
        />
        <line x1="40" y1={waterY} x2="640" y2={waterY} stroke="#1565A8" strokeWidth="1.5" />
        <line
          x1={breakpointX}
          y1={waterY}
          x2={breakpointX}
          y2={breakpointFloorY}
          stroke="#D85A30"
          strokeWidth="1.5"
          strokeDasharray="4 4"
        />
        <circle cx={breakpointX} cy={waterY} r="5" fill="#D85A30" />
        <text
          x={breakpointX}
          y={waterY - 12}
          fontSize="12"
          textAnchor="middle"
          fill="#993C1D"
          fontWeight="500"
        >
          Wave breaks
        </text>
        <text x="50" y="36" fontSize="12" fill="#94A3B8">
          Offshore
        </text>
        <text x="630" y="36" fontSize="12" textAnchor="end" fill="#94A3B8">
          Shore
        </text>
      </svg>

      <div className="flex items-center gap-3 mt-4 mb-3">
        <label className="text-sm text-text-secondary min-w-[80px]">Tide level</label>
        <input
          type="range"
          min="-2"
          max="6"
          step="0.5"
          value={tide}
          onChange={(e) => setTide(parseFloat(e.target.value))}
          className="flex-1"
          aria-label="Tide level"
        />
        <span className="text-sm font-medium text-text-primary min-w-[54px] text-right">
          {tideDisplay}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="bg-slate-50 rounded-lg p-3">
          <div className="text-[11px] uppercase tracking-widest2 text-text-muted">
            Wave height (offshore)
          </div>
          <div className="text-xl sm:text-2xl font-medium mt-0.5 text-text-primary">4 ft</div>
        </div>
        <div className="bg-slate-50 rounded-lg p-3">
          <div className="text-[11px] uppercase tracking-widest2 text-text-muted">
            Depth at breakpoint
          </div>
          <div className="text-xl sm:text-2xl font-medium mt-0.5 text-text-primary">5.1 ft</div>
        </div>
        <div className="bg-slate-50 rounded-lg p-3">
          <div className="text-[11px] uppercase tracking-widest2 text-text-muted">
            Breaker shape
          </div>
          <div className="text-xl sm:text-2xl font-medium mt-0.5 text-text-primary">
            {breakerShape}
          </div>
        </div>
      </div>
    </div>
  );
}
