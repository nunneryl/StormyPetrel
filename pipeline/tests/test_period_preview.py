"""period_preview, the merge gate for the swell train the site shows: the archive's labels and
counts, the buoy join, the published-window replay and its checks, the stars on the wind sea,
and the reproduction of run 54's NWS table.

HAND-WORKED VALUES. Heights use the long-period (Green's law) cases of test_surf_height.py: a face
of 4.27 ft at swell_tp 500 s (period_factor 'ww3' 1.3) is Hs 1.00 m and shows 0.70 x 1.508057 x
3.281 = 3.46 ft; at swell_tp 3 s (period_factor 1.0) the same 4.27 ft is Hs 4.27 / 3.281 = 1.30 m,
and shoaled at a 500 s wind sea it shows 0.70 x 1.508057 x 1.30^0.8 x 3.281 = 4.27 ft. NWS sides
follow face_compare.classify: inside when lo - 0.5 <= face < hi + 0.5.

Run: python -m pytest pipeline/tests/test_period_preview.py
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from pipeline import period_preview as P

STAMP = "2:2026-10-06:abcd1234"
MARK = "+surf:k0.70:d10:g0.78"
FACTORS = {"steamer-lane": {"factor": 2.0, "measured_on": "2026-10-06"}}
ROSTER = [{"name": "Jacksonville Beach Pier", "nwps_wfo": "jax", "nearest_buoy_id": "41117"},
          {"name": "Steamer Lane", "nwps_wfo": "mtr", "nearest_buoy_id": "46284"},
          {"name": "Kure Beach", "nwps_wfo": "ilm", "nearest_buoy_id": "41110"}]


def _slug(name):
    return name.lower().replace(" ", "-")


def _row(t, face, raw, version, swell_tp=500.0, wind_hs=0.1, wind_tp=9.0, source="nwps_height_ww3_dir",
         stars=3.0, **kw):
    r = {"valid_time": f"2026-10-28T{t:02d}:00:00+00:00", "face_ft": face, "face_ft_raw": raw,
         "face_correction_version": version, "swell_source": source, "swell_tp": swell_tp,
         "swell_dp": 80.0, "tp": 9.1, "dp": 72.0, "stars": stars, "effective_size_ft": 4.0,
         "swell_hs": 0.3, "swell_1_hs": 0.24, "swell_1_tp": swell_tp, "swell_1_dp": 80.0,
         "wind_wave_hs": wind_hs, "wind_wave_tp": wind_tp, "wind_wave_dp": 70.0}
    r.update(kw)
    return r


def _published():
    """As the pipeline published them after #283: Jacksonville's quiet hour (4.27 ft raw at
    500 s -> 3.46), its storm hour (rated on a 0.24 m 3 s partition beside a 2.4 m 500 s wind
    sea; #283 showed 2.95 at 3 s), Steamer Lane calibrated (4.27 / 2.0 = 2.135 -> 2.13), and a
    WW3-path hour at Kure Beach."""
    return {
        "Jacksonville Beach Pier": [
            _row(12, 3.46, 4.27, STAMP + MARK),
            _row(13, 2.95, 4.27, STAMP + MARK, swell_tp=3.0, wind_hs=2.4, wind_tp=500.0),
        ],
        "Steamer Lane": [_row(12, 2.13, 4.27, STAMP, stars=4.0)],
        "Kure Beach": [_row(12, 3.0, 3.0, STAMP, source="ww3", wind_hs=2.4, wind_tp=500.0,
                            swell_tp=3.0, stars=4.0)],
    }


# --------------------------------------------------------------------------- #
# 1 — the archive                                                              #
# --------------------------------------------------------------------------- #
def test_classify_hour_labels():
    storm = _row(13, 2.95, 4.27, STAMP, swell_tp=3.0, wind_hs=2.4, wind_tp=9.0)
    longer = {3.0: "longer", 4.0: "longer", 6.0: "longer"}
    assert P.classify_hour(storm) == {"unmatched": False, "big_wind_sea": True, "switched": True,
                                      "case": "longer", "cases": longer, "bigger_shorter": False,
                                      "marginal": False, "rated_hs": 0.24}
    # 0.4 m wind sea: bigger than 0.24 and 6 s longer, but not 2x (0.48)
    assert P.classify_hour(dict(storm, wind_wave_hs=0.4))["big_wind_sea"] is False
    assert P.classify_hour(dict(storm, wind_wave_hs=0.4))["case"] == "longer"
    # 10x the rated swell and shorter: DOMINANT at every K (2.4 >= 6 x 0.24 = 1.44)
    scrap = dict(storm, swell_tp=15.1, swell_1_tp=15.1, wind_wave_tp=5.2)
    assert P.classify_hour(scrap) == {"unmatched": False, "big_wind_sea": False, "switched": True,
                                      "case": "dominant",
                                      "cases": {3.0: "dominant", 4.0: "dominant", 6.0: "dominant"},
                                      "bigger_shorter": True, "marginal": False, "rated_hs": 0.24}
    # Lower Trestles, 2x and shorter: bigger_shorter, switched at no K (0.48 < 3 x 0.24 = 0.72)
    trestles = dict(scrap, wind_wave_hs=0.48)
    lab = P.classify_hour(trestles)
    assert (lab["switched"], lab["cases"], lab["bigger_shorter"]) == (
        False, {3.0: None, 4.0: None, 6.0: None}, True)
    # 3.2x and shorter: DOMINANT at K 3 only (0.768 >= 0.72; < 0.96)
    assert P.classify_hour(dict(scrap, wind_wave_hs=0.768))["cases"] == {3.0: "dominant", 4.0: None, 6.0: None}
    # bigger and 0.3 s longer, 2.1x: marginal, no longer switched
    m = P.classify_hour(dict(storm, wind_wave_hs=0.5, wind_wave_tp=3.3))
    assert (m["marginal"], m["switched"], m["bigger_shorter"]) == (True, False, False)
    assert P.classify_hour(dict(storm, swell_1_tp=4.0)) == {"unmatched": True}
    assert P.classify_hour(dict(storm, swell_source="nwps")) is None


def test_the_archive_counts_by_state_and_keeps_the_switched_hours():
    a = P.Archive({s["name"]: s for s in ROSTER})
    jax = {"name": "Jacksonville Beach Pier", "state": "Florida"}
    rows = [_row(12, 4.27, 4.27, STAMP, swell_tp=400.0, wind_hs=2.4, wind_tp=500.0),   # switched
            _row(13, 4.27, 4.27, STAMP),                                               # quiet
            _row(14, 4.27, 4.27, STAMP, swell_1_tp=4.0),                               # unmatched
            _row(15, 4.27, 4.27, STAMP, source="buoy")]                                # not WW3
    a(jax, ROSTER[0], rows, False)
    a({"name": "Steamer Lane", "state": "California"}, ROSTER[1],
      [_row(12, 2.13, 4.27, STAMP, swell_tp=400.0, wind_hs=2.4, wind_tp=500.0)], True)
    a.finish()
    t = a.table()
    # the switched hour is LONGER (500 - 400 s) and 10x (2.4 / 0.24) on an NWPS-override hour
    # with no stored alignment term
    assert t["Florida"] == {"hours": 4, "ww3_identity": 3, "unmatched": 1, "big_wind_sea": 1,
                            "switched": 1, "switched_longer": 1, "switched_dominant": 0,
                            "bigger_shorter": 0, "marginal": 0, "nwps_ratio_over_4": 1,
                            "nwps_ratio_over_4_alignment_under_half": 0,
                            "switched_k3": 1, "switched_k4": 1, "switched_k6": 1}
    assert t["all"]["switched"] == 2 and t["all"]["hours"] == 5
    jx, sl = a.records
    # 4.27 at 400 s and at 500 s are both in the long-wave limit: Hs 1.00 m, 3.46 ft either way
    assert (jx["surf"], jx["surf_before"], jx["surf_after"], jx["buoy"]) == (True, 3.46, 3.46, "41117")
    assert (jx["rated_tp"], jx["wind_tp"], jx["rated_hs_m"], jx["wind_hs_m"]) == (400.0, 500.0, 0.24, 2.4)
    assert (sl["surf"], sl["surf_before"], sl["calibrated"]) == (False, None, True)


def test_join_buoys_takes_the_nearest_observation_within_30_minutes():
    h = int(datetime(2026, 10, 27, 15, tzinfo=timezone.utc).timestamp())
    recs = [{"buoy": "41117", "hour": h}, {"buoy": "41117", "hour": h + 7200},
            {"buoy": "none", "hour": h}]
    obs = {"41117": [(h - 1500, {"hs": 2.6, "tp": 9.0, "swell_tp": 11.0}),
                     (h + 600, {"hs": 2.7, "tp": 10.0, "swell_tp": None}),
                     (h + 7200 + 1860, {"hs": 9.9, "tp": 1.0})]}
    P.join_buoys(recs, obs)
    assert (recs[0]["buoy_hs_m"], recs[0]["buoy_tp"], recs[0]["buoy_swell_tp"]) == (2.7, 10.0, None)
    assert recs[1]["buoy_hs_m"] is None                     # 31 minutes away
    assert recs[2]["buoy_tp"] is None


def test_summarize_switched():
    recs = [{"state": "Florida", "rated_tp": 3.0, "wind_tp": 9.0, "buoy_tp": 10.0,
             "buoy_swell_tp": 12.0, "rated_hs_m": 0.24, "wind_hs_m": 2.4, "hs_m": 2.5,
             "buoy_hs_m": 2.7, "surf_before": 4.0, "surf_after": 6.0},
            {"state": "Florida", "rated_tp": 4.0, "wind_tp": 8.0, "buoy_tp": None,
             "buoy_swell_tp": None, "rated_hs_m": 0.3, "wind_hs_m": 2.0, "hs_m": None,
             "buoy_hs_m": None, "surf_before": None, "surf_after": None}]
    s = P.summarize_switched(recs)["Florida"]
    assert (s["hours"], s["with_buoy"], s["period_before"], s["period_after"]) == (2, 1, 3.5, 8.5)
    assert (s["near_buoy_dpd_before"], s["near_buoy_dpd_after"]) == (0.0, 1.0)   # |3-10|, |9-10|
    # 2.7 m = 8.8587 ft; 4.0 / 8.8587 = 0.4515 -> 0.45; 6.0 / 8.8587 = 0.6773 -> 0.68
    assert (s["buoy_hs_ft"], s["surf_over_buoy_hs_before"], s["surf_over_buoy_hs_after"]) == (8.86, 0.45, 0.68)
    # rated 0.24 m and 0.3 m: 0.7874 and 0.9843 ft, median 0.88585 -> 0.89
    assert (s["rated_hs_ft"], s["model_hs_ft"], s["surf_hours"]) == (0.89, 8.2, 1)


# --------------------------------------------------------------------------- #
# 2 — the published window                                                     #
# --------------------------------------------------------------------------- #
def test_before_surf_takes_a_surf_row_back_and_leaves_others():
    r = _row(12, 3.46, 4.27, STAMP + MARK + ":windsea")
    b = P.before_surf(r)
    assert (b["face_ft"], b["face_correction_version"]) == (4.27, STAMP)
    assert P.before_surf(_row(12, 2.13, 4.27, STAMP)) == _row(12, 2.13, 4.27, STAMP)
    assert P.before_surf(_row(12, 3.46, 4.27, MARK))["face_correction_version"] is None


def test_the_replay_passes_its_gate():
    pub, after, stats = P.replay(_published(), ROSTER, FACTORS, _slug)
    jax = after["Jacksonville Beach Pier"]
    assert [e["face_ft"] for e in jax] == [3.46, 4.27]
    assert jax[1]["face_correction_version"] == STAMP + MARK + ":windsea"
    assert pub == _published()
    g = P.gate_rows(pub, after)
    assert g["counts"] == {"hours": 4, "surf_unswitched": 1, "switched": 1, "switched_face_moved": 1,
                           "star_differences": 0, "other_field_differences": 0, "unexpected": 0,
                           "unswitched_mismatch": 0, "badge_flips": 0}
    assert stats["hours_wind_sea"] == 1


def _counts(mutate_pub=None, mutate_after=None):
    pub0 = _published()
    if mutate_pub:
        mutate_pub(pub0)
    pub, after, _ = P.replay(pub0, ROSTER, FACTORS, _slug)
    if mutate_after:
        mutate_after(after)
    return P.gate_rows(pub, after)["counts"]


def test_a_published_surf_row_the_replay_does_not_reproduce_fails():
    c = _counts(lambda p: p["Jacksonville Beach Pier"][0].update(face_ft=3.47))
    assert c["unswitched_mismatch"] == 1


def test_a_row_without_its_surf_mark_fails():
    c = _counts(lambda p: p["Jacksonville Beach Pier"][0].update(face_correction_version=STAMP))
    assert c["unexpected"] == 1


def test_a_star_or_another_field_that_moves_fails():
    c = _counts(mutate_after=lambda a: a["Kure Beach"][0].update(stars=4.5))
    assert c["star_differences"] == 1 and c["other_field_differences"] == 1 and c["unexpected"] == 1
    c = _counts(mutate_after=lambda a: a["Steamer Lane"][0].update(face_ft=3.0))
    assert c["unexpected"] == 1 and c["badge_flips"] == 0


def test_a_switched_row_with_the_wrong_mark_fails():
    def m(a):
        a["Jacksonville Beach Pier"][1]["face_correction_version"] = "x" + MARK + ":windsea"
    c = _counts(mutate_after=m)
    assert c["unexpected"] == 1 and c["switched"] == 1


def test_tiles_and_homepage():
    pub, after, _ = P.replay(_published(), ROSTER, FACTORS, _slug)
    states = {"Jacksonville Beach Pier": "Florida", "Steamer Lane": "California",
              "Kure Beach": "North Carolina"}
    t, hours = P.tiles(pub, after, states)
    # Jacksonville's storm hour and Kure Beach's WW3-path hour switch; only Jacksonville's face moves
    assert t["all"] == {"hours": 2, "period_before": 3.0, "period_after": 500.0, "surf_hours": 1,
                        "face_before": 2.95, "face_after": 4.27}
    assert [(h["spot"], h["dir_before"], h["dir_after"]) for h in hours] == [
        ("Jacksonville Beach Pier", 80.0, 70.0), ("Kure Beach", 80.0, 70.0)]
    hp = P.homepage(pub, after, {"Jacksonville Beach Pier": 3, "Steamer Lane": 2, "Kure Beach": 1})
    assert [(h["spot"], h["period_before"], h["period_after"], h["train_after"]) for h in hp] == [
        ("Kure Beach", 3.0, 500.0, "wind_sea"), ("Steamer Lane", 500.0, 500.0, "swell"),
        ("Jacksonville Beach Pier", 500.0, 500.0, "swell")]


def test_stars_on_the_wind_sea(monkeypatch):
    """A stand-in rater: 4.0 stars at 9 s or more, else 3.0. Jacksonville's storm hour reproduces
    (3.0 at 3 s) and would rate 4.0 on the wind sea; its quiet hour reproduces and is not
    switched; a second storm hour published at 2.0 does not reproduce and is left out."""
    from pipeline.forecast import nwps_nearshore as N
    seen = []

    def fake(hs, per, swell_dir, *a, **kw):
        seen.append((round(hs, 4), per, swell_dir))
        return (4.0 if per >= 9 else 3.0), None, None, None, None
    monkeypatch.setattr(N, "nwps_stars", fake)
    pub = {"Jacksonville Beach Pier": [
        _row(12, 3.46, 4.27, STAMP + MARK, swell_tp=5.0, swell_1_tp=5.0, wind_mult=1.0, tide_mult=1.0,
             chop_ratio=0.5, chop_mult=0.9),
        _row(13, 2.95, 4.27, STAMP + MARK, swell_tp=3.0, swell_1_tp=3.0, wind_hs=2.4, wind_tp=9.0,
             wind_mult=1.0, tide_mult=1.0, chop_ratio=0.5, chop_mult=0.9),
        _row(14, 2.95, 4.27, STAMP + MARK, swell_tp=3.0, swell_1_tp=3.0, wind_hs=2.4, wind_tp=9.0,
             stars=2.0, wind_mult=1.0, tide_mult=1.0, chop_ratio=0.5, chop_mult=0.9),
        _row(15, 2.95, 4.27, STAMP + MARK)]}                     # no wind_mult: no inputs
    roster = {"Jacksonville Beach Pier": dict(ROSTER[0], state="Florida", orientation_deg=80.0)}
    q = P.stars_on_the_wind_sea(pub, roster, FACTORS)
    assert q["counts"] == {"no_inputs": 1, "replayed": 3, "reproduced": 2, "switched": 2,
                           "switched_not_reproduced": 1}
    assert q["star_change_on_switched"] == {"1.0": 1}
    assert q["by_state"] == {"Florida": {"hours": 1, "up": 1, "down": 0}}
    # rated on (3.0 s, 80 deg), then on the wind sea (9.0 s, 70 deg); Hs 4.27 / 3.281 = 1.3014
    assert (1.3014, 9.0, 70.0) in seen and (1.3014, 3.0, 80.0) in seen


# --------------------------------------------------------------------------- #
# 3 — against the NWS                                                          #
# --------------------------------------------------------------------------- #
T0 = int(datetime(2026, 10, 1, 15, tzinfo=timezone.utc).timestamp())
LATE = int(datetime(2026, 10, 27, 18, tzinfo=timezone.utc).timestamp())


def _srf(rated, shown, region="R", switched=False, office="ILM", group="headline", hour=T0):
    return {"region": region, "office": office, "group": group, "hour": hour, "lo": 2.0, "hi": 3.0,
            "display_rated": rated, "display": shown, "switched": switched}


def test_nws_tables():
    # range 2-3 ft: inside from 1.5 to under 3.5
    rows = [_srf(1.0, 2.0, switched=True), _srf(2.0, 2.0), _srf(4.0, 4.0, region="S"),
            _srf(1.0, 1.0, office="HFO"), _srf(1.0, 1.0, group="base"), _srf(1.0, 1.0, hour=LATE)]
    want = {"all": (3, 0.333, 0.333, 0.333), "R": (2, 0.5, 0.5, 0.0)}
    got = P.nws_tables(rows, want=want)
    assert got["ok"] and {r: e["got"] for r, e in got["reproduction"].items()} == want
    assert got["fixed"]["all"] == {"display_rated": (3, 0.333, 0.333, 0.333),
                                   "display": (3, 0.667, 0.0, 0.333)}
    assert got["switched"]["all"] == {"display_rated": (1, 0.0, 1.0, 0.0),
                                      "display": (1, 1.0, 0.0, 0.0)}
    assert not P.nws_tables(rows, want={"all": (3, 0.35, 0.333, 0.317)})["ok"]   # 0.017 off
    assert not P.nws_tables(rows, want={"all": (4, 0.333, 0.333, 0.333)})["ok"]  # n 33% off


def test_the_pinned_run_54_table_adds_up():
    regions = {k: v for k, v in P.RUN54_DISPLAY.items() if k != "all"}
    assert sum(v[0] for v in regions.values()) == P.RUN54_DISPLAY["all"][0] == 234897
    for k, v in P.RUN54_DISPLAY.items():
        assert abs(sum(v[1:]) - 1.0) <= 0.0015, k


# --------------------------------------------------------------------------- #
# 4 — run, end to end on a fake database                                       #
# --------------------------------------------------------------------------- #
class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_):
        return self

    def order(self, *_):
        return self

    def eq(self, k, v):
        return _Q([r for r in self.rows if r.get(k) == v])

    def gte(self, k, v):
        return _Q([r for r in self.rows if r.get(k) >= v])

    def range(self, a, b):
        return _Q(self.rows[a:b + 1])

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _Client:
    def __init__(self, tables):
        self.t = tables

    def table(self, name):
        return _Q(self.t[name])


def test_run_end_to_end(monkeypatch):
    spots = [{"id": 1, "name": "Kure Beach", "state": "North Carolina"},
             {"id": 2, "name": "Steamer Lane", "state": "California"},
             {"id": 3, "name": "Jacksonville Beach Pier", "state": "Florida"}]
    ids = {s["name"]: s["id"] for s in spots}
    fc = [{**r, "spot_id": ids[n], "source": "nwps"} for n, rs in _published().items() for r in rs]
    h = P._epoch("2026-10-28T13:00:00+00:00")
    buoys = [{"buoy_id": "41117", "observed_at": "2026-10-28T13:10:00+00:00", "hs": 2.7, "tp": 10.0,
              "swell_hs": 0.5, "swell_tp": 11.0}]
    client = _Client({"spots": spots, "forecasts": fc, "buoy_observations": buoys})
    rows54 = [_srf(1.0, 2.0, switched=True), _srf(2.0, 2.0)]
    monkeypatch.setattr(P, "RUN54_DISPLAY", {"all": (2, 0.5, 0.5, 0.0)})

    def compare(cl, since, until, roster, factors=None, on_rows=None):
        for s in spots:
            on_rows(s, next(x for x in roster if x["name"] == s["name"]),
                    [r for r in fc if r["spot_id"] == s["id"]], s["name"] == "Steamer Lane")
        return {"_pairs": {"srf": rows54}, "errors": {}}

    since = datetime(2026, 9, 7, tzinfo=timezone.utc)
    rep = P.run(client, ROSTER, "2026-10-28T00:00:00+00:00", since,
                datetime(2026, 10, 29, tzinfo=timezone.utc), factors=FACTORS, compare=compare)
    assert rep["gate"]["pass"] is True and rep["gate"]["switched"] == 1
    assert rep["archive_by_state"]["all"]["switched"] == 2           # Jacksonville and Kure Beach
    jx = next(r for r in rep["_records"] if r["spot"] == "Jacksonville Beach Pier")
    assert (jx["hour"], jx["buoy_tp"], jx["buoy_hs_m"]) == (h, 10.0, 2.7)
    assert rep["buoys_read"] == {"41110": 0, "41117": 1}
    monkeypatch.setattr(P, "RUN54_DISPLAY", {"all": (2, 0.6, 0.4, 0.0)})
    rep = P.run(client, ROSTER, "2026-10-28T00:00:00+00:00", since,
                datetime(2026, 10, 29, tzinfo=timezone.utc), factors=FACTORS, compare=compare)
    assert rep["gate"]["pass"] is False and rep["gate"]["run54_reproduced"] is False


def test_a_badge_that_flips_fails():
    # Steamer Lane's calibrated face put back to its raw value: calibrated -> model
    c = _counts(mutate_after=lambda a: a["Steamer Lane"][0].update(face_ft=4.27))
    assert c["badge_flips"] == 1 and c["unexpected"] == 1


# --------------------------------------------------------------------------- #
# 5 — bigger-but-shorter wind seas, and the headline paths (report only)       #
# --------------------------------------------------------------------------- #
def test_quantiles_are_nearest_rank():
    xs = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    # ranks: ceil(0.5 x 10) = 5 -> 5; ceil(9) = 9 -> 9; ceil(9.9) = 10 -> 10; 10 -> 10
    assert P.quantiles(xs, (0.5, 0.9, 0.99, 1.0)) == {"0.5": 5, "0.9": 9, "0.99": 10, "1.0": 10}
    assert P.quantiles([], (0.5,)) == {"0.5": None}


def test_headline_path():
    assert P.headline_path("nwps_height_ww3_dir", True) == "calibrated"
    assert P.headline_path("ww3", True) == "calibrated"
    assert P.headline_path("cdip_mop", False) == "mop"
    assert P.headline_path("nwps_height_ww3_dir", False) == "surf"
    assert P.headline_path("nwps", False) == "surf"
    assert P.headline_path("ww3", False) == "other"
    assert P.headline_path(None, False) == "other"


def _lopsided_archive():
    """Florida: an 8 ft / 8 s wind sea over a 0.5 ft / 14 s scrap (2.4 m over 0.15 m, x16), a
    1.5 m / 6 s wind sea over a 1.0 m / 12 s swell (x1.5), and an uncalibrated surf hour rated at
    600 s beside a 2.4 m wind sea at 500 s (x10; both periods in the long-wave limit). One
    switched hour, so the switched records are kept apart."""
    a = P.Archive({s["name"]: s for s in ROSTER}, top=2)
    jax = {"name": "Jacksonville Beach Pier", "state": "Florida"}
    rows = [
        _row(12, 4.27, 4.27, STAMP, swell_tp=14.0, swell_dp=100.0, swell_1_hs=0.15, swell_1_tp=14.0,
             swell_1_dp=100.0, wind_hs=2.4, wind_tp=8.0),
        _row(13, 4.27, 4.27, STAMP, swell_tp=12.0, swell_1_hs=1.0, swell_1_tp=12.0,
             wind_hs=1.5, wind_tp=6.0),
        _row(14, 4.27, 4.27, STAMP, swell_tp=600.0, swell_1_tp=600.0, wind_hs=2.4, wind_tp=500.0),
        _row(15, 4.27, 4.27, STAMP, swell_tp=3.0, wind_hs=2.4, wind_tp=9.0),         # switched
    ]
    a(jax, ROSTER[0], rows, False)
    a.finish()
    return a


def test_bigger_shorter_distribution_by_state():
    a = _lopsided_archive()
    t = a.bigger_shorter_table()
    f = t["Florida"]
    # the 16x and 10x hours now switch (DOMINANT) and stay in the bigger-but-shorter tally;
    # with the LONGER 3 s / 9 s hour, three records
    assert t["all"] == f and f["hours"] == 3 and len(a.records) == 3
    assert sorted(r["case"] for r in a.records) == ["dominant", "dominant", "longer"]
    # ratios 16.0, 1.5, 10.0 (2.4 / 0.24) -> sorted 1.5, 10, 16
    assert f["ratio"] == {"0.5": 10.0, "0.9": 16.0, "0.99": 16.0, "1.0": 16.0}
    assert f["ratio_bands"] == {"1-2": 1, "2-4": 0, "4-8": 0, "over 8": 2}
    # rated Hs 0.15, 1.0, 0.24 m = 0.49215, 3.281, 0.78744 ft -> sorted 0.492, 0.787, 3.281
    assert f["rated_hs_ft"] == {"0.1": 0.492, "0.5": 0.787, "0.9": 3.281}
    # every row is uncalibrated nwps_height_ww3_dir, so all three are surf hours; the 600 s / 500 s
    # one is the long-wave limit at both periods: shown / at the wind sea's period = 1.0
    assert f["surf_hours"] == 3 and f["surf_hours_ratio_4_up"] == 2
    o = dict(zip(("16", "1.5", "10"), (x for _st, _r, x in a.bs_overstate)))
    assert round(o["10"], 3) == 1.0
    # shoaled at 14 s rather than 8 s, and at 12 s rather than 6 s: the shown period overstates
    assert o["16"] > 1.0 and o["1.5"] > 1.0


def test_the_most_lopsided_examples_with_the_buoy():
    a = _lopsided_archive()
    ex = a.lopsided_examples()
    assert [round(r["wind_hs_m"] / r["rated_hs_m"], 2) for r in ex] == [16.0, 10.0]   # top 2
    h = P._epoch(ex[0]["valid_time"])
    P.join_buoys(ex, {"41117": [(h + 600, {"hs": 2.7, "tp": 8.0, "swell_tp": 13.0})]})
    e = P.example(ex[0])
    assert e == {"spot": "Jacksonville Beach Pier", "state": "Florida",
                 "valid_time": "2026-10-28T12:00:00+00:00", "source": "nwps_height_ww3_dir",
                 "path": "surf", "ratio": 16.0,
                 "rated": {"hs_ft": 0.49, "tp": 14.0, "dp": 100.0},       # 0.15 x 3.281 = 0.492
                 "wind_sea": {"hs_ft": 7.87, "tp": 8.0, "dp": 70.0},      # 2.4 x 3.281 = 7.874
                 "buoy": "41117", "buoy_hs_ft": 8.86, "buoy_dpd": 8.0,     # 2.7 x 3.281 = 8.859
                 "published_ft": 4.27}


def test_paths_report_the_headline_at_the_shown_period():
    """period_factor 'ww3': 1.0 to 6 s, 1.05 at 8, 1.1 at 10, 1.25 at 16."""
    recs = [{"path": "calibrated", "published": 2.0, "rated_tp": 3.0, "wind_tp": 16.0},
            {"path": "other", "published": 4.0, "rated_tp": 8.0, "wind_tp": 10.0},
            {"path": "surf", "surf_before": 2.95, "surf_after": 4.27, "rated_tp": 3.0, "wind_tp": 9.0},
            {"path": "other", "published": None, "rated_tp": 8.0, "wind_tp": 10.0}]
    p = P.paths(recs)
    # 2.0 x 1.25 / 1.0 = 2.5
    assert {k: p["calibrated"][k] for k in ("hours", "headline_before", "headline_if_shown_period",
                                            "changed")} == {"hours": 1, "headline_before": 2.0,
                                                            "headline_if_shown_period": 2.5, "changed": 1}
    assert p["calibrated"]["ratio"]["0.5"] == 1.25
    # 4.0 x 1.1 / 1.05 = 4.190476 -> 4.19, ratio 1.047619 -> 1.048; the hour with no face is counted
    assert (p["other"]["hours"], p["other"]["headline_if_shown_period"], p["other"]["ratio"]["1.0"]) == (2, 4.19, 1.048)
    # the surf path's after is the fix's own height: 4.27 / 2.95 = 1.447458 -> 1.447
    assert (p["surf"]["headline_before"], p["surf"]["headline_if_shown_period"], p["surf"]["ratio"]["0.5"]) == (2.95, 4.27, 1.447)
    assert p["mop"] == {"hours": 0, "depends_on_rated_period": P.PATH_DEPENDS["mop"],
                        "headline_before": None, "headline_if_shown_period": None,
                        "ratio": {"0.5": None, "0.9": None, "1.0": None}, "changed": 0}
    assert {k: v.split(":")[0] for k, v in P.PATH_DEPENDS.items()} == {
        "surf": "no", "calibrated": "yes", "mop": "no", "other": "yes"}


def _run_new_fields(monkeypatch, calibrated_switch):
    spots = [{"id": 1, "name": "Kure Beach", "state": "North Carolina"},
             {"id": 2, "name": "Steamer Lane", "state": "California"},
             {"id": 3, "name": "Jacksonville Beach Pier", "state": "Florida"}]
    ids = {s["name"]: s["id"] for s in spots}
    pubs = _published()
    # a bigger-but-shorter hour at Jacksonville: a 0.9 m wind sea at 500 s over the 0.24 m swell
    # rated at 600 s (3.75x, under 4); #283 published 3.46 (Hs 1.00 m, the long-wave limit), and
    # at the site's K nothing switches
    pubs["Jacksonville Beach Pier"].append(
        _row(14, 3.46, 4.27, STAMP + MARK, swell_tp=600.0, wind_hs=0.9, wind_tp=500.0))
    # and one at calibrated Steamer Lane (4.27 / 2.0 = 2.135 -> 2.13), whose buoy (46284) no
    # switched hour reads: the examples' buoys are fetched as well
    # (2.5x, 0.6 / 0.24: switched at no K, so its buoy is fetched and joined for the examples alone)
    pubs["Steamer Lane"].append(_row(14, 2.13, 4.27, STAMP, swell_tp=600.0, wind_hs=0.6,
                                     wind_tp=500.0, stars=4.0))
    if calibrated_switch:
        # a switched hour at calibrated Steamer Lane: 2.13 ft at 3 s (period_factor 1.0) would
        # read 2.13 x 1.3 = 2.769 -> 2.77 at the wind sea's 500 s
        pubs["Steamer Lane"].append(_row(15, 2.13, 4.27, STAMP, swell_tp=3.0, wind_hs=2.4,
                                         wind_tp=500.0, stars=4.0))
    fc = [{**r, "spot_id": ids[n], "source": "nwps"} for n, rs in pubs.items() for r in rs]
    obs = [{"buoy_id": "46284", "observed_at": "2026-10-28T14:05:00+00:00", "hs": 1.0, "tp": 12.0,
            "swell_hs": 0.9, "swell_tp": 13.0},
           {"buoy_id": "41117", "observed_at": "2026-10-28T12:05:00+00:00", "hs": 2.7, "tp": 9.0,
            "swell_hs": 1.0, "swell_tp": 11.0}]
    client = _Client({"spots": spots, "forecasts": fc, "buoy_observations": obs})
    monkeypatch.setattr(P, "RUN54_DISPLAY", {"all": (2, 0.5, 0.5, 0.0)})

    def compare(cl, since, until, roster, factors=None, on_rows=None):
        for s in spots:
            on_rows(s, next(x for x in roster if x["name"] == s["name"]),
                    [r for r in fc if r["spot_id"] == s["id"]], s["name"] == "Steamer Lane")
        return {"_pairs": {"srf": [_srf(1.0, 2.0, switched=True), _srf(2.0, 2.0)]}, "errors": {}}

    rep = P.run(client, ROSTER, "2026-10-28T00:00:00+00:00", datetime(2026, 9, 7, tzinfo=timezone.utc),
                datetime(2026, 10, 29, tzinfo=timezone.utc), factors=FACTORS, compare=compare)
    g = rep["gate"]
    assert g["pass"] is True and g["star_differences"] == g["other_field_differences"] == 0
    assert g["unswitched_mismatch"] == g["badge_flips"] == 0
    return rep


def test_run_reports_the_lopsided_hours_with_their_own_buoys_and_the_gate_is_unchanged(monkeypatch):
    rep = _run_new_fields(monkeypatch, calibrated_switch=False)
    # at the site's K 4: Jacksonville's storm hour and Kure Beach's WW3 hour; at K 3 also
    # Jacksonville's 3.75x hour (Steamer Lane's is 2.5x)
    assert rep["switched_vs_buoy"]["all"]["hours"] == 2
    assert [rep["by_k"][k]["switched"]["all"]["hours"] for k in ("3", "4", "6")] == [3, 2, 2]
    assert rep["bigger_shorter_by_state"]["Florida"]["hours"] == 1
    assert rep["bigger_shorter_by_state"]["California"]["surf_hours"] == 0     # calibrated
    # most lopsided first: Jacksonville 3.75x (0.9 / 0.24), then Steamer Lane 2.5x
    ex = rep["lopsided_examples"]
    assert [(x["spot"], x["ratio"], x["path"], x["switched"]) for x in ex] == [
        ("Jacksonville Beach Pier", 3.75, "surf", None), ("Steamer Lane", 2.5, "calibrated", None)]
    assert (ex[1]["buoy"], ex[1]["buoy_hs_ft"], ex[1]["buoy_dpd"]) == ("46284", 3.28, 12.0)  # 1.0 x 3.281
    assert "46284" in rep["buoys_read"]
    # switched, published window: Jacksonville's storm hour (surf) and Kure Beach's WW3 hour
    # (other: 3.0 ft at 3 s -> 3.0 x 1.3 / 1.0 = 3.9 at the wind sea's 500 s)
    pp = rep["paths_published"]
    assert (pp["surf"]["hours"], pp["other"]["hours"], pp["calibrated"]["hours"]) == (1, 1, 0)
    assert pp["other"]["headline_if_shown_period"] == 3.9
    assert (rep["paths_archive"]["other"]["hours"], rep["paths_archive"]["calibrated"]["hours"]) == (1, 0)


def test_run_puts_a_calibrated_spots_switched_hour_on_the_calibrated_path(monkeypatch):
    rep = _run_new_fields(monkeypatch, calibrated_switch=True)
    pp = rep["paths_published"]
    assert (pp["surf"]["hours"], pp["other"]["hours"], pp["calibrated"]["hours"]) == (1, 1, 1)
    assert pp["calibrated"]["headline_if_shown_period"] == 2.77
    assert rep["paths_archive"]["calibrated"]["hours"] == 1


def test_ratio_bands_include_their_upper_edge():
    """A wind sea exactly 2x the rated swell (1.0 m over 0.5 m, shorter) is in the 1-2 band;
    exactly 4x (2.0 over 0.5) in 2-4; exactly 8x (4.0 over 0.5) in 4-8."""
    a = P.Archive({s["name"]: s for s in ROSTER})
    rows = [_row(12 + i, 4.27, 4.27, STAMP, swell_tp=12.0, swell_1_hs=0.5, swell_1_tp=12.0,
                 wind_hs=w, wind_tp=6.0) for i, w in enumerate((1.0, 2.0, 4.0))]
    a({"name": "Jacksonville Beach Pier", "state": "Florida"}, ROSTER[0], rows, False)
    a.finish()
    assert a.bigger_shorter_table()["Florida"]["ratio_bands"] == {"1-2": 1, "2-4": 1, "4-8": 1, "over 8": 0}


def test_paths_count_only_headlines_that_change():
    # 3 s and 5 s both read period_factor 1.0: the headline would not change
    recs = [{"path": "other", "published": 4.0, "rated_tp": 3.0, "wind_tp": 5.0},
            {"path": "other", "published": 4.0, "rated_tp": 8.0, "wind_tp": 10.0}]
    assert P.paths(recs)["other"]["changed"] == 1


def test_tiles_put_a_calibrated_spots_switched_hour_on_the_calibrated_path():
    pub, after, _ = P.replay(_published(), ROSTER, FACTORS, _slug)
    _t, hours = P.tiles(pub, after, {}, frozenset({"Kure Beach"}))
    assert {h["spot"]: h["path"] for h in hours} == {"Jacksonville Beach Pier": "surf",
                                                    "Kure Beach": "calibrated"}


# --------------------------------------------------------------------------- #
# 6 — run 56: K, the window, Jacksonville and Hatteras (report only)           #
# --------------------------------------------------------------------------- #
ARCS = [{"min": 60.0, "max": 120.0, "span": 64.0}]     # pad 2: the true sector is 58..122


def test_switch_reason_in_words():
    storm = _row(13, 2.95, 4.27, STAMP, swell_tp=3.0, wind_hs=2.4, wind_tp=9.0)
    assert P.switch_reason(storm) == ("LONGER: wind sea 2.40 m over the rated 0.24 m, and 6.00 s "
                                      "longer (>= 0.5)")
    assert P.switch_reason(dict(storm, wind_wave_tp=2.0)) == ("DOMINANT: wind sea 10.0x the rated "
                                                              "swell (>= 4x), -1.00 s")
    assert P.switch_reason(dict(storm, wind_wave_hs=0.2)) == "wind sea 0.20 m is not bigger than the rated 0.24 m"
    assert P.switch_reason(dict(storm, wind_wave_hs=0.6, wind_wave_tp=3.3)) == (
        "wind sea bigger (2.5x, under 4x) but +0.30 s against the rated period (needs +0.5)")
    assert P.switch_reason(dict(storm, wind_wave_dp=None)) == "no wind sea height, period or direction"
    assert P.switch_reason(dict(storm, swell_1_tp=4.0)) == "no swell partition matches the rated period and direction"
    assert P.switch_reason(dict(storm, swell_source="nwps")) == "not rated on a WW3 swell partition (swell_source nwps)"
    # at K 3 a 3.2x shorter wind sea switches; at K 4 it does not
    r = dict(storm, wind_wave_hs=0.768, wind_wave_tp=2.0)
    assert P.switch_reason(r, 3).startswith("DOMINANT: wind sea 3.2x the rated swell (>= 3x)")
    assert P.switch_reason(r).startswith("wind sea bigger (3.2x, under 4x)")


def test_in_window_reads_the_spots_arcs():
    spot = {"swell_window_arcs": ARCS}
    assert P.in_window(90.0, spot) is True and P.in_window(58.0, spot) is True
    assert P.in_window(57.0, spot) is False and P.in_window(200.0, spot) is False
    assert P.in_window(90.0, {}) is None and P.in_window(None, spot) is None


def test_the_jacksonville_hour():
    spot = dict(ROSTER[0], swell_window_arcs=ARCS)
    # rated 0.24 m at 600 s (dp 80), a 1.0 m wind sea at 500 s from 70: 4.17x, DOMINANT
    r = _row(20, 4.27, 4.27, STAMP + MARK, swell_tp=600.0, wind_hs=1.0, wind_tp=500.0)
    h = P.jacksonville_hour(r, spot, False)
    assert (h["rated"], h["rated_tp"], h["shown"], h["shown_tp"], h["shown_dp"], h["case"]) == (
        "swell_1", 600.0, "wind_sea", 500.0, 70.0, "dominant")
    assert h["trains"] == {"swell_1": {"hs_ft": 0.79, "tp": 600.0, "dp": 80.0},     # 0.24 x 3.281
                           "wind_wave": {"hs_ft": 3.28, "tp": 500.0, "dp": 70.0}}   # 1.0 x 3.281
    # Hs 4.27 / 4.2653 -> 1.00 m: 3.46 ft at either long period; the row's own face_ft is 4.27
    assert (h["surf_at_rated"], h["surf_at_shown"], h["published_ft"]) == (3.46, 3.46, 4.27)
    assert h["why"].startswith("DOMINANT: wind sea 4.2x") and h["wind_in_window"] is True
    assert P.jacksonville_hour(r, spot, True)["surf_at_rated"] is None          # calibrated: no surf


def test_the_hatteras_trace():
    """Rodanthe-shaped: orientation 90, no optimal direction, so the alignment axis is 90. NWPS Hs
    0.15 m; the rated 0.06 m scrap at 12.97 s from 180 (90 off: cos^2(45) = 0.5); the wind sea
    3.55 m at 10.2 s from 270 (180 off: cos^2(90) = 0, floored at 0.25). period_factor('ww3') at
    12.97 s = 1.15 + (0.97 / 2) x 0.05 = 1.17425, so the face is 0.15 x 1.17425 x 3.281 = 0.5779
    -> 0.58 ft."""
    spot = {"name": "Rodanthe", "orientation_deg": 90.0, "swell_window_arcs": ARCS,
            "break_type": "beach", "offshore_wind_deg": 270.0}
    r = _row(17, 0.58, 0.58, STAMP, swell_tp=12.97, swell_dp=180.0, swell_1_hs=0.06,
             swell_1_tp=12.97, swell_1_dp=180.0, wind_hs=3.55, wind_tp=10.2, wind_wave_dp=270.0,
             hs=0.15, dir_gain=0.5, effective_size_ft=0.29, stars=1.0, wind_speed=10.0,
             wind_dir=60.0, wind_mult=0.7, chop_mult=0.4, valid_time="2026-09-23T17:00:00+00:00")
    h = P.hatteras_hour(r, spot, None, 1.59)
    assert (h["nwps_hs_m"], h["nwps_hs_ft"], h["period_factor"], h["face_from_hs"]) == (0.15, 0.49, 1.17425, 0.58)
    assert (h["axis_deg"], h["rated_offset_deg"], h["alignment_rated"], h["alignment_wind_sea"]) == (90.0, 90.0, 0.5, 0.25)
    assert (h["alignment_stored"], h["published_ft"], h["rated"], h["shown"]) == (0.5, 0.58, "swell_1", "wind_sea")
    assert (h["rated_in_window"], h["wind_in_window"]) == (False, False)
    assert h["why"].startswith("DOMINANT: wind sea 59.2x")                  # 3.55 / 0.06
    assert "star_cap" in h or "rules_error" in h
    # with an optimal direction of 180 the axis is 180: the scrap is on it (1.0), and the wind sea
    # from 270 is 90 off (cos^2(45) = 0.5)
    o = P.hatteras_hour(r, dict(spot, optimal_swell_dir=180.0), None, 1.59)
    assert (o["axis_deg"], o["rated_offset_deg"], o["alignment_rated"], o["alignment_wind_sea"]) == (180.0, 0.0, 1.0, 0.5)


def test_by_k_counts_cases_window_and_examples():
    def rec(state, k3, k4, k6, wind_in, hs, hour, spot="S"):
        return {"spot": spot, "state": state, "valid_time": f"t{hour}", "hour": hour, "source": "ww3",
                "path": "other", "cases": {"3.0": k3, "4.0": k4, "6.0": k6}, "bigger_shorter": k3 == "dominant",
                "rated_hs_m": 0.1, "rated_tp": 12.0, "rated_dp": 300.0, "wind_hs_m": hs, "wind_tp": 8.0,
                "wind_dp": 60.0, "rated_in_window": True, "wind_in_window": wind_in, "buoy": None,
                "buoy_hs_m": None, "buoy_tp": None, "buoy_swell_tp": None, "hs_m": 1.0,
                "published": 3.0, "surf": False, "surf_before": None, "surf_after": None}
    rs = [rec("Hawaii", "dominant", "dominant", None, False, 0.5, 1),
          rec("Hawaii", "dominant", None, None, False, 0.35, 2),
          rec("Hawaii", "longer", "longer", "longer", True, 0.2, 3),
          rec("California", "dominant", "dominant", "dominant", None, 0.9, 4)]
    b = P.by_k(rs)
    assert b["3"]["cases"] == {"California": {"dominant": 1}, "Hawaii": {"dominant": 2, "longer": 1},
                               "all": {"dominant": 3, "longer": 1}}
    assert b["4"]["window"]["Hawaii"] == {"switched": 2, "wind_outside": 1, "no_window": 0, "rated_outside": 0}
    assert b["6"]["window"]["all"] == {"switched": 2, "wind_outside": 0, "no_window": 1, "rated_outside": 0}
    # outside-window examples, biggest wind sea first: 0.5 m (hour 1) before 0.35 m (hour 2)
    assert [x["valid_time"] for x in b["3"]["window_examples"]["Hawaii"]] == ["t1", "t2"]
    assert b["3"]["window_examples"]["California"] == []
    assert b["4"]["switched"]["all"]["hours"] == 3
    # 3.0 ft at 12 s (period_factor 1.15) would read 3.0 x 1.05 / 1.15 = 2.739 at 8 s
    assert b["6"]["paths"]["other"]["headline_if_shown_period"] == 2.74
    assert b["3"]["paths_bigger_shorter"]["other"]["hours"] == 3


def test_the_archive_keeps_jacksonville_and_hatteras_and_counts_the_alignment():
    a = P.Archive({s["name"]: s for s in ROSTER}, factors=FACTORS, median_factor=1.59)
    jax = {"name": "Jacksonville Beach Pier", "state": "Florida"}
    rows = [_row(12, 4.27, 4.27, STAMP, valid_time="2026-10-25T23:00:00+00:00"),
            _row(12, 4.27, 4.27, STAMP, valid_time="2026-10-26T00:00:00+00:00"),
            _row(12, 4.27, 4.27, STAMP, valid_time="2026-10-28T23:00:00+00:00"),
            _row(12, 4.27, 4.27, STAMP, valid_time="2026-10-29T00:00:00+00:00")]
    a(jax, ROSTER[0], rows, False)
    hat = {"name": "Rodanthe", "state": "North Carolina"}
    spot = {"name": "Rodanthe", "orientation_deg": 90.0, "nearest_buoy_id": "44095"}
    hrows = [_row(17, 0.58, 0.58, STAMP, swell_tp=12.97, swell_1_tp=12.97, wind_hs=3.55, wind_tp=10.2,
                  hs=0.15, dir_gain=0.3, valid_time=f"2026-09-23T{h:02d}:00:00+00:00") for h in (16, 17, 19, 20)]
    hrows.append(dict(hrows[0], valid_time="2026-09-24T17:00:00+00:00", dir_gain=0.6))
    # 3.2x (0.768 / 0.24) and shorter: switched at K 3 only; and NOT over 4x, so not counted
    hrows.append(dict(hrows[0], valid_time="2026-09-25T17:00:00+00:00", wind_wave_hs=0.768,
                      dir_gain=0.1))
    a(hat, spot, hrows, False)
    a.finish()
    assert [h["valid_time"][:13] for h in a.jacksonville] == ["2026-10-26T00", "2026-10-28T23"]
    assert [h["valid_time"][:13] for h in a.hatteras] == ["2026-09-23T17", "2026-09-23T19"]
    # every Rodanthe hour is an NWPS-override hour with the wind sea 14.8x (3.55 / 0.24); four of
    # the five have dir_gain 0.3, under 0.5
    c = a.table()["North Carolina"]
    assert (c["nwps_ratio_over_4"], c["nwps_ratio_over_4_alignment_under_half"]) == (5, 4)
    assert (c["switched_k3"], c["switched_k4"], c["switched_k6"]) == (6, 5, 5)    # 14.8x: all K
    # the archive keeps the K-3-only hour; the site's rule (K 4) leaves it out
    assert len(a.records) == 6 and len(a.switched()) == 5
    assert [r["valid_time"][:10] for r in a.records if not r["cases"]["4.0"]] == ["2026-09-25"]


def test_run_lists_jacksonvilles_published_hours_after_the_archive(monkeypatch):
    rep = _run_new_fields(monkeypatch, calibrated_switch=False)
    jx = [h for h in rep["jacksonville"]]
    # the archive (the fake compare passes every row) already holds the 28 Oct hours: none is
    # taken twice
    assert [h["valid_time"][:13] for h in jx] == ["2026-10-28T12", "2026-10-28T13", "2026-10-28T14"]
    assert not any(h.get("from_published_window") for h in jx)
    assert [h["shown"] for h in jx] == ["swell", "wind_sea", "swell"]
    # buoy 41117 at 12:05 joins the 12:00 hour: 2.7 m, 9 s
    assert (jx[0]["buoy_hs_m"], jx[0]["buoy_tp"], jx[1]["buoy_hs_m"]) == (2.7, 9.0, None)


def test_run_takes_jacksonvilles_hours_past_the_archive_from_the_published_window(monkeypatch):
    """The archive ends at the run's hour; Jacksonville's later 26-28 Oct hours come from the
    published window, marked as such, and are not taken twice."""
    spots = [{"id": 3, "name": "Jacksonville Beach Pier", "state": "Florida"}]
    pubs = _published()["Jacksonville Beach Pier"]
    fc = [{**r, "spot_id": 3, "source": "nwps"} for r in pubs]
    # the archive's only hour (12:00) switches at no K, so buoy 41117 is fetched for Jacksonville's
    # listing alone
    obs = [{"buoy_id": "41117", "observed_at": "2026-10-28T12:05:00+00:00", "hs": 2.7, "tp": 9.0,
            "swell_hs": 1.0, "swell_tp": 11.0}]
    client = _Client({"spots": spots, "forecasts": fc, "buoy_observations": obs})
    monkeypatch.setattr(P, "RUN54_DISPLAY", {"all": (2, 0.5, 0.5, 0.0)})

    def compare(cl, since, until, roster, factors=None, on_rows=None):
        # the archive holds only the 12:00 hour
        on_rows(spots[0], ROSTER[0], [fc[0]], False)
        return {"_pairs": {"srf": [_srf(1.0, 2.0, switched=True), _srf(2.0, 2.0)]}, "errors": {}}

    rep = P.run(client, ROSTER, "2026-10-28T00:00:00+00:00", datetime(2026, 9, 7, tzinfo=timezone.utc),
                datetime(2026, 10, 29, tzinfo=timezone.utc), factors=FACTORS, compare=compare)
    jx = rep["jacksonville"]
    assert [(h["valid_time"][:13], bool(h.get("from_published_window"))) for h in jx] == [
        ("2026-10-28T12", False), ("2026-10-28T13", True)]
    assert rep["_records"] == [] and (jx[0]["buoy_hs_m"], jx[0]["buoy_tp"]) == (2.7, 9.0)
