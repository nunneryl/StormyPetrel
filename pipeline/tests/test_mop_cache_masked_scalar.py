"""A masked netCDF value is ABSENT. The MOP cache builder used to write it as a number.

THE DEFECT. scripts/mop_blacks_slice._read_meta read every scalar as

    float(np.asarray(nc.variables[n][:]).ravel()[0])

and np.asarray() DISCARDS a numpy mask. netCDF4 masks an element to say "no value here";
that call exposed the raw fill underneath instead, with no warning and no exception.

IT HAD FIRED. 129 of 11,677 cached MOP points carried shore_normal exactly 0.0 (1.1%) while
the other 11,548 spanned 0.56 to 359.21 at full float precision. Queried against THREDDS,
D0930 / OC643 / OC642 / SF071 all report metaShoreNormal PRESENT and MASKED — CDIP has no
shore normal there. Four spots were rejected by the face harness on a delta that was really
the circular distance from their own orientation to the number zero.

WHAT THESE TESTS PIN, and why each matters:
  * masked -> None, for EVERY field the reader touches, not only shore_normal. water_depth
    came back clean across all 11,677 points, but the mechanism is identical and a masked
    depth would feed deshoal arithmetic silently rather than merely costing eligibility.
  * an UNMASKED 0.0 is a real reading and survives. The two cases are distinguishable, and
    the fix must not collapse them: masked 0.0 -> None, plain 0.0 -> 0.0.
  * the audit fires on a cache holding impossible values. 129 of 11,677 was silent.
  * the resume predicate re-reads a damaged point. It used to skip them forever.

netCDF4 is not installed in CI and OPeNDAP is unreachable from it, so every test here drives
the reader with a hand-built stand-in whose contents are written out. No expected value in
this file is produced by calling the code under test.
"""
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

from mop_blacks_slice import (  # noqa: E402
    CACHE_AUDIT_EXIT,
    audit_cache,
    audit_exit_code,
    meta_scalar,
    needs_reread,
    print_cache_audit,
)


class _Var:
    """Stands in for a netCDF4 Variable: the reader only ever does `var[:]`."""

    def __init__(self, payload):
        self._payload = payload

    def __getitem__(self, _key):
        return self._payload


class _RaisingVar:
    def __getitem__(self, _key):
        raise RuntimeError("OPeNDAP read failed")


class _NC:
    def __init__(self, **variables):
        self.variables = dict(variables)


# The exact shape CDIP returns for a point with no shore normal: variable present, data
# masked, a fill sitting underneath. Written out rather than fetched.
MASKED_OVER_ZERO = np.ma.MaskedArray([0.0], mask=[True])
MASKED_OVER_FILL = np.ma.MaskedArray([9.969209968386869e36], mask=[True])
PLAIN_ZERO = np.array([0.0])
PLAIN_BEARING = np.array([231.02000427246094])


# --------------------------------------------------------------------------- #
# the reader                                                                   #
# --------------------------------------------------------------------------- #
def test_a_masked_scalar_is_absent_not_the_fill_underneath():
    value, why = meta_scalar(_NC(metaShoreNormal=_Var(MASKED_OVER_ZERO)), "metaShoreNormal")
    assert value is None
    assert why == "masked"


def test_the_old_expression_really_did_produce_zero():
    """The mechanism itself, pinned, so the fix cannot be mistaken for superstition.

    This is the line that shipped. It is asserted directly rather than described, because a
    future reader deciding whether the guard is still needed should be able to see the
    failure rather than take it on trust.
    """
    old = float(np.asarray(MASKED_OVER_ZERO).ravel()[0])
    assert old == 0.0, "np.asarray must be shown dropping the mask for this test to mean anything"
    # and the same input through the fixed reader
    assert meta_scalar(_NC(metaShoreNormal=_Var(MASKED_OVER_ZERO)), "metaShoreNormal")[0] is None


