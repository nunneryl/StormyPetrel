"""What a spot description says, how it is written, and the signature that says when it is stale.

ONE BUILDER, TWO USERS. pipeline/generate_descriptions.py renders description_payload() through the
template below and stamps the text with description_signature() of that same payload.
pipeline/db_import.py rebuilds the payload from the roster on every run and blanks a description whose
stored signature no longer matches. Both call the functions below, so they cannot disagree about what a
description asserts.

WHAT GOES IN. Only what the roster knows, and only what it knows from a source that can be stated
plainly. break_type, bottom and tide_preference go in only when their source is reviewed, researched or
scraped (a person set it, or a cited page says so). A model_recall, unattributed, algorithm_default,
derived_from_break_type or missing source is LEFT OUT, not hedged: a description then never contradicts
the page — it agrees with a verified value or says nothing. A value of 'unknown' (someone looked and
could not tell) is left out too, since there is nothing to state. Left out entirely:
  the nearest buoy and tide station ids — the model used to quote them ("Tide preferences are
      referenced to station 8652226");
  the swell window — the arcs are not trustworthy enough to state: the sample run of 2026-10-13 had
      Banzai Pipeline and Waimea Bay taking S–SW swell on Oahu's North Shore, Aviones (north coast of
      Puerto Rico) SE–S, Matanzas Inlet (Florida's east coast) S/SW and SW/W, and Seaside Reef, which
      faces west, E–SE. faces and optimal_swell_from stay.

WHY A TEMPLATE, NOT A MODEL. Once the payload was cut down to verified facts, a language model added
nothing a template cannot state, and failed at the spots with the fewest facts: in the 2026-10-13
samples it twice answered Pensacola Beach with self-talk ("Wait, I need to revise — I was instructed
not to mention missing fields"). render() states exactly the payload's name, state, break type,
bottom, facing, optimal swell direction and offshore wind, leaving out whatever is absent, in one of
PATTERN_COUNT sentence patterns picked by a hash of the slug so the pages do not all read alike. The
tide preference is in the payload (and so in the signature) but is not stated. Two more facts are
held back from the sentence while staying in the payload, so that correcting them rewrites the text:
  the optimal swell direction, when it is more than SWELL_MAX_OFF_FACING_DEG from the direction the
      spot faces — a swell from behind the beach comes from the faulty swell windows or a wrong
      orientation (31 spots on 2026-10-13, e.g. Spoils faces E with an optimal W);
  the state, when the spot's name already holds it ("Dana Point, California", "Sunset Beach Hawaii",
      "North End Virginia Beach": 13 spots).

WHY THE SIGNATURE HASHES THE PAYLOAD. Version 1 hashed coordinates, state and orientation only, so a
break_type corrected from beach to reef never made the description stale: 105 descriptions came to
contradict the displayed break type after the research import (#245). Version 2 hashes
TEMPLATE_VERSION and the exact payload, so any change to anything the description is built from — a
field, its source crossing the stated/left-out line, or the template — marks it stale.

    v2:<16 hex>    written ONLY by generate_descriptions, alongside the text it describes
    anything else  a version-1 (or missing) signature: db_import leaves that text alone

Change the template's wording and you must bump TEMPLATE_VERSION; pipeline/tests/test_descriptions.py
pins both.
"""
from __future__ import annotations

import hashlib
import json
import re

TEMPLATE_VERSION = "t2"
SIGNATURE_PREFIX = "v2:"
STATED_SOURCES = ("reviewed", "researched", "scraped")
GATED_FIELDS = ("break_type", "bottom", "tide_preference")  # each stated only from STATED_SOURCES

_CARDINALS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def deg_to_cardinal(deg):
    if deg is None:
        return None
    return _CARDINALS_8[round((float(deg) % 360) / 45) % 8]


def _whole(deg):
    return None if deg is None else int(round(float(deg)))


def stated(value, source):
    """The value when its source may be stated plainly, else None."""
    if value in (None, "", "unknown"):
        return None
    return value if source in STATED_SOURCES else None


def description_payload(rec):
    """What a description may state about one spot. `rec` uses the spots-table names (state, not
    region_hint) — db_import's record, or a roster spot through spot_fields(). Nulls and empties are
    dropped, so an absent fact is absent rather than null."""
    payload = {
        "name": rec.get("name"),
        "state": rec.get("state"),
        "break_type": stated(rec.get("break_type"), rec.get("break_type_source")),
        "bottom": stated(rec.get("bottom"), rec.get("bottom_source")),
        "faces": deg_to_cardinal(rec.get("orientation_deg")),
        "orientation_deg": _whole(rec.get("orientation_deg")),
        "offshore_wind_from": deg_to_cardinal(rec.get("offshore_wind_deg")),
        "offshore_wind_deg": _whole(rec.get("offshore_wind_deg")),
        "tide_preference": stated(rec.get("tide_preference"), rec.get("tide_preference_source")),
        "optimal_swell_from": deg_to_cardinal(rec.get("optimal_swell_dir")),
        "optimal_swell_deg": _whole(rec.get("optimal_swell_dir")),
    }
    return {k: v for k, v in payload.items() if v not in (None, "", [])}


