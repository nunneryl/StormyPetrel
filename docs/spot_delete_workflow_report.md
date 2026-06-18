# Spot-delete workflow — review summary

Branch: `claude/spot-delete-workflow` (off `origin/main` at `c2bf626`).
Goal: routine spot deletes are just `excluded_spots.json` + a pipeline run.
No SQL, no cam reassignment dance, no resurrection.

## What was broken

1. `db_import.import_spots` only upserts. Anything already in the live DB
   stays there forever unless someone hand-writes a SQL DELETE — that's
   why La Ocho needed `docs/orientation_apply.sql`.
2. `cams.spot_slug → spots(slug)` is `ON DELETE NO ACTION` (Postgres
   default for inline FKs). Even when you do write the SQL, the DELETE
   aborts if any cam still references the slug. The La Ocho script
   handled that with a pre-flight `SELECT … FROM cams WHERE spot_slug = …`
   and an instruction to ROLLBACK + reassign if anything came back.

Two small changes remove both pain points.

## Change 1 — cams FK → `ON DELETE SET NULL`

`docs/spot_delete_workflow.sql` does the alter in three steps inside one
transaction:

1. Diagnostic `SELECT` from `information_schema.referential_constraints`
   to confirm the constraint name and current `delete_rule = 'NO ACTION'`.
2. `ALTER TABLE cams DROP CONSTRAINT cams_spot_slug_fkey;` followed by
   `ALTER TABLE cams ADD CONSTRAINT cams_spot_slug_fkey FOREIGN KEY
   (spot_slug) REFERENCES spots(slug) ON DELETE SET NULL;`.
3. Same diagnostic `SELECT` to confirm `delete_rule = 'SET NULL'`.

The constraint name is Postgres's auto-generated default
(`<table>_<column>_fkey`) because `007_cams.sql` declared the FK inline
without a name. If the diagnostic SELECT returns a different name (e.g.
someone renamed it via a later migration we don't have on this branch),
substitute it into the DROP before running the COMMIT.

The cam row itself is preserved — only `spot_slug` is set to NULL.
That's deliberate: a working stream is hard to source again, and the
next time a spot is added at the same beach we want the option to
adopt the cam cleanly. Cams with NULL `spot_slug` will be visible on
the cams page (filtered by status, not by spot) and can be reassigned
via a `UPDATE cams SET spot_slug = ? WHERE id = ?` once a target spot
exists.

`forecasts.spot_id → spots.id` is already `ON DELETE CASCADE`
(`001_initial_schema.sql`) so forecast rows clear automatically on a
spot delete — unchanged here.

## Change 2 — `db_import` honors excluded slugs

`pipeline/db_import.py` `import_spots` was extended in three places:

1. **Loader.** New helper `_excluded_slugs()` reads
   `excluded_spots.json` via `cleanup_spots.load_excluded_names`
   (already normalizes curly-quote variants) and slugifies each entry
   with the same `_slugify` that turns a record's name into its DB
   slug. So the matcher is consistent end-to-end: same fold rule for
   both sides of the comparison.

2. **Pre-flight + safety cap.** Before any write, `_find_excluded_in_db`
   does one `SELECT slug, name FROM spots WHERE slug IN (…)` against
   the live DB. If the result exceeds `SAFETY_DELETE_CAP = 10`, the
   function raises with a clear error listing the targeted slugs and
   no writes happen — the upsert is gated on this check too, so a
   corrupted exclusion file can't leave the table in a half-applied
   state. Routine deletes are 1–2 at a time; the cap exists purely as
   a guardrail against a truncated/swapped/duplicated
   `excluded_spots.json`. Bump only with a deliberate reason and a PR.

3. **Skip + delete.** After the pre-flight passes:
   - Excluded slugs are filtered out of the upsert pass, so a stale
     `spots_enriched.json` that still contains an excluded entry can't
     resurrect what we're about to delete.
   - After upserts complete, every row from the pre-flight
     `to_delete` list is logged as `WARNING removing spot: <slug>
     (<name>)` and then a single `DELETE … WHERE slug IN (…)` call
     removes them all. The cams FK alter from Change 1 turns this into
     a successful delete + NULL'd cam rather than a NO ACTION abort.

The deletion is keyed by slug, derived from the excluded name. So an
entry like `"Spyder"` → `spyder`, `"La Ocho"` → `la-ocho`, `"Deep
Cove"` → `deep-cove`. For the disambiguated cases (`Rockpile (Laguna)`
→ `rockpile-laguna`) the slug derivation is the same as the upsert
side; both halves stay in sync because both go through `_slugify`.

If you ever need to delete a spot whose name doesn't slugify to its
live slug (rename history, manual slug override), extend the loader
to accept dict entries `{"name": "X", "slug": "x-suffix"}` and union
both into the excluded set. Not done now — current data doesn't need
it and YAGNI.

## Change 3 — Deep Cove (the test case)

| File | Change |
| --- | --- |
| `pipeline/data/llm_spots.json` | Removed the `Deep Cove` entry from the Maine block (lat 44.61497, lng -66.87571). Prevents re-seeding on the next enrich. |
| `pipeline/data/excluded_spots.json` | Added `"Deep Cove"` under new reason `manual_removal`. The next `db_import` run picks it up. |

Deep Cove is a Bay of Fundy point near Grand Manan — manifest 2 seeded
it as `manifest_addition` but it doesn't break (sheltered bay, minimal
swell exposure). Slug derivation: `_slugify("Deep Cove")` → `deep-cove`.

