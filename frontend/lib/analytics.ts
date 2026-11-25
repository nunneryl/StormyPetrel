/**
 * Google Analytics loads on the production site only. Vercel sets VERCEL_ENV on every build and
 * function: 'production' for the production deployment, 'preview' for branch and PR previews,
 * 'development' under `vercel dev`; it is unset locally and in CI. Pages are rendered at build
 * time and on revalidation, both of which see the deployment's own VERCEL_ENV, so a preview's
 * pages never carry the tag and its visits never reach the production property.
 */
export const GA_ID = 'G-S7Y5BXQ125';

export function analyticsEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env.VERCEL_ENV === 'production';
}
