"""Every number the methodology post takes from the code, held against the code.

WHAT THIS HOLDS TOGETHER. frontend/content/blog/methodology.md explains the star rating with
numbers the pipeline owns: the four weights, the size, period and chop curves, the swell-window
gains, the chop cap on offshore wind, the model steps and horizons, and the spot counts and
calibration window the data files record. Nothing tied the post to any of them, so either side
could change and the post would go on describing a rating that no longer exists. Each test here
reads one claim out of the post and compares it with the code, so a change to either side alone
fails CI.

A TEST FILE, NOT A PIPELINE CHANGE. It reads the post and imports the code; it changes neither.
The surface-conditions table mirrors frontend code and is held in frontend/lib/methodology.test.mts.

NOT PINNED, deliberately: the 84,774 spot-hour figures (a measurement, dated in the post), the
calibration drift figure (a measurement taken outside this repo), the 3-star shares at calibrated
and uncalibrated spots before and after the rating stopped using the calibrated height (0.4%,
9.1% and 10.3%, measured on the forecast published 4 October 2026, dated in the post), the
earlier calibration's 130 spots and the "all but two" whose calibrated height was the smaller
(item 6 quotes them as history: that file was measured 18 August to 1 September 2026 and has
been replaced, so nothing in the repo can check them), and that MOP's nowcast ends before the
current hour (a property of CDIP's feed, seen on live rows), and the replay of the forecast
published on 20 October 2026 under the wind cap and beach-break limit (8,844 of 83,446
spot-hours, rating-preview run 31, dated in the post), the replay on the calibrated height
(8,673 of 85,470 spot-hours, rating-preview run 32, dated in the post), and the replay of the
forecast published on 21 October 2026 under the 20 kt changes (2,612 of 95,107 spot-hours,
2,256 and 356 by change, rating-preview run 34, dated in the post). None is a number the
code owns. What the code does own about these — that a calibrated spot's stars read its calibrated height times the
median ratio while every other spot's read the model height, that item 6 describes a calibration older than the current file, and that MOP is
read from its nowcast — is pinned below.

NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST. Every expected number is read out of the post;
the code supplies only the actual side. Where a claim is prose, the test finds it by its wording
and fails with a message if the wording changes, rather than passing on a sentence it can't read.

Run: python -m pytest pipeline/tests/test_methodology_tables.py
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re

from pipeline import config as C
from pipeline import interpret as I

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POST = os.path.join(ROOT, "frontend", "content", "blog", "methodology.md")
SPOTS = os.path.join(ROOT, "pipeline", "spots_enriched.json")
FACE_FACTORS = os.path.join(ROOT, "pipeline", "data", "spot_face_factors.json")

_DELIMITER_CELL = re.compile(r"^:?-+:?$")
_N = r"(\d+(?:\.\d+)?)"


def _post():
    with open(POST, encoding="utf-8") as f:
        return f.read()


def _tables(markdown):
    """Every GFM pipe table in *markdown*: [header, row, row, ...], each a list of cell strings.

    Deliberately small. A table is a run of lines starting with '|' whose second line is the
    delimiter row; cells split on '|'. It does not handle escaped pipes or inline markup, and
    none of the tables it reads contains either."""
    runs, current = [], []
    for line in markdown.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            current.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif current:
            runs.append(current)
            current = []
    return [[rows[0]] + rows[2:] for rows in runs
            if len(rows) >= 2 and all(_DELIMITER_CELL.match(cell) for cell in rows[1])]


def _table(header):
    found = [table for table in _tables(_post()) if table[0] == header]
    assert len(found) == 1, (
        f"methodology.md should carry exactly one table headed {header}, found {len(found)}. "
        "If the table was renamed or split, update this test in the same change.")
    return found[0][1:]


def _says(pattern, what):
    m = re.search(pattern, _post())
    assert m, f"methodology.md no longer says {what}. Update this test in the same change as the post."
    return m


def _decimals(text):
    return len(text.split(".")[1]) if "." in text else 0


# --------------------------------------------------------------------------- #
# The weights                                                                  #
# --------------------------------------------------------------------------- #
_FACTOR_BY_WORD = {"Wind": "wind_mult", "Chop": "chop_mult", "Period": "period_quality", "Tide": "tide_mult"}


def test_the_weights_table_is_the_composite_exponents():
    rows = _table(["Quality score", "Weight"])
    assert sorted(name for name, _ in rows) == sorted(_FACTOR_BY_WORD), rows
    published = {_FACTOR_BY_WORD[name]: float(weight) for name, weight in rows}
    assert published == I.COMPOSITE_FACTOR_EXPONENTS, (
        f"post: {published}\ncode: {I.COMPOSITE_FACTOR_EXPONENTS}")
    total = float(_says(rf"blended with these weights, which add up to {_N}", "that the weights add up to 1").group(1))
    assert math.isclose(sum(I.COMPOSITE_FACTOR_EXPONENTS.values()), total, abs_tol=1e-12)


def test_the_formula_line_carries_the_same_weights():
    m = _says(rf"stars = size_score × wind\^{_N} × chop\^{_N} × period\^{_N} × tide\^{_N}", "the formula line")
    published = dict(zip(("wind_mult", "chop_mult", "period_quality", "tide_mult"), map(float, m.groups())))
    assert published == I.COMPOSITE_FACTOR_EXPONENTS, (
        f"post: {published}\ncode: {I.COMPOSITE_FACTOR_EXPONENTS}")


# --------------------------------------------------------------------------- #
# Curves: size, period, chop                                                   #
# --------------------------------------------------------------------------- #
def _curve_rows(header, unit):
    """Rows as (x, y, edge) where edge is 'or less', 'or more' or ''."""
    out = []
    for label, value in _table(header):
        m = re.fullmatch(rf"{_N} {unit}(?: (or less|or more))?", label)
        assert m, f"cannot read the row {label!r} of the {header} table; update this test with the post"
        out.append((float(m.group(1)), float(value), m.group(2) or ""))
    return out


def _assert_curve_matches(rows, knots, curve, name):
    """The rows are the knots within their range, 'or less' / 'or more' hold beyond it, and
    between two rows the curve is the straight line the post says it is."""
    lo, hi = rows[0][0], rows[-1][0]
    inside = [(x, y) for x, y in knots if lo <= x <= hi]
    assert inside == [(x, y) for x, y, _ in rows], f"{name}\n  post: {rows}\n  code: {knots}"
    if rows[0][2] == "or less":
        assert all(y == rows[0][1] for x, y in knots if x < lo), f"{name}: flat below {lo}: {knots}"
    if rows[-1][2] == "or more":
        assert all(y == rows[-1][1] for x, y in knots if x > hi), f"{name}: flat above {hi}: {knots}"
    assert all(edge == "" for _, _, edge in rows[1:-1]), rows
    for (x0, y0, _), (x1, y1, _) in zip(rows, rows[1:]):
        assert math.isclose(curve((x0 + x1) / 2.0), (y0 + y1) / 2.0, abs_tol=1e-12), (name, x0, x1)


def test_the_size_table_is_the_size_curve():
    _says(r"Between rows, the score moves in a straight line\.", "that the size score is a straight line between rows")
    rows = _curve_rows(["Height", "Size score"], "ft")
    assert rows[-1][2] == "or more", rows
    _assert_curve_matches(rows, I._SIZE_POINTS, I.size_score, "size score")


def test_the_period_table_is_the_period_curve():
    rows = _curve_rows(["Period", "Score"], "s")
    assert (rows[0][2], rows[-1][2]) == ("or less", "or more"), rows
    _assert_curve_matches(rows, I._PERIOD_QUALITY_POINTS, I.period_quality, "period score")


def test_the_posts_period_example_between_rows_is_what_the_curve_gives():
    m = _says(rf"Between rows it's a straight line, so {_N} s gives about {_N}\.", "the 14 s example")
    period, stated = float(m.group(1)), float(m.group(2))
    assert round(I.period_quality(period), _decimals(m.group(2))) == stated, I.period_quality(period)


def test_the_posts_chop_table_is_the_chop_curve_knot_for_knot():
    published = [(float(r), float(s)) for r, s in _table(["chop_ratio", "Score"])]
    assert published == I._CHOP_POINTS, f"post: {published}\ncode: {I._CHOP_POINTS}"


def test_the_posts_chop_example_between_two_knots_is_what_the_curve_gives():
    m = _says(rf"so a chop_ratio of {_N} gives {_N}, not {_N}", "the chop example")
    ratio, stated, step_reading = float(m.group(1)), float(m.group(2)), float(m.group(3))
    got = round(I.chop_multiplier(ratio), _decimals(m.group(2)))
    assert got == stated, I.chop_multiplier(ratio)
    assert got != step_reading


def test_the_posts_neutral_score_for_an_unknown_ratio_is_the_codes():
    m = _says(rf"An unknown scores the neutral {_N}", "what an unknown chop_ratio scores")
    assert I.chop_multiplier(None) == float(m.group(1))


def test_heavy_chop_holds_an_above_neutral_wind_score_where_the_post_says():
    m = _says(rf"When more than {_N}% of the height is wind sea, a wind score that would be above "
              rf"neutral is held to {_N}\.", "the chop cap on offshore wind")
    threshold, held = float(m.group(1)) / 100.0, float(m.group(2))
    offshore = 250.0
    # straight offshore at a moderate speed scores above neutral ...
    assert I.wind_multiplier(offshore, 8.0, offshore, threshold) > 1.0      # at the threshold: not "more than"
    assert I.wind_multiplier(offshore, 8.0, offshore, threshold + 0.01) == held
    # ... and a wind that is already at or below neutral is left alone by chop
    onshore = (offshore + 180.0) % 360.0
    assert I.wind_multiplier(onshore, 8.0, offshore, 0.99) == I.wind_multiplier(onshore, 8.0, offshore, 0.0)


# --------------------------------------------------------------------------- #
# FLAT, the range, rounding, and the worked example                            #
# --------------------------------------------------------------------------- #
def test_under_half_a_foot_is_flat_and_everything_else_is_whole_or_half_stars_in_range():
    _says(r"A height under half a foot, after the direction adjustment, is FLAT: 0 stars\.", "the FLAT rule")
    mentions = _every(r"[Uu]nder ([a-z ]+?)(?:, after the direction adjustment,|,)? (?:is|the spot is) FLAT",
                      "the FLAT threshold")
    assert len(mentions) == 2 and set(mentions) == {"half a foot"}, mentions
    m = _says(r"rounded to the nearest half star and kept between (\d+) and (\d+)\.", "the star range")
    low, high = float(m.group(1)), float(m.group(2))
    half_a_foot = 0.5
    best = dict(wind_mult=1.2, tide_mult=1.0, chop_mult=1.0, period_q=1.05)
    worst = dict(wind_mult=0.44, tide_mult=0.6, chop_mult=0.3, period_q=0.5)
    assert I.composite_stars(half_a_foot - 0.01, **best) == 0.0      # even perfect conditions can't lift it
    assert I.composite_stars(half_a_foot, **worst) == low            # at half a foot, the worst still gets the floor
    assert I.composite_stars(50.0, **best) == high
    # "the nearest half star", not the nearest whole one: read off the post's size table, 4.4 ft
    # scores 3.2 (nearest half 3.0) and 4.6 ft scores 3.3 (nearest half 3.5, nearest whole 3).
    size = {x: y for x, y, _ in _curve_rows(["Height", "Size score"], "ft")}
    for face in (4.4, 4.6):
        raw = size[4.0] + (face - 4.0) * (size[5.0] - size[4.0])
        assert I.composite_stars(face, 1.0, 1.0, 1.0, 1.0) == math.floor(raw * 2 + 0.5) / 2, (face, raw)
    for i in range(80):
        face = half_a_foot + 0.25 * i
        for factors in (best, worst, dict(wind_mult=0.8, tide_mult=0.7, chop_mult=0.85, period_q=0.9)):
            stars = I.composite_stars(face, **factors)
            assert low <= stars <= high and stars * 2 == int(stars * 2), (face, factors, stars)


def test_the_worked_example_is_what_the_code_computes():
    m = _says(
        rf"Say a swell comes straight into the spot at {_N} ft, which scores {_N} for size\. "
        rf"The wind is offshore \({_N}\), there's some chop \({_N}\), the period is {_N} s \({_N}\) "
        rf"and the tide is wrong for the spot \({_N}\)\. Weighted, those four come to {_N}\. "
        rf"So the rating is {_N} × {_N} = {_N}, which rounds to {_N} stars\. "
        rf"Multiplied straight, the same scores would give {_N}\.",
        "the worked example")
    (height, size, wind, chop, period_s, period, tide, weighted,
     size_again, weighted_again, raw, stars, straight) = (float(g) for g in m.groups())
    assert (size_again, weighted_again) == (size, weighted)
    # each input is one the code actually produces for the conditions named
    assert I.size_score(height) == size
    assert I.period_quality(period_s) == period
    chop_ratio = next(float(r) for r, s in _table(["chop_ratio", "Score"]) if float(s) == chop)
    assert I.wind_multiplier(250.0, 8.0, 250.0, chop_ratio) == wind      # straight offshore, that much chop
    # the arithmetic, with the code's weights
    e = I.COMPOSITE_FACTOR_EXPONENTS
    blended = (wind ** e["wind_mult"] * chop ** e["chop_mult"]
               * period ** e["period_quality"] * tide ** e["tide_mult"])
    assert round(blended, _decimals(m.group(8))) == weighted, blended
    assert round(size * blended, _decimals(m.group(11))) == raw, size * blended
    assert I.composite_stars(height, wind, tide, chop, period) == stars
    assert round(size * wind * chop * period * tide, _decimals(m.group(13))) == straight


# --------------------------------------------------------------------------- #
# The swell window                                                             #
# --------------------------------------------------------------------------- #
_WINDOW = [{"min": 150.0, "max": 210.0, "span": 60.0}]      # a 60° window, no padding
_WIDE = [{"min": 0.0, "max": 350.0, "span": 350.0}]          # wide enough to reach the floor
_OPTIMAL = 180.0


def _gain_rows():
    rows = dict((k, v) for k, v in _table(["Swell direction", "Direction gain"]))
    inside = rows.pop("Inside the window", None)
    m = re.fullmatch(rf"cos²\(offset ÷ 2\), never below {_N}", inside or "")
    assert m, f"cannot read the in-window gain {inside!r}; update this test with the post"
    bands = []
    for label, value in rows.items():
        below = re.fullmatch(rf"Less than {_N}° outside", label)
        between = re.fullmatch(rf"{_N}° to {_N}° outside", label)
        beyond = re.fullmatch(rf"{_N}° or more outside", label)
        if below:
            lo, hi = 0.0, float(below.group(1))
        elif between:
            lo, hi = float(between.group(1)), float(between.group(2))
        elif beyond:
            lo, hi = float(beyond.group(1)), math.inf
        else:
            raise AssertionError(f"cannot read the gain row {label!r}; update this test with the post")
        bands.append((lo, hi, float(value)))
    return float(m.group(1)), sorted(bands)


def test_the_in_window_gain_is_the_half_angle_curve_with_its_floor():
    floor, _ = _gain_rows()
    for offset in range(0, 171, 5):
        for dp in (_OPTIMAL + offset, _OPTIMAL - offset):
            expected = max(floor, math.cos(math.radians(offset / 2.0)) ** 2)
            assert math.isclose(I.directional_gain(dp, _WIDE, _OPTIMAL, _OPTIMAL), expected, abs_tol=1e-12), offset
    m = _says(rf"A swell straight down that line gets {_N}, and one {_N}° off it gets {_N}\.", "the in-window examples")
    on_axis, off, off_gain = float(m.group(1)), float(m.group(2)), float(m.group(3))
    assert I.directional_gain(_OPTIMAL, _WINDOW, _OPTIMAL, _OPTIMAL) == on_axis
    assert round(I.directional_gain(_OPTIMAL + off, _WIDE, _OPTIMAL, _OPTIMAL), _decimals(m.group(3))) == off_gain


def test_the_outside_window_gains_are_the_ladder():
    _, bands = _gain_rows()
    assert bands[0][0] == 0.0 and bands[-1][1] == math.inf, bands
    assert all(a[1] == b[0] for a, b in zip(bands, bands[1:])), f"the bands must meet: {bands}"
    for lo, hi, value in bands:
        top = 150.0 if hi == math.inf else hi
        probes = {lo + 0.01, (lo + top) / 2.0, top - 0.01} | ({lo} if lo > 0 else set())
        for offset in probes:
            for dp in (_WINDOW[0]["max"] + offset, _WINDOW[0]["min"] - offset):
                assert I.directional_gain(dp % 360.0, _WINDOW, _OPTIMAL, _OPTIMAL) == value, (offset, dp, value)


# --------------------------------------------------------------------------- #
# Swell partitions: combining, and picking the dominant one                     #
# --------------------------------------------------------------------------- #
def _swell(n, hs_ft, tp, dp):
    return {f"swell_{n}_hs": hs_ft / I.M_TO_FT, f"swell_{n}_tp": tp, f"swell_{n}_dp": dp}


def test_combined_height_is_the_posts_energy_sum_without_the_wind_sea():
    _says(r"combined height = √\(sum over swells of height² × direction gain\)", "the combined-height formula")
    _says(r"The local wind sea is left out, and a swell 90° or more outside the spot's window adds nothing\.",
          "what the combined height leaves out")
    floor, bands = _gain_rows()
    band_gain = lambda offset: next(v for lo, hi, v in bands if lo <= offset < hi)  # noqa: E731
    entry = {**_swell(1, 2.0, 12.0, _OPTIMAL),                            # on axis
             **_swell(2, 3.0, 12.0, _WINDOW[0]["max"] + 60.0),            # 60° outside
             **_swell(3, 4.0, 12.0, _WINDOW[0]["max"] + 120.0),           # 120° outside
             "wind_wave_hs": 3.0, "wind_wave_tp": 4.0, "wind_wave_dp": _OPTIMAL}
    got = I.combine_ww3_partitions(entry, _WINDOW, _OPTIMAL, _OPTIMAL)["hs"] * I.M_TO_FT
    expected = math.sqrt(2.0 ** 2 * max(floor, 1.0) + 3.0 ** 2 * band_gain(60.0) + 4.0 ** 2 * band_gain(120.0))
    assert math.isclose(got, expected, rel_tol=1e-9), (got, expected)


def test_the_dominant_swell_is_picked_by_energy_times_period_score_squared():
    _says(r"We pick it by height² × direction gain × period score², so long-period swell counts extra\.",
          "how the dominant swell is picked")
    assert I.SELECTION_PERIOD_QUALITY_EXPONENT == 2      # the "²" on period score
    m = _says(rf"a {_N} ft {_N} s swell beats a {_N} ft {_N} s swell: {_N}² × {_N}² ≈ {_N} against "
              rf"{_N}² × {_N}² ≈ {_N}\.", "the dominant-swell example")
    (win_ft, win_s, lose_ft, lose_s, win_ft2, win_q, win_score,
     lose_ft2, lose_q, lose_score) = (float(g) for g in m.groups())
    assert (win_ft2, lose_ft2) == (win_ft, lose_ft)
    assert round(I.period_quality(win_s), _decimals(m.group(6))) == win_q
    assert round(I.period_quality(lose_s), _decimals(m.group(9))) == lose_q
    assert round(win_ft ** 2 * I.period_quality(win_s) ** 2, _decimals(m.group(7))) == win_score
    assert round(lose_ft ** 2 * I.period_quality(lose_s) ** 2, _decimals(m.group(10))) == lose_score
    entry = {**_swell(1, lose_ft, lose_s, _OPTIMAL), **_swell(2, win_ft, win_s, _OPTIMAL)}
    picked = I.combine_ww3_partitions(entry, _WINDOW, _OPTIMAL, _OPTIMAL)
    assert picked["tp"] == win_s and picked["dominant_partition"] == "swell_2", picked


# --------------------------------------------------------------------------- #
# Model steps and horizons                                                     #
# --------------------------------------------------------------------------- #
def _every(pattern, what):
    """Every mention, so a number repeated in the table and the prose can't drift in one place."""
    found = re.findall(pattern, _post())
    assert found, f"methodology.md no longer says {what}. Update this test in the same change as the post."
    return found


