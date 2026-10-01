#!/usr/bin/env python3
"""Benchmark our calibrated and raw heights against the NWS Surf Zone Forecast (SRF).

THE QUESTION. At the 130 calibrated spots, does the surf height NWS forecasters publish sit
nearer our raw height (face_ft_raw) or our calibrated one (face_ft)? THIS IS A BENCHMARK
AGAINST A FORECAST, NOT GROUND TRUTH. The SRF is itself a forecast, made by forecasters
from wave guidance that may include NWPS, which we use too. No SRF product names NWPS, and
that does not prove the two are independent.

WHAT IS COMPARED
  NWS    For LOX, MTR, SGX and EKA: each office's FIRST issuance of each local day, and in
         each segment the surf height of its FIRST DAYTIME PERIOD (normally ".TODAY...").
         The main range is read low to high. "sets to X" and "local sets to X" are recorded
         separately and never move the range. A segment whose surf height this cannot read
         is LOGGED, never guessed.
  ours   Per calibrated spot and local day, the max of face_ft and of face_ft_raw over
         06:00 to 18:00 America/Los_Angeles, both ends included (13 hourly rows), source
         'nwps'. The daily mean is reported as a check. A day missing any of those 13 rows,
         or with a null in either column, is skipped and counted.
  join   A spot takes the segment naming the zone it falls in, or the nearest zone within
         5 km, using zone geometry from api.weather.gov. That is the survey's rule.

WINDOW. 2026-09-08 00:00 UTC to the last complete UTC day. face_ft_raw is on every hour at
all 130 spots from 2026-09-07 midday, so the window starts on the first whole day. A local
day is used only once its 18:00 has passed.

READ-ONLY. Reads IEM (paced at one request a second or slower), api.weather.gov, Supabase
(select only, through the MOP harness's own fetch_forecasts) and two committed files. It
writes ONE thing: the zone-geometry cache, pipeline/cache/srf_zone_geometry.json, which is
gitignored. Nothing goes to the database and nothing to the repo's data files.

RUN, on the Mac from the repo root (IEM, api.weather.gov and Supabase are blocked in the
dev sandbox). The report goes to stdout, progress to stderr:

    source ~/.stormypetrel.env && python3 scripts/srf_benchmark.py | tee ~/srf_benchmark.txt
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)   # mop_face_validation: the harness's Supabase read
sys.path.insert(0, ROOT)   # pipeline.*

ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
FACTORS = os.path.join(ROOT, "pipeline", "data", "spot_face_factors.json")
# The only file this script writes. pipeline/cache/ is gitignored, and a test holds it so.
CACHE_PATH = os.path.join(ROOT, "pipeline", "cache", "srf_zone_geometry.json")

WINDOW_START = datetime.date(2026, 9, 8)
OFFICES = ("LOX", "MTR", "SGX", "EKA")
LOCAL_TZ = "America/Los_Angeles"
DAY_FIRST_HOUR = 6    # local, included
DAY_LAST_HOUR = 18    # local, included: 06:00 to 18:00 is 13 hourly rows
MAX_ZONE_KM = 5.0
RAW_RATIO_LIMIT = 1.25
MIN_REQUEST_INTERVAL_S = 1.0
USER_AGENT = "StormyPetrel-SRF-benchmark/1.0 (+https://stormypetrel.surf)"

IEM_LIST_URL = "https://mesonet.agron.iastate.edu/api/1/nws/afos/list.json?pil=SRF&date={date}"
IEM_TEXT_URL = "https://mesonet.agron.iastate.edu/api/1/nwstext/{product_id}"
NWS_ZONE_URLS = ("https://api.weather.gov/zones/forecast/{zone}",
                 "https://api.weather.gov/zones/public/{zone}")

# Printed at the top of the output, unchanged. A test holds it to the wording it was given.
READING_RULE = """\
- Raw supports rating everyone on raw if it falls inside NWS's range
  more often than calibrated does, AND its median ratio to the
  midpoint is at most 1.25.
- If raw's median ratio is above 1.25, rating on raw would put us well
  above NWS forecasters: report that as a warning against it.
- Read it office by office. SGX has little power, since its factors
  only run 0.98 to 1.39.
- This is a benchmark against a forecast, not ground truth."""


# --------------------------------------------------------------------------- #
# Reading one SRF                                                              #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SurfHeight:
    """One segment's surf height for one period: the main range, and sets if given."""
    low: float
    high: float
    kind: str                     # range | around | single | less_than | or_less | flat
    sets: float | None = None
    local_sets: bool = False

    @property
    def midpoint(self) -> float:
        return (self.low + self.high) / 2


_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:feet|foot|ft)\.?"
_MAIN = (rf"(?P<lo>{_NUM})\s*(?:to|-|–)\s*(?P<hi>{_NUM})\s*{_UNIT}"
         rf"|around\s+(?P<around>{_NUM})\s*{_UNIT}"
         rf"|less\s+than\s+(?P<lt>{_NUM})\s*{_UNIT}"
         rf"|(?P<orless>{_NUM})\s*{_UNIT}\s*or\s+less"
         rf"|(?P<single>{_NUM})\s*{_UNIT}"
         r"|(?P<flat>flat)")
