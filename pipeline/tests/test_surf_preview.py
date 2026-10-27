"""surf_preview, the merge gate for the surf height: the replay's checks, the height and homepage
tables, and the reproduction of run 53 against the NWS ranges.

HAND-WORKED VALUES. The replayed heights are the Green's-law cases of test_surf_height.py: at
swell_tp 500 s (period_factor 'ww3' 1.3) a published face of 4.27 ft is Hs 4.27 / 4.2653 = 1.0011
-> 1.00 m and shows 0.70 x 1.508057 x 3.281 = 3.46 ft; 8.53 ft is Hs 2.00 and shows 6.03 ft; 34.12
ft is Hs 8.00 (already breaking at 10 m) and shows 0.70 x 8.0 x 3.281 = 18.37 ft. Every NWS
side is worked from face_compare.classify's rule, inside when lo - 0.5 <= face < hi + 0.5.

Run: python -m pytest pipeline/tests/test_surf_preview.py
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from pipeline import surf_preview as P

STAMP = "2:2026-10-06:abcd1234"
MARK = "+surf:k0.70:d10:g0.78"
FACTORS = {"steamer-lane": {"factor": 2.0, "measured_on": "2026-10-06"}}
ROSTER = [{"name": "Rockaway", "nwps_wfo": "okx"}, {"name": "Steamer Lane", "nwps_wfo": "mtr"},
          {"name": "Kure Beach", "nwps_wfo": "ilm"}]


def _slug(name):
    return name.lower().replace(" ", "-")


def _row(t, face, raw, source, stars, tp=500.0):
    return {"valid_time": f"2026-10-28T{t:02d}:00:00+00:00", "face_ft": face, "face_ft_raw": raw,
            "face_correction_version": STAMP, "swell_source": source, "swell_tp": tp,
            "stars": stars, "effective_size_ft": 4.0, "hs": 1.2}


def _published():
    return {
        "Rockaway": [_row(12, 4.27, 4.27, "nwps_height_ww3_dir", 3.5),
                     _row(13, 8.53, 8.53, "nwps", 3.0),
                     _row(14, 34.12, 34.12, "nwps", 2.0),
                     _row(15, 5.0, 5.0, "ww3", 2.5, tp=12.0)],
        "Steamer Lane": [_row(12, 2.13, 4.27, "nwps", 4.0)],
        "Kure Beach": [_row(12, 3.0, 3.0, "ww3", 4.0, tp=9.0)],
    }


# --------------------------------------------------------------------------- #
# 1 — the replay and its gate                                                  #
# --------------------------------------------------------------------------- #
def test_the_replay_passes_its_own_gate():
    before, after, stats = P.replay(_published(), ROSTER, FACTORS, _slug)
    assert [r["face_ft"] for r in after["Rockaway"]] == [3.46, 6.03, 18.37, 5.0]
    assert before == _published()                    # the published rows are never written
    g = P.gate_rows(before, after, {"Steamer Lane"})
    assert g["counts"] == {"hours": 6, "rewritten": 3, "star_differences": 0,
                           "other_field_differences": 0, "unexpected": 0,
                           "uncalibrated_but_divided": 0, "became_calibrated": 0,
                           "lost_calibrated": 0}
    # Rockaway's three NWPS hours and its WW3 hour, and Kure Beach's: model before and after.
    assert g["badges"] == {"before": {"model": 5, "calibrated": 1},
                           "after": {"model": 5, "calibrated": 1}}
    assert g["transitions"] == {"calibrated -> calibrated": 1, "model -> model": 5}
    assert stats["hours_rewritten"] == 3


def _gate_after(mutate):
    before = _published()
    after = copy.deepcopy(before)
    _, good, _ = P.replay(before, ROSTER, FACTORS, _slug)
    after.update(copy.deepcopy(good))
    mutate(after)
    return P.gate_rows(before, after, {"Steamer Lane"})["counts"]


def test_a_star_that_moves_fails_the_gate():
    c = _gate_after(lambda a: a["Rockaway"][0].update(stars=3.0))
    assert c["star_differences"] == 1 and c["other_field_differences"] == 1


def test_any_other_field_that_moves_fails_the_gate():
    c = _gate_after(lambda a: a["Rockaway"][3].update(effective_size_ft=3.9))
    assert c["other_field_differences"] == 1 and c["unexpected"] == 1 and c["star_differences"] == 0


def test_a_calibrated_spot_written_fails_the_gate():
    def m(a):
        a["Steamer Lane"][0].update(face_ft=3.46, face_correction_version=STAMP + MARK)
    c = _gate_after(m)
    assert c["unexpected"] == 1 and c["lost_calibrated"] == 1


def test_an_expected_hour_left_unmarked_fails_the_gate_and_reads_calibrated():
    # face moved but no mark: the site's rule sees two different faces and calls it calibrated.
    c = _gate_after(lambda a: a["Rockaway"][1].update(face_correction_version=STAMP))
    assert c["unexpected"] == 1 and c["became_calibrated"] == 1


def test_a_ww3_hour_written_fails_the_gate():
    c = _gate_after(lambda a: a["Rockaway"][3].update(face_ft=3.5, face_correction_version=STAMP + MARK))
    assert c["unexpected"] == 1


def test_an_uncalibrated_face_that_was_divided_is_counted():
    pub = _published()
    pub["Rockaway"][0]["face_ft_raw"] = 5.0          # published face 4.27 differs from raw 5.0
    before, after, _ = P.replay(pub, ROSTER, FACTORS, _slug)
    assert P.gate_rows(before, after, {"Steamer Lane"})["counts"]["uncalibrated_but_divided"] == 1


def test_expected_surf_needs_an_override_hour_a_face_and_a_period():
    r = _row(12, 4.27, 4.27, "nwps", 3.0)
    assert P.expected_surf(r, False) and not P.expected_surf(r, True)
    for bad in ({"swell_source": "ww3"}, {"swell_source": "cdip_mop"}, {"face_ft": None},
                {"swell_tp": None}, {"swell_tp": 0}):
        assert not P.expected_surf({**r, **bad}, False), bad


# --------------------------------------------------------------------------- #
# 2 — heights and the homepage                                                 #
# --------------------------------------------------------------------------- #
def test_heights_before_and_after():
    before, after, _ = P.replay(_published(), ROSTER, FACTORS, _slug)
    h = P.heights(before, after, {"Rockaway": "Mid-Atlantic"})
    # before 4.27, 8.53, 34.12 -> after 3.46, 6.03, 18.37
    # ratios 0.810304, 0.706917, 0.538394; means 46.92 / 3 = 15.64 and 27.86 / 3 = 9.286667
    want = {"n": 3, "median_before": 8.53, "median_after": 6.03, "mean_before": 15.64,
            "mean_after": 9.29, "median_ratio": 0.707, "lower": 1.0, "higher": 0.0}
    assert h["by_region"] == {"all": want, "Mid-Atlantic": want}
    assert h["by_spot"] == {"Rockaway": want}


def test_the_homepage_keeps_its_order_and_shows_both_heights():
    before, after, _ = P.replay(_published(), ROSTER, FACTORS, _slug)
    # Soonest hours: Rockaway 3.5 (id 3), Steamer Lane 4.0 (id 2), Kure Beach 4.0 (id 1).
    got = P.homepage(before, after, {"Kure Beach": 1, "Steamer Lane": 2, "Rockaway": 3})
    assert [(h["spot"], h["stars_before"], h["stars_after"], h["face_before"], h["face_after"],
             h["basis_before"], h["basis_after"]) for h in got] == [
        ("Kure Beach", 4.0, 4.0, 3.0, 3.0, "model", "model"),
        ("Steamer Lane", 4.0, 4.0, 2.13, 2.13, "calibrated", "calibrated"),
        ("Rockaway", 3.5, 3.5, 4.27, 3.46, "model", "model")]
    assert len(P.homepage(before, after, {}, n=2)) == 2


# --------------------------------------------------------------------------- #
# 3 — against the NWS                                                          #
# --------------------------------------------------------------------------- #
T0 = int(datetime(2026, 10, 1, 15, tzinfo=timezone.utc).timestamp())
LATE = int(datetime(2026, 10, 27, 16, tzinfo=timezone.utc).timestamp())


def _srf(typical, region="R", calibrated=False, office="ILM", group="headline", hour=T0,
         published=None, display=None):
    return {"region": region, "calibrated": calibrated, "office": office, "group": group,
            "hour": hour, "typical": typical, "lo": 2.0, "hi": 3.0,
            "published": published, "display": display}


def test_reproduce_counts_only_uncalibrated_mainland_headline_hours_before_run_53s_end():
    # range 2-3 ft: inside from 1.5 to under 3.5. 0.70 x: 3.0 -> 2.1 in, 1.0 -> 0.7 below,
    # 6.0 -> 4.2 above, 4.0 -> 2.8 in.
    rows = [_srf(3.0), _srf(1.0), _srf(6.0), _srf(4.0, region="S"),
            _srf(1.0, calibrated=True), _srf(1.0, office="HFO"), _srf(1.0, group="base"),
            _srf(1.0, hour=LATE), _srf(None)]
    want = {"all": (4, 0.5, 0.25, 0.25), "R": (3, 0.333, 0.333, 0.333), "S": (1, 1.0, 0.0, 0.0)}
    got = P.reproduce(rows, want_by_region=want)
    assert got["ok"] and {r: e["got"] for r, e in got["by_region"].items()} == want


def test_reproduce_fails_on_a_share_or_a_count_out_of_tolerance():
    rows = [_srf(3.0), _srf(1.0), _srf(6.0), _srf(4.0)]
    assert P.reproduce(rows, want_by_region={"all": (4, 0.5, 0.25, 0.25)})["ok"]
    assert P.reproduce(rows, want_by_region={"all": (4, 0.5, 0.25, 0.25), "R": (4, 0.5, 0.25, 0.25)})["ok"]
    assert not P.reproduce(rows, want_by_region={"all": (4, 0.52, 0.23, 0.25)})["ok"]   # 0.02 off
    assert P.reproduce(rows, want_by_region={"all": (4, 0.509, 0.25, 0.241)})["ok"]     # 0.009
    assert not P.reproduce(rows, want_by_region={"all": (5, 0.5, 0.25, 0.25)})["ok"]     # n 20% off
    assert not P.reproduce(rows, want_by_region={"Q": (0, 0.5, 0.25, 0.25)})["ok"]      # no hours


def test_the_pinned_run_53_table_adds_up():
    """Copied by hand: the regions' hours sum to the overall count, and each row's shares to 1."""
    regions = {k: v for k, v in P.RUN53_K070.items() if k != "all"}
    assert sum(v[0] for v in regions.values()) == P.RUN53_K070["all"][0] == 171534
    for k, v in P.RUN53_K070.items():
        assert abs(sum(v[1:]) - 1.0) <= 0.0015, k


def test_display_against_published_by_region():
    rows = [_srf(None, published=4.0, display=2.8),                 # above -> inside
            _srf(None, published=2.0, display=2.0),                 # inside, unchanged
            _srf(None, region="S", calibrated=True, published=1.0, display=1.0),   # below
            _srf(None, office="HFO", published=4.0, display=2.8)]
    got = P.display_vs_published(rows)
    assert got["all_spots"]["all"] == {"published": (3, 0.333, 0.333, 0.333),
                                       "display": (3, 0.667, 0.333, 0.0)}
    assert got["all_spots"]["S"] == {"published": (1, 0.0, 1.0, 0.0), "display": (1, 0.0, 1.0, 0.0)}
    assert got["uncalibrated"]["all"] == {"published": (2, 0.5, 0.0, 0.5), "display": (2, 1.0, 0.0, 0.0)}
    assert got["hawaii"]["all"] == {"published": (1, 0.0, 0.0, 1.0), "display": (1, 1.0, 0.0, 0.0)}


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
    def __init__(self, spots, forecasts):
        self.t = {"spots": spots, "forecasts": forecasts}

    def table(self, name):
        return _Q(self.t[name])


def test_run_passes_on_a_clean_replay_and_a_reproduced_run_53(monkeypatch):
    spots = [{"id": 1, "name": "Kure Beach"}, {"id": 2, "name": "Steamer Lane"},
             {"id": 3, "name": "Rockaway"}]
    ids = {"Kure Beach": 1, "Steamer Lane": 2, "Rockaway": 3}
    fc = [{**r, "spot_id": ids[n], "source": "nwps"} for n, rs in _published().items() for r in rs]
    rows = [_srf(3.0), _srf(1.0), _srf(6.0), _srf(4.0)]
    monkeypatch.setattr(P, "RUN53_K070", {"all": (4, 0.5, 0.25, 0.25)})
    seen = {}

    def compare(client, since, until, roster, factors=None):
        seen["factors"] = factors
        return {"_pairs": {"srf": rows}, "errors": {}}

    since = datetime(2026, 9, 7, tzinfo=timezone.utc)
    rep = P.run(_Client(spots, fc), ROSTER, "2026-10-28T00:00:00+00:00", since,
                datetime(2026, 10, 28, tzinfo=timezone.utc), factors=FACTORS, compare=compare)
    assert rep["gate"]["pass"] is True and rep["gate"]["rewritten"] == 3
    assert seen["factors"] is FACTORS
    assert [h["spot"] for h in rep["homepage"]] == ["Kure Beach", "Steamer Lane", "Rockaway"]
    assert len(rep["_hours"]) == 3
    # A reproduction off by more than the tolerance fails the whole gate.
    monkeypatch.setattr(P, "RUN53_K070", {"all": (4, 0.6, 0.15, 0.25)})
    rep = P.run(_Client(spots, fc), ROSTER, "2026-10-28T00:00:00+00:00", since,
                datetime(2026, 10, 28, tzinfo=timezone.utc), factors=FACTORS, compare=compare)
    assert rep["gate"]["pass"] is False and rep["gate"]["reproduction_ok"] is False
