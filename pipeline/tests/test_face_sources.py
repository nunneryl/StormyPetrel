"""The face-height source check (pipeline/face_sources.py): its parsers and its pass/fail
logic, offline. Every expected value is read off the fixture text by hand or worked in the
comment beside it; none is read back from the function under test.

  distance   a square from -75.76 to -75.75 E and 36.17 to 36.19 N.
             cos 36.18 deg = cos 36 cos 0.18 - sin 36 sin 0.18
                           = 0.809017 x 0.999995 - 0.587785 x 0.0031416 = 0.807166
             (36.18, -75.74): 0.01 deg east of the east edge -> 0.01 x 111.32 x 0.807166
                              = 0.89854 km
             (36.20, -75.755): 0.01 deg north of the north edge -> 0.01 x 110.57 = 1.1057 km
"""
from __future__ import annotations

import pytest

from pipeline import face_sources as F

MHX = """
921
FZUS52 KMHX 250046
SRFMHX

Surf Zone Forecast
National Weather Service Newport/Morehead City NC
846 PM EDT Sat Oct 24 2026

NCZ203-250830-
Northern Outer Banks-
Including the beaches of Duck, Southern Shores, Kitty Hawk,
Kill Devil Hills, and Nags Head
846 PM EDT Sat Oct 24 2026

...HIGH SURF ADVISORY IN EFFECT UNTIL 8 PM EDT SUNDAY...

.SUNDAY...
Rip Current Risk*...........High.
Surf Height.................6 to 8 feet.
Winds.......................Northeast winds around 15 mph.

.MONDAY...
Rip Current Risk*...........High.
Surf Height.................Around 6 feet.

.TUESDAY...Surf height 3 to 4 feet. Partly sunny.
.WEDNESDAY...Surf height around 4 feet. Sunny.

$$

NCZ205-NCZ207-250830-
Hatteras Island-
846 PM EDT Sat Oct 24 2026

.SUNDAY...
Surf Height...

$$
"""


@pytest.mark.parametrize("text, want", [
    ("6 to 8 feet.", {"lo": 6.0, "hi": 8.0, "sets": None}),
    ("Around 6 feet.", {"lo": 6.0, "hi": 6.0, "sets": None}),
    ("5 to 7 ft.", {"lo": 5.0, "hi": 7.0, "sets": None}),
    ("3 to 5 feet, local sets to 6 feet.", {"lo": 3.0, "hi": 5.0, "sets": 6.0}),
    ("Surf height 2 to 4 feet. Mostly cloudy.", {"lo": 2.0, "hi": 4.0, "sets": None}),
    ("1-2 feet.", {"lo": 1.0, "hi": 2.0, "sets": None}),
    ("Surf height around 3 feet. Sunny.", {"lo": 3.0, "hi": 3.0, "sets": None}),
    ("", None),
    ("Flat.", None),
    ("Winds around 15 mph.", None),          # a number with no feet is not a height
])
def test_parse_surf_height(text, want):
    assert F.parse_surf_height(text) == want


@pytest.mark.parametrize("header, want", [
    ("NCZ203-250830-", ["NCZ203"]),
    ("NCZ205-NCZ207-250830-", ["NCZ205", "NCZ207"]),
    ("HIZ003-029>031-260245-", ["HIZ003", "HIZ029", "HIZ030", "HIZ031"]),
    ("FLZ141-147-260800-", ["FLZ141", "FLZ147"]),
    ("029-260245-", []),                     # bare numbers with no state before them
])
def test_ugc_codes(header, want):
    assert F._ugc_codes(header) == want


