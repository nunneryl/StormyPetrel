import type { Metadata } from 'next';
import Link from 'next/link';

export const revalidate = 86400; // 24h — content is essentially static

export const metadata: Metadata = {
  title: { absolute: 'About | Stormy Petrel' },
  description:
    'Stormy Petrel is a free surf forecast site built on public NOAA data. No paywall, no premium tier.',
  alternates: { canonical: '/about' },
  openGraph: {
    title: 'About | Stormy Petrel',
    description:
      'Stormy Petrel is a free surf forecast site built on public NOAA data. No paywall, no premium tier.',
    type: 'website',
  },
};

export default function AboutPage() {
  return (
    <article className="mx-auto max-w-[700px] px-4 sm:px-6 py-10 sm:py-14">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          About
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          Stormy Petrel
        </h1>
      </header>

      <div className="space-y-6 text-base sm:text-lg text-text-primary leading-relaxed">
        <p>
          Storm petrels are small seabirds that appear before big weather.
          Sailors noticed them showing up ahead of storms, feeding on the
          chaos of a changing sea. They read the conditions before anyone had
          a model for it.
        </p>

        <p>
          Every wave starts with public data. Government buoys measure the
          swell, NOAA supercomputers model the nearshore and tide tables are
          published by federal scientists who probably surf. All of it
          collected with public money, all of it free to access, all of it
          sitting on government servers waiting for someone to read it.
        </p>

        <p>
          Somewhere along the way, a company decided that interpreting this
          data was worth $100 a year. Then they bought every other forecast
          site and shut them down. Then they put the surf cam behind a
          60-second paywall. You know who we&rsquo;re talking about.
        </p>

        <p>
          Stormy Petrel is what happens when you get annoyed enough to do
          something about it.
        </p>

        <p>
          We take the same NOAA buoys, the same WAVEWATCH III models, the same
          nearshore predictions, the same tide tables and turn them into a
          surf forecast you can actually use. No paywall. No &ldquo;premium
          tier&rdquo; that unlocks the cam you used to watch for free five
          years ago.
        </p>

        <p>
          The data isn&rsquo;t a secret. The math isn&rsquo;t proprietary. A
          south swell at 15 seconds doesn&rsquo;t care who&rsquo;s reading the
          buoy.
        </p>

        <p>
          Not sure what a 15-second period means or why swell direction
          matters?{' '}
          <Link href="/learn" className="text-cyan-600 hover:underline">
            We wrote a few guides to help you read the forecast like a local.
          </Link>
        </p>

        <h2 className="pt-4 text-xl sm:text-2xl font-bold tracking-tightish text-text-primary">
          How it works
        </h2>

        <p>
          Every six hours, we pull fresh data from NOAA&rsquo;s Nearshore Wave
          Prediction System, WAVEWATCH III spectral models, HRRR 3km wind
          grids, NDBC buoy observations, and CO-OPS tide predictions. Our
          interpretation engine scores each spot on swell direction, period,
          wind quality, and tide state. No one&rsquo;s selling you a
          &ldquo;forecaster&rsquo;s insight&rdquo; upgrade.
        </p>

        <p>
          The methodology is public. The code is on GitHub. If we&rsquo;re
          wrong, you can see exactly why and tell us.
        </p>

        <h2 className="pt-4 text-xl sm:text-2xl font-bold tracking-tightish text-text-primary">
          What we&rsquo;re not
        </h2>

        <p>
          We&rsquo;re not building AI wave detection or surfer tracking or
          whatever dystopian feature gets announced next quarter.
        </p>

        <p>
          We&rsquo;re building the forecast site that should have always
          existed. The one that treats public data like public data.
        </p>

        <p>
          Check a spot. If the rating matches what you see at the beach, tell
          a friend. If it doesn&rsquo;t, tell us.
        </p>
      </div>
    </article>
  );
}