def test_a_masked_default_fill_is_also_absent():
    """A default NC_FILL_FLOAT would have been visible as 1e37; a zero fill was not.

    Both are masked and both must be declined, so the guard cannot depend on recognising a
    particular fill value.
    """
    value, why = meta_scalar(_NC(metaShoreNormal=_Var(MASKED_OVER_FILL)), "metaShoreNormal")
    assert value is None
    assert why == "masked"


def test_an_unmasked_zero_is_a_real_reading_and_survives():
    """The two cases ARE distinguishable and the fix must not collapse them.

    0.0 is a legal bearing. If CDIP ever publishes one unmasked, it is data and is kept —
    which is why the build audit reports an exact zero as SUSPECT rather than impossible.
    """
    value, why = meta_scalar(_NC(metaShoreNormal=_Var(PLAIN_ZERO)), "metaShoreNormal")
    assert value == 0.0
    assert why == "ok"


def test_a_real_bearing_is_returned_unchanged_at_full_precision():
    value, why = meta_scalar(_NC(metaShoreNormal=_Var(PLAIN_BEARING)), "metaShoreNormal")
    assert value == 231.02000427246094
    assert why == "ok"


def test_a_masked_first_alias_falls_through_to_a_good_second():
    """The three names are spellings of one quantity; a masked first must not end the search."""
    nc = _NC(metaShoreNormal=_Var(MASKED_OVER_ZERO),
             metaShoreNormalOrientation=_Var(np.array([147.25])))
    value, why = meta_scalar(nc, "metaShoreNormal", "metaShoreNormalOrientation",
                             "metaShorelineAngle")
    assert value == 147.25
    assert why == "ok"


def test_all_aliases_masked_reports_masked_not_absent():
    nc = _NC(metaShoreNormal=_Var(MASKED_OVER_ZERO),
             metaShoreNormalOrientation=_Var(MASKED_OVER_FILL))
    value, why = meta_scalar(nc, "metaShoreNormal", "metaShoreNormalOrientation")
    assert value is None
    assert why == "masked"


def test_a_missing_variable_is_absent():
    value, why = meta_scalar(_NC(), "metaShoreNormal", "metaShorelineAngle")
    assert value is None
    assert why == "absent"


def test_a_raising_read_is_unreadable_not_a_number():
    value, why = meta_scalar(_NC(metaShoreNormal=_RaisingVar()), "metaShoreNormal")
    assert value is None
    assert why == "unreadable"


def test_an_empty_variable_is_empty():
    value, why = meta_scalar(_NC(metaShoreNormal=_Var(np.array([]))), "metaShoreNormal")
    assert value is None
    assert why == "empty"


def test_a_non_finite_value_is_declined():
    for payload in (np.array([float("nan")]), np.array([float("inf")]),
                    np.array([float("-inf")])):
        value, why = meta_scalar(_NC(metaShoreNormal=_Var(payload)), "metaShoreNormal")
        assert value is None
        assert why == "non-finite"


def test_a_plain_ndarray_with_no_mask_at_all_still_works():
    """getmaskarray on a plain ndarray reads all-False, so the reader does not require
    netCDF4 to have returned a MaskedArray."""
    assert meta_scalar(_NC(metaWaterDepth=_Var(np.array([10.5]))), "metaWaterDepth") == (10.5, "ok")


def test_a_zero_dimensional_scalar_works_masked_and_unmasked():
    assert meta_scalar(_NC(v=_Var(np.ma.MaskedArray(42.0, mask=False))), "v") == (42.0, "ok")
    assert meta_scalar(_NC(v=_Var(np.ma.MaskedArray(0.0, mask=True))), "v") == (None, "masked")


def test_the_np_ma_masked_singleton_is_absent():
    """A fully-masked 0-d read can come back as the np.ma.masked singleton itself."""
    assert meta_scalar(_NC(v=_Var(np.ma.masked)), "v") == (None, "masked")