def test_srf_segments():
    segs = F.srf_segments(MHX)
    assert [s["zones"] for s in segs] == [["NCZ203"], ["NCZ205", "NCZ207"]]
    assert [s["name"] for s in segs] == ["Northern Outer Banks", "Hatteras Island"]
    assert segs[0]["periods"] == [
        ("SUNDAY", {"lo": 6.0, "hi": 8.0, "sets": None}),
        ("MONDAY", {"lo": 6.0, "hi": 6.0, "sets": None}),
        ("TUESDAY", {"lo": 3.0, "hi": 4.0, "sets": None}),     # the outlook's heading line
        ("WEDNESDAY", {"lo": 4.0, "hi": 4.0, "sets": None}),
    ]
    assert segs[1]["periods"] == [("SUNDAY", None)]            # a bare 'Surf Height...'


def test_text_after_the_segment_end_belongs_to_no_segment():
    # A trailer after '$$' (here a stray outlook line) is outside every segment: the
    # Northern Outer Banks keeps its four periods and gains no FRIDAY.
    first = MHX.split("NCZ205")[0]                              # NCZ203 up to its '$$'
    trailer = first + ".FRIDAY...Surf height 9 to 10 feet.\n"
    (seg,) = F.srf_segments(trailer)
    assert [p for p, _ in seg["periods"]] == ["SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY"]


CATALOG_YEARS = """
<a href="1990/catalog.html"><code>1990</code></a>
<a href="2026/catalog.html"><code>2026</code></a>
<a href="2025/catalog.html"><code>2025</code></a>
<a href="catalog.html?dataset=frfDatasetScan/oceanography/waves/8m-array/8m-array.ncml">
"""
CATALOG_FILES = """
<a href='catalog.html?dataset=frfDatasetScan/oceanography/waves/8m-array/2026/FRF-ocean_waves_8m-array_202609.nc'>
<a href='catalog.html?dataset=frfDatasetScan/oceanography/waves/8m-array/2026/FRF-ocean_waves_8m-array_202610.nc'>
<a href='catalog.html?dataset=frfDatasetScan/oceanography/waves/8m-array/2026/FRF-ocean_waves_8m-array_202601.nc'>
<a href='catalog.html?dataset=frfDatasetScan/oceanography/waves/8m-array/8m-array.ncml'>
"""


def test_catalog_parsing():
    assert F.catalog_years(CATALOG_YEARS) == ["1990", "2025", "2026"]
    assert F.catalog_files(CATALOG_FILES) == [
        "FRF-ocean_waves_8m-array_202601.nc", "FRF-ocean_waves_8m-array_202609.nc",
        "FRF-ocean_waves_8m-array_202610.nc"]                  # the .ncml is not a file


SQUARE = {"type": "Polygon", "coordinates": [[
    [-75.76, 36.17], [-75.75, 36.17], [-75.75, 36.19], [-75.76, 36.19], [-75.76, 36.17]]]}


@pytest.mark.parametrize("lat, lng, km", [
    (36.18, -75.755, 0.0),          # inside
    (36.19, -75.755, 0.0),          # on the edge counts as inside
    (36.18, -75.74, 0.899),         # 0.89854 km east
    (36.20, -75.755, 1.106),        # 1.1057 km north
])
def test_distance_to_geometry_km(lat, lng, km):
    assert round(F.distance_to_geometry_km(lat, lng, SQUARE), 3) == km


def test_frf_newest_takes_the_newest_year_and_month():
    pages = {f"{F.FRF_CATALOG}8m-array/catalog.html": CATALOG_YEARS,
             f"{F.FRF_CATALOG}8m-array/2026/catalog.html": CATALOG_FILES}
    assert F.frf_newest("8m-array", text=pages.__getitem__) == (
        "2026", "FRF-ocean_waves_8m-array_202610.nc")


def test_frf_newest_with_no_years_fails():
    with pytest.raises(ValueError, match="no year folders"):
        F.frf_newest("8m-array", text=lambda url: "<html></html>")


def test_iem_latest_parses_the_duck_segment():
    got = F.check_iem_latest(text=lambda url: MHX)
    assert got["segment"] == "Northern Outer Banks"
    assert got["issued"] == "846 PM EDT Sat Oct 24 2026"
    assert got["surf_height_ft"][0] == ("SUNDAY", {"lo": 6.0, "hi": 8.0, "sets": None})
    assert got["segments_in_product"] == 2


