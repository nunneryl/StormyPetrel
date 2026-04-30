import { CompassArrow } from './CompassArrow';
import { EnergyBar } from './Sparkline';
import { degToCardinal, fmtSec, metersToFeet } from '@/lib/formatting';
import type { Forecast } from '@/lib/types';

type Component = {
  label: string;
  hs: number | null;
  tp: number | null;
  dp: number | null;
  /** Marks wind-sea component for visual de-emphasis. */
  windSea?: boolean;
};

function gather(f: Forecast): Component[] {
  const all: Component[] = [
    { label: 'P1 (primary)',   hs: f.swell_1_hs, tp: f.swell_1_tp, dp: f.swell_1_dp },
    { label: 'P2 (secondary)', hs: f.swell_2_hs, tp: f.swell_2_tp, dp: f.swell_2_dp },
    { label: 'P3 (tertiary)',  hs: f.swell_3_hs, tp: f.swell_3_tp, dp: f.swell_3_dp },
    {
      label: 'Wind sea',
      hs: f.wind_wave_hs,
      tp: f.wind_wave_tp,
      dp: f.wind_wave_dp,
      windSea: true,
    },
  ];
  return all
    .filter((c) => c.hs !== null && c.hs !== undefined && c.hs > 0.05)
    .sort((a, b) => (b.hs ?? 0) - (a.hs ?? 0));
}

const SOURCE_LABEL: Record<string, string> = {
  ww3:        'WAVEWATCH III partitions',
  nwps_swell: 'NWPS swell channel',
  buoy:       'NDBC spectral buoy',
  nwps_total: 'NWPS total wave (no spectral decomposition)',
};

export function SwellPartitions({ forecast }: { forecast: Forecast }) {
  const components = gather(forecast);
  if (components.length === 0) return null;

  // Energy ~ Hs²; bar width is each component's energy fraction of the
  // largest component so the dominant partition fills the bar.
  const maxEnergy = Math.max(...components.map((c) => (c.hs ?? 0) ** 2));

  return (
    <section className="rounded-xl border border-ink-600 bg-ink-800/60 backdrop-blur-sm">
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <h3 className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Swell components
        </h3>
        <span className="text-[10px] uppercase tracking-widest text-text-muted">
          {SOURCE_LABEL[forecast.swell_source ?? ''] ?? forecast.swell_source ?? '—'}
        </span>
      </div>
      <div className="px-4 pb-3 divide-y divide-ink-600">
        {components.map((c) => {
          const ft = metersToFeet(c.hs);
          const energy = (c.hs ?? 0) ** 2;
          const fraction = maxEnergy > 0 ? energy / maxEnergy : 0;
          const color = c.windSea ? '#94A3B8' : '#38BDF8';
          const dim = c.windSea ? 'opacity-60' : '';
          return (
            <div key={c.label} className={`grid grid-cols-[110px_minmax(0,1fr)_70px_64px_72px] sm:grid-cols-[140px_minmax(0,1fr)_80px_72px_88px] items-center gap-3 py-2 ${dim}`}>
              <div className="text-xs text-text-secondary truncate">{c.label}</div>
              <EnergyBar fraction={fraction} color={color} />
              <div className="text-right font-bold tabular-nums text-text-primary">
                {ft !== null ? `${ft.toFixed(1)}ft` : '—'}
              </div>
              <div className="text-right text-text-secondary tabular-nums">{fmtSec(c.tp)}</div>
              <div className="flex items-center justify-end gap-1.5">
                <CompassArrow
                  deg={c.dp}
                  size={14}
                  variant={c.windSea ? 'neutral' : 'swell'}
                  showLabel={false}
                />
                <span className="text-xs text-text-muted tabular-nums w-12 text-right">
                  {c.dp !== null && c.dp !== undefined
                    ? `${degToCardinal(c.dp)} ${c.dp.toFixed(0)}°`
                    : ''}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
