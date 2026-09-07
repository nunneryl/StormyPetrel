"""The pre-correction face is stored, and every row says which pipeline produced it.

WHY THIS FILE EXISTS. face_correction divides face_ft in place, and forecasts is
UNIQUE(spot_id, valid_time, source) with every run upserting over the hours it covers — so a
past hour freezes at whatever the last run to touch it wrote. The correction shipped
2026-09-02 03:30 UTC, and from that moment any window straddling it held a MIXTURE of
corrected and uncorrected faces in one column with nothing on the row to say which.

scripts/mop_face_validation.py measures face_ft / (MOP Hs x 3.281). Over a straddling window
that is neither the offset nor the residual. Measured 2026-09-07 it read steamer-lane at 2.188
against a committed factor of 2.81 — not wrong-looking enough to stop anyone, and it nearly
produced the conclusion that the calibration was unstable.

UN-CORRECTING AFTERWARDS CANNOT RECOVER IT, which is why this is a stored column and not a
script. steamer-lane's rows span at least three regimes inside one fortnight:

    before 2026-09-02 03:30   not corrected
    865665c (2026-09-01)      divided by 2.8702
    724f442 (2026-09-03)      ABSENT from the factor file — not corrected at all
    2ff93e6 (2026-09-03)      divided by 2.8084

EVERY EXPECTED VALUE IS A LITERAL. The one stamp fingerprint asserted here is computed with
hashlib in the test body from a hand-written string, not by calling face_correction_stamp.

Run: python -m pipeline.tests.test_face_provenance
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pipeline.forecast import face_correction as FC

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def _slug(name):
    return (name or "").lower().replace(" ", "-")


def _spot(name="Steamer Lane", source="nwps"):
    return {"name": name, "swell_window_source": source}


def _rec(factor, p25=None, p75=None, measured_on="2026-09-01"):
    rec = {"factor": factor, "measured_on": measured_on}
    if p25 is not None:
        rec["p25"], rec["p75"] = p25, p75
    return rec


def _entry(face=8.0, eff=6.0, stars=4.0):
    return {"valid_time": "2026-09-07T10:00:00Z", "face_ft": face,
            "effective_size_ft": eff, "stars": stars,
            "wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0, "period_quality": 1.0}


TODAY = __import__("datetime").date(2026, 9, 7)


# --------------------------------------------------------------------------- #
# 1 — face_ft_raw is the PRE-DIVISION value for a corrected spot               #
# --------------------------------------------------------------------------- #

def test_face_ft_raw_is_the_pre_division_face_for_a_corrected_spot():
    """The whole point. face_ft is divided; face_ft_raw is what it was.

    Literals: a face of 8.0 divided by 2.0 is 4.0, and the raw is 8.0. Nothing here is
    produced by calling the seam — 8.0 and 2.0 are the inputs and 4.0 is arithmetic done by
    hand."""
    ratings = {"Steamer Lane": [_entry(face=8.0, eff=6.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 4.0, e            # corrected: 8.0 / 2.0
    assert e["face_ft_raw"] == 8.0, e        # raw: what it was before the division
    assert e["effective_size_ft"] == 3.0, e  # 6.0 / 2.0, to show the seam really ran


def test_the_applied_divisor_is_recoverable_from_the_two_columns():
    """No separate factor column is needed, and this is why: raw / published IS the divisor
    that was applied to that row, whatever generation of the file it came from."""
    ratings = {"Steamer Lane": [_entry(face=9.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(3.0)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 3.0 and e["face_ft_raw"] == 9.0
    assert e["face_ft_raw"] / e["face_ft"] == 3.0, "the divisor, back out of the row"


def test_raw_is_stored_verbatim_not_re_rounded():
    """face_ft is rounded to 2dp by the seam; face_ft_raw keeps whatever the upstream
    producer wrote, unrounded. That is what makes it directly comparable to the face_ft of a
    row written before the correction existed — the harness falls back across the migration
    boundary with no precision seam."""
    ratings = {"Steamer Lane": [_entry(face=8.126, eff=6.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft_raw"] == 8.126, "verbatim, not rounded to 8.13"
    assert e["face_ft"] == 4.06, e           # round(8.126 / 2.0, 2) = round(4.063, 2)


def test_an_unrateable_hour_has_a_null_raw_exactly_as_it_has_a_null_face():
    """Honest absence, not a fabricated zero."""
    ratings = {"Steamer Lane": [{"valid_time": "x", "face_ft": None,
                                 "effective_size_ft": 6.0, "stars": 4.0}]}
    st = FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                                   slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft_raw"] is None and e["face_ft"] is None
    assert e["face_correction_version"], "still stamped — the row was still produced by us"
    assert st["unrateable_hours"] == 1


# --------------------------------------------------------------------------- #
# 2 — raw EQUALS face where no factor applies, in all three skip cases          #
# --------------------------------------------------------------------------- #
# The equality is the assertion that no arithmetic ran. It is a stronger statement than
# "the entry is unchanged", which cannot distinguish "not divided" from "divided by 1.0".

def test_raw_equals_face_for_a_spot_with_no_factor():
    ratings = {"Ocean Beach": [_entry(face=8.0)]}
    FC.apply_face_corrections(ratings, [_spot("Ocean Beach"), _spot("Steamer Lane")],
                              factors={"steamer-lane": _rec(2.87)}, slug_for=_slug, now=TODAY)
    e = ratings["Ocean Beach"][0]
    assert e["face_ft"] == 8.0 and e["face_ft_raw"] == 8.0


def test_raw_equals_face_for_a_mop_tier_spot_the_seam_skips():
    """The MOP tier is skipped structurally, by swell_window_source. Its face is computed
    FROM MOP, so correcting it would divide MOP by a number derived from MOP — and the raw
    column has to be populated anyway, or the harness could not tell a skipped row from a
    pre-migration one."""
    ratings = {"Steamer Lane": [_entry(face=8.0)]}
    st = FC.apply_face_corrections(ratings, [_spot(source="cdip_mop")],
                                   factors={"steamer-lane": _rec(2.87)},
                                   slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 8.0 and e["face_ft_raw"] == 8.0
    assert st["mop_tier_skipped"] == 1


def test_raw_equals_face_for_a_held_out_spot():
    """Held out = absent from the factors map, so it takes the no-factor path."""
    ratings = {"Fort Point": [_entry(face=8.0)]}
    FC.apply_face_corrections(ratings, [_spot("Fort Point"), _spot("Steamer Lane")],
                              factors={"steamer-lane": _rec(2.0)}, slug_for=_slug, now=TODAY)
    e = ratings["Fort Point"][0]
    assert e["face_ft"] == 8.0 and e["face_ft_raw"] == 8.0


def test_raw_equals_face_when_there_are_no_factors_at_all():
    """A fresh checkout with no factors file. The seam short-circuits, and it stamps FIRST —
    otherwise local rows would be indistinguishable from pre-migration ones."""
    ratings = {"Steamer Lane": [_entry(face=8.0)]}
    st = FC.apply_face_corrections(ratings, [_spot()], factors={}, slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 8.0 and e["face_ft_raw"] == 8.0
    assert e["face_correction_version"] == "1:none:none", e
    assert st["stamp"] == "1:none:none"


# --------------------------------------------------------------------------- #
# 3 — the version stamp is on EVERY row                                         #
# --------------------------------------------------------------------------- #

def test_every_row_of_every_spot_is_stamped_whatever_path_it_took():
    """Four spots, four different paths through the seam, one stamp on all of them. A stamp
    that varied with the outcome would answer the wrong question: what has to be comparable
    is 'were these rows produced by the same regime', not 'was this row corrected'."""
    ratings = {
        "Steamer Lane": [_entry(), _entry(face=4.0)],       # corrected, two hours
        "Ocean Beach": [_entry()],                          # no factor
        "Mop Spot": [_entry()],                             # MOP tier
        "Ghost Spot": [_entry()],                           # not on the roster at all
    }
    st = FC.apply_face_corrections(
        ratings,
        [_spot("Steamer Lane"), _spot("Ocean Beach"), _spot("Mop Spot", source="cdip_mop")],
        factors={"steamer-lane": _rec(2.0)}, slug_for=_slug, now=TODAY)
    seen = {e["face_correction_version"] for es in ratings.values() for e in es}
    assert len(seen) == 1, seen
    assert seen == {st["stamp"]}, (seen, st["stamp"])
    n_rows = sum(len(es) for es in ratings.values())
    assert n_rows == 5
    assert all("face_ft_raw" in e for es in ratings.values() for e in es)


def test_the_stamp_format_is_code_date_fingerprint():
    """Asserted against a hash computed HERE from a hand-written string, not by calling the
    function under test. The canonical form is "<slug>=<factor:.10g>" lines, sorted, newline
    joined — %g rather than repr so the fingerprint does not depend on float formatting."""
    factors = {"b-spot": _rec(2.0, measured_on="2026-09-01"),
               "a-spot": _rec(1.5, measured_on="2026-08-30")}
    canonical = "a-spot=1.5\nb-spot=2"
    expect = f"1:2026-09-01:{hashlib.sha256(canonical.encode()).hexdigest()[:8]}"
    assert FC.face_correction_stamp(factors) == expect, FC.face_correction_stamp(factors)
    assert FC.FACE_CORRECTION_VERSION == 1
    # The date is the NEWEST measured_on, not the first encountered or the alphabetically
    # smallest — a partially re-measured file must read as the newer measurement.
    assert expect.split(":")[1] == "2026-09-01"


def test_a_regeneration_that_moves_a_divisor_changes_the_fingerprint():
    """This is what makes a contaminated window a query. steamer-lane really did go 2.8702 ->
    2.8084 between two committed files, and the two must not stamp alike."""
    a = FC.face_correction_stamp({"steamer-lane": _rec(2.8702)})
    b = FC.face_correction_stamp({"steamer-lane": _rec(2.8084)})
    assert a != b, (a, b)
    # ...and a regeneration that moves NOTHING does not, so the stamp is not just a run id.
    assert a == FC.face_correction_stamp({"steamer-lane": _rec(2.8702)})
    # A spot LEAVING the file changes it too — that is the 724f442 regime, where steamer-lane
    # was absent and therefore not corrected at all.
    assert a != FC.face_correction_stamp({"steamer-lane": _rec(2.8702), "other": _rec(1.1)})
    assert FC.face_correction_stamp({}) == "1:none:none"


def test_the_code_version_is_the_first_field_so_it_can_be_split_out():
    """The harness compares seam CODE versions across rows and ignores fingerprint drift —
    raw is pre-correction whatever divisor was applied, but only with respect to one seam's
    arithmetic. That split has to be positional and stable."""
    s = FC.face_correction_stamp({"x": _rec(2.0)})
    assert s.split(":", 1)[0] == "1"
    assert len(s.split(":")) == 3, s


# --------------------------------------------------------------------------- #
# 4 — db_import carries both keys, and NEVER derives them                       #
# --------------------------------------------------------------------------- #

class _FakeTable:
    def __init__(self, sink):
        self.sink = sink

    def upsert(self, chunk, **_kw):
        self.sink.extend(chunk)
        return self

    def execute(self):
        return self


class _FakeClient:
    """Captures whatever db_import would have sent to PostgREST."""

    def __init__(self):
        self.sent = []

    def table(self, _name):
        return _FakeTable(self.sent)


def _import_rows(ratings, tmpdir):
    from pipeline import db_import
    path = Path(tmpdir) / "ratings.json"
    path.write_text(json.dumps(ratings))
    client = _FakeClient()
    saved = db_import._spot_id_map
    try:
        db_import._spot_id_map = lambda _c: {"Steamer Lane": 1}
        db_import.import_forecasts(client, ratings_path=path)
    finally:
        db_import._spot_id_map = saved
    return client.sent


def test_db_import_sends_both_provenance_keys_to_the_database():
    """Behavioural, not a source grep: the record db_import actually builds is inspected."""
    import tempfile
    rows = _import_rows({"Steamer Lane": [{
        "valid_time": "2026-09-07T10:00:00Z", "face_ft": 4.0, "face_ft_raw": 8.0,
        "face_correction_version": "1:2026-09-01:abcd1234", "effective_size_ft": 3.0,
    }]}, tempfile.mkdtemp())
    assert len(rows) == 1, rows
    assert rows[0]["face_ft"] == 4.0
    assert rows[0]["face_ft_raw"] == 8.0
    assert rows[0]["face_correction_version"] == "1:2026-09-01:abcd1234"


def test_db_import_does_NOT_coalesce_a_missing_raw_to_the_published_face():
    """THE COALESCE THAT MUST NOT EXIST. `h.get("face_ft_raw", h.get("face_ft"))` would make
    the column always populated and would paper over a seam that corrected without stamping —
    the row would then read as clean while holding a corrected face. NULL is the correct
    value for "this row's correction state is unknown", and it is what a ratings.json written
    by a pre-017 interpret must produce."""
    import tempfile
    rows = _import_rows({"Steamer Lane": [{
        "valid_time": "2026-09-07T10:00:00Z", "face_ft": 4.0, "effective_size_ft": 3.0,
    }]}, tempfile.mkdtemp())
    assert len(rows) == 1, rows
    assert rows[0]["face_ft"] == 4.0
    assert rows[0]["face_ft_raw"] is None, "must NOT fall back to face_ft"
    assert rows[0]["face_correction_version"] is None
    # The keys must still be PRESENT: PostgREST NULLs any key missing from a row of a bulk
    # upsert, so a ragged key set across a batch is its own bug (see db_import's own comment).
    assert "face_ft_raw" in rows[0] and "face_correction_version" in rows[0]


# --------------------------------------------------------------------------- #
# 5 — the harness prefers raw, and is loud about a mixture                      #
# --------------------------------------------------------------------------- #

def test_the_harness_prefers_raw_over_the_corrected_face():
    import mop_face_validation as MFV
    # Both present: raw wins. 8.0 and 4.0 are literals; the point is which one comes back.
    assert MFV.measured_face({"face_ft": 4.0, "face_ft_raw": 8.0}) == (8.0, "raw")
    # Only the published face: fall back, and SAY it is a fallback.
    assert MFV.measured_face({"face_ft": 4.0, "face_ft_raw": None}) == (4.0, "fallback")
    assert MFV.measured_face({"face_ft": 4.0}) == (4.0, "fallback")
    # A raw of 0.0 is a value, not an absence — `or` instead of `is not None` would drop it.
    assert MFV.measured_face({"face_ft": 4.0, "face_ft_raw": 0.0}) == (0.0, "raw")


def test_an_unrateable_hour_does_NOT_make_a_clean_window_look_mixed():
    """THE FALSE POSITIVE THE CLASSIFIER WAS REDESIGNED TO AVOID, and it was found by
    mutation rather than by reading.

    face_ft_raw is NULL on an hour whose face_ft is NULL — the seam copies face_ft verbatim,
    nulls included. Classifying on the raw COLUMN would file every such hour as
    unknown-provenance, flag an entirely post-017 window MIXED, and print the loud banner
    over nothing. The stamp has no such hole: it is written on every hour the seam sees.

    Here: three good hours and two unrateable ones, all from the same post-017 run. Clean,
    nothing dropped, and the two contribute no ratio because their face is None."""
    import mop_face_validation as MFV
    pairs = ([_pair(1.0, 6.562, 3.281)] * 3
             + [_pair(1.0, None, None)] * 2)     # stamped, but no face to measure
    got = MFV.summarise_spot(pairs, mop_hours=5, our_hours=5)
    assert got["provenance"]["verdict"] == "clean", got["provenance"]
    assert got["provenance"]["dropped"] == 0, "nothing may be dropped from a clean window"
    assert got["face_ratio"]["n"] == 3, "the unrateable hours contribute no ratio"
    assert got["face_ratio"]["median"] == 2.0


def test_the_classifier_reads_the_stamp_not_the_raw_column():
    import mop_face_validation as MFV
    assert MFV.is_stamped({"face_correction_version": STAMP}) is True
    assert MFV.is_stamped({"face_correction_version": None}) is False
    assert MFV.is_stamped({}) is False
    # Stamped with no raw — an unrateable post-017 hour — is STAMPED, which is the whole fix.
    assert MFV.is_stamped({"face_correction_version": STAMP, "face_ft_raw": None}) is True
    # Raw but unstamped is treated as unstamped: conservative, and only reachable by hand.
    assert MFV.is_stamped({"face_ft_raw": 8.0}) is False


def test_the_window_verdict_names_the_four_states():
    import mop_face_validation as MFV
    assert MFV.classify_window(10, 0) == "clean"
    assert MFV.classify_window(0, 10) == "legacy"
    assert MFV.classify_window(6, 4) == "mixed"
    assert MFV.classify_window(0, 0) == "empty"
    # One row of either kind is enough to make it mixed — there is no tolerance threshold,
    # because a threshold is a number someone would later tune to make a warning stop.
    assert MFV.classify_window(1, 999) == "mixed"
    assert MFV.classify_window(999, 1) == "mixed"


def test_a_mixed_window_DROPS_the_unknown_rows_rather_than_averaging_them():
    """The contamination, in miniature. Three raw rows and two of unknown state: the two are
    excluded, not blended in."""
    import mop_face_validation as MFV
    pairs = ([{"face_correction_version": STAMP}] * 3 + [{"face_correction_version": None}] * 2)
    kept = MFV.retain_for_measurement(pairs)
    assert len(kept) == 3, kept
    assert all(p["face_correction_version"] == STAMP for p in kept)
    # Clean and legacy windows keep everything — in both, every row is of the SAME
    # provenance, and internal consistency is the property that was violated.
    assert len(MFV.retain_for_measurement([{"face_correction_version": STAMP}] * 4)) == 4
    assert len(MFV.retain_for_measurement([{"face_correction_version": None}] * 4)) == 4
    assert MFV.retain_for_measurement([]) == []


def test_the_join_carries_both_faces_apart():
    """The published face must stay reachable under its own name: the blocked-hours report
    asks 'what did we publish there', and answering it with the raw face would be a second
    contamination of the same shape as the first."""
    import datetime as _dt

    import mop_face_validation as MFV
    # The join key is floor(epoch / 3600). Written out, and cross-checked against datetime
    # rather than against hour_of, so the fixture cannot agree with a broken key function.
    hour_ix = 496882
    assert hour_ix == int(_dt.datetime(2026, 9, 7, 10, 0, 0,
                                       tzinfo=_dt.timezone.utc).timestamp()) // 3600
    rows = [{"valid_time": "2026-09-07T10:00:00Z", "face_ft": 4.0, "face_ft_raw": 8.0,
             "face_correction_version": "1:2026-09-01:abcd1234",
             "effective_size_ft": 3.0, "swell_source": "nwps"}]
    pairs = MFV.join_on_hour({hour_ix: 1.0}, rows)
    assert len(pairs) == 1, pairs
    p = pairs[0]
    assert p["face_ft"] == 4.0, "published, verbatim"
    assert p["face_measured"] == 8.0, "pre-correction, for the ratio"
    assert p["face_source"] == "raw"
    assert p["face_correction_version"] == "1:2026-09-01:abcd1234"


# A joined pair. `version` is what CLASSIFIES it — see is_stamped — so it defaults to a real
# stamp and the pre-017 case is written out explicitly as version=None.
STAMP = "1:2026-09-01:aaaaaaaa"


def _pair(mop_hs, face_measured, face_ft, eff=None, version=STAMP, swell="nwps"):
    return {"mop_hs_m": mop_hs, "face_measured": face_measured, "face_ft": face_ft,
            "face_source": "raw" if version else "fallback",
            "effective_size_ft": eff if eff is not None else face_ft,
            "face_correction_version": version, "swell_source": swell}


def test_the_spot_summary_measures_ONLY_the_retained_rows():
    """THE CONTAMINATION, END TO END THROUGH THE AGGREGATION.

    Three raw rows whose pre-correction ratio is exactly 2.0, and three unknown-state rows
    whose ratio is exactly 1.0. Pooled, the median would be 1.5 — a number belonging to
    neither population, which is precisely the 2.188 that started this. The unknown rows are
    dropped, so the median is 2.0.

    Every literal here is hand-computed: MOP Hs 1.0 m is 3.281 ft, so a face of 6.562 ft is
    a ratio of 2.0 and a face of 3.281 ft is a ratio of 1.0.
    """
    import mop_face_validation as MFV
    pairs = ([_pair(1.0, 6.562, 3.281)] * 3
             + [_pair(1.0, 3.281, 3.281, version=None)] * 3)
    got = MFV.summarise_spot(pairs, mop_hours=6, our_hours=6)
    assert got["face_ratio"]["median"] == 2.0, got["face_ratio"]
    assert got["face_ratio"]["n"] == 3, "the three unknown rows are not in the sample"
    assert got["provenance"] == {"verdict": "mixed", "raw_rows": 3, "fallback_rows": 3,
                                 "dropped": 3, "stamps": {STAMP: 3}}, got
    # The stamp tally counts the RETAINED rows. Counting all of them would report a sample
    # size the statistics were never computed over.
    assert sum(got["provenance"]["stamps"].values()) == got["joined_hours"]


def test_joined_hours_is_the_RETAINED_count_not_the_joined_one():
    """build_face_factors excludes on p75/p25 and reads this count. Reporting the pre-drop
    number would let a spot be judged on a sample size it never had — a dropped window would
    read as an unstable spot, which is the misreading this whole change exists to prevent."""
    import mop_face_validation as MFV
    pairs = ([_pair(1.0, 6.562, 3.281)] * 2
             + [_pair(1.0, 3.281, 3.281, version=None)] * 8)
    got = MFV.summarise_spot(pairs, mop_hours=10, our_hours=10)
    assert got["joined_hours"] == 2, got["joined_hours"]
    assert got["join_rate"] == 0.2, got["join_rate"]      # 2 retained / 10 fetched
    assert got["provenance"]["dropped"] == 8


def test_a_clean_spot_summary_keeps_every_row_and_says_clean():
    import mop_face_validation as MFV
    pairs = [_pair(1.0, 6.562, 3.281)] * 4
    got = MFV.summarise_spot(pairs, mop_hours=4, our_hours=4)
    assert got["provenance"]["verdict"] == "clean" and got["provenance"]["dropped"] == 0
    assert got["joined_hours"] == 4 and got["face_ratio"]["n"] == 4
    assert got["face_ratio"]["median"] == 2.0


def test_the_blocked_hours_report_still_uses_the_PUBLISHED_face():
    """"What did we publish in hours MOP says the swell did not arrive" is a question about
    what a reader saw, so it takes the corrected number. Answering it with the raw face
    would be a second contamination of the same shape as the first."""
    import mop_face_validation as MFV
    # MOP_HS_FLOOR_M is the "swell did not arrive" threshold; 0.0 is under any floor.
    pairs = [_pair(0.0, 6.562, 3.281)] * 2
    got = MFV.summarise_spot(pairs, mop_hours=2, our_hours=2)
    assert got["blocked_hours"]["n"] == 2
    assert got["blocked_hours"]["published_face_ft"]["median"] == 3.281, \
        "published, not the 6.562 raw"


def test_an_empty_spot_summary_does_not_divide_by_zero():
    import mop_face_validation as MFV
    got = MFV.summarise_spot([], mop_hours=0, our_hours=0)
    assert got["joined_hours"] == 0 and got["join_rate"] is None
    assert got["face_ratio"]["n"] == 0
    assert got["provenance"]["verdict"] == "empty"


def test_the_mixed_banner_says_what_was_dropped_and_why(capsys=None):
    """Loud, and specific about the consequence: a shortened window widens p75/p25, and
    build_face_factors excludes on p75/p25 — so a spot can be dropped for sample size and
    read as unstable. That is the mistake this whole change exists to prevent."""
    import io
    import contextlib
    import mop_face_validation as MFV
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        MFV._print_window_integrity({"verdict": "mixed", "raw_rows": 120, "fallback_rows": 216,
                                     "dropped_rows": 216, "seam_code_versions": ["1"]})
    out = buf.getvalue()
    assert "MIXED WINDOW" in out and "NOT safe to apply".upper() in out.upper(), out
    assert "216" in out and "120" in out, out
    assert "p75/p25" in out, "must name the exclusion rule it interacts with"
    assert "!" * 20 in out, "must be visually loud"
    # A clean window is QUIET, so that the loud case stays loud.
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        MFV._print_window_integrity({"verdict": "clean", "raw_rows": 336, "fallback_rows": 0,
                                     "dropped_rows": 0, "seam_code_versions": ["1"]})
    assert "!" not in buf2.getvalue(), buf2.getvalue()
    assert "clean" in buf2.getvalue()


def test_a_differing_seam_code_version_is_called_out_separately():
    """Fingerprint drift is benign once raw is stored; a differing seam CODE version is not,
    because raw is only pre-correction with respect to one seam's arithmetic."""
    import io
    import contextlib
    import mop_face_validation as MFV
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        MFV._print_window_integrity({"verdict": "clean", "raw_rows": 336, "fallback_rows": 0,
                                     "dropped_rows": 0, "seam_code_versions": ["1", "2"]})
    assert "SEAM CODE VERSIONS DIFFER" in buf.getvalue(), buf.getvalue()


