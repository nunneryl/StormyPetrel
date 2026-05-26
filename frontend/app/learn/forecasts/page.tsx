import type { Metadata } from 'next';
import Link from 'next/link';
import { ForecastPipelineDiagram } from '@/components/learn/ForecastPipelineDiagram';
import { StarRatingDecomposition } from '@/components/learn/StarRatingDecomposition';
import { ForecastSkillCurve } from '@/components/learn/ForecastSkillCurve';
import { siteUrl } from '@/lib/site-url';

export const revalidate = 86400;

const DESCRIPTION =
  'A surf forecast is a stack of nested models: weather model → global wave model → nearshore transformation → wind and tide blend → human overlay → star rating. How each layer works, what star ratings actually mean, and why forecast accuracy drops off after 3 days.';

export const metadata: Metadata = {
  title: { absolute: 'How a Surf Forecast Actually Works | Stormy Petrel' },
  description: DESCRIPTION,
  alternates: { canonical: '/learn/forecasts' },
  openGraph: {
    title: 'How a Surf Forecast Actually Works | Stormy Petrel',
    description: DESCRIPTION,
    type: 'article',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'How a Surf Forecast Actually Works | Stormy Petrel',
    description: DESCRIPTION,
  },
};

export default function ForecastsArticle() {
  const base = siteUrl();
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: 'How a Surf Forecast Actually Works',
    description: DESCRIPTION,
    url: `${base}/learn/forecasts`,
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
          Learn · 11 min read
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          How a Surf Forecast Actually Works
        </h1>
        <p className="mt-3 text-base sm:text-lg text-text-secondary leading-relaxed">
          A surf forecast is a stack of nested models — from a global weather
          simulation down to a forecaster nudging the star rating.
        </p>
      </header>

      <div className="space-y-6 text-base sm:text-lg text-text-primary leading-relaxed">
        <p>
          When you check the forecast at 6 a.m. and see &ldquo;head-high, 3.2
          stars&rdquo; for tomorrow, you&rsquo;re reading the output of a
          24-hour computational relay race. A real storm is brewing thousands
          of miles away. Overnight, a supercomputer simulated where its winds
          will go and how strong they&rsquo;ll be. Those predicted winds got
          fed into a separate model that grew waves on a global grid, then
          sent them traveling across the ocean. The energy was bent and
          shoaled by your local bathymetry. Near the end, a forecaster who
          knows your break nudged the rating up or down.
        </p>

        <p>
          This is the final piece in our forecasting series. It ties together
          the{' '}
          <Link href="/learn/swell-period" className="text-cyan-600 hover:underline">
            period
          </Link>
          ,{' '}
          <Link href="/learn/swell-direction" className="text-cyan-600 hover:underline">
            direction
          </Link>
          ,{' '}
          <Link href="/learn/wind" className="text-cyan-600 hover:underline">
            wind
          </Link>
          ,{' '}
          <Link href="/learn/tides" className="text-cyan-600 hover:underline">
            tides
          </Link>
          , and{' '}
          <Link href="/learn/buoys" className="text-cyan-600 hover:underline">
            buoys
          </Link>{' '}
          articles into a single picture. The big idea: a forecast is a stack
          of nested models, with each layer feeding the next. Knowing how
          they fit together tells you which ones to trust and where errors
          come from.
        </p>

        <SectionHeading>The five layers</SectionHeading>

        <p>
          Every surf forecast on every app runs through roughly the same
          chain. Here&rsquo;s what each layer does.
        </p>

        <SubHeading>Layer 1: The weather model</SubHeading>

        <p>
          Everything starts with wind. Until you know what the wind has been
          doing across an entire ocean basin for the past several days, you
          can&rsquo;t predict any waves.
        </p>

        <p>
          The big two are <strong>GFS</strong> (run by NOAA) and{' '}
          <strong>ECMWF IFS</strong> (run by the European Centre for
          Medium-Range Weather Forecasts). Both run on supercomputers four
          times a day at horizontal resolutions of around 9 to 13 kilometers
          globally. They solve the same physical equations (how air moves on
          a rotating planet, how heat and moisture get carried around) but
          with different numerical schemes and different initial data. ECMWF
          has been consistently more accurate than GFS for most of the last
          two decades.
        </p>

        <p>
          For coastal wind detail in the US, there&rsquo;s also{' '}
          <strong>HRRR</strong> (High-Resolution Rapid Refresh), a 3-km model
          that updates every hour. It captures sea breezes, marine layers,
          and Santa Ana outflows that the bigger global models smooth away.
          HRRR is what tells you whether your dawn-patrol offshore will hold
          until 10 a.m. or get shredded by 8.
        </p>

        <p>
          If the model gets the wind wrong by even a small amount, the wave
          forecast ends up significantly off. A wind error of just a couple
          miles per hour can translate to a 10 to 15 percent error in
          predicted wave height. Most forecast busts trace back to this
          layer first.
        </p>

        <SubHeading>Layer 2: The global wave model</SubHeading>

        <p>
          The wind forecast gets fed into a global wave model. The standard
          in the US is <strong>WAVEWATCH III</strong> (developed at NOAA).
          The European equivalent is <strong>WAM</strong>.
        </p>

        <p>
          These don&rsquo;t predict &ldquo;wave height.&rdquo; They predict
          the full wave <em>spectrum</em>, meaning how much energy exists at
          every wave period and every direction, at every point in the
          ocean. Significant wave height is just a summary of the spectrum.
        </p>

        <p>
          Wave models track four things: waves being generated by wind,
          waves traveling across the ocean, waves redistributing energy
          among themselves through nonlinear interactions, and waves losing
          energy to whitecaps and friction. Global wave models work on a
          grid where each cell is roughly 10 to 15 miles across, which is
          way too coarse to see your specific surf break. They give you the
          open-ocean signal.
        </p>

        <p>
          This is the same model output that powers the buoy partition
          algorithms from the{' '}
          <Link href="/learn/buoys" className="text-cyan-600 hover:underline">
            buoy article
          </Link>
          . When a buoy reports &ldquo;4 ft of 14-second NW swell + 2 ft of
          6-second wind sea,&rdquo; those are partitions of a model
          spectrum.
        </p>

        <SubHeading>Layer 3: The nearshore transformation</SubHeading>

        <p>
          A global wave model knows what&rsquo;s happening offshore, but it
          doesn&rsquo;t know about your point break, your reef, or your
          sandbar. To get from &ldquo;open-ocean spectrum&rdquo; to
          &ldquo;wave height at your spot,&rdquo; you need a nearshore
          transformation.
        </p>

        <p>
          The standard tool is <strong>SWAN</strong>, developed at Delft
          University. SWAN takes the offshore spectrum and propagates it
          through high-resolution bathymetry, meaning the actual underwater
          geography of your coastline. It applies refraction (waves bending
          toward shallower water), shoaling (waves slowing and steepening
          as they hit the shelf), island shadowing, and depth-induced
          breaking.
        </p>

        <p>
          The result is spot-specific. Not &ldquo;this ocean basin has 6 ft
          of 14-second swell&rdquo; but &ldquo;your break should see 4
          ft.&rdquo; This is where two spots ten miles apart can get
          totally different forecasts. Same offshore signal, different
          bathymetry.
        </p>

        <p>
          For California, the <strong>CDIP MOP</strong> system at Scripps
          does this at very high resolution (100m × 100m grid), using
          actual buoy measurements as the initial condition rather than
          just modeled wind. CDIP forecasts are often more accurate than
          the standard chain because they start from observed reality.
        </p>

        <SubHeading>Layer 4: Wind + tide blend</SubHeading>

        <p>
          The wave forecast at this point is just the wave field. Real surf
          depends on wind quality and tide too. The forecast app blends:
        </p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>Local wind forecast (HRRR in the US, or ECMWF for global)</li>
          <li>Tide predictions (deterministic, computed decades in advance)</li>
          <li>
            Spot-specific corrections for things like wind shadowing or
            breeze timing
          </li>
        </ul>

        <p>
          This is where you start seeing the &ldquo;wind quality&rdquo; and
          &ldquo;tide stage&rdquo; annotations next to the wave height.
        </p>

        <SubHeading>Layer 5: The human overlay</SubHeading>

        <p>
          The last step is interpretation. Surfline&rsquo;s in-house model
          (called <strong>LOTUS</strong>) takes the chain&rsquo;s output and
          runs spot-specific corrections. But the star rating you actually
          see has typically been adjusted by:
        </p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            Spot-specific calibration based on years of &ldquo;what the
            model said vs what showed up&rdquo;
          </li>
          <li>
            Forecaster judgment (&ldquo;the model is overestimating the
            south swell this morning&rdquo;)
          </li>
          <li>
            Increasingly, machine-learning corrections trained on historical
            forecast errors
          </li>
        </ul>

        <p>
          Free apps (Windy, surf-forecast.com, etc.) typically skip the
          human overlay and present model output directly. The paid
          services with human forecasters tend to be more accurate at
          well-known spots. Free apps can be more accurate for obscure ones
          where there isn&rsquo;t enough observation data to train
          spot-specific corrections.
        </p>

        <ForecastPipelineDiagram />

        <SectionHeading>What star ratings actually mean</SectionHeading>

        <p>
          A star rating is not a wave-height measurement. It&rsquo;s a
          composite score that combines several factors:
        </p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>Wave height</strong> relative to the spot&rsquo;s
            potential, not absolute size
          </li>
          <li>
            <strong>Swell period</strong>, where longer is better with
            diminishing returns past 16 to 18 seconds
          </li>
          <li>
            <strong>Swell direction</strong>, meaning how well it aligns
            with the spot&rsquo;s optimal window
          </li>
          <li>
            <strong>Wind</strong>, where offshore is good, side-shore is
            neutral, onshore is bad, and strength matters
          </li>
          <li>
            <strong>Tide</strong>, meaning alignment with the spot&rsquo;s
            preferred window
          </li>
        </ul>

        <p>
          A 3-star at Lower Trestles means something completely different
          than a 3-star at Mavericks. The score is normalized to each
          spot&rsquo;s own potential. A 5-star at Trestles is &ldquo;perfect
          Trestles,&rdquo; meaning head-high, organized, offshore, ideal
          tide. A 5-star at Mavericks is double-overhead-plus with a
          long-period west swell.
        </p>

        <p>
          The same offshore conditions can produce wildly different ratings
          at different spots, because the rating reflects how well those
          conditions match what that specific spot needs.
        </p>

        <StarRatingDecomposition />

        <SectionHeading>Why forecasts get worse with lead time</SectionHeading>

        <p>
          Forecast skill is bounded by chaos. This is the same effect
          Edward Lorenz discovered in the 1960s, where tiny errors in the
          initial conditions grow exponentially over time. The skill
          ceiling for atmospheric forecasts in the medium range is roughly
          14 days. Beyond that, no amount of computing power can tell you
          reliably what tomorrow&rsquo;s wind will be.
        </p>

        <p>For surf forecasts in practice:</p>

        <ul className="list-disc pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>24 hours out:</strong> very reliable. Treat as fact.
          </li>
          <li>
            <strong>72 hours (3 days):</strong> solid. Treat as a strong
            working hypothesis.
          </li>
          <li>
            <strong>5 days:</strong> pattern recognition. The forecast
            captures the rough shape, like a swell coming from a certain
            direction. But timing might slip by 12 to 24 hours and size
            might be off by 20 to 30 percent.
          </li>
          <li>
            <strong>7 to 10 days:</strong> vibes only. The model can see
            large-scale patterns (&ldquo;a storm is going to develop in the
            North Pacific&rdquo;) but can&rsquo;t predict exactly where or
            how strong.
          </li>
          <li>
            <strong>Past 14 days:</strong> no skill. Don&rsquo;t trust it.
          </li>
        </ul>

        <ForecastSkillCurve />

        <SectionHeading>A note on AI</SectionHeading>

        <p>
          Since 2023, AI weather models (DeepMind&rsquo;s GraphCast,
          ECMWF&rsquo;s AIFS, Huawei&rsquo;s Pangu-Weather) have started
          matching or beating traditional numerical weather prediction at
          standard skill metrics, at a fraction of the compute cost.
          Wave-model AI analogues are coming. Surfline has been
          incorporating machine learning into LOTUS since 2021.
        </p>

        <p>
          Nobody yet knows how much further AI will push the skill ceiling.
          What&rsquo;s certain is that the physics of swell propagation
          across an ocean, which is the most predictable part, will keep
          working the same way it always has. The wind forecasts driving
          those waves just keep getting better.
        </p>

        <SectionHeading>How to use this</SectionHeading>

        <ol className="list-decimal pl-6 space-y-2 marker:text-text-muted">
          <li>
            <strong>Look at ensemble spread when you can.</strong> Most apps
            show only the deterministic run, but the full picture comes
            from running the model many times with slightly different
            starting conditions. If all the runs show the same thing, trust
            it. If they disagree, the model is uncertain.
          </li>
          <li>
            <strong>Check the buoys as a swell approaches.</strong> Once a
            swell is within 12 to 24 hours of arrival, the deep-water buoy
            will see it. That&rsquo;s a reality check on the model.
          </li>
          <li>
            <strong>Treat star ratings as a starting point.</strong> The
            model can tell you everything except whether the session will
            be worth it for you. That last call is still yours.
          </li>
        </ol>

        <p>
          The forecast is a stack of models. Outer shell: a global
          atmospheric model. Inside that, a global wave model. Inside that,
          a nearshore transformation. Inside that, a wind blend and a tide
          model. And at the very center, a human who has surfed your break
          in winter and summer, in offshores and onshores, and who is
          nudging the score based on something the model can&rsquo;t quite
          see.
        </p>

        <p>
          Knowing this lets you read a forecast critically, and spot errors
          when they happen.
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
          Bauer, P., Thorpe, A. &amp; Brunet, G. (2015). The quiet
          revolution of numerical weather prediction. Nature 525,
          47&ndash;55.
        </p>
        <p className="mb-2">
          Booij, N., Ris, R.C. &amp; Holthuijsen, L.H. (1999). A
          third-generation wave model for coastal regions. Journal of
          Geophysical Research 104(C4), 7649&ndash;7666.
        </p>
        <p className="mb-2">
          Lorenz, E.N. (1963). Deterministic nonperiodic flow. Journal of
          the Atmospheric Sciences 20(2), 130&ndash;141.
        </p>
        <p className="mb-2">
          O&rsquo;Reilly, W.C. et al. (2016). The California coastal wave
          monitoring and prediction system. Coastal Engineering 116,
          118&ndash;132.
        </p>
        <p className="mb-2">
          Price, I. et al. (2024). Probabilistic weather forecasting with
          machine learning. Nature 637, 84&ndash;90.
        </p>
        <p className="mb-2">
          Tolman, H.L. (2014). User manual and system documentation of
          WAVEWATCH III version 4.18. NOAA / NWS / NCEP Technical Note 316.
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

function SubHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="pt-2 text-lg sm:text-xl font-bold tracking-tightish text-text-primary">
      {children}
    </h3>
  );
}
