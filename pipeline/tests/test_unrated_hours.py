"""An hour the rater cannot rate gets empty stars (None), never 0.0.

0.0 is a rating: the site shows it as FLAT. rate_spot used to write 0.0 on two paths where the
hour says nothing about the surf:

  1. NO SURF HEIGHT. No WW3 partition kept any gain and the NWPS entry has no height (the sample
     cell blank in the cycle's file for longer than series_hour's one-hour fill, as at Long Beach
     Township and Sea Bright on 26 Nov 06-08Z in the 2026-11-24 12Z phi cycle).
  2. NOTHING TO RATE DIRECTION AGAINST. No swell window, no optimal direction and no orientation:
     directional_gain returns 0.0 for every direction.

Both now write None, which the site shows as "No data" (#357). A real flat hour still rates 0.0,
and a spot with only arcs or only an optimal direction is still rated. Every expected value is
written by hand.
"""
from __future__ import annotations

from collections import Counter

from pipeline import daily_report as D
from pipeline import interpret as I

T = "2026-11-26T06:00:00Z"


def _spot(**over):
    s = {"name": "Test Beach", "lat": 39.6, "lng": -74.2, "orientation_deg": 130.0,
         "offshore_wind_deg": 310.0, "optimal_swell_dir": 130.0,
         "swell_window_arcs": [{"min": 60, "max": 200, "span": 144}],
         "break_type": "beach", "bottom": "sand"}
    s.update(over)
    return s


def _hour(**over):
    e = {"valid_time": T, "hs": 1.5, "tp": 10.0, "dp": 130.0, "swell_hs": 1.4,
         "wind_speed": 2.0, "wind_dir": 310.0}
    e.update(over)
    return e


def _rate(spot, entry):
    out = I.rate_spot(spot, [entry], None)
    assert len(out) == 1, out
    return out[0]


# --------------------------------------------------------------------------- #
# The two paths                                                                #
# --------------------------------------------------------------------------- #
def test_an_hour_with_no_surf_height_is_unrated_not_flat():
    # The blank-cell hour: no Hs, no period, no direction, and no WW3 series to fall back on.
    r = _rate(_spot(), _hour(hs=None, tp=None, dp=None, swell_hs=None))
    assert r["face_ft"] is None, r
    assert r["stars"] is None, r


def test_an_hour_with_a_period_but_no_height_is_unrated():
    r = _rate(_spot(), _hour(hs=None, swell_hs=None))
    assert r["face_ft"] is None and r["stars"] is None, r


def test_a_spot_with_nothing_to_rate_direction_against_is_unrated():
    spot = _spot(orientation_deg=None, optimal_swell_dir=None, swell_window_arcs=[])
    r = _rate(spot, _hour())
    assert r["face_ft"] is not None and r["face_ft"] > 0, r   # the height is still known
    assert r["dir_gain"] == 0.0, r
    assert r["stars"] is None, r


def test_the_same_hours_are_rated_once_there_is_something_to_rate_against():
    full = _rate(_spot(), _hour())
    assert isinstance(full["stars"], float) and full["stars"] > 0, full
    # orientation alone, optimal alone or arcs alone are each enough
    for over in ({"optimal_swell_dir": None, "swell_window_arcs": []},
                 {"orientation_deg": None, "swell_window_arcs": []},
                 {"orientation_deg": None, "optimal_swell_dir": None}):
        r = _rate(_spot(**over), _hour())
        assert r["stars"] is not None, (over, r)


def test_a_real_flat_hour_still_rates_zero():
    # 0.1 m at 5 s: a known height too small to surf. That is FLAT, 0.0, not unknown.
    r = _rate(_spot(), _hour(hs=0.1, swell_hs=0.1, tp=5.0))
    assert r["face_ft"] is not None, r
    assert r["stars"] == 0.0, r


# --------------------------------------------------------------------------- #
# Readers in interpret: the run summary                                         #
# --------------------------------------------------------------------------- #
def _ratings():
    return {"A": [{"valid_time": T, "stars": 2.5}, {"valid_time": T, "stars": None}],
            "B": [{"valid_time": T, "stars": 0.0}, {"valid_time": T, "stars": None}],
            "C": [{"valid_time": T, "stars": None}]}


def test_the_histogram_counts_unrated_hours_apart_from_flat_ones():
    assert I._star_histogram(_ratings()) == Counter({None: 3, 2.5: 1, 0.0: 1})


def test_unrated_hours_are_not_zero_star_hours():
    spots = [{"name": "A", "orientation_deg": 130.0, "swell_window_arcs": [{"min": 1, "max": 2}]},
             {"name": "B", "orientation_deg": 130.0, "swell_window_arcs": [{"min": 1, "max": 2}]},
             {"name": "C"}]
    # B's 0.0 is the one flat hour; C's unrated hour is not counted as null-orientation
    assert I._zero_star_breakdown(_ratings(), spots) == (0, 0, 1)


def test_the_summary_prints_with_unrated_hours(capsys):
    spots = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    I._print_summary(_ratings(), spots, sample_name="C")
    out = capsys.readouterr().out
    assert "unrated (no height or no direction reference):      3" in out, out
    assert "0.0 stars:      1" in out and "2.5 stars:      1" in out, out


def test_the_log_formatter():
    assert I._fmt_stars(None) == "—" and I._fmt_stars(0.0) == "0.0" and I._fmt_stars(3.25) == "3.2"


# --------------------------------------------------------------------------- #
# Reader: the daily report's top spots                                          #
# --------------------------------------------------------------------------- #
class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _FakeAnthropic:
    def __init__(self):
        self.sent, self.messages = [], self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return type("Msg", (), {"content": [_Block("A report.")]})()


def _fc(stars, face):
    row = {"stars": stars, "face_ft": face, "face_ft_raw": face, "swell_source": "nwps_height_ww3_dir",
           "swell_tp": 12.0, "swell_dp": 290.0, "wind_speed": 3.0, "wind_dir": 90.0}
    return {"latest": row, "plus24": dict(row)}


def _ca(i, name):
    return {"id": i, "slug": name.lower().replace(" ", "-"), "name": name, "state": "California",
            "lat": 38.9, "lng": -123.7, "offshore_wind_deg": 90.0}


def test_an_unrated_spot_is_never_a_top_spot():
    norcal = next(r for r in D.REGIONS if r.key == "norcal")
    spots = [_ca(1, "Rated Point"), _ca(2, "Flat Cove"), _ca(3, "Unrated Reef")]
    fcs = {1: _fc(2.0, 3.0), 2: _fc(0.0, 0.4), 3: _fc(None, None)}
    row = D.build_region_report(norcal, spots, fcs, _FakeAnthropic())
    assert [s["slug"] for s in row["top_spots"]] == ["rated-point", "flat-cove"], row["top_spots"]


def test_a_region_with_no_rated_spot_has_no_report():
    norcal = next(r for r in D.REGIONS if r.key == "norcal")
    fake = _FakeAnthropic()
    assert D.build_region_report(norcal, [_ca(3, "Unrated Reef")], {3: _fc(None, None)}, fake) is None
    assert fake.sent == []   # no request made for a report with nothing to rank