_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}


def test_ww3_is_read_in_the_steps_the_post_names_with_up_to_three_swells():
    steps = {int(s) for s in _every(r"in (\d+)-hour steps", "WW3's step")}
    diffs = {b - a for a, b in zip(C.WW3_STEP_HOURS, C.WW3_STEP_HOURS[1:])}
    assert diffs == steps, (diffs, steps)
    counts = {_WORDS[w.lower()] for w in _every(r"[Uu]p to (\w+) swells", "how many swells WW3 gives")}
    assert counts == {len([p for p in I._WW3_PARTITION_PREFIXES if p.startswith("swell_")])}, counts


def test_hrrr_covers_the_hours_the_post_names_then_nwps_wind():
    _says(r"We use HRRR for the first (\d+) hours of each run, and NWPS wind after that\.", "how long HRRR covers")
    mentions = {int(h) for h in _every(r"first (\d+) hours of each run", "how long HRRR covers")}
    assert mentions == {C.HRRR_MAX_FORECAST_HOURS}, mentions
    hours = C.HRRR_MAX_FORECAST_HOURS
    assert list(C.HRRR_STEP_HOURS) == list(range(0, hours + 1))       # "hourly steps"
    base = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    vt = lambda h: (base + datetime.timedelta(hours=h)).isoformat().replace("+00:00", "Z")  # noqa: E731
    nwps = {"S": [{"valid_time": vt(h), "wind_speed": 1.0, "wind_dir": 10.0} for h in range(hours + 3)]}
    hrrr = {"S": [{"valid_time": vt(h), "wind_speed": 5.0, "wind_dir": 20.0} for h in range(hours + 1)]}
    I._merge_hrrr_into_nwps(nwps, hrrr)
    sources = [e["wind_source"] for e in nwps["S"]]
    assert sources == ["hrrr"] * (hours + 1) + ["nwps"] * 2, sources