**Intentionally NOT edited:** `pipeline/spots_enriched.json` still
contains the Deep Cove row. That's the workflow test — it confirms
that the new skip+delete logic handles a stale enriched file correctly
(the upsert skips Deep Cove, then the deletion pass removes the live
DB row). From now on, deleting a spot is just the two edits above
plus a pipeline run; no further `spots_enriched.json` hand-edits are
needed.

**Intentionally NO SQL delete for Deep Cove.** Per the task spec, the
whole point is that Change 2 handles it on the next pipeline run.

## Latent-collision review — resolved

The first pre-merge pass surfaced four slug matches between
`spots_enriched.json` and the exclusion list. After review (per
follow-up commit) the verdicts are:

| Slug | Excluded reason | Verdict |
| --- | --- | --- |
| `deep-cove` | `manual_removal` | **Delete.** Test case. |
| `malibu-point` | (removed) | **Keep the row.** ~950 m from Malibu Surfrider — adjacent break, not a true duplicate. Pulled from `duplicates`. |
| `sunset-point` | (removed) | **Keep the row.** Pacific Palisades, real break. Pulled from `unknown`. |
| `trails` | (removed) | **Keep the row.** San Onofre Trails, real break. Pulled from `non_surfable`. |

After the follow-up edit, `excluded_spots.json` no longer references
those three names. Confirmed by re-running `_excluded_slugs()` against
the current `spots_enriched.json`: **only `deep-cove` matches.** The
first pipeline run after merge will log exactly one removal line.

### Why all three slipped through historically

The three legacy entries were added as `source: "manifest_addition"` by
manifest 2 *after* they were already in the exclusion list.
`cleanup_spots.apply_cleanup` runs on the seeded/scraped path but
doesn't enforce against `manifest_addition` rows, so they landed in
`spots_enriched.json` and got upserted into the DB. Without a deletion
pass, `db_import` never reconciled them. With the new logic plus this
exclusion-list cleanup, the roster and the exclusion list are now
internally consistent.

## Files changed

| File | Why |
| --- | --- |
| `pipeline/db_import.py` | Adds `_excluded_slugs`, `_find_excluded_in_db`, `SAFETY_DELETE_CAP`. Extends `import_spots` with pre-flight check, upsert filter, deletion pass, and removal log lines. Imports `load_excluded_names` from `cleanup_spots`. |
| `pipeline/data/llm_spots.json` | Removed `Deep Cove` from `maine.spots`. |
| `pipeline/data/excluded_spots.json` | Added `manual_removal: ["Deep Cove"]`. |
| `docs/spot_delete_workflow.sql` *(new)* | One-time cams FK alter to `ON DELETE SET NULL`. |
| `docs/spot_delete_workflow_report.md` *(new)* | This file. |

## Prod steps (in order, on your side)

1. **One time:** run `docs/spot_delete_workflow.sql` in Supabase.
   Verify the first SELECT shows `delete_rule = 'NO ACTION'` before
   the ALTER and the second shows `'SET NULL'` after. The script also
   does `ALTER TABLE cams ALTER COLUMN spot_slug DROP NOT NULL;` so a
   later `SET NULL` doesn't trip a NOT NULL violation; that ALTER is
   idempotent if the column was already nullable. COMMIT.

2. Merge the branch via the GitHub UI.

3. Run the forecast pipeline (or `python -m pipeline.db_import --all`
   directly). Watch the spots step in the run log:

   ```
   spots: upserting <N> records (… 1 excluded)
   removing spot: deep-cove (Deep Cove)
   ```

4. Confirm in Supabase:

   ```sql
   SELECT slug, name FROM spots WHERE slug = 'deep-cove';   -- expect 0 rows
   SELECT id, cam_name, spot_slug FROM cams WHERE spot_slug IS NULL; -- if any cam was attached to deep-cove, expect to see it here with spot_slug = NULL
   ```

5. **From here on:** routine spot deletion is just

   ```diff
     # pipeline/data/excluded_spots.json
       "manual_removal": [
   -    "Deep Cove"
   +    "Deep Cove",
   +    "The Next One"
       ]
   ```

   …plus a pipeline run. The 10-row cap means up to 10 deletions per
   run; if you ever need to retire more than 10 spots at once, split
   into multiple runs or raise the cap with a deliberate PR.

## Edge-case notes

- **Safety cap semantics.** The cap is checked PRE-WRITE — if it
  trips, neither the upsert filter nor the delete runs. The error
  message lists the targeted slugs so you can confirm whether the
  exclusion file genuinely grew or got corrupted.

- **Idempotence.** Deleting an already-deleted slug is a no-op: the
  pre-flight SELECT returns zero rows, the deletion pass is skipped,
  and the upsert filter prevents resurrection.

- **Slug collisions across excluded entries.** If the same slug shows
  up via two different excluded names (e.g. unicode variants), the
  set collapses them — single DELETE, no double-handling.

- **Cam orphaning.** After this rollout, cams pointing at a deleted
  spot have `spot_slug = NULL`. They still appear in the cams table
  and on `/cams` (the page filters by status, not by spot). Reassign
  via `UPDATE cams SET spot_slug = 'new-slug' WHERE id = ?` whenever a
  replacement spot exists; or DELETE the cam row outright if the
  upstream stream is dead.
