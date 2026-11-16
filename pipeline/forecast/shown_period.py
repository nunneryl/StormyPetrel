"""The swell train the site SHOWS for an hour: its period and direction. DISPLAY ONLY.

THE DEFECT (live, Jacksonville Beach Pier, 2026-10-26/27). combine_ww3_partitions picks the rated
train from WW3's SWELL partitions only (swell_1..3); the wind sea is a separate channel. In a
nor'easter the swell partitions can hold almost nothing while the wind sea carries the energy:
a 0.8 ft, 3 s "swell" was rated, so the period tile read 3 s and #283's surf height shoaled at
3 s, beside a 7.8 ft, 9 s wind sea and buoy 41117 at 8.9 ft. sw1-raycast run 55 found the
reverse far more often: a small LONG-period scrap rated over a much bigger, shorter wind sea
(204,341 archive hours; 53,278 with the wind sea 4x the scrap or more), where shoaling at the
scrap's period overstated the surf height by a median 13% (p90 39%).

THE RULE. On an hour whose rated period came from a WW3 swell partition (swell_source ww3 or
nwps_height_ww3_dir), the shown train is the WIND SEA when either
    LONGER    its Hs is above the rated partition's AND its period is at least
              MIN_LONGER_S (0.5 s) longer than swell_tp: a train both bigger and
              longer-period; the margin keeps a wind sea a few hundredths of a second
              longer from flipping the shown direction (run 55: 605 such hours); or
    DOMINANT  its Hs is at least DOMINANCE_K (4) x the rated partition's, whatever its
              period: the rated "swell" is under a quarter of the wind sea's height, a
              sixteenth of its energy, so the sea is the wind sea. Here the shown period
              CAN GO DOWN. A lean groundswell under a moderate chop (Lower Trestles: a
              1.0 m, 5.2 s wind sea over a 0.5 m, 15.1 s groundswell, 2x) stays the swell;
              see interpret.combine_ww3_partitions.
otherwise it is the rated train (swell_tp / swell_dp, else the row's tp / dp, as before).

NOT FROM BEHIND THE COAST. Neither case switches when the wind sea comes from BLOCKED_OUTSIDE_DEG
(90) or more outside the spot's swell window: the rating's blocked rung (interpret.directional_gain
gives a swell there 0, measured the same way, from the window's padded edge). Sw1-raycast run 56:
on those 2,422 hours at K 4 the shown period's agreement with the buoy did not improve (46.8% of
hours within 2 s before, 45.3% after; New Jersey 71% to 23%), while wind seas under 45° outside
the window agreed well (26.3% to 80.3%), so only the blocked rung is excluded. A spot with no
window arcs has no rung to read, and switches as before.

NOT TRAVELLING AWAY FROM THE BEACH (sw1-raycast runs 76 and 77). Neither case switches either when
the wind sea comes from OUTSIDE the spot's window AND from FACING_OFF_DEG (90) or more off its
facing (orientation_deg, the bearing the beach faces): it is then running along the shore or out
to sea, not onto the beach. St. Pete Beach, 2026-10-28: a 4.7 s wind sea from 16 deg at a beach
facing 240 deg is 136 deg off its facing and 59 deg outside its window (sector 171 to 317 deg):
blocked. Inside the window the switch is decided as before, however far off the facing: where
swell wraps in from beside the facing (The Hook, facing 149, window 187 to 253) a wind sea from
245 deg reaches the break. "Outside the window" is the window rung's test, interpret.in_any_arc:
each arc's stored bounds widened by its half-step pad, both padded edges inclusive, so a wind sea
exactly on a padded edge is inside. Exactly 90 deg off the facing counts as away, as the #279
surface label puts exactly 120 deg off with onshore. A spot with no window arcs, or no facing,
switches as the window alone decides (run 76 measured the facing alone: 18,405 of 75,547 archive
switches removed, 987 of them from inside the window).

The rated partition is the swell_N whose tp and dp equal swell_tp and swell_dp (to 0.001; the
row stores swell_tp / swell_dp rounded to 3 decimals). No match, no switch.

STARS ARE NOT TOUCHED. The rating keeps the rated partition's period and direction (period
quality, the face's period factor, the alignment term); this only changes what the period and
direction tiles, the lists, the map and the daily report show, and the period #283's surf height
is shoaled at (forecast/surf_height.py). frontend/lib/shownPeriod.ts is the same rule with the
same constants, held to the same cases (frontend/lib/shownPeriod.cases.json).
"""
from __future__ import annotations