def test_the_guard_covers_every_field_the_builder_reads():
    """Not just shore_normal. water_depth is clean today; the mechanism is not.

    A masked depth is the worse failure: shore_normal only costs a spot its eligibility,
    while a depth of 0.0 would feed deshoal arithmetic as if it were a measurement.
    """
    for name in ("metaLatitude", "metaLongitude", "metaWaterDepth", "metaShoreNormal"):
        assert meta_scalar(_NC(**{name: _Var(MASKED_OVER_ZERO)}), name) == (None, "masked"), name


def test_the_comment_forbids_inventing_a_normal_from_neighbours():
    """The tempting wrong fix, refused in writing so nobody adds it later.

    Pinned by content: a previous mutation round found that explanatory prose was the one
    thing no test noticed.
    """
    src = open(os.path.join(ROOT, "scripts", "mop_blacks_slice.py")).read()
    assert "DO NOT DERIVE A SHORE NORMAL FOR THESE POINTS FROM THEIR NEIGHBOURS" in src
    assert "would be a number we made up" in src
    assert "np.asarray() DISCARDS a numpy mask" in src


# --------------------------------------------------------------------------- #
# the build-time audit                                                         #
# --------------------------------------------------------------------------- #
def _clean_point(pid="D0001"):
    return {pid: {"url": "u", "lat": 32.9, "lon": -117.25,
                  "water_depth": 10.0, "shore_normal": 231.02}}


def test_a_clean_cache_produces_no_findings():
    report = audit_cache(_clean_point())
    assert report["points"] == 1
    assert report["errors"] == 0
    for field in ("lat", "lon", "water_depth", "shore_normal"):
        assert report[field]["present"] == 1
        assert report[field]["absent"] == 0
        assert report[field]["impossible"] == 0
        assert report[field]["zero"] == 0
    assert print_cache_audit(report) == 0


def test_the_audit_counts_the_real_shape_of_the_damage():
    """Three of four points hold a fabricated shore normal — the 2026 cache in miniature.

    Counts are written out, not computed from the fixture by the code under test.
    """
    cache = {
        "D0930": {"lat": 33.2, "lon": -117.4, "water_depth": 10.0, "shore_normal": 0.0},
        "OC642": {"lat": 33.7, "lon": -118.1, "water_depth": 11.0, "shore_normal": 0.0},
        "OC643": {"lat": 33.7, "lon": -118.1, "water_depth": 11.0, "shore_normal": 0.0},
        "SM297": {"lat": 37.5, "lon": -122.5, "water_depth": 12.0, "shore_normal": 158.6},
    }
    report = audit_cache(cache)
    assert report["points"] == 4
    assert report["shore_normal"]["present"] == 4      # 0.0 is present-but-suspect
    assert report["shore_normal"]["zero"] == 3
    assert report["shore_normal"]["impossible"] == 0
    assert report["shore_normal"]["zero_ids"] == ["D0930", "OC642", "OC643"]
    assert print_cache_audit(report) == 3


def test_an_absent_shore_normal_is_counted_as_absent_not_as_a_finding():
    """After the fix this is what a damaged point looks like: null, not zero.

    A null is the honest state and must NOT fail the build — 129 points genuinely have no
    shore normal upstream, and a run that refuses to finish over them would never finish.
    """
    cache = {"D0930": {"lat": 33.2, "lon": -117.4, "water_depth": 10.0, "shore_normal": None}}
    report = audit_cache(cache)
    assert report["shore_normal"]["absent"] == 1
    assert report["shore_normal"]["present"] == 0
    assert report["shore_normal"]["zero"] == 0
    assert report["shore_normal"]["impossible"] == 0
    assert print_cache_audit(report) == 0