_SETS = (r"\s*[,;.]?\s*(?:(?:with|and)\s+)?"
         rf"(?P<local>local\s+)?sets?\s+to\s+(?P<sets>{_NUM})(?:\s*{_UNIT})?")
# The WHOLE value has to match. Anything else, a location qualifier or a "building to" for
# instance, makes the segment unreadable and it is logged rather than half-read.
SURF_VALUE = re.compile(rf"^(?:{_MAIN})(?:{_SETS})?\s*\.?$", re.I)


def parse_surf_height(value: str) -> tuple[SurfHeight | None, str | None]:
    """(SurfHeight, None) for a value this recognises, else (None, why). Never guesses.

    "around N" and a bare "N feet" are a range of zero width, N to N, so a height counts as
    inside only if it equals N; the ratio to the midpoint is unaffected. "less than N" and
    "N or less" run from 0 to N. "flat" is 0 to 0, and its midpoint gives no ratio.
    """
    text = " ".join((value or "").split())
    if not text:
        return None, "empty surf height"
    m = SURF_VALUE.match(text)
    if not m:
        return None, f"unrecognised surf height {text!r}"
    sets = float(m.group("sets")) if m.group("sets") else None
    local = bool(m.group("local"))
    if m.group("lo") is not None:
        low, high = float(m.group("lo")), float(m.group("hi"))
        if low > high:
            return None, f"range runs high to low {text!r}"
        return SurfHeight(low, high, "range", sets, local), None
    for group, kind in (("around", "around"), ("single", "single")):
        if m.group(group) is not None:
            v = float(m.group(group))
            return SurfHeight(v, v, kind, sets, local), None
    for group, kind in (("lt", "less_than"), ("orless", "or_less")):
        if m.group(group) is not None:
            return SurfHeight(0.0, float(m.group(group)), kind, sets, local), None
    return SurfHeight(0.0, 0.0, "flat", sets, local), None


def normalise(text: str) -> str:
    """IEM serves products as transmitted: CR CR LF line ends and SOH/ETX framing."""
    return (text or "").replace("\r", "").replace("\x01", "").replace("\x03", "")


UGC_LINE = re.compile(r"^[A-Z]{2}[ZC]\d{3}", re.M)
_UGC_END = re.compile(r"\d{6}-\s*$")


def ugc_zones(segment: str) -> list[str]:
    """Zones named by a segment's UGC block, e.g. 'CAZ087-349>351-011600-'."""
    m = UGC_LINE.search(segment)
    if not m:
        return []
    block = ""
    for line in segment[m.start():].splitlines()[:8]:
        block += line.strip()
        if _UGC_END.search(block):
            break
    zones, prefix = [], None
    for tok in block.split("-"):
        z = re.fullmatch(r"([A-Z]{2}[ZC])?(\d{3})(?:>(\d{3}))?", tok)
        if not z:
            continue
        prefix = z.group(1) or prefix
        if prefix:
            first, last = int(z.group(2)), int(z.group(3) or z.group(2))
            zones += [f"{prefix}{n:03d}" for n in range(first, last + 1)]
    return zones


@dataclass(frozen=True)
class Segment:
    zones: tuple[str, ...]
    name: str
    text: str


def split_segments(product: str) -> list[Segment]:
    """The product's segments, each with its zones and its area name line(s)."""
    out = []
    for chunk in normalise(product).split("$$"):
        m = UGC_LINE.search(chunk)
        if not m:
            continue
        body = chunk[m.start():]
        lines = body.splitlines()
        end = next((i for i, ln in enumerate(lines[:8]) if _UGC_END.search(ln)), None)
        names = []
        for line in (lines[end + 1:] if end is not None else []):
            s = line.strip()
            if not s.endswith("-") or UGC_LINE.match(s):
                break
            names.append(s.rstrip("-").strip())
        zones = tuple(ugc_zones(body))
        out.append(Segment(zones, " / ".join(names) or "+".join(zones), body))
    return out


_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_DOWS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
ISSUED = re.compile(r"\b(?P<hhmm>\d{3,4})\s+(?P<ampm>AM|PM)\s+(?P<tz>[A-Z]{3,4})\s+"
                    r"(?P<dow>[A-Z]{3})\s+(?P<mon>[A-Z]{3})\s+(?P<day>\d{1,2})\s+(?P<year>\d{4})\b",
                    re.I)


@dataclass(frozen=True)
class Issuance:
    local: datetime.datetime      # naive: the office's local wall clock, as printed
    tz: str