def test_outside_hrrrs_area_the_spots_are_the_places_the_post_names():
    _says(r"HRRR only covers the continental US, so Hawaii and Puerto Rico use NWPS wind throughout\.",
          "which spots HRRR does not cover")
    _says(r"then NWPS wind\. NWPS wind throughout in Hawaii and Puerto Rico\. \|", "the data table's wind note")
    lat_min, lat_max, lng_min, lng_max = C.HRRR_CONUS_BBOX
    outside = {s.get("nwps_wfo") for s in _rated_spots()
               if not (lat_min <= s["lat"] <= lat_max and lng_min <= s["lng"] <= lng_max)}
    assert outside == {"hfo", "sju"}, outside     # the Honolulu and San Juan forecast offices


# --------------------------------------------------------------------------- #
# Spot counts and the calibration window, from the data files                  #
# --------------------------------------------------------------------------- #
_CALIFORNIA_OFFICES = {"sgx", "lox", "mtr", "eka"}


def _rated_spots():
    with open(SPOTS, encoding="utf-8") as f:
        return [s for s in json.load(f) if s.get("is_valid_surf_spot") is not False]


def _factors():
    with open(FACE_FACTORS, encoding="utf-8") as f:
        return json.load(f)


def test_the_mop_spot_count_is_the_tagged_roster():
    n = int(_says(r"\*\*(\d+) California spots: CDIP MOP, for past hours only\.\*\*", "the MOP spot count").group(1))
    assert _post().count(f"CDIP MOP at {n} California spots") == 1
    mop = [s for s in _rated_spots() if s.get("swell_window_source") == "cdip_mop"]
    assert len(mop) == n
    assert {s.get("nwps_wfo") for s in mop} <= _CALIFORNIA_OFFICES
    nwps = [s for s in _rated_spots() if s.get("swell_window_source") == "nwps"]
    assert len(nwps) > len(_rated_spots()) / 2      # "Most spots: NWPS"


