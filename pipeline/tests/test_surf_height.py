"""The surf height at uncalibrated spots (pipeline/forecast/surf_height.py): what it writes, where,
and what it leaves alone.

HAND-WORKED VALUES. Every expected height is worked out here, never taken from the code under
test. They use the long-period limit, where linear shoaling is Green's law, H ∝ h^(-1/4): a wave
of height Hs at d0 = 10 m breaks where Hs (10 / h)^(1/4) = 0.78 h, so
    h = (10^0.25 Hs / 0.78)^0.8   and   Hb = 0.78 h.
For Hs = 1.0: 10^0.25 = 1.7782794, / 0.78 = 2.2798454, ^0.8 = exp(0.8 x 0.824107) = 1.933406 m,
and Hb = 0.78 x 1.933406 = 1.508057 m (test_face_compare pins the solver at 1.508059 for this
case). Hb scales as Hs^0.8, so Hs = 2.0 gives 1.508057 x 2^0.8 = 1.508057 x 1.741101 = 2.625680.
A swell_tp of 500 s puts the hour there, and period_factor('ww3') is 1.3 past the table's last
point (99 s), so the override's face for Hs is Hs x 1.3 x 3.281 = Hs x 4.2653 ft.

Run: python -m pytest pipeline/tests/test_surf_height.py
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from pipeline.forecast import surf_height as S
from pipeline.forecast.face_correction import apply_face_corrections

MARK = "+surf:k0.70:d10:g0.78"
STAMP = "2:2026-10-06:abcd1234"


def _close(a, b, tol):
    return abs(a - b) <= tol


def _slug(name):
    return name.lower().replace(" ", "-")


def _hour(face, tp, source, **kw):
    """A rated hour as it leaves apply_face_corrections at an uncalibrated spot: face_ft_raw
    equal to face_ft, the run's stamp."""
    e = {"valid_time": "2026-10-27T12:00:00Z", "face_ft": face, "face_ft_raw": face,
         "face_correction_version": STAMP, "swell_tp": tp, "swell_source": source,
         "hs": 9.99, "stars": 3.5, "effective_size_ft": 4.4, "dir_gain": 0.9,
         "period_quality": 0.8, "wind_mult": 1.0, "tide_mult": 1.0}
    e.update(kw)
    return e


# --------------------------------------------------------------------------- #
# 1 — the constants and the mark                                               #
# --------------------------------------------------------------------------- #
def test_the_constants_and_the_mark_they_spell():
    assert (S.SURF_K, S.D0_M, S.GAMMA, S.M_TO_FT) == (0.70, 10.0, 0.78, 3.281)
    assert S.SURF_MARK == MARK
    assert S.NWPS_OVERRIDE_SOURCES == {"nwps", "nwps_height_ww3_dir"}


def test_is_surf_row_reads_the_mark_with_its_colon():
    assert S.is_surf_row(STAMP + MARK) and S.is_surf_row(MARK)
    assert not S.is_surf_row(STAMP) and not S.is_surf_row(STAMP + "+surf")
    assert not S.is_surf_row(None) and not S.is_surf_row("") and not S.is_surf_row(3)


# --------------------------------------------------------------------------- #
# 2 — the height                                                               #
# --------------------------------------------------------------------------- #
def test_surf_face_is_seventy_percent_of_the_breaking_height_in_feet():
    got = S.surf_face_ft([1.0, 2.0], [500.0, 500.0])
    # 0.70 x 1.508057 x 3.281 = 3.463554;  0.70 x 2.625680 x 3.281 = 6.030401
    assert _close(float(got[0]), 3.463554, 0.002), got
    assert _close(float(got[1]), 6.030401, 0.003), got


def test_a_wave_already_breaking_at_ten_metres_is_its_own_breaking_height():
    # 8.0 m >= 0.78 x 10: Hb = 8.0, so 0.70 x 8.0 x 3.281 = 18.3736
    assert _close(float(S.surf_face_ft([8.0], [500.0])[0]), 18.3736, 1e-6)


