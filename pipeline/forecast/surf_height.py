"""The surf height shown at uncalibrated spots: the model's nearshore height carried to the break,
scaled to stay conservative against the NWS Surf Zone Forecasts. DISPLAY ONLY.

WHAT IT WRITES. On every NWPS-override hour (swell_source nwps / nwps_height_ww3_dir) at a spot
with no MOP calibration factor (face_correction.spot_factor is None), face_ft becomes

    SURF_K x typical,   typical = the breaking height of the hour's Hs, in feet

and the row's face_correction_version gains SURF_MARK, so a reader can tell this height from a
calibrated one (frontend/lib/heightBasis.ts and daily_report.height_basis read it). Nothing else
on the row moves: face_ft_raw, effective_size_ft and the stars were all set before this runs,
and the rating never reads face_ft. Calibrated spots keep their MOP-calibrated face; WW3-path
and fallback hours keep the producer's face.

typical. Hs carried from D0_M = 10 m to its breaking point by linear shoaling — Green's energy flux, H = Hs x sqrt(Cg(D0)
/ Cg(h)) — breaking where H reaches GAMMA = 0.78 x the depth h, at the hour's rated period
(swell_tp), or at the wind sea's where the wind sea is bigger and longer-period than the rated
partition (shown_period.py; since 2026-10-28, marked WIND_SEA_MARK). Solved once per (Hs, period) pair rounded to 0.01 m and 0.1 s. Hs is the NWPS
node's height the override sized the hour's face from, recovered from that face (face_ft_raw)
by inverting the override's formula, face = Hs x period_factor(swell_tp, 'ww3') x 3.281: the row's
own hs is rate_spot's and is not always the height the override used. It is how the validation
read the archive (face_calibration.sized_from), so the scale fitted there is the scale applied here.

SURF_K = 0.70. From the face-height validation on claude/practical-dirac-bshfbr-face
(docs/face_validation): sw1-raycast run 52 found typical's breaking height 1.42 x the FRF Duck
lidar's on small days (one spell, 6.5-8 s), and run 53 fitted the scale on typical to the NWS
Surf Zone Forecast ranges by period band, one scale for every region: 0.75 to 0.80 with at most
10% of hours above the NWS range, 0.64 to 0.71 with at most 10% above in every region, with no
trend in period. 0.70 is the FRF figure, and keeps every region near or under 10% of hours above.
"""
from __future__ import annotations

import logging
import math

log = logging.getLogger(__name__)

M_TO_FT = 3.281
G = 9.81
D0_M = 10.0
GAMMA = 0.78
SURF_K = 0.70
NWPS_OVERRIDE_SOURCES = frozenset({"nwps", "nwps_height_ww3_dir"})
# Appended to face_correction_version on the rows this step rewrites. The part before the first
# ':' is what readers test for; the rest names the constants, so a row says how it was made.
SURF_MARK_TAG = "+surf"
SURF_MARK = f"{SURF_MARK_TAG}:k{SURF_K:.2f}:d{D0_M:g}:g{GAMMA:g}"
# Added after SURF_MARK on a row shoaled at the WIND SEA's period, not the rated swell's
# (forecast/shown_period.py): the hours where the wind sea is bigger and longer-period than
# the rated partition. Every other row's mark is SURF_MARK alone, as #283 wrote it.
WIND_SEA_MARK = ":windsea"


def is_surf_row(version) -> bool:
    """Did apply_surf_height write this row's face_ft? Read off face_correction_version."""
    return isinstance(version, str) and SURF_MARK_TAG + ":" in version


# --------------------------------------------------------------------------- #
# Linear shoaling to breaking (pure, numpy)                                    #
# --------------------------------------------------------------------------- #
def wavenumber(period, depth):
    """k (rad/m) from the linear dispersion relation w^2 = g k tanh(k h), by Newton's method
    from the Fenton-McKee approximation; elementwise over arrays."""
    import numpy as np
    t = np.asarray(period, dtype=float)
    h = np.asarray(depth, dtype=float)
    kh0 = (2 * math.pi / t) ** 2 / G * h
    kh = kh0 / np.tanh(kh0 ** 0.75) ** (2.0 / 3.0)
    for _ in range(6):
        th = np.tanh(kh)
        kh = kh - (kh * th - kh0) / (th + kh * (1 - th * th))
    return kh / h


def group_velocity(period, depth):
    """Cg (m/s) = n c, n = (1 + 2kh / sinh 2kh) / 2; elementwise."""
    import numpy as np
    t = np.asarray(period, dtype=float)
    h = np.asarray(depth, dtype=float)
    k = wavenumber(t, h)
    kh = k * h
    n = 0.5 * (1 + np.where(kh < 20, 2 * kh / np.sinh(np.minimum(2 * kh, 40)), 0.0))
    return n * (2 * math.pi / t) / k


def breaking_height(hs, period, d0=D0_M, gamma=GAMMA, levels=240, iterations=40):
    """The height (m) at breaking of a wave of height *hs* at depth *d0*, carried shoreward by
    linear shoaling until it reaches *gamma* x the depth: the deepest such depth on a
    geometric scan from d0 to 1 mm, refined by bisection. hs at or above gamma x d0 is already
    breaking (returned as is); hs or period not above 0 gives 0. Elementwise."""
    import numpy as np
    H = np.atleast_1d(np.asarray(hs, dtype=float))
    T = np.atleast_1d(np.asarray(period, dtype=float))
    out = np.zeros_like(H)
    live = (H > 0) & (T > 0) & np.isfinite(H) & np.isfinite(T)
    already = live & (H >= gamma * d0)
    out[already] = H[already]
    todo = np.where(live & ~already)[0]
    if todo.size:
        h_scan = d0 * (0.001 / d0) ** (np.arange(levels + 1) / levels)     # d0 .. 1 mm
        Hc, Tc = H[todo][:, None], T[todo][:, None]
        cg0 = group_velocity(Tc, d0)

        def excess(h):
            return Hc * np.sqrt(cg0 / group_velocity(Tc, h)) - gamma * h

        f = excess(np.broadcast_to(h_scan, (todo.size, levels + 1)))
        first = np.argmax(f >= 0, axis=1)                  # first depth where it breaks
        hi = h_scan[np.maximum(first - 1, 0)]              # deeper: not yet breaking
        lo = h_scan[first]
        hi, lo = hi[:, None], lo[:, None]
        for _ in range(iterations):
            mid = 0.5 * (hi + lo)
            brk = excess(mid) >= 0
            lo = np.where(brk, mid, lo)
            hi = np.where(brk, hi, mid)
        out[todo] = (gamma * lo)[:, 0]
    return out