def test_the_calibration_count_and_window_are_the_factor_files():
    n = int(_says(r"\*\*Calibration at (\d+) California spots\.\*\*", "the calibrated spot count").group(1))
    assert _post().count(f"{n} California spots") >= 3      # the table, this section, and "still working on"
    data = _factors()
    assert len(data["factors"]) == n
    offices = {s["name"]: s.get("nwps_wfo") for s in _rated_spots()}
    slug = lambda name: re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")  # noqa: E731
    by_slug = {slug(name): wfo for name, wfo in offices.items()}
    assert {by_slug.get(k) for k in data["factors"]} <= _CALIFORNIA_OFFICES
    m = _says(r"over (\w+) weeks, (\d+) (\w+) to (\d+) (\w+) (\d{4})", "the calibration window")
    t0 = datetime.datetime.strptime(f"{m.group(2)} {m.group(3)} {m.group(6)}", "%d %B %Y").date()
    t1 = datetime.datetime.strptime(f"{m.group(4)} {m.group(5)} {m.group(6)}", "%d %B %Y").date()
    window = data["measurement"]["window"]
    assert (t0.isoformat(), t1.isoformat()) == (window["t0"], window["t1"]), window
    # Every length the post gives the current window — the table's, this section's and the one in
    # "what we're still working on" — is the file's. (Item 4's "earlier two-week windows" is plural
    # and describes older files, so the singular pattern leaves it alone.)
    weeks = {_WORDS[w] for w in _every(r"over (\w+) weeks", "how long the calibration was measured")}
    weeks |= {_WORDS[w] for w in _every(r"single (\w+)-week window\b", "the window each factor comes from")}
    assert {(t1 - t0).days} == {7 * w for w in weeks}, weeks