def test_typical_rounds_inputs_to_a_centimetre_and_a_tenth_of_a_second():
    a = S.typical_face_ft([1.004, 1.0, 0.996], [500.04, 500.0, 499.96])
    assert float(a[0]) == float(a[1]) == float(a[2])
    assert S.typical_face_ft([], []).size == 0


def test_sized_hs_inverts_the_override_face():
    # 3.61 / (1.1 x 3.281) = 3.61 / 3.6091 = 1.000249 at 10 s (period_factor 'ww3' 1.1)
    assert _close(S.sized_hs({"face_ft_raw": 3.61, "face_ft": 1.0, "swell_tp": 10.0}), 1.000249, 1e-6)
    # face_ft_raw first; face_ft when there is no raw
    assert _close(S.sized_hs({"face_ft": 3.61, "swell_tp": 10.0}), 1.000249, 1e-6)
    # 4.27 / 4.2653 = 1.001102 at 500 s
    assert _close(S.sized_hs({"face_ft_raw": 4.27, "swell_tp": 500.0}), 1.001102, 1e-6)
    for bad in ({"face_ft_raw": 4.27}, {"face_ft_raw": 4.27, "swell_tp": 0},
                {"face_ft_raw": 4.27, "swell_tp": -3}, {"swell_tp": 10.0},
                {"face_ft_raw": -1.0, "swell_tp": 10.0}, {"face_ft_raw": "x", "swell_tp": 10.0},
                {"face_ft_raw": float("nan"), "swell_tp": 10.0}):
        assert S.sized_hs(bad) is None, bad


# --------------------------------------------------------------------------- #
# 3 — where it writes, and what it leaves alone                                #
# --------------------------------------------------------------------------- #
def _setup():
    spots = [{"name": "Rockaway"}, {"name": "Steamer Lane"},
             {"name": "Asilomar", "swell_window_source": "cdip_mop"}, {"name": "Montauk"}]
    factors = {"steamer-lane": {"factor": 2.0, "measured_on": "2026-10-06"}}
    ratings = {
        "Rockaway": [
            _hour(4.27, 500.0, "nwps_height_ww3_dir"),   # Hs 1.00 -> 3.46
            _hour(8.53, 500.0, "nwps"),                  # 8.53 / 4.2653 = 1.99986 -> Hs 2.00 -> 6.03
            _hour(5.0, 12.0, "ww3"),                     # WW3 path: kept
            _hour(3.3, 12.0, "cdip_mop"),                # MOP-fed: kept
            _hour(2.2, 12.0, "nwps_swell"),              # fallback: kept
            _hour(2.2, 12.0, None),                      # no source: kept
            _hour(None, 500.0, "nwps", face_ft_raw=None),  # no face: kept, counted
            _hour(4.27, None, "nwps"),                   # no period: kept, counted
        ],
        "Steamer Lane": [_hour(2.13, 500.0, "nwps", face_ft_raw=4.27)],  # calibrated: kept
        # MOP tier: no factor, so uncalibrated. 34.12 / 4.2653 = 7.99943 -> 8.00 -> 18.37
        "Asilomar": [_hour(34.12, 500.0, "nwps_height_ww3_dir")],
        "Not in the roster": [_hour(4.27, 500.0, "nwps")],
        "Montauk": [_hour(5.0, 12.0, "ww3")],            # uncalibrated, no NWPS hour: not rewritten
    }
    return spots, factors, ratings


