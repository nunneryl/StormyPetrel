"""pipeline/face_compare.py: the two face-height candidates against FRF, the NWS Surf Zone
Forecast and visitor reports. Every expected value is worked by hand in the test, never read
back from the module.

Shoaling hand values:
  dispersion  T 10 s, h 10 m: k0 h = (2 pi / 156.13) x 10 = 0.40243 (L0 = g T^2 / 2 pi =
              156.13 m); kh tanh kh = 0.40243 at kh = 0.6802 (tanh 0.6802 = 0.59164,
              0.6802 x 0.59164 = 0.40243), so L = 2 pi x 10 / 0.6802 = 92.37 m.
  deep Cg     T 4 s, h 100 m (kh = 25): Cg = g T / 4 pi = 9.81 x 4 / 12.5664 = 3.1227 m/s.
  Green's law at a 500 s period the water is shallow everywhere (kh at 10 m = 0.0127, n =
              0.99995), so H = H0 (d0 / h)^(1/4) and breaking at H = 0.78 h gives
              h = (H0 / 0.78)^(4/5) d0^(1/5):
                H0 1, d0 10: (1.282051)^0.8 = 1.219900, 10^0.2 = 1.584893, h = 1.933409,
                Hb = 0.78 h = 1.508059 m = 4.947942 ft; sets x 1.27 = 6.283886 ft.
                H0 1, d0 8:  8^0.2 = 1.515717, h = 1.849026, Hb = 1.442240 m = 4.731990 ft.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path

from pipeline import face_compare as F

REPO = Path(__file__).resolve().parents[2]


def _close(a, b, tol):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# Shoaling                                                                     #
# --------------------------------------------------------------------------- #
def test_the_wavelength_at_10_s_in_10_m():
    k = float(F.wavenumber(10.0, 10.0))
    assert _close(2 * math.pi / k, 92.37, 0.01)


def test_deep_water_group_velocity():
    assert _close(float(F.group_velocity(4.0, 100.0)), 3.1227, 0.0005)


def test_intermediate_depth_group_velocity():
    # T 10 s, h 10 m: kh 0.6802, c = L / T = 9.2373 m/s, sinh(1.3604) = 1.82060,
    # n = 0.5 x (1 + 1.3604 / 1.82060) = 0.87361, Cg = 8.0698 m/s
    assert _close(float(F.group_velocity(10.0, 10.0)), 8.0698, 0.001)


def test_the_dispersion_relation_is_solved_in_deep_and_shallow_water():
    import numpy as np
    T = np.array([4.0, 6.0, 10.0, 16.0, 30.0])
    h = np.array([100.0, 30.0, 10.0, 2.0, 0.5])
    k = F.wavenumber(T, h)
    resid = (2 * np.pi / T) ** 2 - 9.81 * k * np.tanh(k * h)
    assert float(np.max(np.abs(resid / (2 * np.pi / T) ** 2))) < 1e-10


def test_greens_law_breaking_height():
    assert _close(float(F.breaking_height([1.0], [500.0])[0]), 1.508059, 0.0005)
    assert _close(float(F.breaking_height([1.0], [500.0], d0=8.0)[0]), 1.442240, 0.0005)


def test_a_wave_already_breaking_at_d0_is_returned_as_is():
    # 0.78 x 10 = 7.8 m (7.800000000000001 in floating point, so 7.8 itself is solved, and
    # breaks within a millimetre of where it stands)
    got = F.breaking_height([7.9, 8.0, 7.8, 0.78 * 10.0], [10.0, 10.0, 10.0, 10.0])
    assert got.tolist()[:2] == [7.9, 8.0] and _close(float(got[2]), 7.8, 0.001)
    assert float(got[3]) == 0.78 * 10.0                     # exactly gamma x d0: as is


def test_no_height_or_period_gives_no_breaker():
    got = F.breaking_height([0.0, 1.0, -1.0, float("nan"), 1.0, float("inf")],
                            [10.0, 0.0, 10.0, 10.0, float("nan"), 10.0])
    assert got.tolist() == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_a_bigger_wave_shoals_less_at_the_same_period():
    hb = F.breaking_height([0.5, 2.5], [10.0, 10.0])
    assert hb[0] / 0.5 > hb[1] / 2.5


def test_candidate_faces_in_feet_and_the_set_factor():
    typical, sets = F.candidate_faces([1.0, 1.0], [500.0, 500.0])
    assert _close(float(typical[0]), 4.947942, 0.002) and _close(float(sets[0]), 6.283886, 0.003)
    typical8, _ = F.candidate_faces([1.0], [500.0], d0=8.0)
    assert _close(float(typical8[0]), 4.731990, 0.002)


def test_candidate_faces_round_inputs_to_a_centimetre_and_a_tenth_of_a_second():
    a, _ = F.candidate_faces([1.004, 1.0], [500.04, 500.0])
    assert float(a[0]) == float(a[1])


# --------------------------------------------------------------------------- #
# Spots and groups                                                             #
# --------------------------------------------------------------------------- #
def test_slug_region_type_and_facing():
    assert F.slug("St. Pete Beach") == "st-pete-beach"
    assert F.region_of({"nwps_wfo": "MHX"}) == "Carolinas and Georgia"
    assert F.region_of({"nwps_wfo": "xyz"}) == "other"
    assert F.spot_type({"break_type": "reef"}) == "reef"
    assert F.spot_type({}) == "unknown"
    got = [F.facing(d) for d in (0, 44.9, 45, 134.9, 135, 224.9, 225, 314.9, 315, 359.9)]
    assert got == ["N", "N", "E", "E", "S", "S", "W", "W", "N", "N"]
    assert F.facing(None) is None


def test_group_of_each_case():
    before = datetime(2026, 10, 1, tzinfo=timezone.utc)
    after = datetime(2026, 10, 27, 14, tzinfo=timezone.utc)
    assert F.group_of("coolidge", "nwps", before) == "held"
    assert F.group_of("coolidge", "ww3", before) == "held"
    assert F.group_of("swamis", "ww3", before) == "ww3"
    assert F.group_of("duck-pier", "nwps_height_ww3_dir", before) == "moved (old point)"
    assert F.group_of("duck-pier", "nwps_height_ww3_dir", after) == "headline"
    assert F.group_of("duck-pier", "ww3", before) == "ww3"
    assert F.group_of("swamis", "cdip_mop", before) == "headline"
    assert F.group_of("x", "buoy", before) == "base"
    assert F.group_of("x", None, before) == "base"


def test_the_held_and_moved_lists_are_roster_slugs():
    import json
    slugs = {F.slug(s["name"]) for s in json.loads((REPO / "pipeline/spots_enriched.json")
                                                   .read_text())}
    assert F.HELD | F.MOVED | F.SUSPECTS_KEPT <= slugs
    assert len(F.HELD) == 7 and len(F.MOVED) == 5 and len(F.SUSPECTS_KEPT) == 5


def test_assess_groups_and_sizes():
    spot = {"name": "Swamis", "nwps_wfo": "sgx", "break_type": "reef", "orientation_deg": 260}
    rows = [
        # MOP: Hs = face_ft_raw / 3.281 = 1.0 m, period 500 s -> typical 4.95 ft
        {"valid_time": "2026-10-01T15:00:00+00:00", "swell_source": "cdip_mop",
         "face_ft_raw": 3.281, "face_ft": 3.0, "swell_tp": 500.0, "tp": 9.0, "hs": 0.7},
        {"valid_time": "2026-10-01T16:00:00+00:00", "swell_source": "ww3",
         "face_ft_raw": 4.0, "face_ft": 4.0, "swell_tp": 12.0, "tp": 9.0, "hs": 0.7},
        # no face_ft_raw: no height, so not a headline hour
        {"valid_time": "2026-10-01T17:00:00+00:00", "swell_source": "cdip_mop",
         "face_ft_raw": None, "face_ft": 3.0, "swell_tp": 12.0, "tp": 9.0, "hs": 0.7},
    ]
    got = F.assess(rows, spot, "California")
    assert [g["group"] for g in got] == ["headline", "ww3", "no face"]
    a = got[0]
    assert (a["hs"], a["period"], a["published"], a["region"], a["type"], a["facing"],
            a["office"]) == (1.0, 500.0, 3.0, "Southern California", "reef", "W", "SGX")
    assert a["typical"] == 4.95 and a["sets"] == 6.28
    assert a["hour"] == int(datetime(2026, 10, 1, 15, tzinfo=timezone.utc).timestamp())


def test_assess_falls_back_to_tp_without_a_swell_period():
    spot = {"name": "X", "nwps_wfo": "sgx"}
    rows = [{"valid_time": "2026-10-01T15:00:00+00:00", "swell_source": "cdip_mop",
             "face_ft_raw": 3.281, "face_ft": 3.0, "swell_tp": None, "tp": 500.0}]
    assert F.assess(rows, spot, None)[0]["period"] == 500.0


# --------------------------------------------------------------------------- #
# The Surf Zone Forecast                                                       #
# --------------------------------------------------------------------------- #
MHX_1 = """\x01
FZUS52 KMHX 151459
SRFMHX