# --------------------------------------------------------------------------- #
# The height scale: MOP's hours, the calibrated spots, and everywhere else      #
# --------------------------------------------------------------------------- #
def test_every_mop_and_calibrated_count_in_the_post_is_the_datas():
    mop = sum(s.get("swell_window_source") == "cdip_mop" for s in _rated_spots())
    assert {int(n) for n in _every(r"(\d+) MOP spots", "the MOP spot count")} == {mop}
    calibrated = len(_factors()["factors"])
    assert {int(n) for n in _every(r"(\d+) calibrated California spots", "the calibrated spot count")} == {calibrated}


def test_only_mops_own_hours_skip_the_period_boost():
    _says(r"MOP's own height, which carries no boost, never reaches the page\.", "that MOP's own height carries no boost")
    _says(r"\*\*On the remaining hours\*\*, from WAVEWATCH III or our fallbacks, the model height gets a "
          r"boost for longer-period swell and isn't corrected, so it can read higher than CDIP's nearshore "
          r"height\.", "the uncorrected, boosted height")
    from pipeline.forecast import mop
    hs = 1.5
    for tp in (8.0, 12.0, 16.0):
        face = mop.mop_stars(hs, tp, 270.0, hs, 270.0)[1]
        assert math.isclose(face, hs * I.M_TO_FT, rel_tol=1e-12), (tp, face)   # no boost on MOP's hours
    # Everywhere else the boost never lowers a height, and it grows with period.
    for source in ("ww3", "nwps"):
        factors = [I.period_factor(tp, source) for tp in range(0, 31)]
        assert min(factors) >= 1.0 and factors == sorted(factors), (source, factors)
        assert I.period_factor(16.0, source) > I.period_factor(6.0, source), source