def test_a_zero_water_depth_is_IMPOSSIBLE_not_merely_suspect():
    """These points sit on the 10 m contour. Zero is a missing reading, not a shallow one.

    The low bound is exclusive for depth and inclusive for the bearings, which is the one
    asymmetry in _FIELD_BOUNDS and the reason it carries a third element.
    """
    cache = {"X": {"lat": 32.9, "lon": -117.2, "water_depth": 0.0, "shore_normal": 231.0}}
    report = audit_cache(cache)
    assert report["water_depth"]["impossible"] == 1
    assert report["water_depth"]["present"] == 0
    assert report["water_depth"]["impossible_ids"] == ["X"]
    assert print_cache_audit(report) >= 1


def test_out_of_range_values_are_impossible():
    cache = {
        "A": {"lat": 91.0, "lon": -117.2, "water_depth": 10.0, "shore_normal": 231.0},
        "B": {"lat": 32.9, "lon": -181.0, "water_depth": 10.0, "shore_normal": 231.0},
        "C": {"lat": 32.9, "lon": -117.2, "water_depth": 12001.0, "shore_normal": 231.0},
        "D": {"lat": 32.9, "lon": -117.2, "water_depth": 10.0, "shore_normal": 361.0},
        "E": {"lat": 32.9, "lon": -117.2, "water_depth": 10.0, "shore_normal": -1.0},
    }
    report = audit_cache(cache)
    assert report["lat"]["impossible"] == 1
    assert report["lon"]["impossible"] == 1
    assert report["water_depth"]["impossible"] == 1
    assert report["shore_normal"]["impossible"] == 2
    assert print_cache_audit(report) == 5


def test_a_non_finite_cached_value_is_impossible():
    cache = {"A": {"lat": 32.9, "lon": -117.2, "water_depth": float("nan"),
                   "shore_normal": float("inf")}}
    report = audit_cache(cache)
    assert report["water_depth"]["impossible"] == 1
    assert report["shore_normal"]["impossible"] == 1


def test_error_entries_are_counted_and_not_audited_as_fields():
    """A fetch failure is not a metadata failure and must not be counted as one.

    Added an assertion on `absent` after mutation testing: without it, auditing the error
    entry's missing fields as absent survived every check. That distinction matters at
    11,677 points — a run with 200 OPeNDAP failures would otherwise report 200 absent shore
    normals and send someone looking at CDIP's metadata instead of at the network.
    """
    cache = {"A": {"error": "HTTP 500"}, "B": {"lat": 32.9, "lon": -117.2,
                                               "water_depth": 10.0, "shore_normal": 231.0}}
    report = audit_cache(cache)
    assert report["errors"] == 1
    assert report["points"] == 2
    for field in ("lat", "lon", "water_depth", "shore_normal"):
        assert report[field]["present"] == 1, field
        assert report[field]["absent"] == 0, field       # NOT 1 — the error point is skipped
        assert report[field]["impossible"] == 0, field


def test_the_impossible_id_sample_is_capped_too():
    """Same contract as zero_ids. Uncapped, a real failure dumps thousands of ids into a log.

    Written as a separate case because the zero_ids test exercises a different list and
    leaving this one uncovered survived mutation.
    """
    cache = {f"P{i:04d}": {"lat": 32.9, "lon": -117.2, "water_depth": -1.0,
                           "shore_normal": 231.0} for i in range(40)}
    report = audit_cache(cache)
    assert report["water_depth"]["impossible"] == 40
    assert len(report["water_depth"]["impossible_ids"]) == 12
    assert report["water_depth"]["impossible_ids"][0] == "P0000"
    assert print_cache_audit(report) == 40


def test_the_audit_sample_is_capped_but_the_count_is_not():
    cache = {f"P{i:04d}": {"lat": 32.9, "lon": -117.2, "water_depth": 10.0,
                           "shore_normal": 0.0} for i in range(40)}
    report = audit_cache(cache)
    assert report["shore_normal"]["zero"] == 40
    assert len(report["shore_normal"]["zero_ids"]) == 12
    assert print_cache_audit(report) == 40


