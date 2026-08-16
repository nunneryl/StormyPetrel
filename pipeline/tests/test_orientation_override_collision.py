"""BU-17 — a manual orientation with a slug row is silently overridden by enrich.py.

enrich.py applies the NAME-keyed pipeline/data/manual_orientations.json first (Algo 1b) and the
SLUG-keyed pipeline/data/spot_orientations.json second (Algo 1c). _load_spot_orientations' own
docstring states the consequence: "a slug match here beats every other source". So a manual
entry whose name slugs to a key present in the slug file NEVER reaches the rating, however
carefully it was hand-reviewed, and nothing errors or warns.

`apply_orientation_relook --promote-to-manual` is the fix: it writes the reviewed value into
the manual file AND DELETES the slug row, so the two agree and a re-seed of the bulk cache
cannot flip them back.

ORDERING — READ THIS BEFORE TREATING A FAILURE AS A REGRESSION.

  test_no_live_disagreeing_manual_orientation_is_shadowed
      Scoped to the BU-17 work: spots still present in spots_enriched.json whose two files
      DISAGREE. FAILS at 21 before the promotion, PASSES at 0 after it. This is the test that
      tracks the promotion.

  test_no_manual_orientation_is_shadowed_by_a_slug_row
      The full invariant, and it does NOT reach zero on the BU-17 promotion alone. 41 collisions
      exist today; promoting the 21 leaves 20 — 18 Great Lakes entries whose spots are already
      in excluded_spots.json, and 2 (Sebastian Inlet, Rincon) that are live and currently AGREE.
      An agreeing collision is still a latent fault: the manual value is not the effective one,
      so a bulk re-seed of the slug file can change the rating with the manual file untouched.
      Clearing those 20 is separate work — promote them too, or drop the dead manual entries.

Run: python -m pipeline.tests.test_orientation_override_collision   (or pytest)
"""
from __future__ import annotations

import json
import os

import pipeline.enrich as enrich

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_ENRICHED = os.path.join(_REPO, "pipeline", "spots_enriched.json")


def _collisions():
    """[(name, slug, manual_deg, slug_deg, live)] for every manual entry whose name slugs to a
    key present in the slug file. Uses enrich's OWN loaded maps and its OWN _slug_for, so this
    reproduces the exact lookup enrich performs rather than an approximation of it."""
    live = {s.get("name") for s in json.loads(open(_ENRICHED).read())}
    out = []
    for name, rec in enrich._MANUAL_ORIENTATIONS.items():
        slug = enrich._slug_for(name)
        slug_deg = enrich._SPOT_ORIENTATIONS.get(slug)
        if slug_deg is not None:
            out.append((name, slug, rec["orientation_deg"], slug_deg, name in live))
    return sorted(out)


def _disagrees(manual_deg, slug_deg):
    d = abs(manual_deg - slug_deg) % 360.0
    return min(d, 360.0 - d) > 0.5


def _render(rows):
    return "\n".join(
        f"      {n:28} {s:28} manual {m:5.0f}  slug {v:5.0f}  "
        f"{'DISAGREE' if _disagrees(m, v) else 'agree':9} live={live}"
        for n, s, m, v, live in rows)


def test_no_live_disagreeing_manual_orientation_is_shadowed():
    """BU-17 scope: 21 before the promotion, 0 after. This is the one that goes green."""
    rows = [r for r in _collisions() if r[4] and _disagrees(r[2], r[3])]
    assert not rows, (
        f"\n{len(rows)} LIVE manual orientation(s) disagree with a slug row that overrides them.\n"
        "  A manual orientation with a slug row is silently overridden by enrich.py: it applies\n"
        "  manual_orientations.json first and spot_orientations.json second, so the slug value is\n"
        "  what reaches the rating and the hand-reviewed value never does.\n"
        "  FIX: python -m pipeline.apply_orientation_relook --input EXPORT.json "
        "--promote-to-manual --apply\n"
        "  which writes the reviewed value into the manual file AND deletes the slug row.\n"
        f"{_render(rows)}")


def test_no_manual_orientation_is_shadowed_by_a_slug_row():
    """The full invariant. Does NOT reach zero on the BU-17 promotion alone — see the module
    docstring: 20 collisions remain, 18 on excluded spots and 2 that currently agree."""
    rows = _collisions()
    pending = [r for r in rows if r[4] and _disagrees(r[2], r[3])]
    excluded = [r for r in rows if not r[4]]
    agreeing = [r for r in rows if r[4] and not _disagrees(r[2], r[3])]
    assert not rows, (
        f"\n{len(rows)} manual orientation(s) have a slug row.\n"
        "  A manual orientation with a slug row is silently overridden by enrich.py — the\n"
        "  slug-keyed file is applied last and wins, so the hand value never reaches the rating.\n"
        "  This holds even when the two AGREE today: the manual value is still not the effective\n"
        "  one, so a re-seed of the bulk slug cache moves the rating with the manual file\n"
        "  untouched and no diff to notice.\n"
        f"  breakdown: {len(pending)} live+disagreeing (the BU-17 promotion clears these), "
        f"{len(excluded)} on spots already in excluded_spots.json, "
        f"{len(agreeing)} live but currently agreeing.\n"
        "  FIX: promote each one (writes the manual value, deletes the slug row), or delete the\n"
        "  manual entry if its spot is gone.\n"
        f"{_render(rows)}")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}:{e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run_all() else 0)
