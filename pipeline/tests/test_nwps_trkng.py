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


def test_diag_matches_the_swell_not_raw_index_0():
    """The --diag comparison must select the SWELL system, not systems[0]. NWPS's system index is
    neither energy-ordered nor temporally stable, so a short-period WIND SEA can occupy index 0 —
    the mhx 20260728 00Z shape: index 0 = 0.14 m / 3.4 s / 173°, index 1 = 0.74 m / 7.0 s / 117°,
    buoy swell_dir ~90°. Taking index 0 compared chop against the buoy's swell and inflated the
    NEW/REF rows to ~+42° while the trust gate reported ~+7° on the same buoy and cycle."""
    from pipeline.forecast import nwps_nearshore as nn
    windsea = {"system": 1, "hs": 0.14, "tp": 3.4, "dir": 173.0}     # index 0 — short, wind-aligned
    swell = {"system": 2, "hs": 0.74, "tp": 7.0, "dir": 117.0}       # index 1 — the real swell
    systems = [windsea, swell]
    ws, wdir = 7.0, 180.0                                            # ~7 m/s from the south
    # wave age classifies them apart: c = g·tp/2π is 5.3 m/s for 3.4 s (below 1.2·U·cosδ) and
    # 10.9 m/s for 7.0 s (above it)
    assert nn._system_is_swell(windsea, ws, wdir) is False
    assert nn._system_is_swell(swell, ws, wdir) is True
    matched = nn._match_swell_system(systems, ws, wdir)
    assert matched is swell, f"matched the wind sea at index 0, not the swell: {matched}"
    assert matched["dir"] == 117.0
    # the delta the diag would report: vs a ~90° buoy swell, matching gives ~+27°, index 0 ~+83°
    assert abs(((matched["dir"] - 90.0 + 180) % 360) - 180) < abs(((systems[0]["dir"] - 90.0 + 180) % 360) - 180)

    # ordering must not matter — the selector is energy-ranked among swells, not index-ranked
    assert nn._match_swell_system([swell, windsea], ws, wdir) is swell
    # a bigger wind sea must still lose to a smaller swell (energy rank applies AFTER the veto)
    big_chop = {"system": 3, "hs": 1.8, "tp": 3.0, "dir": 178.0}
    assert nn._match_swell_system([big_chop, swell], ws, wdir) is swell
    # all-wind-sea → None, so the hour contributes NO sample (never a fallback to index 0)
    assert nn._match_swell_system([windsea, big_chop], ws, wdir) is None
    # no model wind → unclassifiable → None, again no fallback
    assert nn._match_swell_system(systems, None, None) is None

    # --- the actual --diag selection step (not a re-implementation of it) ---
    # _matched_swell_at is what the diag loop calls per hour; --diag itself needs NOMADS+eccodes,
    # so this is the seam that makes the changed code path testable offline.
    VALID = 486_000
    mw = {VALID: (ws, wdir)}
    m, mdir = trk._matched_swell_at(systems, mw, VALID)
    assert m is swell and mdir == 117.0, (m, mdir)
    # index 0 would have given 173° — the chop-vs-swell comparison being fixed
    assert mdir != systems[0]["dir"]
    # an hour with no model wind is dropped, NOT back-filled with index 0
    assert trk._matched_swell_at(systems, mw, VALID + 1) == (None, None)
    assert trk._matched_swell_at(systems, {}, VALID) == (None, None)
    assert trk._matched_swell_at(systems, None, VALID) == (None, None)
    # an all-wind-sea hour is dropped the same way
    assert trk._matched_swell_at([windsea, big_chop], mw, VALID) == (None, None)
    assert trk._matched_swell_at([], mw, VALID) == (None, None)

    # and the per-hour table marks the matched system so the selection is visible
    assert trk._sys_str(systems, 1, matched).startswith("*"), "matched system must be marked"
    assert not trk._sys_str(systems, 0, matched).startswith("*"), "wind sea must NOT be marked"
    assert trk._sys_str(systems, 0, None) == trk._sys_str(systems, 0), "marker is opt-in"
    assert trk._sys_str(systems, 5, matched).strip() == "—", "absent system still dashes"


