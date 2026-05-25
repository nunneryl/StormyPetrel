import type { Metadata } from 'next';
import Link from 'next/link';
import { BuoyReportDecoder } from '@/components/learn/BuoyReportDecoder';
import { SeaStateTriad } from '@/components/learn/SeaStateTriad';
import { BuoyMap } from '@/components/learn/BuoyMap';
import { siteUrl } from '@/lib/site-url';

export const revalidate = 86400;

const DESCRIPTION =
  'How to read an NDBC buoy report the way a forecaster does. What WVHT, DPD, APD, and MWD actually mean, why the gap between DPD and APD is the most useful single diagnostic, why you should always read two buoys, and how to calculate lead time from a deep-water station.';

export const metadata: Metadata = {
  title: { absolute: 'How to Read a Buoy Report | Stormy Petrel' },
  description: DESCRIPTION,
  alternates: { canonical: '/learn/buoys' },
  openGraph: {
    title: 'How to Read a Buoy Report | Stormy Petrel',
    description: DESCRIPTION,
    type: 'article',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'How to Read a Buoy Report | Stormy Petrel',
    description: DESCRIPTION,
  },
};

export default function BuoysArticle() {
  const base = siteUrl();
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: 'How to Read a Buoy Report',
    description: DESCRIPTION,
    url: `${base}/learn/buoys`,
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
          Learn · 10 min read
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          How to Read a Buoy Report
        </h1>
        <p className="mt-3 text-base sm:text-lg text-text-secondary leading-relaxed">
          NDBC looks like alphabet soup at first. Ten minutes from now,
          you&rsquo;ll read it like a forecaster.
        </p>
      </header>

      <div className="space-y-6 text-base sm:text-lg text-text-primary leading-relaxed">
        <p>
          The NDBC website looks like alphabet soup at first. WVHT, DPD, APD,
          MWD — what are you even looking at? Stick with me for ten minutes
          and you&rsquo;ll come out the other side reading the buoy like a
          forecaster does, and probably catching things your forecast app
          glosses over.
        </p>

        <SectionHeading>What a buoy is actually doing</SectionHeading>

        <p>
          A wave buoy is a floating platform tethered to the seafloor in
          deep water. It moves up and down (and side to side on directional
          ones) continuously as waves pass underneath. Every 20 minutes, it
          sends a summary of how it&rsquo;s been moving over that window.
          That summary becomes the numbers you see on the NDBC page.
        </p>

        <p>
          The buoy isn&rsquo;t measuring &ldquo;waves&rdquo; the way
          you&rsquo;d measure with a ruler. It&rsquo;s measuring its own
          motion. From that motion, the onboard computer figures out how
          big the waves are, how often they&rsquo;re passing, and (on
          directional buoys) which way they&rsquo;re coming from.
        </p>

        <SectionHeading>The four numbers that matter</SectionHeading>

        <p>
          Every NDBC report has these four headline numbers. They&rsquo;re
          all derived from the buoy&rsquo;s motion record. Here&rsquo;s
          what each one actually tells you:
        </p>

        <p>
          <strong>WVHT (significant wave height).</strong> How big the
          waves are. Specifically, it&rsquo;s a measure of &ldquo;what
          waves you&rsquo;d actually notice&rdquo; — not every tiny ripple,
          but the biggest third of what&rsquo;s passing under the buoy. If
          a friend on the buoy described the conditions, this is roughly
          the size they&rsquo;d give you.
        </p>

        <p>
          <strong>DPD (dominant period).</strong> The number of seconds
          between the most energetic waves. The buoy picks out the loudest
          rhythm in the water and reports its period. If most of the
          energy is in long, organized groundswell, the DPD will be long —
          12 seconds, 16 seconds, even 20+. If most of the energy is
          short, choppy wind waves, the DPD will be short — 4 to 7 seconds.
        </p>

        <p>
          <strong>APD (average period).</strong> Same idea as DPD, but
          averaged across <em>everything</em> in the water — the dominant
          rhythm, the chop on top, the cross-swells, all of it. This is
          the key: DPD picks out the loudest rhythm, APD averages all of
          them.
        </p>

        <p>
          <strong>MWD (mean wave direction).</strong> The compass direction
          the dominant waves are coming from. 270° is straight west, 180°
          is south, 90° is east.
        </p>

        <p>
          That&rsquo;s it. Every other &ldquo;swell&rdquo; and &ldquo;wind
          wave&rdquo; number on the page is derived from these.
        </p>

        <BuoyReportDecoder />

        <SectionHeading>The gap between DPD and APD is the most useful thing on the page</SectionHeading>

        <p>
          This pairing takes a minute to understand, but once it clicks
          you&rsquo;ll start using it on every check.
        </p>

        <p>
          Think of DPD as the loudest drummer in the ocean and APD as the
          average of every drummer playing. If only one drummer is playing,
          those two numbers will be close to each other — one rhythm, one
          period. If five drummers are playing different rhythms at once,
          the DPD picks the loudest, but the APD pulls toward all of them.
        </p>

        <p>In wave terms:</p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>DPD and APD close together</strong> (e.g., DPD 16 s, APD
            13 s): the ocean has one organized rhythm. A single clean
            groundswell is carrying most of the energy. At the beach,
            you&rsquo;ll see smooth wave faces and good lines.
          </li>
          <li>
            <strong>DPD and APD far apart</strong> (e.g., DPD 16 s, APD 7 s):
            a long-period swell exists, but most of the energy is in
            short-period wind chop sitting on top. The headline DPD looks
            great. The reality at the beach will be bumpy and disorganized.
          </li>
          <li>
            <strong>Both DPD and APD short</strong> (e.g., DPD 6 s, APD 5 s):
            there&rsquo;s no groundswell. Everything in the water is local
            wind chop. Not surf.
          </li>
        </ul>

        <p>
          Rule of thumb: if APD is within 2 seconds of DPD, the swell is
          clean. If APD is half of DPD or less, expect bumps.
        </p>

        <p>
          This is why WVHT alone doesn&rsquo;t tell you whether a spot will
          fire. A 6-foot WVHT with DPD 14 s and APD 12 s is gold. A 6-foot
          WVHT with DPD 14 s and APD 6 s is a windy mess. Same headline
          size, completely different sessions.
        </p>

        <SeaStateTriad />

        <SectionHeading>Read the breakdown, not just the headline</SectionHeading>

        <p>
          The four bulk numbers are summaries. NDBC also publishes the full
          breakdown — how much energy is sitting at each different wave
          period. On the NDBC page it&rsquo;s called the &ldquo;spectral
          wave data&rdquo; or &ldquo;spectral plot.&rdquo; That breakdown
          is the truth; the bulk numbers are abbreviations of it.
        </p>

        <p>
          NDBC also automatically separates the breakdown into
          &ldquo;swell&rdquo; and &ldquo;wind waves&rdquo; and reports them
          separately. So you might see a 6-foot total WVHT broken into 4 ft
          of 14-second swell + 2 ft of 6-second wind chop. That tells you
          exactly what&rsquo;s in the water — much more useful than the
          single headline number.
        </p>

        <p>
          When something looks weird on the bulk parameters, click through
          to the breakdown. It almost always explains it.
        </p>

        <SectionHeading>Pick two buoys, not one</SectionHeading>

        <p>
          The single biggest mistake in buoy reading is using only one. The
          fix is to read two:
        </p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            A <strong>deep-water buoy</strong> — far offshore, in 200+
            meters of water — shows the raw open-ocean signal. What&rsquo;s
            coming.
          </li>
          <li>
            A <strong>nearshore buoy</strong> — closer to your spot, in
            50&ndash;200 meters of water — shows what&rsquo;s actually
            arriving after the swell bends around islands, refracts over
            the continental shelf, and loses some energy along the way.
          </li>
        </ul>

        <p>Some pairings for major US surf coasts:</p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>SoCal:</strong> NDBC 46086 (San Clemente Basin,
            offshore) + CDIP 067 (Harvest, off Pt Conception) for nearshore.
          </li>
          <li>
            <strong>NorCal:</strong> NDBC 46059 (West California, 250 nm
            offshore) + CDIP 029 (Mavericks) or 158 (Half Moon Bay).
          </li>
          <li>
            <strong>Mid-Atlantic to Northeast:</strong> NDBC 41001 (East
            Hatteras) offshore + NDBC 44025 (Long Island) nearshore.
          </li>
          <li>
            <strong>Southeast:</strong> NDBC 41002 (South Hatteras) + 41010
            (Canaveral East).
          </li>
          <li>
            <strong>Hawaii:</strong> NDBC 51001 (NW Hawaii) + CDIP 098
            (Mokapu Point) or 165 (Hilo).
          </li>
          <li>
            <strong>Pacific Northwest:</strong> NDBC 46005 (West Washington)
            + 46211 (Grays Harbor) or 46248 (Astoria Canyon).
          </li>
        </ul>

        <BuoyMap />

        <SectionHeading>Lead time: how far ahead is the buoy seeing?</SectionHeading>

        <p>
          A swell travels at a predictable speed. The formula surfers care
          about is <strong>Cg ≈ 0.78·T meters per second</strong>, where T
          is the period in seconds.
        </p>

        <p>So for a 16-second swell:</p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>Speed = 0.78 × 16 = 12.5 m/s</li>
          <li>
            A buoy 500 km offshore gives 500,000 / 12.5 / 3600 ≈ 11 hours
            of warning
          </li>
          <li>A buoy 250 km offshore gives about 5.5 hours</li>
        </ul>

        <p>
          Use this to time your check-in. If a long-period swell shows up
          on the SoCal deep-water buoy (46086, ~150 km offshore), the beach
          will see it in 3&ndash;5 hours. If it shows up on the NorCal
          deep-water buoy (46059, ~470 km offshore), you have
          10&ndash;14 hours.
        </p>

        <p>
          Short-period wind chop travels at half the speed of long-period
          swell, which is why local chop almost never arrives intact from a
          distant buoy. Long-period swell is the only useful long-distance
          signal.
        </p>

        <p>
          <strong>Worked example.</strong> NDBC 46086 reports WVHT 8.2 ft,
          DPD 14 s, APD 9 s, MWD 290°. Reading: a real NW groundswell (14
          seconds) is in the water, but the gap between DPD (14) and APD
          (9) means a meaningful wind sea is mixed in. The 8.2 ft WVHT is
          probably more like 5&ndash;6 ft of clean swell with 2&ndash;3 ft
          of wind chop on top. Direction is solid NW. SoCal beaches see it
          in 3&ndash;5 hours.
        </p>

        <SectionHeading>Things to watch out for</SectionHeading>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>DPD flicker.</strong> A buoy reporting &ldquo;DPD 13 s
            … 9 s … 14 s&rdquo; in successive hours isn&rsquo;t seeing
            three swells. It&rsquo;s seeing two swells of similar energy
            and the algorithm flips back and forth on which one is slightly
            bigger. Look at the breakdown.
          </li>
          <li>
            <strong>Stale data.</strong> NDBC buoys go offline. Always
            check the timestamp on the most recent observation. Anything
            older than an hour, don&rsquo;t trust.
          </li>
          <li>
            <strong>Units.</strong> NDBC reports meters and
            meters-per-second. Most surf apps display feet and knots. Times
            are UTC, not your local time.
          </li>
          <li>
            <strong>MWD is the dominant direction, not the average.</strong>{' '}
            A bimodal swell — say a NW component AND a S component — will
            report MWD for whichever is slightly stronger. The other
            won&rsquo;t appear in the bulk parameters.
          </li>
          <li>
            <strong>In big winds, WVHT runs high.</strong> The 3-meter
            discus buoys overestimate height by 5&ndash;10% in winds over
            20 knots. Storm and hurricane numbers are biased high.
          </li>
        </ul>

        <SectionHeading>How to use this</SectionHeading>

        <ol className="list-decimal pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>Read two buoys.</strong> Deep water for what&rsquo;s
            coming, nearshore for what&rsquo;s arriving.
          </li>
          <li>
            <strong>Always look at DPD AND APD.</strong> The gap tells you
            cleanliness.
          </li>
          <li>
            <strong>Click through to the breakdown when something looks
            weird.</strong> It almost always explains it.
          </li>
          <li>
            <strong>Check the timestamp.</strong> Stale data is worse than
            no data.
          </li>
          <li>
            <strong>Compute lead time with Cg ≈ 0.78·T.</strong> A
            long-period swell at a deep-water buoy gives you anywhere from
            3 to 14 hours of warning, depending on distance.
          </li>
        </ol>

        <p>
          The buoy is the most concentrated source of real-time wave
          information you have. Learn to read it directly and you&rsquo;ve
          cut out a layer of forecast interpretation — when an app says
          &ldquo;4 ft @ 14 s,&rdquo; you can now go straight to the buoy
          and verify it, and see what the app left out.
        </p>
      </div>

      <footer className="mt-12 pt-6 border-t border-ink-600">
        <Link href="/learn" className="text-sm text-cyan-600 hover:underline">
          ← All guides
        </Link>
      </footer>

      <section className="mt-10 pt-6 border-t border-ink-600 text-xs text-text-muted leading-relaxed">
        <div className="text-[10px] uppercase tracking-widest2 mb-2">References</div>
        <p className="mb-2">
          Collins, C.O. et al. (2022). Tilt error in NDBC ocean wave height
          records. Journal of Atmospheric and Oceanic Technology 39(7).
        </p>
        <p className="mb-2">
          Earle, M.D. (1996). NDBC Technical Document 96-01: NDBC Wave Data
          Analysis and Processing.
        </p>
        <p className="mb-2">
          Gilhousen, D.B. &amp; Hervey, R. (2001). Improved estimates of
          swell from moored buoys. Proceedings of WAVES 2001, ASCE,
          387&ndash;393.
        </p>
        <p className="mb-2">
          Hanson, J.L. &amp; Phillips, O.M. (2001). Automated analysis of
          ocean surface directional wave spectra. Journal of Atmospheric
          and Oceanic Technology 18(2), 277&ndash;293.
        </p>
        <p className="mb-2">
          Kuik, A.J., van Vledder, G.Ph. &amp; Holthuijsen, L.H. (1988). A
          method for the routine analysis of pitch-and-roll buoy wave data.
          Journal of Physical Oceanography 18(7), 1020&ndash;1034.
        </p>
        <p className="mb-2">
          Longuet-Higgins, M.S., Cartwright, D.E. &amp; Smith, N.D. (1963).
          Observations of the directional spectrum of sea waves using the
          motions of a floating buoy. Ocean Wave Spectra, Prentice-Hall,
          111&ndash;136.
        </p>
        <p className="mb-2">
          NDBC Technical Document 03-01: Handbook of Automated Data Quality
          Control Checks and Procedures.
        </p>
        <p className="mb-2">
          O&rsquo;Reilly, W.C. et al. (2016). The California coastal wave
          monitoring and prediction system. Coastal Engineering 116,
          118&ndash;132.
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
