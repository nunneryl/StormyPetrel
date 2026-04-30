import Link from 'next/link';
import { WaveGlyph } from './Logo';

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-ink-600 bg-ink-900">
      <div className="mx-auto max-w-7xl px-4 py-8 grid gap-6 sm:grid-cols-3 text-sm">
        <div>
          <div className="flex items-center gap-2 text-text-primary">
            <WaveGlyph className="text-cyan-500" size={20} />
            <span className="font-bold tracking-tightish">Stormy Petrel</span>
          </div>
          <p className="mt-2 text-text-muted leading-relaxed">
            Free surf forecasts powered by NOAA / NCEP open data. No paywall, no ads.
          </p>
        </div>
        <div>
          <div className="text-text-secondary uppercase tracking-widest text-xs mb-2">
            Data sources
          </div>
          <ul className="space-y-1 text-text-muted">
            <li>NWPS · nearshore wave model</li>
            <li>WAVEWATCH III (gfswave) · spectral swell</li>
            <li>HRRR · 3 km wind</li>
            <li>NDBC · realtime buoys</li>
            <li>CO-OPS · tide predictions</li>
          </ul>
        </div>
        <div>
          <div className="text-text-secondary uppercase tracking-widest text-xs mb-2">
            About
          </div>
          <ul className="space-y-1.5">
            <li>
              <Link href="/blog/about" className="text-text-secondary hover:text-cyan-400">
                What is Stormy Petrel?
              </Link>
            </li>
            <li>
              <Link href="/blog/methodology" className="text-text-secondary hover:text-cyan-400">
                How our forecasts work
              </Link>
            </li>
            <li>
              <a
                href="https://github.com/nunneryl/StormyPetrel"
                target="_blank"
                rel="noreferrer"
                className="text-text-secondary hover:text-cyan-400"
              >
                GitHub →
              </a>
            </li>
          </ul>
          <p className="mt-3 text-text-muted text-xs">
            Forecast data refreshes every 6 hours. Buoy observations refresh hourly.
          </p>
        </div>
      </div>
    </footer>
  );
}
