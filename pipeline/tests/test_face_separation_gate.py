"""MATCH_SEPARATION_M: the face harness's geometric-separation gate, at 2200 m.

WHAT THESE TESTS CAN AND CANNOT PIN. The constant was chosen from a separation
distribution measured off-repo, against scripts/mop_points.json — gitignored, absent from
any clone, and required by _match. No test here can recompute a single spot's distance, so
none tries. What IS pinned is the gate's behaviour at given distances, which is pure
arithmetic on its arguments, plus the facts that survive in committed data.

The distinction matters and is enforced below: assertions of the form
`match_verdict(2439.3, 73.0)[0] is False` say "IF a pairing is 2439 m apart THEN this gate
rejects it". They do not say Bolinas is 2439 m from its MOP point. That figure, and every
percentile in the constant's comment, is a reported measurement this repo cannot check.

WHY THE GATE EXISTS AT ALL is argued in the constant's comment block, not here, and the
one-line summary is that it is EMPIRICAL: a cut placed in the widest gap of an observed
distribution, with no physical threshold behind it.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

from mop_ca_rollout import MATCH_SANITY_M, _slug        # noqa: E402
from mop_face_validation import (                       # noqa: E402
    MATCH_SEPARATION_M,
    ROSTER,
    match_verdict,
)
from mop_handful_slice import MATCH_FALLBACK_M          # noqa: E402

# An angle comfortably inside FACE_SHORE_NORMAL_MAX_DELTA (90), so every verdict below
# turns on distance alone and never on aspect.
ALIGNED = 10.0


# --------------------------------------------------------------------------- #
# The constant itself.                                                         #
# --------------------------------------------------------------------------- #

def test_the_gate_is_2200_metres():
    assert MATCH_SEPARATION_M == 2200.0


def test_it_is_not_the_adoption_paths_publishing_threshold():
    """The whole argument in one assertion: this is a PAIRING test, MATCH_FALLBACK_M is a
    PUBLISHING threshold, and borrowing the latter would repeat the 35-deg mistake."""
    assert MATCH_FALLBACK_M == 1200.0
    assert MATCH_SEPARATION_M != MATCH_FALLBACK_M
    assert MATCH_SEPARATION_M > MATCH_FALLBACK_M


def test_it_sits_between_the_publishing_threshold_and_the_sanity_cap():
    assert MATCH_FALLBACK_M < MATCH_SEPARATION_M < MATCH_SANITY_M


def test_the_adoption_path_constant_is_untouched():
    """Adding a gate here must not move the path that decides what gets published."""
    import mop_handful_slice

    assert mop_handful_slice.MATCH_FALLBACK_M == 1200.0
    src = open(os.path.join(ROOT, "scripts", "mop_handful_slice.py")).read()
    assert "MATCH_FALLBACK_M = 1200.0" in src
    assert "MATCH_SEPARATION_M" not in src, "the separation gate belongs to the face path"


# --------------------------------------------------------------------------- #
# The boundary. Literals throughout — writing these as MATCH_SEPARATION_M +/- eps
# would make them true for any value of the constant and pin nothing.           #
# --------------------------------------------------------------------------- #

def test_the_gate_is_inclusive_at_its_own_boundary():
    assert match_verdict(2200.0, ALIGNED)[0] is True
    assert match_verdict(2200.1, ALIGNED)[0] is False


def test_just_inside_and_just_outside():
    assert match_verdict(2199.0, ALIGNED)[0] is True
    assert match_verdict(2201.0, ALIGNED)[0] is False


def test_the_healthy_bulk_of_the_distribution_still_passes():
    """The reported p50/p75/p90/p95 all sit below the gate, which is the point of it
    being at 2200 rather than at 1200. Asserted as verdicts at those distances."""
    for metres in (648.0, 888.0, 1340.0, 1623.0):
        assert match_verdict(metres, ALIGNED)[0] is True, metres


def test_1200_would_have_cut_into_that_bulk_and_2200_does_not():
    """The concrete cost of borrowing MATCH_FALLBACK_M, stated as two verdicts on the same
    distance. 1340 m is the reported p90: above the publishing threshold, below this gate.
    """
    p90 = 1340.0
    assert p90 > MATCH_FALLBACK_M          # the adoption path would have dropped it
    assert p90 < MATCH_SEPARATION_M        # this gate keeps it
    assert match_verdict(p90, ALIGNED)[0] is True


def test_a_five_kilometre_match_is_rejected_however_good_its_angle():
    assert match_verdict(5000.0, 0.0)[0] is False
    assert match_verdict(5000.0, ALIGNED)[0] is False


# --------------------------------------------------------------------------- #
# Which gate fired. The reason strings diagnose different problems.            #
# --------------------------------------------------------------------------- #

def test_a_loose_pairing_reports_separation_not_the_sanity_cap():
    ok, why = match_verdict(2753.0, ALIGNED)
    assert ok is False
    assert "separation gate" in why
    assert "2753" in why


def test_a_coverage_hole_still_reports_the_sanity_cap():
    """25 km subsumes 2.2 km, so the cap no longer gates independently — it diagnoses.
    'no MOP coverage on this stretch' and 'this pairing is too loose' are different
    problems with different fixes, and the reason string must keep saying which."""
    ok, why = match_verdict(31_400.0, ALIGNED)
    assert ok is False
    assert "km away" in why
    assert "separation gate" not in why


def test_an_accepted_pairing_says_ok():
    ok, why = match_verdict(600.0, ALIGNED)
    assert ok is True
    assert why == "ok"


def test_a_pairing_that_fails_both_gates_reports_the_distance():
    """ORDER IS DELIBERATE: separation is checked before aspect.

    The angle of a point 3 km away is beside the point — the pairing is already unusable,
    and reporting the aspect sends a reader off to check an orientation that was never the
    problem. Distance is also the cheaper thing to fix (match a different point); aspect
    usually is not.
    """
    ok, why = match_verdict(3000.0, 120.0)
    assert ok is False
    assert "separation gate" in why
    assert "shore-normal" not in why


def test_a_far_pairing_with_no_shore_normal_still_reports_the_distance():
    """Same argument, and it matters more here: "no metaShoreNormal" reads as a repairable
    cache entry, and repairing it would not save a pairing this gate rejects anyway."""
    ok, why = match_verdict(3000.0, None)
    assert ok is False
    assert "separation gate" in why
    assert "metaShoreNormal" not in why


def test_a_near_pairing_with_no_shore_normal_still_reports_the_missing_normal():
    """The converse, so the test above pins ordering rather than deleting a behaviour."""
    ok, why = match_verdict(600.0, None)
    assert ok is False
    assert "metaShoreNormal" in why


# --------------------------------------------------------------------------- #
# Independence from the angle gate.                                            #
# --------------------------------------------------------------------------- #

def test_distance_and_angle_reject_independently():
    assert match_verdict(600.0, 10.0)[0] is True      # both fine
    assert match_verdict(600.0, 120.0)[0] is False    # angle alone
    assert match_verdict(3000.0, 10.0)[0] is False    # distance alone
    assert match_verdict(3000.0, 120.0)[0] is False   # both


def test_a_perfect_angle_cannot_rescue_a_far_pairing():
    """No trade-off between the two gates: they are conjunctive, not scored."""
    for delta in (0.0, 1.0, 45.0, 89.0, 90.0):
        assert match_verdict(2500.0, delta)[0] is False, delta


def test_a_close_pairing_still_needs_an_angle():
    assert match_verdict(100.0, None)[0] is False


# --------------------------------------------------------------------------- #
# The spots the gate is reported to change, by name, without asserting their    #
# distances. Membership in the population IS checkable; separation is not.      #
# --------------------------------------------------------------------------- #

# Reported by the run that motivated the constant. NOT verifiable here — recorded so the
# expected effect is written down somewhere, and used only as INPUTS to match_verdict.
REPORTED_TAIL_M = {
    "new-brighton-reef": 2753.0,
    "bolinas": 2439.0,
    "moonstone-beach-humboldt": 2426.0,
    "toes-over": 2074.0,
}
REPORTED_REJECTED = {"new-brighton-reef", "bolinas", "moonstone-beach-humboldt"}


def test_the_four_tail_spots_are_all_in_the_population():
    """This half IS checkable from the committed roster, and it is what makes the
    expected 152 -> 149 arithmetic coherent rather than a number taken on faith."""
    import json

    from mop_face_validation import is_population

    roster = json.load(open(ROSTER))
    pop = {_slug(s["name"]) for s in roster if is_population(s)}
    assert len(pop) == 152
    for slug in REPORTED_TAIL_M:
        assert slug in pop, slug


def test_the_gate_splits_the_reported_tail_where_the_brief_says():
    """Written out by name, then checked against the gate — not derived from it.

    Reads as: IF these four are at these reported distances, THEN three are rejected and
    Toes Over survives, leaving 149 of 152.
    """
    rejected = {s for s, m in REPORTED_TAIL_M.items() if not match_verdict(m, ALIGNED)[0]}
    assert rejected == REPORTED_REJECTED
    assert len(rejected) == 3
    assert 152 - len(rejected) == 149
    assert match_verdict(REPORTED_TAIL_M["toes-over"], ALIGNED)[0] is True


def test_the_cut_falls_inside_the_reported_gap_and_not_on_a_data_point():
    """2074 -> 2426 is the gap the constant was placed in. A gate ON a measured value
    would be fragile in a different way: nudge that spot and the verdict flips."""
    assert REPORTED_TAIL_M["toes-over"] < MATCH_SEPARATION_M < REPORTED_TAIL_M["moonstone-beach-humboldt"]
    assert MATCH_SEPARATION_M not in set(REPORTED_TAIL_M.values())


def test_the_known_weakness_is_two_neighbours_split_by_the_cut():
    """Toes Over and New Brighton Reef sit on the same stretch of Santa Cruz coast, and
    this gate keeps one and drops the other.

    THE SEPARATION BETWEEN THE TWO SPOTS IS COMPUTED HERE, from committed roster
    coordinates — it is the one distance in this area that does not depend on the absent
    MOP cache. Their separations FROM THEIR MOP POINTS are the reported figures, and are
    only fed to the gate, never asserted.
    """
    import json
    import math

    roster = json.load(open(ROSTER))
    by_slug = {_slug(s.get("name") or ""): s for s in roster}
    a, b = by_slug["new-brighton-reef"], by_slug["toes-over"]

    r = 6371000.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp = math.radians(b["lat"] - a["lat"])
    dl = math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    metres = 2 * r * math.asin(math.sqrt(h))

    assert 1200 < metres < 1350, metres          # 1272 m as committed
    # Near neighbours, opposite verdicts — the blindness the bearing test should fix.
    assert match_verdict(REPORTED_TAIL_M["toes-over"], ALIGNED)[0] is True
    assert match_verdict(REPORTED_TAIL_M["new-brighton-reef"], ALIGNED)[0] is False


# --------------------------------------------------------------------------- #
# The reasoning is the asset. A bare number invites tidying to 2000.           #
# --------------------------------------------------------------------------- #

def test_the_comment_says_the_constant_is_empirical_not_physical():
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert "EMPIRICAL, NOT PHYSICAL" in src
    assert "2074 -> 2426" in src
    assert "352 m jump" in src


def test_the_comment_records_that_the_figures_cannot_be_checked_here():
    """The provenance is the part most likely to be lost, and the part that decides how
    much weight a later reader should give the number."""
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert "REPORTED MEASUREMENTS" in src
    assert "gitignored and absent from any clone" in src
    assert "NO TEST ASSERTS THEM" in src


def test_the_comment_records_why_it_is_not_match_fallback_m():
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert "WHY THIS IS NOT MATCH_FALLBACK_M (1200 m)" in src
    assert "0.5-1.5 km" in src           # quoted from that constant's own comment
    assert "PUBLISHING threshold" in src
    assert "PAIRING test" in src
    # The pointer must survive on ONE line, or it stops being greppable.
    assert "mop_handful_slice.py:73-75" in src


def test_the_quoted_comment_is_still_where_the_pointer_says_it_is():
    """A file:line pointer that has drifted is worse than none — it sends a reader to the
    wrong place with confidence. Checked against the real file rather than trusted."""
    lines = open(os.path.join(ROOT, "scripts", "mop_handful_slice.py")).read().splitlines()
    window = "\n".join(lines[72:75])          # 1-indexed 73-75
    assert "MOP points sit on the 10 m contour" in window
    assert "0.5–1.5 km" in window or "0.5-1.5 km" in window
    assert "HARD-disqualify beyond ~1.2 km" in window


def test_the_comment_records_the_directional_blindness_and_its_successor():
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert "THE KNOWN WEAKNESS" in src
    assert "alongshore" in src
    assert "BEARING TEST IS THE INTENDED SUCCESSOR" in src
    assert "REVISIT THIS NUMBER" in src


def test_the_comment_owns_the_gap_fitting_it_rejected_for_the_angle():
    """The angle gate declined 60 deg for being gap-fitted. Doing it here needs an
    explicit reason, not silence."""
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert "THIS IS GAP-FITTING, WHICH THE ANGLE GATE ABOVE EXPLICITLY REJECTED" in src
