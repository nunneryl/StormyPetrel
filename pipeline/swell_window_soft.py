"""A candidate soft, period-dependent swell window. READ-ONLY: nothing in the rater imports it.

It exists so the answer key (scripts/build_swell_relook.py) and later scoring read one
implementation. interpret.directional_gain is today's function and is not changed here.

TODAY. Inside the stored arcs (widened by interpret.arc_pad_deg) the gain is
max(0.25, cos²(Δ/2)) about optimal_swell_dir; outside, a step ladder by angle from the padded
edge: 0.40 within 45°, 0.15 within 90°, 0 beyond. On the NWPS path it multiplies the face, so
it is a height factor; on the WW3 path it weights hs², so the same gain is √g in height.

THE CANDIDATE, as a HEIGHT factor:

    H(dp, T) = A(Δ) · E(δ, T)

    A(Δ)  = max(0.25, cos²(Δ/2)), Δ from optimal_swell_dir (else orientation_deg) — today's
            in-window term, unchanged (calibrated against CDIP MOP; see directional_gain).
    δ     = signed angle from dp to the nearest GEOMETRIC window edge, negative inside. The
            geometric window is each arc's padded sector widened again by
            config.SWELL_ARC_SHRINK_DEG on both ends for ray-cast arcs (pad >= 1°), which the
            ray-cast trimmed off "for diffraction" — the soft edge does that job instead.
            Synthesised arcs (pad 0 or 0.5) were never trimmed and are not widened.
    E(δ,T)= knife-edge (Sommerfeld half-plane) diffraction in the Fresnel approximation:
              L = g·T²/2π (deep-water wavelength), v = δ_rad · √(2r/L)
              shadow (v >= 0): K(v) = √((f² + g'²)/2), K(0) = 0.5
                f  = (1 + 0.926v) / (2 + 1.792v + 3.104v²)
                g' = 1 / (2 + 4.142v + 3.492v² + 6.670v³)     (Abramowitz & Stegun 7.3.32-33)
              lit side (v < 0): 1 - K(|v|)
            plus, outside only, a refraction floor in ENERGY (Pawka, Inman & Guza 1984: up to
            ~10% of blocked energy, only below 0.11 Hz):
              F = 0.10 · clamp((T - 9) / 5, 0, 1) · (1 - δ/90),  E = max(K, √F)
            and 0 beyond 90° outside.

    r is the distance from the spot to the obstacle that makes the edge. The arcs do not record
    it yet, so EDGE_DISTANCE_M (5 km) stands in for every edge.

JUDGEMENT CALLS, to be settled by the answer key: r's fallback, the floor's ramp (9 -> 14 s) and
its linear fade to 90°, the 90° cutoff, and the lit side as 1 - K (it ignores the ripple, so a
bearing deep inside reads about 0.99, not 1).
"""
from __future__ import annotations

import math

from .config import SWELL_ARC_SHRINK_DEG
from .interpret import arc_pad_deg, directional_gain

G = 9.81
EDGE_DISTANCE_M = 5000.0
REFRACTION_FLOOR = 0.10           # share of blocked ENERGY refraction can supply
REFRACTION_FROM_S = 9.0           # 0.11 Hz: below this period refraction adds nothing
REFRACTION_FULL_S = 14.0
CUTOFF_DEG = 90.0
RAY_ARC_MIN_PAD = 1.0             # pad 2 (4° cast) and 1 (2° cast) are ray samples
PERIODS_S = (8.0, 12.0, 18.0)


def wavelength_m(period_s: float) -> float:
    """Deep-water wavelength g·T²/2π."""
    return G * period_s * period_s / (2.0 * math.pi)


def fresnel_k(v: float) -> float:
    """Wave height behind a semi-infinite barrier, relative to the open sea: 0.5 on the shadow
    line, falling as about 0.225/v into the shadow (v > 0), rising toward 1 on the lit side."""
    if v < 0:
        return 1.0 - fresnel_k(-v)
    f = (1.0 + 0.926 * v) / (2.0 + 1.792 * v + 3.104 * v * v)
    g = 1.0 / (2.0 + 4.142 * v + 3.492 * v * v + 6.670 * v ** 3)
    return math.sqrt((f * f + g * g) / 2.0)