# --------------------------------------------------------------------------- #
# 6 — the migration and the committed factor file                               #
# --------------------------------------------------------------------------- #

def test_the_migration_adds_both_columns_and_is_idempotent():
    sql = (Path(__file__).resolve().parents[1]
           / "migrations" / "017_face_raw_provenance.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS face_ft_raw DOUBLE PRECISION" in sql
    assert "ADD COLUMN IF NOT EXISTS face_correction_version TEXT" in sql
    # The deployment order is load-bearing: code before migration fails the whole forecasts
    # upsert with PGRST204 and publishes NO rows. It must be stated in the file itself.
    assert "RUN THIS MIGRATION FIRST" in sql, "the ordering hazard must be written down"


def test_the_committed_factors_are_UNCHANGED_by_this_work():
    """130 spots, measured_on 2026-09-01. Nothing here regenerates them, and nothing may:
    they stay as they are until a clean 14-day window exists."""
    p = Path(__file__).resolve().parents[1] / "data" / "spot_face_factors.json"
    doc = json.loads(p.read_text())
    assert len(doc["factors"]) == 130
    assert doc["measurement"]["run_on"] == "2026-09-01"
    assert doc["factors"]["steamer-lane"]["factor"] == 2.8084
    assert {r["measured_on"] for r in doc["factors"].values()} == {"2026-09-01"}


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_face_provenance: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
