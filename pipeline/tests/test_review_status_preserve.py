"""review_status must survive a pipeline run.

THE DEFECT. _spot_record wrote ``"review_status": "auto"`` as a literal into the
always-written block of every upsert record. The column therefore could not hold a human
review: a reviewer's verdict was overwritten by the next pipeline run, within 8 hours. It
also made every "show it only when reviewed" idea unbuildable, because the condition could
never once be true.

THE FIX USES THE MECHANISM THAT WAS ALREADY THERE. _spot_record returns a PARTIAL record,
and import_spots' SELECT-then-merge fills any key ABSENT from that partial with the current
DB value (``for k, v in base.items(): if k not in rec: rec[k] = v``). review_status is not
derived from source at all — nothing in spots_enriched.json produces it — so the correct
move is simply to stop writing it, at which point the generic preserve net carries it like
every other column. No second mechanism, no name list to maintain.

The one case the preserve net cannot cover is a spot with NO existing row: there is nothing
to preserve. import_spots seeds "auto" at the single place a new row is identified
(``if not base:``). That is also what keeps the chunk's key set uniform — PostgREST NULLs a
key missing from one row of a batch, the exact bug the merge exists to prevent, so leaving
it unset for new spots would land them NULL-reviewed rather than 'auto'.

Run: python -m pipeline.tests.test_review_status_preserve   (or pytest)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline import db_import


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, client): self.c = client; self._sel = None; self._rng = None; self._in = None
    def select(self, cols): self._sel = cols; return self
    def range(self, a, b): self._rng = (a, b); return self
    def in_(self, col, vals): self._in = (col, vals); return self
    def eq(self, *a): return self
    def upsert(self, chunk, on_conflict=None): self.c.upserted.extend(chunk); return self
    def delete(self): self._del = True; return self
    def execute(self):
        if self._sel and "*" in self._sel and self._in is None:
            a, b = self._rng or (0, 10**9)
            return _Result([dict(r) for r in self.c.rows[a:b + 1]])
        return _Result([])


class _Client:
    def __init__(self, rows): self.rows = rows; self.upserted = []
    def table(self, _name): return _Query(self)


def _run_import(existing_rows, enriched_spots):
    """Drive import_spots with a fake Supabase client; return {slug: upserted record}."""
    saved = db_import._excluded_slugs
    db_import._excluded_slugs = lambda: set()
    try:
        tmp = Path(tempfile.mkdtemp()) / "spots.json"
        tmp.write_text(json.dumps(enriched_spots))
        client = _Client(existing_rows)
        db_import.import_spots(client, spots_path=tmp)
        return {r["slug"]: r for r in client.upserted}
    finally:
        db_import._excluded_slugs = saved


def _enriched(name="Steamer Lane", lat=36.9513, lng=-122.0266, state="California"):
    return {"name": name, "lat": lat, "lng": lng, "region_hint": state,
            "is_valid_surf_spot": True}


def _db_row(slug="steamer-lane", name="Steamer Lane", lat=36.9513, lng=-122.0266,
            state="California", **extra):
    row = {"slug": slug, "name": name, "lat": lat, "lng": lng, "state": state}
    row.update(extra)
    return row


# --------------------------------------------------------------------------- #
# 1 — the three pins asked for                                                 #
# --------------------------------------------------------------------------- #
def test_an_existing_reviewed_value_survives_an_upsert():
    """THE POINT OF THE CHANGE. A human sets review_status='reviewed'; the next pipeline
    run must not touch it. Before the fix this record went out as 'auto' every time."""
    up = _run_import([_db_row(review_status="reviewed")], [_enriched()])["steamer-lane"]
    assert up.get("review_status") == "reviewed", (
        f"a human review must survive the pipeline (got {up.get('review_status')!r})")


def test_an_existing_auto_stays_auto():
    """The 648 rows that are 'auto' today keep reading 'auto' — the fix must be invisible
    for every spot nobody has reviewed."""
    up = _run_import([_db_row(review_status="auto")], [_enriched()])["steamer-lane"]
    assert up.get("review_status") == "auto", up.get("review_status")


def test_a_brand_new_spot_gets_auto():
    """No DB row means nothing to preserve, so the value is created here. It must be 'auto'
    — not absent (PostgREST would NULL it against the rest of the batch) and not missing
    from the record."""
    up = _run_import([], [_enriched(name="New Spot")])["new-spot"]
    assert "review_status" in up, "a new spot must carry the key, not rely on the column DEFAULT"
    assert up["review_status"] == "auto", up["review_status"]


# --------------------------------------------------------------------------- #
# 2 — the mechanism, directly                                                  #
# --------------------------------------------------------------------------- #
def test_spot_record_does_not_emit_review_status_at_all():
    """The unit under the fix. _spot_record's partial record must NOT carry the key — that
    absence is what hands the column to the preserve net. A test on import_spots alone
    would still pass if someone re-added the literal AND special-cased it downstream; this
    one pins the actual mechanism."""
    rec = db_import._spot_record(_enriched())
    assert "review_status" not in rec, (
        f"_spot_record must leave review_status to the preserve net (got {rec.get('review_status')!r})")
    # ...while the genuinely always-written keys are still written
    for k in ("slug", "name", "lat", "lng", "state", "region", "swell_window_arcs", "data_sources"):
        assert k in rec, f"{k} must still be always-written"


def test_review_status_is_not_treated_as_coordinate_derived():
    """A review is about the spot, not about its coordinates, so moving a spot must not
    drop it — unlike buoy/tide/nwps_wfo, which are deliberately NULLed on a move."""
    assert "review_status" not in db_import._COORD_DERIVED_FIELDS
    existing = [_db_row(slug="56th-street", name="56th Street", lat=33.6239, lng=-117.9459,
                        review_status="reviewed")]
    enriched = [_enriched(name="56th Street", lat=39.1416, lng=-74.6968, state="New Jersey")]
    up = _run_import(existing, enriched)["56th-street"]
    assert up["lat"] == 39.1416, "the spot really did move"
    assert up.get("review_status") == "reviewed", "a move must not discard the review"


def test_every_other_non_auto_value_survives_too():
    """The fix must be about the MECHANISM, not about the literal string 'reviewed'. Any
    value a future workflow writes has to survive equally."""
    for verdict in ("reviewed", "flagged", "needs_review", "rejected", "auto_v2"):
        up = _run_import([_db_row(review_status=verdict)], [_enriched()])["steamer-lane"]
        assert up.get("review_status") == verdict, f"{verdict!r} -> {up.get('review_status')!r}"


def test_a_null_review_status_is_preserved_not_stamped():
    """A NULL existing value is not 'auto', so the rule ("only a spot with no row, or an
    existing 'auto', gets 'auto' written") leaves it alone. Preserving what is there is the
    conservative read and matches how the merge treats every other column."""
    up = _run_import([_db_row(review_status=None)], [_enriched()])["steamer-lane"]
    assert up.get("review_status") is None, up.get("review_status")


# --------------------------------------------------------------------------- #
# 3 — the batch hazard the seeding exists to avoid                             #
# --------------------------------------------------------------------------- #
def test_every_record_in_a_mixed_batch_carries_the_key():
    """PostgREST NULLs a key that is missing from one row of a bulk upsert — the bug the
    whole SELECT-then-merge exists to prevent. A batch holding both an existing reviewed
    spot and a brand-new one must therefore have review_status on BOTH records, or the new
    spot silently NULLs the column for itself."""
    existing = [_db_row(review_status="reviewed")]
    enriched = [_enriched(), _enriched(name="Brand New", lat=36.60, lng=-121.89)]
    up = _run_import(existing, enriched)
    assert set(up) == {"steamer-lane", "brand-new"}, sorted(up)
    for slug, rec in up.items():
        assert "review_status" in rec, f"{slug} is missing the key — the batch key set is ragged"
    assert up["steamer-lane"]["review_status"] == "reviewed"
    assert up["brand-new"]["review_status"] == "auto"


def test_the_preserve_net_still_fills_other_columns():
    """Guard against the fix being implemented by weakening the merge itself: an unrelated
    absent column must still be filled from the DB row exactly as before."""
    existing = [_db_row(review_status="reviewed", crowd_factor="heavy", break_type="point")]
    up = _run_import(existing, [_enriched()])["steamer-lane"]
    assert up.get("crowd_factor") == "heavy"
    assert up.get("break_type") == "point"
    assert up.get("review_status") == "reviewed"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} review-status preserve checks passed")


if __name__ == "__main__":
    _run_all()