def typical_face_ft(hs, period, d0=D0_M):
    """typical (ft) for heights *hs* (m) at periods *period* (s): breaking_height in feet, solved
    once per pair rounded to 0.01 m and 0.1 s."""
    import numpy as np
    H = np.round(np.asarray(hs, dtype=float), 2)
    T = np.round(np.asarray(period, dtype=float), 1)
    if H.size == 0:
        return np.zeros(0)
    pairs = np.stack([np.atleast_1d(H), np.atleast_1d(T)], axis=1)
    uniq, inv = np.unique(pairs, axis=0, return_inverse=True)
    hb = breaking_height(uniq[:, 0], uniq[:, 1], d0=d0)
    return hb[np.ravel(inv)] * M_TO_FT


def surf_face_ft(hs, period):
    """The surf height shown (ft): SURF_K x typical."""
    return SURF_K * typical_face_ft(hs, period)


# --------------------------------------------------------------------------- #
# The step in interpret.main                                                   #
# --------------------------------------------------------------------------- #
def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def sized_hs(entry):
    """The Hs (m) the NWPS override sized *entry*'s face from, or None: face_ft_raw (else
    face_ft) / (period_factor(swell_tp, 'ww3') x M_TO_FT)."""
    from ..interpret import period_factor
    face = _num(entry.get("face_ft_raw"))
    if face is None:
        face = _num(entry.get("face_ft"))
    tp = _num(entry.get("swell_tp"))
    if face is None or tp is None or tp <= 0 or face < 0:
        return None
    return face / (period_factor(tp, "ww3") * M_TO_FT)


def surf_period(entry):
    """(period s, wind_sea) the hour is shoaled at: the shown train's period
    (shown_period.shown_train), which is the rated swell_tp unless the wind sea is bigger and
    longer-period than the rated partition."""
    from .shown_period import shown_train
    tp, _dp, which = shown_train(entry)
    return tp, which == "wind_sea"


def apply_surf_height(ratings, spots, factors, slug_for=None):
    """Rewrite face_ft on every NWPS-override hour at an uncalibrated spot, in place, and mark the
    row (see the module docstring). Runs after apply_face_corrections; writes nothing but
    face_ft and face_correction_version. Every new value is computed before any row is
    written, so a failure leaves every row as it was.

    THE PERIOD IT SHOALS AT is surf_period's: the rated swell_tp, or the wind sea's period
    where the wind sea is bigger and longer-period than the rated partition (those rows get
    WIND_SEA_MARK after SURF_MARK). Hs is still recovered at swell_tp, the period the
    override's face was sized at.

    Returns {spots_calibrated, spots_rewritten, hours_rewritten, hours_wind_sea,
    hours_kept_by_source, hours_without_inputs}: the hours shoaled at the wind sea's period,
    hours kept because another rater fed them, and NWPS-override hours with no face or no
    rated period (they keep their face and get no mark).

    NO FACTORS, NO STEP. With no factors file every spot reads as uncalibrated, and the
    California spots the factors exist for would get this height in place of the one they are
    calibrated to; a run with no file rewrites nothing and says so (factors_missing)."""
    from .face_correction import spot_factor
    if slug_for is None:
        from ..enrich import _slug_for as slug_for      # noqa: PLC0415 - avoids a cycle
    by_name = {s.get("name"): s for s in spots if s.get("name")}
    todo, stats = [], {"spots_calibrated": 0, "spots_rewritten": 0, "hours_rewritten": 0,
                       "hours_wind_sea": 0, "hours_kept_by_source": 0, "hours_without_inputs": 0,
                       "factors_missing": not factors}
    if not factors:
        return stats
    for name, entries in ratings.items():
        spot = by_name.get(name)
        if spot is None:
            continue
        if spot_factor(spot, factors, slug_for) is not None:
            stats["spots_calibrated"] += 1
            continue
        n0 = len(todo)
        for e in entries:
            if e.get("swell_source") not in NWPS_OVERRIDE_SOURCES:
                stats["hours_kept_by_source"] += 1
                continue
            hs = sized_hs(e)
            if e.get("face_ft") is None or hs is None:
                stats["hours_without_inputs"] += 1
                continue
            tp, wind_sea = surf_period(e)
            todo.append((e, hs, tp, wind_sea))
        stats["spots_rewritten"] += len(todo) > n0
    if todo:
        faces = surf_face_ft([t[1] for t in todo], [t[2] for t in todo])
        for (e, _, _, wind_sea), f in zip(todo, faces):
            e["face_ft"] = round(float(f), 2)
            mark = SURF_MARK + (WIND_SEA_MARK if wind_sea else "")
            v = e.get("face_correction_version")
            e["face_correction_version"] = f"{v}{mark}" if v else mark
    stats["hours_rewritten"] = len(todo)
    stats["hours_wind_sea"] = sum(1 for t in todo if t[3])
    return stats