def test_the_audit_would_have_caught_the_real_thing():
    """1.1% of 11,677 is what went unreported. Scaled down, the audit must not shrug.

    129 zeros among 11,548 good values is the ratio that slipped through a summary reporting
    only "N points, M with coordinates".
    """
    cache = {}
    for i in range(11548):
        cache[f"G{i:05d}"] = {"lat": 32.9, "lon": -117.2, "water_depth": 10.0,
                              "shore_normal": 180.0}
    for i in range(129):
        cache[f"B{i:05d}"] = {"lat": 32.9, "lon": -117.2, "water_depth": 10.0,
                              "shore_normal": 0.0}
    report = audit_cache(cache)
    assert report["points"] == 11677
    assert report["shore_normal"]["zero"] == 129
    assert print_cache_audit(report) == 129


def test_findings_reach_the_build_exit_code():
    """An audit nobody acts on is the silence this change replaces.

    Added after mutation testing: with the audit inline in build_cache — which opens the
    THREDDS catalog on its first line and so cannot run offline — both "the audit runs at
    all" and "findings reach the exit code" were unreachable by any test, and mutants
    removing each survived.
    """
    assert CACHE_AUDIT_EXIT == 3
    assert CACHE_AUDIT_EXIT != 0, "a findings exit status must be distinguishable from success"
    damaged = {"D0930": {"lat": 33.2, "lon": -117.4, "water_depth": 10.0, "shore_normal": 0.0}}
    assert audit_exit_code(damaged) == 3
    assert audit_exit_code(_clean_point()) == 0
    # a null shore normal is the POST-FIX state and must not fail a build
    assert audit_exit_code({"X": {"lat": 1.0, "lon": 2.0, "water_depth": 10.0,
                                  "shore_normal": None}}) == 0


def test_build_cache_itself_returns_the_audit_status():
    """Drives the real build_cache with the network stubbed out, not a source grep.

    Added after mutation testing: audit_exit_code being correct does not help if
    build_cache stops calling it, and that edit survived everything else. build_cache opens
    the THREDDS catalog on its first line, so the test supplies an EMPTY point list — the
    executor then has nothing to do and the function falls straight through its resume,
    write and audit path, which is exactly the stretch under test.

    A grep for "audit_exit_code" in the source would have been cheaper and worthless: it
    would pass on a call sitting in a comment. Source-grep tests have already misfired once
    in this repo, on a db_import guard that tripped over its own warning text.
    """
    import mop_blacks_slice as mbs

    tmp = os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "mop_points_test_cache.json")
    damaged = {"D0930": {"url": "u", "lat": 33.2, "lon": -117.4,
                         "water_depth": 10.0, "shore_normal": 0.0}}
    clean = {"D0001": {"url": "u", "lat": 32.9, "lon": -117.25,
                       "water_depth": 10.0, "shore_normal": 231.02}}

    real_list, real_cache = mbs.list_all_mop_points, mbs.CACHE
    try:
        mbs.list_all_mop_points = lambda *a, **k: []       # no network, nothing to fetch
        mbs.CACHE = tmp
        for seed, expected in ((damaged, 3), (clean, 0)):
            with open(tmp, "w") as fh:
                json.dump(seed, fh)
            assert mbs.build_cache(workers=1) == expected, seed
    finally:
        mbs.list_all_mop_points, mbs.CACHE = real_list, real_cache
        if os.path.exists(tmp):
            os.remove(tmp)


