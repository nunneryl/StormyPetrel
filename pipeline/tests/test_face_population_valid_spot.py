"""The MOP face harness's population must exclude spots the importer rejects.

THE DEFECT THIS PINS. scripts/mop_face_validation.py reads pipeline/spots_enriched.json
directly — the full enrichment record, including entries enrichment itself judged not to be
surf spots. pipeline/db_import.py:473 drops those on the way into the `spots` table, so the
table is the SUBSET that survived judgement. Until is_population grew the same clause the
two populations were silently different, and the harness was measuring MOP against a break
the site does not publish and has no row for.

IDENTITY, NOT TRUTHINESS. db_import's rule is `is not False`, which keeps the 49 roster
entries that carry NO is_valid_surf_spot key and drops only the 2 that are explicitly false.
(The roster has no explicit JSON nulls in this field — unverified means the key is absent.
.get() flattens the two cases to None, so both are covered below.) is_population mirrors the
rule as `is False`. Writing `if not spot.get("is_valid_surf_spot")` instead would look
equivalent and would additionally drop every unverified spot — including seal-beach-pier,
the real break this filter exists to keep. The tests below separate those two readings,
because nothing else in the suite does.

NO EXPECTED VALUE HERE IS OBTAINED BY CALLING is_population. The before-count comes from a
longhand copy of the three clauses that predate this change; the after-count, the removed
spot and the distribution are written-out literals.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

from mop_ca_rollout import _slug            # noqa: E402
from mop_face_validation import ROSTER, is_population  # noqa: E402

CA_NWPS = {"region_hint": "California", "swell_window_source": "nwps", "name": "A"}


def _roster():
    return json.load(open(ROSTER))


def _population_before_this_change(spot):
    """The predicate as it stood before the is_valid_surf_spot clause — longhand.

    A SECOND IMPLEMENTATION ON PURPOSE. Using is_population to compute the "before" count
    would make the delta agree with the change by construction and pin nothing.
    """
    return (
        spot.get("region_hint") == "California"
        and spot.get("swell_window_source") == "nwps"
        and not any(k.startswith("mop_") for k in spot)
    )


# --------------------------------------------------------------------------- #
# The unit behaviour: which values are rejected, and which merely look like it. #
# --------------------------------------------------------------------------- #

def test_an_explicit_false_is_excluded():
    assert is_population({**CA_NWPS, "is_valid_surf_spot": False}) is False


def test_an_explicit_true_is_kept():
    assert is_population({**CA_NWPS, "is_valid_surf_spot": True}) is True


def test_a_null_is_kept_because_unverified_is_not_rejected():
    """49 roster entries have never been verified either way. They are not rejections."""
    assert is_population({**CA_NWPS, "is_valid_surf_spot": None}) is True


def test_the_key_being_absent_is_kept():
    assert "is_valid_surf_spot" not in CA_NWPS
    assert is_population(CA_NWPS) is True


def test_falsy_is_not_false():
    """The whole point of the identity check, stated as three values that would flip a
    truthiness test and must not flip this one."""
    for falsy in (None, 0, 0.0, "", [], {}):
        assert bool(falsy) is False, falsy            # genuinely falsy
        assert (falsy is False) is False, falsy       # and genuinely not False
        assert is_population({**CA_NWPS, "is_valid_surf_spot": falsy}) is True, falsy


def test_zero_and_empty_string_are_not_false_in_this_language():
    """Pinned separately so the distinction survives even if the loop above is edited.

    Bound to names first: `0 is False` written inline is a SyntaxWarning, and the point is
    the runtime comparison, not the literal.
    """
    zero, empty, false = 0, "", False
    assert zero == false            # equal — which is exactly what makes `is` load-bearing
    assert (zero is false) is False
    assert (empty is false) is False
    assert (false is false) is True


def test_the_clause_does_not_override_the_other_three():
    """is_valid_surf_spot true must not readmit a spot the older clauses exclude."""
    valid = {"is_valid_surf_spot": True}
    assert is_population({**CA_NWPS, **valid, "region_hint": "Hawaii"}) is False
    assert is_population({**CA_NWPS, **valid, "swell_window_source": "cdip_mop"}) is False
    assert is_population({**CA_NWPS, **valid, "mop_point_id": "D0515"}) is False


# --------------------------------------------------------------------------- #
# The committed roster: the counts, and the one entry that moves.              #
# --------------------------------------------------------------------------- #

def test_the_population_goes_from_153_to_152():
    """Both literals written out. The before-count uses the longhand predicate above."""
    roster = _roster()
    before = sum(1 for s in roster if _population_before_this_change(s))
    after = sum(1 for s in roster if is_population(s))
    assert before == 153
    assert after == 152
    assert before - after == 1


def test_the_single_removed_spot_is_seal_beach_california():
    roster = _roster()
    removed = [
        s for s in roster
        if _population_before_this_change(s) and not is_population(s)
    ]
    assert [_slug(s["name"]) for s in removed] == ["seal-beach-california"]
    (spot,) = removed
    assert spot["name"] == "Seal Beach, California"
    assert spot["is_valid_surf_spot"] is False
    assert spot["invalid_reason"] == "duplicate"
    assert spot["verification_confidence"] == "high"


def test_the_removed_entry_is_the_city_article_not_a_break():
    """The record's own provenance, so the comment in is_population is not taken on trust.

    Q593039 is the Wikipedia/Wikidata entity for the CITY of Seal Beach; the coordinate is
    the town centre, inside Anaheim Bay.
    """
    roster = _roster()
    (spot,) = [s for s in roster if _slug(s.get("name") or "") == "seal-beach-california"]
    assert spot["sources"]["wikidata_id"] == "Q593039"
    assert spot["sources"]["wikipedia_url"].endswith("/Seal_Beach,_California")
    notes = spot["verification_notes"]
    assert "the actual surf break is Seal Beach Pier" in notes
    assert "Anaheim Bay" in notes


def test_the_city_record_sits_3_3_km_from_the_break_it_duplicates():
    """Computed from the two roster coordinates with a haversine written out here.

    Deliberately NOT the harness's own _match distance: that is measured against
    scripts/mop_points.json, which is gitignored and absent from any clone. This is
    spot-to-spot, reproducible from committed data alone, and it is the separation that
    makes "duplicate" the right verdict.
    """
    import math

    roster = _roster()
    by_slug = {_slug(s.get("name") or ""): s for s in roster}
    a, b = by_slug["seal-beach-california"], by_slug["seal-beach-pier"]

    r = 6371000.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp = math.radians(b["lat"] - a["lat"])
    dl = math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    metres = 2 * r * math.asin(math.sqrt(h))

    assert 3300 < metres < 3400, metres        # 3344 m as committed
    assert a["lat"] > b["lat"] and a["lng"] > b["lng"]   # north-east of the pier, in the bay


def test_the_real_break_is_a_different_entry_and_survives():
    """seal-beach-pier is the break; the removed entry is a Wikidata record for the CITY.

    It carries no is_valid_surf_spot key at all, so it is exactly the case a truthiness test
    would have taken out along with the duplicate. This is the assertion that makes the
    identity check load-bearing rather than stylistic.
    """
    roster = _roster()
    pier = [s for s in roster if _slug(s.get("name") or "") == "seal-beach-pier"]
    assert len(pier) == 1
    (pier,) = pier
    assert "is_valid_surf_spot" not in pier
    assert pier.get("is_valid_surf_spot") is None
    assert is_population(pier) is True


def test_unverified_means_the_key_is_absent_not_a_json_null():
    """Pinned because the two are indistinguishable through .get() and only one is real.

    If a future writer starts emitting an explicit null this fails, which is the moment to
    re-read the identity check rather than discover later that it still happens to work.
    """
    roster = _roster()
    explicit_null = [s for s in roster if "is_valid_surf_spot" in s
                     and s["is_valid_surf_spot"] is None]
    assert explicit_null == [], [s.get("name") for s in explicit_null]
    absent = [s for s in roster if "is_valid_surf_spot" not in s]
    assert len(absent) == 49


def test_a_truthiness_test_would_have_dropped_seventeen_not_one():
    """The measured cost of the wrong operator, over the committed roster.

    136 true + 16 null + 1 false = the 153 the predicate used to return. `if not
    spot.get(...)` keeps only the 136.
    """
    roster = _roster()
    before = [s for s in roster if _population_before_this_change(s)]
    n_true = sum(1 for s in before if s.get("is_valid_surf_spot") is True)
    n_null = sum(1 for s in before if s.get("is_valid_surf_spot") is None)
    n_false = sum(1 for s in before if s.get("is_valid_surf_spot") is False)
    assert (n_true, n_null, n_false) == (136, 16, 1)
    assert n_true + n_null + n_false == 153

    truthy_kept = [s for s in before if s.get("is_valid_surf_spot")]
    assert len(truthy_kept) == 136
    dropped_by_truthiness = {_slug(s["name"]) for s in before if not s.get("is_valid_surf_spot")}
    assert len(dropped_by_truthiness) == 17
    assert "seal-beach-pier" in dropped_by_truthiness


def test_only_one_of_the_rosters_two_invalid_entries_was_ever_in_this_population():
    """The other, lewes-street-surf-beach, is in Delaware and was already out on region."""
    roster = _roster()
    invalid = {_slug(s["name"]) for s in roster if s.get("is_valid_surf_spot") is False}
    assert invalid == {"seal-beach-california", "lewes-street-surf-beach"}
    lewes = [s for s in roster if _slug(s.get("name") or "") == "lewes-street-surf-beach"][0]
    assert lewes["region_hint"] == "Delaware"
    assert _population_before_this_change(lewes) is False


def test_the_other_california_seal_beach_entries_are_untouched():
    """Three roster entries share the name. Only the city record leaves."""
    roster = _roster()
    by_slug = {_slug(s.get("name") or ""): s for s in roster}
    assert is_population(by_slug["seal-beach-pier"]) is True
    assert is_population(by_slug["seal-beach-jetty"]) is True
    assert by_slug["seal-beach-jetty"]["is_valid_surf_spot"] is True
    assert is_population(by_slug["seal-beach-california"]) is False


# --------------------------------------------------------------------------- #
# The mirror: the harness population must be a subset of what db_import keeps.  #
# --------------------------------------------------------------------------- #

def test_no_spot_in_the_population_would_be_dropped_by_db_import():
    """The invariant the change exists to establish, asserted over the whole roster.

    db_import's rule is the SPEC here, not the code under test — it is the thing the
    harness is being brought into line with.
    """
    roster = _roster()
    db_import_keeps = [
        s for s in roster
        if s.get("name")
        and s.get("lat") is not None
        and s.get("lng") is not None
        and s.get("is_valid_surf_spot") is not False
    ]
    kept = {id(s) for s in db_import_keeps}
    orphans = [s for s in roster if is_population(s) and id(s) not in kept]
    assert orphans == [], [s.get("name") for s in orphans]


def test_db_imports_rule_is_still_an_identity_check():
    """If the importer ever switches to truthiness, this harness must be revisited, not
    silently left behind. Read from source: importing the module cannot observe the
    operator, only its effect on whatever happens to be in the roster today."""
    src = open(os.path.join(ROOT, "pipeline", "db_import.py")).read()
    assert 's.get("is_valid_surf_spot") is not False' in src


def test_the_harness_selftest_pins_the_same_count_as_this_file():
    """`--selftest` is not wired into CI or into any test here, so a stale literal in it is
    invisible until someone runs it by hand — which mutation testing confirmed: reverting
    that one number to 153 failed the selftest (exit 1) and passed the whole suite. Pinned
    from source so the harness's own count and this file's cannot drift apart.
    """
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert "n_pop == 152" in src
    assert "n_pop == 153" not in src


def test_the_harness_records_why_it_needs_its_own_copy_of_the_filter():
    """The reason is the asset. A bare clause invites someone to delete it as redundant
    with db_import — which it is not, because the two read different populations."""
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert 'spot.get("is_valid_surf_spot") is False' in src
    assert "IDENTITY, NOT TRUTHINESS" in src
    # The two halves of the justification, each pinned by the sentence that carries it:
    # WHICH rule is being mirrored and where it lives, and WHY a second copy is needed.
    assert "mirroring db_import.py:473's `is not False`" in src
    assert "pipeline/spots_enriched.json" in src
    assert "SUBSET that survived that judgement" in src
    # and the evidence for the one spot it removes, so the claim stays checkable
    assert "Q593039" in src
