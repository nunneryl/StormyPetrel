"""Fixture checks for the NWPS CG0_Trkng partitioned-swell reader.

Synthetic GRIB "records" (short, system, fh, values2D, missing) mirror the verified
mhx / 2026-07-13 12Z structure — two coexisting swell systems (a ~138–256° SE system
and a ~24–79° NE system) plus an EMPTY system 3 at step 0, with 9999 sentinels in the
non-covered cells — so the three required behaviours are tested with no eccodes and no
network:

  * sentinel masking     — 9999 (and a declared missingValue) never leak into a value;
  * absent-system        — an empty system yields NO entry, not a crash or a zero;
  * system/step decoding  — level→system and step→hour decode to the stated structure.

Run: python -m pipeline.tests.test_nwps_trkng   (or pytest)
"""
from __future__ import annotations

import datetime

import numpy as np

from pipeline.forecast import nwps_trkng as trk

S = trk.TRKNG_SENTINEL
_CDT = datetime.datetime(2026, 7, 13, 12, tzinfo=datetime.timezone.utc)

# A 4×4 nest. Rows 0–1 carry BOTH tracked systems (as mhx step 0 did); rows 2–3 carry
# none. swdir spans the two verified ranges; hs/tp are plausible. One rows-0/1 cell has
# system 2 masked (partial) to prove partial systems are dropped.
_SYS1_DIR = np.linspace(138.1, 256.5, 8).reshape(2, 4)   # SE system, over rows 0–1
_SYS2_DIR = np.linspace(23.6, 79.1, 8).reshape(2, 4)     # NE system, over rows 0–1
_PARTIAL_CELL = (1, 3)   # here system 2's height is sentinel → system 2 omitted


def _lat_lon():
    lat_axis = np.array([40.06, 40.04, 40.02, 40.00])
    lon_axis = np.array([-73.00, -72.98, -72.96, -72.94])
    return np.meshgrid(lat_axis, lon_axis, indexing="ij")


def _field(top_rows_value_fn, present_rows=(0, 1), hole=None):
    """4×4 grid: cells in *present_rows* get value_fn(i,j); everything else sentinel.
    *hole* = a cell forced to sentinel even inside present_rows (a partial-system hole)."""
    a = np.full((4, 4), S, dtype="float64")
    for i in present_rows:
        for j in range(4):
            if hole is not None and (i, j) == hole:
                continue
            a[i, j] = top_rows_value_fn(i, j)
    return a


def _fixture():
    records = [
        # system 1 (SE): present rows 0–1
        ("swdir", 1, 0, _field(lambda i, j: float(_SYS1_DIR[i, j])), S),
        ("shts",  1, 0, _field(lambda i, j: 0.61), S),
        ("mpts",  1, 0, _field(lambda i, j: 10.5), S),
        # system 2 (NE): present rows 0–1, but HEIGHT masked at the partial cell
        ("swdir", 2, 0, _field(lambda i, j: float(_SYS2_DIR[i, j])), S),
        ("shts",  2, 0, _field(lambda i, j: 0.12, hole=_PARTIAL_CELL), S),
        ("mpts",  2, 0, _field(lambda i, j: 15.2), S),
        # system 3: ENTIRELY sentinel at step 0 (empty system)
        ("swdir", 3, 0, np.full((4, 4), S), S),
        ("shts",  3, 0, np.full((4, 4), S), S),
        ("mpts",  3, 0, np.full((4, 4), S), S),
    ]
    lats, lons = _lat_lon()
    return trk.parse_trkng(lats, lons, _CDT, records)


def test_system_step_decoding_matches_structure():
    cyc = _fixture()
    assert cyc["systems"] == [1, 2, 3], "level → system index"
    assert cyc["steps"] == [0], "step → forecast hour"
    assert cyc["shape"] == (4, 4)
    # a rows-0/1 cell carries both systems, in system-index order
    at = trk.trkng_systems_at(cyc, 0, 0, 0)
    assert [s["system"] for s in at] == [1, 2]
    assert at[0]["dir"] == 138.1 and at[0]["hs"] == 0.61 and at[0]["tp"] == 10.5
    assert abs(at[1]["dir"] - 23.6) < 1e-9 and at[1]["hs"] == 0.12
    # every tracked system-1 direction sits in the verified SE range; system-2 in NE
    for i in (0, 1):
        for j in range(4):
            s = {x["system"]: x for x in trk.trkng_systems_at(cyc, i, j, 0)}
            if 1 in s:
                assert 138.0 <= s[1]["dir"] <= 257.0
            if 2 in s:
                assert 23.0 <= s[2]["dir"] <= 80.0