def test_calibrated_spots_are_divided_and_everything_else_is_left_as_the_model_made_it():
    # The calibrated count is held to the factor file by
    # test_every_mop_and_calibrated_count_in_the_post_is_the_datas.
    _says(r"Outside the \d+ calibrated California spots, heights aren't calibrated against measurements, and "
          r"that includes all the hours you can see at the 48 MOP spots", "which heights aren't corrected")
    from pipeline.forecast import face_correction as F
    spots = [{"name": "Calibrated", "swell_window_source": "nwps"},
             {"name": "Uncalibrated", "swell_window_source": "nwps"},
             {"name": "Mop", "swell_window_source": "cdip_mop"}]
    hour = {"face_ft": 4.0, "effective_size_ft": 4.0, "stars": 3.0,
            "wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0, "period_quality": 1.0}
    ratings = {s["name"]: [dict(hour)] for s in spots}
    # The MOP spot is given a factor on purpose: its height is MOP's own, so even a factor must not touch it.
    F.apply_face_corrections(ratings, spots, factors={"calibrated": {"factor": 2.0}, "mop": {"factor": 3.0}},
                             slug_for=lambda name: name.lower(), now=datetime.date(2026, 9, 25))
    assert ratings["Calibrated"][0]["face_ft"] == 4.0 / 2.0      # "divide our height by each spot's typical ratio"
    assert ratings["Uncalibrated"][0]["face_ft"] == 4.0          # "aren't corrected"
    assert ratings["Mop"][0]["face_ft"] == 4.0                   # MOP's own height, never divided


def test_the_surf_height_paragraph_is_the_codes():
    """"What the number is" states the surf height's constants and where it applies; they are
    pipeline/forecast/surf_height.py's, and the step leaves the stars alone."""
    from pipeline.forecast import surf_height as S
    _says(r"\*\*At every other spot, on the hours NWPS feeds\*\*, we take NWPS's height at its point off the "
          r"spot as the height in 10 m of water, carry it in to where the waves break, and take 70% of that\.",
          "the surf height's recipe")
    _says(r"it breaks once it is about 0\.78 times as high as the water is deep", "the breaking ratio")
    _says(r"70% is about one over 1\.4", "where 70% comes from")
    assert (S.SURF_K, S.D0_M, S.GAMMA) == (0.70, 10.0, 0.78)
    assert S.NWPS_OVERRIDE_SOURCES == {"nwps", "nwps_height_ww3_dir"}
    # 1 / 1.42 (run 52's FRF ratio) = 0.704, "about" 0.70
    assert round(1 / 1.42, 2) == 0.70
    _says(r"on the hours NWPS feeds, we show the surf height described under What the number is, and the "
          r"stars stay on the model's height\.", "that the stars don't read the surf height")
    spots = [{"name": "Uncalibrated", "swell_window_source": "nwps"}]
    # Hs 1.0 m at 500 s: face 1.0 x 1.3 x 3.281 = 4.2653 -> 4.27 (period_factor 'ww3' is 1.3 past 99 s)
    hour = {"face_ft": 4.27, "face_ft_raw": 4.27, "face_correction_version": "2:x:y", "swell_tp": 500.0,
            "swell_source": "nwps", "effective_size_ft": 4.0, "stars": 3.0}
    ratings = {"Uncalibrated": [dict(hour)]}
    S.apply_surf_height(ratings, spots, {"someone-else": {"factor": 2.0}}, lambda n: n.lower())
    # 0.70 x 1.508057 m (Green's-law breaking height of 1.0 m from 10 m) x 3.281 = 3.4636 -> 3.46
    assert ratings["Uncalibrated"][0]["face_ft"] == 3.46
    assert ratings["Uncalibrated"][0]["stars"] == 3.0 and ratings["Uncalibrated"][0]["effective_size_ft"] == 4.0


def test_the_wind_sea_paragraph_is_the_codes_rule():
    """"When the wind sea is the surf" states shown_period's rule: its two cases, with the code's
    constants, the window exclusion, and that the stars keep the rated swell."""
    from pipeline.forecast import shown_period as SP
    m = _says(r"when it is bigger than the swell we rate on and at least half a second longer-period, or "
              rf"when it is at least {_N} times that swell's height, whatever its period\.", "the two cases")
    assert float(m.group(1)) == SP.DOMINANCE_K == 4.0 and SP.MIN_LONGER_S == 0.5
    _says(r"In the second case the period we show can go down", "that the shown period can go down")
    _says(r"Neither case applies when the wind sea comes from 90° or more outside the spot's window, "
          r"where it can't reach the break and the rating gives a swell nothing\.", "the window exclusion")
    assert SP.BLOCKED_OUTSIDE_DEG == 90.0
    window = [{"min": 30, "max": 150, "span": 124}]            # sector 28-152
    from pipeline.interpret import directional_gain
    assert directional_gain(242.0, window, None, 90.0) == 0.0    # 242 - 152 = 90: nothing
    _says(r"The stars still use the swell we rate on", "that the stars keep the rated swell")
    row = {"swell_source": "nwps_height_ww3_dir", "swell_tp": 3.0, "swell_dp": 80.0,
           "swell_1_hs": 0.24, "swell_1_tp": 3.0, "swell_1_dp": 80.0,
           "wind_wave_hs": 2.4, "wind_wave_tp": 9.0, "wind_wave_dp": 70.0}
    assert SP.shown_train(row, None) == (9.0, 70.0, "wind_sea")                       # bigger and longer
    assert SP.shown_train(dict(row, wind_wave_tp=3.4), None)[2] == "wind_sea"          # 10x: dominant
    assert SP.shown_train(dict(row, wind_wave_hs=0.5, wind_wave_tp=3.4), None)[2] == "swell"   # 2x, 0.4 s
    # The Hatteras hour the paragraph names: 3.55 m (11.65 ft) at 10.2 s over 0.06 m (0.2 ft) at 12.97 s
    hat = dict(row, swell_tp=12.97, swell_dp=130.94, swell_1_hs=0.06, swell_1_tp=12.97,
               swell_1_dp=130.94, wind_wave_hs=3.55, wind_wave_tp=10.2, wind_wave_dp=59.42)
    assert SP.shown_train(hat, None) == (10.2, 59.42, "wind_sea")
    _says(r"an 11\.65 ft, 10\.2 s wind sea sat over a 0\.2 ft, 13 s swell", "the Hatteras example")
    # ... and the same Jacksonville wind sea from 242, 90 past that window, is not shown
    assert SP.shown_train(dict(row, wind_wave_dp=242.0), window) == (3.0, 80.0, "swell")
    assert SP.shown_train(dict(row, wind_wave_dp=241.0), window) == (9.0, 241.0, "wind_sea")


def test_mop_is_read_from_its_nowcast():
    """"For past hours only" rests on what we read: MOP's NOWCAST, which stops before the present.
    That the feed stops short of the current hour is CDIP's, measured on live rows and not pinned;
    that the pipeline reads the nowcast, whatever flavour a point URL was cached in, is code."""
    _says(r"We read its nowcast, and the nowcast ends before the current hour", "that MOP is read from its nowcast")
    from pipeline.forecast import mop
    for flavour in ("_hindcast", "_forecast", "_ecmwf_fc"):
        assert mop.nowcast_url(f"x/D0045{flavour}.nc") == "x/D0045_nowcast.nc", flavour
    # ... and the override's fetch asks for that URL, not the one the spot was cached with.
    asked = []
    real = mop.pull_mop_window
    mop.pull_mop_window = lambda url, t0, t1: asked.append(url) or []
    try:
        assert mop.mop_swell_by_hour({"mop_point_url": "x/D0045_forecast.nc"}) is None
    finally:
        mop.pull_mop_window = real
    assert asked == ["x/D0045_nowcast.nc"], asked


def _median_factor():
    ratios = sorted(rec["factor"] for rec in _factors()["factors"].values())
    mid = len(ratios) // 2
    return ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2.0


def test_calibrated_spots_are_rated_on_the_calibrated_height_times_the_median_ratio():
    """The post says it under Size, under calibration, and in item 6, so all three are read,
    and the code is held to them: a calibrated spot is sized at its calibrated height x M, i.e.
    model height x M / F (interpret.rule_keywords' size_scale), every other spot on the model's
    height, and the seam itself still divides only the height shown."""
    m = _says(r"At the \d+ calibrated California spots it reads the calibrated height times "
              rf"{_N}, the median of those spots' ratios between our model height and MOP's",
              "that calibrated spots are sized on the calibrated height times the median")
    published_m = float(m.group(1))
    # "1.59" is the factor file's median to the post's two decimals.
    assert abs(_median_factor() - published_m) <= 0.005, (_median_factor(), published_m)
    ex = _says(rf"one that runs higher, like Steamer Lane at {_N}, rates lower, and one that runs "
               rf"lower, like Pacifica Linda Mar at {_N}, rates higher\.", "the two examples")
    factors = _factors()["factors"]
    assert round(factors["steamer-lane"]["factor"], 2) == float(ex.group(1))
    assert round(factors["pacifica-linda-mar"]["factor"], 2) == float(ex.group(2))
    assert float(ex.group(1)) > published_m > float(ex.group(2))
    _says(r"At every other spot the size score reads the model's height with the boost for longer-period "
          r"swell \(see What the number is\): there's nothing to calibrate it against\.",
          "that uncalibrated spots keep the model's height")
    _says(rf"The star rating reads the calibrated height too, times {_N}, so these spots are rated "
          r"on the same scale as everywhere else", "the calibration paragraph")
    then = _says(r"We used to work out stars at the calibrated spots from the calibrated height\. The "
                 r"calibration in use then covered \d+ California spots, measured from (\d+) (\w+) to (\d+) (\w+) "
                 r"(\d{4}), and at all but \w+ of them that height was smaller than the uncalibrated one",
                 "why calibrated spots used to rate lower, dated to the calibration of the time")
    shares = _says(r"In the forecast published on (\d+) (\w+) (\d{4}), " + _N + r"% of those spots' daylight "
                   r"hours rated 3 stars or more, against " + _N + r"% at uncalibrated spots; rated on the "
                   r"model's height instead, they came to " + _N + r"%\.",
                   "the 3-star shares, dated to the forecast they were measured on")
    _says(rf"Now the rating reads their calibrated height times {_N} \(see Size\)\.", "what we changed")
    # Item 6's earlier numbers describe an EARLIER factor file, so they must read as history: its
    # window ended before the current file's began, and the forecast the shares were measured on
    # was published before the current file was generated.
    _date = lambda d, mon, y: datetime.datetime.strptime(f"{d} {mon} {y}", "%d %B %Y").date()  # noqa: E731
    measurement = _factors()["measurement"]
    assert _date(then.group(3), then.group(4), then.group(5)) < \
        datetime.date.fromisoformat(measurement["window"]["t0"]), (then.groups(), measurement["window"])
    assert _date(*shares.group(1, 2, 3)) < \
        datetime.date.fromisoformat(measurement["generated_at"][:10]), (shares.groups(), measurement)
    # The code. The rater sizes a calibrated spot at model height x M / F, which is calibrated
    # height x M; with F = M that is the model height, so it "rates exactly as" on the model's.
    reef = {"break_type": "reef", "bottom": "rock"}
    assert I.rule_keywords(reef, None, None, None, 4.0, published_m, published_m)["size_scale"] == 1.0
    assert I.rule_keywords(reef, None, None, None, 4.0, 3.11, published_m)["size_scale"] < 1.0
    assert I.rule_keywords(reef, None, None, None, 4.0, 0.88, published_m)["size_scale"] > 1.0
    assert I.rule_keywords(reef, None, None, None, 4.0, None, published_m)["size_scale"] == 1.0
    # And the seam stays display-only: it divides the height shown and leaves the producer's
    # effective size and stars alone. Expected values are read off the post's size table.
    size = {x: y for x, y, _ in _curve_rows(["Height", "Size score"], "ft")}
    from pipeline.forecast import face_correction as F
    spots = [{"name": "Calibrated", "swell_window_source": "nwps"},
             {"name": "Uncalibrated", "swell_window_source": "nwps"}]
    hour = {"face_ft": 4.0, "effective_size_ft": 4.0, "stars": size[4.0],
            "wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0, "period_quality": 1.0}
    ratings = {s["name"]: [dict(hour)] for s in spots}
    F.apply_face_corrections(ratings, spots, factors={"calibrated": {"factor": 2.0}},
                             slug_for=lambda name: name.lower(), now=datetime.date(2026, 10, 4))
    cal, unc = ratings["Calibrated"][0], ratings["Uncalibrated"][0]
    assert (cal["face_ft"], unc["face_ft"]) == (4.0 / 2.0, 4.0)          # "the height we show"
    assert cal["effective_size_ft"] == unc["effective_size_ft"] == 4.0   # the seam leaves it
    assert cal["stars"] == unc["stars"] == size[4.0]                     # the seam leaves it


def test_the_uncorrected_height_ratio_is_the_measured_median():
    m = _says(rf"At the California spots where we measured it, the uncorrected height was typically about "
              rf"{_N} times MOP's\.", "how far the uncorrected height ran above MOP's")
    median = _median_factor()
    # "About X times" is read literally, so it has to be X to the nearest tenth. Rounding to the
    # post's own precision is too loose here: it would let a median of 1.54 be called "about 2".
    assert abs(median - float(m.group(1))) <= 0.05, (median, m.group(1))


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} methodology-table checks passed")


if __name__ == "__main__":
    _run_all()


# --------------------------------------------------------------------------- #
# The beach-break limit and the strong-wind cap                                #
# --------------------------------------------------------------------------- #
def test_the_beach_break_table_is_the_beach_curve():
    rows = dict(_table(["Height", "Size score at a sand beach or jetty break"]))
    assert rows["up to 8 ft"] == "as above", rows
    for ft in (0.0, 1.0, 2.5, 4.0, 6.5, 8.0):
        assert I.beach_size_score(ft) == I.size_score(ft), ft
    assert {k: v for k, v in rows.items() if k != "up to 8 ft"} == {
        "8 to 10 ft": "4.5", "12 ft": "3.5", "15 ft": "2.5", "20 ft or more": "1.5"}, rows
    for ft in (8.0, 9.0, 10.0):
        assert I.beach_size_score(ft) == 4.5, ft
    assert (I.beach_size_score(12.0), I.beach_size_score(15.0)) == (3.5, 2.5)
    assert all(I.beach_size_score(ft) == 1.5 for ft in (20.0, 30.0, 50.0, 80.0))
    _says(r"Past about 8 ft the sandbars can't hold the waves", "where the limit starts")
    assert I.CLOSEOUT_START_FT == 8.0
    assert I.SAND_CEILING_BREAK_TYPES == ("beach", "jetty")


def test_closing_out_is_judged_at_the_calibrated_height():
    _says(r"A spot whose model height is 12 ft but whose calibrated height is 6 ft doesn't close "
          r"out; one whose calibrated height is 12 ft does", "the calibrated-height example")
    assert I.closeout_factor(6.0) == 1.0 and I.closeout_factor(12.0) < 1.0
    spot = {"break_type": "beach", "bottom": "sand"}
    assert I.rule_keywords(spot, None, None, None, 12.0, 2.0)["closeout_face_ft"] == 6.0


def _cap_cell(cell):
    if cell == "no cap":
        return 5.0
    m = re.fullmatch(rf"{_N}(?: stars)?", cell)
    assert m, f"cannot read the cap cell {cell!r}; update this test with the post"
    return float(m.group(1))


def test_the_wind_cap_table_is_the_cap():
    # Offshore bearing 0: offshore wind from 0, cross-shore from 90, onshore from 180. At 8 s
    # there is no allowance, so each "over" row is read half a knot above its threshold.
    rows = _table(["Wind", "Onshore", "Cross-shore", "Offshore"])
    assert [r[0] for r in rows] == ["6 m/s (11.7 kt) or more", "over 13 kt", "over 15 kt",
                                    "over 20 kt", "over 25 kt"], rows
    # The 6 m/s row reads the MEASURED speed: exactly 6 m/s (6 / 0.514444 = 11.663 kt, the
    # post's 11.7) at 16 s, where the allowance would read 3.663 kt, is still capped onshore.
    label, onshore, cross, offshore = rows[0]
    assert I.WIND_CAP_BLOWN_OUT_MS == 6.0 and round(6.0 / I.KT_TO_MS, 1) == 11.7
    got = tuple(I.wind_star_cap(d, 6.0, 0.0, 16.0) for d in (180.0, 90.0, 0.0))
    assert got == tuple(map(_cap_cell, (onshore, cross, offshore))), (label, got)
    assert I.wind_star_cap(180.0, 5.99, 0.0, 8.0) == 5.0       # just under 6 m/s: no cap
    for label, onshore, cross, offshore in rows[1:]:
        kt = float(re.fullmatch(r"over (\d+) kt", label).group(1)) + 0.5
        ms = kt * I.KT_TO_MS
        got = tuple(I.wind_star_cap(d, ms, 0.0, 8.0) for d in (180.0, 90.0, 0.0))
        assert got == tuple(map(_cap_cell, (onshore, cross, offshore))), (label, got)


def test_the_onshore_6_ms_row_is_the_blown_out_label():
    _says(r"The onshore 6 m/s row is the exception: it reads the measured wind, with no allowance "
          r"for the period\. 6 m/s onshore is where the forecast labels the surface \"Blown out\"",
          "why the 6 m/s row reads the measured wind")
    # The label's threshold is the rung's: classifySurface reads "Blown out" onshore (120 off
    # or more, the cap's onshore class) from 6 m/s, "choppy" below it.
    ts = open(os.path.join(ROOT, "frontend", "lib", "ratings.ts"), encoding="utf-8").read()
    assert "if (windSpeedMs < 6) return 'choppy';" in ts
    assert "if (off < 120) return 'mixed';" in ts
    assert I.WIND_CAP_BLOWN_OUT_MS == 6.0 and I.WIND_CAP_BLOWN_OUT == 2.5


def test_the_wind_classes_are_the_posts():
    _says(r"Offshore means within 60° of the spot's straight-offshore direction, cross-shore 60° "
          r"up to 120°, and onshore 120° or more\.", "the wind classes")
    assert [I.wind_class(d, 0.0) for d in (59.0, 60.0, 119.0, 120.0)] == [
        "offshore", "cross", "cross", "onshore"]


def test_cross_offshore_reads_the_offshore_column():
    m = _says(r"For the cap, cross-offshore wind, (\d+)° to (\d+)° off, counts as offshore",
              "the cross-offshore cap")
    lo, hi = (float(g) for g in m.groups())
    assert (lo, hi) == (60.0, I.WIND_CAP_OFFSHORE_SIDE_DEG)
    # Offshore bearing 0, at 8 s. 75 off reads the offshore column of the table (3 over 20 kt,
    # 2 over 25, none over 13 or 15); 90 off is cross-shore (1.5 over 20 kt).
    for kt, offshore in ((13.5, 5.0), (15.5, 5.0), (20.5, 3.0), (25.5, 2.0)):
        assert I.wind_star_cap(75.0, kt * I.KT_TO_MS, 0.0, 8.0) == offshore, kt
    assert I.wind_star_cap(90.0, 20.5 * I.KT_TO_MS, 0.0, 8.0) == 1.5


def test_the_period_allowance_example():
    m = _says(r"a (\d+) s swell in an (\d+) kt wind is read at (\d+) kt", "the allowance example")
    period, wind, read = (float(g) for g in m.groups())
    assert wind - (period - 8.0) == read
    # cross-shore: read at 14 kt the cap is the over-13 rung, not the over-15 one at 18 kt
    assert I.wind_star_cap(90.0, wind * I.KT_TO_MS, 0.0, period) == \
        I.wind_star_cap(90.0, read * I.KT_TO_MS, 0.0, 8.0)
    m = _says(r"so up to (\d+) kt the wind is read a knot lighter", "where the allowance stops")
    _says(rf"Above {m.group(1)} kt there's no allowance\.", "the measured rungs")
    top = float(m.group(1))
    # Cross-shore 1 kt above where the post says the allowance stops, at 16 s: read at the
    # measured speed it is over 20 kt (1.5); with 8 kt of allowance it would be under 13 (no cap).
    assert top == 20.0
    assert I.wind_star_cap(90.0, (top + 1.0) * I.KT_TO_MS, 0.0, 16.0) == 1.5
    # ...and 1 kt below it the allowance still applies: 19 - 8 = 11 kt, no cap.
    assert I.wind_star_cap(90.0, (top - 1.0) * I.KT_TO_MS, 0.0, 16.0) == 5.0
    assert I.wind_star_cap(90.0, 26.0 * I.KT_TO_MS, 0.0, 16.0) == 1.0
