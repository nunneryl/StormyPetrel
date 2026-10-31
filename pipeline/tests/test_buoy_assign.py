"""pipeline/buoy_assign.py (sw1-raycast mode buoy-assign): the buoy rule's choice and its reasons.

Stand-in world: a spot at (30.0, -80.0), region South Carolina (cap 150 km, the east-coast
default), and wave stations due north of it. On the pipeline's sphere (EARTH_RADIUS_M = 6371 km)
one degree of latitude is 111.1949 km, so, worked by hand:

    0.1 deg  11.12 km     0.2 deg  22.24 km     0.3 deg  33.36 km     0.4 deg  44.48 km
    0.5 deg  55.60 km     1.4 deg 155.67 km     2.0 deg 222.39 km

Line of sight is a stand-in: a station is blocked when its id starts with "x".
"""
from __future__ import annotations

import pytest

from pipeline import buoy_assign as A
from pipeline.enrichment import buoys as B

SPOT = {"name": "Stand-in", "lat": 30.0, "lng": -80.0, "region_hint": "South Carolina"}


def st(sid, dlat):
    return {"id": sid, "lat": 30.0 + dlat, "lng": -80.0, "name": sid}


def los(_spot, station, _land):
    return not station["id"].startswith("x")


def sfa(spot=SPOT):
    return {**spot, "_algo_lat": spot["lat"], "_algo_lng": spot["lng"]}


# --------------------------------------------------------------------------- #
# diagnose: the rule's steps and the reason when nothing passes                #
# --------------------------------------------------------------------------- #

def test_the_first_clear_station_within_the_cap_is_the_choice():
    d = A.diagnose(sfa(), [st("w3", 0.3), st("x1", 0.1), st("w2", 0.2), st("w9", 2.0)],
                   None, 150, los=los)
    assert d["choice"] == "w2" and d["choice_km"] == 22.24 and d["fallbacks"] == ["w3"]
    assert d["reason"] is None
    assert d["candidates"] == [
        {"id": "x1", "km": 11.12, "within_cap": True, "los": False},
        {"id": "w2", "km": 22.24, "within_cap": True, "los": True},
        {"id": "w3", "km": 33.36, "within_cap": True, "los": True},
        {"id": "w9", "km": 222.39, "within_cap": False, "los": None},   # LOS not asked past the cap
    ]


def test_at_most_three_fallbacks():
    stations = [st("w1", 0.1), st("w2", 0.2), st("w3", 0.3), st("w4", 0.4), st("w5", 0.5)]
    d = A.diagnose(sfa(), stations, None, 150, los=los)
    assert d["choice"] == "w1" and d["fallbacks"] == ["w2", "w3", "w4"]


def test_nothing_within_the_cap_is_beyond_cap():
    d = A.diagnose(sfa(), [st("w8", 1.4), st("w9", 2.0)], None, 150, los=los)
    assert (d["choice"], d["reason"]) == (None, "beyond_cap")
    assert [c["km"] for c in d["candidates"]] == [155.67, 222.39]


def test_within_the_cap_but_all_blocked_is_no_line_of_sight():
    d = A.diagnose(sfa(), [st("x1", 0.1), st("x2", 0.2), st("w9", 2.0)], None, 150, los=los)
    assert (d["choice"], d["reason"]) == (None, "no_line_of_sight")


def test_only_the_ten_nearest_are_looked_at():
    # Ten blocked stations 0.01..0.10 deg away, and a clear one at 0.3: the rule never reaches it.
    stations = [st(f"x{i}", i / 100) for i in range(1, 11)] + [st("w3", 0.3)]
    d = A.diagnose(sfa(), stations, None, 150, los=los)
    assert (d["choice"], d["reason"]) == (None, "no_line_of_sight")
    assert len(d["candidates"]) == 10 and "w3" not in [c["id"] for c in d["candidates"]]


def test_no_station_list_is_no_wave_stations():
    d = A.diagnose(sfa(), [], None, 150, los=los)
    assert (d["choice"], d["reason"], d["candidates"]) == (None, "no_wave_stations", [])


