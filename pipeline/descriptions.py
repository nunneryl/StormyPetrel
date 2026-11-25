"""What a spot description says, how it is written, and the signature that says when it is stale.

ONE BUILDER, TWO USERS. pipeline/generate_descriptions.py renders description_payload() through the
template below and stamps the text with description_signature() of that same payload.
pipeline/db_import.py rebuilds the payload from the roster on every run and blanks a description whose
stored signature no longer matches. Both call the functions below, so they cannot disagree about what a
description asserts.

TEMPLATE t4 (descriptions v2): SAY WHAT THE PAGE'S CARDS DO NOT. The Optimal conditions card already
gives the swell direction, the offshore wind, the tide and the break type, so the description no longer
repeats the directions. It says, from committed inputs only, nothing from model recall:
  1. what the spot is: its break type and bottom, each only from a reviewed, researched or scraped
     source (STATED_SOURCES), and from that research's own words, re-said, never quoted: whether the
     wave is a left, a right or both, and whether the spot is exposed or sheltered
     (evidence_traits: a fixed list of phrases; a text that says both, or neither, says nothing);
  2. its tide preference, from a stated source only (TIDE_SENTENCES);
  3. where its data comes from: the nearest wave buoy within BUOY_MAX_KM, by its NDBC name with the
     number in brackets ("the Waimea Bay buoy (NDBC 51201)", pipeline/data/buoy_names.json) or, where
     NDBC gives only a code, "NDBC buoy 41067", and its distance; and its NOAA tide station, by name
     and distance, no number (pipeline/data/tide_station_names.json), "tide times (not heights)"
     where the height is withheld;
  4. its live cam, if it has one (pipeline/data/cam_seed.json);
  5. up to NEARBY_N other spots within NEARBY_MAX_KM, nearest first, by name and distance. The spot
     page lists nearby spots nowhere else, and turns these names into links
     (frontend/lib/spotDescription.ts), so the sentence's shape is a contract: "Nearby spots: "
     then "<shown name> (<n> km)" items joined by ", " and " and ", then ".".
Left out entirely: hazards and crowds (never sourced), the swell window, anything whose only source is
model recall, and any field with no value. A missing fact is left out, never hedged.

BEST MONTHS ARE HELD (2026-11-25). pipeline/best_months.py (sw1-raycast mode best-months) can score
the months each spot rated best in our own forecasts, but the archive starts in April 2026, so no
northern-winter month can win yet. Revisit around April 2027, once it covers a full year; adding it
then is a new template version.

WHY A TEMPLATE, NOT A MODEL. A language model added nothing a template cannot state from these facts,
and failed at the spots with the fewest (2026-10-13: Pensacola Beach answered with self-talk).

WHY THE SIGNATURE HASHES THE PAYLOAD. The signature is 'v2:' + a hash of TEMPLATE_VERSION and the exact
payload, so any change to anything a description states (a field, its source crossing the stated line,
a renamed station, a moved buoy, a new neighbour) or to the template marks it stale.

    v2:<16 hex>    written ONLY by generate_descriptions, alongside the text it describes
    anything else  a version-1 (or missing) signature: db_import leaves that text alone

Change the template's wording and you must bump TEMPLATE_VERSION; pipeline/tests/test_descriptions.py
pins both.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

TEMPLATE_VERSION = "t4"
SIGNATURE_PREFIX = "v2:"
STATED_SOURCES = ("reviewed", "researched", "scraped")
GATED_FIELDS = ("break_type", "bottom", "tide_preference")  # each stated only from STATED_SOURCES

DATA = Path(__file__).resolve().parent / "data"
BUOY_MAX_KM = 150          # the page's own rule: no buoy is named beyond it (lib/buoyDisplay)
NEARBY_MAX_KM = 25
NEARBY_N = 3

_CARDINALS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def deg_to_cardinal(deg):
    if deg is None:
        return None
    return _CARDINALS_8[round((float(deg) % 360) / 45) % 8]


def stated(value, source):
    """The value when its source may be stated plainly, else None."""
    if value in (None, "", "unknown"):
        return None
    return value if source in STATED_SOURCES else None


def _slug(name):
    from .db_import import _slugify       # lazily: db_import imports this module
    return _slugify(name or "")


def display_name(name, state):
    """The name the site shows (frontend/lib/spotName.ts): a trailing state, after a comma or a
    space, dropped; the name as it is when nothing would be left."""
    name = name or ""
    if not state:
        return name
    shown = re.sub(r"(?:,\s*|\s+)" + re.escape(state) + r"$", "", name, flags=re.I).strip()
    return shown or name


def _km(a_lat, a_lng, b_lat, b_lng):
    p = math.radians
    h = (math.sin(p(b_lat - a_lat) / 2) ** 2
         + math.cos(p(a_lat)) * math.cos(p(b_lat)) * math.sin(p(b_lng - a_lng) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- #
# What research says, re-said                                                  #
# --------------------------------------------------------------------------- #
_LEFT = re.compile(r"\bleft[- ]?hand(?:er|ed)?\b|\blefthanders?\b", re.I)
_RIGHT = re.compile(r"\bright[- ]?hand(?:er|ed)?\b|\brighthanders?\b", re.I)
_BOTH = re.compile(r"\blefts? and rights?\b|\brights? and lefts?\b|\bboth (?:a )?left and right\b|"
                   r"\bboth left and right\b", re.I)
_EXPOSED = re.compile(r"\bexposed\b", re.I)
_SHELTERED = re.compile(r"\bsheltered\b", re.I)


def evidence_traits(evidence):
    """(hand, exposure) a research quote states plainly, each None when it does not.

    hand: 'left' or 'right' when the text calls the wave left-hand or right-hand and not the other;
    'both' when it speaks of lefts and rights (or calls it both). exposure: 'exposed' or 'sheltered'
    when the text uses exactly one of the two words. Only these phrases count: the description
    re-says what the source says in our words, and says nothing when the source is not plain."""
    text = evidence or ""
    left, right, both = bool(_LEFT.search(text)), bool(_RIGHT.search(text)), bool(_BOTH.search(text))
    hand = "both" if both or (left and right) else "left" if left else "right" if right else None
    exp, shel = bool(_EXPOSED.search(text)), bool(_SHELTERED.search(text))
    exposure = "exposed" if exp and not shel else "sheltered" if shel and not exp else None
    return hand, exposure


# --------------------------------------------------------------------------- #
# The facts beyond the spot's own row                                          #
# --------------------------------------------------------------------------- #
class Context:
    """Everything a description states that is not on the spot's own row: the other spots (for the
    nearby ones), the cams, the tide station names and the buoy names. Built from committed files
    only, so db_import and generate_descriptions build the same one."""

    def __init__(self, roster, cams=None, station_names=None, buoy_names=None):
        self.spots = []
        for s in roster:
            if s.get("is_valid_surf_spot") is False or s.get("lat") is None or s.get("lng") is None:
                continue
            self.spots.append((_slug(s.get("name")), display_name(s.get("name"), s.get("region_hint")),
                               float(s["lat"]), float(s["lng"])))
        self.cams = {c["spot_slug"]: c for c in (cams or [])}
        self.station_names = station_names or {}
        self.buoy_names = buoy_names or {}

    def nearby(self, slug, lat, lng):
        if lat is None or lng is None:
            return []
        near = sorted((_km(float(lat), float(lng), la, lo), name) for s, name, la, lo in self.spots
                      if s != slug)
        return [[name, max(1, int(d + 0.5))] for d, name in near if d <= NEARBY_MAX_KM][:NEARBY_N]


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def context_for(roster):
    """The Context of *roster* (spots_enriched.json's spots) and the committed data files."""
    return Context(list(roster),
                   _read(DATA / "cam_seed.json", {}).get("cams", []),
                   _read(DATA / "tide_station_names.json", {}).get("stations", {}),
                   _read(DATA / "buoy_names.json", {}).get("stations", {}))


@lru_cache(maxsize=1)
def default_context():
    """The Context of the committed roster and data files (what db_import imports)."""
    from .config import DEFAULT_ENRICHED_OUTPUT
    return context_for(_read(DEFAULT_ENRICHED_OUTPUT, []))


# --------------------------------------------------------------------------- #
# The payload                                                                  #
# --------------------------------------------------------------------------- #
def description_payload(rec, ctx=None):
    """What a description may state about one spot. `rec` uses the spots-table names (state, not
    region_hint): db_import's record, or a roster spot through spot_fields(). *ctx* defaults to the
    committed files' Context. Absent facts are absent, never null."""
    ctx = ctx or default_context()
    name, state = rec.get("name"), rec.get("state")
    slug = rec.get("slug") or _slug(name)
    break_type = stated(rec.get("break_type"), rec.get("break_type_source"))
    hand, exposure = (evidence_traits(rec.get("break_type_evidence")) if break_type
                      else (None, None))
    buoy_id, buoy_km = rec.get("nearest_buoy_id"), rec.get("nearest_buoy_dist_km")
    buoy = None
    if buoy_id and buoy_km is not None and float(buoy_km) <= BUOY_MAX_KM:
        known = ctx.buoy_names.get(str(buoy_id).lower()) or {}
        buoy = [str(buoy_id), known.get("name"), max(1, int(float(buoy_km) + 0.5)),
                "station" if known and "buoy" not in (known.get("type") or "").lower() else "buoy"]
    tide_id, tide_km = rec.get("nearest_tide_station_id"), rec.get("nearest_tide_station_dist_km")
    tide = ([tide_id, ctx.station_names.get(str(tide_id)), max(1, int(float(tide_km) + 0.5)),
             bool(rec.get("tide_height_suppressed"))]
            if tide_id and tide_km is not None else None)
    cam = ctx.cams.get(slug)
    payload = {
        "name": display_name(name, state),
        "state": state,
        "break_type": break_type,
        "hand": hand,
        "exposure": exposure,
        "bottom": stated(rec.get("bottom"), rec.get("bottom_source")),
        "tide_preference": stated(rec.get("tide_preference"), rec.get("tide_preference_source")),
        "buoy": buoy,
        "tide_station": tide,
        "cam": (cam.get("attribution") or cam.get("cam_name")) if cam else None,
        "nearby": ctx.nearby(slug, rec.get("lat"), rec.get("lng")) or None,
    }
    return {k: v for k, v in payload.items() if v not in (None, "", [])}


PAYLOAD_INPUTS = ("name", "lat", "lng", "break_type", "break_type_source", "break_type_evidence",
                  "bottom", "bottom_source", "tide_preference", "tide_preference_source",
                  "nearest_buoy_id", "nearest_buoy_dist_km", "nearest_tide_station_id",
                  "nearest_tide_station_dist_km", "tide_height_suppressed")


def spot_fields(spot):
    """A roster spot (spots_enriched.json) under the spots-table names, exactly as db_import's
    _spot_record maps it: state from region_hint, every other input only when the key is present."""
    rec = {"state": spot.get("region_hint")}
    for k in PAYLOAD_INPUTS:
        if k in spot:
            rec[k] = spot[k]
    return rec


def canonical(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def description_signature(payload):
    """'v2:' + 16 hex of sha1(TEMPLATE_VERSION, the payload). Stable across key order."""
    digest = hashlib.sha1(f"{TEMPLATE_VERSION}\n{canonical(payload)}".encode("utf-8")).hexdigest()
    return SIGNATURE_PREFIX + digest[:16]


def is_v2(signature):
    return isinstance(signature, str) and signature.startswith(SIGNATURE_PREFIX)


# --------------------------------------------------------------------------- #
# The template                                                                 #
# --------------------------------------------------------------------------- #
BREAK_WORDS = {"beach": "beach break", "reef": "reef break", "point": "point break",
               "jetty": "jetty break", "rivermouth": "rivermouth break"}
NO_BREAK_TYPE = "surf spot"
HAND_WORDS = {"left": "left-hand", "right": "right-hand"}

TIDE_SENTENCES = {
    "low": "It works best around low tide.",
    "low_mid": "It works best from low to mid tide.",
    "mid": "It works best around mid tide.",
    "mid_high": "It works best from mid to high tide.",
    "high": "It works best around high tide.",
    "all": "It works at all stages of the tide.",
}

PATTERN_COUNT = 1


def pattern_for(slug):
    """One pattern since t4: the facts differ enough from spot to spot."""
    return 0


def _an(word):
    return "an" if word[:1] and word[:1].lower() in "aeiou" else "a"


def _name_holds(name, state):
    """True when *state* appears in *name* as whole words, in any case."""
    return bool(state) and re.search(r"(?<!\w)" + re.escape(state) + r"(?!\w)", name, re.I) is not None


def _list(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _identity(p):
    name, state = p["name"], p.get("state")
    where = f" in {state}" if state and not _name_holds(name, state) else ""
    kind = BREAK_WORDS.get(p.get("break_type"), f"{p.get('break_type')} break") if p.get("break_type") \
        else NO_BREAK_TYPE
    hand = p.get("hand")
    words = " ".join(w for w in (p.get("exposure"), HAND_WORDS.get(hand), kind) if w)
    tail = " with both lefts and rights" if hand == "both" else ""
    bottom = f" over {_an(p['bottom'])} {p['bottom']} bottom" if p.get("bottom") else ""
    return f"{name} is {_an(words)} {words}{where}{tail}{bottom}."


def _sources(p):
    buoy, tide = p.get("buoy"), p.get("tide_station")
    clauses = []
    if buoy:
        bid, name, km, kind = buoy
        ref = (f"the {name} {kind} (NDBC {bid.upper()})" if name
               else f"NDBC {kind} {bid.upper()}")
        clauses.append(f"its nearest wave {kind} is {ref}, {km} km away")
    if tide:
        _sid, name, km, times_only = tide
        station = f"NOAA's {name} station" if name else "a NOAA tide station"
        what = "tide times (not heights) come" if times_only else "tides come"
        clauses.append(f"its {what} from {station}, {km} km away")
    if not clauses:
        return None
    text = ", and ".join(clauses)
    return text[0].upper() + text[1:] + "."


def _cam(p):
    return f"This page has a live cam from {p['cam']}." if p.get("cam") else None


def _nearby(p):
    near = p.get("nearby")
    if not near:
        return None
    return "Nearby spots: " + _list([f"{n} ({km} km)" for n, km in near]) + "."


def render(payload, slug=None):
    """The description of *payload*: every stated fact in a fixed order, nothing else."""
    sentences = [_identity(payload), TIDE_SENTENCES.get(payload.get("tide_preference")),
                 _sources(payload), _cam(payload), _nearby(payload)]
    return " ".join(s for s in sentences if s)
