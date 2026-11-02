"""The buoy rule counts a station that reported a wave height in the last 7 days.

pipeline/ndbc_recent_wave.py lists them from each station's realtime2 file;
geodata.load_ndbc_wave_stations adds them to latest_obs.txt's one moment. sw1-raycast run 64's
fresh list left out 44084, which was reporting that day; this is the fix.

"Now" in these tests is 2026-11-02 20:00Z, so the 7-day window starts 2026-10-26 20:00Z.
Every expected value is written out by hand.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pipeline import buoy_assign as A
from pipeline import ndbc_recent_wave as RW
from pipeline.enrichment import geodata as G

NOW = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)

HEADER = ("#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
          "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n")


def rt(*rows):
    """A realtime2 file: rows of (YYYY, MM, DD, hh, mm, WVHT or 'MM'), newest first."""
    return HEADER + "".join(f"{y} {mo:02d} {d:02d} {h:02d} {mi:02d}  MM   MM   MM  {w}  MM  MM  MM  MM  MM  MM  MM  MM  MM  MM\n"
                            for y, mo, d, h, mi, w in rows)


LIVE = rt((2026, 11, 2, 13, 50, "1.2"), (2026, 11, 2, 13, 20, "MM"), (2026, 10, 30, 6, 0, "0.9"))
WEATHER_ONLY = rt((2026, 11, 2, 18, 30, "MM"), (2026, 10, 24, 15, 0, "1.5"))     # like 42358
SILENT = rt((2026, 9, 28, 19, 55, "2.1"))                                         # like 46453
EDGE = rt((2026, 10, 26, 20, 0, "0.5"))                                           # on the window's start


# --------------------------------------------------------------------------- #
# Reading a realtime2 file                                                     #
# --------------------------------------------------------------------------- #

def test_parse_reads_time_and_wave_height():
    got = RW.parse_realtime2(LIVE)
    assert got == [(datetime(2026, 11, 2, 13, 50, tzinfo=timezone.utc), 1.2),
                   (datetime(2026, 11, 2, 13, 20, tzinfo=timezone.utc), None),
                   (datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc), 0.9)]


def test_a_file_with_no_wave_column_parses_to_nothing():
    assert RW.parse_realtime2("#YY MM DD hh mm WSPD\n2026 11 02 13 50 5.0\n") == []


@pytest.mark.parametrize("text, want", [
    (LIVE, {"newest_row": "2026-11-02T13:50:00+00:00", "last_wave": "2026-11-02T13:50:00+00:00",
            "wave_rows_in_window": 2, "recent": True}),
    (WEATHER_ONLY, {"newest_row": "2026-11-02T18:30:00+00:00", "last_wave": "2026-10-24T15:00:00+00:00",
                    "wave_rows_in_window": 0, "recent": False}),
    (SILENT, {"newest_row": "2026-09-28T19:55:00+00:00", "last_wave": "2026-09-28T19:55:00+00:00",
              "wave_rows_in_window": 0, "recent": False}),
    (EDGE, {"newest_row": "2026-10-26T20:00:00+00:00", "last_wave": "2026-10-26T20:00:00+00:00",
            "wave_rows_in_window": 1, "recent": True}),                # exactly 7 days old counts
])
def test_the_7_day_summary(text, want):
    assert RW.wave_summary(RW.parse_realtime2(text), NOW, 7) == want


def test_a_wave_one_minute_older_than_the_window_does_not_count():
    old = rt((2026, 10, 26, 19, 59, "0.5"))
    assert RW.wave_summary(RW.parse_realtime2(old), NOW, 7)["recent"] is False


# --------------------------------------------------------------------------- #
# Candidates and the list                                                      #
# --------------------------------------------------------------------------- #

def test_candidates_are_the_stations_within_a_spots_cap():
    # 0.2 deg north is 22.24 km and 0.3 deg is 33.36 km (1 deg = 111.1949 km on a 6371 km sphere).
    spots = [{"name": "S", "lat": 30.0, "lng": -80.0}]
    stations = [{"id": "a", "lat": 30.2, "lng": -80.0}, {"id": "b", "lat": 30.3, "lng": -80.0},
                {"id": "c", "lat": 30.1, "lng": -80.0}]
    assert RW.candidates(spots, stations, lambda s: 30) == ["a", "c"]
    assert RW.candidates(spots, stations, lambda s: 22.0) == ["c"]


def test_build_lists_only_stations_with_a_recent_wave_height():
    files = {"46041": LIVE, "42358": WEATHER_ONLY, "46453": SILENT, "gone1": None}

    def fetch(sid):
        if sid == "flaky":
            raise OSError("timed out")
        return files[sid]
    got = RW.build(["46041", "42358", "46453", "gone1", "flaky"], NOW, 7, fetch=fetch, pace_s=0)
    assert got["stations"] == ["46041"]
    assert (got["generated_at"], got["days"]) == ("2026-11-02T20:00:00+00:00", 7)
    assert got["checked"]["gone1"] == {"no_file": True, "recent": False}
    assert got["checked"]["flaky"] == {"error": "OSError: timed out"}       # never listed on a guess
    assert got["checked"]["42358"]["last_wave"] == "2026-10-24T15:00:00+00:00"


# --------------------------------------------------------------------------- #
# The loader counts them                                                       #
# --------------------------------------------------------------------------- #

def _write(path, generated_at, days, ids):
    path.write_text(json.dumps({"generated_at": generated_at, "days": days, "stations": ids}))


def test_recent_wave_ids_reads_a_fresh_list(tmp_path, monkeypatch):
    f = tmp_path / "recent.json"
    _write(f, "2026-11-02T08:00:00+00:00", 7, ["46041", "44084"])
    monkeypatch.setattr(G, "NDBC_RECENT_WAVE_JSON", f)
    assert G.recent_wave_ids(now=NOW) == {"46041", "44084"}


def test_a_list_older_than_its_window_is_ignored(tmp_path, monkeypatch):
    f = tmp_path / "recent.json"
    _write(f, "2026-10-26T19:59:00+00:00", 7, ["46041"])              # 7 days and a minute old
    monkeypatch.setattr(G, "NDBC_RECENT_WAVE_JSON", f)
    assert G.recent_wave_ids(now=NOW) == set()
    _write(f, "2026-10-26T20:00:00+00:00", 7, ["46041"])              # exactly 7 days: still used
    assert G.recent_wave_ids(now=NOW) == {"46041"}


@pytest.mark.parametrize("content", [None, "not json", '{"days": 7}'])
def test_a_missing_or_broken_list_is_no_list(tmp_path, monkeypatch, content):
    f = tmp_path / "recent.json"
    if content is not None:
        f.write_text(content)
    monkeypatch.setattr(G, "NDBC_RECENT_WAVE_JSON", f)
    assert G.recent_wave_ids(now=NOW) == set()


def test_the_rule_counts_a_station_that_missed_the_moment(tmp_path):
    """44084 has 'MM' in latest_obs.txt, as on 2026-10-31, but reported in the last 7 days."""
    xml = tmp_path / "stations.xml"
    obs = tmp_path / "obs.txt"
    recent = tmp_path / "recent.json"
    xml.write_text('<stations><station id="44084" lat="38.6" lon="-74.8" name="Ocean City"/>'
                   '<station id="44097" lat="40.967" lon="-71.124" name="Block Island"/></stations>')
    obs.write_text("#STN LAT LON WVHT\n#text deg deg m\n44084 38.6 -74.8 MM\n44097 40.967 -71.124 0.8\n")
    _write(recent, datetime.now(timezone.utc).isoformat(), 7, ["44084"])
    saved = (G.NDBC_STATIONS_XML, G.NDBC_LATEST_OBS_TXT, G.NDBC_RECENT_WAVE_JSON)
    try:
        without = [s["id"] for s in A.use_wave_files(xml, obs)]
        with_recent = [s["id"] for s in A.use_wave_files(xml, obs, recent)]
    finally:
        A.use_wave_files(*saved)
    assert without == ["44097"]
    assert with_recent == ["44084", "44097"]


# --------------------------------------------------------------------------- #
# buoy_assign --spots                                                          #
# --------------------------------------------------------------------------- #

def test_pick_takes_names_or_slugs():
    roster = [{"name": "Mecox Beach"}, {"name": "La Push First Beach"}, {"name": "Other"}]
    assert [s["name"] for s, g in A.pick(roster, ["mecox-beach", " La Push First Beach", ""])] == \
        ["Mecox Beach", "La Push First Beach"]
    assert {g for _s, g in A.pick(roster, ["Mecox Beach"])} == {"held"}


def test_the_run_65_command_end_to_end(tmp_path, monkeypatch):
    """buoy_assign --spots ... --recent-days 7 on stand-in files and the real rule.

    w2 is 0.2 deg north of the spot (22.24 km) and missed latest_obs's moment ('MM') but reported
    within 7 days; w3 is 0.3 deg north (33.36 km) and reported at the moment. Counting the last
    7 days the rule picks w2; at the moment only, w3."""
    from pipeline.enrichment import buoys as B
    xml, obs = tmp_path / "s.xml", tmp_path / "o.txt"
    xml.write_text('<stations><station id="w2" lat="30.2" lon="-80.0" name="w2"/>'
                   '<station id="w3" lat="30.3" lon="-80.0" name="w3"/></stations>')
    obs.write_text("#STN LAT LON WVHT\n#text deg deg m\nW2 0 0 MM\nW3 0 0 1.0\n")
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps([
        {"name": "Held Spot", "lat": 30.0, "lng": -80.0, "region_hint": "South Carolina",
         "nearest_buoy_id": None},
        {"name": "Not Asked", "lat": 30.0, "lng": -80.0, "region_hint": "South Carolina",
         "nearest_buoy_id": None}]))
    now = datetime.now(timezone.utc)
    fresh_row = (now.year, now.month, now.day, 0, 0, "1.1")
    monkeypatch.setattr(RW, "fetch_realtime2", lambda sid: rt(fresh_row) if sid == "w2" else rt(fresh_row[:5] + ("MM",)))
    monkeypatch.setattr(G, "load_buoy_snapshot", lambda: {})
    monkeypatch.setattr(G, "load_land_index", lambda: object())
    monkeypatch.setattr(B, "load_land_index", lambda: object())
    monkeypatch.setattr(B, "_los_clear", lambda spot, st, land: True)
    monkeypatch.setattr(A, "seaward_adjust", lambda lat, lng, land: (lat, lng, False))
    monkeypatch.setattr(RW.time, "sleep", lambda s: None)
    saved = (G.NDBC_STATIONS_XML, G.NDBC_LATEST_OBS_TXT, G.NDBC_RECENT_WAVE_JSON)
    out = tmp_path / "buoy_assign.json"
    try:
        A.use_wave_files(xml, obs)                      # the "cached" pair, for this test
        A.main(["--roster", str(roster), "--spots", "Held Spot", "--recent-days", "7",
                "--fresh-xml", str(xml), "--fresh-obs", str(obs), "--out", str(out)])
    finally:
        A.use_wave_files(*saved)
        G.NDBC_RECENT_WAVE_JSON = saved[2]
    rep = json.loads(out.read_text())
    assert rep["scope"] == {"held": 1}
    assert list(rep["lists"]) == ["fresh+7d", "cached+7d", "fresh", "cached"]
    pick = {k: (L["spots"][0]["choice"], L["spots"][0]["choice_km"]) for k, L in rep["lists"].items()}
    assert pick == {"fresh+7d": ("w2", 22.24), "cached+7d": ("w2", 22.24),
                    "fresh": ("w3", 33.36), "cached": ("w3", 33.36)}
    assert rep["recent_wave"]["stations"] == ["w2"]
    assert json.loads((tmp_path / "buoy_assign_recent_wave.json").read_text())["stations"] == ["w2"]
