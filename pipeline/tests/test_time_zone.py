"""Each spot's time zone (pipeline/enrichment/time_zone.py), the roster's time_zone field, and
the region report table the frontend shows report times in (frontend/lib/reports.ts).

NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every zone below is written out by hand from
where the place is. The lookup itself is timezonefinder's; these tests pin what it must answer
at the spots where the answer is not the state's (the Florida panhandle) or has no daylight
saving (Hawaii, Puerto Rico), and that every roster spot carries a zone.

Run: python -m pytest pipeline/tests/test_time_zone.py
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from zoneinfo import ZoneInfo

import pytest

from pipeline.enrichment import time_zone as T

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
REPORTS_TS = os.path.join(ROOT, "frontend", "lib", "reports.ts")

# (spot, lat, lng, zone), the zone written by hand from the place.
NAMED = [
    ("Steamer Lane", 36.951506, -122.025611, "America/Los_Angeles"),
    ("Jacksonville Beach Pier", 30.2935679, -81.3877064, "America/New_York"),
    # The panhandle: Bay County and west are Central; Gulf County's Cape San Blas is Eastern.
    ("Pensacola Beach", 30.328731, -87.140293, "America/Chicago"),
    ("Mexico Beach", 29.930837, -85.402222, "America/Chicago"),
    ("Cape San Blas", 29.675973, -85.365272, "America/New_York"),
    ("Galveston Seawall", 29.284396, -94.792893, "America/Chicago"),
    ("Banzai Pipeline", 21.664, -158.054, "Pacific/Honolulu"),
    ("Rincon Domes", 18.365065, -67.270306, "America/Puerto_Rico"),
]


def _roster():
    with open(ROSTER, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("name, lat, lng, zone", NAMED, ids=[n[0] for n in NAMED])
def test_the_lookup_gives_each_named_spot_its_zone(name, lat, lng, zone):
    assert T.time_zone_for(lat, lng) == zone


@pytest.mark.parametrize("name, lat, lng, zone", NAMED, ids=[n[0] for n in NAMED])
def test_the_roster_carries_each_named_spot_s_zone(name, lat, lng, zone):
    spot = next(s for s in _roster() if s["name"] == name)
    assert (spot["lat"], spot["lng"], spot["time_zone"]) == (lat, lng, zone)


def test_every_roster_spot_has_a_real_zone():
    """Every spot with coordinates carries a zone this system knows, none of them an Etc/
    ocean zone; the five on the 2026-10-29 roster are these."""
    roster = _roster()
    missing = [s["name"] for s in roster if s.get("lat") is not None and not s.get("time_zone")]
    assert not missing, missing
    zones = Counter(s["time_zone"] for s in roster if s.get("time_zone"))
    for z in zones:
        ZoneInfo(z)
        assert not z.startswith("Etc/"), z
    assert set(zones) == {"America/Los_Angeles", "America/New_York", "America/Chicago",
                          "Pacific/Honolulu", "America/Puerto_Rico"}


def test_florida_splits_at_the_panhandle_not_by_state():
    """The seven Florida spots west of Cape San Blas are Central; every other Florida spot is
    Eastern."""
    fl = [s for s in _roster() if s.get("region_hint") == "Florida"]
    central = sorted(s["name"] for s in fl if s["time_zone"] == "America/Chicago")
    assert central == sorted(["Pensacola Beach", "Navarre Beach", "Okaloosa Island Pier",
                              "Destin Jetty", "Panama City Beach Pier", "St Andrews Park",
                              "Mexico Beach"])
    assert all(s["time_zone"] == "America/New_York" for s in fl if s["name"] not in central)
    assert all(s["lng"] < -85.38 for s in fl if s["name"] in central)


class _Finder:
    """A stand-in for timezonefinder: *land* answers at points whose latitude is at least
    *edge*; everything else is open ocean."""

    def __init__(self, edge, land="America/New_York", ocean="Etc/GMT+5"):
        self.edge, self.land, self.ocean, self.asked = edge, land, ocean, []

    def timezone_at(self, lng, lat):
        self.asked.append((lat, lng))
        return self.land if lat >= self.edge else self.ocean


def test_an_offshore_spot_takes_the_nearest_land_zone():
    # 10 km south of a coast running east-west: the 2 and 5 km rings stay at sea (5 km is
    # 0.0452 deg of latitude), the 10 km ring's northern points (0.0904 deg) reach land.
    f = _Finder(edge=30.0 + 0.09)
    assert T.time_zone_for(30.0, -80.0, finder=f) == "America/New_York"
    assert len(f.asked) == 1 + 3 * T.RING_BEARINGS


def test_a_spot_with_no_land_zone_in_reach_raises():
    with pytest.raises(ValueError):
        T.time_zone_for(30.0, -80.0, finder=_Finder(edge=31.0))


def test_a_spot_inside_a_zone_is_not_searched_around():
    f = _Finder(edge=0.0, land="Pacific/Honolulu")
    assert T.time_zone_for(21.6, -158.0, finder=f) == "Pacific/Honolulu" and len(f.asked) == 1


def test_etc_zones_are_not_land_zones():
    assert [T.is_land_zone(z) for z in ("America/Chicago", "Etc/GMT+5", None)] == [True, False, False]


def test_each_report_region_shows_its_spots_majority_zone():
    """frontend/lib/reports.ts REGION_TIME_ZONE gives each daily-report region the zone most of
    its roster spots are in (daily_report.REGIONS, with the roster's region_hint as state)."""
    from pipeline.daily_report import REGIONS
    text = open(REPORTS_TS, encoding="utf-8").read()
    block = text[text.index("export const REGION_TIME_ZONE"):]
    block = block[:block.index("};")]
    table = dict(re.findall(r"^\s+(\w+): '([^']+)',$", block, re.M))
    assert set(table) == {r.key for r in REGIONS}
    roster = [dict(s, state=s.get("region_hint")) for s in _roster() if s.get("time_zone")]
    for region in REGIONS:
        zones = Counter(s["time_zone"] for s in roster if region.matcher(s))
        assert zones, region.key
        assert table[region.key] == zones.most_common(1)[0][0], (region.key, zones)
    # written out: the Florida report is Eastern although seven of its spots are Central
    assert table["florida"] == "America/New_York" and table["gulf"] == "America/Chicago"


def test_enrich_writes_each_spot_s_zone_and_keeps_the_old_one_when_the_lookup_fails(monkeypatch):
    """_enrich_one sets time_zone from the spot's own coordinates; a lookup that raises leaves
    the zone the spot already carried."""
    from pipeline import enrich
    from pipeline.tests.test_break_type_provenance import _enrich
    out = _enrich({"name": "Mexico Beach", "lat": 29.930837, "lng": -85.402222})
    assert out["time_zone"] == "America/Chicago"

    def boom(lat, lng):
        raise ValueError("no zone")
    monkeypatch.setattr(enrich, "time_zone_for", boom)
    kept = _enrich({"name": "Mexico Beach", "lat": 29.930837, "lng": -85.402222,
                    "time_zone": "America/Chicago"})
    assert kept["time_zone"] == "America/Chicago"
    assert "time_zone" not in _enrich({"name": "Mexico Beach", "lat": 29.930837, "lng": -85.402222})


def test_only_the_enrich_step_and_the_tests_install_the_lookup():
    """timezonefinder is about 50 MB: requirements.txt, which every workflow installs, does not
    carry it; requirements-enrich.txt does, and only sw1-raycast's enrich step and
    requirements-dev.txt read that file."""
    req = lambda name: open(os.path.join(ROOT, "pipeline", name), encoding="utf-8").read()  # noqa: E731
    assert "timezonefinder" not in req("requirements.txt")
    lines = [ln for ln in req("requirements-enrich.txt").splitlines() if ln and not ln.startswith("#")]
    assert lines == ['timezonefinder>=8.2,<9; python_version >= "3.11"',
                     'timezonefinder>=6.6,<7; python_version < "3.11"']
    assert "-r requirements-enrich.txt" in req("requirements-dev.txt").splitlines()
    workflows = os.path.join(ROOT, ".github", "workflows")
    users = sorted(f for f in os.listdir(workflows)
                   if "requirements-enrich.txt" in open(os.path.join(workflows, f), encoding="utf-8").read())
    assert users == ["sw1-raycast.yml"]
    wf = open(os.path.join(workflows, "sw1-raycast.yml"), encoding="utf-8").read()
    step = wf[wf.index("- name: Install the enrich / report / preview dependencies"):]
    step = step[:step.index("- name: Enrich --skip-raycast")]
    assert "run: pip install -r pipeline/requirements.txt -r pipeline/requirements-enrich.txt" in step


def test_enrich_stops_when_the_lookup_is_not_installed(monkeypatch):
    from pipeline import enrich
    from pipeline.tests.test_break_type_provenance import _enrich

    def missing(lat, lng):
        raise ModuleNotFoundError("No module named 'timezonefinder'")
    monkeypatch.setattr(enrich, "time_zone_for", missing)
    with pytest.raises(ModuleNotFoundError):
        _enrich({"name": "Mexico Beach", "lat": 29.930837, "lng": -85.402222})