import math

WW3_IDENTITY_SOURCES = frozenset({"ww3", "nwps_height_ww3_dir"})
SWELL_PREFIXES = ("swell_1", "swell_2", "swell_3")
MATCH_TOL = 0.001
# The rule's constants. frontend/lib/shownPeriod.ts declares the same values, and its test reads
# them from this file, so the site and the pipeline cannot drift apart.
MIN_LONGER_S = 0.5
DOMINANCE_K = 4.0
BLOCKED_OUTSIDE_DEG = 90.0
FACING_OFF_DEG = 90.0


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def rated_partition(row):
    """(hs, tp, dp) of the swell partition the hour was rated on, or None."""
    tp, dp = _num(row.get("swell_tp")), _num(row.get("swell_dp"))
    if tp is None or dp is None:
        return None
    for p in SWELL_PREFIXES:
        h, t, d = (_num(row.get(f"{p}_{k}")) for k in ("hs", "tp", "dp"))
        if h is None or t is None or d is None:
            continue
        if abs(t - tp) <= MATCH_TOL and abs(d - dp) <= MATCH_TOL:
            return h, t, d
    return None


def wind_sea_blocked(dp, arcs) -> bool:
    """Does bearing *dp* come from BLOCKED_OUTSIDE_DEG or more outside the window *arcs* (a spot's
    swell_window_arcs)? The rating's blocked rung: interpret.directional_gain's offset from the
    padded edge. False when there are no arcs."""
    from ..interpret import _min_offset_from_arcs, in_any_arc
    if not arcs or in_any_arc(dp, arcs):
        return False
    return _min_offset_from_arcs(dp, arcs) >= BLOCKED_OUTSIDE_DEG


def off_facing_deg(dp, facing):
    """Degrees between bearing *dp* and the spot's facing, 0 to 180; None with no facing."""
    f = _num(facing)
    if f is None:
        return None
    d = abs(float(dp) - f) % 360.0
    return min(d, 360.0 - d)


def wind_sea_away(dp, facing, arcs) -> bool:
    """Does a wind sea from *dp* run along the shore or out to sea at a spot facing *facing* whose
    window is *arcs*: from outside the window (interpret.in_any_arc, padded edges inclusive) and
    FACING_OFF_DEG or more off the facing? False with no facing or no arcs."""
    from ..interpret import in_any_arc
    if not arcs or in_any_arc(dp, arcs):
        return False
    off = off_facing_deg(dp, facing)
    return off is not None and off >= FACING_OFF_DEG


def switch_case(row, arcs, facing, k=DOMINANCE_K):
    """'longer' or 'dominant' when the wind sea is shown for *row* (see the module docstring;
    'longer' first when both hold), else None. *arcs* are the spot's swell_window_arcs (None or
    empty: no window to read); *facing* its orientation_deg (None: no facing to read). *k* is
    the dominance ratio; the preview varies it, the site uses DOMINANCE_K."""
    if row.get("swell_source") not in WW3_IDENTITY_SOURCES:
        return None
    rated = rated_partition(row)
    wh, wt, wd = (_num(row.get(f"wind_wave_{x}")) for x in ("hs", "tp", "dp"))
    if rated is None or wh is None or wt is None or wd is None:
        return None
    if wind_sea_blocked(wd, arcs) or wind_sea_away(wd, facing, arcs):
        return None
    if wh > rated[0] and wt - rated[1] >= MIN_LONGER_S:
        return "longer"
    if wh >= k * rated[0]:
        return "dominant"
    return None


def wind_sea_dominates(row, arcs, facing, k=DOMINANCE_K) -> bool:
    """Is the wind sea shown for *row*? (switch_case is not None)"""
    return switch_case(row, arcs, facing, k) is not None


def shown_train(row, arcs, facing, k=DOMINANCE_K):
    """(tp, dp, which) shown for *row* at a spot whose window is *arcs* and facing *facing*:
    'wind_sea' when switch_case holds, else 'swell' with swell_tp / swell_dp, each falling back to
    the row's tp / dp when absent (as pickSwell did)."""
    if wind_sea_dominates(row, arcs, facing, k):
        return _num(row["wind_wave_tp"]), _num(row["wind_wave_dp"]), "wind_sea"
    tp = _num(row.get("swell_tp"))
    dp = _num(row.get("swell_dp"))
    return (tp if tp is not None else _num(row.get("tp")),
            dp if dp is not None else _num(row.get("dp")), "swell")
