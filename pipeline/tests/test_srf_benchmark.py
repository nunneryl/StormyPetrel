"""Fixture tests for scripts/srf_benchmark.py. No network, no database.

The script benchmarks our raw and calibrated heights against NWS Surf Zone Forecasts. It can
only run on the Mac, so everything it does between the HTTP calls and the printout is held
here against hand-written SRF products, listings, zone squares and forecast rows.

EVERY EXPECTED VALUE IS WRITTEN OUT BY HAND: ranges, zones, dates, maxima, means, shares and
medians. None comes from calling the function under test. Distances are chosen due north or
south of a zone edge, so the expected km is latitude degrees x 110.57.
"""
import ast
import datetime
import io
import json
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

import srf_benchmark as sb  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
D = datetime.date
UTC = datetime.timezone.utc


# --------------------------------------------------------------------------- #
# Fixture products                                                             #
# --------------------------------------------------------------------------- #

LOX_0908 = """000
FZUS56 KLOX 081030
SRFLOX

Surf Zone Forecast
National Weather Service Los Angeles/Oxnard CA
330 AM PDT Tue Sep 8 2026

CAZ087-349>350-082300-
Los Angeles County Beaches-
330 AM PDT Tue Sep 8 2026

...HIGH SURF ADVISORY IN EFFECT UNTIL 9 PM PDT THIS EVENING...

.TODAY...
Surf Height.................3 to 5 feet with local sets to 6 feet.
Water Temperature...........68 degrees.
Rip Current Risk............High.

.TONIGHT...
Surf Height.................2 to 4 feet.

.WEDNESDAY...
Surf Height.................2 to 3 feet.

$$

CAZ039-040-082300-
Ventura County Beaches-
330 AM PDT Tue Sep 8 2026

.TODAY...
Surf Height.................Around 2 feet.
Rip Current Risk............Low.

$$

CAZ034-035-082300-
San Luis Obispo County Beaches-
330 AM PDT Tue Sep 8 2026

.TODAY...
Surf Height.................3 to 5 feet at west facing beaches.

$$

CAZ548-082300-
Santa Barbara County South Coast Beaches-
330 AM PDT Tue Sep 8 2026

.TODAY...
Surf Height.................1 to 2 feet with local
                            sets to 3 feet.
Rip Current Risk............Low.

$$
"""

LOX_0909_LATE = """000
FZUS56 KLOX 090701
SRFLOX

Surf Zone Forecast
National Weather Service Los Angeles/Oxnard CA
1158 PM PDT Tue Sep 8 2026

CAZ087-349>350-091100-
Los Angeles County Beaches-
1158 PM PDT Tue Sep 8 2026

.REST OF TONIGHT...
Surf Height.................5 to 7 feet.

.WEDNESDAY...
Surf Height.................5 to 7 feet.

$$
"""

LOX_0909 = """000
FZUS56 KLOX 091030
SRFLOX

Surf Zone Forecast
National Weather Service Los Angeles/Oxnard CA
330 AM PDT Wed Sep 9 2026

CAZ087-349>350-092300-
Los Angeles County Beaches-
330 AM PDT Wed Sep 9 2026

.TODAY...
Surf Height.................2 to 4 feet.

$$

CAZ039-040-092300-
Ventura County Beaches-
330 AM PDT Wed Sep 9 2026

.TODAY...
Surf Height.................2 to 3 feet.

$$
"""

SGX_0908 = """000
FZUS56 KSGX 081012
SRFSGX

Surf Zone Forecast
National Weather Service San Diego CA
312 AM PDT Tue Sep 8 2026

CAZ552-082300-
Orange County Coastal Areas-
312 AM PDT Tue Sep 8 2026

.TODAY...
Surf Height.................2 to 3 feet.

$$

CAZ043-082300-
San Diego County Coastal Areas-
312 AM PDT Tue Sep 8 2026

.TODAY...
Surf Height.................2 to 3 feet.
Surf Height.................4 to 5 feet.

$$
"""

SGX_0909 = """000
FZUS56 KSGX 091012
SRFSGX

Surf Zone Forecast
National Weather Service San Diego CA
312 AM PDT Wed Sep 9 2026

CAZ552-092300-
Orange County Coastal Areas-
312 AM PDT Wed Sep 9 2026

.TODAY...
Surf Height.................2 to 3 feet.

$$

CAZ043-092300-
San Diego County Coastal Areas-
312 AM PDT Wed Sep 9 2026

.TODAY...
Surf Height.................3 to 4 feet.

$$
"""

MTR_0908 = """000
FZUS56 KMTR 080745
SRFMTR

Surf Zone Forecast
National Weather Service San Francisco CA
1245 AM PDT Tue Sep 8 2026

CAZ529-080800-
Santa Cruz County-
1245 AM PDT Tue Sep 8 2026

.REST OF TONIGHT...
Surf Height.................2 to 3 feet.

.TODAY...
Surf Height.................4 to 6 feet with sets to 8 feet.

$$

CAZ006-505-080800-
San Francisco-
San Mateo Coast-
1245 AM PDT Tue Sep 8 2026

.REST OF TONIGHT...
Surf Height.................1 to 2 feet.

.TODAY...
Surf Height.................Flat.

$$
"""

EKA_0908 = """000
FZUS56 KEKA 081030
SRFEKA

Surf Zone Forecast
National Weather Service Eureka CA
330 AM PDT Tue Sep 8 2026

CAZ101>103-082300-
Del Norte-
Northern Humboldt Coast-
Southern Humboldt Coast-
330 AM PDT Tue Sep 8 2026

.TODAY...
Surf Height.................Less than 1 foot.

$$
"""

AFTERNOON_0908 = """000
FZUS56 KLOX 082330
SRFLOX

Surf Zone Forecast
National Weather Service Los Angeles/Oxnard CA
430 PM PDT Tue Sep 8 2026

CAZ087-090800-
Los Angeles County Beaches-
430 PM PDT Tue Sep 8 2026

.TONIGHT...
Surf Height.................2 to 3 feet.

.WEDNESDAY...
Surf Height.................2 to 3 feet.

$$

CAZ039-090800-
Ventura County Beaches-
430 PM PDT Tue Sep 8 2026

.THIS AFTERNOON...
Rip Current Risk............Low.

$$
"""