def test_sentinel_never_leaks_into_a_value():
    cyc = _fixture()
    # no stored array anywhere retains the 9999 sentinel — it became NaN
    for d in cyc["data"].values():
        for arr in d.values():
            assert not np.any(arr >= S), "sentinel survived into a stored grid"
    # and no queried value is ever the sentinel, across every cell/system
    for i in range(4):
        for j in range(4):
            for s in trk.trkng_systems_at(cyc, i, j, 0):
                assert s["hs"] != S and s["dir"] != S and (s["tp"] is None or s["tp"] != S)
    # a fully-sentinel cell (rows 2–3) returns no systems (masked, not 9999)
    assert trk.trkng_systems_at(cyc, 3, 3, 0) == []


def test_absent_system_yields_no_entry_not_crash():
    cyc = _fixture()
    # system 3 was entirely sentinel → it appears in no cell (no crash, no zero-fill)
    for i in range(4):
        for j in range(4):
            assert all(s["system"] != 3 for s in trk.trkng_systems_at(cyc, i, j, 0))
    # querying an hour with no data at all is empty, not an error
    assert trk.trkng_systems_at(cyc, 0, 0, 99) == []
    # a partial system (direction present, height masked) is dropped, not half-emitted
    at = trk.trkng_systems_at(cyc, *_PARTIAL_CELL, 0)
    assert [s["system"] for s in at] == [1], "system 2 omitted where its height was masked"


def test_mask_marks_cells_with_no_tracked_swell():
    cyc = _fixture()
    # rows 0–1 have data → not masked; rows 2–3 never tracked → masked
    assert not cyc["mask"][0, 0] and not cyc["mask"][1, 2]
    assert cyc["mask"][2, 0] and cyc["mask"][3, 3]


def test_node_reconciliation_same_vs_different_grid():
    cyc = _fixture()
    lats, lons = _lat_lon()
    # CG1 dicts here carry NO 'shape' key — exactly like nwps_nearshore.load_cycle's
    # real output. (Regression: reading cg1['shape'] raised KeyError('shape') on the Mac.)
    cg1_same = {"lats": lats, "lons": lons, "mask": np.zeros((4, 4), bool), "cycle_dt": _CDT}
    assert trk._grids_coincident(cyc, cg1_same)
    ti, tj, why = trk.trkng_node(cyc, cg1_same, 40.06, -73.00)
    assert (ti, tj) == (0, 0) and "same grid" in why.lower(), "coincident → index reused"
    # a different-resolution grid must be remapped EXPLICITLY (by coords), never silently
    cg1_diff = {"lats": np.array([[40.06]]), "lons": np.array([[-73.0]]),
                "mask": np.zeros((1, 1), bool), "cycle_dt": _CDT}
    _, _, why2 = trk.trkng_node(cyc, cg1_diff, 40.06, -73.00)
    assert "cg1 node" in why2.lower() and ("footprint" in why2.lower() or "domain" in why2.lower())


def test_latlon_axes_matches_real_mhx_grid_keys():
    """The eccodes seam's geolocation, against the verified mhx grid definition:
    Ni=61, Nj=62, first=(33.85, 282.0), di=0.054167, dj=0.045082, scanningMode=64."""
    lat2d, lon2d = trk._latlon_axes(61, 62, 33.85, 282.0, 0.054167, 0.045082, 64)
    assert lat2d.shape == (62, 61)
    # scanningMode=64 → j south→north: row 0 is the SOUTH edge, latitude ASCENDING
    assert abs(lat2d[0, 0] - 33.85) < 1e-6
    assert abs(lat2d[-1, 0] - 36.6) < 1e-3 and lat2d[-1, 0] > lat2d[0, 0]
    # lon 0/360 → −180/180: 282→−78.0, 285.25→−74.75
    assert abs(lon2d[0, 0] + 78.0) < 1e-6 and abs(lon2d[0, -1] + 74.75) < 1e-3


def test_latlon_axes_rejects_column_major_layout():
    try:
        trk._latlon_axes(61, 62, 33.85, 282.0, 0.054167, 0.045082, 64 | 0x20)
        raised = False
    except NotImplementedError:
        raised = True
    assert raised, "jPointsAreConsecutive must raise, not silently transpose the grid"


def test_missing_value_key_masks_in_addition_to_9999():
    # a system whose non-covered cells use a declared missingValue (1e20), not 9999
    lats, lons = _lat_lon()
    dir_arr = np.full((4, 4), 1e20); dir_arr[0, 0] = 210.0
    hs_arr = np.full((4, 4), 1e20); hs_arr[0, 0] = 0.5
    tp_arr = np.full((4, 4), 1e20); tp_arr[0, 0] = 11.0
    cyc = trk.parse_trkng(lats, lons, _CDT, [
        ("swdir", 1, 0, dir_arr, 1e20), ("shts", 1, 0, hs_arr, 1e20), ("mpts", 1, 0, tp_arr, 1e20),
    ])
    assert trk.trkng_systems_at(cyc, 0, 0, 0) == [{"system": 1, "hs": 0.5, "tp": 11.0, "dir": 210.0}]
    assert trk.trkng_systems_at(cyc, 1, 1, 0) == [], "1e20 missingValue masked, not read as swell"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} Trkng-reader checks passed")


if __name__ == "__main__":
    _run_all()
