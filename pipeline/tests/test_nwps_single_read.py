"""The NWPS override reads the FETCH STAGE's series, not a second download of the cycle.

THE DEFECT. `fetch_all` and `interpret` ran as two processes, each resolving its own NWPS
cycle by its own rule: nwps._locate_cycle takes candidate DIRECTORIES, builds one filename
and pulls a grib_filter subset; nwps_nearshore.find_latest_cycle takes any listed CG1 file
and pulls the whole nest. Nothing made them agree. Measured over 14 days of California rows,
the two reads published different peak periods for the same spot-hour on ~5.5% of rows (up
to 13.695 s apart) and different faces by up to 8.643 ft, leaving `hs`/`tp`/`dp` from one
read beside `face_ft`/`swell_*`/`chop_*` from the other on the same row.

Four other explanations were tested against production and refuted before this one: a
publish-window race (predicted a block at UTC 18-23; measured flat across all 24), the +/-1
hour bucket slop at the join (bounded ~4% by construction, and it predicts dp to diverge
MORE than tp; measured 2:1 the other way), a perpw/mwp variable collision (predicted the
tp ratio to cluster at 0.6-0.85; measured p10 0.973 / p50 0.998 / p90 1.008), and the
apparent 25-28% rate itself, which was ~22% sub-0.05 s rounding noise over a real 5.5%.

ONLY THE SOURCE OF FOUR VALUES CHANGES. swh, perpw, dirpw and shts now come from
forecast_data/nwps.json instead of a second GRIB. Everything computed from them —
face_ft, dir_gain, chop_ratio, chop_mult, period_quality, effective_size_ft, stars,
swell_hs, swell_source — is computed by the same calls in the same order, which is what
test_computed_outputs_are_unchanged_for_fixed_inputs pins with literals.

EVERY EXPECTED VALUE IS WRITTEN LITERALLY, with the arithmetic in a comment. None is
produced by calling the function under test.

Run: python -m pipeline.tests.test_nwps_single_read
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import tempfile
from pathlib import Path

from pipeline.forecast import nwps_nearshore as nn

# 2026-01-01T00:00:00Z = epoch 1767225600; 1767225600 / 3600 = 490896 exactly.
HOUR_ISO = "2026-01-01T00:00:00Z"
HOUR_BUCKET = 490896

# The one artifact hour every test below rates, chosen so each factor is exact:
#   period_factor(13.0, "ww3") = 1.15 + (13-12)/(14-12) * (1.20-1.15) = 1.175
#   chop_ratio(2.0, 1.6)       = (2.0-1.6)/2.0 = 0.2, the curve's own knot -> mult 1.0
#   period_quality(13.0)       = 1.0, the curve's own knot
ARTIFACT_ENTRY = {"valid_time": HOUR_ISO, "hs": 2.0, "tp": 13.0, "dp": 220.0, "swell_hs": 1.6}


def _spot(name="T"):
    return {"name": name, "swell_window_source": "nwps", "orientation_deg": 220,
            "nwps_wfo": "lox", "swell_window_arcs": [{"min": 180, "max": 260, "span": 84}],
            "optimal_swell_dir": 220}


def _ratings(name="T"):
    # No wind_dir / wind_speed, so apply_nwps_overrides' wind re-judge does not fire and
    # wind_mult stays the 1.0 supplied here.
    return {name: [{"valid_time": HOUR_ISO, "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0}]}


class _Boom(Exception):
    pass


class _CaptureLog(logging.Handler):
    """Collect the module's own records so the run-summary warning can be asserted on."""
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _forbid_grid_path():
    """Replace the download+parse entry points with tripwires. Returns a restore callable."""
    saved = (nn.load_cycle, nn.nwps_series_by_hour, nn.find_latest_cycle)

    def _bang(*_a, **_k):
        raise _Boom("the override touched the GRID path")

    nn.load_cycle = _bang
    nn.nwps_series_by_hour = _bang
    nn.find_latest_cycle = _bang

    def restore():
        nn.load_cycle, nn.nwps_series_by_hour, nn.find_latest_cycle = saved
    return restore


# --------------------------------------------------------------------------- #
# 1 — the override never reaches the grid path                                 #
# --------------------------------------------------------------------------- #

