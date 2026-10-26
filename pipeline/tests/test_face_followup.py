"""The run-49 follow-up (pipeline/face_followup.py), offline. Every expected value is worked by
hand in the comment beside it; none is read back from the function under test.

Shared arithmetic, at 36 N:
  cos 36 = 0.809017; one degree of longitude on the flat-earth scale = 111.320 x 0.809017
  = 90.0598 km, so 0.01 deg of longitude = 0.900598 km; one degree of latitude = 110.574 km.
  2026-10-25 00:00 UTC = 1,792,886,400 s (H0).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pipeline import face_followup as F

H0 = 1792886400


# --------------------------------------------------------------------------- #
# Parsers                                                                      #
# --------------------------------------------------------------------------- #
def test_parse_stations():
    xml = ('<stations><station id="42098" lat="27.59" lon="-82.93" name="Egmont" />'
           '<station id="41110" lat="34.14" lon="-77.72" name="Masonboro"/>'
           '<station id="xyz" name="no position" /></stations>')
    assert F.parse_stations(xml) == {"42098": (27.59, -82.93), "41110": (34.14, -77.72)}


def test_parse_ndbc_realtime():
    # 2026-10-26 16:26 = H0 + 1 day (86,400) + 16 h 26 min (59,160) = 1,792,972,800 + 59,160
    #                  = 1,793,031,960
    text = ("#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD\n"
            "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT\n"
            "2026 10 26 16 26  MM   MM   MM   2.3     8   5.7  15\n"
            "2026 10 26 15 56  MM   MM   MM    MM    MM    MM  MM\n")
    assert F.parse_ndbc_realtime(text) == [(1793031960.0, 2.3, 8.0), (1793030160.0, None, None)]


def test_nearest_buoy():
    # From (27.75, -82.77): Egmont 0.16 deg south and 0.16 deg west. 0.16 x 111.195 = 17.79 km
    # north-south; 0.16 x 111.195 x cos 27.67 (0.88563) = 15.76 km east-west; about 23.8 km.
    # West Tampa is about 2 deg away.
    pos = {"42098": (27.59, -82.93), "42036": (28.5, -84.5), "99999": (27.75, -82.77)}
    bid, km = F.nearest_buoy(27.75, -82.77, pos, ids=("42098", "42036"))
    assert bid == "42098" and round(km) == 24
    assert F.nearest_buoy(27.75, -82.77, {}, ids=("42098",)) is None


def test_offset_point():
    lat, lng = F.offset_point(36.0, -75.7, 0.0, 1.10574)          # 0.01 deg of latitude north
    assert (round(lat, 6), round(lng, 6)) == (36.01, -75.7)
    lat, lng = F.offset_point(36.0, -75.7, 90.0, 0.900598)        # 0.01 deg of longitude east
    assert (round(lat, 6), round(lng, 6)) == (36.0, -75.69)


# --------------------------------------------------------------------------- #
# The coastline classes, on drawn coasts                                       #
# --------------------------------------------------------------------------- #
class _Land:
    def __init__(self, pred):
        self.pred = pred

    def contains(self, lats, lngs):
        return [self.pred(a, b) for a, b in zip(lats, lngs)]


STRAIGHT = _Land(lambda lat, lng: lng < -75.70)                  # land west, sea east
LAGOON = _Land(lambda lat, lng: lng < -75.70 or -75.68 < lng < -75.67)   # + a barrier island
INLET = _Land(lambda lat, lng: lng < -75.70 or (abs(lat - 36.0) > 0.005 and lng < -75.68))


def test_open_ocean_off_a_straight_coast():
    # From (36.0, -75.69), 0.9006 km off a coast running north-south. A ray at bearing b
    # reaches the coast when k |sin b| >= 0.9006 km, within 10 km for |sin b| >= 0.09: every
    # ray from 190 to 350 deg. 0 to 180 stay at sea (sin >= 0), 19 rays: 190 deg wide.
    assert F.classify(STRAIGHT, 36.0, -75.69, 90.0) == {
        "class": "open ocean", "facing_land_km": None, "open_width_deg": 190}


def test_the_facing_picks_its_own_ray():
    # Facing exactly 180: that ray stays at sea (sin 180 is +1.2e-16) and its sector is the
    # same 0..180, 190 deg. The ray one step on (190) is on land 5.19 km out.
    assert F.classify(STRAIGHT, 36.0, -75.69, 180.0) == {
        "class": "open ocean", "facing_land_km": None, "open_width_deg": 190}


def test_a_point_on_land():
    assert F.classify(STRAIGHT, 36.0, -75.71, 90.0) == {
        "class": "on land", "facing_land_km": 0.0, "open_width_deg": 0}


def test_behind_land():
    # In the lagoon at -75.69, the island begins 0.01 deg east: 0.900598 km. Samples are every
    # 0.1 km; 0.9 km reaches -75.689993 (still lagoon), 1.0 km is on the island.
    got = F.classify(LAGOON, 36.0, -75.69, 90.0)
    assert got["class"] == "behind land" and got["facing_land_km"] == 1.0


def test_pass_or_inlet():
    # In a channel |lat - 36| <= 0.005 between land west of -75.68 (and -75.70 in the channel).
    # A ray leaves the channel when k cos b / 110.574 > 0.005, i.e. k > 0.5529 / |cos b|, and
    # is on land there if it has not yet gone 0.01 deg east (k sin b < 0.9006 km).
    #   50 deg: leaves at 0.860 km, sample 0.9 km is at lat 36.00523, lng -75.68234: land
    #   60 deg: leaves at 1.106 km, sample 1.2 km is at lng -75.67846, already past -75.68: open
    # So 60..120 are open (by symmetry about the channel), 7 rays: 70 deg wide, under 90.
    assert F.classify(INLET, 36.0, -75.69, 90.0) == {
        "class": "pass/inlet", "facing_land_km": None, "open_width_deg": 70}


def test_open_all_round():
    assert F.classify(_Land(lambda a, b: False), 36.0, -75.69, 90.0)["open_width_deg"] == 360


def test_nearest_open_ocean_is_past_the_island():
    # From the lagoon at -75.69: the island runs to -75.67, 0.02 deg east = 1.8012 km. Rings
    # are 0.25 km apart; 1.75 km east reaches -75.67057 (on the island), 2.0 km reaches
    # -75.69 + 2 / 90.0598 = -75.667793: east of the island, where the facing ray is open and
    # the open sector is 0..180 (190 deg). Straight out (offset 0) is tried first.
    assert F.nearest_open_ocean(LAGOON, 36.0, -75.69, 90.0) == {
        "lat": 36.0, "lng": -75.66779, "km": 2.0, "bearing_deg": 90.0}


def test_nearest_open_ocean_none_within_the_search(monkeypatch):
    monkeypatch.setattr(F, "SEARCH_MAX_KM", 0.5)
    assert F.nearest_open_ocean(LAGOON, 36.0, -75.69, 90.0) is None


# --------------------------------------------------------------------------- #
# The seaward NWPS cell                                                        #
# --------------------------------------------------------------------------- #
def test_seaward_cell():
    # From (36.0, -75.70) facing east. 0.02 deg of longitude along the parallel is
    # 2 x 6371 x asin(cos 36 x sin 0.01 deg) = 6371 x 0.809017 x 3.490659e-4 = 1.7992 km;
    # 0.03 deg is 2.6988 km. The cell due north (1.11 km, 90 off the facing) is out of reach.
    cells = [
        (0, 0, 36.0, -75.69, lambda: False),     # nearest, 0.90 km, but dry
        (0, 1, 36.01, -75.70, lambda: True),     # 90 deg off the facing
        (0, 2, 36.0, -75.68, lambda: True),      # 1.80 km, wet
        (0, 3, 36.0, -75.67, lambda: True),      # 2.70 km, wet
    ]
    assert F.seaward_cell(cells, 36.0, -75.70, 90.0) == {
        "li": 0, "lj": 2, "lat": 36.0, "lng": -75.68, "km": 1.8, "bearing_deg": 90.0}
    # Not open ocean at -75.68: the next one out
    got = F.seaward_cell(cells, 36.0, -75.70, 90.0, is_open=lambda la, ln: ln > -75.675)
    assert (got["lj"], got["km"]) == (3, 2.7)
    assert F.seaward_cell(cells, 36.0, -75.70, 90.0, is_open=lambda la, ln: False) is None


def test_seaward_cell_within_30_deg():
    # A cell 0.01 deg north and 0.01 deg east: bearing atan2(0.9006, 1.1119) about 39 deg
    # (39 x 1 = off the facing by 51) is out; one 0.002 deg north and 0.02 deg east is about
    # 7 deg off: in.
    cells = [(0, 0, 36.01, -75.69, lambda: True), (0, 1, 36.002, -75.68, lambda: True)]
    assert F.seaward_cell(cells, 36.0, -75.70, 90.0)["lj"] == 1


def test_shortest_lead():
    c1, c2 = H0, H0 + 3600
    s1 = [{"valid_time": "2026-10-25T00:00:00Z", "hs": 1.0, "tp": 8.0},
          {"valid_time": "2026-10-25T01:00:00Z", "hs": 1.1, "tp": 8.0},
          {"valid_time": "2026-10-25T02:00:00Z", "hs": 1.2, "tp": 8.0}]
    s2 = [{"valid_time": "2026-10-25T00:00:00Z", "hs": 9.0, "tp": 8.0},   # before its cycle
          {"valid_time": "2026-10-25T01:00:00Z", "hs": 2.1, "tp": 9.0},
          {"valid_time": "2026-10-25T02:00:00Z", "hs": 2.2, "tp": 9.0},
          {"valid_time": "2026-10-25T03:00:00Z", "hs": None, "tp": 9.0}]
    # 00Z only c1 (lead 0); 01Z and 02Z the newer c2 (leads 0 and 1); 03Z has no height
    assert F.shortest_lead([(c2, s2), (c1, s1)]) == {
        H0: (1.0, 8.0, 0.0), H0 + 3600: (2.1, 9.0, 0.0), H0 + 7200: (2.2, 9.0, 1.0)}


def test_pairs_vs_and_banded():
    ours = {H0: (1.2,), H0 + 3600: (0.45,), H0 + 7200: (2.0,)}
    measured = {H0: (1.0, 9.0, 1), H0 + 3600: (0.5, 13.0, 1), H0 + 10800: (3.0, 9.0, 1)}
    p = F.pairs_vs(ours, measured)
    assert [(x["hour"], x["ours"], x["measured"], x["band"]) for x in p] == [
        (H0, 1.2, 1.0, "8-10 s"), (H0 + 3600, 0.45, 0.5, "12-14 s")]
    b = F.banded(p)
    # all: (0.2 - 0.05) / 2 = 0.075;  1.0 m is in 1.0-1.5, 0.5 m in 0.5-1.0
    assert b["all"]["n"] == 2 and b["all"]["bias_m"] == 0.075
    assert b["by_measured_height"]["1.0-1.5 m"]["bias_m"] == 0.2
    assert b["by_measured_height"]["0.5-1.0 m"]["bias_m"] == -0.05
    assert b["by_measured_height"]["0.0-0.5 m"] == {"n": 0}
    assert list(b["by_measured_period"]) == ["8-10 s", "12-14 s"]


def test_suspect_vs_buoy():
    rows = [{"valid_time": "2026-10-25T00:00:00+00:00", "hs": 0.4, "tp": 2.0},
            {"valid_time": "2026-10-25T01:00:00+00:00", "hs": 0.6, "tp": 2.5},
            {"valid_time": "2026-10-25T02:00:00+00:00", "hs": 0.9, "tp": 6.0},
            {"valid_time": "2026-10-25T03:00:00+00:00", "hs": None, "tp": 6.0}]
    buoy = {H0: (0.8, 4.0, 1), H0 + 3600: (1.0, 5.0, 1), H0 + 7200: (0.9, 2.0, 1)}
    # heights: ours 0.4, 0.6, 0.9 (median 0.6); buoy 0.8, 1.0, 0.9 (median 0.9)
    # ratios 0.5, 0.6, 1.0 -> median 0.6; periods ours 2.0, 2.5, 6.0 (median 2.5, 2 of 3 under
    # 3 s = 0.667), buoy 4.0, 5.0, 2.0 (median 4.0, 1 of 3 = 0.333)
    assert F.suspect_vs_buoy(rows, buoy) == {
        "paired_hours": 3, "our_hs_median_m": 0.6, "buoy_hs_median_m": 0.9, "hs_ratio_median": 0.6,
        "our_tp_median_s": 2.5, "buoy_dpd_median_s": 4.0, "our_share_under_3s": 0.667,
        "buoy_share_under_3s": 0.333}


def test_suspect_vs_buoy_with_nothing_paired():
    got = F.suspect_vs_buoy([], {H0: (1.0, 5.0, 1)})
    assert got["paired_hours"] == 0 and got["hs_ratio_median"] is None


def test_etopo_depth():
    csv_text = "latitude,longitude,z\ndegrees_north,degrees_east,\n36.19375,-75.74791666666667,-7.43439\n"
    assert F.etopo_depth_m(36.193, -75.7485, text=lambda url: csv_text) == 7.4

    def down(url):
        raise OSError("timeout")
    assert F.etopo_depth_m(36.193, -75.7485, text=down) is None


# --------------------------------------------------------------------------- #
# Sections, with fakes                                                         #
# --------------------------------------------------------------------------- #
class _Q:
    def __init__(self, fn):
        self.fn, self.f = fn, {}

    def select(self, c):
        self.f["select"] = c
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def gte(self, k, v):
        self.f["gte"] = v
        return self

    def lt(self, k, v):
        self.f["lt"] = v
        return self

    def order(self, *a):
        return self

    def range(self, a, b):
        self.f["range"] = (a, b)
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.fn(self.f)
        return r


class _Client:
    def __init__(self, ids, rows):
        self.ids, self.rows, self.calls = ids, rows, []

    def table(self, name):
        def fn(f):
            self.calls.append((name, dict(f)))
            if name == "spots":
                return [{"id": self.ids[f["name"]]}] if f["name"] in self.ids else []
            a, b = f["range"]
            return self.rows.get(f["spot_id"], [])[a:b + 1]
        return _Q(fn)


def test_section_suspects():
    roster = {"Spot A": {"lat": 27.75, "lng": -82.77}}
    pages = {F.NDBC_STATIONS: '<station id="42098" lat="27.59" lon="-82.93"/>',
             F.NDBC_REALTIME.format("42098"):
             "#YY MM DD hh mm WVHT DPD\n#yr mo dy hr mn m sec\n2026 10 25 00 00 0.8 4\n"}
    rows = {1: [{"valid_time": "2026-10-25T00:00:00+00:00", "hs": 0.4, "tp": 2.0}]}
    c = _Client({"Spot A": 1}, rows)
    since = datetime(2026, 10, 12, tzinfo=timezone.utc)
    until = datetime(2026, 10, 26, tzinfo=timezone.utc)
    (rec,) = F.section_suspects(c, roster, [{"spot": "Spot A", "state": "Florida"}], since, until,
                                text=pages.__getitem__)
    assert (rec["spot"], rec["buoy"], round(rec["buoy_km"]), rec["paired_hours"], rec["hs_ratio_median"],
            rec["our_tp_median_s"], rec["buoy_dpd_median_s"]) == ("Spot A", "42098", 24, 1, 0.5, 2.0, 4.0)
    fc = [f for n, f in c.calls if n == "forecasts"]
    assert fc[0]["source"] == "nwps" and fc[0]["gte"] == since.isoformat() and fc[0]["lt"] == until.isoformat()


class _Index:
    """A LandIndex of drawn boxes: the mainland west of -75.70, an island -75.68..-75.67."""

    def __init__(self, boxes):
        from shapely.geometry import box
        from shapely.strtree import STRtree
        self.polygons = [box(*b) for b in boxes]
        self.polygon_tree = STRtree(self.polygons)


def test_section_coastline_on_drawn_coasts():
    idx = _Index([(-76.5, 35.0, -75.70, 37.0), (-75.68, 35.0, -75.67, 37.0)])
    roster = {
        "Lagoon": {"lat": 36.0, "lng": -75.695, "orientation_deg": 90.0,
                   "nwps_node_lat": 36.0, "nwps_node_lng": -75.69},
        "No Node": {"lat": 36.0, "lng": -75.695, "orientation_deg": 90.0},
    }
    got = F.section_coastline(idx, roster, ["Lagoon", "No Node"], depth=lambda la, ln: 3.5)
    lag, none = got
    assert (lag["class"], lag["facing_land_km"], lag["node_depth_m"]) == ("behind land", 1.0, 3.5)
    # 0.005 deg of longitude from the spot: 0.5 x 0.8996 km = 0.45 km
    assert lag["node_km_from_spot"] == 0.45
    # From the spot at -75.695, the island ends 0.025 deg east = 2.2515 km: the 2.25 km ring
    # reaches -75.695 + 2.25 / 90.0598 = -75.670017 (island); 2.5 km reaches -75.667241.
    assert lag["nearest_open_ocean"] == {"lat": 36.0, "lng": -75.66724, "km": 2.5,
                                         "bearing_deg": 90.0, "depth_m": 3.5}
    assert none["class"] == "no baked node"


class _Var:
    def __init__(self, vals):
        import numpy as np
        self._v = np.ma.masked_invalid(np.array(vals, dtype=float))

    def __getitem__(self, k):
        return self._v[k]

    def __len__(self):
        return len(self._v)


class _DS:
    def __init__(self, **v):
        self.variables = {k: _Var(x) for k, x in v.items()}


def test_section_mop(monkeypatch):
    monkeypatch.setattr(F, "MOP_BUOYS", ("284",))
    roster = {"Doheny State Beach": {"name": "Doheny State Beach", "lat": 33.4611, "lng": -117.6859,
                                     "swell_window_source": "cdip_mop"},
              "Far": {"name": "Far", "lat": 34.0, "lng": -118.5, "swell_window_source": "cdip_mop"}}

    def open_nc(url):
        if "MOP_validation" in url:      # the third MOP hour is flagged bad (4)
            return _DS(waveTime=[float(H0), H0 + 3600.0, H0 + 7200.0], waveHs=[0.9, 1.6, 5.0],
                       waveTp=[14.0, 15.0, 15.0], waveFlagPrimary=[1, 1, 4])
        return _DS(waveTime=[float(H0), H0 + 3600.0, H0 + 7200.0], waveHs=[1.0, 2.0, 1.0],
                   waveTp=[14.5, 15.0, 15.0], waveFlagPrimary=[1, 1, 1],
                   metaDeployLatitude=[33.445], metaDeployLongitude=[-117.68])
    # our MOP-tier rows: face_ft_raw 2.6248 / 3.281 = 0.8 m at H0; a WW3-path row is not MOP
    rows = {7: [{"valid_time": "2026-10-25T00:00:00+00:00", "swell_source": "cdip_mop",
                 "face_ft_raw": 2.6248, "swell_tp": 14.0, "tp": 14.0},
                {"valid_time": "2026-10-25T01:00:00+00:00", "swell_source": "ww3",
                 "face_ft_raw": 9.0, "swell_tp": 14.0, "tp": 14.0}]}
    got = F.section_mop(_Client({"Doheny State Beach": 7}, rows), roster,
                        datetime(2026, 9, 7, tzinfo=timezone.utc), open_nc)["284"]
    # MOP at the buoy: 0.9 / 1.0 and 1.6 / 2.0 -> ratios 0.9, 0.8, median 0.85 (the flagged
    # third hour is not paired)
    assert got["mop_at_buoy"]["all"]["n"] == 2 and got["mop_at_buoy"]["all"]["ratio_median"] == 0.85
    assert got["mop_at_buoy"]["by_measured_height"]["1.5-99.0 m"]["ratio_median"] == 0.8
    o = got["our_mop_spot"]
    # Doheny is 1.9 km from the buoy (0.0161 deg north, 0.0059 deg west); 0.8 / 1.0 = 0.8
    assert (o["spot"], o["km"], o["all"]["n"], o["all"]["ratio_median"]) == ("Doheny State Beach", 1.9, 1, 0.8)


def test_section_mop_without_a_mop_spot_near_the_buoy(monkeypatch):
    monkeypatch.setattr(F, "MOP_BUOYS", ("284",))
    # "Far" at (34.0, -118.5) is about 85 km from the buoy: beyond MOP_SPOT_KM
    roster = {"Far": {"name": "Far", "lat": 34.0, "lng": -118.5, "swell_window_source": "cdip_mop"}}

    def open_nc(url):
        return _DS(waveTime=[float(H0)], waveHs=[1.0], waveTp=[14.0], waveFlagPrimary=[1],
                   metaDeployLatitude=[33.445], metaDeployLongitude=[-117.68])
    got = F.section_mop(_Client({"Far": 1}, {}), roster, datetime(2026, 9, 7, tzinfo=timezone.utc), open_nc)
    assert got["284"]["our_mop_spot"] is None and got["284"]["mop_at_buoy"]["all"]["n"] == 1


def test_section_mop_reports_an_unreadable_buoy(monkeypatch):
    monkeypatch.setattr(F, "MOP_BUOYS", ("153",))

    def open_nc(url):
        raise OSError("403 Forbidden")
    got = F.section_mop(_Client({}, {}), {}, datetime(2026, 9, 7, tzinfo=timezone.utc), open_nc)
    assert got["153"]["error"] == "OSError: 403 Forbidden"


def test_section_seaward(monkeypatch, tmp_path):
    from pipeline.forecast import nwps
    monkeypatch.setattr(F, "SEAWARD_SPOTS", (("Duck Pier", "frf-8m"),))
    roster = {"Duck Pier": {"lat": 36.0, "lng": -75.70, "orientation_deg": 90.0, "nwps_wfo": "mhx",
                            "nwps_node_lat": 36.01, "nwps_node_lng": -75.70}}
    monkeypatch.setattr(F, "list_all_cycles", lambda wfo: ("er", [("20261025", "00")]))
    downloaded = []

    def download(region, wfo, d, hh, path):
        downloaded.append((region, wfo, d, hh))
        path.write_bytes(b"x")
        return True
    monkeypatch.setattr(nwps, "_download_filtered", download)
    monkeypatch.setattr(nwps, "_open_grib_datasets", lambda path: ["ds"])
    monkeypatch.setattr(F, "nwps_cells", lambda ds, la, ln: [(0, 2, 36.0, -75.68, lambda: True)])

    def series(ds, lat, lng):
        # the node (north) reads low on both hours; the cell reads right but only at 00Z, so
        # only 00Z is compared, for both
        if lat > 36.005:
            return [{"valid_time": "2026-10-25T00:00:00Z", "hs": 0.5, "tp": 9.0},
                    {"valid_time": "2026-10-25T01:00:00Z", "hs": 0.4, "tp": 9.0}]
        return [{"valid_time": "2026-10-25T00:00:00Z", "hs": 0.9, "tp": 9.0}]
    monkeypatch.setattr(nwps, "_extract_time_series_from_datasets", series)
    monkeypatch.setattr(F, "frf_8m_hourly",
                        lambda a, b, o: ({H0: (1.0, 9.0, 1), H0 + 3600: (1.0, 9.0, 1)}, ["202610"]))
    got = F.section_seaward(roster, None, datetime(2026, 10, 21, tzinfo=timezone.utc),
                            datetime(2026, 10, 26, tzinfo=timezone.utc), tmp_path,
                            depth=lambda la, ln: 8.0)["Duck Pier"]
    assert downloaded == [("er", "mhx", "20261025", "00")] and not list(tmp_path.iterdir())
    assert (got["cycles_read"], got["hours"]) == (1, 1)
    # node 0.01 deg north: 1.1119 km at bearing 0
    assert (got["node"]["km"], got["node"]["bearing_deg"], got["node"]["depth_m"]) == (1.11, 0.0, 8.0)
    assert (got["seaward_cell"]["km"], got["seaward_cell"]["coastline_checked"]) == (1.8, False)
    assert (got["node_vs_measured"]["all"]["n"], got["node_vs_measured"]["all"]["ratio_median"]) == (1, 0.5)
    assert (got["cell_vs_measured"]["all"]["n"], got["cell_vs_measured"]["all"]["ratio_median"]) == (1, 0.9)


def test_write_csvs(tmp_path):
    import csv as _csv
    rep = {"suspects": [{"spot": "A", "buoy": "42098", "paired_hours": 3}],
           "coastline": [{"spot": "A", "class": "behind land", "node_lat": 36.0, "node_lng": -75.69,
                          "nearest_open_ocean": {"lat": 36.0, "lng": -75.66779, "km": 2.0,
                                                 "bearing_deg": 90.0, "depth_m": 4.0}},
                         {"spot": "B", "class": "no baked node", "nearest_open_ocean": None}]}
    F.write_csvs(rep, str(tmp_path))
    s = list(_csv.DictReader(open(tmp_path / "suspects_vs_buoys.csv")))
    assert s == [{"spot": "A", "buoy": "42098", "paired_hours": "3"}]
    c = list(_csv.DictReader(open(tmp_path / "coastline.csv")))
    assert (c[0]["class"], c[0]["open_km"], c[0]["open_depth_m"]) == ("behind land", "2.0", "4.0")
    assert c[1]["open_km"] == ""


def test_run48_inputs_are_on_the_branch():
    sus = F.load_suspects()
    assert len(sus) == 16 and sus[0]["spot"] == "Mexico Beach"
    a, b = F.run48_window()
    assert (a.isoformat()[:16], b.isoformat()[:16]) == ("2026-10-12T17:02", "2026-10-26T17:02")