def parse_issuance(product: str) -> tuple[Issuance | None, str | None]:
    """The product's issuance line, e.g. '330 AM PDT Wed Oct 1 2026', as local time."""
    m = ISSUED.search(normalise(product))
    if not m:
        return None, "no issuance line"
    hour12, minute = divmod(int(m.group("hhmm")), 100)
    mon = m.group("mon").upper()
    if not (1 <= hour12 <= 12 and minute <= 59) or mon not in _MONTHS:
        return None, f"unreadable issuance line {m.group(0)!r}"
    hour = hour12 % 12 + (12 if m.group("ampm").upper() == "PM" else 0)
    try:
        local = datetime.datetime(int(m.group("year")), _MONTHS.index(mon) + 1,
                                  int(m.group("day")), hour, minute)
    except ValueError:
        return None, f"unreadable issuance line {m.group(0)!r}"
    if _DOWS[local.weekday()] != m.group("dow").upper():
        return None, f"issuance weekday does not match its date {m.group(0)!r}"
    return Issuance(local, m.group("tz").upper()), None


PERIOD_HEADER = re.compile(r"^\.(?P<name>[A-Za-z][A-Za-z ]*?)\.\.\.(?P<rest>.*)$")
_WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
_TODAY_PERIODS = frozenset({"TODAY", "THIS MORNING", "THIS AFTERNOON", "REST OF TODAY",
                            "LATE THIS MORNING", "LATE THIS AFTERNOON"})


def periods(segment_text: str) -> list[tuple[str, list[str]]]:
    """[(PERIOD NAME, its lines)] in order. A '.NAME...' line opens a period."""
    out = []
    for line in segment_text.splitlines():
        m = PERIOD_HEADER.match(line.strip())
        if m:
            rest = m.group("rest").strip()
            out.append((" ".join(m.group("name").upper().split()), [rest] if rest else []))
        elif out:
            out[-1][1].append(line)
    return out


def period_date(name: str, issued: datetime.date) -> datetime.date | None:
    """The date a DAYTIME period covers, or None for night, evening and combined periods."""
    if name in _TODAY_PERIODS:
        return issued
    if name in _WEEKDAYS:
        return issued + datetime.timedelta(days=(_WEEKDAYS.index(name) - issued.weekday()) % 7)
    return None


SURF_LABEL = re.compile(r"^surf\s+height\b[\s.:]*(?P<value>.*)$", re.I)
_LABEL_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9 /'()-]*\.{3,}")


def surf_height_text(lines: list[str]) -> tuple[str | None, str | None]:
    """The value of the period's one 'Surf Height....' line, continuation lines joined."""
    found = []
    for i, line in enumerate(lines):
        m = SURF_LABEL.match(line.strip())
        if not m:
            continue
        parts = [m.group("value").strip()]
        for nxt in lines[i + 1:]:
            if not nxt.strip() or not nxt[:1].isspace() or _LABEL_LINE.match(nxt.strip()):
                break
            parts.append(nxt.strip())
        found.append(" ".join(p for p in parts if p))
    if not found:
        return None, "no Surf Height line"
    if len(found) > 1:
        return None, f"{len(found)} Surf Height lines"
    return found[0], None


def segment_surf(segment: Segment, issued: datetime.date):
    """(SurfHeight or None, reason or None, period name or None) for the segment's first
    daytime period. That period has to be for the issuance day itself."""
    for name, lines in periods(segment.text):
        when = period_date(name, issued)
        if when is None:
            continue
        if when != issued:
            return None, f"first daytime period .{name}... is for {when}, not {issued}", name
        value, why = surf_height_text(lines)
        if value is None:
            return None, f"{why} in .{name}...", name
        surf, why = parse_surf_height(value)
        return surf, (None if surf else f"{why} in .{name}..."), name
    return None, "no daytime period", None


# --------------------------------------------------------------------------- #
# Choosing the products                                                        #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Listed:
    office: str
    product_id: str
    utc: datetime.datetime
    bbb: str | None


def parse_listing(payload, offices=OFFICES) -> list[Listed]:
    """SRFs of *offices* from one IEM list.json response. The UTC time is read from the
    product id's leading YYYYmmddHHMM, which IEM builds from the product's own timestamp."""
    rows = payload.get("data", []) if isinstance(payload, dict) else (payload or [])
    out = []
    for r in rows:
        tokens = str(r.get("product_id") or "").split("-")
        pil = str(r.get("pil") or (tokens[3] if len(tokens) > 3 else "")).strip()
        office = pil[3:] if pil.startswith("SRF") else ""
        if office not in offices or len(tokens) < 4 or not re.fullmatch(r"\d{12}", tokens[0]):
            continue
        utc = datetime.datetime.strptime(tokens[0], "%Y%m%d%H%M").replace(tzinfo=datetime.timezone.utc)
        out.append(Listed(office, r["product_id"], utc, tokens[4] if len(tokens) > 4 else None))
    return out