Surf Zone Forecast
National Weather Service Newport/Morehead City NC
1059 AM EDT Tue Sep 15 2026

NCZ203-160000-
Northern Outer Banks-
1059 AM EDT Tue Sep 15 2026

.REST OF TODAY...
Rip Current Risk*...........High.
Surf Height.................3 to 5 feet.

.TONIGHT...
Surf Height.................4 to 6 feet.

.WEDNESDAY...
Surf Height.................2 to 4 feet.

$$

NCZ205-160000-
Hatteras Island-
1059 AM EDT Tue Sep 15 2026

.REST OF TODAY...
Surf Height...
   North of Cape Hatteras...3 to 5 feet.
   South of Cape Hatteras...Around 2 feet.

$$
\x03"""

MHX_2 = """\x01
FZUS52 KMHX 151959
SRFMHX

National Weather Service Newport/Morehead City NC
359 PM EDT Tue Sep 15 2026

NCZ203-160000-
Northern Outer Banks-
359 PM EDT Tue Sep 15 2026

.REST OF TODAY...
Surf Height.................1 foot or less.

$$
\x03"""

HFO = """\x01
FZHW52 PHFO 260124
SRFHFO

Surf Zone Forecast for Hawaii
324 PM HST Sun Oct 25 2026

HIZ003-029>031-270230-
Kauai-
324 PM HST Sun Oct 25 2026

__________________________________________________________________
                      Tonight                    Monday

Shores                  Surf                       Surf
                     PM     AM                  AM     PM
__________________________________________________________________

North Facing        7-10   8-12                9-12  10-14
West Facing          4-6    4-6                 5-7      6

