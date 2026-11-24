/**
 * How readers reach us: one address, used by the footer, the About page, the legal pages and every
 * spot page's "Report incorrect data" link. It replaced a link to GitHub's new-issue form, which
 * needs a GitHub account most surfers do not have.
 *
 * Imports nothing, so client and server code can both use it and it tests under bare node.
 */

export const CONTACT_EMAIL = 'hello@stormypetrel.surf';

/** A mailto: link to CONTACT_EMAIL, with the subject and body pre-filled when given. Both are
 *  percent-encoded (encodeURIComponent: a space is %20, never "+", which mail clients would show
 *  literally). */
export function mailtoHref(subject?: string, body?: string): string {
  const params: string[] = [];
  if (subject) params.push(`subject=${encodeURIComponent(subject)}`);
  if (body) params.push(`body=${encodeURIComponent(body)}`);
  return `mailto:${CONTACT_EMAIL}${params.length ? `?${params.join('&')}` : ''}`;
}

/** The spot page's "Report incorrect data" link: the spot's name in the subject, and its slug and
 *  page in the body, so the report says which spot without the reader having to. */
export function spotReportHref(name: string, slug: string, pageUrl: string): string {
  return mailtoHref(
    `Spot data: ${name}`,
    `Spot: ${name} (${slug})\nPage: ${pageUrl}\n\nWhat's wrong:\n`,
  );
}
