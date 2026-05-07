import type { Forecast, Spot } from '@/lib/types';
import { degToCardinal, msToMph } from '@/lib/formatting';

type Match = 'yes' | 'partial' | 'no' | 'unknown';

const ICON: Record<Match, { glyph: string; color: string; bg: string }> = {
  yes:     { glyph: '✓', color: '#15803D', bg: '#DCFCE7' },
  partial: { glyph: '~', color: '#A16207', bg: '#FEF3C7' },
  no:      { glyph: '✗', color: '#B91C1C', bg: '#FEE2E2' },
  unknown: { glyph: '–', color: '#94A3B8', bg: '#F1F5F9' },
};

function evalSwell(
  dp: number | null | undefined,
  arcs: Spot['swell_window_arcs'],
): Match {
  if (dp === null || dp === undefined) return 'unknown';
  if (!arcs || arcs.length === 0) return 'unknown';
  for (const arc of arcs) {
    if (arc.min <= arc.max) {
      if (dp >= arc.min && dp <= arc.max) return 'yes';
    } else {
      // Wrap-around arc (e.g. 350..30 covers north).
      if (dp >= arc.min || dp <= arc.max) return 'yes';
    }
  }
  return 'no';
}

function evalWind(windMult: number | null | undefined): Match {
  if (windMult === null || windMult === undefined) return 'unknown';
  if (windMult >= 0.8) return 'yes';
  if (windMult < 0.6) return 'no';
  return 'partial';
}

function evalTide(tideMult: number | null | undefined): Match {
  if (tideMult === null || tideMult === undefined) return 'unknown';
  if (tideMult >= 0.9) return 'yes';
  if (tideMult < 0.7) return 'no';
  return 'partial';
}

function evalPeriod(tp: number | null | undefined): Match {
  if (tp === null || tp === undefined) return 'unknown';
  if (tp >= 10) return 'yes';
  if (tp >= 7) return 'partial';
  return 'no';
}

function fmtCardinalDeg(deg: number | null | undefined): string {
  if (deg === null || deg === undefined) return '—';
  return `${degToCardinal(deg)} ${Math.round(deg)}°`;
}

function fmtTideNow(
  level: number | null | undefined,
  next: number | null | undefined,
): string {
  if (level === null || level === undefined) return '—';
  let trend = '';
  if (next !== null && next !== undefined) {
    if (next > level + 0.05) trend = ' rising';
    else if (next < level - 0.05) trend = ' falling';
  }
  return `${level.toFixed(1)}ft${trend}`;
}

export function ConditionsMatch({
  spot,
  current,
  next,
}: {
  spot: Spot;
  current: Forecast | null;
  /** The forecast row immediately after `current`, used for tide trend. */
  next: Forecast | null;
}) {
  const swellDp = current?.swell_dp ?? current?.dp ?? null;
  const swellTp = current?.swell_tp ?? current?.tp ?? null;
  const windMph = msToMph(current?.wind_speed ?? null);

  const rows = [
    {
      label: 'Swell',
      now: swellDp !== null ? fmtCardinalDeg(swellDp) : '—',
      ideal:
        spot.optimal_swell_dir !== null
          ? fmtCardinalDeg(spot.optimal_swell_dir)
          : '—',
      match: evalSwell(swellDp, spot.swell_window_arcs),
    },
    {
      label: 'Wind',
      now:
        current?.wind_dir !== null && current?.wind_dir !== undefined
          ? `${degToCardinal(current.wind_dir)} ${windMph !== null ? `${windMph.toFixed(0)} mph` : ''}`.trim()
          : '—',
      ideal:
        spot.offshore_wind_deg !== null
          ? `${degToCardinal(spot.offshore_wind_deg)} offshore`
          : 'offshore',
      match: evalWind(current?.wind_mult ?? null),
    },
    {
      label: 'Tide',
      now: fmtTideNow(current?.tide_level_ft, next?.tide_level_ft),
      ideal: spot.tide_preference ?? 'any',
      match: evalTide(current?.tide_mult ?? null),
    },
    {
      label: 'Period',
      now: swellTp !== null && swellTp !== undefined ? `${swellTp.toFixed(0)}s` : '—',
      ideal: '10s+',
      match: evalPeriod(swellTp),
    },
  ];

  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-4">
      <div className="text-[10px] uppercase tracking-widest2 text-text-secondary mb-3">
        Conditions match
      </div>
      <div className="grid grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_auto] gap-x-3 gap-y-2 items-center text-sm">
        <div />
        <div className="text-[10px] uppercase tracking-widest2 text-text-muted">
          Now
        </div>
        <div className="text-[10px] uppercase tracking-widest2 text-text-muted">
          Ideal
        </div>
        <div />
        {rows.map((r) => {
          const ic = ICON[r.match];
          return (
            <Row key={r.label} label={r.label} now={r.now} ideal={r.ideal} icon={ic} />
          );
        })}
      </div>
    </div>
  );
}

function Row({
  label,
  now,
  ideal,
  icon,
}: {
  label: string;
  now: string;
  ideal: string;
  icon: { glyph: string; color: string; bg: string };
}) {
  return (
    <>
      <span className="text-text-secondary">{label}</span>
      <span className="text-text-primary tabular-nums truncate">{now}</span>
      <span className="text-text-secondary tabular-nums truncate">{ideal}</span>
      <span
        className="inline-flex items-center justify-center w-5 h-5 rounded-full text-xs font-bold shrink-0"
        style={{ background: icon.bg, color: icon.color }}
        aria-hidden
      >
        {icon.glyph}
      </span>
    </>
  );
}