$$
\x03"""


def _utc(y, mo, d, h):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp())


def test_split_products():
    assert len(F.split_products(MHX_1 + "\n" + MHX_2)) == 2
    assert F.split_products("ERROR: Could not Find: SRFHGX") == []


def test_issued_reads_the_local_time_and_zone():
    t, off = F.issued(MHX_1)
    assert (t, off) == (datetime(2026, 9, 15, 14, 59, tzinfo=timezone.utc), -4)
    t, off = F.issued(HFO)
    # 3:24 PM HST = 15:24 + 10 h = 01:24Z on the 26th (the header's PHFO 260124)
    assert (t, off) == (datetime(2026, 10, 26, 1, 24, tzinfo=timezone.utc), -10)
    assert F.issued("1200 PM EST Mon Jan 05 2026")[0] == datetime(2026, 1, 5, 17, 0,
                                                                  tzinfo=timezone.utc)
    assert F.issued("1205 AM PST Mon Jan 05 2026")[0] == datetime(2026, 1, 5, 8, 5,
                                                                  tzinfo=timezone.utc)
    assert F.issued("1059 AM XYZ Tue Sep 15 2026") is None


def test_period_date():
    tue = datetime(2026, 9, 15).date()                 # a Tuesday
    assert F.period_date("REST OF TODAY", tue) == tue
    assert F.period_date("TODAY", tue) == tue
    assert F.period_date("THIS AFTERNOON", tue) == tue
    assert F.period_date("WEDNESDAY", tue) == datetime(2026, 9, 16).date()
    assert F.period_date("MONDAY", tue) == datetime(2026, 9, 21).date()
    assert F.period_date("TUESDAY", tue) == tue
    for night in ("TONIGHT", "WEDNESDAY NIGHT", "THIS EVENING", "REMARKS FOR EXTENDED"):
        assert F.period_date(night, tue) is None, night


def test_surf_range_phrases():
    assert F.surf_range("3 to 5 feet.") == (3.0, 5.0)
    assert F.surf_range("Around 2 feet.") == (2.0, 2.0)
    assert F.surf_range("1 foot or less.") == (0.0, 1.0)
    assert F.surf_range("Less than 1 foot.") == (0.0, 1.0)
    assert F.surf_range("9 FT.") == (9.0, 9.0)
    assert F.surf_range("2 feet") == (2.0, 2.0)
    assert F.surf_range("") is None
    assert F.surf_range("Flat.") is None


def test_srf_periods_skip_a_split_segment():
    got = F.srf_periods(MHX_1)
    assert [(r["zones"], r["period"], r["lo"], r["hi"]) for r in got] == [
        (["NCZ203"], "REST OF TODAY", 3.0, 5.0), (["NCZ203"], "TONIGHT", 4.0, 6.0),
        (["NCZ203"], "WEDNESDAY", 2.0, 4.0)]


def test_srf_hours_daytime_after_issue_and_the_newest_product_wins():
    hours, cov = F.srf_hours(F.split_products(MHX_1 + MHX_2), "MHX")
    # Tue 15 Sep, product 1 at 10:59 EDT (14:59Z): REST OF TODAY from 11:00 to 17:00 EDT
    # = 15Z to 21Z; product 2 at 15:59 EDT (19:59Z) takes 16:00 and 17:00 EDT (20Z, 21Z).
    # Wednesday 16 Sep: 08:00 to 17:00 EDT = 12Z to 21Z. TONIGHT is not used.
    want = {_utc(2026, 9, 15, h): (3.0, 5.0) for h in range(15, 20)}
    want.update({_utc(2026, 9, 15, h): (0.0, 1.0) for h in (20, 21)})
    want.update({_utc(2026, 9, 16, h): (2.0, 4.0) for h in range(12, 22)})
    assert {t: v[:2] for (z, f, t), v in hours.items()} == want
    assert {(z, f) for z, f, _ in hours} == {("NCZ203", None)}
    assert all(v[2] == "MHX" for v in hours.values())
    assert cov == {"products": 2, "day periods": 3, "night or other periods": 1}


def test_hfo_table_by_facing_and_am_pm():
    rows = F.hfo_periods(HFO)
    assert rows[0]["zones"] == ["HIZ003", "HIZ029", "HIZ030", "HIZ031"]
    got = [(r["period"], r["ampm"], r["facing"], r["lo"], r["hi"]) for r in rows]
    assert got == [("TONIGHT", "PM", "N", 7.0, 10.0), ("TONIGHT", "AM", "N", 8.0, 12.0),
                   ("MONDAY", "AM", "N", 9.0, 12.0), ("MONDAY", "PM", "N", 10.0, 14.0),
                   ("TONIGHT", "PM", "W", 4.0, 6.0), ("TONIGHT", "AM", "W", 4.0, 6.0),
                   ("MONDAY", "AM", "W", 5.0, 7.0), ("MONDAY", "PM", "W", 6.0, 6.0)]


def test_hfo_hours():
    hours, _ = F.srf_hours(F.split_products(HFO), "HFO")
    # Monday 26 Oct HST (UTC-10): AM 08-11 = 18Z-21Z, PM 12-17 = 22Z 26 Oct to 03Z 27 Oct.
    n_am = {_utc(2026, 10, 26, h) for h in range(18, 22)}
    n_pm = {_utc(2026, 10, 26, h) for h in (22, 23)} | {_utc(2026, 10, 27, h) for h in range(0, 4)}
    north = {t: v[:2] for (z, f, t), v in hours.items() if z == "HIZ003" and f == "N"}
    assert north == {**{t: (9.0, 12.0) for t in n_am}, **{t: (10.0, 14.0) for t in n_pm}}
    assert len(hours) == 4 * 2 * 10                   # 4 zones x 2 facings x 10 hours


def test_classify_rounds_to_whole_feet():
    assert [F.classify(f, 2.0, 3.0) for f in (1.49, 1.5, 2.6, 3.49, 3.5)] == [
        "below", "inside", "inside", "inside", "above"]
    assert F.classify(None, 2.0, 3.0) is None
    assert [F.classify(f, 0.0, 1.0) for f in (0.0, 1.4, 1.5)] == ["inside", "inside", "above"]


# --------------------------------------------------------------------------- #
# Visitor reports                                                              #
# --------------------------------------------------------------------------- #
def test_buckets_match_the_frontend_scale():
    ts = (REPO / "frontend/lib/surfReport.ts").read_text()
    block = ts.split("export const BUCKET_FACE_FT", 1)[1].split("};", 1)[0]
    pairs = re.findall(r"(\w+):\s*([\d.]+)", block)
    assert {k: float(v) for k, v in pairs} == F.BUCKET_FACE_FT
    order = ts.split("export const SIZE_BUCKETS = [", 1)[1].split("]", 1)[0]
    assert re.findall(r"'(\w+)'", order) == list(F.BUCKET_FACE_FT)


def test_bucket_of_nearest_with_ties_to_the_smaller():
    assert F.bucket_of(3.4) == "waist"           # 3 and 4: 0.4 from waist
    assert F.bucket_of(3.5) == "waist"           # tie
    assert F.bucket_of(3.6) == "chest"
    assert F.bucket_of(13.0) == "double_overhead"   # 11 and 15: tie
    assert F.bucket_of(0.0) == "ankle" and F.bucket_of(40.0) == "triple_overhead_plus"


def test_bucket_side():
    assert F.bucket_side(3.2, "waist") == "inside"
    assert F.bucket_side(2.0, "waist") == "below"      # nearest thigh (2.25)
    assert F.bucket_side(4.6, "waist") == "above"      # nearest shoulder (5)
    assert F.bucket_side(None, "waist") is None


# --------------------------------------------------------------------------- #
# FRF lidar                                                                    #
# --------------------------------------------------------------------------- #
def test_lidar_hour_small_day():
    nan = float("nan")
    x = [80.0, 90.0, 100.0, 110.0, 120.0]
    hs = [0.3, 0.5, 0.9, 0.7, 0.6]
    miss = [0.0, 0.0, 0.0, 0.0, 30.0]                 # x 120 fails the missing rule
    # max 0.9 at x 100; most seaward good cell x 110; 100 <= 110 - 10, so a small day
    assert F.lidar_hour(x, hs, miss, 1) == {"hs_max": 0.9, "x_peak": 100.0,
                                             "x_seaward": 110.0, "small": True}
    hs2 = [0.3, 0.5, 0.6, 0.9, nan]
    got = F.lidar_hour(x, hs2, [0.0] * 5, 1)
    assert got["x_peak"] == 110.0 and got["x_seaward"] == 110.0 and got["small"] is False
    # 5 m inside the seaward end is not the 10 m needed
    got = F.lidar_hour([100.0, 105.0, 110.0], [0.5, 0.9, 0.6], [0.0] * 3, 1)
    assert got["x_peak"] == 105.0 and got["small"] is False


def test_lidar_hour_needs_flag_1_and_a_good_cell():
    assert F.lidar_hour([1.0], [0.5], [0.0], 2) is None
    assert F.lidar_hour([1.0], [0.5], [0.0], None) is None
    assert F.lidar_hour([1.0], [float("nan")], [0.0], 1) is None
    assert F.lidar_hour([1.0], [0.5], [20.0], 1) is None          # 20% is not under 20%


# --------------------------------------------------------------------------- #
# Aggregation and the comparisons                                              #
# --------------------------------------------------------------------------- #
def _row(key, pub, typ, sets, rp=None, rt=None, rs=None):
    return {"office": key, "published_side": pub, "typical_side": typ, "sets_side": sets,
            "published_ratio": rp, "typical_ratio": rt, "sets_ratio": rs}


def test_tally():
    rows = [_row("A", "inside", "below", "inside", 1.0, 0.5, 1.2),
            _row("A", "above", "inside", "above", 2.0, 1.0, 1.4),
            _row("B", "inside", None, "inside", None, None, 0.8),
            _row("B", "below", "below", "below", 0.5, 0.25, 0.3)]
    t = F.tally(rows, "office")
    assert list(t) == ["all", "A", "B"]
    assert t["A"]["published"] == {"inside": 0.5, "below": 0.0, "above": 0.5,
                                   "ratio_median": 1.5}
    assert t["B"]["typical"] == {"inside": 0.0, "below": 1.0, "above": 0.0, "ratio_median": 0.25}
    assert t["all"]["n"] == 4
    assert t["all"]["sets"] == {"inside": 0.5, "below": 0.25, "above": 0.25,
                                "ratio_median": 1.0}


def test_multiplier_spread():
    # Hs 1 m = 3.281 ft; typical 3.281, 4.9215, 6.562 ft -> 1.0, 1.5, 2.0; one at 13 s
    got = F.multiplier_spread([1.0, 1.0, 1.0, 1.0, 0.0], [3.281, 4.9215, 6.562, 3.281, 5.0],
                              [9.0, 9.0, 9.0, 13.0, 9.0])
    assert got["8-10 s"] == {"n": 3, "p25": 1.0, "median": 1.5, "p75": 2.0}
    assert got["12-14 s"] == {"n": 1, "p25": 1.0, "median": 1.0, "p75": 1.0}
    # all: 1.0, 1.5, 2.0, 1.0 sorted 1, 1, 1.5, 2 -> median 1.25, p25 [1] 1.0, p75 [3] 2.0
    assert got["all"] == {"n": 4, "p25": 1.0, "median": 1.25, "p75": 2.0}


def test_compare_srf_by_facing_and_ratio_to_the_middle():
    t = _utc(2026, 9, 15, 15)
    srf = {("NCZ203", None, t): (2.0, 4.0, "MHX"), ("HIZ003", "N", t): (9.0, 12.0, "HFO")}
    base = {"slug": "x", "state": "S", "region": "R", "type": "beach", "group": "headline",
            "hour": t, "hs": 1.0, "period": 9.0}
    hours = [dict(base, spot="A", office="MHX", zone="NCZ203", facing="E",
                  published=3.0, typical=1.2, sets=4.6),
             dict(base, spot="B", office="HFO", zone="HIZ003", facing="N",
                  published=10.5, typical=8.0, sets=10.0),
             dict(base, spot="C", office="HFO", zone="HIZ003", facing="S",
                  published=1.0, typical=1.0, sets=1.0),
             dict(base, spot="D", office="MHX", zone=None, facing="E",
                  published=1.0, typical=1.0, sets=1.0)]
    got = F.compare_srf(hours, srf)
    assert [g["spot"] for g in got] == ["A", "B"]
    a, b = got
    assert (a["published_side"], a["typical_side"], a["sets_side"]) == ("inside", "below", "above")
    assert (a["typical_ratio"], a["sets_ratio"]) == (0.4, 1.533)          # mid 3.0
    assert (b["typical_side"], b["typical_ratio"]) == ("below", 0.762)    # mid 10.5
    assert (a["hs"], a["period"], a["band"]) == (1.0, 9.0, "8-10 s")


def test_compare_reports():
    t = _utc(2026, 10, 1, 14)
    by_hour = {(7, t): {"spot": "A", "state": "Hawaii", "region": "Hawaii", "type": "reef",
                        "office": "HFO", "group": "headline", "hour": t,
                        "published": 3.2, "typical": 2.0, "sets": 4.6}}
    reports = [{"spot_id": 7, "observed_hour": "2026-10-01T14:00:00+00:00",
                "size_bucket": "waist"},
               {"spot_id": 7, "observed_hour": "2026-10-01T15:00:00+00:00",
                "size_bucket": "waist"}]
    got = F.compare_reports(reports, by_hour, {7: "A"})
    assert len(got) == 1
    g = got[0]
    assert (g["bucket"], g["bucket_ft"], g["state"]) == ("waist", 3.0, "Hawaii")
    assert (g["published_side"], g["typical_side"], g["sets_side"]) == ("inside", "below", "above")
    assert g["typical_ratio"] == 0.667
    assert (g["question_version"], g["sets_bucket"]) == (1, None)   # no version: version 1


def _rep(version, region, side, bucket="waist"):
    return {"question_version": version, "region": region, "type": "beach", "bucket": bucket,
            "published_side": side}


def test_reports_by_version_never_pools():
    rows = [_rep(1, "NC", "inside"), _rep(2, "NC", "below"), _rep(2, "SC", "above", "head")]
    got = F.reports_by_version(rows)
    assert list(got) == ["1", "2"]
    assert (got["1"]["n"], got["2"]["n"]) == (1, 2)
    assert got["1"]["by_region"]["all"]["published"]["inside"] == 1.0
    # version 2: one below and one above, so half each, and none inside
    v2 = got["2"]["by_region"]["all"]["published"]
    assert (v2["inside"], v2["below"], v2["above"]) == (0.0, 0.5, 0.5)
    assert set(got["2"]["by_bucket"]) == {"all", "head", "waist"}


def test_public_totals_show_only_regions_with_enough_reports():
    # version 2: NC has 4 reports (3 inside, 1 above); SC has 2; version 1: NC has 1
    rows = ([_rep(2, "NC", "inside")] * 3 + [_rep(2, "NC", "above")]
            + [_rep(2, "SC", "below")] * 2 + [_rep(1, "NC", "inside")])
    got = F.public_totals(rows, min_reports=3)
    assert got == {"min_reports": 3, "by_version": {
        "1": {"regions": {}, "suppressed": {"regions": 1, "reports": 1}},
        "2": {"regions": {"NC": {"n": 4, "inside": 0.75, "below": 0.0, "above": 0.25}},
              "suppressed": {"regions": 1, "reports": 2}}}}
    assert F.MIN_PUBLIC_REPORTS == 20
    assert F.public_totals(rows)["by_version"]["2"]["regions"] == {}      # 4 < 20
    # totals only: nothing names a spot, an hour or a reporter
    assert "spot" not in str(got) and "hour" not in str(got)


def test_compare_frf_small_days_only_and_the_ideal_input():
    t1, t2, t3 = _utc(2026, 10, 1, 14), _utc(2026, 10, 1, 15), _utc(2026, 10, 1, 16)
    t4, t5 = _utc(2026, 10, 1, 17), _utc(2026, 10, 1, 18)
    lidar = {t1: {"hs_max": 1.5, "x_peak": 100.0, "small": True},
             t2: {"hs_max": 1.0, "x_peak": 190.0, "small": False},
             t3: {"hs_max": 1.0, "x_peak": 100.0, "small": True},
             t4: {"hs_max": 1.0, "x_peak": 150.0, "small": True},     # 8 m Hs 1.01: outer bar
             t5: {"hs_max": 1.0, "x_peak": 100.0, "small": True}}     # no 8 m reading
    duck = [{"hour": t1, "typical": 4.5, "published": 6.0, "group": "moved (old point)"}]
    frf8 = {t1: (1.0, 500.0, 3), t2: (0.5, 8.0, 3), t3: (1.0, None, 3), t4: (1.01, 9.0, 3)}
    got = F.compare_frf(duck, lidar, frf8)
    assert got["small_day_hours"] == 2
    assert got["not_small"] == {"maximum at the transect's seaward end": 1,
                                "FRF 8 m Hs over 1.0 m": 1, "no FRF 8 m reading": 1}
    p1, p3 = got["pairs"]
    assert p1["observed_ft"] == round(1.5 * 3.281, 2) == 4.92
    assert p1["ours_typical"] == 4.5 and p1["group"] == "moved (old point)"
    assert p1["frf8_typical"] == 4.73                           # 4.731990 ft, d0 8 m
    assert p3["frf8_typical"] is None and p3["ours_typical"] is None
    assert got["ours_typical_vs_lidar_ft"]["n"] == 1
    assert got["ours_typical_vs_lidar_ft"]["ratio_median"] == round(4.5 / 4.92, 3)


def test_nearest_zone_within_the_tolerance():
    sq = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    far = {"type": "Polygon", "coordinates": [[[5, 5], [6, 5], [6, 6], [5, 6], [5, 5]]]}
    zones = F.prepare_zones({"AAZ001": ("AAA", sq), "BBZ002": ("BBB", far), "CCZ003": ("CCC", None)})
    assert set(zones) == {"AAZ001", "BBZ002"}
    # 0.01 deg of longitude at 0.5 N: 0.01 x 111.32 x cos(0.5 deg) = 1.1132 km
    z, d, office = F.nearest_zone(0.5, 1.01, zones)
    assert (z, office) == ("AAZ001", "AAA") and _close(d, 1.1132, 0.0005)
    assert F.nearest_zone(0.5, 1.03, zones) is None              # 3.34 km
    assert F.nearest_zone(0.5, 1.019, zones) is None             # 2.115 km, inside the box pad
    assert F.nearest_zone(0.5, 0.5, zones)[1] == 0.0


def test_nearest_zone_measures_longitude_at_the_spots_latitude():
    sq = {"type": "Polygon", "coordinates": [[[0, 59.5], [1, 59.5], [1, 60.5], [0, 60.5],
                                              [0, 59.5]]]}
    zones = F.prepare_zones({"AAZ001": ("AAA", sq)})
    # 0.03 deg of longitude at 60 N: 0.03 x 111.32 x 0.5 = 1.6698 km
    z, d, _ = F.nearest_zone(60.0, 1.03, zones)
    assert z == "AAZ001" and _close(d, 1.6698, 0.0005)


def test_nearest_zone_takes_the_nearer_of_two():
    a = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    b = {"type": "Polygon", "coordinates": [[[1.02, 0], [2, 0], [2, 1], [1.02, 1], [1.02, 0]]]}
    zones = F.prepare_zones({"AAZ001": ("AAA", a), "BBZ002": ("BBB", b)})
    assert F.nearest_zone(0.5, 1.012, zones)[0] == "BBZ002"     # 0.008 deg from b, 0.012 from a
    assert F.nearest_zone(0.5, 1.008, zones)[0] == "AAZ001"


# --------------------------------------------------------------------------- #
# run(), end to end on stand-ins                                               #
# --------------------------------------------------------------------------- #
class _Q:
    def __init__(self, data):
        self.data, self.f, self.r = data, {}, None

    def select(self, *_):
        return self

    def order(self, *_):
        return self

    def eq(self, c, v):
        self.f[c] = v
        return self

    def gte(self, c, v):
        self.f[">="] = (c, v)
        return self

    def lt(self, c, v):
        self.f["<"] = (c, v)
        return self

    def range(self, a, b):
        self.r = (a, b)
        return self

    def execute(self):
        rows = [r for r in self.data
                if all(r.get(c) == v for c, v in self.f.items() if c not in (">=", "<"))]
        if ">=" in self.f:
            rows = [r for r in rows if r[self.f[">="][0]] >= self.f[">="][1]]
        if "<" in self.f:
            rows = [r for r in rows if r[self.f["<"][0]] < self.f["<"][1]]
        a, b = self.r or (0, len(rows))
        return type("R", (), {"data": rows[a:b + 1]})()


class _Client:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables[name])


class _Resp:
    def __init__(self, text=None, js=None):
        self.text, self._js = text, js

    def json(self):
        return self._js


def test_run_end_to_end_on_stand_ins():
    # One spot inside NCZ203's square, every hour MOP Hs 1.0 m at 500 s: typical 4.95 ft,
    # sets 6.28 ft, published 3.0 ft, against MHX_1 + MHX_2's 17 daytime hours.
    since = datetime(2026, 9, 15, tzinfo=timezone.utc)
    until = datetime(2026, 9, 17, tzinfo=timezone.utc)
    rows = [{"spot_id": 1, "source": "nwps", "valid_time": datetime.fromtimestamp(
                _utc(2026, 9, 15, 0) + 3600 * i, timezone.utc).isoformat(),
             "swell_source": "cdip_mop", "face_ft_raw": 3.281, "face_ft": 3.0,
             "swell_tp": 500.0, "tp": 9.0, "hs": 0.5} for i in range(48)]
    client = _Client({"spots": [{"id": 1, "name": "Test Beach", "state": "North Carolina"}],
                      "forecasts": rows,
                      "surf_reports": [{"id": 1, "spot_id": 1, "size_bucket": "chest",
                                        "observed_hour": "2026-09-16T15:00:00+00:00"}]})
    square = {"type": "Polygon", "coordinates": [[[-76, 36], [-75, 36], [-75, 37], [-76, 37],
                                                  [-76, 36]]]}

    def get(url, params=None, timeout=120):
        if "afos" in url:
            return _Resp(text=(MHX_1 + MHX_2) if params["pil"] == "SRFMHX" else "")
        return _Resp(js={"geometry": square})

    def open_nc(url):
        raise OSError("no FRF here")

    roster = [{"name": "Test Beach", "nwps_wfo": "mhx", "lat": 36.5, "lng": -75.5,
               "orientation_deg": 90, "break_type": "beach", "swell_window_source": "cdip_mop"}]
    rep = F.run(client, since, until, roster, get=get, open_nc=open_nc)
    assert rep["errors"] == {}
    assert rep["hours_by_group"] == {"headline": 48}
    assert rep["zones"] == {"named": 1, "outlines": 1, "spots_mapped": 1, "spots_unmapped": []}
    a = rep["srf"]["by_office"]["MHX"]
    assert a["n"] == 17
    # typical 4.95: inside 3-5 (2.5 <= 4.95 < 5.5) on 5 hours; above 2-4 (4.95 >= 4.5) on 10
    # and above 0-1 on 2
    assert a["typical"]["inside"] == round(5 / 17, 3)
    assert a["typical"]["above"] == round(12 / 17, 3)
    # published 3.0: inside 3-5 (5) and 2-4 (10), above 0-1 (2)
    assert a["published"]["inside"] == round(15 / 17, 3)
    assert rep["reports"]["n_paired"] == 1
    # reported chest (4 ft): typical 4.95 is nearest shoulder (5), above; sets 6.28 nearest
    # head (6), above; published 3.0 is waist, below
    assert list(rep["reports"]["by_version"]) == ["1"]   # a row with no version answered 1
    r = rep["reports"]["by_version"]["1"]["by_region"]["all"]
    assert (r["typical"]["above"], r["sets"]["above"], r["published"]["below"]) == (1.0, 1.0, 1.0)
    assert rep["frf"]["small_day_hours"] == 0
    assert rep["frf_8m_months"] == ["unread 202609: no FRF here"]


# --------------------------------------------------------------------------- #
# The scale on typical by period band (run 53)                                 #
# --------------------------------------------------------------------------- #
def test_side_counts_by_hand():
    # typical 2.0 and 4.0 against 2 to 3 ft (inside 1.5 <= face < 3.5):
    #   k 0.5 -> 1.0 below, 2.0 inside; k 1.0 -> 2.0 inside, 4.0 above; k 1.5 -> 3.0 in, 6.0 above
    n, inside, below, above = F.side_counts([2.0, 4.0], [2.0, 2.0], [3.0, 3.0], (0.5, 1.0, 1.5))
    assert n == 2
    assert (inside.tolist(), below.tolist(), above.tolist()) == ([1, 1, 1], [1, 0, 0], [0, 1, 1])
    # the edges: 3.5 (= hi + 0.5) is above, 1.5 (= lo - 0.5) is inside
    n, inside, below, above = F.side_counts([3.5, 1.5], [2.0, 2.0], [3.0, 3.0], (1.0,))
    assert (inside.tolist(), below.tolist(), above.tolist()) == ([1], [0], [1])


def test_fit_scale_honours_the_cap_on_hours_above():
    # 8 hours at typical 2.0 and 2 at 4.4, all against 2 to 3 ft (inside 1.5 <= face < 3.5):
    #   k 0.7 -> 1.4 (below) x8, 3.08 (inside) x2   inside 2, below 8, above 0
    #   k 0.8 -> 1.6 (inside) x8, 3.52 (above) x2   inside 8, above 2 (20%)
    #   k 1.0 -> 2.0 (inside) x8, 4.4 (above) x2    inside 8, above 2
    typ = [2.0] * 8 + [4.4] * 2
    got = F.fit_scale(typ, [2.0] * 10, [3.0] * 10, ks=(0.7, 0.8, 1.0))
    assert got == {"n": 10, "k": 0.7, "inside": 0.2, "below": 0.8, "above": 0.0,
                   "best_k": 0.8, "best_inside": 0.8, "balanced_k": 0.8}
    # a 20% cap admits 0.8 (ties to the smaller k)
    assert F.fit_scale(typ, [2.0] * 10, [3.0] * 10, cap=0.2, ks=(0.7, 0.8, 1.0))["k"] == 0.8


def test_fit_scale_strict_holds_every_group_to_the_cap():
    # Group A: 9 hours at typical 2.0, group B: 1 hour at 3.4, all against 2-3 ft
    # (inside 1.5 <= face < 3.5).
    #   k 1.1: A 2.2 inside x9; B 3.74 above. Pooled: 1 of 10 above = 10%, meets the cap;
    #          B alone: 1 of 1 above = 100%, fails it.
    #   k 1.2: A 2.4 inside x9; B 4.08 above. The same.
    typ, lo, hi = [2.0] * 9 + [3.4], [2.0] * 10, [3.0] * 10
    groups = ["A"] * 9 + ["B"]
    assert F.fit_scale(typ, lo, hi, ks=(1.1, 1.2))["k"] == 1.1     # pooled, ties to the smaller
    assert F.fit_scale(typ, lo, hi, groups=groups, ks=(1.1, 1.2))["k"] is None
    # k 1.0: B 3.4 inside, so every group meets the cap and all 10 hours are inside
    strict = F.fit_scale(typ, lo, hi, groups=groups, ks=(1.0, 1.1))
    assert (strict["k"], strict["inside"]) == (1.0, 1.0)


def test_fit_scale_with_no_rows():
    assert F.fit_scale([], [], []) == {"n": 0, "k": None}


def test_sides_at():
    rows = [{"typical": 2.0, "lo": 2.0, "hi": 3.0}, {"typical": 4.0, "lo": 2.0, "hi": 3.0}]
    assert F.sides_at(rows, lambda r: 1.0) == {"n": 2, "inside": 0.5, "below": 0.0, "above": 0.5}
    assert F.sides_at(rows, lambda r: 0.5) == {"n": 2, "inside": 0.5, "below": 0.5, "above": 0.0}
    assert F.sides_at([], lambda r: 1.0) == {"n": 0}


def _srf(band_period, typical, lo, hi, region, office="MHX", group="headline", hs=1.0):
    return {"group": group, "office": office, "region": region, "typical": typical, "lo": lo,
            "hi": hi, "hs": hs, "period": band_period, "band": F.period_band(band_period)}


def test_fit_one_scale_per_band_for_every_region():
    # Against 2-3 ft, inside is 1.5 <= k x typical < 3.5.
    # Band "<8 s": 200 hours at typical 4.0. Inside for k 0.50 to 0.87 (3.48), above from 0.88
    #   (3.52); never below (4 x 0.50 = 2.0). Capped k: the smallest with all inside, 0.50. The
    #   balanced k is the first with above >= below: 0 >= 0 at 0.50.
    # Band "12-14 s": 100 hours in region X at 2.0 and 100 in region Y at 3.0. X needs k >= 0.75,
    #   Y needs k < 1.1667; the smallest k with all 200 inside is 0.75.
    rows = ([_srf(6.0, 4.0, 2.0, 3.0, "X")] * 200
            + [_srf(13.0, 2.0, 2.0, 3.0, "X")] * 100 + [_srf(13.0, 3.0, 2.0, 3.0, "Y")] * 100
            + [_srf(9.0, 2.0, 2.0, 3.0, "X")] * 5                      # a band too small to fit
            + [_srf(13.0, 2.0, 2.0, 3.0, "Hawaii", office="HFO")] * 50   # left out
            + [_srf(13.0, 9.0, 2.0, 3.0, "X", group="moved (old point)")] * 50)
    got = F.fit(rows)
    assert got["hours"] == 405
    lt8 = got["by_band"]["<8 s"]["capped"]
    assert (lt8["k"], lt8["inside"], lt8["above"], lt8["best_k"], lt8["balanced_k"]) == (
        0.5, 1.0, 0.0, 0.5, 0.5)
    assert got["by_band"]["<8 s"]["face_over_hs_median"] == round(0.5 * 4.0 / 3.281, 3)
    b12 = got["by_band"]["12-14 s"]["capped"]
    assert (b12["k"], b12["inside"]) == (0.75, 1.0)
    assert got["by_band"]["8-10 s"] == {"n": 5}
    assert got["k_by_band"] == {"<8 s": 0.5, "12-14 s": 0.75}
    assert got["by_region"]["Y"]["at_band_k"] == {"n": 100, "inside": 1.0, "below": 0.0,
                                                  "above": 0.0}
    assert set(got["office_band"]["MHX"]) == {"<8 s", "12-14 s"}         # 5 hours: not fitted
    assert got["flat"]["capped"]["n"] == 405


def test_fit_strict_differs_from_capped_when_one_region_runs_high():
    # Band "8-10 s", against 2-3 ft (inside 1.5 <= face < 3.5): region X 190 hours at typical
    # 1.2 (inside from k 1.25), region Y 10 hours at 3.4 (inside for k 0.50 to 1.02, above from
    # 1.03).
    #   capped: k 1.25 puts X's 190 inside and Y's 10 above: 10 of 200 = 5% above, under the cap.
    #   strict: Y alone must be at most 10% above, so k <= 1.02, where X is below; the most
    #   inside is then Y's 10, from k 0.50 (the smallest).
    rows = [_srf(9.0, 1.2, 2.0, 3.0, "X")] * 190 + [_srf(9.0, 3.4, 2.0, 3.0, "Y")] * 10
    got = F.fit(rows)
    b = got["by_band"]["8-10 s"]
    assert (b["capped"]["k"], b["capped"]["inside"], b["capped"]["above"]) == (1.25, 0.95, 0.05)
    assert (b["strict"]["k"], b["strict"]["inside"], b["strict"]["below"]) == (0.5, 0.05, 0.95)
    assert got["k_strict_by_band"] == {"8-10 s": 0.5}
    assert got["by_region"]["Y"]["at_band_k"]["above"] == 1.0
    assert got["by_region"]["Y"]["at_strict_k"]["inside"] == 1.0


def test_frf_at_band_scales():
    # 8 m period 9 s (8-10 s band, k 0.8): 0.8 x 5.0 = 4.0 vs 4.0 observed -> ratio 1.0;
    # 13 s (no k for that band): left out; no typical: left out.
    pairs = [{"frf8_tp": 9.0, "frf8_typical": 5.0, "observed_ft": 4.0},
             {"frf8_tp": 13.0, "frf8_typical": 5.0, "observed_ft": 4.0},
             {"frf8_tp": 9.0, "frf8_typical": None, "observed_ft": 4.0}]
    got = F.frf_at_scales(pairs, {"8-10 s": 0.8})
    assert got["n"] == 1 and got["ratio_median"] == 1.0


def test_lidar_months_retry_once_then_name_the_month():
    calls = []

    def flaky(url):
        calls.append(url)
        raise OSError("NetCDF: DAP server error")

    got, cov = F.lidar_hourly(datetime(2026, 10, 1, tzinfo=timezone.utc),
                              datetime(2026, 10, 2, tzinfo=timezone.utc), open_nc=flaky)
    assert got == {} and len(calls) == 2                     # one month, read twice
    assert cov == {"202610": "unread: NetCDF: DAP server error"}

    class _Var:
        def __init__(self, v):
            self.v = v

        def __getitem__(self, _):
            import numpy as np
            return np.ma.masked_invalid(np.asarray(self.v, dtype=float))

    class _DS:
        # one hour, 2026-10-01 14:00Z, flag 1, peak 0.9 at x 100, seaward cell x 110
        t = datetime(2026, 10, 1, 14, tzinfo=timezone.utc).timestamp()
        variables = {"xFRF": _Var([100.0, 110.0]), "time": _Var([t]),
                     "waveHs": _Var([[0.9, 0.5]]), "percentTimeSeriesMissing": _Var([[0.0, 0.0]]),
                     "hydrodynamicsFlag": _Var([1.0])}

    tries = []

    def second_time(url):
        tries.append(url)
        if len(tries) == 1:
            raise OSError("File is truncated")
        return _DS()

    got, cov = F.lidar_hourly(datetime(2026, 10, 1, tzinfo=timezone.utc),
                              datetime(2026, 10, 2, tzinfo=timezone.utc), open_nc=second_time)
    assert len(tries) == 2
    assert got == {int(_DS.t): {"hs_max": 0.9, "x_peak": 100.0, "x_seaward": 110.0,
                                "small": True}}
    assert cov == {"202610": {"flag 1": 1, "maximum inside the transect": 1}}


def test_assess_display_is_the_surf_height_only_where_the_site_will_show_it():
    """display: 0.70 x typical on an NWPS-override hour at a spot with no factor, published
    elsewhere. face_ft_raw 4.27 at 500 s is Hs 4.27 / (1.3 x 3.281) = 1.0011 -> 1.00 m, typical
    1.508057 x 3.281 = 4.947935 -> 4.95, display 0.70 x 4.947935 = 3.4636 -> 3.46 (from the
    unrounded typical, as apply_surf_height computes it)."""
    spot = {"name": "Rockaway", "nwps_wfo": "okx"}
    rows = [{"valid_time": "2026-10-01T15:00:00+00:00", "swell_source": src, "face_ft_raw": 4.27,
             "face_ft": 4.27, "swell_tp": 500.0, "tp": 9.0, "hs": 0.7}
            for src in ("nwps", "nwps_height_ww3_dir", "cdip_mop", "ww3", "nwps_swell")]
    got = F.assess(rows, spot, "New York")
    assert [g["display"] for g in got] == [3.46, 3.46, 4.27, 4.27, 4.27]
    assert [g["source"] for g in got][:2] == ["nwps", "nwps_height_ww3_dir"]
    assert got[0]["typical"] == 4.95 and got[0]["calibrated"] is False
    # Hs 8.10 m is already breaking at 10 m: typical 8.10 x 3.281 = 26.5761 (26.58 shown), and
    # display 0.70 x 26.5761 = 18.6033 -> 18.60, where 0.70 x the rounded 26.58 would give 18.61.
    # face_ft_raw 8.10 x 4.2653 = 34.549 -> 34.55, which sizes back to 34.55 / 4.2653 = 8.1003 -> 8.10.
    big = F.assess([dict(rows[0], face_ft_raw=34.55, face_ft=34.55)], spot, "New York")[0]
    assert (big["typical"], big["display"]) == (26.58, 18.6)
    cal = F.assess(rows, spot, "New York", calibrated=True)
    assert [g["display"] for g in cal] == [4.27] * 5 and cal[0]["calibrated"] is True


def test_assess_display_shoals_at_the_wind_seas_period_where_the_site_will():
    """A storm hour (shown_period's Jacksonville case): face_ft_raw 4.27 at swell_tp 3 s
    (period_factor 'ww3' 1.0) is Hs 4.27 / 3.281 = 1.30143 -> 1.30 m. With a 2.4 m wind sea at
    500 s the display shoals at 500 s (Green's law): 0.70 x 1.508057 x 1.30^0.8 x 3.281 =
    0.70 x 1.860248 x 3.281 = 4.2724 -> 4.27. A 0.2 m wind sea does not take over, and the
    display is then the rated-period one."""
    spot = {"name": "Jacksonville Beach Pier", "nwps_wfo": "jax"}
    row = {"valid_time": "2026-10-27T15:00:00+00:00", "swell_source": "nwps_height_ww3_dir",
           "face_ft_raw": 4.27, "face_ft": 4.27, "swell_tp": 3.0, "swell_dp": 80.0, "tp": 9.0,
           "hs": 2.0, "swell_2_hs": 0.24, "swell_2_tp": 3.0, "swell_2_dp": 80.0,
           "wind_wave_hs": 2.4, "wind_wave_tp": 500.0, "wind_wave_dp": 70.0}
    storm, calm = F.assess([row, dict(row, wind_wave_hs=0.2)], spot, "Florida")
    assert storm["switched"] is True and storm["display"] == 4.27
    assert storm["display_rated"] != storm["display"]
    assert calm["switched"] is False and calm["display"] == calm["display_rated"]
    cal = F.assess([row], spot, "Florida", calibrated=True)[0]
    assert cal["switched"] is False and cal["display"] == cal["display_rated"] == 4.27
    # the case, and the comparison variant: LONGER keeps the fix's height
    assert (storm["case"], storm["display_variant"]) == ("longer", 4.27)
    assert (calm["case"], calm["display_variant"]) == (None, calm["display_rated"])
    assert (cal["case"], cal["display_variant"]) == (None, 4.27)


def test_assess_reads_the_spots_window_and_keeps_dominant_at_the_rated_period_in_the_variant():
    """The storm hour's wind sea from 70 at a spot whose window is 180-270 (span 94: sector
    178-272) is 108 deg outside it: not switched, display at the rated period. A DOMINANT hour (a
    0.24 m swell rated at 600 s under a 1.0 m wind sea at 500 s, 4.17x; 4.27 ft raw at 600 s is
    Hs 1.00 m, 3.46 ft at either long period) is switched, and its variant is the rated-period
    display, here the same 3.46."""
    spot = {"name": "Jacksonville Beach Pier", "nwps_wfo": "jax",
            "swell_window_arcs": [{"min": 180, "max": 270, "span": 94}]}
    row = {"valid_time": "2026-10-27T15:00:00+00:00", "swell_source": "nwps_height_ww3_dir",
           "face_ft_raw": 4.27, "face_ft": 4.27, "swell_tp": 3.0, "swell_dp": 80.0, "tp": 9.0,
           "hs": 2.0, "swell_2_hs": 0.24, "swell_2_tp": 3.0, "swell_2_dp": 80.0,
           "wind_wave_hs": 2.4, "wind_wave_tp": 500.0, "wind_wave_dp": 70.0}
    behind = F.assess([row], spot, "Florida")[0]
    assert (behind["switched"], behind["case"]) == (False, None)
    assert behind["display"] == behind["display_rated"] == behind["display_variant"]
    dom = dict(row, swell_tp=600.0, swell_2_tp=600.0, wind_wave_hs=1.0, wind_wave_dp=200.0)
    d = F.assess([dom], spot, "Florida")[0]
    assert (d["switched"], d["case"], d["display"], d["display_rated"], d["display_variant"]) == (
        True, "dominant", 3.46, 3.46, 3.46)
    # the same DOMINANT wind sea at 8 s: the fix shoals at 8 s, under the long-wave 3.46; the
    # variant stays at the rated period's 3.46
    d8 = F.assess([dict(dom, wind_wave_tp=8.0)], spot, "Florida")[0]
    assert (d8["case"], d8["display_rated"], d8["display_variant"]) == ("dominant", 3.46, 3.46)
    assert d8["display"] < 3.46