def candidates_by_local_day(listed: list[Listed], tz) -> dict:
    """{(office, local date): [products, earliest first]}. A correction sorts after the
    original it shares a minute with."""
    out = {}
    seen = set()
    for p in listed:
        if p.product_id in seen:
            continue
        seen.add(p.product_id)
        out.setdefault((p.office, p.utc.astimezone(tz).date()), []).append(p)
    for products in out.values():
        products.sort(key=lambda p: (p.utc, p.bbb is not None, p.product_id))
    return out


@dataclass(frozen=True)
class SegmentResult:
    name: str
    zones: tuple[str, ...]
    surf: SurfHeight | None
    reason: str | None
    period: str | None


@dataclass(frozen=True)
class DayProduct:
    office: str
    day: datetime.date
    product_id: str
    issued: Issuance
    by_zone: dict
    segments: tuple


def read_product(office: str, day: datetime.date, product_id: str, text: str, issued: Issuance,
                 parse_log: list) -> DayProduct:
    """Every segment of one chosen product, keyed by zone. Unreadable segments are logged."""
    by_zone, segments = {}, []
    for seg in split_segments(text):
        surf, reason, period = segment_surf(seg, day)
        result = SegmentResult(seg.name, seg.zones, surf, reason, period)
        segments.append(result)
        for zone in seg.zones:
            by_zone.setdefault(zone, result)
        if surf is None:
            parse_log.append(f"{office} {day} {product_id} [{seg.name}] "
                             f"{'+'.join(seg.zones) or 'no zones'}: {reason}")
    return DayProduct(office, day, product_id, issued, by_zone, tuple(segments))


def choose_products(get, candidates: dict, days, offices, parse_log: list, progress) -> dict:
    """{(office, day): DayProduct} from each office's first issuance of each local day.

    The earliest product of the day is skipped only when its own issuance line puts it on
    another local day: one issued just before midnight and stamped just after. Any other
    failure leaves the day without a product, logged, rather than quietly using a later one.
    """
    chosen = {}
    for office in offices:
        for day in days:
            listed = candidates.get((office, day), [])
            if not listed:
                parse_log.append(f"{office} {day}: no SRF listed for this local day")
                continue
            for product in listed[:3]:
                try:
                    text = get(IEM_TEXT_URL.format(product_id=product.product_id))
                except FetchError as e:
                    parse_log.append(f"{office} {day} {product.product_id}: {e}")
                    break
                issued, why = parse_issuance(text)
                if issued is None:
                    parse_log.append(f"{office} {day} {product.product_id}: {why}")
                    break
                if issued.local.date() != day:
                    parse_log.append(f"{office} {day} {product.product_id}: issued "
                                     f"{issued.local:%Y-%m-%d %H:%M} {issued.tz}, another local day")
                    continue
                chosen[(office, day)] = read_product(office, day, product.product_id, text,
                                                     issued, parse_log)
                break
        progress(f"  {office}: first issuance read for {sum(1 for k in chosen if k[0] == office)}"
                 f" of {len(days)} local days")
    return chosen


# --------------------------------------------------------------------------- #
# Zones and spots                                                              #
# --------------------------------------------------------------------------- #

def rings(geom) -> list:
    """Outer rings of a GeoJSON Polygon, MultiPolygon or GeometryCollection."""
    if not geom:
        return []
    if geom.get("type") == "Polygon":
        return [geom["coordinates"][0]]
    if geom.get("type") == "MultiPolygon":
        return [p[0] for p in geom["coordinates"]]
    if geom.get("type") == "GeometryCollection":
        return [r for g in geom.get("geometries", []) for r in rings(g)]
    return []


def dist_km(lat: float, lon: float, ring) -> float:
    """0 inside the ring, else km to its nearest edge (equirectangular; fine at this scale)."""
    kx, ky = 111.32 * math.cos(math.radians(lat)), 110.57
    inside, best = False, float("inf")
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
        ax, ay, dx, dy = x1 * kx, y1 * ky, (x2 - x1) * kx, (y2 - y1) * ky
        t = max(0.0, min(1.0, ((lon * kx - ax) * dx + (lat * ky - ay) * dy) / (dx * dx + dy * dy or 1)))
        best = min(best, math.hypot(lon * kx - ax - t * dx, lat * ky - ay - t * dy))
    return 0.0 if inside else best


def nearest_zone(lat: float, lon: float, geoms: dict) -> tuple[str | None, float]:
    """(zone, km) of the nearest zone, 0 km when inside one. Ties go to the lower zone id."""
    best_zone, best_km = None, float("inf")
    for zone in sorted(geoms):
        km = min(dist_km(lat, lon, ring) for ring in geoms[zone])
        if km < best_km:
            best_zone, best_km = zone, km
    return best_zone, best_km


def map_spots(spots, geoms: dict, max_km: float = MAX_ZONE_KM) -> tuple[dict, list]:
    """({slug: (zone, km)} for spots inside a zone or within *max_km* of the nearest one,
    [(spot, nearest zone or None, km)] for the rest)."""
    mapped, unmapped = {}, []
    for s in spots:
        zone, km = nearest_zone(s["lat"], s["lng"], geoms)
        if zone is not None and km <= max_km:
            mapped[s["slug"]] = (zone, km)
        else:
            unmapped.append((s, zone, km))
    return mapped, unmapped


