"""The swell train the site SHOWS for an hour: its period and direction. DISPLAY ONLY.

THE DEFECT (live, Jacksonville Beach Pier, 2026-10-27). combine_ww3_partitions picks the rated
train from WW3's SWELL partitions only (swell_1..3); the wind sea is a separate channel. In a
nor'easter the swell partitions can hold almost nothing while the wind sea carries the energy:
a 0.8 ft, 3 s "swell" was rated, so the period tile read 3 s and #283's surf height shoaled at
3 s, beside a 7.8 ft, 9 s wind sea and buoy 41117 at 8.9 ft.

THE RULE. On an hour whose rated period came from a WW3 swell partition (swell_source ww3 or
nwps_height_ww3_dir), the shown train is the WIND SEA when it is both
    BIGGER than the rated partition   wind_wave_hs > the rated partition's hs, and
    LONGER-PERIOD than it             wind_wave_tp > swell_tp;
otherwise it is the rated train (swell_tp / swell_dp, else the row's tp / dp, as before).
"Longer" as well as "bigger" because the selection score exists to stop a fat short-period
chop beating a lean groundswell (Lower Trestles' 5.2 s windsea over a 15.1 s groundswell; see
interpret.combine_ww3_partitions): with both conditions the shown period can only go UP.

The rated partition is the swell_N whose tp and dp equal swell_tp and swell_dp (to 0.001; the
row stores swell_tp / swell_dp rounded to 3 decimals). No match, no switch.

STARS ARE NOT TOUCHED. The rating keeps the rated partition's period and direction (period
quality, the face's period factor, the alignment term); this only changes what the period and
direction tiles, the lists, the map and the daily report show, and the period #283's surf height
is shoaled at (forecast/surf_height.py). frontend/lib/shownPeriod.ts is the same rule, held to
the same cases (frontend/lib/shownPeriod.cases.json).
"""
from __future__ import annotations

import math

WW3_IDENTITY_SOURCES = frozenset({"ww3", "nwps_height_ww3_dir"})
SWELL_PREFIXES = ("swell_1", "swell_2", "swell_3")
MATCH_TOL = 0.001


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


def wind_sea_dominates(row) -> bool:
    """Is the wind sea bigger AND longer-period than the rated swell partition?"""
    if row.get("swell_source") not in WW3_IDENTITY_SOURCES:
        return False
    rated = rated_partition(row)
    wh, wt, wd = (_num(row.get(f"wind_wave_{k}")) for k in ("hs", "tp", "dp"))
    if rated is None or wh is None or wt is None or wd is None:
        return False
    return wh > rated[0] and wt > rated[1]


def shown_train(row):
    """(tp, dp, which) shown for *row*: 'wind_sea' when wind_sea_dominates, else 'swell' with
    swell_tp / swell_dp, each falling back to the row's tp / dp when absent (as pickSwell did)."""
    if wind_sea_dominates(row):
        return _num(row["wind_wave_tp"]), _num(row["wind_wave_dp"]), "wind_sea"
    tp = _num(row.get("swell_tp"))
    dp = _num(row.get("swell_dp"))
    return (tp if tp is not None else _num(row.get("tp")),
            dp if dp is not None else _num(row.get("dp")), "swell")
