import type { Metadata } from 'next';
import Link from 'next/link';
import { WindOnWave } from '@/components/learn/WindOnWave';
import { WindCompass } from '@/components/learn/WindCompass';
import { SeaBreezeCycle } from '@/components/learn/SeaBreezeCycle';
import { siteUrl } from '@/lib/site-url';

export const revalidate = 86400;

const DESCRIPTION =
  'Why offshore wind makes good waves and onshore ruins them, the daily sea breeze cycle, regional winds like Santa Anas and trades, and how to read a wind forecast.';

export const metadata: Metadata = {
  title: { absolute: 'How Wind Makes or Breaks a Surf Session | Stormy Petrel' },
  description: DESCRIPTION,
  alternates: { canonical: '/learn/wind' },
  openGraph: {
    title: 'How Wind Makes or Breaks a Surf Session | Stormy Petrel',
    description: DESCRIPTION,
    type: 'article',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'How Wind Makes or Breaks a Surf Session | Stormy Petrel',
    description: DESCRIPTION,
  },
};

export default function WindArticle() {
  const base = siteUrl();
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: 'How Wind Makes or Breaks a Surf Session',
    description: DESCRIPTION,
    url: `${base}/learn/wind`,
    isPartOf: { '@type': 'WebSite', name: 'Stormy Petrel', url: base },
    author: { '@type': 'Organization', name: 'Stormy Petrel' },
    publisher: { '@type': 'Organization', name: 'Stormy Petrel', url: base },
  };

  return (
    <article className="mx-auto max-w-[720px] px-4 sm:px-6 py-10 sm:py-14">
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav className="mb-4 text-xs text-text-muted">
        <Link href="/learn" className="hover:text-cyan-600">
          ← All guides
        </Link>
      </nav>

      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Learn · 9 min read
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          How Wind Makes or Breaks a Surf Session
        </h1>
        <p className="mt-3 text-base sm:text-lg text-text-secondary leading-relaxed">
          Why offshore wind makes good waves, onshore wind ruins them, and how
          to read the daily forecast.
        </p>
      </header>

      <div className="space-y-6 text-base sm:text-lg text-text-primary leading-relaxed">
        <p>
          You can have head-high swell at the perfect period from the perfect
          direction and the wind can still wreck the session. That&rsquo;s how
          decisive wind is for surf quality. This is the third piece in our
          forecasting series. The{' '}
          <Link href="/learn/swell-period" className="text-cyan-600 hover:underline">
            period article
          </Link>{' '}
          told you whether the wave packs energy. The{' '}
          <Link href="/learn/swell-direction" className="text-cyan-600 hover:underline">
            direction article
          </Link>{' '}
          told you whether that energy reaches your sandbar. This one is about
          whether what arrives is a glossy, surfable face or a foamy mess that
          closes out before you can stand up.
        </p>

        <p>
          Two physical things are happening at once whenever wind blows over
          the ocean near you, and surfers tend to confuse them.{' '}
          <strong>Wind shapes the wave that&rsquo;s already there</strong> —
          it tilts the lip forward or holds it back.{' '}
          <strong>Wind also makes new waves locally</strong>, which is the
          short-period chop sitting on top of your groundswell. Both matter,
          but they&rsquo;re different problems with different solutions.
        </p>

        <SectionHeading>What offshore wind does to a breaking wave</SectionHeading>

        <p>
          In the deep ocean, a 4 ft, 12-second swell travels at about 18 m/s —
          roughly 35 knots. By the time that wave is feeling the bottom in the
          surf zone, it&rsquo;s slowed to about 4 m/s, or 7&ndash;8 knots. A
          12-knot offshore wind is now comparable in magnitude to the
          wave&rsquo;s own phase speed at the breakpoint. That&rsquo;s why
          offshore wind has such an outsized effect on what the wave actually
          does.
        </p>

        <p>
          The mechanism: offshore wind flows up and over the crest of the
          wave, pressing down on the lip just as it&rsquo;s trying to throw
          forward. The wave is allowed to climb to a higher steepness before
          it finally breaks. The lip is <em>held up</em>. Spray gets blown
          backward off the top of the crest — that visible feathering plume
          you see in good surf photos. When the wave finally goes, it pitches
          cleanly into a hollow plunging breaker. Controlled wave-tank
          experiments confirm what surfers have known for a century: wind
          shear measurably changes how steep a wave can get before it breaks.
        </p>

        <p>
          Onshore wind reverses everything. Aligned with the wave&rsquo;s
          direction of travel, it tips the lip forward before the wave has
          time to organize into a clean curl. The crest crumbles. Beach
          breaks under more than about 10 knots onshore stop producing
          peelers and start producing closeouts.
        </p>

        <WindOnWave />

        <SectionHeading>Wind sea: the second problem</SectionHeading>

        <p>
          Onshore wind has a separate, additional cost: it makes its own
          waves locally. The same physics that builds a swell thousands of
          miles away runs locally on the patch of ocean a few tens of
          kilometers off your beach. A 20-knot onshore wind across 30 km of
          fetch builds about a 3-foot, 4-second wind sea in well under an
          hour. That short-period chop sits <em>on top of</em> whatever clean
          groundswell you have, and that&rsquo;s the lumpiness you see when
          you squint at the wave faces.
        </p>

        <p>
          This is why a buoy reading 4-foot waves arriving every 5 seconds
          from the same direction as the local wind is almost certainly
          reporting chop, not surf. The period (how often the waves arrive)
          is too short to be a remote groundswell, and the direction matches
          the local wind. (We&rsquo;ll go deep on reading buoy reports in an
          upcoming article.) Modern NOAA buoys split the signal: they report
          the swell component (height, period, direction) separately from
          the wind-sea component. The swell side is what&rsquo;s worth
          driving for. The wind-sea side tells you what&rsquo;s going to be
          sitting on top of it.
        </p>

        <SectionHeading>The wind speed ladder</SectionHeading>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>Under 5 kt:</strong> Glassy. Faces are perfect.
          </li>
          <li>
            <strong>5&ndash;10 kt:</strong> Light. Clean groomed offshore;
            mushy but rideable onshore.
          </li>
          <li>
            <strong>10&ndash;15 kt:</strong> Moderate. Classic offshore with
            visible spray feathering off the lips; junky on most beach breaks
            under onshore.
          </li>
          <li>
            <strong>15&ndash;25 kt:</strong> Stiff. Offshore this strong holds
            the lip up so long that drops go vertical and late, and paddling
            against it is hard. Onshore: blown out at almost every exposed
            spot.
          </li>
          <li>
            <strong>Above 25 kt:</strong> Generally unsurfable, even when
            offshore.
          </li>
        </ul>

        <p>
          Cross-shore wind is the trickiest case. The lip itself isn&rsquo;t
          being pushed forward or held back, but the wind is still making
          chop and that chop crosses the wave faces at an angle. Surfers
          consistently find cross-shore harder to read than mild onshore.
        </p>

        <p>
          Long-period swells handle more onshore wind than short-period
          swells. Long-period waves carry more of their energy beneath the
          surface, so what&rsquo;s happening up at the surface matters less
          to the overall shape. A 14-second groundswell can still produce
          surfable waves under 10 knots of onshore wind. A 6-second windswell
          falls apart at 4 knots.
        </p>

        <WindCompass />

        <SectionHeading>Why dawn patrol works</SectionHeading>

        <p>
          Water holds heat much better than land — about three to four times
          better. The practical consequence: under the same sunshine, land
          warms up and cools down much faster than the ocean.
        </p>

        <p>
          On a clear summer day, this plays out as a daily wind cycle.
          Overnight, land cools below the ocean&rsquo;s temperature. By
          sunrise, a weak offshore land breeze is blowing — the residual of
          all that nighttime cooling. By mid-morning, the land has heated up
          again, the wind flips, and a sea breeze starts pushing onshore. By
          2&ndash;4 p.m., especially spring through fall, the onshore wind is
          peaking and most exposed beach breaks are textured or blown out.
        </p>

        <p>
          The actionable window: roughly an hour before sunrise to about 9
          a.m. on most U.S. coasts (assuming no big weather system is forcing
          a different pattern), the morning runs offshore-to-light. By 11
          a.m. to noon, the sea breeze is filling in onshore. Plan around
          that and you&rsquo;ll surf clean conditions far more often than
          you&rsquo;ll luck into them.
        </p>

        <SeaBreezeCycle />

        <SectionHeading>Regional winds that override the default</SectionHeading>

        <p>
          Some coasts have specific regional winds that beat the
          synoptic-to-diurnal default and deliver clean conditions when
          neighboring spots are blown out.
        </p>

        <p>
          <strong>Santa Anas</strong> are dry, warm, gusty offshore winds
          driven by high pressure over the Great Basin pushing air through
          gaps in the Transverse Range to the Southern California coast.
          They peak in December. At 10&ndash;20 kt at the coast, Santa Anas
          groom Rincon, Trestles, and the entire SoCal lineup; at 30+ kt
          they overwhelm even a powerful swell.
        </p>

        <p>
          <strong>Diablos</strong> are the Bay Area analog, giving Ocean
          Beach, Mavericks, and Marin offshore from the northeast.
        </p>

        <p>
          <strong>Sundowners</strong> are the Santa Barbara&ndash;specific
          downslope wind on the south side of the Santa Ynez Mountains. A
          recent NOAA field campaign measured peak winds reaching ~49 kt
          sustained with 68 kt gusts in extreme events. Milder Sundowners
          groom Rincon and the SB points hours after the rest of SoCal has
          gone onshore.
        </p>

        <p>
          <strong>Trade winds</strong> blow 80&ndash;95% of summer days and
          50&ndash;80% of winter days in Hawaii. An ENE trade is
          approximately cross-shore at Pipeline (which faces about 340°) and
          lightly offshore at NW-facing reefs — that&rsquo;s why winter
          trades shape rather than ruin North Shore waves. South-facing
          shores (Waikiki, Ala Moana) are offshore from the trade, which is
          why summer south swells at Town break clean. Kona events flip
          everything: South Shore goes onshore, North Shore offshore.
        </p>

        <SectionHeading>How to use this</SectionHeading>

        <ol className="list-decimal pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>Check the hourly wind forecast, not the daily.</strong> A
            morning offshore that turns onshore by 1 p.m. is the most common
            spring/summer pattern on most U.S. coasts.
          </li>
          <li>
            <strong>Read the swell component, not just the combined wave
            height.</strong> A buoy reading 4 ft at 5 seconds onshore is
            chop, not surf.
          </li>
          <li>
            <strong>Calibrate your wind tolerance to the period.</strong>{' '}
            Longer-period swells handle more onshore wind. Rough rule: one
            extra knot of tolerance per second of period above 8.
          </li>
          <li>
            <strong>Drive to the spot whose orientation matches the
            wind.</strong> Most coasts have at least one beach, point, or
            reef whose offshore quadrant aligns with today&rsquo;s wind
            direction. Build that map for your area.
          </li>
        </ol>

        <p>
          The forecast you get from NOAA&rsquo;s High-Resolution Rapid
          Refresh (HRRR) model is good down to about 3 km grid spacing and
          updates every hour out to 18 hours — enough to capture the daily
          sea-breeze cycle and the major wind regimes. It&rsquo;s less
          reliable at the spot level in complex terrain. Trust HRRR for the{' '}
          <em>timing and direction</em> of wind transitions; verify the{' '}
          <em>magnitude</em> against the nearest buoy or anemometer before
          you commit to the drive.
        </p>
      </div>

      <footer className="mt-12 pt-6 border-t border-ink-600">
        <Link href="/learn" className="text-sm text-cyan-600 hover:underline">
          ← All guides
        </Link>
      </footer>

      <section className="mt-10 pt-6 border-t border-ink-600 text-xs text-text-muted leading-relaxed">
        <div className="text-[10px] uppercase tracking-widest2 mb-2">
          References
        </div>
        <p className="mb-2">
          Bowers, R.J. (2018). The Diablo winds of Northern California:
          climatology and numerical simulations. M.S. Thesis, San Jose State
          University.
        </p>
        <p className="mb-2">
          Carvalho, L.M.V., et al. (2024). The Sundowner Winds Experiment
          (SWEX) field campaign overview. Bulletin of the American
          Meteorological Society.
        </p>
        <p className="mb-2">
          Gilhousen, D.B. &amp; Hervey, R. (2001). Improved estimates of
          swell from moored buoys. Proceedings of WAVES 2001, 387&ndash;393.
        </p>
        <p className="mb-2">
          Guzman-Morales, J., et al. (2016). Santa Ana winds of Southern
          California: their climatology, extremes, and behavior. Geophysical
          Research Letters, 43(7), 2827&ndash;2834.
        </p>
        <p className="mb-2">
          Hughes, M. &amp; Hall, A. (2010). Local and synoptic mechanisms
          causing Southern California&rsquo;s Santa Ana winds. Climate
          Dynamics, 34, 847&ndash;857.
        </p>
        <p className="mb-2">
          Miller, S.T.K., et al. (2003). Sea breeze: structure, forecasting,
          and impacts. Reviews of Geophysics, 41(3).
        </p>
        <p className="mb-2">
          Perlin, M., Choi, W., &amp; Tian, Z. (2013). Breaking waves in deep
          and intermediate waters. Annual Review of Fluid Mechanics, 45,
          115&ndash;145.
        </p>
        <p className="mb-2">
          Reul, N., Branger, H., &amp; Giovanangeli, J.-P. (1999). Air flow
          separation over unsteady breaking waves. Physics of Fluids, 11.
        </p>
      </section>
    </article>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="pt-4 text-xl sm:text-2xl font-bold tracking-tightish text-text-primary">
      {children}
    </h2>
  );
}
