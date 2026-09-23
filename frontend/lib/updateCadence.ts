/**
 * HOW OFTEN THE SITE SAYS ITS DATA IS UPDATED. The one place those words live: the spot page
 * description (and through it the Open Graph and Twitter descriptions), its JSON-LD, the About
 * page, the footer and the methodology post all read them from here. updateCadence.test.mts
 * fails if a cadence claim is written into copy by hand.
 *
 * WHY IT EXISTS. The copy said "updated every 6 hours" in four places and "every 8 h · buoys
 * every 3 h" in the footer, and both were false against the measured cadence: forecasts landed
 * 5.4 to 11.4 hours apart, at the mercy of GitHub's schedule delays, and the hourly buoy job
 * ran in about a quarter of its hours. A number repeated across files drifts, and so does a
 * number that happens to be true today.
 *
 * DELIBERATELY NOT A NUMBER. "Several times a day" and "throughout the day" are true now and
 * stay true after the cadence changes under consideration (hourly checks, event-driven runs),
 * so a pipeline change does not reopen the copy.
 *
 * MARKDOWN READS IT TOO. A blog post cannot import a constant, so it writes {{FORECAST_UPDATES}}
 * or {{BUOY_UPDATES}} and lib/blog.ts runs fillCopyTokens over the file before anything else
 * reads it. A token it does not know throws, rather than reaching a reader as "{{TYPO}}".
 */
export const FORECAST_UPDATES = 'updated several times a day';
export const BUOY_UPDATES = 'throughout the day';

const TOKENS: Record<string, string> = { FORECAST_UPDATES, BUOY_UPDATES };

/** Replace each {{TOKEN}} in markdown with its setting. Throws on a token it does not know. */
export function fillCopyTokens(markdown: string): string {
  return markdown.replace(/\{\{\s*([A-Z_]+)\s*\}\}/g, (whole: string, name: string) => {
    if (!Object.prototype.hasOwnProperty.call(TOKENS, name)) {
      throw new Error(`unknown copy token ${whole}`);
    }
    return TOKENS[name];
  });
}