def test_iem_latest_without_the_zone_fails():
    with pytest.raises(ValueError, match="no NCZ203 segment"):
        F.check_iem_latest(text=lambda url: MHX.replace("NCZ203", "NCZ999"))


def test_iem_archive_counts_products():
    two = MHX + MHX
    assert F.check_iem_archive(text=lambda url: two)["products"] == 2
    with pytest.raises(ValueError, match="no SRFMHX products"):
        F.check_iem_archive(text=lambda url: "")


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _Var:
    def __init__(self, vals, units=None):
        import numpy as np
        self._v = np.ma.masked_invalid(np.array(vals, dtype=float))
        self.units = units

    def __getitem__(self, k):
        return self._v[k]

    def __len__(self):
        return len(self._v)


class _DS:
    def __init__(self, **v):
        self.variables = v


def _frf_pages(url):
    return CATALOG_YEARS if url.endswith("8m-array/catalog.html") else CATALOG_FILES


def _frf_ds(url):
    # 2026-10-25 00:00 UTC: 56 years to 2026-01-01 = 56 x 365 + 14 leap days = 20,454 days;
    # 1 Jan to 25 Oct = 31+28+31+30+31+30+31+31+30 + 24 = 297 days; 20,751 days x 86,400 s
    # = 1,792,886,400 s. The hour before is 1,792,882,800.
    return _DS(time=_Var([1792882800.0, 1792886400.0], "seconds since 1970-01-01 00:00:00"),
               waveHs=_Var([1.5, 1.886]))


def test_frf_8m_passes_on_opendap_and_fileserver():
    got = F.check_frf_8m(text=_frf_pages, open_nc=_frf_ds, http=lambda url, **kw: _Resp(206))
    assert got == {"file": "FRF-ocean_waves_8m-array_202610.nc", "last_time": "2026-10-25T00:00Z",
                   "last_waveHs_m": 1.886, "times": 2, "fileServer_status": 206}


def test_frf_8m_fails_when_the_fileserver_is_refused():
    with pytest.raises(ValueError, match="HTTP 403"):
        F.check_frf_8m(text=_frf_pages, open_nc=_frf_ds, http=lambda url, **kw: _Resp(403))


def test_zone_inside_tolerance_passes_and_beyond_fails(monkeypatch):
    import json
    near = {"geometry": SQUARE, "properties": {"name": "Square"}}
    monkeypatch.setattr(F, "DUCK_PIER", (36.18, -75.74))         # 0.899 km out: inside 2 km
    got = F.check_zone(text=lambda url, **kw: json.dumps(near))
    assert got["duck_pier_km_from_zone"] == 0.899
    monkeypatch.setattr(F, "DUCK_PIER", (36.18, -75.70))
    # 0.05 deg east: 0.05 x 111.32 x 0.807166 = 4.4927 km, beyond 2 km
    with pytest.raises(ValueError, match="4.49 km"):
        F.check_zone(text=lambda url, **kw: json.dumps(near))


def test_main_exits_1_when_any_check_fails(monkeypatch, capsys, tmp_path):
    def boom():
        raise OSError("403 Forbidden")
    monkeypatch.setattr(F, "CHECKS", [("good", lambda: {"x": 1}), ("bad", boom)])
    out = tmp_path / "r.json"
    assert F.main(["--out", str(out)]) == 1
    text = capsys.readouterr().out
    assert "PASS  good" in text and "FAIL  bad" in text
    assert "1 of 2 reachable; FAILED: bad" in text
    import json
    rows = json.loads(out.read_text())
    assert [r["ok"] for r in rows] == [True, False]
    assert rows[1]["error"] == "OSError: 403 Forbidden"


def test_main_exits_0_when_every_check_passes(monkeypatch, capsys):
    monkeypatch.setattr(F, "CHECKS", [("good", lambda: {"x": 1})])
    assert F.main([]) == 0
    assert "1 of 1 reachable" in capsys.readouterr().out