def test_it_rewrites_face_ft_on_uncalibrated_nwps_override_hours_only():
    spots, factors, ratings = _setup()
    before = copy.deepcopy(ratings)
    stats = S.apply_surf_height(ratings, spots, factors, _slug)
    r = ratings["Rockaway"]
    assert [e["face_ft"] for e in r] == [3.46, 6.03, 5.0, 3.3, 2.2, 2.2, None, 4.27]
    assert [e["face_correction_version"] for e in r] == [STAMP + MARK] * 2 + [STAMP] * 6
    assert ratings["Asilomar"][0]["face_ft"] == 18.37
    assert ratings["Asilomar"][0]["face_correction_version"] == STAMP + MARK
    assert ratings["Steamer Lane"] == before["Steamer Lane"]
    assert ratings["Not in the roster"] == before["Not in the roster"]
    assert ratings["Montauk"] == before["Montauk"]
    assert stats == {"spots_calibrated": 1, "spots_rewritten": 2, "hours_rewritten": 3,
                     "hours_wind_sea": 0, "hours_kept_by_source": 5, "hours_without_inputs": 2,
                     "factors_missing": False}


def test_nothing_but_face_ft_and_the_version_moves_and_never_the_stars():
    spots, factors, ratings = _setup()
    before = copy.deepcopy(ratings)
    S.apply_surf_height(ratings, spots, factors, _slug)
    for name, entries in ratings.items():
        for e, b in zip(entries, before[name]):
            assert set(e) == set(b), (name, e)
            moved = {k for k in e if e[k] != b[k]}
            assert moved <= {"face_ft", "face_correction_version"}, (name, moved)
            assert e["stars"] == b["stars"] and e["effective_size_ft"] == b["effective_size_ft"]
            assert e["face_ft_raw"] == b["face_ft_raw"] and e["hs"] == b["hs"]


def test_no_mark_without_a_stamp_to_append_to():
    spots, factors, ratings = _setup()
    ratings["Rockaway"][0]["face_correction_version"] = None
    S.apply_surf_height(ratings, spots, factors, _slug)
    assert ratings["Rockaway"][0]["face_correction_version"] == MARK


def test_no_factors_file_no_step():
    """With no factors every spot reads uncalibrated, Steamer Lane included: nothing is written."""
    spots, _, ratings = _setup()
    before = copy.deepcopy(ratings)
    for factors in ({}, None):
        stats = S.apply_surf_height(ratings, spots, factors, _slug)
        assert ratings == before
        assert stats["factors_missing"] is True and stats["hours_rewritten"] == 0


def test_a_failure_leaves_every_row_as_it_was(monkeypatch):
    spots, factors, ratings = _setup()
    before = copy.deepcopy(ratings)

    def boom(hs, period):
        raise RuntimeError("solver failed")

    monkeypatch.setattr(S, "surf_face_ft", boom)
    with pytest.raises(RuntimeError):
        S.apply_surf_height(ratings, spots, factors, _slug)
    assert ratings == before


def test_after_the_correction_seam_calibrated_spots_keep_their_divided_face():
    """The order interpret.main runs them in: the seam divides Steamer Lane (4.27 / 2.0 = 2.135
    -> 2.13, rounded half to even by round()), then the surf step writes Rockaway."""
    spots = [{"name": "Rockaway"}, {"name": "Steamer Lane"}]
    factors = {"steamer-lane": {"factor": 2.0, "measured_on": "2026-10-06"}}
    raw = {"valid_time": "2026-10-27T12:00:00Z", "face_ft": 4.27, "swell_tp": 500.0,
           "swell_source": "nwps", "stars": 3.5, "effective_size_ft": 4.4}
    ratings = {"Rockaway": [dict(raw)], "Steamer Lane": [dict(raw)]}
    import datetime
    apply_face_corrections(ratings, spots, factors=factors, slug_for=_slug,
                           now=datetime.date(2026, 10, 7))
    S.apply_surf_height(ratings, spots, factors, _slug)
    lane, rock = ratings["Steamer Lane"][0], ratings["Rockaway"][0]
    assert lane["face_ft"] == 2.13 and lane["face_ft_raw"] == 4.27
    assert not S.is_surf_row(lane["face_correction_version"])
    assert rock["face_ft"] == 3.46 and rock["face_ft_raw"] == 4.27
    assert S.is_surf_row(rock["face_correction_version"])
    assert rock["stars"] == lane["stars"] == 3.5


