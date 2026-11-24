import type { Metadata } from 'next';
import Link from 'next/link';
import { CONTACT_EMAIL, mailtoHref } from '@/lib/contact';
import { DAILY_REPORT_CAP } from '@/lib/surfReport';

export const revalidate = 86400;
// Static, and regenerated on the revalidate period where there is one: the root layout reads the
// spots past the Data Cache (lib/queries.ts fetchAllSpots), which would otherwise render this
// page on every request.
export const dynamic = 'force-static';

/** Change this, and say what changed, whenever the policy changes. */
const EFFECTIVE = '24 November 2026';

export const metadata: Metadata = {
  title: { absolute: 'Privacy | Stormy Petrel' },
  description: 'What Stormy Petrel collects, why, and what it never does with it.',
  alternates: { canonical: '/privacy' },
};

const A = 'text-cyan-600 hover:underline';

export default function PrivacyPage() {
  return (
    <article className="mx-auto max-w-[700px] px-4 sm:px-6 py-10 sm:py-14">
      <header className="mb-8">
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">Legal</div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">Privacy</h1>
        <p className="mt-2 text-sm text-text-secondary">Effective {EFFECTIVE}</p>
      </header>

      <div className="space-y-5 text-base text-text-primary leading-relaxed">
        <p>
          The short version: there are no accounts, we never ask for your name or email, we show
          no ads, and we never sell or share data about you.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">What we collect</h2>

        <h3 className="font-bold">Analytics</h3>
        <p>
          We use Google Analytics to see which pages people use. It sets cookies and records the
          pages you visit, your device and browser type, your approximate location (city or
          region) and the site that sent you. Google handles this under its own{' '}
          <a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer" className={A}>
            privacy policy
          </a>
          . You can block it with your browser&rsquo;s cookie settings or Google&rsquo;s{' '}
          <a href="https://tools.google.com/dlpage/gaoptout" target="_blank" rel="noreferrer" className={A}>
            opt-out add-on
          </a>
          .
        </p>

        <h3 className="font-bold">Surf reports</h3>
        <p>
          If you answer &ldquo;Surfed here today?&rdquo; on a spot page, we store the spot, the
          hour you surfed, your answers, the forecast we showed for that hour, and when you sent
          it. We use reports only to check how good our forecasts are; they never change a
          forecast or a rating. We keep them indefinitely.
        </p>
        <p>
          To stop duplicate and spam reports, we also store a short code made from your IP address
          and browser, mixed with a secret that changes every day. The code can&rsquo;t be turned
          back into your IP address, and you get a different code each day, so your reports
          can&rsquo;t be joined up into a history. We never store your IP address. Each code can
          send up to {DAILY_REPORT_CAP} reports a day.
        </p>

        <h3 className="font-bold">Error reports</h3>
        <p>
          When a page breaks, we record which page, the error message and the time, and count
          repeats of the same error. Error reports contain no IP address, no browser details and
          no cookies.
        </p>

        <h3 className="font-bold">Services that load with our pages</h3>
        <p>
          Our host (Vercel) and database (Supabase) keep standard server logs, which can include
          IP addresses. Pages with a live cam load the cam&rsquo;s player from its provider (for
          example YouTube or Explore.org), which may set its own cookies. Maps load tiles from
          CARTO or OpenStreetMap. Each of these handles your data under its own policy.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">What we don&rsquo;t do</h2>
        <p>
          We don&rsquo;t sell, rent or share data about you, we don&rsquo;t run ads, and we
          don&rsquo;t build profiles of visitors.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Do Not Track</h2>
        <p>
          We don&rsquo;t change what we do when your browser sends a Do Not Track signal, and
          Google Analytics doesn&rsquo;t either. To stop analytics, use one of the options above.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">California</h2>
        <p>
          This policy is written to meet the California Online Privacy Protection Act (CalOPPA).
          Because we don&rsquo;t keep information that identifies you, there is nothing of yours
          for us to show or correct, but you can email us with any question about your data.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Children</h2>
        <p>The site isn&rsquo;t aimed at children under 13, and we don&rsquo;t knowingly collect data from them.</p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Changes</h2>
        <p>
          If we change this policy, we&rsquo;ll update the date at the top and say what changed here.
        </p>

        <h2 className="pt-3 text-xl font-bold tracking-tightish">Contact</h2>
        <p>
          <a href={mailtoHref('Privacy question')} className={A}>{CONTACT_EMAIL}</a>
          . See also our <Link href="/terms" className={A}>terms</Link>.
        </p>
      </div>
    </article>
  );
}