def test_override_never_calls_load_cycle_when_given_the_artifact():
    restore = _forbid_grid_path()
    try:
        ratings = _ratings()
        stats = nn.apply_nwps_overrides(ratings, [_spot()], nwps={"T": [dict(ARTIFACT_ENTRY)]})
    finally:
        restore()
    assert stats["fed"] == 1, stats
    assert ratings["T"][0]["swell_source"] == "nwps", ratings["T"][0]


def test_override_never_calls_load_cycle_when_given_an_injected_fetch():
    restore = _forbid_grid_path()
    try:
        ratings = _ratings()
        series = {HOUR_BUCKET: (2.0, 13.0, 220.0, 1.6)}
        stats = nn.apply_nwps_overrides(ratings, [_spot()], _fetch=lambda _s: series)
    finally:
        restore()
    assert stats["fed"] == 1, stats


def test_injected_fetch_wins_over_the_artifact():
    """_fetch is the seam the selftest, the validate harness and the trust gate use; it must
    take precedence over *nwps* rather than the artifact silently overriding it."""
    ratings = _ratings()
    # The artifact says 2.0 m; the injected series says 3.0 m. 3.0 * 1.175 * 3.281 = 11.5697
    # -> face 11.57. If the artifact won instead, face would be 7.71.
    nn.apply_nwps_overrides(ratings, [_spot()],
                            nwps={"T": [dict(ARTIFACT_ENTRY)]},
                            _fetch=lambda _s: {HOUR_BUCKET: (3.0, 13.0, 220.0, 2.4)})
    assert ratings["T"][0]["face_ft"] == 11.57, ratings["T"][0]["face_ft"]


# --------------------------------------------------------------------------- #
# 2 — the four inputs are read A's, verbatim                                   #
# --------------------------------------------------------------------------- #

def test_the_four_inputs_match_the_artifact_exactly():
    """swh, perpw, dirpw, shts must arrive at nwps_stars as the artifact's hs, tp, dp,
    swell_hs — same values, same order, no substitution."""
    seen = {}
    real = nn.nwps_stars

    def recorder(hs, per, swell_dir, swell_hs, *a, **k):
        seen.update(hs=hs, per=per, swell_dir=swell_dir, swell_hs=swell_hs)
        return real(hs, per, swell_dir, swell_hs, *a, **k)

    nn.nwps_stars = recorder
    try:
        nn.apply_nwps_overrides(_ratings(), [_spot()], nwps={"T": [dict(ARTIFACT_ENTRY)]})
    finally:
        nn.nwps_stars = real
    assert seen == {"hs": 2.0, "per": 13.0, "swell_dir": 220.0, "swell_hs": 1.6}, seen


def test_artifact_fetch_buckets_the_hour_and_orders_the_tuple():
    fetch = nn._make_artifact_fetch({"T": [dict(ARTIFACT_ENTRY)]})
    got = fetch(_spot())
    assert got == {HOUR_BUCKET: (2.0, 13.0, 220.0, 1.6)}, got


def test_artifact_fetch_drops_an_hour_missing_any_of_swh_perpw_dirpw():
    """Mirrors nwps_series_by_hour's own guard, so the two paths admit the same hours."""
    for absent in ("hs", "tp", "dp"):
        entry = {k: v for k, v in ARTIFACT_ENTRY.items() if k != absent}
        assert nn._make_artifact_fetch({"T": [entry]})(_spot()) is None, absent


def test_artifact_fetch_passes_a_missing_shts_through_as_none():
    """shts is allowed to be absent — chop then falls to the explicit unknown, exactly as
    it did when nwps_series_by_hour returned None for it."""
    entry = {k: v for k, v in ARTIFACT_ENTRY.items() if k != "swell_hs"}
    got = nn._make_artifact_fetch({"T": [entry]})(_spot())
    assert got == {HOUR_BUCKET: (2.0, 13.0, 220.0, None)}, got


def test_artifact_fetch_skips_an_unparseable_valid_time():
    entry = dict(ARTIFACT_ENTRY, valid_time="not a time")
    assert nn._make_artifact_fetch({"T": [entry]})(_spot()) is None


# --------------------------------------------------------------------------- #
# 3 — a spot absent from the artifact is COUNTED and NAMED                     #
# --------------------------------------------------------------------------- #