# --------------------------------------------------------------------------- #
# 4 — the wiring                                                               #
# --------------------------------------------------------------------------- #
def test_interpret_runs_it_after_the_seam_and_before_the_write():
    src = (Path(__file__).resolve().parents[1] / "interpret.py").read_text()
    seam = src.index("face_stats = apply_face_corrections(ratings, spots, factors=face_factors)")
    surf = src.index("surf_stats = apply_surf_height(ratings, spots, face_factors)")
    write = src.index("args.output.write_text(json.dumps(ratings")
    assert seam < surf < write


def test_the_comparison_tool_uses_this_solver():
    from pipeline import face_compare as F
    assert F.breaking_height is S.breaking_height and F.typical_face_ft is S.typical_face_ft
    assert (F.D0_M, F.GAMMA, F.M_TO_FT) == (S.D0_M, S.GAMMA, S.M_TO_FT)


# --------------------------------------------------------------------------- #
# 5 — the period it shoals at: the wind sea's, when it is bigger and longer    #
# --------------------------------------------------------------------------- #
def _storm_hour(wind_hs, wind_tp, **kw):
    """A nor'easter hour like Jacksonville's on 2026-10-27: the rated swell partition is a
    0.24 m, 3 s scrap (swell_2), the wind sea carries the energy. At swell_tp 3 s
    period_factor('ww3') is 1.0, so the published face 4.27 ft sizes back to Hs 4.27 / 3.281 =
    1.30143 -> 1.30 m."""
    e = _hour(4.27, 3.0, "nwps_height_ww3_dir", swell_dp=80.0,
              swell_1_hs=0.1, swell_1_tp=11.0, swell_1_dp=120.0,
              swell_2_hs=0.24, swell_2_tp=3.0, swell_2_dp=80.0,
              wind_wave_hs=wind_hs, wind_wave_tp=wind_tp, wind_wave_dp=70.0)
    e.update(kw)
    return e


def test_a_bigger_longer_wind_sea_sets_the_shoaling_period_and_marks_the_row():
    """Shoaled at the wind sea's 500 s (Green's law): Hb = 1.508057 x 1.30^0.8 = 1.508057 x
    exp(0.8 x 0.262364) = 1.508057 x 1.233540 = 1.860248 m; 0.70 x 1.860248 x 3.281 = 4.2724
    -> 4.27 ft."""
    ratings = {"Rockaway": [_storm_hour(2.4, 500.0)]}
    stats = S.apply_surf_height(ratings, [{"name": "Rockaway"}],
                                {"steamer-lane": {"factor": 2.0}}, _slug)
    e = ratings["Rockaway"][0]
    assert e["face_ft"] == 4.27
    assert e["face_correction_version"] == STAMP + MARK + ":windsea"
    assert S.is_surf_row(e["face_correction_version"])
    assert stats["hours_wind_sea"] == 1 and stats["hours_rewritten"] == 1
    assert S.surf_period(_storm_hour(2.4, 500.0)) == (500.0, True)


def test_a_wind_sea_that_is_bigger_but_shorter_or_longer_but_smaller_does_not():
    for wind_hs, wind_tp in ((2.4, 2.9), (0.2, 9.0), (0.24, 9.0), (2.4, 3.0)):
        e = _storm_hour(wind_hs, wind_tp)
        assert S.surf_period(e) == (3.0, False), (wind_hs, wind_tp)
        ratings = {"Rockaway": [e]}
        S.apply_surf_height(ratings, [{"name": "Rockaway"}], {"x": {"factor": 2.0}}, _slug)
        assert ratings["Rockaway"][0]["face_correction_version"] == STAMP + MARK
