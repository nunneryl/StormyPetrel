import type { Metadata } from 'next';
import Link from 'next/link';
import { CONTACT_EMAIL, mailtoHref } from '@/lib/contact';

export const revalidate = 86400;
// Static, and regenerated on the revalidate period where there is one: the root layout reads the
// spots past the Data Cache (lib/queries.ts fetchAllSpots), which would otherwise render this
// page on every request.
export const dynamic = 'force-static';

/** Change this whenever the terms change. */
const EFFECTIVE = '24 November 2026';

export const metadata: Metadata = {
  title: { absolute: 'Terms | Stormy Petrel' },
  description: 'Forecasts are estimates, the ocean is dangerous, and the data comes from NOAA and CDIP.',
  alternates: { canonical: '/terms' },
};

const A = 'text-cyan-600 hover:underline';

export default function TermsPage() {
  return (
    <article className="mx-auto max-w-[700px] px-4 sm:px-6 py-10 sm:py-14">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">Legal</div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">Terms</h1>
        <p className="mt-2 text-sm text-text-secondary">Effective {EFFECTIVE}</p>
      </header>

      <div className="space-y-5 text-base text-text-primary leading-relaxed">
        <p>By using Stormy Petrel you agree to these terms. They&rsquo;re short.</p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Forecasts are estimates</h2>
        <p>
          Everything here comes from computer models and instruments. Models can be wrong,
          sometimes badly, and buoys and tide stations can go quiet or report late. Star ratings
          are our interpretation, not a guarantee of what you&rsquo;ll find.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">The ocean is dangerous</h2>
        <p>
          Surfing can cause serious injury or death. Check conditions yourself at the beach before
          you go in, know your limits, and follow lifeguards, posted warnings and National Weather
          Service alerts. Never rely on this site alone to decide whether it&rsquo;s safe.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">No warranty, no liability</h2>
        <p>
          The site is provided &ldquo;as is&rdquo;, without any promise that it&rsquo;s accurate,
          complete or available. To the extent the law allows, we aren&rsquo;t liable for any loss,
          damage or injury that comes from using it.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Data and credits</h2>
        <ul className="list-disc pl-5 space-y-1.5">
          <li>
            Wave, wind and tide data from NOAA: the Nearshore Wave Prediction System, WAVEWATCH III
            and HRRR (NCEP), buoys from the National Data Buoy Center, and tides from CO-OPS. NOAA
            doesn&rsquo;t endorse this site.
          </li>
          <li>
            California nearshore data courtesy of{' '}
            <a href="https://cdip.ucsd.edu/" target="_blank" rel="noreferrer" className={A}>
              CDIP, Scripps Institution of Oceanography
            </a>
            .
          </li>
          <li>
            Maps &copy;{' '}
            <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className={A}>
              OpenStreetMap contributors
            </a>{' '}
            and{' '}
            <a href="https://carto.com/attributions" target="_blank" rel="noreferrer" className={A}>
              CARTO
            </a>
            .
          </li>
          <li>Live cams belong to their operators, who are credited beside each cam.</li>
        </ul>
        <p>
          How we turn the data into a forecast is in{' '}
          <Link href="/blog/methodology" className={A}>how our forecasts work</Link>.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Changes</h2>
        <p>If we change these terms, we&rsquo;ll update the date at the top.</p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Contact</h2>
        <p>
          <a href={mailtoHref('Terms question')} className={A}>{CONTACT_EMAIL}</a>
          . See also our <Link href="/privacy" className={A}>privacy policy</Link>.
        </p>
      </div>
    </article>
  );
}