def load_geometry_cache(path: str) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_geometry_cache(path: str, cache: dict) -> None:
    """THE ONE WRITE. Zone geometry only, to a gitignored cache, replaced atomically."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, path)


def zone_geometries(get, zones, cache_path: str, parse_log: list, progress) -> dict:
    """{zone: [rings]} from api.weather.gov, via the cache."""
    cache = load_geometry_cache(cache_path)
    cached = sum(1 for z in zones if z in cache)
    fetched = 0
    for zone in sorted(zones):
        if zone in cache:
            continue
        errors = []
        for template in NWS_ZONE_URLS:
            try:
                geom = (json.loads(get(template.format(zone=zone))) or {}).get("geometry")
            except (FetchError, ValueError) as e:
                errors.append(str(e))
                continue
            if rings(geom):
                cache[zone] = geom
                fetched += 1
                break
            errors.append(f"no polygon at {template.format(zone=zone)}")
        else:
            parse_log.append(f"zone {zone}: no geometry from api.weather.gov ({'; '.join(errors)})")
    if fetched:
        save_geometry_cache(cache_path, cache)
    progress(f"  zone geometry: {len(zones)} zones: {cached} from the cache, {fetched} fetched, "
             f"{len(zones) - cached - fetched} without geometry")
    return {z: rings(cache[z]) for z in zones if z in cache and rings(cache[z])}


def calibrated_spots(roster_path: str = ROSTER, factors_path: str = FACTORS) -> list:
    """The calibrated spots, keyed the way the factor builder and the correction key them."""
    from pipeline.enrich import _slug_for
    with open(factors_path) as f:
        factors = json.load(f)["factors"]
    with open(roster_path) as f:
        roster = json.load(f)
    out = []
    for s in roster:
        slug = _slug_for(s.get("name"))
        if slug in factors and s.get("is_valid_surf_spot") is not False:
            out.append({"slug": slug, "name": s["name"], "lat": s["lat"], "lng": s["lng"],
                        "wfo": (s.get("nwps_wfo") or "").upper(), "factor": factors[slug]["factor"]})
    return out


# --------------------------------------------------------------------------- #
# Our heights                                                                  #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DayValues:
    max_cal: float
    max_raw: float
    mean_cal: float
    mean_raw: float


def _utc(value) -> datetime.datetime:
    t = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def daytime_values(rows, tz) -> tuple[dict, dict]:
    """({local date: DayValues}, {local date: why skipped}) over local 06:00 to 18:00.

    Both ends are included, so a day needs all 13 hourly rows with face_ft and face_ft_raw
    present. A row off the hour, or outside the hours, is not part of any day.
    """
    by_day = {}
    for r in rows:
        t = _utc(r["valid_time"]).astimezone(tz)
        if t.minute or t.second or not DAY_FIRST_HOUR <= t.hour <= DAY_LAST_HOUR:
            continue
        by_day.setdefault(t.date(), {})[t.hour] = (r.get("face_ft"), r.get("face_ft_raw"))
    hours = range(DAY_FIRST_HOUR, DAY_LAST_HOUR + 1)
    values, skipped = {}, {}
    for day, got in sorted(by_day.items()):
        missing = [h for h in hours if h not in got or got[h][0] is None or got[h][1] is None]
        if missing:
            skipped[day] = f"missing or null at local hours {missing}"
            continue
        cal = [float(got[h][0]) for h in hours]
        raw = [float(got[h][1]) for h in hours]
        values[day] = DayValues(max(cal), max(raw), sum(cal) / len(cal), sum(raw) / len(raw))
    return values, skipped


def read_forecasts(names, t0_iso: str, t1_iso: str) -> dict:
    """{spot name: rows}, read exactly as the MOP harness reads them: select only, chunked
    over spot ids, paged by id. Spots missing from the spots table are left out."""
    from mop_face_validation import fetch_forecasts
    from pipeline.db_import import _spot_id_map, get_client
    client = get_client()
    name_to_id = _spot_id_map(client)
    ids = {name_to_id[n]: n for n in names if n in name_to_id}
    with contextlib.redirect_stdout(sys.stderr):   # the harness prints its chunk progress
        by_id = fetch_forecasts(client, sorted(ids), t0_iso, t1_iso)
    return {ids[i]: rows for i, rows in by_id.items() if i in ids}


# --------------------------------------------------------------------------- #
# Comparing                                                                    #
# --------------------------------------------------------------------------- #

def classify(value: float, surf: SurfHeight) -> str:
    """'below', 'inside' or 'above' NWS's range. Both ends of the range count as inside."""
    if value < surf.low:
        return "below"
    if value > surf.high:
        return "above"
    return "inside"


def ratio_to_midpoint(value: float, surf: SurfHeight) -> float | None:
    """value / the range's midpoint, or None when the midpoint is 0 ('flat')."""
    return value / surf.midpoint if surf.midpoint > 0 else None


