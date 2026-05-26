'use client';

import { useMemo, useState } from 'react';

// Interactive star-rating decomposition. Same swell across all
// scenarios; user toggles wind / tide / direction and watches the
// rating recompute as a product of three multipliers.

type WindVal = 'offshore' | 'cross' | 'onshore';
type TideVal = 'right' | 'wrong';
type DirVal = 'onaxis' | 'offaxis';

const MULTS = {
  wind: { offshore: 1.0, cross: 0.62, onshore: 0.28 } as Record<WindVal, number>,
  tide: { right: 1.0, wrong: 0.55 } as Record<TideVal, number>,
  dir: { onaxis: 1.0, offaxis: 0.42 } as Record<DirVal, number>,
};

function verdictFor(r: number) {
  if (r >= 4.5) return "Don't miss it";
  if (r >= 3.5) return 'Worth checking';
  if (r >= 2.5) return 'Marginal';
  if (r >= 1.5) return "Don't drive";
  return 'Skip it';
}

function SegBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`py-2 px-3 border rounded-md text-sm transition-colors ${
        active
          ? 'bg-blue-50 border-blue-600 text-blue-700 font-medium'
          : 'border-ink-600 text-text-secondary hover:bg-slate-50'
      }`}
    >
      {children}
    </button>
  );
}

export function StarRatingDecomposition() {
  const [wind, setWind] = useState<WindVal>('offshore');
  const [tide, setTide] = useState<TideVal>('right');
  const [dir, setDir] = useState<DirVal>('onaxis');

  const rating = useMemo(
    () => 5.0 * MULTS.wind[wind] * MULTS.tide[tide] * MULTS.dir[dir],
    [wind, tide, dir],
  );
  const clipWidth = (rating / 5) * 160;

  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <div className="text-xs text-text-muted text-center pb-2">
        <span className="font-mono text-text-primary font-medium">4 ft @ 14 s · WNW</span>{' '}
        — same swell across all scenarios
      </div>

      <div className="p-5 bg-slate-50 rounded-md flex items-center justify-between gap-4 flex-wrap mb-4">
        <div className="flex items-center gap-4">
          <svg width="160" height="32" viewBox="0 0 160 32" role="img" aria-label="Star rating">
            <defs>
              <clipPath id="rating-clip-srd">
                <rect x="0" y="0" width={clipWidth.toFixed(1)} height="32" />
              </clipPath>
              <g id="star-row-srd">
                <path d="M16,3 L19.55,12.5 L30,13.2 L21.8,19.83 L24.39,29.8 L16,24.32 L7.61,29.8 L10.2,19.83 L2,13.2 L12.45,12.5 Z" />
                <path d="M48,3 L51.55,12.5 L62,13.2 L53.8,19.83 L56.39,29.8 L48,24.32 L39.61,29.8 L42.2,19.83 L34,13.2 L44.45,12.5 Z" />
                <path d="M80,3 L83.55,12.5 L94,13.2 L85.8,19.83 L88.39,29.8 L80,24.32 L71.61,29.8 L74.2,19.83 L66,13.2 L76.45,12.5 Z" />
                <path d="M112,3 L115.55,12.5 L126,13.2 L117.8,19.83 L120.39,29.8 L112,24.32 L103.61,29.8 L106.2,19.83 L98,13.2 L108.45,12.5 Z" />
                <path d="M144,3 L147.55,12.5 L158,13.2 L149.8,19.83 L152.39,29.8 L144,24.32 L135.61,29.8 L138.2,19.83 L130,13.2 L140.45,12.5 Z" />
              </g>
            </defs>
            <use href="#star-row-srd" fill="#CBD5E1" />
            <g clipPath="url(#rating-clip-srd)">
              <use href="#star-row-srd" fill="#F59E0B" />
            </g>
          </svg>
          <div className="text-4xl font-medium font-mono text-text-primary min-w-[80px] leading-none">
            {rating.toFixed(1)}
          </div>
        </div>
        <div className="text-sm text-text-secondary italic">{verdictFor(rating)}</div>
      </div>

      <div className="text-xs text-text-muted text-center mb-3 tracking-wide">
        Toggle the conditions below to see how the rating changes
      </div>

      <div className="mb-3">
        <div className="text-sm font-medium text-text-primary mb-1.5">Wind at the beach</div>
        <div className="grid grid-cols-3 gap-1.5">
          <SegBtn active={wind === 'offshore'} onClick={() => setWind('offshore')}>
            Light offshore
          </SegBtn>
          <SegBtn active={wind === 'cross'} onClick={() => setWind('cross')}>
            Cross-shore
          </SegBtn>
          <SegBtn active={wind === 'onshore'} onClick={() => setWind('onshore')}>
            Onshore
          </SegBtn>
        </div>
      </div>

      <div className="mb-3">
        <div className="text-sm font-medium text-text-primary mb-1.5">Tide</div>
        <div className="grid grid-cols-2 gap-1.5">
          <SegBtn active={tide === 'right'} onClick={() => setTide('right')}>
            Good tide for spot
          </SegBtn>
          <SegBtn active={tide === 'wrong'} onClick={() => setTide('wrong')}>
            Wrong tide
          </SegBtn>
        </div>
      </div>

      <div className="mb-1">
        <div className="text-sm font-medium text-text-primary mb-1.5">Swell direction</div>
        <div className="grid grid-cols-2 gap-1.5">
          <SegBtn active={dir === 'onaxis'} onClick={() => setDir('onaxis')}>
            Lined up with spot
          </SegBtn>
          <SegBtn active={dir === 'offaxis'} onClick={() => setDir('offaxis')}>
            Hits at an angle
          </SegBtn>
        </div>
      </div>
    </div>
  );
}