def test_the_distance_is_from_the_seaward_adjusted_point():
    # The rule runs from _algo_lat/_algo_lng; 0.1 deg south of the spot, w2 is 0.3 deg away.
    d = A.diagnose({**SPOT, "_algo_lat": 29.9, "_algo_lng": -80.0}, [st("w2", 0.2)], None, 150,
                   los=los)
    assert d["choice_km"] == 33.36


# --------------------------------------------------------------------------- #
# scope: the spots db_import drops, and the spots with no buoy                  #
# --------------------------------------------------------------------------- #

def test_scope_mirrors_db_imports_check():
    snapshot = {"b1": {"lat": 30.2, "lng": -80.0}}          # 22.24 km north of every spot below
    roster = [
        {"name": "ok", "lat": 30.0, "lng": -80.0, "nearest_buoy_id": "B1", "nearest_buoy_dist_km": 22.24},
        {"name": "edge", "lat": 30.0, "lng": -80.0, "nearest_buoy_id": "b1", "nearest_buoy_dist_km": 17.24},
        {"name": "off", "lat": 30.0, "lng": -80.0, "nearest_buoy_id": "b1", "nearest_buoy_dist_km": 17.2},
        {"name": "none", "lat": 30.0, "lng": -80.0, "nearest_buoy_id": None},
        {"name": "unknown", "lat": 30.0, "lng": -80.0, "nearest_buoy_id": "zz", "nearest_buoy_dist_km": 1.0},
    ]
    # The great-circle is 22.2389 km: 17.24 is 4.9989 km out, inside the 5 km tolerance; 17.2 is not.
    assert [(s["name"], g) for s, g in A.scope(roster, snapshot)] == [("off", "dropped"), ("none", "none")]


def test_scope_keeps_a_spot_exactly_at_the_tolerance():
    # A buoy ON the spot is exactly 0.0 km away, so a stored 5.0 is exactly 5 km out: db_import
    # NULLs only when the gap is MORE than the tolerance, so this spot is kept; 5.01 is dropped.
    snapshot = {"b0": {"lat": 30.0, "lng": -80.0}}
    roster = [{"name": "at", "lat": 30.0, "lng": -80.0, "nearest_buoy_id": "b0", "nearest_buoy_dist_km": 5.0},
              {"name": "past", "lat": 30.0, "lng": -80.0, "nearest_buoy_id": "b0", "nearest_buoy_dist_km": 5.01}]
    assert [(s["name"], g) for s, g in A.scope(roster, snapshot)] == [("past", "dropped")]


def test_the_real_roster_has_39_dropped_and_173_without_a_buoy():
    import json
    from pipeline import config
    from pipeline.enrichment.geodata import load_buoy_snapshot
    roster = json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())
    groups = [g for _s, g in A.scope(roster, load_buoy_snapshot())]
    assert (groups.count("dropped"), groups.count("none")) == (39, 173)


# --------------------------------------------------------------------------- #
# assess: the real compute_nearest_buoy, checked against diagnose               #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def world(monkeypatch):
    stations = [st("x1", 0.1), st("w2", 0.2), st("w3", 0.3)]
    monkeypatch.setattr(B, "load_ndbc_wave_stations", lambda: stations)
    monkeypatch.setattr(B, "load_land_index", lambda: object())
    monkeypatch.setattr(B, "_los_clear", los)
    monkeypatch.setattr(A, "seaward_adjust", lambda lat, lng, land: (lat, lng, False))
    B._build_kdtree.cache_clear()
    yield stations
    B._build_kdtree.cache_clear()


def test_assess_runs_the_real_rule_and_reports_a_change(world):
    snapshot = {"old1": {"lat": 30.5, "lng": -80.0}}         # 55.60 km, no longer a wave station
    spot = {**SPOT, "nearest_buoy_id": "old1", "nearest_buoy_dist_km": 19.3}
    r = A.assess(spot, "dropped", object(), world, snapshot)
    assert (r["choice"], r["choice_km"], r["fallbacks"]) == ("w2", 22.24, ["w3"])
    assert r["diagnose_agrees"] is True and r["changes"] is True and r["reason"] is None
    assert r["cap_km"] == 150
    assert r["current"] == {"id": "old1", "stored_km": 19.3, "snapshot_km": 55.6,
                            "km_from_rule_point": 55.6, "is_wave_station": False,
                            "within_cap": True, "los": True}


