import { SwellCompass } from './SwellCompass';
import { degToCardinal, fmtSec, metersToFeet } from '@/lib/formatting';
import type { Forecast } from '@/lib/types';

type Component = {
  label: string;
  hs: number | null;
  tp: number | null;
  dp: number | null;
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
  ww3:        'WAVEWATCH III',
  nwps_swell: 'NWPS swell',
  buoy:       'NDBC buoy',
  nwps_total: 'NWPS total',
};

export function SwellPartitions({ forecast }: { forecast: Forecast }) {
  const all = gather(forecast);
  if (all.length === 0) return null;

  // If we only have 1 swell partition AND no wind sea worth showing,
  // skip the whole card — the hero "Swell" tile already covers it.
  const swells = all.filter((c) => !c.windSea);
  if (swells.length <= 1 && all.length <= 1) return null;

  // Total energy of the SURFABLE partitions (ignore wind sea here so the
  // percentage ratio reflects "what fraction of the rideable swell is
  // this component", not "fraction of total ocean energy").
  const swellEnergyTotal = swells.reduce(
    (acc, c) => acc + (c.hs ?? 0) ** 2,
    0,
  ) || 1;

  return (
    <section className="rounded-xl border border-ink-600 bg-white shadow-card">
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <h3 className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Swell breakdown
        </h3>
        <span className="text-[10px] uppercase tracking-widest text-text-muted">
          {SOURCE_LABEL[forecast.swell_source ?? ''] ?? forecast.swell_source ?? '—'}
        </span>
      </div>
      <div className="px-4 pb-3 divide-y divide-ink-600">
        {all.map((c) => {
          const ft = metersToFeet(c.hs);
          const energy = (c.hs ?? 0) ** 2;
          // Only swell rows show a percentage of rideable energy.
          // Wind sea is muted and gets no bar.
          const fraction = c.windSea ? 0 : energy / swellEnergyTotal;
          const barColor = '#0369A1'; // dark blue (swell brand)
          return (
            <div
              key={c.label}
              className={`grid grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] sm:grid-cols-[140px_84px_72px_104px_minmax(0,1fr)_64px] items-center gap-2 sm:gap-3 py-2 ${c.windSea ? 'opacity-60' : ''}`}
            >
              <div className="text-xs text-text-secondary truncate">{c.label}</div>
              <div className="flex items-center gap-1.5">
                <SwellCompass
                  deg={c.dp}
                  size={20}
                  color={c.windSea ? '#94A3B8' : '#0369A1'}
                />
                <span className="text-xs text-text-secondary tabular-nums">
                  {c.dp !== null && c.dp !== undefined ? degToCardinal(c.dp) : '—'}
                </span>
              </div>
              <div className="text-right font-bold tabular-nums text-text-primary text-sm">
                {ft !== null ? `${ft.toFixed(1)}ft` : '—'}
              </div>
              <div className="text-right text-xs text-text-secondary tabular-nums">{fmtSec(c.tp)}</div>
              {/* Energy bar / "not surfable" filler — desktop only. On
                  mobile we hide this cell entirely so the grid collapses
                  to 5 columns and the row fits a phone width. */}
              <div className="hidden sm:block">
                {c.windSea ? (
                  <div className="text-text-muted text-xs italic">not surfable</div>
                ) : (
                  <div className="relative h-2 rounded-full bg-ink-800 overflow-hidden">
                    <div
                      className="absolute inset-y-0 left-0 rounded-full"
                      style={{
                        width: `${Math.max(4, fraction * 100)}%`,
                        background: barColor,
                      }}
                    />
                  </div>
                )}
              </div>
              <div className="text-right text-xs text-text-muted tabular-nums whitespace-nowrap">
                {c.windSea ? (
                  // Mobile rolls "not surfable" into this cell since the
                  // bar slot is hidden; desktop already labels it there
                  // so this cell stays empty above sm.
                  <span className="italic sm:hidden">not surfable</span>
                ) : (
                  `${(fraction * 100).toFixed(0)}%`
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
