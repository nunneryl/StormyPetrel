'use client';

import { Area, AreaChart, ResponsiveContainer } from 'recharts';

export function Sparkline({
  values,
  color = '#38BDF8',
  height = 32,
  className = '',
}: {
  values: number[];
  color?: string;
  height?: number;
  className?: string;
}) {
  const data = values.map((v, i) => ({ i, v }));
  return (
    <div className={className} style={{ height, width: '100%' }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`spark-${color.replace('#', '')}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.6} />
              <stop offset="100%" stopColor={color} stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#spark-${color.replace('#', '')})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Simple horizontal "energy bar" — used in the partition card to show
 *  relative contribution of each swell partition. Width is a fraction of
 *  the cell, color is the partition's brand color. */
export function EnergyBar({
  fraction,
  color = '#38BDF8',
  className = '',
}: {
  fraction: number; // 0..1
  color?: string;
  className?: string;
}) {
  const w = Math.max(0, Math.min(1, fraction));
  return (
    <div className={`relative h-1.5 rounded-full bg-ink-700 overflow-hidden ${className}`}>
      <div
        className="absolute inset-y-0 left-0 rounded-full"
        style={{ width: `${w * 100}%`, background: color }}
      />
    </div>
  );
}
