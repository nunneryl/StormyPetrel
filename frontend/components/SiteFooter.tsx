import Link from 'next/link';
import { WaveGlyph } from './Logo';

export function SiteFooter() {
  return (
    <footer className="mt-16 border-t border-ink-600 bg-ink-900">
      <div className="mx-auto max-w-7xl px-4 py-8 grid gap-8 md:grid-cols-[1.1fr_1fr_auto] md:items-start text-sm">
        {/* Left: brand + tagline */}
        <div>
          <div className="flex items-center gap-2 text-text-primary">
            <WaveGlyph className="text-cyan-500" size={20} />
            <span className="font-bold tracking-tightish">Stormy Petrel</span>
          </div>
          <p className="mt-2 text-text-secondary leading-relaxed">
            Free surf forecasts · No paywall, no ads.
          </p>
        </div>

        {/* Center: data sources */}
        <div>
          <div className="text-text-secondary uppercase tracking-widest2 text-[11px] mb-2">
            Data sources
          </div>
          <ul className="grid grid-cols-1 gap-1 text-text-secondary">
            <li>
              <a
                href="https://polar.ncep.noaa.gov/nwps/"
                target="_blank"
                rel="noreferrer"
                className="hover:text-cyan-600"
              >
                NWPS
              </a>{' '}
              — nearshore wave model
            </li>
            <li>
              <a
                href="https://polar.ncep.noaa.gov/waves/"
                target="_blank"
                rel="noreferrer"
                className="hover:text-cyan-600"
              >
                WAVEWATCH III
              </a>{' '}
              — spectral swell
            </li>
            <li>
              <a
                href="https://rapidrefresh.noaa.gov/hrrr/"
                target="_blank"
                rel="noreferrer"
                className="hover:text-cyan-600"
              >
                HRRR
              </a>{' '}
              — 3 km wind
            </li>
            <li>
              <a
                href="https://www.ndbc.noaa.gov/"
                target="_blank"
                rel="noreferrer"
                className="hover:text-cyan-600"
              >
                NDBC
              </a>{' '}
              — realtime buoys
            </li>
            <li>
              <a
                href="https://tidesandcurrents.noaa.gov/"
                target="_blank"
                rel="noreferrer"
                className="hover:text-cyan-600"
              >
                CO-OPS
              </a>{' '}
              — tide predictions
            </li>
          </ul>
        </div>

        {/* Right: about / how / blog / github */}
        <div>
          <div className="text-text-secondary uppercase tracking-widest2 text-[11px] mb-2">
            About
          </div>
          <ul className="space-y-1.5">
            <li>
              <Link href="/about" className="text-text-secondary hover:text-cyan-600">
                About
              </Link>
            </li>
            <li>
              <Link
                href="/blog/methodology"
                className="text-text-secondary hover:text-cyan-600"
              >
                How forecasts work
              </Link>
            </li>
            <li>
              <Link href="/blog" className="text-text-secondary hover:text-cyan-600">
                Blog
              </Link>
            </li>
            <li>
              <a
                href="https://github.com/nunneryl/StormyPetrel"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-text-secondary hover:text-cyan-600"
              >
                <GitHubIcon /> GitHub
              </a>
            </li>
          </ul>
          <p className="mt-3 text-text-muted text-xs">
            Forecasts refresh every 8 h · buoys every 3 h
          </p>
        </div>
      </div>
    </footer>
  );
}

function GitHubIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.39 7.86 10.91.58.1.79-.25.79-.55v-1.95c-3.2.7-3.88-1.54-3.88-1.54-.52-1.32-1.27-1.67-1.27-1.67-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.02 1.76 2.69 1.25 3.34.96.1-.74.4-1.25.72-1.54-2.55-.29-5.24-1.27-5.24-5.65 0-1.25.45-2.27 1.18-3.07-.12-.29-.51-1.46.11-3.05 0 0 .96-.31 3.15 1.17a10.96 10.96 0 0 1 5.74 0c2.19-1.48 3.15-1.17 3.15-1.17.62 1.59.23 2.76.11 3.05.74.8 1.18 1.82 1.18 3.07 0 4.39-2.69 5.36-5.25 5.64.41.36.78 1.07.78 2.16v3.2c0 .31.21.66.8.55C20.21 21.39 23.5 17.07 23.5 12 23.5 5.65 18.35.5 12 .5z" />
    </svg>
  );
}