def framed(text):
    """As IEM serves it: SOH, CR CR LF line ends, ETX."""
    return "\x01" + text.replace("\n", "\r\r\n") + "\x03"


# --------------------------------------------------------------------------- #
# 1. Every surf-height phrasing                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value, low, high, kind, sets, local", [
    ("3 to 5 feet.", 3.0, 5.0, "range", None, False),
    ("2 TO 3 FEET.", 2.0, 3.0, "range", None, False),
    ("2-3 feet", 2.0, 3.0, "range", None, False),
    ("1.5 to 2.5 feet.", 1.5, 2.5, "range", None, False),
    ("3 to 5 feet with local sets to 7 feet.", 3.0, 5.0, "range", 7.0, True),
    ("3 to 5 feet with sets to 6 feet.", 3.0, 5.0, "range", 6.0, False),
    ("3 to 5 feet, with local sets to 6 feet", 3.0, 5.0, "range", 6.0, True),
    ("4 to 6 feet. Local sets to 8 feet.", 4.0, 6.0, "range", 8.0, True),
    ("2 to 4 feet, sets to 5", 2.0, 4.0, "range", 5.0, False),
    ("Around 2 feet.", 2.0, 2.0, "around", None, False),
    ("around 1 foot with local sets to 2 feet.", 1.0, 1.0, "around", 2.0, True),
    ("Less than 1 foot.", 0.0, 1.0, "less_than", None, False),
    ("1 foot or less.", 0.0, 1.0, "or_less", None, False),
    ("Flat.", 0.0, 0.0, "flat", None, False),
    ("2 feet.", 2.0, 2.0, "single", None, False),
])
def test_every_phrasing_reads_as_written(value, low, high, kind, sets, local):
    surf, why = sb.parse_surf_height(value)
    assert why is None
    assert (surf.low, surf.high, surf.kind, surf.sets, surf.local_sets) == (low, high, kind, sets, local)


@pytest.mark.parametrize("value, why", [
    ("3 to 5 feet at west facing beaches.", "unrecognised surf height"),
    ("Building to 4 to 6 feet.", "unrecognised surf height"),
    ("3 to 5 feet with occasional sets to 7 feet.", "unrecognised surf height"),
    ("knee to waist high", "unrecognised surf height"),
    ("5 to 3 feet.", "range runs high to low"),
    ("", "empty surf height"),
])
def test_anything_else_is_refused_not_guessed(value, why):
    surf, reason = sb.parse_surf_height(value)
    assert surf is None
    assert reason.startswith(why)


def test_sets_never_move_the_range():
    surf, _ = sb.parse_surf_height("2 to 3 feet with local sets to 9 feet.")
    assert (surf.low, surf.high, surf.midpoint) == (2.0, 3.0, 2.5)


# --------------------------------------------------------------------------- #
# 2. Zones                                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ugc, zones", [
    ("CAZ087-349>351-011600-\n", ["CAZ087", "CAZ349", "CAZ350", "CAZ351"]),
    ("CAZ039-040-041-087-\n088-011600-\n", ["CAZ039", "CAZ040", "CAZ041", "CAZ087", "CAZ088"]),
    ("CAZ034-035-NVZ002-011600-\n", ["CAZ034", "CAZ035", "NVZ002"]),
    ("CAZ040>042-011600-\n", ["CAZ040", "CAZ041", "CAZ042"]),
    ("No zone line here.\n", []),
])
def test_ugc_zone_lists(ugc, zones):
    assert sb.ugc_zones(ugc) == zones


def test_segments_zones_and_names():
    segs = sb.split_segments(LOX_0908)
    assert [(s.zones, s.name) for s in segs] == [
        (("CAZ087", "CAZ349", "CAZ350"), "Los Angeles County Beaches"),
        (("CAZ039", "CAZ040"), "Ventura County Beaches"),
        (("CAZ034", "CAZ035"), "San Luis Obispo County Beaches"),
        (("CAZ548",), "Santa Barbara County South Coast Beaches"),
    ]
    mtr = sb.split_segments(MTR_0908)
    assert [(s.zones, s.name) for s in mtr] == [
        (("CAZ529",), "Santa Cruz County"),
        (("CAZ006", "CAZ505"), "San Francisco / San Mateo Coast"),
    ]
    eka = sb.split_segments(EKA_0908)
    assert [(s.zones, s.name) for s in eka] == [
        (("CAZ101", "CAZ102", "CAZ103"), "Del Norte / Northern Humboldt Coast / Southern Humboldt Coast"),
    ]


def test_framing_as_iem_serves_it_is_read_the_same():
    plain = [(s.zones, s.name) for s in sb.split_segments(SGX_0908)]
    assert [(s.zones, s.name) for s in sb.split_segments(framed(SGX_0908))] == plain
    issued, why = sb.parse_issuance(framed(SGX_0908))
    assert (issued.local, issued.tz, why) == (datetime.datetime(2026, 9, 8, 3, 12), "PDT", None)


# --------------------------------------------------------------------------- #
# 3. Issuance, periods, and each segment's first daytime surf height           #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("line, local, tz", [
    ("330 AM PDT Tue Sep 8 2026", datetime.datetime(2026, 9, 8, 3, 30), "PDT"),
    ("1200 AM PDT Thu Oct 1 2026", datetime.datetime(2026, 10, 1, 0, 0), "PDT"),
    ("1200 PM PDT Thu Oct 1 2026", datetime.datetime(2026, 10, 1, 12, 0), "PDT"),
    ("1045 PM PST Mon Nov 2 2026", datetime.datetime(2026, 11, 2, 22, 45), "PST"),
])
def test_issuance_lines(line, local, tz):
    issued, why = sb.parse_issuance(f"SRFLOX\n\nSurf Zone Forecast\n{line}\n")
    assert (issued.local, issued.tz, why) == (local, tz, None)


def test_issuance_refused_when_unreadable():
    assert sb.parse_issuance("330 AM PDT Wed Sep 8 2026")[1].startswith("issuance weekday")
    assert sb.parse_issuance("no time here") == (None, "no issuance line")


def _surf_by_segment(text, day):
    return [sb.segment_surf(s, day) for s in sb.split_segments(text)]


