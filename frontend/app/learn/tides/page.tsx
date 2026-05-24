import type { Metadata } from 'next';
import Link from 'next/link';
import { TideBreakerVisualizer } from '@/components/learn/TideBreakerVisualizer';
import { BreakTypeTideWindows } from '@/components/learn/BreakTypeTideWindows';
import { TideSessionWindow } from '@/components/learn/TideSessionWindow';
import { siteUrl } from '@/lib/site-url';

export const revalidate = 86400;

const DESCRIPTION =
  'How tide moves the depth at your break — and why a foot of swing reshapes the wave. Why reef breaks like mid tide, points are forgiving, beach breaks want incoming mid, and shorebreaks need low. Plus how the rule of twelfths governs your session window.';

export const metadata: Metadata = {
  title: { absolute: 'Tides and Surfing: Why Depth Changes Everything | Stormy Petrel' },
  description: DESCRIPTION,
  alternates: { canonical: '/learn/tides' },
  openGraph: {
    title: 'Tides and Surfing: Why Depth Changes Everything | Stormy Petrel',
    description: DESCRIPTION,
    type: 'article',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Tides and Surfing: Why Depth Changes Everything | Stormy Petrel',
    description: DESCRIPTION,
  },
};

export default function TidesArticle() {
  const base = siteUrl();
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: 'Tides and Surfing: Why Depth Changes Everything',
    description: DESCRIPTION,
    url: `${base}/learn/tides`,
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
          Learn · 8 min read
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          Tides and Surfing: Why Depth Changes Everything
        </h1>
        <p className="mt-3 text-base sm:text-lg text-text-secondary leading-relaxed">
          How tide moves the depth at your break — and why a foot of swing
          reshapes the wave.
        </p>
      </header>

      <div className="space-y-6 text-base sm:text-lg text-text-primary leading-relaxed">
        <p>
          You can have the same swell at the same break and walk down to the
          water three hours later and find a different wave. Almost always,
          the tide has changed. This session is about why that matters: the
          tide moves the water level up and down at your break, and that
          depth decides how a wave actually breaks.
        </p>

        <SectionHeading>What tides actually are</SectionHeading>

        <p>
          Tides happen because the moon&rsquo;s gravity pulls on the side of
          Earth facing it slightly more strongly than the side facing away.
          That <em>difference</em> is what raises and lowers sea level, not
          gravity itself. The sun does the same thing on a smaller scale,
          about half the lunar effect. Earth rotates underneath these
          &ldquo;tidal bulges&rdquo; and a given coastline passes through
          high and low water roughly twice per day on most coasts.
        </p>

        <p>
          Why &ldquo;most coasts&rdquo; and not all? Because real ocean
          basins are complicated. Some areas get two highs and two lows per
          day (semidiurnal). Others get just one tide a day (diurnal). Many
          places land somewhere in between (mixed: two highs and two lows
          but unequal in height). This is why Galveston has a one-foot daily
          tide while the head of the Bay of Fundy has a fifty-foot range —
          same moon, completely different local response.
        </p>

        <SectionHeading>How tide reshapes the wave</SectionHeading>

        <p>
          A wave breaks when its height reaches roughly 78% of the local
          water depth. This is the McCowan breaker criterion from 1894 and
          it&rsquo;s still the right number to keep in your head. A 4-foot
          wave needs about 5 feet of water under it to stay vertical. Less
          than that and it&rsquo;s already broken; more and it&rsquo;s a
          swell that hasn&rsquo;t peaked yet.
        </p>

        <p>
          Push the water depth at the break down a couple of feet with a
          falling tide and you&rsquo;ve shifted the breakpoint. The same
          swell now breaks farther out, on a steeper part of the bottom, and
          harder. The wave that was a soft shoulder at high tide becomes a
          hollow barrel at low. Push the depth back up two feet and the same
          swell breaks on a gentler part of the bottom and ends up softer
          and mushier.
        </p>

        <p>
          Tide also changes <em>how</em> the wave breaks once it does. The
          combination of bottom slope and wave steepness decides whether a
          wave <strong>spills</strong> (foam crumbling down the face),{' '}
          <strong>plunges</strong> (the classic clean barrel), or{' '}
          <strong>surges</strong> (runs up the slope without really
          breaking, or what surfers describe as the wave &ldquo;backing
          off&rdquo;). Tide changes both inputs at once. Depth changes the
          effective slope the wave &ldquo;sees,&rdquo; and it also changes
          the height-to-depth ratio at breaking. So a reef that throws
          plunging barrels at mid tide can go fat and spilling at high tide
          as the depth grows.
        </p>

        <p>This is why a foot of tide change can completely re-shape your home break.</p>

        <TideBreakerVisualizer />

        <SectionHeading>Why different breaks like different tides</SectionHeading>

        <p>Three rough categories, each with a different tide story:</p>

        <p>
          <strong>Reef breaks</strong> sit on fixed, often steep underwater
          terrain (bathymetry). Pipeline&rsquo;s main reef is in 6&ndash;10
          feet of water and rises sharply toward shore. With only a
          1.3-foot mean tidal range in Hawaii, even a foot of swing changes
          the breaker criterion meaningfully. At extreme low, the reef is
          too shallow and waves close out across exposed coral. At extreme
          high, the swell rolls over the reef without finding enough depth
          contrast to peak. The Goldilocks window, moderate and often
          rising mid tide, is when Pipe fires. Same physics governs
          Teahupoo, Cloudbreak, Mavericks (in a much larger absolute tide
          range), and basically every shallow reef.
        </p>

        <p>
          <strong>Point breaks</strong> are long, gently angled features. A
          one- or two-foot tide change shifts the breakpoint laterally
          along the point rather than chopping the wave in half. Malibu
          works through almost any tide. Rincon is more tide-sensitive than
          people think. It&rsquo;s best around mid-to-low, with the tube
          sections appearing at low.
        </p>

        <p>
          <strong>Beach breaks</strong> ride on sandbars that themselves
          move. Tide selects which bar the wave breaks on. Low tide and the
          outside bar is shallowest, so the wave breaks far out, often
          closing into a deeper inside trough. High tide and the outside
          bar is too deep to break on; the wave reforms on the inner
          shorebreak. The classic beach-break sweet spot is the incoming
          mid tide: enough water for the outside bar to deliver a clean
          wall, enough rising water to carry the broken wave across to the
          inside bar without burying it.
        </p>

        <p>
          <strong>Shorebreak slabs</strong> like The Wedge or Sandy&rsquo;s
          are the extreme version: a sudden depth transition right at the
          shoreline. They want low tide, when the depth ratio is most
          violent.
        </p>

        <BreakTypeTideWindows />

        <SectionHeading>Spring tides, neap tides, and timing the swing</SectionHeading>

        <p>
          The moon goes from new to full back to new in 29.5 days. Twice
          per cycle, the moon, sun, and Earth line up and you get a{' '}
          <strong>spring tide</strong>, the biggest tidal range of the
          cycle, roughly 20% above the monthly mean. At first and last
          quarter moons, the sun and moon pull at right angles and partly
          cancel, giving you a <strong>neap tide</strong>, roughly 20%
          below mean.
        </p>

        <p>
          The moon&rsquo;s orbit is also slightly elliptical, so its
          distance from Earth varies. When the closest-approach point
          (perigee) lines up with a spring tide, six to eight times a year,
          you get a <strong>king tide</strong>, with a few extra inches at
          high water and a symmetric drop at low.
        </p>

        <p>
          For surfers this matters because on spring tides the tide swings
          faster (same six hours, bigger range), so optimal-tide windows
          are narrower and conditions change more quickly. Reef trips
          benefit from spring weeks because the mid-tide window is sharper.
          Long-session point trips don&rsquo;t care.
        </p>

        <p>
          The tide also doesn&rsquo;t rise in a straight line. It follows a
          curve. The middle half of the vertical swing happens in the
          middle third of the cycle, with the tide barely moving near the
          top and bottom and racing through the middle. This is why a spot
          with a tight tide window fires for about two hours at the top or
          bottom (when the tide is barely moving), and why current-driven
          spots like Sebastian Inlet are strongest mid-cycle when water is
          actually moving.
        </p>

        <TideSessionWindow />

        <SectionHeading>How to use this</SectionHeading>

        <ol className="list-decimal pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>Find your spot&rsquo;s tide preference first.</strong>{' '}
            Note whether it prefers low, mid, high, incoming, or outgoing,
            and whether the window is narrow (a reef, 1&ndash;2 hours) or
            wide (a point, 4+ hours).
          </li>
          <li>
            <strong>Use NOAA tide predictions</strong> at{' '}
            <a
              href="https://tidesandcurrents.noaa.gov/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-cyan-600 hover:underline"
            >
              tidesandcurrents.noaa.gov
            </a>{' '}
            for high-stakes calls. Most commercial apps use NOAA&rsquo;s
            underlying data anyway, but error in the rare cases comes from
            out-of-date constants or wrong subordinate-station offsets.
          </li>
        </ol>

        <p>
          The forecast question for tides is simpler than for wind or
          swell: tide predictions are deterministic decades in advance. The
          only real question is whether your session window aligns with
          your spot&rsquo;s preferred tide window. Match those two and
          you&rsquo;ve removed one variable from the equation.
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
          Battjes, J.A. (1974). Surf similarity. Proceedings of the 14th
          Coastal Engineering Conference, 466&ndash;480.
        </p>
        <p className="mb-2">
          Doodson, A.T. (1921). The harmonic development of the
          tide-generating potential. Proceedings of the Royal Society A,
          100, 305&ndash;329.
        </p>
        <p className="mb-2">
          Egbert, G.D. &amp; Erofeeva, S.Y. (2002). Efficient inverse
          modeling of barotropic ocean tides. Journal of Atmospheric and
          Oceanic Technology, 19, 183&ndash;204.
        </p>
        <p className="mb-2">
          Garrett, C. (1972). Tidal resonance in the Bay of Fundy and Gulf
          of Maine. Nature, 238, 441&ndash;443.
        </p>
        <p className="mb-2">
          McCowan, J. (1894). On the highest wave of permanent type.
          Philosophical Magazine, 5(38), 351&ndash;358.
        </p>
        <p className="mb-2">
          Pugh, D. &amp; Woodworth, P. (2014). Sea-Level Science:
          Understanding Tides, Surges, Tsunamis and Mean Sea-Level Changes.
          Cambridge University Press.
        </p>
        <p className="mb-2">
          USACE Coastal Engineering Manual (EM 1110-2-1100), Part II, Ch.
          4: Surf Zone Hydrodynamics.
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
