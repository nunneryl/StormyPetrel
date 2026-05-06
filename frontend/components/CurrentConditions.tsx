import { CompassArrow } from './CompassArrow';
import { SwellCompass } from './SwellCompass';
import type { Forecast } from '@/lib/types';
import {
  classifyChop, chopBadgeClass, chopLabel,
  classifyWind, windQualityClass, windQualityLabel,
} from '@/lib/ratings';
import {
  degToCardinal,
  fmtFt,
  fmtMph,
  fmtSec,
  pickSwell,
} from '@/lib/formatting';

type Tile = {
  label: string;
  value: string;
  hint?: string | null;
  icon?: React.ReactNode;
  rightSpark?: React.ReactNode;
};

export function CurrentConditions({
  current,
  forecasts,
  offshoreDeg,
}: {
  current: Forecast | null;
  forecasts: Forecast[];
  offshoreDeg: number | null | undefined;
}) {
  const tp = pickSwell(current?.swell_tp ?? null, current?.tp ?? null);
  const dp = pickSwell(current?.swell_dp ?? null, current?.dp ?? null);

  const wQ = classifyWind(current?.wind_dir ?? null, offshoreDeg ?? null);
  const cQ = classifyChop(current?.chop_ratio ?? null);

  // Tide trend over the next 1 hour for the inline arrow indicator.
  const tideTrend = (() => {
    const a = current?.tide_level_ft;
    const b = forecasts[1]?.tide_level_ft;
    if (a === null || a === undefined || b === null || b === undefined) return null;
    if (b > a + 0.05) return 'rising';
    if (b < a - 0.05) return 'falling';
    return 'slack';
  })();

  return (
    <section className="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <BigTile
        label="Face"
        value={fmtFt(current?.face_ft)}
        hint={tp ? `${fmtSec(tp)} period` : null}
      />
      <BigTile
        label="Swell"
        value={degToCardinal(dp)}
        hint={dp !== null && dp !== undefined ? `${dp.toFixed(0)}°` : null}
        icon={<SwellCompass deg={dp} size={40} />}
      />
      <BigTile
        label="Wind"
        value={fmtMph(current?.wind_speed)}
        hint={
          current?.wind_dir !== null && current?.wind_dir !== undefined
            ? `${degToCardinal(current.wind_dir)}`
            : null
        }
        icon={<CompassArrow deg={current?.wind_dir ?? null} size={22} variant="wind" showLabel={false} />}
        badge={
          wQ !== 'unknown' && (
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 ${windQualityClass(wQ)}`}>
              {windQualityLabel(wQ)}
            </span>
          )
        }
      />
      <BigTile
        label="Tide"
        value={
          current?.tide_level_ft !== null && current?.tide_level_ft !== undefined
            ? `${current.tide_level_ft.toFixed(1)} ft`
            : '—'
        }
        hint={tideTrend ?? null}
        icon={
          tideTrend === 'rising' ? (
            <span className="text-cyan-400 text-xl leading-none">↑</span>
          ) : tideTrend === 'falling' ? (
            <span className="text-cyan-400 text-xl leading-none">↓</span>
          ) : null
        }
      />
      <BigTile
        label="Conditions"
        value={chopLabel(cQ)}
        hint={
          current?.chop_ratio !== null && current?.chop_ratio !== undefined
            ? `${(current.chop_ratio * 100).toFixed(0)}% wind sea`
            : null
        }
        badge={
          cQ !== 'unknown' && (
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 ${chopBadgeClass(cQ)}`}>
              {chopLabel(cQ)}
            </span>
          )
        }
      />
    </section>
  );
}

function BigTile({
  label,
  value,
  hint,
  icon,
  badge,
  rightSpark,
}: Tile & { badge?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-3.5">
      <div className="flex items-start justify-between mb-2">
        <span className="text-[10px] uppercase tracking-widest2 text-text-secondary">
          {label}
        </span>
        {badge}
      </div>
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-2xl font-bold text-text-primary tabular-nums tracking-tightish">
          {value}
        </span>
      </div>
      {(hint || rightSpark) && (
        <div className="mt-1 flex items-center justify-between text-xs text-text-muted">
          <span>{hint}</span>
          {rightSpark}
        </div>
      )}
    </div>
  );
}