def test_band_split_excludes_ndbc_null_swell_partition():
    """CAUSE 1, a comparison artifact. NDBC emits a NULL swell partition as SwH exactly 0.0 paired
    with a physically impossible SwP, typically 20-25 s. At 42092 EVERY record over 45 days reads
    SwH 0.0 with SwP 20.0-25.0 while WWH carries the full 0.9-1.1 m at 5 s, so the old check scored
    our reader against a null and reported the split SUSPECT. Those hours must be EXCLUDED, and the
    exclusion COUNTED so it is visible in the diag rather than silent."""
    assert trk._is_null_spec_partition(0.0, 25.0) is True
    assert trk._is_null_spec_partition(0.0, 20.0) is True, "20 s is the threshold, inclusive"
    # BOTH conditions required: a genuinely flat hour at a SANE period is a real measurement
    assert trk._is_null_spec_partition(0.0, 9.0) is False, "flat swell at 9 s is a measurement"
    assert trk._is_null_spec_partition(1.4, 22.0) is False, "real swell height is never the null"
    assert trk._is_null_spec_partition(0.0, None) is False
    assert trk._is_null_spec_partition(None, 25.0) is False

    # the 42092 shape — our reader measures real swell, .spec publishes only the null marker
    rows = [(0.55, 0.0, 25.0), (0.60, 0.0, 22.0), (0.48, 0.0, 20.0)]
    # what the OLD check saw: a 0.54 m mean|Δ| against a 0.25 m bar → SUSPECT, from nulls alone
    assert sum(abs(h - s) for h, s, _ in rows) / len(rows) > trk.BAND_SPLIT_BAR_M
    bs = trk.band_split_verdict(rows)
    assert bs["n_excluded_null"] == 3 and bs["n_kept"] == 0
    assert bs["verdict"] == "NO_DATA", "nothing left to compare — a non-verdict, NOT SUSPECT"
    assert bs["mean_abs_delta"] is None and "NOT a split failure" in bs["label"]

    # mixed: only the null hour is dropped, and the surviving hours decide the verdict
    mixed = [(0.55, 0.0, 25.0), (0.30, 0.32, 11.0), (0.41, 0.38, 12.0)]
    bs2 = trk.band_split_verdict(mixed)
    assert bs2["n_excluded_null"] == 1 and bs2["n_kept"] == 2
    assert bs2["verdict"] == "OK" and bs2["mean_abs_delta"] < trk.BAND_SPLIT_BAR_M
    assert abs(bs2["mean_swh"] - 0.35) < 1e-9, "the excluded 0.0 must not drag the mean down"


def test_band_split_labels_the_sub_8s_convention_difference():
    """CAUSE 2, a convention difference. At 44095 NDBC reports SwH 0.8-1.0 m at SwP 4.5-5.6 s with
    STEEPNESS VERY_STEEP — its partitioner classifies 5 s energy as swell, while our fixed 0.125 Hz
    (8 s) cutoff classifies it as wind sea. For a surf forecast ours is the appropriate convention,
    since 5 s waves are chop, so the cutoff is NOT changed; the diag must SAY that instead of
    reporting SUSPECT."""
    assert trk._is_convention_difference(0.10, 0.90, 4.5) is True
    # THE SIGN MATTERS: ours ABOVE .spec SwH is over-assignment (cause 3), never a convention gap
    assert trk._is_convention_difference(1.46, 0.30, 4.5) is False
    # nor is a LONG-period disagreement the cutoff's doing — 12 s is swell on both conventions
    assert trk._is_convention_difference(0.10, 0.90, 12.0) is False
    assert trk._is_convention_difference(0.10, 0.90, 8.0) is False, "8 s is the cutoff, exclusive"
    assert trk._is_convention_difference(0.10, 0.90, None) is False
    assert trk._is_convention_difference(None, 0.90, 4.5) is False

    rows = [(0.10, 0.90, 4.5), (0.12, 0.85, 5.0), (0.08, 1.00, 5.6)]      # the 44095 shape
    bs = trk.band_split_verdict(rows)
    assert bs["n_kept"] == 3 and bs["n_excluded_null"] == 0 and bs["n_convention"] == 3
    assert bs["mean_abs_delta"] > trk.BAND_SPLIT_BAR_M, "the gap is real; only its LABEL changes"
    assert bs["verdict"] == "CONVENTION"
    assert "SUSPECT" not in bs["label"] and "convention difference" in bs["label"].lower()
    assert "NOT changed" in bs["label"], "the label must say the cutoff stays"

    # the 46278 shape — ours ABOVE .spec SwH at 10-14 s — must STILL come out SUSPECT
    over = [(1.46, 0.30, 12.0), (1.40, 0.35, 11.0), (1.50, 0.40, 14.0)]
    bs_over = trk.band_split_verdict(over)
    assert bs_over["verdict"] == "SUSPECT" and bs_over["n_convention"] == 0
    # and a minority of convention hours does not excuse a genuinely bad split
    minority = [(0.10, 0.90, 4.5), (1.46, 0.30, 12.0), (1.40, 0.35, 11.0)]
    assert trk.band_split_verdict(minority)["verdict"] == "SUSPECT"
    # a split that simply agrees is OK regardless of period
    assert trk.band_split_verdict([(0.30, 0.32, 4.5), (0.41, 0.38, 5.0)])["verdict"] == "OK"


