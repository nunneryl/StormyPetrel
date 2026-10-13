"""What a spot description may say, and the signature that says when it is stale.

ONE BUILDER, TWO USERS. pipeline/generate_descriptions.py sends description_payload() to the model,
and stamps the description with description_signature() of that same payload. pipeline/db_import.py
rebuilds the payload from the roster on every run and blanks a description whose stored signature no
longer matches. Both call the functions below, so they cannot disagree about what a description asserts.

WHAT GOES IN. Only what the roster knows, and only what it knows from a source that can be stated
plainly. break_type and bottom go in only when their source is reviewed, researched or scraped (a person
set it, or a cited page says so). A model_recall, unattributed or algorithm_default value is LEFT OUT,
not hedged: a description then never contradicts the break-type badge — it agrees with a verified value
or says nothing. A bottom of 'unknown' (someone looked and could not tell) is left out too, since there
is nothing to state. The nearest buoy and tide station ids are not sent: the model used to quote them
("Tide preferences are referenced to station 8652226").

WHY THE SIGNATURE HASHES THE PAYLOAD. Version 1 hashed coordinates, state and orientation only, so a
break_type corrected from beach to reef never made the description stale: 105 descriptions came to
contradict the displayed break type after the research import (#245). Version 2 hashes PROMPT_VERSION
and the exact payload, so any change to anything the description is built from — a field, its source
crossing the stated/left-out line, or the prompt — marks it stale.

    v2:<16 hex>    written ONLY by generate_descriptions, alongside the text it describes
    anything else  a version-1 (or missing) signature: db_import leaves that text alone

Change SYSTEM_PROMPT and you must bump PROMPT_VERSION; pipeline/tests/test_descriptions.py pins both.
"""
from __future__ import annotations

import hashlib
import json

PROMPT_VERSION = "2"
SIGNATURE_PREFIX = "v2:"
STATED_SOURCES = ("reviewed", "researched", "scraped")

SYSTEM_PROMPT = (
    "Write a 2-3 sentence factual description of this surf spot using ONLY the provided metadata. "
    "Mention only the fields that are given: the type of break (break_type), what the waves break "
    "over (bottom), the direction it faces, the swell window and optimal swell direction, the "
    "preferred tide, and the offshore wind direction. Say nothing at all about a field that is not "
    "given: do not mention that it is missing, unknown or unspecified, and do not guess it. If "
    "break_type is not given, call it a surf spot. A bottom of 'rock' means a rock reef; 'mixed' "
    "means more than one kind of bottom, so say 'a mixed bottom' without naming them. Do not make "
    "claims about wave quality, consistency, barrel potential, crowd levels, skill level, or how "
    "the wave breaks beyond what the data directly supports. Do not say 'one of the best' or 'most "
    "popular' or 'iconic'. Keep it factual and concise. No more than 75 words."
)

_CARDINALS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def deg_to_cardinal(deg):
    if deg is None:
        return None
    return _CARDINALS_8[round((float(deg) % 360) / 45) % 8]


def arcs_to_readable(arcs):
    """[{min: 168, max: 328}] -> ['S..NW']. 8-point cardinals so the range reads as a directional
    sweep rather than a math problem."""
    out = []
    for arc in arcs or []:
        a = deg_to_cardinal(arc.get("min"))
        b = deg_to_cardinal(arc.get("max"))
        if a and b:
            out.append(f"{a}..{b}")
    return out


def _whole(deg):
    return None if deg is None else int(round(float(deg)))


def stated(value, source):
    """The value when its source may be stated plainly, else None."""
    if value in (None, "", "unknown"):
        return None
    return value if source in STATED_SOURCES else None


def description_payload(rec):
    """What the model is told about one spot. `rec` uses the spots-table names (state, not
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
        "tide_preference": rec.get("tide_preference"),
        "swell_window": arcs_to_readable(rec.get("swell_window_arcs")),
        "optimal_swell_from": deg_to_cardinal(rec.get("optimal_swell_dir")),
    }
    return {k: v for k, v in payload.items() if v not in (None, "", [])}


PAYLOAD_INPUTS = ("name", "break_type", "break_type_source", "bottom", "bottom_source",
                  "orientation_deg", "offshore_wind_deg", "tide_preference", "swell_window_arcs",
                  "optimal_swell_dir")


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
    """'v2:' + 16 hex of sha1(PROMPT_VERSION, the payload). Stable across key order."""
    digest = hashlib.sha1(f"{PROMPT_VERSION}\n{canonical(payload)}".encode("utf-8")).hexdigest()
    return SIGNATURE_PREFIX + digest[:16]


def is_v2(signature):
    return isinstance(signature, str) and signature.startswith(SIGNATURE_PREFIX)
