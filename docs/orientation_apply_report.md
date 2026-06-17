# Orientation apply pass — review summary

Branch: `claude/orientation-apply` (off `origin/main` at `15e17cb`).
Input: `orientation_apply_manifest.json` (664 orientations / 133 coord_updates / 1 deletion).

## Top-line counts

| Category | Manifest | Applied to `spots_enriched.json` | Notes |
| --- | --- | --- | --- |
| Orientation overrides | 664 | 666 | 664 unique slugs match 664 kept enriched rows; 2 extra applications come from `rockpile` and `shell-beach` slug-collisions inherited from manifest 2 (see Caveat below) |
| Coord refinements | 133 | 133 | All 133 matched by exact name; coord-dependent fields cleared so the next enrich rebuilds them |
| Deletions | 1 | 2 | Both duplicate `La Ocho` rows removed from `spots_enriched.json` |

After this pass: `670` rows in `spots_enriched.json` (`672` before − `2` La Ocho dups).

## Files changed

| File | Why |
| --- | --- |
| `pipeline/data/spot_orientations.json` *(new)* | The durable, slug-keyed orientation override. Same role for `orientation_deg` that `spot_coord_fixes.json` plays for lat/lng. |
| `pipeline/config.py` | Adds `SPOT_ORIENTATIONS_FILE` constant. |
| `pipeline/enrich.py` | New Algorithm 1c overlays slug-keyed overrides AFTER both Algorithm 1 (geometric) and the name-keyed Algorithm 1b. Stamps `orientation_source = "manual"` and writes both `orientation_deg` and `offshore_wind_deg`. |
| `pipeline/data/spot_coord_fixes.json` | +133 coord refinements (49 net new; 84 overwrote earlier fixes — typical for the same spot getting two rounds of position review). |
| `pipeline/data/excluded_spots.json` | Adds `"La Ocho"` under new reason `manual_cleanup_3` so seed runs can't re-add it. |
| `pipeline/data/llm_spots.json` | Removed La Ocho (it was the only manifest delete with an `llm_spots` entry). |
| `pipeline/data/manual_orientations.json` | Removed La Ocho — now excluded, no point keeping a hand-set orientation. |
| `pipeline/spots_enriched.json` | Direct edits so the next `db_import` reflects the manifest without waiting for a full re-enrich: 133 coord updates (with stale-field clearing identical to `cleanup_spots.apply_cleanup`), 666 orientation overrides, both La Ocho rows removed. |
| `docs/orientation_apply.sql` *(new)* | The single DELETE plus a cam-FK pre-check. |
| `docs/orientation_apply_report.md` *(new)* | This file. |

## How the override mechanism wires into the rating

`pipeline/interpret.directional_gain` (the rating's swell-direction term)
reads `orientation_deg` as the fallback `target` whenever `optimal_swell_dir`
is not set:

```python
target = optimal_swell_dir if optimal_swell_dir is not None else orientation_deg
```

`pipeline/interpret.wind_multiplier` reads `offshore_wind_deg` directly.
Both fields are persisted by `pipeline/db_import._spot_record` (lines
82–83), which copies them straight off the enriched record. So the
chain is:

```
spot_orientations.json (slug→deg)
  → enrich.py Algo 1c overlays orientation_deg + offshore_wind_deg
    → db_import upserts both into spots
      → interpret.directional_gain / wind_multiplier read them
        → live ratings reflect the human value.
```

Confirmed wiring, not inferred — see `pipeline/interpret.py:174` and
`pipeline/db_import.py:82-83`.

## Cam FK check (Task 3)

Grepped `pipeline/data/cam_seed.json`, `pipeline/discover_cams.py`,
`pipeline/seed_cams.py`, and `pipeline/resolve_cams.py` for `la-ocho`
and `"La Ocho"`. **Zero hits.** No tree-tracked cam references the
slug, so the DELETE shouldn't trip the `cams.spot_slug → spots.slug`
FK (ON DELETE NO ACTION).

`docs/orientation_apply.sql` still runs a `SELECT … WHERE spot_slug =
'la-ocho'` inside the transaction as a belt-and-braces check against
any out-of-tree row that might have landed in the live DB. If it
returns rows: ROLLBACK, reassign or drop those cams, retry.

`forecasts.spot_id → spots.id` is ON DELETE CASCADE, so forecast rows
for la-ocho clear automatically.

## Caveat — pre-existing slug collisions from manifest 2

Manifest 2 added new spots named `Rockpile` and `Shell Beach` whose
report mentioned a `-ca` suffix disambiguation, but the actual rows
in `pipeline/data/llm_spots.json` and `pipeline/spots_enriched.json`
ended up named without the suffix — they currently slugify to
`rockpile` and `shell-beach`, colliding with the existing same-named
SB / Pismo entries. A third collision exists at `spyder` (two enriched
rows both named `Spyder`). At `db_import` time, `_dedupe_by_slug`
drops the second occurrence of each.

This orientation pass writes the same override to **both** colliding
rows where applicable (so whichever wins dedup gets the right
orientation). It does NOT fix the underlying name collision — that's
manifest 2 followup, out of scope here.

## Coord-derived staleness (SW-2 fallback windows)

The 133 coord refinements clear `swell_window_arcs`,
`optimal_swell_dir`, `swell_window_source`, and the buoy/tide caches,
exactly like `pipeline/cleanup_spots.apply_cleanup`. On the next
enrich the orientation-derived fallback rebuilds these arcs against
both the corrected coord and the new (overridden) orientation. Per
your instruction, no raycast is being kicked off now — the upcoming
SW-1 task does a full raycast pass and refreshes everything cleanly.

For the orientation overrides on coord-unchanged spots, only the
SW-2 fallback windows are orientation-centered. Existing raycast
windows are unaffected, and existing orientation-derived windows
stay frozen on re-enrich (the fallback no-ops when arcs exist).
That gap closes with the SW-1 raycast.

## Prod steps (in order, on your side)

1. Review the branch diff + this file.
2. Run `docs/orientation_apply.sql` in Supabase. Verify the cam-FK
   `SELECT` returns zero rows before letting the transaction COMMIT.
3. Merge the PR via the GitHub UI.
4. Trigger the full pipeline workflow (or `python -m pipeline.db_import
   --all` directly). The 664 orientation overrides + 133 coord
   updates + Algo-1c wiring all land in the spots table on this
   run.
5. Re-audit ratings — `directional_gain` for the 664 spots is now
   reading the human-set orientation; spot-page rating tiles should
   reflect that immediately on the next forecast pipeline tick.