def test_assess_same_buoy_is_not_a_change(world):
    spot = {**SPOT, "nearest_buoy_id": "w2", "nearest_buoy_dist_km": 22.24}
    r = A.assess(spot, "dropped", object(), world, {"w2": {"lat": 30.2, "lng": -80.0}})
    assert (r["choice"], r["changes"], r["current"]["is_wave_station"]) == ("w2", False, True)


def test_assess_a_spot_with_no_buoy_and_nothing_in_reach(world):
    far = {**SPOT, "lat": 20.0, "region_hint": "Puerto Rico"}   # 10 deg south: 1111.95 km and more
    r = A.assess(far, "none", object(), world, {})
    assert (r["choice"], r["reason"], r["changes"], r["current"], r["cap_km"]) == \
        (None, "beyond_cap", False, None, 250)


def test_summary_counts(world):
    rows = [A.assess({**SPOT, "nearest_buoy_id": "w2", "nearest_buoy_dist_km": 30.0}, "dropped",
                     object(), world, {}),
            A.assess({**SPOT, "nearest_buoy_id": "w3", "nearest_buoy_dist_km": 30.0}, "dropped",
                     object(), world, {}),
            A.assess(SPOT, "none", object(), world, {}),
            A.assess({**SPOT, "lat": 20.0}, "none", object(), world, {})]
    assert A.summarise(rows) == {
        "dropped": {"spots": 2, "rule_picks_a_buoy": 2, "changes": 1, "same_buoy": 1,
                    "no_buoy": {"beyond_cap": 0, "no_line_of_sight": 0, "no_wave_stations": 0},
                    "diagnose_disagrees": 0},
        "none": {"spots": 2, "rule_picks_a_buoy": 1, "changes": 1, "same_buoy": 0,
                 "no_buoy": {"beyond_cap": 1, "no_line_of_sight": 0, "no_wave_stations": 0},
                 "diagnose_disagrees": 0}}


def test_the_caps_are_the_rules():
    assert A.caps() == {
        "by_region": {"California": 150, "Oregon": 200, "Washington": 200, "Hawaii": 150,
                      "Puerto Rico": 250, "Texas": 350, "Louisiana": 350, "Mississippi": 350,
                      "Alabama": 350, "Great Lakes": 100},
        "gulf_florida_west_of_83W": 350, "east_coast_default": 150, "default": 150}


def test_use_wave_files_points_the_rule_at_the_given_files(tmp_path):
    from pipeline.enrichment import geodata as G
    saved = (G.NDBC_STATIONS_XML, G.NDBC_LATEST_OBS_TXT)
    xml = tmp_path / "stations.xml"
    obs = tmp_path / "obs.txt"
    xml.write_text('<stations><station id="41076" lat="32.536" lon="-79.659" name="CHR60WAVE"/>'
                   '<station id="chts1" lat="32.78" lon="-79.92" name="Charleston"/></stations>')
    obs.write_text("#STN LAT LON WVHT\n#text deg deg m\n41076 32.536 -79.659 1.2\n"
                   "CHTS1 32.78 -79.92 MM\n")
    obs2 = tmp_path / "obs2.txt"
    obs2.write_text("#STN LAT LON WVHT\n#text deg deg m\n41076 32.536 -79.659 MM\n"
                    "CHTS1 32.78 -79.92 0.4\n")
    try:
        got = A.use_wave_files(str(xml), str(obs))
        assert got == [{"id": "41076", "lat": 32.536, "lng": -79.659, "name": "CHR60WAVE"}]
        # A second pair of files, where only the other station reports waves: reloaded, not cached.
        got = A.use_wave_files(str(xml), str(obs2))
        assert got == [{"id": "chts1", "lat": 32.78, "lng": -79.92, "name": "Charleston"}]
    finally:
        A.use_wave_files(*saved)


