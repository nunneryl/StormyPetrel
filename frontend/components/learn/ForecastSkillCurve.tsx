'use client';

import { useMemo } from 'react';

const KEYS: [number, number][] = [
  [0, 100], [1, 96], [2, 92], [3, 86], [4, 79], [5, 71],
  [6, 63], [7, 55], [8, 47], [9, 38], [10, 30], [11, 23],
  [12, 17], [13, 13], [14, 10],
];

function interp(d: number): number {
  for (let i = 0; i < KEYS.length - 1; i++) {
    const [d1, s1] = KEYS[i];
    const [d2, s2] = KEYS[i + 1];
    if (d >= d1 && d <= d2) {
      const t = (d - d1) / (d2 - d1);
      return s1 + t * (s2 - s1);
    }
  }
  return KEYS[KEYS.length - 1][1];
}

export function ForecastSkillCurve() {
  const { strokePath, fillPath } = useMemo(() => {
    const xMin = 70, xMax = 640, dayMax = 14;
    const yTop = 40, yBot = 240;
    const tx = (d: number) => xMin + ((xMax - xMin) * d) / dayMax;
    const ty = (s: number) => yBot - ((yBot - yTop) * s) / 100;

    let stroke = '';
    let fill = `M ${xMin.toFixed(1)} ${yBot} `;
    let first = true;
    for (let d = 0; d <= dayMax + 0.001; d += 0.1) {
      const dd = Math.min(d, dayMax);
      const x = tx(dd);
      const y = ty(interp(dd));
      stroke += (first ? 'M ' : 'L ') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
      fill += 'L ' + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
      first = false;
    }
    fill += `L ${xMax.toFixed(1)} ${yBot} Z`;
    return { strokePath: stroke, fillPath: fill };
  }, []);

  return (
    <div className="my-8">
      <svg viewBox="0 0 680 310" className="w-full" role="img">
        <title>Forecast skill vs lead time</title>
        <desc>A curve showing how surf forecast accuracy declines with forecast lead time, with annotated trust zones from Fact to Vibes.</desc>

        <text x="20" y="22" style={{ fontSize: '12px', fontWeight: 500 }} className="fill-slate-500">
          Accuracy
        </text>

        <rect x="70" y="40" width="81.4" height="200" fill="#1D9E75" fillOpacity="0.14" />
        <rect x="151.4" y="40" width="81.4" height="200" fill="#185FA5" fillOpacity="0.12" />
        <rect x="232.8" y="40" width="122.1" height="200" fill="#BA7517" fillOpacity="0.12" />
        <rect x="354.9" y="40" width="285.1" height="200" fill="#D85A30" fillOpacity="0.10" />

        <text x="110.7" y="62" textAnchor="middle" style={{ fontSize: '13px', fontWeight: 500 }} fill="#0F6E56">Fact</text>
        <text x="192.1" y="62" textAnchor="middle" style={{ fontSize: '13px', fontWeight: 500 }} fill="#0F4B85">Hypothesis</text>
        <text x="293.8" y="62" textAnchor="middle" style={{ fontSize: '13px', fontWeight: 500 }} fill="#854F0B">Pattern</text>
        <text x="497.4" y="62" textAnchor="middle" style={{ fontSize: '13px', fontWeight: 500 }} fill="#993C1D">Vibes</text>

        <line x1="66" y1="40" x2="70" y2="40" className="stroke-slate-200" strokeWidth="0.5" />
        <text x="62" y="44" textAnchor="end" style={{ fontSize: '11px' }} className="fill-slate-500">100%</text>
        <line x1="66" y1="140" x2="70" y2="140" className="stroke-slate-200" strokeWidth="0.5" />
        <text x="62" y="144" textAnchor="end" style={{ fontSize: '11px' }} className="fill-slate-500">50%</text>
        <line x1="66" y1="240" x2="70" y2="240" className="stroke-slate-200" strokeWidth="0.5" />
        <text x="62" y="244" textAnchor="end" style={{ fontSize: '11px' }} className="fill-slate-500">0%</text>

        <line x1="70" y1="140" x2="640" y2="140" className="stroke-slate-200" strokeWidth="0.5" strokeDasharray="2 3" />
        <line x1="70" y1="40" x2="70" y2="240" className="stroke-slate-200" strokeWidth="0.5" />
        <line x1="70" y1="240" x2="640" y2="240" className="stroke-slate-300" strokeWidth="0.5" />

        <path d={fillPath} fill="#185FA5" fillOpacity="0.10" />
        <path d={strokePath} stroke="#0F4B85" strokeWidth="2" fill="none" />

        {([
          [70, '0'], [151.4, '2'], [232.8, '4'], [354.9, '7'], [640, '14'],
        ] as [number, string][]).map(([x, label]) => (
          <g key={label}>
            <line x1={x} y1="240" x2={x} y2="246" className="stroke-slate-500" strokeWidth="0.5" />
            <text x={x} y="262" textAnchor="middle" style={{ fontSize: '11px' }} className="fill-slate-500">
              {label}
            </text>
          </g>
        ))}

        <text x="355" y="288" textAnchor="middle" style={{ fontSize: '12px' }} className="fill-slate-500">
          Forecast lead time (days)
        </text>
      </svg>
    </div>
  );
}