def test_each_segment_reads_its_first_daytime_period():
    got = _surf_by_segment(LOX_0908, D(2026, 9, 8))
    assert got[0] == (sb.SurfHeight(3.0, 5.0, "range", 6.0, True), None, "TODAY")
    assert got[1] == (sb.SurfHeight(2.0, 2.0, "around", None, False), None, "TODAY")
    assert got[2] == (None, "unrecognised surf height '3 to 5 feet at west facing beaches.' "
                            "in .TODAY...", "TODAY")
    # the wrapped value is joined across its continuation line
    assert got[3] == (sb.SurfHeight(1.0, 2.0, "range", 3.0, True), None, "TODAY")


def test_night_periods_are_skipped_for_the_first_daytime_one():
    got = _surf_by_segment(MTR_0908, D(2026, 9, 8))
    assert got == [(sb.SurfHeight(4.0, 6.0, "range", 8.0, False), None, "TODAY"),
                   (sb.SurfHeight(0.0, 0.0, "flat", None, False), None, "TODAY")]
    assert _surf_by_segment(EKA_0908, D(2026, 9, 8)) == [
        (sb.SurfHeight(0.0, 1.0, "less_than", None, False), None, "TODAY")]


def test_a_first_daytime_period_for_another_day_or_without_a_height_is_refused():
    got = _surf_by_segment(AFTERNOON_0908, D(2026, 9, 8))
    assert got[0] == (None, "first daytime period .WEDNESDAY... is for 2026-09-09, not 2026-09-08",
                      "WEDNESDAY")
    assert got[1] == (None, "no Surf Height line in .THIS AFTERNOON...", "THIS AFTERNOON")


def test_two_surf_heights_in_one_period_are_ambiguous():
    got = _surf_by_segment(SGX_0908, D(2026, 9, 8))
    assert got[0] == (sb.SurfHeight(2.0, 3.0, "range", None, False), None, "TODAY")
    assert got[1] == (None, "2 Surf Height lines in .TODAY...", "TODAY")


def test_every_unreadable_segment_is_logged():
    log = []
    product = sb.read_product("LOX", D(2026, 9, 8), "202609081030-KLOX-FZUS56-SRFLOX", LOX_0908,
                              sb.Issuance(datetime.datetime(2026, 9, 8, 3, 30), "PDT"), log)
    assert log == ["LOX 2026-09-08 202609081030-KLOX-FZUS56-SRFLOX [San Luis Obispo County Beaches] "
                   "CAZ034+CAZ035: unrecognised surf height '3 to 5 feet at west facing beaches.' "
                   "in .TODAY..."]
    assert sorted(product.by_zone) == ["CAZ034", "CAZ035", "CAZ039", "CAZ040", "CAZ087",
                                       "CAZ349", "CAZ350", "CAZ548"]
    assert product.by_zone["CAZ349"].surf == sb.SurfHeight(3.0, 5.0, "range", 6.0, True)
    assert len(product.segments) == 4


# --------------------------------------------------------------------------- #
# 4. Choosing each office's first issuance per local day                       #
# --------------------------------------------------------------------------- #

def _row(pid, pil):
    return {"index": 0, "entered": "x", "pil": pil, "product_id": pid, "cccc": "K" + pil[3:],
            "count": 1, "link": "x"}


def test_listing_keeps_our_offices_and_reads_utc_from_the_id():
    payload = {"schema": {}, "data": [
        _row("202609081030-KLOX-FZUS56-SRFLOX", "SRFLOX"),
        _row("202609081012-KSGX-FZUS56-SRFSGX-CCA", "SRFSGX"),
        _row("202609081000-KBOX-FZUS51-SRFBOX", "SRFBOX"),
        _row("garbage", "SRFLOX"),
    ]}
    assert sb.parse_listing(payload) == [
        sb.Listed("LOX", "202609081030-KLOX-FZUS56-SRFLOX",
                  datetime.datetime(2026, 9, 8, 10, 30, tzinfo=UTC), None),
        sb.Listed("SGX", "202609081012-KSGX-FZUS56-SRFSGX-CCA",
                  datetime.datetime(2026, 9, 8, 10, 12, tzinfo=UTC), "CCA"),
    ]


def test_candidates_are_grouped_by_local_day_earliest_first():
    rows = [_row(pid, "SRF" + pid.split("-")[1][1:]) for pid in (
        "202609082200-KLOX-FZUS56-SRFLOX",       # 15:00 PDT Sep 8
        "202609081030-KLOX-FZUS56-SRFLOX",       # 03:30 PDT Sep 8
        "202609080659-KLOX-FZUS56-SRFLOX",       # 23:59 PDT Sep 7
        "202609090701-KLOX-FZUS56-SRFLOX",       # 00:01 PDT Sep 9
        "202609081012-KSGX-FZUS56-SRFSGX-CCA",   # correction, same minute as the original
        "202609081012-KSGX-FZUS56-SRFSGX",
        "202611020730-KLOX-FZUS56-SRFLOX",       # 23:30 PST Nov 1 (UTC-8 after the change)
        "202611021400-KLOX-FZUS56-SRFLOX",       # 06:00 PST Nov 2
    )]
    got = sb.candidates_by_local_day(sb.parse_listing({"data": rows}), PT)
    assert {k: [p.product_id for p in v] for k, v in got.items()} == {
        ("LOX", D(2026, 9, 7)): ["202609080659-KLOX-FZUS56-SRFLOX"],
        ("LOX", D(2026, 9, 8)): ["202609081030-KLOX-FZUS56-SRFLOX", "202609082200-KLOX-FZUS56-SRFLOX"],
        ("LOX", D(2026, 9, 9)): ["202609090701-KLOX-FZUS56-SRFLOX"],
        ("SGX", D(2026, 9, 8)): ["202609081012-KSGX-FZUS56-SRFSGX", "202609081012-KSGX-FZUS56-SRFSGX-CCA"],
        ("LOX", D(2026, 11, 1)): ["202611020730-KLOX-FZUS56-SRFLOX"],
        ("LOX", D(2026, 11, 2)): ["202611021400-KLOX-FZUS56-SRFLOX"],
    }


def _fake_get(table):
    calls = []

    def get(url):
        calls.append(url)
        if url not in table:
            raise sb.FetchError(f"HTTP 404 on {url}")
        value = table[url]
        if isinstance(value, Exception):
            raise value
        return value
    return get, calls