def test_a_spot_absent_from_the_artifact_is_counted_and_named():
    ratings = {"Absent Spot": [{"valid_time": HOUR_ISO, "stars": 1.0,
                                "wind_mult": 1.0, "tide_mult": 1.0}]}
    stats = nn.apply_nwps_overrides(ratings, [_spot("Absent Spot")], nwps={"Other": []})
    assert stats["not_in_artifact"] == 1, stats
    assert stats["not_in_artifact_names"] == ["Absent Spot"], stats["not_in_artifact_names"]
    # It is its OWN bucket — not folded into an ordinary fallback or an error.
    assert stats["fed"] == 0 and stats["fell_back"] == 0 and stats["errored"] == 0, stats
    # And the base rating survives untouched: no override, no blanking.
    assert ratings["Absent Spot"][0]["stars"] == 1.0
    assert "swell_source" not in ratings["Absent Spot"][0]


def test_absent_spots_are_named_in_sorted_order_and_do_not_stop_the_others():
    ratings = {n: [{"valid_time": HOUR_ISO, "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0}]
               for n in ("Zed", "Present", "Alpha")}
    spots = [_spot("Zed"), _spot("Present"), _spot("Alpha")]
    stats = nn.apply_nwps_overrides(ratings, spots, nwps={"Present": [dict(ARTIFACT_ENTRY)]})
    assert stats["not_in_artifact"] == 2, stats
    assert stats["not_in_artifact_names"] == ["Alpha", "Zed"], stats["not_in_artifact_names"]
    assert stats["fed"] == 1, stats
    assert ratings["Present"][0]["swell_source"] == "nwps"


def test_an_empty_entry_list_counts_as_absent_not_as_a_silent_skip():
    """nwps.fetch writes a spot only when its series is non-empty, so an empty list should
    not occur — but if it does it must read as absent rather than as zero usable hours."""
    ratings = _ratings()
    stats = nn.apply_nwps_overrides(ratings, [_spot()], nwps={"T": []})
    assert stats["not_in_artifact"] == 1 and stats["not_in_artifact_names"] == ["T"], stats


def test_a_spot_present_but_with_no_usable_hour_is_an_ordinary_fallback():
    """Distinct from absent: the fetcher DID produce a series, it just has nothing joinable.
    That is the pre-existing fell_back path and must not be recategorised."""
    ratings = _ratings()
    entry = {k: v for k, v in ARTIFACT_ENTRY.items() if k != "hs"}
    stats = nn.apply_nwps_overrides(ratings, [_spot()], nwps={"T": [entry]})
    assert stats["not_in_artifact"] == 0, stats
    assert stats["fell_back"] == 1, stats


def test_the_loss_is_warned_about_by_count_and_name():
    """The counted, NAMED warning is the whole point of the new bucket: a spot that lost its
    second chance must be legible in the run log, not inferable only from a stats dict nobody
    prints per-spot."""
    cap = _CaptureLog()
    nn.log.addHandler(cap)
    try:
        ratings = {n: [{"valid_time": HOUR_ISO, "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0}]
                   for n in ("Zed", "Alpha")}
        nn.apply_nwps_overrides(ratings, [_spot("Zed"), _spot("Alpha")],
                                nwps={"Other": [dict(ARTIFACT_ENTRY)]})
    finally:
        nn.log.removeHandler(cap)
    said = [r.getMessage() for r in cap.records
            if r.levelno >= logging.WARNING and "absent from the fetch artifact" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in cap.records]
    assert "2 spot(s)" in said[0], said[0]
    assert "Alpha, Zed" in said[0], said[0]


def test_the_absent_spot_leaves_a_named_row_in_details():
    """details is the per-spot audit trail behind the aggregate counts; an absent spot must
    appear in it under its own reason, not vanish."""
    ratings = {"Absent Spot": [{"valid_time": HOUR_ISO, "stars": 1.0,
                                "wind_mult": 1.0, "tide_mult": 1.0}]}
    stats = nn.apply_nwps_overrides(ratings, [_spot("Absent Spot")], nwps={"Other": []})
    rows = [d for d in stats["details"] if d[0] == "absent-spot"]
    assert len(rows) == 1, stats["details"]
    assert "absent from the NWPS fetch artifact" in rows[0][1], rows[0]
    assert rows[0][2] == 0, rows[0]


def test_an_explicitly_empty_artifact_is_honoured_and_the_file_is_not_read():
    """`nwps={}` means "the fetch stage produced nothing", not "go find the file yourself".
    Falling through to disk on an empty-but-present artifact would reintroduce a second,
    different source for the four values — the exact split this change removes."""
    saved = nn.NWPS_FORECAST_FILE
    tmp = Path(tempfile.mkdtemp())
    try:
        # A decoy at the default path, holding the spot with a DIFFERENT height. Reading it
        # would feed the override (face 34.70 from 9.0 m) instead of reporting the spot absent.
        decoy = dict(ARTIFACT_ENTRY, hs=9.0)
        (tmp / "nwps.json").write_text(json.dumps({"T": [decoy]}))
        nn.NWPS_FORECAST_FILE = tmp / "nwps.json"
        ratings = _ratings()
        stats = nn.apply_nwps_overrides(ratings, [_spot()], nwps={})
    finally:
        nn.NWPS_FORECAST_FILE = saved
        (tmp / "nwps.json").unlink(missing_ok=True)
        tmp.rmdir()
    assert stats["not_in_artifact"] == 1 and stats["fed"] == 0, stats
    assert "face_ft" not in ratings["T"][0], ratings["T"][0]


def test_interpret_overrides_from_the_artifact_it_rated_from():
    """THE CALL SITE, end to end. interpret must hand the override the very dict it rated
    from, not let it re-find an artifact on disk — otherwise `--nwps <path>` rates from one
    file and overrides from another, which is the two-source split in a new costume.

    Fixtures only: no network, no database. --nwps carries hs 2.0 (face 7.71); a decoy at the
    default NWPS_FORECAST_FILE carries hs 9.0 (face 9.0 * 1.175 * 3.281 = 34.6963 -> 34.70).
    """
    from pipeline import interpret

    saved_file = nn.NWPS_FORECAST_FILE
    tmp = Path(tempfile.mkdtemp())
    spot = dict(_spot(), slug="t", lat=33.0, lon=-118.0)
    (tmp / "spots.json").write_text(json.dumps([spot]))
    (tmp / "rated.json").write_text(json.dumps({"T": [dict(ARTIFACT_ENTRY)]}))
    (tmp / "decoy.json").write_text(json.dumps({"T": [dict(ARTIFACT_ENTRY, hs=9.0)]}))
    (tmp / "tides.json").write_text(json.dumps({}))
    try:
        nn.NWPS_FORECAST_FILE = tmp / "decoy.json"
        # main() calls logging.basicConfig itself, so silencing the root logger would not
        # survive the call; disable() outranks it and is restored below.
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = interpret.main(["--spots", str(tmp / "spots.json"),
                                 "--nwps", str(tmp / "rated.json"),
                                 "--tides", str(tmp / "tides.json"),
                                 "--output", str(tmp / "out.json")])
        out = json.loads((tmp / "out.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        nn.NWPS_FORECAST_FILE = saved_file
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()
    assert rc == 0, rc
    e = out["T"][0]
    assert e["swell_source"] == "nwps", e
    assert e["face_ft"] == 7.71, e["face_ft"]      # 2.0 from --nwps, not 9.0 from the decoy
    assert e["hs"] == 2.0, e["hs"]                 # ...and the published hs is that same 2.0


def test_the_stats_dict_keeps_every_pre_existing_key():
    stats = nn.apply_nwps_overrides(_ratings(), [_spot()], nwps={"T": [dict(ARTIFACT_ENTRY)]})
    for key in ("fed", "fell_back", "errored", "wfo_unavailable", "details", "by_wfo",
                "hours_ww3_dir", "hours_dirpw_dir"):
        assert key in stats, key


# --------------------------------------------------------------------------- #
# 4 — the computed outputs are unchanged, pinned by literal                    #
# --------------------------------------------------------------------------- #

def test_computed_outputs_are_unchanged_for_fixed_inputs():
    """Every published value for the fixture hour, hand-computed:

        period_factor(13.0,"ww3") = 1.15 + (13-12)/2 * 0.05      = 1.175
        face_ft   = 2.0 * 1.175 * 3.281 = 7.71035                -> 7.71
        dir_gain  = cos^2(0/2) with dp == optimal == 220          = 1.0
        eff       = 7.71035 * 1.0                                 -> 7.71
        chop_ratio= (2.0 - 1.6) / 2.0                             = 0.2
        chop_mult = chop curve knot at 0.2                        = 1.0
        period_q  = period-quality curve knot at 13.0             = 1.0
        stars: every factor is 1.0, so raw == size_score(7.71035)
               = 4.0 + (7.71035-6)/(8-6) * 0.5 = 4.4275875
               -> round(4.4275875 * 2) / 2                        = 4.5
    """
    ratings = _ratings()
    nn.apply_nwps_overrides(ratings, [_spot()], nwps={"T": [dict(ARTIFACT_ENTRY)]})
    e = ratings["T"][0]
    assert e["face_ft"] == 7.71, e["face_ft"]
    assert e["dir_gain"] == 1.0, e["dir_gain"]
    assert e["effective_size_ft"] == 7.71, e["effective_size_ft"]
    assert e["chop_ratio"] == 0.2, e["chop_ratio"]
    assert e["chop_mult"] == 1.0, e["chop_mult"]
    assert e["period_quality"] == 1.0, e["period_quality"]
    assert e["wind_mult"] == 1.0, e["wind_mult"]
    assert e["stars"] == 4.5, e["stars"]
    assert e["swell_dp"] == 220.0, e["swell_dp"]
    assert e["swell_tp"] == 13.0, e["swell_tp"]
    assert e["swell_hs"] == 1.6, e["swell_hs"]
    assert e["swell_source"] == "nwps", e["swell_source"]


def test_the_override_does_not_rewrite_hs_tp_or_dp():
    """Reported, not fixed, in this branch: the override still leaves hs/tp/dp to read A.
    With one read they now agree by construction, so the columns are consistent without
    being rewritten. Pinned so a later change to that is deliberate."""
    ratings = _ratings()
    ratings["T"][0].update(hs=2.0, tp=13.0, dp=220.0)
    nn.apply_nwps_overrides(ratings, [_spot()], nwps={"T": [dict(ARTIFACT_ENTRY)]})
    e = ratings["T"][0]
    assert e["hs"] == 2.0 and e["tp"] == 13.0 and e["dp"] == 220.0, e
    # ...and they equal the four inputs the face was built from, which is the whole point.
    assert e["hs"] == ARTIFACT_ENTRY["hs"], (e["hs"], ARTIFACT_ENTRY["hs"])
    assert e["tp"] == ARTIFACT_ENTRY["tp"] and e["dp"] == ARTIFACT_ENTRY["dp"], e


def test_dry_run_still_computes_but_writes_nothing():
    ratings = _ratings()
    stats = nn.apply_nwps_overrides(ratings, [_spot()], nwps={"T": [dict(ARTIFACT_ENTRY)]},
                                    dry_run=True)
    assert stats["fed"] == 1, stats
    assert "swell_source" not in ratings["T"][0], ratings["T"][0]
    assert ratings["T"][0]["stars"] == 1.0


# --------------------------------------------------------------------------- #
# 5 — the artifact loader                                                      #
# --------------------------------------------------------------------------- #

def test_load_nwps_artifact_raises_loudly_when_the_fetch_stage_never_ran():
    """Raising beats returning {}: an empty dict would send all ~600 spots down the
    not-in-artifact path and bury the real cause under 600 warnings."""
    saved = nn.NWPS_FORECAST_FILE
    try:
        nn.NWPS_FORECAST_FILE = saved.parent / "definitely_not_here_nwps.json"
        raised = False
        try:
            nn._load_nwps_artifact()
        except OSError as exc:
            raised = True
            assert "fetch_all" in str(exc), str(exc)
        assert raised, "a missing artifact must raise, not return empty"
    finally:
        nn.NWPS_FORECAST_FILE = saved


def test_load_nwps_artifact_reads_the_file(tmp_name="_test_nwps_artifact.json"):
    saved = nn.NWPS_FORECAST_FILE
    path = saved.parent / tmp_name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"T": [dict(ARTIFACT_ENTRY)]}))
        nn.NWPS_FORECAST_FILE = path
        assert nn._load_nwps_artifact() == {"T": [dict(ARTIFACT_ENTRY)]}
    finally:
        nn.NWPS_FORECAST_FILE = saved
        path.unlink(missing_ok=True)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_nwps_single_read: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