def test_assess_runs_from_the_seaward_adjusted_point(world, monkeypatch):
    # A spot on land, moved 0.1 deg south by seaward_adjust: w2 is then 0.3 deg away.
    monkeypatch.setattr(A, "seaward_adjust", lambda lat, lng, land: (lat - 0.1, lng, True))
    r = A.assess(SPOT, "none", object(), world, {})
    assert (r["choice"], r["choice_km"], r["seaward_adjusted"], r["rule_point"]) == \
        ("w2", 33.36, True, [29.9, -80.0])


def test_the_command_end_to_end_on_stand_in_files(tmp_path, monkeypatch, capsys):
    """main() as the workflow runs it: a fresh and a cached pair of NDBC files, the real rule."""
    import json
    from pipeline.enrichment import geodata as G

    def files(tag, reporting):
        xml = tmp_path / f"{tag}.xml"
        obs = tmp_path / f"{tag}.txt"
        xml.write_text("<stations>" + "".join(
            f'<station id="{i}" lat="{30 + d}" lon="-80.0" name="{i}"/>'
            for i, d in (("w2", 0.2), ("w3", 0.3))) + "</stations>")
        obs.write_text("#STN LAT LON WVHT\n#text deg deg m\n" + "".join(
            f"{i.upper()} 0 0 {'1.0' if i in reporting else 'MM'}\n" for i in ("w2", "w3")))
        return xml, obs

    fresh_xml, fresh_obs = files("fresh", {"w2", "w3"})
    cached_xml, cached_obs = files("cached", {"w3"})          # w2 was not reporting waves then
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps([
        {**SPOT, "name": "Dropped", "nearest_buoy_id": "w3", "nearest_buoy_dist_km": 10.0},
        {**SPOT, "name": "Bare", "nearest_buoy_id": None},
        {**SPOT, "name": "Fine", "nearest_buoy_id": "w3", "nearest_buoy_dist_km": 33.36}]))
    saved = (G.NDBC_STATIONS_XML, G.NDBC_LATEST_OBS_TXT)
    monkeypatch.setattr(G, "load_buoy_snapshot", lambda: {"w3": {"lat": 30.3, "lng": -80.0}})
    monkeypatch.setattr(G, "load_land_index", lambda: object())
    monkeypatch.setattr(B, "load_land_index", lambda: object())
    monkeypatch.setattr(B, "_los_clear", los)
    monkeypatch.setattr(A, "seaward_adjust", lambda lat, lng, land: (lat, lng, False))
    out = tmp_path / "out.json"
    try:
        A.use_wave_files(cached_xml, cached_obs)
        A.main(["--roster", str(roster), "--fresh-xml", str(fresh_xml), "--fresh-obs",
                str(fresh_obs), "--out", str(out)])
    finally:
        A.use_wave_files(*saved)
    rep = json.loads(out.read_text())
    assert rep["scope"] == {"dropped": 1, "none": 1}
    assert list(rep["lists"]) == ["fresh", "cached"]
    fresh = {r["name"]: r for r in rep["lists"]["fresh"]["spots"]}
    cached = {r["name"]: r for r in rep["lists"]["cached"]["spots"]}
    assert (fresh["Dropped"]["choice"], fresh["Dropped"]["choice_km"], fresh["Dropped"]["changes"]) == \
        ("w2", 22.24, True)
    assert (cached["Dropped"]["choice"], cached["Dropped"]["choice_km"], cached["Dropped"]["changes"]) == \
        ("w3", 33.36, False)
    assert (fresh["Bare"]["choice"], cached["Bare"]["choice"]) == ("w2", "w3")
    assert rep["lists"]["fresh"]["wave_stations"] == 2 and rep["lists"]["cached"]["wave_stations"] == 1
    assert all(r["diagnose_agrees"] for L in rep["lists"].values() for r in L["spots"])
    printed = capsys.readouterr().out
    assert "[dropped] Dropped (South Carolina, cap 150): w3 stored 10.0 real 33.36 -> " \
           "w2 22.24 km fb ['w3']  CHANGES" in printed