def _text_url(pid):
    return sb.IEM_TEXT_URL.format(product_id=pid)


def test_a_product_issued_the_evening_before_is_passed_over_for_the_first_of_the_day():
    late, first = "202609090701-KLOX-FZUS56-SRFLOX", "202609091030-KLOX-FZUS56-SRFLOX"
    get, calls = _fake_get({_text_url(late): LOX_0909_LATE, _text_url(first): LOX_0909})
    cands = sb.candidates_by_local_day(sb.parse_listing({"data": [_row(late, "SRFLOX"),
                                                                  _row(first, "SRFLOX")]}), PT)
    log = []
    chosen = sb.choose_products(get, cands, [D(2026, 9, 9)], ("LOX",), log, lambda *_: None)
    assert chosen[("LOX", D(2026, 9, 9))].product_id == first
    assert chosen[("LOX", D(2026, 9, 9))].by_zone["CAZ087"].surf == sb.SurfHeight(2.0, 4.0, "range")
    assert log == [f"LOX 2026-09-09 {late}: issued 2026-09-08 23:58 PDT, another local day"]


def test_a_failed_first_issuance_leaves_the_day_empty_rather_than_using_a_later_one():
    first, later = "202609081030-KLOX-FZUS56-SRFLOX", "202609082200-KLOX-FZUS56-SRFLOX"
    get, calls = _fake_get({_text_url(first): sb.FetchError("HTTP 500 on x"),
                            _text_url(later): LOX_0908})
    cands = sb.candidates_by_local_day(sb.parse_listing({"data": [_row(first, "SRFLOX"),
                                                                  _row(later, "SRFLOX")]}), PT)
    log = []
    chosen = sb.choose_products(get, cands, [D(2026, 9, 8)], ("LOX",), log, lambda *_: None)
    assert chosen == {}
    assert calls == [_text_url(first)]
    assert log == [f"LOX 2026-09-08 {first}: HTTP 500 on x"]


# --------------------------------------------------------------------------- #
# 5. Mapping spots to zones                                                    #
# --------------------------------------------------------------------------- #

def square(lat0, lat1, lon0, lon1):
    return {"type": "Polygon", "coordinates": [[[lon0, lat0], [lon1, lat0], [lon1, lat1],
                                                 [lon0, lat1], [lon0, lat0]]]}


def test_inside_a_zone_is_0_km_and_offshore_is_the_distance_to_its_edge():
    ring = sb.rings(square(34.30, 34.40, -119.30, -119.20))[0]
    assert sb.dist_km(34.35, -119.25, ring) == 0.0
    # 0.03 degrees due south of the bottom edge: 0.03 x 110.57 = 3.3171 km
    assert sb.dist_km(34.27, -119.25, ring) == pytest.approx(3.3171, abs=1e-3)


def test_spots_map_inside_or_within_5_km_and_no_further():
    geoms = {"CAZ039": sb.rings(square(34.30, 34.40, -119.30, -119.20)),
             "CAZ087": sb.rings(square(33.95, 34.05, -118.55, -118.45)),
             "CAZ040": sb.rings({"type": "MultiPolygon", "coordinates": [
                 square(35.00, 35.10, -120.10, -120.00)["coordinates"]]})}
    spots = [{"slug": "inside", "lat": 34.00, "lng": -118.50},
             {"slug": "near", "lat": 34.27, "lng": -119.25},     # 3.3171 km from CAZ039
             {"slug": "far", "lat": 34.24, "lng": -119.25},      # 6.6342 km from CAZ039
             {"slug": "edge", "lat": 34.2548, "lng": -119.25}]   # 0.0452 x 110.57 = 4.9978 km
    mapped, unmapped = sb.map_spots(spots, geoms)
    assert {k: (z, round(km, 3)) for k, (z, km) in mapped.items()} == {
        "inside": ("CAZ087", 0.0), "near": ("CAZ039", 3.317), "edge": ("CAZ039", 4.998)}
    assert [(s["slug"], z, round(km, 3)) for s, z, km in unmapped] == [("far", "CAZ039", 6.634)]


def test_zone_geometry_falls_back_to_the_public_endpoint_and_is_cached(tmp_path):
    cache = tmp_path / "cache" / "srf_zone_geometry.json"
    get, calls = _fake_get({
        "https://api.weather.gov/zones/forecast/CAZ039": json.dumps({"geometry": square(34.3, 34.4, -119.3, -119.2)}),
        "https://api.weather.gov/zones/public/CAZ040": json.dumps({"geometry": square(35.0, 35.1, -120.1, -120.0)}),
    })
    log = []
    geoms = sb.zone_geometries(get, {"CAZ039", "CAZ040", "CAZ999"}, str(cache), log, lambda *_: None)
    assert sorted(geoms) == ["CAZ039", "CAZ040"]
    assert calls == ["https://api.weather.gov/zones/forecast/CAZ039",
                     "https://api.weather.gov/zones/forecast/CAZ040",
                     "https://api.weather.gov/zones/public/CAZ040",
                     "https://api.weather.gov/zones/forecast/CAZ999",
                     "https://api.weather.gov/zones/public/CAZ999"]
    assert log == ["zone CAZ999: no geometry from api.weather.gov (HTTP 404 on "
                   "https://api.weather.gov/zones/forecast/CAZ999; HTTP 404 on "
                   "https://api.weather.gov/zones/public/CAZ999)"]
    assert sorted(json.loads(cache.read_text())) == ["CAZ039", "CAZ040"]
    # a second run asks only for what the cache lacks
    get2, calls2 = _fake_get({})
    assert sorted(sb.zone_geometries(get2, {"CAZ039", "CAZ040"}, str(cache), [], lambda *_: None)) == \
        ["CAZ039", "CAZ040"]
    assert calls2 == []


# --------------------------------------------------------------------------- #
# 6. Our daytime max and mean                                                  #
# --------------------------------------------------------------------------- #

def _rows_at(local_day, hours_values, tzoff):
    """Rows at local hours given a fixed UTC offset written out per fixture day."""
    out = []
    for hour, cal, raw in hours_values:
        utc = (datetime.datetime(local_day.year, local_day.month, local_day.day, hour)
               - datetime.timedelta(hours=tzoff))
        out.append({"valid_time": utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "face_ft": cal, "face_ft_raw": raw})
    return out