def geometric_sectors(arcs: list[dict] | None) -> list[tuple[float, float]]:
    """(start, length) per arc: the padded sector, widened by the ray-cast's shrink on both ends
    when the arc is a ray sample. A length of 360 or more is the whole compass."""
    out = []
    for arc in arcs or []:
        lo, hi = float(arc["min"]), float(arc["max"])
        pad = arc_pad_deg(arc)
        grow = SWELL_ARC_SHRINK_DEG if pad >= RAY_ARC_MIN_PAD else 0.0
        length = ((hi - lo) % 360.0) + 2.0 * pad + 2.0 * grow
        out.append(((lo - pad - grow) % 360.0, length))
    return out


def padded_sectors(arcs: list[dict] | None) -> list[tuple[float, float]]:
    """(start, length) per arc as interpret.in_any_arc reads it: today's window."""
    out = []
    for arc in arcs or []:
        lo, hi = float(arc["min"]), float(arc["max"])
        pad = arc_pad_deg(arc)
        out.append(((lo - pad) % 360.0, ((hi - lo) % 360.0) + 2.0 * pad))
    return out


_EPS_DEG = 1e-6


def _within(x: float, start: float, length: float) -> bool:
    return ((x - start) % 360.0) <= length


def _covered(x: float, sectors: list[tuple[float, float]]) -> bool:
    return any(_within(x, s, length) for s, length in sectors)


def union_edges(sectors: list[tuple[float, float]]) -> list[float]:
    """The bearings where the union of *sectors* begins or ends. A sector's start is an edge of
    the union when the bearing just before it is open sea no sector covers, and its end when the
    bearing just after it is; so sectors that touch or nest share no false edge. [] when the
    union is the whole compass, since a sector of 360° or more covers both sides of its own ends
    (or when there are no sectors)."""
    edges = set()
    for start, length in sectors:
        end = (start + length) % 360.0
        if not _covered((start - _EPS_DEG) % 360.0, sectors):
            edges.add(round(start, 9) % 360.0)
        if not _covered((end + _EPS_DEG) % 360.0, sectors):
            edges.add(round(end, 9) % 360.0)
    return sorted(edges)


def _angle_off(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def edge_offset(dp: float, arcs: list[dict] | None) -> float | None:
    """δ: degrees from *dp* to the nearest geometric edge, negative inside the window. None when
    there are no arcs; -180 when the window is the whole compass."""
    sectors = geometric_sectors(arcs)
    if not sectors:
        return None
    edges = union_edges(sectors)
    if not edges:
        return -180.0
    dist = min(_angle_off(dp, e) for e in edges)
    inside = _covered(dp, sectors)
    return -dist if inside else dist


def edge_factor(delta: float, period_s: float, r_m: float = EDGE_DISTANCE_M) -> float:
    """E(δ, T): the height the window's edge lets through."""
    if delta > CUTOFF_DEG:
        return 0.0
    v = math.radians(delta) * math.sqrt(2.0 * r_m / wavelength_m(period_s))
    k = fresnel_k(v)
    if delta <= 0:
        return k
    ramp = min(1.0, max(0.0, (period_s - REFRACTION_FROM_S) / (REFRACTION_FULL_S - REFRACTION_FROM_S)))
    floor_energy = REFRACTION_FLOOR * ramp * (1.0 - delta / CUTOFF_DEG)
    return max(k, math.sqrt(floor_energy))


def alignment(dp: float, optimal: float | None, orientation: float | None) -> float | None:
    """A(Δ) = max(0.25, cos²(Δ/2)) about optimal (else orientation); None with no target."""
    target = optimal if optimal is not None else orientation
    if target is None:
        return None
    diff = ((dp - target + 540.0) % 360.0) - 180.0
    return max(0.25, math.cos(math.radians(diff / 2.0)) ** 2)


def candidate_height_factor(dp: float, period_s: float, arcs: list[dict] | None,
                            optimal: float | None, orientation: float | None,
                            r_m: float = EDGE_DISTANCE_M) -> float:
    """H(dp, T) = A(Δ) · E(δ, T). With no arcs there is no edge: A alone, or 0 with no target —
    today's no-arcs branch. Inside a window with no target, A is today's neutral 0.5."""
    a = alignment(dp, optimal, orientation)
    delta = edge_offset(dp, arcs)
    if delta is None:
        return 0.0 if a is None else a
    return (0.5 if a is None else a) * edge_factor(delta, period_s, r_m)


def today_height_factor(dp: float, arcs: list[dict] | None, optimal: float | None,
                        orientation: float | None) -> float:
    """Today's gain as the NWPS path uses it (face x gain): a height factor."""
    return directional_gain(dp, arcs, optimal, orientation)
