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
    assert P.classify_hour(storm) == {"unmatched": False, "big_wind_sea": True, "switched": True,
                                      "bigger_shorter": False, "rated_hs": 0.24}
    # 0.4 m wind sea: bigger than 0.24 and longer, but not 2x (0.48)
    assert P.classify_hour(dict(storm, wind_wave_hs=0.4))["big_wind_sea"] is False
    assert P.classify_hour(dict(storm, wind_wave_hs=0.4))["switched"] is True
    # bigger but shorter: the plain rule would lower the period
    trestles = dict(storm, swell_tp=15.1, swell_1_tp=15.1, wind_wave_tp=5.2)
    assert P.classify_hour(trestles) == {"unmatched": False, "big_wind_sea": False,
                                         "switched": False, "bigger_shorter": True, "rated_hs": 0.24}
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
    assert t["Florida"] == {"hours": 4, "ww3_identity": 3, "unmatched": 1, "big_wind_sea": 1,
                            "switched": 1, "bigger_shorter": 0}
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