def test_classify_bands_prefers_fixed_cutoff_over_wave_age():
    """CAUSE 3, the real defect. The wave-age split OVER-ASSIGNS to swell: at 46278 it read
    Hs_swell 1.46 m against .spec SwH 0.30 m, folding a 7-8 s wind sea (WWH 1.5 m) into swell,
    while .spec showed the true picture as SwH 0.3-0.4 at 10-14 s. Over 45 days the fixed-cutoff
    path's worst-case mean|Δ| against .spec is 0.36 m against wave age's 1.16 m, so the fixed
    cutoff must win when BOTH a wind and a spectrum are available. Wave age stays as an explicit
    opt-in; a VALID published Sep_Freq still outranks both."""
    from pipeline.forecast import ndbc_spectral as sp
    fq = [0.08, 0.133, 0.20, 0.25]                    # 0.133 Hz = 7.5 s
    dirs = {0.08: 90.0, 0.133: 90.0, 0.20: 90.0, 0.25: 90.0}

    # wind AND spectrum present, Sep_Freq the usual 9.999 sentinel → fixed cutoff, not wave age
    isw, method, sep = sp.classify_bands(fq, dirs, sep_freq=9.999, wind_speed=8.0, wind_dir=90.0)
    assert method == "fixed_cutoff", "a usable wind must NOT divert the split to wave age"
    assert abs(sep - sp.SWELL_WINDSEA_CUTOFF_HZ) < 1e-12, "and 9.999 is never coerced to a cutoff"
    assert isw == [True, False, False, False], "f < 0.125 Hz is swell; the 7.5 s band is not"

    # the opt-in still works, and still gives the wave-age answer
    isw_wa, m_wa, _ = sp.classify_bands(fq, dirs, sep_freq=9.999, wind_speed=8.0, wind_dir=90.0,
                                        prefer_wave_age=True)
    assert m_wa == "wave_age" and isw_wa[1] is True, "wave age calls the 7.5 s band swell"
    assert isw_wa != isw, "the two paths genuinely disagree — the preference is not cosmetic"

    # a VALID published Sep_Freq outranks both, opt-in or not
    _, m_sep, s_sep = sp.classify_bands(fq, dirs, sep_freq=0.10, wind_speed=8.0, wind_dir=90.0,
                                        prefer_wave_age=True)
    assert m_sep == "ndbc_sep_freq" and abs(s_sep - 0.10) < 1e-12
    # no wind at all is still the fixed cutoff, and reports itself honestly
    assert sp.classify_bands(fq, dirs, sep_freq=None, wind_speed=None)[1] == "fixed_cutoff"

    # end-to-end: the metrics + compute paths inherit the default, and wave age assigns MORE
    # energy to swell — the over-assignment being fixed
    spec = {"sep_freq": 9.999, "freqs": fq, "c11": [1.0, 8.0, 1.0, 0.5]}
    m = sp.spectral_metrics(spec, dirs, wind_speed=8.0, wind_dir=90.0)
    m_opt = sp.spectral_metrics(spec, dirs, wind_speed=8.0, wind_dir=90.0, prefer_wave_age=True)
    assert m["split_method"] == "fixed_cutoff" and m_opt["split_method"] == "wave_age"
    assert m_opt["hs_swell"] > m["hs_swell"], "wave age folds the 7.5 s wind sea into swell"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} Trkng-reader checks passed")


if __name__ == "__main__":
    _run_all()
