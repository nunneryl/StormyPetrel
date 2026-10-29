/**
 * TIMES ARE SHOWN IN THE SPOT'S OWN TIME ZONE.
 *
 * Every clock time the site shows for a spot (the forecast grid, the chart axes and tooltips,
 * the buoy reading, the surf-report hour picker, the cam's sunrise) is that spot's local time,
 * in the IANA zone stored on the spot (spots.time_zone, migration 026). Until 2026-10-29 they
 * were all America/Los_Angeles, so an East Coast spot's "5pm" row was 8pm on the beach.
 *
 * WHERE THE ZONE COMES FROM. pipeline/enrichment/time_zone.py looks each spot's coordinates up
 * in the timezone-boundary-builder polygons, never its state: the Florida panhandle west of
 * Cape San Blas is Central, Puerto Rico is America/Puerto_Rico (Atlantic Standard, no DST).
 * The frontend never computes a zone; it reads the stored one.
 *
 * FALLBACK. A spot whose row has no zone yet (between this change deploying and the pipeline
 * run that uploads the zones) shows America/Los_Angeles, as every spot did before, and every
 * place that shows a time also shows the zone's abbreviation, so a fallback time is never
 * mistaken for local time.
 *
 * Strings are built from formatToParts, not by editing format()'s output: some ICU builds put
 * a narrow no-break space before AM/PM, which a replace(' ', '') would leave behind.
 */

export const FALLBACK_TIME_ZONE = 'America/Los_Angeles';

/** Is *tz* an IANA zone this runtime knows? */
export function isTimeZone(tz: string | null | undefined): tz is string {
  if (!tz) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/** The zone to show a spot's times in: its stored time_zone, else FALLBACK_TIME_ZONE. */
export function spotTimeZone(spot: { time_zone?: string | null } | null | undefined): string {
  const tz = spot?.time_zone;
  return isTimeZone(tz) ? tz : FALLBACK_TIME_ZONE;
}

const FORMATS = new Map<string, Intl.DateTimeFormat>();

function formatter(tz: string, kind: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${kind}|${tz}`;
  let f = FORMATS.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', { ...opts, timeZone: tz });
    FORMATS.set(key, f);
  }
  return f;
}

function parts(iso: string, tz: string, kind: string, opts: Intl.DateTimeFormatOptions) {
  const out: Partial<Record<Intl.DateTimeFormatPartTypes, string>> = {};
  for (const p of formatter(tz, kind, opts).formatToParts(new Date(iso))) out[p.type] = p.value;
  return out;
}

const CLOCK: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit', hour12: true };
const HOUR: Intl.DateTimeFormatOptions = { hour: 'numeric', hour12: true };

/** "5pm": the hour at *iso* in *tz*. */
export function fmtShortTime(iso: string, tz: string): string {
  const p = parts(iso, tz, 'hour', HOUR);
  return `${p.hour}${(p.dayPeriod ?? '').toLowerCase()}`;
}

/** "6:42am": the hour and minute at *iso* in *tz*. */
export function fmtClock(iso: string, tz: string): string {
  const p = parts(iso, tz, 'clock', CLOCK);
  return `${p.hour}:${p.minute}${(p.dayPeriod ?? '').toLowerCase()}`;
}

/** "Wed, Oct 28": the date at *iso* in *tz*. */
export function fmtDay(iso: string, tz: string): string {
  return formatter(tz, 'day', { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date(iso));
}

/** "Wed 5pm": the chart axis label, day and hour at *iso* in *tz*. */
export function fmtDayTimeTick(iso: string, tz: string): string {
  const day = formatter(tz, 'weekday', { weekday: 'short' }).format(new Date(iso));
  return `${day} ${fmtShortTime(iso, tz)}`;
}

/** "Wed, Oct 28 5pm EDT": a chart tooltip's time, with the zone. */
export function fmtTooltipTime(iso: string, tz: string): string {
  return `${fmtDay(iso, tz)} ${fmtShortTime(iso, tz)} ${zoneAbbr(iso, tz)}`;
}

/** "2026-10-28": the local date at *iso* in *tz*, for grouping rows by day. */
export function dayKey(iso: string, tz: string): string {
  const p = parts(iso, tz, 'date', { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}-${p.month}-${p.day}`;
}

/** The local hour at *iso* in *tz*, 0 to 23. */
export function localHour(iso: string, tz: string): number {
  const p = parts(iso, tz, 'hour24', { hour: 'numeric', hourCycle: 'h23' });
  return Number(p.hour);
}

/** Is *iso* one of the forecast grid's rows: midnight, 3am, 6am ... in *tz*? */
export function isThreeHourMark(iso: string, tz: string): boolean {
  return localHour(iso, tz) % 3 === 0;
}

/** "EDT": the zone's abbreviation at *iso* (it changes with daylight saving). */
export function zoneAbbr(iso: string, tz: string): string {
  return parts(iso, tz, 'zone', { hour: 'numeric', timeZoneName: 'short' }).timeZoneName ?? tz;
}

/** "Oct 28, 4:50pm EDT": a timestamp with its date, local clock and zone. */
export function fmtStamp(iso: string, tz: string): string {
  const day = formatter(tz, 'monthday', { month: 'short', day: 'numeric' }).format(new Date(iso));
  return `${day}, ${fmtClock(iso, tz)} ${zoneAbbr(iso, tz)}`;
}

/** The zone abbreviations a run of timestamps spans, in order, each once: ["EDT"], or
 *  ["EDT", "EST"] across the November change. */
export function zoneAbbrs(isos: readonly string[], tz: string): string[] {
  const out: string[] = [];
  for (const iso of isos) {
    const a = zoneAbbr(iso, tz);
    if (out[out.length - 1] !== a && !out.includes(a)) out.push(a);
  }
  return out;
}
