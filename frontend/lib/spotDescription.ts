/**
 * THE SPOT DESCRIPTION, WITH ITS NEARBY SPOTS AS LINKS.
 *
 * spots.description is plain text, written by pipeline/generate_descriptions.py from template t4
 * (pipeline/descriptions.py). Its last sentence, when the spot has neighbours within 25 km, is
 *
 *     Nearby spots: Backdoor Pipeline (1 km), Off The Wall (1 km) and Ehukai Beach Park (1 km).
 *
 * The spot page lists nearby spots nowhere else, so that sentence's names become links here. The
 * text stays plain in the database (and in the page's meta description, which never used it): the
 * page fetches the spots around this one (nearbyBox) and links each item whose name is exactly one
 * of their SHOWN names (lib/spotName displaySpotName, the same rule as the pipeline's
 * display_name). Anything that does not parse, or a name with no single match, stays plain text,
 * so a template change can never break the paragraph, only leave it unlinked.
 *
 * Imports nothing, so client code and tests can use it.
 */

export const NEARBY_LEAD = 'Nearby spots: ';

/** The pipeline's radius for a neighbour (descriptions.NEARBY_MAX_KM). */
export const NEARBY_MAX_KM = 25;

export type DescriptionPart = { text: string; slug?: string };

/** One item of the nearby sentence: its name and its "(n km)" as written. */
type NearbyItem = { name: string; km: string; sep: string };

/**
 * The nearby sentence's items, or null when *description* has none or it is not in the template's
 * exact shape: "Nearby spots: " at the start or after ". ", then "<name> (<n> km)" items joined by
 * ", " and " and ", then a final "." that ends the text.
 */
export function nearbyItems(description: string): { head: string; items: NearbyItem[] } | null {
  const at = description.lastIndexOf(NEARBY_LEAD);
  if (at < 0 || (at > 0 && description.slice(at - 2, at) !== '. ')) return null;
  const tail = description.slice(at + NEARBY_LEAD.length);
  const item = /(.+?) \((\d+ km)\)(, | and |\.$)/y;
  const items: NearbyItem[] = [];
  let m: RegExpExecArray | null;
  while ((m = item.exec(tail)) !== null) {
    items.push({ name: m[1], km: m[2], sep: m[3] });
    if (m[3] === '.') break;
  }
  if (items.length === 0 || item.lastIndex !== tail.length || items[items.length - 1].sep !== '.') {
    return null;
  }
  return { head: description.slice(0, at + NEARBY_LEAD.length), items };
}

/**
 * *description* cut into text and links. *spots* are candidate neighbours with their SHOWN names
 * (fetchAllSpots/withDisplayName already applied); a name links only when exactly one candidate
 * other than *selfSlug* has it.
 */
export function descriptionParts(
  description: string,
  spots: ReadonlyArray<{ slug: string; name: string }>,
  selfSlug?: string,
): DescriptionPart[] {
  const parsed = nearbyItems(description);
  if (!parsed) return [{ text: description }];
  const byName = new Map<string, string[]>();
  for (const s of spots) {
    if (s.slug === selfSlug) continue;
    byName.set(s.name, [...(byName.get(s.name) ?? []), s.slug]);
  }
  const parts: DescriptionPart[] = [{ text: parsed.head }];
  for (const it of parsed.items) {
    const slugs = byName.get(it.name) ?? [];
    parts.push(slugs.length === 1 ? { text: it.name, slug: slugs[0] } : { text: it.name });
    parts.push({ text: ` (${it.km})${it.sep}` });
  }
  // Adjacent text parts merged, so the paragraph renders as few nodes as it needs.
  return parts.reduce<DescriptionPart[]>((out, p) => {
    const last = out[out.length - 1];
    if (last && !last.slug && !p.slug) last.text += p.text;
    else out.push({ ...p });
    return out;
  }, []);
}

/**
 * The lat/lng box that holds every spot within NEARBY_MAX_KM of (lat, lng), with a margin: what
 * the page asks the spots table for. 1 degree of latitude is 111.2 km; a degree of longitude
 * shrinks by cos(lat).
 */
export function nearbyBox(lat: number, lng: number): {
  latMin: number; latMax: number; lngMin: number; lngMax: number;
} {
  const dLat = (NEARBY_MAX_KM + 5) / 111.2;
  const dLng = dLat / Math.max(0.1, Math.cos((lat * Math.PI) / 180));
  return { latMin: lat - dLat, latMax: lat + dLat, lngMin: lng - dLng, lngMax: lng + dLng };
}
