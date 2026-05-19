import type { Metadata } from 'next';
import Link from 'next/link';
import { SwellDirectionMap } from '@/components/learn/SwellDirectionMap';
import { PointVsBay } from '@/components/learn/PointVsBay';

export const revalidate = 86400;

export const metadata: Metadata = {
  title: { absolute: 'Why swell direction matters | Stormy Petrel' },
  description:
    'Learn why two spots ten miles apart get completely different waves. Interactive tools show how swell direction and coastal orientation determine what your spot receives.',
  alternates: { canonical: '/learn/swell-direction' },
  openGraph: {
    title: 'Why swell direction matters | Stormy Petrel',
    description:
      'The reason two spots ten miles apart get completely different waves.',
    type: 'article',
  },
};

export default function SwellDirectionArticle() {
  return (
    <article className="mx-auto max-w-[720px] px-4 sm:px-6 py-10 sm:py-14">
      <nav className="mb-4 text-xs text-text-muted">
        <Link href="/learn" className="hover:text-cyan-600">
          ← All guides
        </Link>
      </nav>

      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Learn · 7 min read
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          Why swell direction matters
        </h1>
        <p className="mt-3 text-base sm:text-lg text-text-secondary leading-relaxed">
          The reason two spots ten miles apart get completely different waves.
        </p>
      </header>

      <div className="space-y-6 text-base sm:text-lg text-text-primary leading-relaxed">
        <p>
          Two spots ten miles apart. Same buoy reading. Same day. One is
          head-high and pumping, the other is knee-high and gutless. The buoy
          didn&rsquo;t lie. The forecast wasn&rsquo;t wrong. What changed in
          those ten miles is the angle between the swell and the coastline.
        </p>

        <SectionHeading>Your spot has an optimal angle</SectionHeading>

        <p>
          Every surf spot has a general orientation: the compass direction
          it&rsquo;s most open to. But it&rsquo;s not always as simple as
          &ldquo;this beach faces south.&rdquo; Point breaks often have
          multiple sections that respond to different angles. The outside
          section might favor a more westerly swell while the inside works
          better on a south. Reef breaks can refract swell around a shelf so
          the wave that actually hits the takeoff zone comes from a different
          direction than what the buoy shows.
        </p>

        <p>
          The shape of the ocean floor in front of your spot also plays a
          major role. Underwater ridges, canyons, and reef shelves can bend,
          focus, or scatter swell energy in ways that the surface-level
          direction alone doesn&rsquo;t explain. We&rsquo;ll cover how bottom
          contours shape waves in a future guide.
        </p>

        <p>
          When a swell is well-aligned with the spot&rsquo;s orientation, it
          delivers the most raw energy. But more energy doesn&rsquo;t always
          mean better waves. Many of the best point breaks work precisely
          because swell arrives at a slight angle and wraps along the reef,
          creating a long, peeling wall. A swell aimed straight at a point
          might close out, while the same swell from 20 degrees to the side
          peels perfectly. Each spot has an optimal direction that balances
          energy delivery with wave shape.
        </p>

        <p>
          As a general rule, the energy a swell delivers drops off as the
          angle between the swell and the spot increases:
        </p>

        <p>
          20 degrees off-axis: about 88% of maximum energy. Still firing.
        </p>
        <p>
          45 degrees off-axis: about 50%. Noticeably smaller.
        </p>
        <p>
          70 degrees off-axis: about 12%. Scraps.
        </p>
        <p>
          90 degrees off-axis: zero. The swell is running parallel to the
          beach.
        </p>

        <ComponentLabel>
          Swell direction calculator — pick a spot and adjust the incoming
          swell to see energy delivery
        </ComponentLabel>
        <SwellDirectionMap />

        <SectionHeading>The swell window</SectionHeading>

        <p>
          A swell window is the angular slice of open ocean your spot can
          see. It&rsquo;s the range of compass bearings from which a swell
          can reach your beach in a straight line without being blocked by
          land: a headland, an island, a peninsula, a continent.
        </p>

        <p>
          Some spots have wide windows. The Outer Banks of North Carolina
          jut 30 miles into the Atlantic with almost nothing between Hatteras
          and Europe. The swell window stretches from about 045 degrees all
          the way around to 200 degrees.
        </p>

        <p>
          Other spots have narrow windows. Southern California sits behind
          the Channel Islands and Point Conception. Winter swells from the
          west and northwest can be reduced by 50&ndash;90% by the time they
          reach spots inside the channel island shadow, almost entirely due
          to offshore island blocking.
        </p>

        <p>
          On Stormy Petrel, each spot has a swell window measured in degrees.
          If the forecast swell direction falls outside that window, the
          spot won&rsquo;t see much of it regardless of how big the buoy
          reads.
        </p>

        <SectionHeading>Refraction: how long-period swell bends around obstacles</SectionHeading>

        <p>
          Swell doesn&rsquo;t just travel in a straight line. When waves
          approach shallow water at an angle, the part of the crest in
          shallower water slows down first while the deeper part keeps
          moving. The wave bends toward shore. This is refraction.
        </p>

        <p>
          Long-period swell refracts more than short-period swell because it
          starts feeling the bottom in deeper water. A 20-second swell
          begins interacting with the seafloor at around 1,000 feet of
          depth. A 10-second swell doesn&rsquo;t feel anything until about
          250 feet. The long-period swell has far more room to bend.
        </p>

        <p>
          This is why a 17-second south swell can wrap into east-facing
          spots that a 9-second wind swell from the same direction
          can&rsquo;t reach. Long-period energy bends around obstacles that
          completely block short-period waves.
        </p>

        <SectionHeading>Points amplify. Bays spread.</SectionHeading>

        <p>
          When swell approaches a point or headland, the wave rays converge
          on the shallow promontory. Energy concentrates. Wave height grows.
          NOAA training materials show that a 10ft swell at 15 seconds can
          produce 29ft breakers at a steep point with a 20 degree
          convergence angle. This is the physics behind every famous point
          and reef break.
        </p>

        <p>
          In open bays, wave rays tend to diverge, spreading energy across a
          wider area. This often produces smaller, softer waves compared to
          an adjacent headland. But &ldquo;bay&rdquo; doesn&rsquo;t
          automatically mean bad waves. Some of the world&rsquo;s best
          breaks sit inside bays where the right combination of bottom
          contour, reef shape, and swell angle focuses energy despite the
          bay geometry. Waimea Bay and Honolua Bay are obvious examples.
        </p>

        <ComponentLabel>
          How coastline shape focuses or spreads wave energy — toggle
          between point and bay
        </ComponentLabel>
        <PointVsBay />

        <SectionHeading>How to use this</SectionHeading>

        <p>
          Learn your spot&rsquo;s optimal swell direction. Not
          &ldquo;west&rdquo; but &ldquo;267 degrees.&rdquo; Every time you
          check the forecast, do the subtraction: how far off-axis is
          today&rsquo;s swell? Within 20 degrees is prime. Past 60 degrees
          is leftovers.
        </p>

        <p>
          On Stormy Petrel, each spot page shows the optimal swell direction
          and the swell window. The star rating already factors in how well
          the current swell direction aligns with your spot. But
          understanding the geometry yourself means you know when to drive
          ten miles down the coast to the spot with a better angle.
        </p>

        <SectionHeading>One piece of a bigger picture</SectionHeading>

        <p>
          Swell direction is one of the most important variables in a surf
          forecast, and the general rules here hold true across most spots.
          A swell outside your window won&rsquo;t produce waves. A swell 60
          degrees off-axis will be a fraction of what the buoy says. Points
          focus energy, and long-period swell bends more than short.
        </p>

        <p>
          But every spot has its own personality. The general rules get you
          most of the way there. The last 20% is learning the specifics of
          your home break: which angle produces the longest walls, which
          tide makes the sandbar work, which wind direction the cliff
          blocks. No formula replaces time in the water. The forecast tells
          you when to show up. Your experience tells you exactly where to
          sit.
        </p>
      </div>

      <footer className="mt-12 pt-6 border-t border-ink-600 text-sm">
        <Link href="/learn" className="text-cyan-600 hover:underline">
          ← All guides
        </Link>
      </footer>
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

function ComponentLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="pt-2 text-[11px] uppercase tracking-widest2 font-bold text-text-secondary">
      {children}
    </div>
  );
}