CAL_0908 = [1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2]   # 06:00..18:00


def test_daytime_max_and_mean_over_06_to_18_local_both_included():
    rows = _rows_at(D(2026, 9, 8), [(5, 9.9, 9.9)]
                    + [(6 + i, c, 2 * c) for i, c in enumerate(CAL_0908)]
                    + [(19, 9.9, 9.9)], tzoff=-7)
    rows.append({"valid_time": "2026-09-08T19:30:00+00:00", "face_ft": 9.9, "face_ft_raw": 9.9})  # 12:30 PDT
    values, skipped = sb.daytime_values(rows, PT)
    v = values[D(2026, 9, 8)]
    assert (v.max_cal, v.max_raw) == (2.2, 4.4)
    assert v.mean_cal == pytest.approx(1.6)
    assert v.mean_raw == pytest.approx(3.2)
    assert skipped == {}


def test_daytime_hours_follow_pacific_standard_time_after_the_change():
    # Mon 2026-11-02 is PST, UTC-8: 06:00 local is 14:00Z, 18:00 is 02:00Z on the 3rd.
    rows = _rows_at(D(2026, 11, 2), [(5, 9.9, 9.9)] + [(h, 1.0, 1.5) for h in range(6, 18)]
                    + [(18, 3.0, 4.5), (19, 9.9, 9.9)], tzoff=-8)
    values, _ = sb.daytime_values(rows, PT)
    v = values[D(2026, 11, 2)]
    assert (v.max_cal, v.max_raw) == (3.0, 4.5)
    assert v.mean_cal == pytest.approx(15 / 13)
    assert v.mean_raw == pytest.approx(22.5 / 13)


def test_a_day_missing_an_hour_or_a_value_is_skipped_and_says_why():
    rows = (_rows_at(D(2026, 9, 9), [(h, 1.0, 2.0) for h in range(6, 19) if h != 12], tzoff=-7)
            + _rows_at(D(2026, 9, 10), [(h, 1.0, None if h == 9 else 2.0) for h in range(6, 19)], tzoff=-7))
    values, skipped = sb.daytime_values(rows, PT)
    assert values == {}
    assert skipped == {D(2026, 9, 9): "missing or null at local hours [12]",
                       D(2026, 9, 10): "missing or null at local hours [9]"}


# --------------------------------------------------------------------------- #
# 7. Below / inside / above, and the ratio to the midpoint                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("value, low, high, side", [
    (1.99, 2, 4, "below"), (2.0, 2, 4, "inside"), (3.0, 2, 4, "inside"), (4.0, 2, 4, "inside"),
    (4.01, 2, 4, "above"), (2.0, 2, 2, "inside"), (2.01, 2, 2, "above"), (0.5, 0, 0, "above"),
    (0.0, 0, 1, "inside"),
])
def test_classification_counts_both_ends_as_inside(value, low, high, side):
    assert sb.classify(value, sb.SurfHeight(float(low), float(high), "range")) == side


def test_ratio_to_the_midpoint():
    assert sb.ratio_to_midpoint(3.0, sb.SurfHeight(2.0, 4.0, "range")) == 1.0
    assert sb.ratio_to_midpoint(4.5, sb.SurfHeight(2.0, 4.0, "range")) == 1.5
    assert sb.ratio_to_midpoint(0.5, sb.SurfHeight(0.0, 1.0, "less_than")) == 1.0
    assert sb.ratio_to_midpoint(0.5, sb.SurfHeight(0.0, 0.0, "flat")) is None


def _rec(cal, raw, low=2.0, high=4.0, sets=None, office="LOX"):
    return sb.SpotDay(office, "seg", "s", D(2026, 9, 8), sb.SurfHeight(low, high, "range", sets),
                      sb.DayValues(cal, raw, cal / 2, raw / 2))


def test_summary_shares_and_medians():
    recs = [_rec(1.5, 3.0), _rec(2.5, 4.0, sets=5.0), _rec(3.0, 4.5), _rec(5.0, 6.0)]
    s = sb.summarise(recs, "max")
    assert (s["n"], s["sets"]) == (4, 1)
    assert {k: s["cal"][k] for k in ("below", "inside", "above")} == {"below": 1, "inside": 2, "above": 1}
    assert {k: s["raw"][k] for k in ("below", "inside", "above")} == {"below": 0, "inside": 2, "above": 2}
    # cal ratios 0.5, 0.8333, 1.0, 1.6667 -> median 0.91667; raw 1.0, 1.3333, 1.5, 2.0 -> 1.41667
    assert s["cal"]["median_ratio"] == pytest.approx(0.916667, abs=1e-6)
    assert s["raw"]["median_ratio"] == pytest.approx(1.416667, abs=1e-6)
    m = sb.summarise(recs, "mean")   # every mean is half the max: cal 0.75 1.25 1.5 2.5, raw 1.5 2.0 2.25 3.0
    assert {k: m["cal"][k] for k in ("below", "inside", "above")} == {"below": 3, "inside": 1, "above": 0}
    assert m["raw"]["median_ratio"] == pytest.approx(0.708333, abs=1e-6)


def test_the_rule_applied_supported_not_supported_and_warned():
    supported = sb.summarise([_rec(1.0, 3.0), _rec(1.5, 2.5), _rec(1.8, 4.0), _rec(3.0, 4.5)], "max")
    # raw inside 3 of 4 vs calibrated 1 of 4; raw ratios 1.0, 0.8333, 1.3333, 1.5 -> median 1.1667
    assert sb.rule_lines("LOX", supported) == [
        "LOX: raw inside 75.0% vs calibrated 25.0%, more often: yes; raw median ratio 1.17 at most "
        "1.25: yes -> raw SUPPORTED"]
    tied = sb.summarise([_rec(1.5, 3.0), _rec(2.5, 4.0), _rec(3.0, 4.5), _rec(5.0, 6.0)], "max")
    assert sb.rule_lines("SGX", tied) == [
        "SGX: raw inside 50.0% vs calibrated 50.0%, more often: no; raw median ratio 1.42 at most "
        "1.25: no -> raw not supported",
        "SGX: WARNING raw's median ratio 1.42 is above 1.25: rating on raw would put us well above "
        "NWS forecasters."]
    exactly = sb.summarise([_rec(1.0, 3.75), _rec(1.0, 3.75)], "max")   # raw ratio 3.75 / 3 = 1.25
    assert sb.rule_lines("EKA", exactly)[0].endswith("1.25 at most 1.25: yes -> raw SUPPORTED")
    assert sb.rule_lines("MTR", sb.summarise([], "max")) == ["MTR: no spot-days"]


