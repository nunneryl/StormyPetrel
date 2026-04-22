"""Orientation-derived swell-window fallback (SW-2).

Ray-casting (Algorithm 2) produces empty `swell_window_arcs` for many spots:
Great-Lakes shorelines on shallow coasts, partially-enclosed waters where
every bearing hits land within SWELL_MIN_FETCH_KM, and spots whose
coordinates sit on ambiguous coastline tiles.

For those spots we fall back to a single arc centered on the seaward
bearing (orientation_deg), with a width chosen by context:

- protected water (bay/inlet/harbor/sound/cove/creek in the name):  90°
- Great Lakes spots (MI/WI/MN/OH/IN/PA):                           120°
- open coast (default):                                            160°

The fallback sets `swell_window_source: "orientation_derived"` so
downstream consumers can tell it apart from the ray-cast result. When the
spot has no orientation either, we leave the arcs empty.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

PROTECTED_WATER_KEYWORDS = ("bay", "inlet", "harbor", "sound", "cove", "creek")
GREAT_LAKES_STATES = frozenset({
    "Michigan", "Wisconsin", "Minnesota", "Ohio", "Indiana", "Pennsylvania",
})


def _centered_arc(orientation_deg: float, span_deg: float) -> list[dict]:
    """Arc centered on *orientation_deg* with total width *span_deg*.

    Returns one non-wrapping arc when the arc fits in [0, 359], otherwise
    two arcs split across 0°, so downstream `min <= dp <= max` checks work
    without special-casing wraparound.
    """
    half = span_deg / 2.0
    lo = (orientation_deg - half) % 360.0
    hi = (orientation_deg + half) % 360.0
    lo_r = int(round(lo)) % 360
    hi_r = int(round(hi)) % 360
    if lo_r <= hi_r:
        return [{"min": lo_r, "max": hi_r, "span": int(round(span_deg))}]
    # Wraps through 0°: split into [lo, 359] + [0, hi].
    return [
        {"min": lo_r, "max": 359, "span": 360 - lo_r},
        {"min": 0,    "max": hi_r, "span": hi_r + 1},
    ]


def _fallback_span(name: str | None, region_hint: str | None) -> int:
    """Pick the arc width for this spot's context."""
    nm = (name or "").lower()
    if any(k in nm for k in PROTECTED_WATER_KEYWORDS):
        return 90
    if (region_hint or "") in GREAT_LAKES_STATES:
        return 120
    return 160


def compute_swell_window_fallback(spot: dict) -> dict:
    """Return fields to patch onto *spot* when its arcs are empty.

    Expects `orientation_deg`, `name`, and `region_hint` to already be set.
    If orientation is missing we can't derive a window — return a no-op.
    """
    arcs = spot.get("swell_window_arcs") or []
    if arcs:
        return {}

    orientation = spot.get("orientation_deg")
    if orientation is None:
        return {}

    span = _fallback_span(spot.get("name"), spot.get("region_hint"))
    fallback_arcs = _centered_arc(float(orientation), span)
    return {
        "swell_window_arcs": fallback_arcs,
        "optimal_swell_dir": int(round(float(orientation))) % 360,
        "swell_window_source": "orientation_derived",
    }