@dataclass(frozen=True)
class SpotDay:
    office: str
    segment: str
    slug: str
    day: datetime.date
    nws: SurfHeight
    ours: DayValues


def summarise(records, metric: str) -> dict:
    """Counts below/inside/above and the median ratio to the midpoint, for calibrated and
    raw, using the daily *metric* ('max' or 'mean')."""
    out = {"n": len(records), "sets": sum(1 for r in records if r.nws.sets is not None),
           "around": sum(1 for r in records if r.nws.kind in ("around", "single"))}
    for which, field in (("cal", f"{metric}_cal"), ("raw", f"{metric}_raw")):
        values = [(getattr(r.ours, field), r.nws) for r in records]
        side = [classify(v, s) for v, s in values]
        ratios = [x for x in (ratio_to_midpoint(v, s) for v, s in values) if x is not None]
        out[which] = {"below": side.count("below"), "inside": side.count("inside"),
                      "above": side.count("above"),
                      "median_ratio": statistics.median(ratios) if ratios else None,
                      "ratios": len(ratios)}
    return out


def median_text(median: float) -> str:
    """Two decimals, or as many more as it takes to show which side of the limit it is on,
    so a printed 1.25 is never followed by 'no'. The rule compares the exact value."""
    for digits in (2, 3, 4, 6):
        text = f"{median:.{digits}f}"
        if (float(text) <= RAW_RATIO_LIMIT) == (median <= RAW_RATIO_LIMIT):
            return text
    return repr(median)


def rule_lines(label: str, s: dict) -> list:
    """The printed rule, applied to one office's (or all offices') numbers."""
    if not s["n"]:
        return [f"{label}: no spot-days"]
    raw_in, cal_in = s["raw"]["inside"] / s["n"], s["cal"]["inside"] / s["n"]
    median = s["raw"]["median_ratio"]
    more = raw_in > cal_in
    within = median is not None and median <= RAW_RATIO_LIMIT
    lines = [f"{label}: raw inside {raw_in:.1%} vs calibrated {cal_in:.1%}, more often: "
             f"{'yes' if more else 'no'}; raw median ratio "
             f"{'n/a' if median is None else median_text(median)} at most {RAW_RATIO_LIMIT}: "
             f"{'yes' if within else 'no'} -> raw {'SUPPORTED' if more and within else 'not supported'}"]
    if median is not None and median > RAW_RATIO_LIMIT:
        lines.append(f"{label}: WARNING raw's median ratio {median_text(median)} is above "
                     f"{RAW_RATIO_LIMIT}: rating on raw would put us well above NWS forecasters.")
    return lines


# --------------------------------------------------------------------------- #
# Fetching                                                                     #
# --------------------------------------------------------------------------- #

class FetchError(Exception):
    """A GET that failed for good: an HTTP status, or a transport error after retries."""