def test_a_median_just_over_the_limit_never_prints_as_the_limit():
    # 3.7647 / 3 = 1.2549: two decimals would read "1.25 ... no", so it shows three
    over = sb.rule_lines("LOX", sb.summarise([_rec(1.0, 3.7647)], "max"))
    assert over == [
        "LOX: raw inside 100.0% vs calibrated 0.0%, more often: yes; raw median ratio 1.255 at most "
        "1.25: no -> raw not supported",
        "LOX: WARNING raw's median ratio 1.255 is above 1.25: rating on raw would put us well above "
        "NWS forecasters."]
    # 3.75039 / 3 = 1.25013 needs four
    assert "raw median ratio 1.2501 at most 1.25: no" in sb.rule_lines("LOX", sb.summarise([_rec(1.0, 3.75039)], "max"))[0]
    # 3.738 / 3 = 1.246 rounds to 1.25 and is within, so two decimals stand
    under = sb.rule_lines("LOX", sb.summarise([_rec(1.0, 3.738)], "max"))
    assert under == ["LOX: raw inside 100.0% vs calibrated 0.0%, more often: yes; raw median ratio 1.25 at "
                     "most 1.25: yes -> raw SUPPORTED"]


# --------------------------------------------------------------------------- #
# 8. Read-only, paced, labelled                                                #
# --------------------------------------------------------------------------- #

SCRIPT = os.path.join(ROOT, "scripts", "srf_benchmark.py")


