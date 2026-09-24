"""The methodology post's chop table is the chop curve, and CI says so if either moves alone.

WHAT THIS HOLDS TOGETHER. frontend/content/blog/methodology.md publishes the chop penalty as
a table of knots and tells readers the multiplier is piecewise-linear through them. The curve
itself is interpret._CHOP_POINTS. Nothing tied the two, so either could be edited and the post
would go on describing a curve the rater no longer uses. These tests parse the table out of the
post and compare it with the constant knot by knot, so a change to either side alone fails.

A TEST FILE, NOT A PIPELINE CHANGE. It reads the post and imports the constants; it changes no
pipeline code and no data.

ONLY TABLES THAT MATCH TODAY ARE PINNED. The post's period-quality table does not match
_PERIOD_QUALITY_POINTS: its 14 s row says 1.00, and the curve gives 1.0167 there (1.00 at the
13 s knot, rising to 1.05 at 16 s). Pinning it would mean editing a number to make a test pass,
so it is reported for the prose rewrite instead and deliberately not tested here. The
surface-conditions table mirrors frontend code and is held in frontend/lib/methodology.test.mts.

NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST. Every expected number is read out of the post;
the code supplies only the actual side.

Run: python -m pytest pipeline/tests/test_methodology_tables.py
"""
from __future__ import annotations

import os
import re

from pipeline import interpret as I

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
POST = os.path.join(ROOT, "frontend", "content", "blog", "methodology.md")

_DELIMITER_CELL = re.compile(r"^:?-+:?$")
_NUMBER = r"(\d+(?:\.\d+)?)"


def _post():
    with open(POST, encoding="utf-8") as f:
        return f.read()


def _tables(markdown):
    """Every GFM pipe table in *markdown*: [header, row, row, ...], each a list of cell strings.

    Deliberately small. A table is a run of lines starting with '|' whose second line is the
    delimiter row; cells split on '|'. It does not handle escaped pipes or inline markup, and
    the table it is used on holds plain numbers, so neither can occur there."""
    runs, current = [], []
    for line in markdown.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            current.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif current:
            runs.append(current)
            current = []
    return [[rows[0]] + rows[2:] for rows in runs
            if len(rows) >= 2 and all(_DELIMITER_CELL.match(cell) for cell in rows[1])]


def _body_of_the_table_headed(header):
    found = [table for table in _tables(_post()) if table[0] == header]
    assert len(found) == 1, (
        f"methodology.md should carry exactly one table headed {header}, found {len(found)}. "
        "If the table was renamed or split, update this test in the same change.")
    return found[0][1:]


def test_the_posts_chop_table_is_the_chop_curve_knot_for_knot():
    published = [(float(ratio), float(mult))
                 for ratio, mult in _body_of_the_table_headed(["chop_ratio", "Multiplier"])]
    assert published == I._CHOP_POINTS, (
        "The chop table in frontend/content/blog/methodology.md and interpret._CHOP_POINTS "
        "disagree. Change them together.\n"
        f"  post: {published}\n  code: {I._CHOP_POINTS}")


def test_the_posts_worked_example_between_two_knots_is_what_the_curve_gives():
    # "a chop_ratio between two knots is interpolated, so 0.3 gives 0.9250, not 0.85"
    m = re.search(rf"so {_NUMBER} gives {_NUMBER}", _post())
    assert m, "the chop section's worked example ('so X gives Y') is gone; update this test"
    ratio, stated = float(m.group(1)), float(m.group(2))
    decimals = len(m.group(2).split(".")[1]) if "." in m.group(2) else 0
    assert round(I.chop_multiplier(ratio), decimals) == stated, (
        f"the post says chop_ratio {m.group(1)} gives {m.group(2)}; "
        f"chop_multiplier({ratio}) is {I.chop_multiplier(ratio)!r}")


def test_the_posts_neutral_score_for_an_unknown_ratio_is_the_codes():
    # "An unknown scores the neutral 1.00 rather than being treated as zero."
    m = re.search(rf"unknown scores the neutral {_NUMBER}", _post())
    assert m, "the chop section's sentence on unknown ratios is gone; update this test"
    assert I.chop_multiplier(None) == float(m.group(1)), (
        f"the post says an unknown ratio scores {m.group(1)}; "
        f"chop_multiplier(None) is {I.chop_multiplier(None)!r}")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} methodology-table checks passed")


if __name__ == "__main__":
    _run_all()
