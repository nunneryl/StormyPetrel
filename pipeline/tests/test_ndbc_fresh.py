"""pipeline/ndbc_fresh.py: NDBC's downloaded files are checked before they replace the cached ones."""
from __future__ import annotations

from pipeline import ndbc_fresh as F

XML3 = ('<stations><station id="a" lat="1.5" lon="-2.5"/><station id="b" lat="3" lon="4"/>'
        '<station id="c" lat="5" lon="6"/><station id="d" lat="" lon="6"/></stations>\n')
OBS2 = "#STN LAT LON WVHT\n#text deg deg m\na 1 2 MM\nb 3 4 1.1\n\nshort 1\n"


def _files(tmp_path, xml, obs):
    x, o = tmp_path / "s.xml", tmp_path / "o.txt"
    x.write_text(xml)
    o.write_text(obs)
    return x, o


def test_the_floors():
    # 1352 stations and 784 observation rows on 2026-11-03.
    assert (F.MIN_STATIONS, F.MIN_OBS_ROWS) == (500, 200)


def test_files_at_the_floor_pass(tmp_path):
    # Three stations have coordinates (d's lat is empty); two rows are full rows ("short 1" is not).
    x, o = _files(tmp_path, XML3, OBS2)
    assert F.check(x, o, min_stations=3, min_rows=2) == []


def test_one_station_short(tmp_path):
    x, o = _files(tmp_path, XML3, OBS2)
    assert F.check(x, o, min_stations=4, min_rows=2) == [
        "activestations.xml lists 3 stations with coordinates (at least 4 expected)"]


def test_one_row_short(tmp_path):
    x, o = _files(tmp_path, XML3, OBS2)
    assert F.check(x, o, min_stations=3, min_rows=3) == [
        "latest_obs.txt has 2 station rows (at least 3 expected)"]


def test_an_empty_download(tmp_path):
    x, o = _files(tmp_path, "", "")
    got = F.check(x, o, min_stations=3, min_rows=2)
    assert len(got) == 2
    assert got[0].startswith("activestations.xml is not readable XML (ParseError")
    assert got[1] == "latest_obs.txt has no header row naming STN and WVHT"


def test_a_maintenance_page(tmp_path):
    page = "<html><body>Service unavailable</body></html>\n"
    x, o = _files(tmp_path, page, page)
    assert F.check(x, o, min_stations=3, min_rows=2) == [
        "activestations.xml lists 0 stations with coordinates (at least 3 expected)",
        "latest_obs.txt has no header row naming STN and WVHT"]


def test_a_header_without_wave_height(tmp_path):
    x, o = _files(tmp_path, XML3, "#STN LAT LON WSPD\na 1 2 3\nb 3 4 5\n")
    assert F.check(x, o, min_stations=3, min_rows=2) == [
        "latest_obs.txt has no header row naming STN and WVHT"]


def test_missing_files(tmp_path):
    got = F.check(tmp_path / "none.xml", tmp_path / "none.txt")
    assert got[0].startswith("activestations.xml is not readable XML (FileNotFoundError")
    assert got[1].startswith("latest_obs.txt is not readable (FileNotFoundError")


def test_the_command_reports_each_problem_and_exits_1(tmp_path, capsys):
    page = "<html>Service unavailable</html>\n"
    x, o = _files(tmp_path, page, page)
    assert F.main(["--xml", str(x), "--obs", str(o)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == ["::error::NDBC activestations.xml lists 0 stations with coordinates (at least 500 expected).",
                   "::error::NDBC latest_obs.txt has no header row naming STN and WVHT."]