PAYLOAD_INPUTS = ("name", "break_type", "break_type_source", "bottom", "bottom_source",
                  "orientation_deg", "offshore_wind_deg", "tide_preference",
                  "tide_preference_source", "optimal_swell_dir")


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
DIRECTION_WORDS = {"N": "north", "NE": "northeast", "E": "east", "SE": "southeast",
                   "S": "south", "SW": "southwest", "W": "west", "NW": "northwest"}
BREAK_WORDS = {"beach": "beach break", "reef": "reef break", "point": "point break",
               "jetty": "jetty break", "rivermouth": "rivermouth break"}
NO_BREAK_TYPE = "surf spot"
SWELL_MAX_OFF_FACING_DEG = 90


def _an(word):
    return "an" if word[:1] and word[:1].lower() in "aeiou" else "a"


def _sentence(clauses):
    """Clauses joined with ', and ', first letter raised, full stop; None when there are none."""
    clauses = [c for c in clauses if c]
    if not clauses:
        return None
    text = ", and ".join(clauses)
    return text[0].upper() + text[1:] + "."


def _degrees_apart(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _swell_behind(payload):
    """True when the optimal swell is more than SWELL_MAX_OFF_FACING_DEG from where the spot faces."""
    facing, swell = payload.get("orientation_deg"), payload.get("optimal_swell_deg")
    if facing is None or swell is None:
        return False
    return _degrees_apart(facing, swell) > SWELL_MAX_OFF_FACING_DEG


def _name_holds(name, state):
    """True when *state* appears in *name* as whole words, in any case."""
    return bool(state) and re.search(r"(?<!\w)" + re.escape(state) + r"(?!\w)", name, re.I) is not None


def _facts(payload):
    """The payload's words: name, state, kind of break, bottom and the three directions. The state
    is dropped when the name already holds it, the swell when it comes from behind the spot."""
    bt = payload.get("break_type")
    state = payload.get("state")
    return {
        "name": payload["name"],
        "state": None if _name_holds(payload["name"], state) else state,
        "kind": BREAK_WORDS.get(bt, f"{bt} break") if bt else NO_BREAK_TYPE,
        "bottom": payload.get("bottom"),
        "faces": DIRECTION_WORDS.get(payload.get("faces")),
        "swell": (None if _swell_behind(payload)
                  else DIRECTION_WORDS.get(payload.get("optimal_swell_from"))),
        "wind": DIRECTION_WORDS.get(payload.get("offshore_wind_from")),
    }


def _pattern_0(f):
    # Banzai Pipeline is a reef break in Hawaii over a rock bottom. It faces northwest, and its
    # optimal swell direction is north. Offshore winds blow from the southeast.
    first = (f"{f['name']} is {_an(f['kind'])} {f['kind']}"
             + (f" in {f['state']}" if f["state"] else "")
             + (f" over {_an(f['bottom'])} {f['bottom']} bottom" if f["bottom"] else "") + ".")
    return [first,
            _sentence([f["faces"] and f"it faces {f['faces']}",
                       f["swell"] and f"its optimal swell direction is {f['swell']}"]),
            _sentence([f["wind"] and f"offshore winds blow from the {f['wind']}"])]


def _pattern_1(f):
    # Banzai Pipeline is a Hawaii reef break with a rock bottom. It faces northwest, and it is
    # offshore in southeast winds. The optimal swell comes from the north.
    lead = f"{f['state']} {f['kind']}" if f["state"] else f["kind"]
    first = (f"{f['name']} is {_an(lead)} {lead}"
             + (f" with {_an(f['bottom'])} {f['bottom']} bottom" if f["bottom"] else "") + ".")
    return [first,
            _sentence([f["faces"] and f"it faces {f['faces']}",
                       f["wind"] and f"it is offshore in {f['wind']} winds"]),
            _sentence([f["swell"] and f"the optimal swell comes from the {f['swell']}"])]


def _pattern_2(f):
    # Facing northwest, Banzai Pipeline is a reef break in Hawaii over a rock bottom. The optimal
    # swell comes from the north, and the offshore wind is from the southeast.
    first = ((f"Facing {f['faces']}, " if f["faces"] else "")
             + f"{f['name']} is {_an(f['kind'])} {f['kind']}"
             + (f" in {f['state']}" if f["state"] else "")
             + (f" over {_an(f['bottom'])} {f['bottom']} bottom" if f["bottom"] else "") + ".")
    return [first,
            _sentence([f["swell"] and f"the optimal swell comes from the {f['swell']}",
                       f["wind"] and f"the offshore wind is from the {f['wind']}"])]


PATTERNS = (_pattern_0, _pattern_1, _pattern_2)
PATTERN_COUNT = len(PATTERNS)


def pattern_for(slug):
    """Which pattern a spot gets: sha1 of its slug, mod PATTERN_COUNT. Stable across runs."""
    return int(hashlib.sha1(slug.encode("utf-8")).hexdigest(), 16) % PATTERN_COUNT


def render(payload, slug):
    """The description of *payload*: every stated fact, nothing else, in the slug's pattern."""
    return " ".join(s for s in PATTERNS[pattern_for(slug)](_facts(payload)) if s)