# --------------------------------------------------------------------------- #
# the resume predicate                                                         #
# --------------------------------------------------------------------------- #
def test_build_cache_actually_re_reads_the_damaged_point():
    """The stickiness, end to end. needs_reread being right does not help if build_cache
    stops calling it — and reverting the predicate to `pid not in cache` survived every
    other test here, because the exit-code test supplies no points to iterate.

    This one supplies two: one damaged, one healthy. The fetch is stubbed, so what is under
    test is purely WHICH points build_cache decides to re-read.
    """
    import mop_blacks_slice as mbs

    tmp = os.path.join(os.environ.get("TMPDIR", "/tmp"), "mop_points_resume_test.json")
    seeded = {
        "D0930": {"url": "u", "lat": 33.2, "lon": -117.4,
                  "water_depth": 10.0, "shore_normal": 0.0},      # damaged -> must re-read
        "D0001": {"url": "u", "lat": 32.9, "lon": -117.25,
                  "water_depth": 10.0, "shore_normal": 231.02},   # healthy -> must not
    }
    fetched = []

    def _fake_read(args):
        pid, url = args
        fetched.append(pid)
        return pid, {"url": url, "lat": 33.2, "lon": -117.4,
                     "water_depth": 10.0, "shore_normal": None}   # CDIP has none: null

    real_list, real_cache, real_read = (mbs.list_all_mop_points, mbs.CACHE, mbs._read_meta)
    try:
        mbs.list_all_mop_points = lambda *a, **k: [("D0930", "u"), ("D0001", "u")]
        mbs._read_meta = _fake_read
        mbs.CACHE = tmp
        with open(tmp, "w") as fh:
            json.dump(seeded, fh)
        rc = mbs.build_cache(workers=1)
        assert fetched == ["D0930"], f"expected only the damaged point re-read, got {fetched}"
        written = json.load(open(tmp))
        assert written["D0930"]["shore_normal"] is None, "the fabricated 0.0 must be replaced"
        assert written["D0001"]["shore_normal"] == 231.02, "the healthy point must be untouched"
        assert rc == 0, "a cache of nulls is clean; null is the honest state, not a finding"
    finally:
        mbs.list_all_mop_points, mbs.CACHE, mbs._read_meta = real_list, real_cache, real_read
        if os.path.exists(tmp):
            os.remove(tmp)


def test_a_damaged_point_is_re_read_rather_than_skipped_forever():
    """The zeros were sticky: good lat, so the old predicate never retried them."""
    cache = {"D0930": {"lat": 33.2, "lon": -117.4, "water_depth": 10.0, "shore_normal": 0.0}}
    assert needs_reread(cache, "D0930") is True


def test_a_zero_COORDINATE_is_also_a_retry_signal_not_only_a_zero_bearing():
    """Every field the same mechanism can reach, not just the one that happened to fire.

    Added after mutation testing: narrowing the retry to shore_normal alone survived. lat
    and lon come through the identical reader, and a masked latitude used to arrive as 0.0
    while suppressing the geospatial_lat_min fallback that exists to rescue it — so a zero
    coordinate is precisely as sticky as a zero bearing was.
    """
    assert needs_reread({"A": {"lat": 0.0, "lon": -117.4, "water_depth": 10.0,
                               "shore_normal": 231.0}}, "A") is True
    assert needs_reread({"A": {"lat": 33.2, "lon": 0.0, "water_depth": 10.0,
                               "shore_normal": 231.0}}, "A") is True


def test_a_healthy_point_is_not_re_read():
    cache = _clean_point("D0001")
    assert needs_reread(cache, "D0001") is False


def test_an_absent_or_errored_or_coordless_point_is_re_read():
    assert needs_reread({}, "D0001") is True
    assert needs_reread({"A": {"error": "boom"}}, "A") is True
    assert needs_reread({"A": {"lat": None, "lon": None}}, "A") is True
    assert needs_reread({"A": "not a dict"}, "A") is True


def test_a_point_whose_shore_normal_is_NULL_is_not_re_read_for_ever():
    """A null is a settled answer — CDIP has no value — not a retry signal.

    Re-reading these on every build would add 129 OPeNDAP round trips per run and change
    nothing. Only a ZERO means "written by a builder that could not tell".
    """
    cache = {"D0930": {"lat": 33.2, "lon": -117.4, "water_depth": 10.0, "shore_normal": None}}
    assert needs_reread(cache, "D0930") is False


if __name__ == "__main__":
    import io
    import contextlib
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
    print("mop cache masked scalar: ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