def _calls(tree):
    """(enclosing function, call) for every call in the module. A call on a module name is
    qualified ('os.replace'), so it cannot be confused with str.replace or datetime.replace;
    any other method call is '.name'."""
    out = []

    def label(f):
        if isinstance(f, ast.Attribute):
            if isinstance(f.value, ast.Name) and f.value.id in ("os", "json", "shutil", "pathlib"):
                return f"{f.value.id}.{f.attr}"
            if (isinstance(f.value, ast.Attribute) and f.value.attr == "path"
                    and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys"):
                return f"sys.path.{f.attr}"
            return f".{f.attr}"
        return getattr(f, "id", "")

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(child, ast.Call):
                out.append((fn, label(child.func)))
            visit(child, name)
    visit(tree, "<module>")
    return out


def test_nothing_writes_except_the_geometry_cache():
    tree = ast.parse(open(SCRIPT).read())
    calls = _calls(tree)
    db_writes = {".insert", ".upsert", ".update", ".delete", ".rpc", ".table", ".execute"}
    assert [c for c in calls if c[1] in db_writes] == []
    assert sorted({c for c in calls if c[1].startswith(("os.", "json.dump", "shutil.", "pathlib."))
                   or c[1] in (".write", ".write_text", ".mkdir", ".unlink", ".rename")}) == [
        ("save_geometry_cache", "json.dump"), ("save_geometry_cache", "os.makedirs"),
        ("save_geometry_cache", "os.replace")]
    opened = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"]
    modes = sorted(n.args[1].value if len(n.args) > 1 else "r" for n in opened)
    assert modes == ["r", "r", "r", "w"]


def test_the_cache_is_in_a_gitignored_path():
    rel = os.path.relpath(sb.CACHE_PATH, ROOT)
    assert rel == os.path.join("pipeline", "cache", "srf_zone_geometry.json")
    assert subprocess.run(["git", "check-ignore", "-q", rel], cwd=ROOT).returncode == 0


def test_requests_are_paced_a_second_apart():
    clock = [100.0]
    slept = []

    def sleep(s):
        slept.append(s)
        clock[0] += s
    pacer = sb.Pacer(clock=lambda: clock[0], sleep=sleep)
    pacer.wait()
    clock[0] += 0.25
    pacer.wait()
    clock[0] += 3.0
    pacer.wait()
    assert slept == [0.75]


def test_requests_carry_the_contact_and_go_through_the_pacer(monkeypatch):
    import pipeline.http
    seen = []

    class Resp:
        text = "ok"
    monkeypatch.setattr(pipeline.http, "get", lambda url, **kw: seen.append((url, kw)) or Resp())
    waits = []
    pacer = sb.Pacer(clock=lambda: 0.0, sleep=waits.append)
    get = sb.make_get(pacer)
    assert get("https://example.test/a") == "ok"
    assert get("https://example.test/b") == "ok"
    assert [u for u, _ in seen] == ["https://example.test/a", "https://example.test/b"]
    assert all(kw["headers"]["User-Agent"] == sb.USER_AGENT for _, kw in seen)
    assert "https://stormypetrel.surf" in sb.USER_AGENT
    assert waits == [1.0]
    assert sb.MIN_REQUEST_INTERVAL_S >= 1.0


# --------------------------------------------------------------------------- #
# 9. End to end, with every network and database call faked                    #
# --------------------------------------------------------------------------- #

RULE_AS_GIVEN = """\
- Raw supports rating everyone on raw if it falls inside NWS's range
  more often than calibrated does, AND its median ratio to the
  midpoint is at most 1.25.
- If raw's median ratio is above 1.25, rating on raw would put us well
  above NWS forecasters: report that as a warning against it.
- Read it office by office. SGX has little power, since its factors
  only run 0.98 to 1.39.
- This is a benchmark against a forecast, not ground truth."""

SPOTS = [
    {"slug": "alpha", "name": "Alpha Beach", "lat": 34.00, "lng": -118.50, "wfo": "LOX", "factor": 1.5},
    {"slug": "bravo", "name": "Bravo Point", "lat": 34.27, "lng": -119.25, "wfo": "LOX", "factor": 2.0},
    {"slug": "charlie", "name": "Charlie Reef", "lat": 34.24, "lng": -119.25, "wfo": "LOX", "factor": 1.2},
    {"slug": "delta", "name": "Delta Pier", "lat": 33.60, "lng": -117.90, "wfo": "SGX", "factor": 1.5},
    {"slug": "echo", "name": "Echo Cove", "lat": 32.80, "lng": -117.25, "wfo": "SGX", "factor": 1.3},
    # an LOX spot inside SGX's Orange County zone; mapped, but the database returns no rows for it
    {"slug": "foxtrot", "name": "Foxtrot Sands", "lat": 33.62, "lng": -117.92, "wfo": "LOX", "factor": 1.1},
]

# Constant heights through each day: (cal, raw) per spot per local day.
HEIGHTS = {
    ("Alpha Beach", D(2026, 9, 8)): (2.5, 3.75), ("Alpha Beach", D(2026, 9, 9)): (3.0, 4.5),
    ("Bravo Point", D(2026, 9, 8)): (2.0, 3.0), ("Bravo Point", D(2026, 9, 9)): (1.5, 2.25),
    ("Delta Pier", D(2026, 9, 8)): (2.4, 3.6), ("Delta Pier", D(2026, 9, 9)): (2.2, 3.3),
    ("Echo Cove", D(2026, 9, 8)): (3.1, 4.65), ("Echo Cove", D(2026, 9, 9)): (3.1, 4.65),
}


def _world():
    pids = {"LOX_0908": "202609081030-KLOX-FZUS56-SRFLOX", "LOX_0908_PM": "202609082200-KLOX-FZUS56-SRFLOX",
            "LOX_0909_LATE": "202609090701-KLOX-FZUS56-SRFLOX", "LOX_0909": "202609091030-KLOX-FZUS56-SRFLOX",
            "SGX_0908": "202609081012-KSGX-FZUS56-SRFSGX", "SGX_0909": "202609091012-KSGX-FZUS56-SRFSGX",
            "SGX_0909_CCA": "202609091012-KSGX-FZUS56-SRFSGX-CCA"}
    listing = {
        "2026-09-08": ["LOX_0908", "LOX_0908_PM", "SGX_0908"],
        "2026-09-09": ["LOX_0909_LATE", "LOX_0909", "SGX_0909", "SGX_0909_CCA"],
        "2026-09-10": [],
    }
    table = {}
    for date, names in listing.items():
        rows = [_row(pids[n], "SRF" + pids[n].split("-")[1][1:]) for n in names]
        rows.append(_row("202609081000-KBOX-FZUS51-SRFBOX", "SRFBOX"))
        table[sb.IEM_LIST_URL.format(date=date)] = json.dumps({"schema": {}, "data": rows})
    for name, text in (("LOX_0908", framed(LOX_0908)), ("LOX_0909_LATE", LOX_0909_LATE),
                       ("LOX_0909", LOX_0909), ("SGX_0908", SGX_0908), ("SGX_0909", SGX_0909)):
        table[_text_url(pids[name])] = text
    zones = {"CAZ087": square(33.95, 34.05, -118.55, -118.45), "CAZ039": square(34.30, 34.40, -119.30, -119.20),
             "CAZ552": square(33.55, 33.65, -117.95, -117.85), "CAZ043": square(32.75, 32.85, -117.30, -117.20)}
    for zone, geom in zones.items():
        table[f"https://api.weather.gov/zones/forecast/{zone}"] = json.dumps({"geometry": geom})
    return table


def _read_rows(expect_t0, expect_t1, asked):
    def read(names, t0, t1):
        asked.append((sorted(names), t0, t1))
        assert (t0, t1) == (expect_t0, expect_t1)
        out = {}
        for (name, day), (cal, raw) in HEIGHTS.items():
            hours = [(h, cal, raw) for h in range(6, 19)
                     if not (name == "Echo Cove" and day == D(2026, 9, 9) and h == 12)]
            out.setdefault(name, []).extend(_rows_at(day, hours, tzoff=-7))
        return out
    return read


def _run(tmp_path, get, asked):
    out, err = io.StringIO(), io.StringIO()
    code = sb.run(get=get, read_rows=_read_rows("2026-09-08T13:00:00+00:00", "2026-09-10T01:00:00+00:00", asked),
                  now=datetime.datetime(2026, 9, 10, 12, 0, tzinfo=UTC), tz=PT, spots=SPOTS,
                  cache_path=str(tmp_path / "srf_zone_geometry.json"), out=out, err=err)
    return code, out.getvalue()


def _table_row(text, section, label):
    """The cells of *label*'s row in the first table after *section*."""
    body = text.split(section, 1)[1]
    line = next(ln for ln in body.splitlines() if ln.startswith(label + " "))
    return line[34:].split()


def _plain_row(text, section, label):
    """The whitespace-split cells after *label* in the first such row after *section*."""
    body = text.split(section, 1)[1]
    return next(ln for ln in body.splitlines() if ln.startswith(label + " ")).split()[1:]


def test_end_to_end_report(tmp_path):
    get, calls = _fake_get(_world())
    asked = []
    code, text = _run(tmp_path, get, asked)
    assert code == 0
    assert text.startswith(RULE_AS_GIVEN + "\n")
    assert asked == [(["Alpha Beach", "Bravo Point", "Delta Pier", "Echo Cove", "Foxtrot Sands"],
                      "2026-09-08T13:00:00+00:00", "2026-09-10T01:00:00+00:00")]
    # IEM: three listings, then each office's first readable issuance per local day
    iem = [c for c in calls if "mesonet" in c]
    assert iem == [sb.IEM_LIST_URL.format(date=d) for d in ("2026-09-08", "2026-09-09", "2026-09-10")] + [
        _text_url("202609081030-KLOX-FZUS56-SRFLOX"), _text_url("202609090701-KLOX-FZUS56-SRFLOX"),
        _text_url("202609091030-KLOX-FZUS56-SRFLOX"), _text_url("202609081012-KSGX-FZUS56-SRFSGX"),
        _text_url("202609091012-KSGX-FZUS56-SRFSGX")]

    # Products: local days, first issuance read, segments, unreadable. LOX 4 + 2 segments with
    # San Luis Obispo unreadable; SGX 2 + 2 with San Diego's two surf heights on the 8th.
    assert _plain_row(text, "== Products", "LOX") == ["2", "2", "6", "1"]
    assert _plain_row(text, "== Products", "SGX") == ["2", "2", "4", "1"]
    assert _plain_row(text, "== Products", "MTR") == ["2", "0", "0", "0"]
    assert _plain_row(text, "== Products", "EKA") == ["2", "0", "0", "0"]
    # Mapping, by each spot's NWPS office: calibrated, mapped, unmapped.
    assert _plain_row(text, "== Spots mapped", "LOX") == ["4", "3", "1"]
    assert _plain_row(text, "== Spots mapped", "SGX") == ["2", "2", "0"]
    assert _plain_row(text, "== Spots mapped", "MTR") == ["0", "0", "0"]
    assert _plain_row(text, "== Spots mapped", "ALL") == ["6", "5", "1"]
    assert ("  cross-office LOX Foxtrot Sands: CAZ552 is in SGX's SRF, so its spot-days count "
            "under SGX") in text
    assert text.count("  cross-office ") == 1

    # LOX: alpha 08 (3-5) cal below raw inside; alpha 09 (2-4) cal inside raw above;
    #      bravo 08 (around 2) cal inside raw above; bravo 09 (2-3) cal below raw inside.
    #      cal ratios .625 1.0 1.0 .6 -> .8125; raw .9375 1.5 1.5 .9 -> 1.21875; one set.
    assert _table_row(text, "== Results: daily MAX", "LOX") == [
        "4", "|", "50.0%", "50.0%", "0.0%", "0.81", "|", "0.0%", "50.0%", "50.0%", "1.22", "|", "1",
        "factors", "1.50-2.00"]
    # SGX: delta 08 and 09 (2-3) cal inside, raw above; echo 08 unreadable, echo 09 incomplete.
    #      cal ratios .96 .88 -> .92; raw 1.44 1.32 -> 1.38
    assert _table_row(text, "== Results: daily MAX", "SGX") == [
        "2", "|", "0.0%", "100.0%", "0.0%", "0.92", "|", "0.0%", "0.0%", "100.0%", "1.38", "|", "0",
        "factors", "1.50-1.50"]
    assert _table_row(text, "== Results: daily MAX", "ALL") == [
        "6", "|", "33.3%", "66.7%", "0.0%", "0.92", "|", "0.0%", "33.3%", "66.7%", "1.38", "|", "1"]
    # an office with no spot-days keeps its row and says so under the rule
    assert _table_row(text, "== Results: daily MAX", "MTR") == [
        "0", "|", "-", "-", "-", "-", "|", "-", "-", "-", "-", "|", "0"]
    assert "  MTR: no spot-days" in text and "  EKA: no spot-days" in text
    assert ("  LOX: raw inside 50.0% vs calibrated 50.0%, more often: no; raw median ratio 1.22 at "
            "most 1.25: yes -> raw not supported") in text
    assert ("  SGX: WARNING raw's median ratio 1.38 is above 1.25: rating on raw would put us well "
            "above NWS forecasters.") in text
    # per segment. Los Angeles is alpha alone: cal 2.5 in 3-5, 3.0 in 2-4; raw 3.75, 4.5.
    #   cal ratios .625 1.0 -> .8125; raw .9375 1.5 -> 1.21875; one set.
    assert _table_row(text, "-- Per segment (max)", "LOX Los Angeles County Beaches") == [
        "2", "|", "50.0%", "50.0%", "0.0%", "0.81", "|", "0.0%", "50.0%", "50.0%", "1.22", "|", "1",
        "1", "spot"]
    assert _table_row(text, "-- Per segment (max)", "LOX Ventura County Beaches")[:1] == ["2"]
    assert _table_row(text, "-- Per segment (max)", "SGX Orange County Coastal Areas")[:1] == ["2"]
    # the mean check sees the same spot-days; constant heights make its shares match the max
    assert _table_row(text, "== Check: daily MEAN", "ALL") == _table_row(text, "== Results: daily MAX", "ALL")

    assert "  unmapped LOX Charlie Reef: nearest CAZ039 at 6.6 km" in text
    assert "  SGX segment unreadable that day: 1" in text
    assert "  SGX our rows incomplete for 06:00 to 18:00: 1" in text
    assert "  SGX no forecast rows for the spot (or not in the spots table): 2" in text
    assert ("  LOX 2026-09-08 202609081030-KLOX-FZUS56-SRFLOX [San Luis Obispo County Beaches] "
            "CAZ034+CAZ035: unrecognised surf height '3 to 5 feet at west facing beaches.' in .TODAY...") in text
    assert "  LOX 2026-09-09 202609090701-KLOX-FZUS56-SRFLOX: issued 2026-09-08 23:58 PDT, another local day" in text
    assert "  MTR 2026-09-08: no SRF listed for this local day" in text
    assert "  spot-days on an 'around N' or bare 'N feet' height: 1" in text

    # a second run reuses the geometry cache and asks api.weather.gov for nothing it has
    get2, calls2 = _fake_get(_world())
    _run(tmp_path, get2, [])
    assert [c for c in calls2 if "zones/forecast/CAZ087" in c] == []


def test_a_local_day_counts_only_once_its_18_00_has_passed():
    # Wed 2026-09-09 18:00 PDT is 2026-09-10 01:00Z.
    assert sb.local_days(D(2026, 9, 8), D(2026, 9, 9), PT,
                         datetime.datetime(2026, 9, 10, 0, 59, tzinfo=UTC)) == [D(2026, 9, 8)]
    assert sb.local_days(D(2026, 9, 8), D(2026, 9, 9), PT,
                         datetime.datetime(2026, 9, 10, 1, 0, tzinfo=UTC)) == [D(2026, 9, 8), D(2026, 9, 9)]


def test_end_before_the_window_or_after_the_last_complete_day_is_refused(tmp_path):
    out = io.StringIO()
    code = sb.run(end=D(2026, 9, 10), get=lambda u: "", read_rows=lambda *a: {}, spots=[],
                  now=datetime.datetime(2026, 9, 10, 12, 0, tzinfo=UTC), tz=PT,
                  cache_path=str(tmp_path / "c.json"), out=out, err=io.StringIO())
    assert code == 2
    assert out.getvalue().startswith(RULE_AS_GIVEN + "\n")
    assert "--end 2026-09-10 must fall between 2026-09-08 and the last complete UTC day, 2026-09-09" \
        in out.getvalue()