class Pacer:
    """At least *interval* seconds between the starts of consecutive requests."""

    def __init__(self, interval=MIN_REQUEST_INTERVAL_S, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep, self.last = interval, clock, sleep, None

    def wait(self) -> None:
        now = self.clock()
        if self.last is not None and now - self.last < self.interval:
            self.sleep(self.interval - (now - self.last))
            now = self.clock()
        self.last = now


def make_get(pacer: Pacer | None = None):
    """GET -> text through pipeline.http (requests; retries 429/5xx with backoff of 2 s or
    more), paced and labelled with this script's contact."""
    pacer = pacer or Pacer()

    def get(url: str) -> str:
        import requests
        from pipeline.http import get as http_get
        pacer.wait()
        try:
            return http_get(url, headers={"User-Agent": USER_AGENT}, timeout=60).text
        except requests.HTTPError as e:
            raise FetchError(f"HTTP {getattr(e.response, 'status_code', '?')} on {url}") from e
        except Exception as e:  # retries exhausted, connection refused, timeout
            raise FetchError(f"{type(e).__name__} on {url}: {e}") from e
    return get


def fetch_listing(get, dates, parse_log: list) -> list:
    """Every SRF of our offices that IEM lists for each UTC date."""
    out = []
    for d in dates:
        try:
            out += parse_listing(json.loads(get(IEM_LIST_URL.format(date=d.isoformat()))))
        except (FetchError, ValueError) as e:
            parse_log.append(f"IEM listing for {d}: {e}")
    return out


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #

def _pct(n: int, total: int) -> str:
    return f"{n / total:6.1%}" if total else "     -"


def _ratio(x) -> str:
    return "   -" if x is None else f"{x:4.2f}"


def table_rows(groups: list, metric: str) -> list:
    """Fixed-width rows: label, spot-days, then below/inside/above and median ratio for
    calibrated and for raw. *groups* is [(label, records, extra)]."""
    head = (f"{'':34} {'spot-':>6} | {'calibrated (face_ft)':^34} | {'raw (face_ft_raw)':^34} |\n"
            f"{'':34} {'days':>6} | {'below':>7} {'inside':>7} {'above':>7} {'median':>8} "
            f"| {'below':>7} {'inside':>7} {'above':>7} {'median':>8} | {'sets':>5}  notes")
    out = [head]
    for label, records, extra in groups:
        s = summarise(records, metric)
        cells = []
        for which in ("cal", "raw"):
            c = s[which]
            cells.append(f"{_pct(c['below'], s['n']):>7} {_pct(c['inside'], s['n']):>7} "
                         f"{_pct(c['above'], s['n']):>7} {_ratio(c['median_ratio']):>8}")
        out.append(f"{label[:34]:34} {s['n']:6d} | {cells[0]} | {cells[1]} | {s['sets']:5d}  {extra}")
    return out


def report_results(records: list, metric: str, title: str, factors_by_office: dict) -> list:
    lines = [f"\n== {title}"]
    groups = []
    for o in OFFICES:   # every office, so one with no spot-days says so rather than vanishing
        f = factors_by_office.get(o, [])
        extra = f"factors {min(f):.2f}-{max(f):.2f}" if f else ""
        groups.append((o, [r for r in records if r.office == o], extra))
    groups.append(("ALL", records, ""))
    lines += table_rows(groups, metric)
    lines.append("\n  The rule, office by office (SGX has little power; read it that way):")
    for label, recs, _ in groups:
        lines += ["  " + ln for ln in rule_lines(label, summarise(recs, metric))]
    lines.append(f"\n-- Per segment ({metric})")
    segs = sorted({(r.office, r.segment) for r in records}, key=lambda k: (OFFICES.index(k[0]), k[1]))
    seg_groups = []
    for office, segment in segs:
        recs = [r for r in records if r.office == office and r.segment == segment]
        n_spots = len({r.slug for r in recs})
        seg_groups.append((f"{office} {segment}", recs, f"{n_spots} spot{'' if n_spots == 1 else 's'}"))
    lines += table_rows(seg_groups, metric)
    return lines


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #

def local_days(start: datetime.date, last: datetime.date, tz, now: datetime.datetime) -> list:
    """Local dates start..last whose 18:00 has passed, so all 13 hours are past."""
    days, d = [], start
    while d <= last:
        if datetime.datetime(d.year, d.month, d.day, DAY_LAST_HOUR, tzinfo=tz) <= now:
            days.append(d)
        d += datetime.timedelta(days=1)
    return days


def run(end=None, *, get=None, read_rows=None, now=None, tz=None, spots=None,
        cache_path: str = CACHE_PATH, out=None, err=None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr

    def emit(line=""):
        print(line, file=out)

    def progress(line):
        print(line, file=err, flush=True)

    emit(READING_RULE)
    emit()
    if tz is None:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(LOCAL_TZ)
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    last_complete = now.date() - datetime.timedelta(days=1)
    last = end or last_complete
    if not WINDOW_START <= last <= last_complete:
        emit(f"--end {last} must fall between {WINDOW_START} and the last complete UTC day, "
             f"{last_complete}")
        return 2
    days = local_days(WINDOW_START, last, tz, now)
    get = get or make_get()
    read_rows = read_rows or read_forecasts
    spots = calibrated_spots() if spots is None else spots
    parse_log = []

    emit("SRF benchmark: NWS Surf Zone Forecast surf height against our heights at the "
         "calibrated spots")
    emit(f"Window: {WINDOW_START} 00:00 UTC to the last complete UTC day, {last}. Local days "
         f"used: {days[0] if days else '-'} to {days[-1] if days else '-'} ({len(days)}).")
    emit(f"Ours: max (and, as a check, mean) of face_ft and face_ft_raw over local "
         f"{DAY_FIRST_HOUR:02d}:00 to {DAY_LAST_HOUR:02d}:00 {LOCAL_TZ}, both included, "
         f"source 'nwps'.")
    emit(f"NWS: offices {', '.join(OFFICES)}; first issuance per local day; first daytime "
         f"period; main range low to high, sets recorded separately; inside includes both ends.")

    progress("listing SRFs on IEM…")
    dates = [WINDOW_START + datetime.timedelta(days=i) for i in range((last - WINDOW_START).days + 2)]
    candidates = candidates_by_local_day(fetch_listing(get, dates, parse_log), tz)
    progress("reading each office's first issuance per local day…")
    chosen = choose_products(get, candidates, days, OFFICES, parse_log, progress)

    zone_office = {}
    for (office, _day), product in sorted(chosen.items()):
        for zone in product.by_zone:
            zone_office.setdefault(zone, office)
    geoms = zone_geometries(get, set(zone_office), cache_path, parse_log, progress)

    emit("\n== Products")
    emit(f"{'office':8} {'local days':>10} {'first issuance read':>20} {'segments':>9} "
         f"{'unreadable':>11}")
    for office in OFFICES:
        prods = [p for (o, _d), p in chosen.items() if o == office]
        results = [r for p in prods for r in p.segments]
        emit(f"{office:8} {len(days):10d} {len(prods):20d} {len(results):9d} "
             f"{sum(1 for r in results if r.surf is None):11d}")

    mapped, unmapped = map_spots(spots, geoms)
    emit(f"\n== Spots mapped to an SRF zone (inside one, or nearest within {MAX_ZONE_KM:g} km)")
    emit(f"{'office':8} {'calibrated':>10} {'mapped':>7} {'unmapped':>9}")
    for office in OFFICES:
        mine = [s for s in spots if s["wfo"] == office]
        emit(f"{office:8} {len(mine):10d} {sum(1 for s in mine if s['slug'] in mapped):7d} "
             f"{sum(1 for s in mine if s['slug'] not in mapped):9d}")
    emit(f"{'ALL':8} {len(spots):10d} {len(mapped):7d} {len(unmapped):9d}")
    for s, zone, km in sorted(unmapped, key=lambda u: (u[0]["wfo"], u[0]["name"])):
        near = f"nearest {zone} at {km:.1f} km" if zone else "no zone geometry at all"
        emit(f"  unmapped {s['wfo']} {s['name']}: {near}")
    # Results group spot-days by the office whose SRF names the zone; this table groups spots
    # by their NWPS office. Where the two differ, say so.
    for s in sorted(spots, key=lambda s: (s["wfo"], s["name"])):
        zone = mapped.get(s["slug"], (None, None))[0]
        if zone is not None and zone_office[zone] != s["wfo"]:
            emit(f"  cross-office {s['wfo']} {s['name']}: {zone} is in {zone_office[zone]}'s SRF, "
                 f"so its spot-days count under {zone_office[zone]}")

    by_slug = {s["slug"]: s for s in spots}
    names = [by_slug[slug]["name"] for slug in mapped]
    records, excluded = [], {}

    def exclude(office, why):
        excluded[(office, why)] = excluded.get((office, why), 0) + 1

    if days and names:
        t0 = datetime.datetime(days[0].year, days[0].month, days[0].day, DAY_FIRST_HOUR, tzinfo=tz)
        t1 = datetime.datetime(days[-1].year, days[-1].month, days[-1].day, DAY_LAST_HOUR, tzinfo=tz)
        progress(f"reading our forecasts for {len(names)} spots from Supabase (select only)…")
        rows_by_name = read_rows(names, t0.astimezone(datetime.timezone.utc).isoformat(),
                                 t1.astimezone(datetime.timezone.utc).isoformat())
        for slug, (zone, _km) in sorted(mapped.items()):
            spot = by_slug[slug]
            office = zone_office[zone]
            has_rows = bool(rows_by_name.get(spot["name"]))
            values, _skipped = daytime_values(rows_by_name.get(spot["name"], []), tz)
            for day in days:
                product = chosen.get((office, day))
                result = product.by_zone.get(zone) if product else None
                if product is None:
                    exclude(office, "no first issuance read for that day")
                elif result is None:
                    exclude(office, "zone not in that day's SRF")
                elif result.surf is None:
                    exclude(office, "segment unreadable that day")
                elif not has_rows:
                    exclude(office, "no forecast rows for the spot (or not in the spots table)")
                elif day not in values:
                    exclude(office, "our rows incomplete for 06:00 to 18:00")
                else:
                    records.append(SpotDay(office, result.name, slug, day, result.surf, values[day]))

    factors_by_office = {}
    for slug in {r.slug for r in records}:
        office = next(r.office for r in records if r.slug == slug)
        factors_by_office.setdefault(office, []).append(by_slug[slug]["factor"])

    for line in report_results(records, "max", "Results: daily MAX over 06:00 to 18:00",
                               factors_by_office):
        emit(line)
    for line in report_results(records, "mean", "Check: daily MEAN over 06:00 to 18:00",
                               factors_by_office):
        emit(line)

    emit("\n== Spot-days left out")
    if not excluded:
        emit("  none")
    for (office, why), n in sorted(excluded.items(), key=lambda kv: (OFFICES.index(kv[0][0]), kv[0][1])):
        emit(f"  {office} {why}: {n}")
    emit("\n== Notes: around/single heights are zero-width ranges, flat gives no ratio")
    emit(f"  spot-days on an 'around N' or bare 'N feet' height: {summarise(records, 'max')['around']}")
    emit(f"  spot-days with no ratio (flat): {len(records) - summarise(records, 'max')['cal']['ratios']}")
    emit(f"\n== Log: everything this could not read ({len(parse_log)})")
    for line in parse_log:
        emit(f"  {line}")
    return 0 if records else 1


def _date(text: str) -> datetime.date:
    return datetime.datetime.strptime(text, "%Y-%m-%d").date()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--end", type=_date, default=None,
                    help="last local day, YYYY-MM-DD (default: the last complete UTC day)")
    args = ap.parse_args(argv)
    return run(end=args.end)


if __name__ == "__main__":
    sys.exit(main())
