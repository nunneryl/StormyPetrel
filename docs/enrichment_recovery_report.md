# Enrichment recovery — buoy mapping restore + preserve hardening

Branch: `claude/enrichment-recovery` (off `claude/tide-mapping-rebuild`
at `f62601d`, which is itself off `origin/main` at `93646ca`). Carries
the tide rebuild + durable-fix forward; this PR layers the rest.

Scope: restore the enrichment columns the same db_import bug class
nulled before the durable fix landed, harden the preserve logic from
a 13-column hardcoded list to a schema-wide `SELECT *` rule, and
derive what can be derived for manifest-added spots without NOAA
egress.

## 1. What was actually lost — and what wasn't

The tide-rebuild PR's at-risk table over-counted the loss for fields
other than the two ID columns. Re-measured per-column presence across
the bug timeline:

| Commit | Spots | tide_id | buoy_id | tide_pref | break_type | nwps_wfo | crowd | hazards | verif | url |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `5177b0e` initial track | 489 | 466 | 284 | 489 | 489 | 473 | 181 | 179 | 183 | 285 |
| `10197ac` manifest 2 | 672 | **82** | **62** | 468 | 468 | 454 | 176 | 174 | 177 | 280 |
| `f62601d` tide PR | 668 | 637 | **44** | 464 | 464 | 450 | 174 | 174 | 175 | 278 |

The two ID columns crashed by ~85% in manifest 2 — that's the active
hole. The other fields (`tide_preference` / `break_type` /
`crowd_factor` / etc.) held steady at ~460-470 because manifest 2
generally carried them through for the surviving 489 source spots
even as it stripped the IDs. The remaining ~200 spots without those
descriptive fields are the manifest-added rows, which were never
enriched for them — there's no legacy state to restore for those.

So the recovery work splits cleanly:

- **Buoy mapping** — actively lost. Restore + derive aggressively.
- **`nwps_wfo`** — partially lost (gradual erosion 473 → 450).
  Re-derive via the existing `assign_wfo()` function (no NOAA egress
  needed; it's a lat/lng + region rule table).
- **`tide_preference` / `break_type` / `verification_confidence` /
  `crowd_factor` / `hazards` / `surf_forecast_url`** — held steady at
  ~legacy counts. The current "missing" set is manifest-additions,
  not bug victims. **Left as null + flagged for a later
  review/enrich pass**, per the user's spec.

## 2. Restore-from-legacy by slug match

`git show 5177b0e:pipeline/spots_enriched.json` → 489 spots. Build
`slug → legacy_record` map (same `_slugify` rule as `db_import`).
For each current spot whose slug matches legacy, restore the field
verbatim **iff** it's currently absent / null / `[]` / `""`. Restore
counts per field:

| Field | Restored |
| --- | --- |
| `nearest_buoy_id` | 231 |
| `nearest_buoy_dist_km` | 231 |
| `tide_preference` | 0 |
| `break_type` | 0 |
| `break_type_confidence` | 0 |
| `nwps_wfo` | 0 |
| `crowd_factor` | 0 |
| `hazards` | 0 |
| `verification_confidence` | 0 |
| `surf_forecast_url` | 0 |

The zeros are the answer to "did the bug actually wipe these for
legacy spots?" — **no**. Manifest 2 carried them through; legacy
spots in current still have those values. The restore-from-legacy
work has nothing to do for those fields.

`231 buoy restores` — every legacy spot whose buoy was wiped to
NULL in `manifest_2`'s upsert and never refilled.

Sanity-check stats: 456 spots matched legacy by slug. 0 name drifts.
66 coord drifts >5 km — about half are slug collisions across
different cities (e.g. "56th Street" exists in SoCal and NJ; "Bird
Rock" similar), the rest are coord_fix moves from `manifest_2`. The
restore-only-if-absent rule covers both: for true slug collisions
the current record carries its own values; for moved spots the
legacy values are still semantically valid (these are
geometrically-derived nearest-buoy mappings; if the spot moved
within a few km the buoy assignment is still the right one, and the
db_import-blanking bug had nothing to do with the move).

## 3. Derive for manifest-added spots

### `nwps_wfo` via `pipeline.forecast.nwps.assign_wfo()`

The existing function is a deterministic lat/lng + region_hint rule
table for every CONUS WFO. No NOAA egress needed.

```
spots without nwps_wfo before derivation: 218
  newly derived via assign_wfo():           204
  still no WFO (unmapped region):            14
    region='Michigan'   6 spots
    region='Wisconsin'  3 spots
    region='Minnesota'  2 spots
    region='Ohio'       2 spots
    region='Indiana'    1 spot

FINAL nwps_wfo set: 654/668
```

The 14 unmapped spots are Great Lakes — NWPS doesn't serve the Great
Lakes; correct to leave NULL by design.

### `nearest_buoy_id` / `nearest_buoy_dist_km` for new spots

Same triangulation approach as the tide PR. From the 284 legacy
buoy observations, estimate each unique buoy's coordinates
(71 buoys; multi-mapping buoys get SciPy least-squares, single-mapping
buoys use the spot location as proxy). Buckets each buoy by ocean
basin (West / East / Gulf / Hawaii / Puerto Rico / Alaska) and
builds a per-basin KDTree.

For each spot without `nearest_buoy_id`:

- If it has a legacy match and legacy had no buoy → **keep NULL**.
  This is a genuine coverage gap that the original Algorithm 4 (with
  the LOS land-mask filter + regional caps) couldn't resolve. The
  reconstruction wouldn't do better; flagging as if it could would
  be misleading.
- Otherwise (manifest-added or coord-shifted) → nearest synthetic
  buoy in the same ocean basin. **Buoys are legitimately far
  offshore** (regular 30–50 km is normal for swell buoys), so no
  raw-distance flag — the sanity check is basin match.
- If no buoy in the basin → flag (none triggered for the current
  roster).

Result:

```
already had nearest_buoy_id (preserved): 275
derived new nearest_buoy_id this pass:   208
legacy coverage gaps (kept NULL):        185
flagged (no buoys in basin):               0

FINAL nearest_buoy_id set: 483/668
```

185 NULL is the genuine coverage gap — every one of those was also
NULL in `5177b0e` after the original Algorithm 4 ran.

### Other descriptive fields — left null with flag

For `tide_preference` / `break_type` / `crowd_factor` / `hazards` /
`verification_confidence` / `surf_forecast_url` on manifest-added
spots: per the user's spec, these aren't geometrically derivable,
so leave NULL for a later review pass. They're not regressions —
they're holes in the manifest-addition rows that have always been
there.

## 4. Schema-wide preserve rule (Change 3)

`pipeline/db_import.py`:

- **Removed** the hardcoded `_PRESERVE_COLUMNS` 13-tuple.
- **Added** `_DB_MANAGED_COLUMNS = frozenset({"id", "geom",
  "created_at", "updated_at"})` — the strict deny-list.
- **`_fetch_existing_spots` now uses `.select("*")`** and strips
  `slug` + `_DB_MANAGED_COLUMNS` from each row. Everything else is
  pulled and merged.

The merge unchanged: per record, fill any column absent from the
partial source-derived record with the existing DB value. After
merge, every record carries the same key set, so PostgREST's
bulk-upsert NULL-on-missing-key behavior can no longer fire.

**Why this matters going forward**: when migration 010 adds a
column (e.g. a new `tide_type` or `bottom_type`), `_fetch_existing_spots`
picks it up automatically and the merge preserves it. No hardcoded
list to maintain; the next person to add a schema column can't
forget to update a preserve list because there isn't one. The
hardcoded list still exists in `_spot_record` for the *source-to-DB
write* path — but that list controls only "what to write from
source", not "what to preserve". Forgetting to add a key there
means it isn't written from source, NOT that it gets silently
NULLed.

## Acceptance checks

### Check 1 — buoy coverage

```
nearest_buoy_id set: 482/666
  NULL (genuine coverage gap):                184
  legacy enrichment also found no buoy:       184  ← exact match
  new spots without basin/synthetic match:      0
```

✅ **Pass.** Every NULL is a documented legacy coverage gap. Zero
spots failed the basin-match sanity check.

### Check 2 — every assigned buoy is in `buoy_observations`

```
known-good (legacy fetch) buoys:        71
distinct buoys assigned this pass:      71
assigned NOT in known-good legacy set:   0
```

✅ **Pass.** Same approach as the tide PR — every assigned ID is
from the pool the buoy fetcher was already pulling rows for, so the
JOIN to `buoy_observations` resolves.

### Check 3 — CA spots resolve a buoy

```
Rincon                              → 46053  dist=35.25 km
Malibu Surfrider Beach              → 46221  dist=19.56 km
Mavericks, California               → 46237  dist=34.78 km
Steamer Lane                        → 46236  dist=22.38 km
Trestles                            → 46277  dist=7.74  km
Ocean Beach SF                      → 46237  dist=11.25 km
Huntington Beach Pier               → 46253  dist=18.52 km
```

✅ **Pass.** Each maps to the right CDIP/NDBC buoy for its stretch
of coast.

### Per-column restored counts

| Column | Before this PR | After |
| --- | --- | --- |
| `nearest_buoy_id` | 44 | **483** (+439) |
| `nearest_buoy_dist_km` | 44 | **483** (+439) |
| `nwps_wfo` | 450 | **654** (+204) |
| `tide_preference` | 464 | 464 (no change — wasn't lost) |
| `break_type` | 464 | 464 |
| `crowd_factor` | 174 | 174 |
| `hazards` | 174 | 174 |
| `verification_confidence` | 175 | 175 |
| `surf_forecast_url` | 278 | 278 |

The descriptive fields would be filled by a fresh LLM verification
pass + surf-forecast scrape, both out of scope here.

### Check 4 — idempotence under PostgREST bulk-upsert

Simulator (modelling the actual PostgREST NULL-on-missing-key
behavior the bug came from) pre-populated with current state +
DB-managed columns + a `description` field (migration 009 — not in
the old hardcoded preserve list).

```
CHECK 4: snap_run1 == snap_run2 ? True

Rincon after 2 imports:
  id              = 89
  geom            = POINT(-119.478507 34.371814)
  created_at      = 2026-01-01T00:00:00Z
  description     = 'Description for Rincon'
  orientation_deg = 210.0
  nearest_buoy_id = 46053
  nearest_tide_station_id = 9411270

PRESERVED across 2 imports:
  description (schema col not in old hardcoded list): 666/666
  id (DB-managed):                                     666/666
  nearest_buoy_id (restored in step 1+2):              482/666
```

✅ **Pass.** Bit-identical between runs. `description` (which isn't
in the old hardcoded list and is hand-edited by surfers) is now
preserved by the SELECT-* generic rule. DB-managed columns survive
intact.

### Check 5 — in-source columns still update

```
CHECK 5: Rincon orientation_deg: 210.0 → 999.0   ✓
```

✅ **Pass.** Change a single spot's `orientation_deg` in source,
run the simulator, the new value flows through. The preserve rule
doesn't accidentally prefer DB-state over source updates — the merge
only fills *absent* keys.

## Files changed

| File | Change |
| --- | --- |
| `pipeline/db_import.py` | `_fetch_existing_spots` now uses `select("*")` and strips `_DB_MANAGED_COLUMNS`. `_PRESERVE_COLUMNS` 13-tuple removed; `_spot_record` docstring rewritten to explain the source-to-DB-write list there is separate from the preserve concern. +20 / −18 lines. |
| `pipeline/spots_enriched.json` | 231 buoy assignments restored from `5177b0e`; 208 new buoys derived for manifest-added spots; 204 `nwps_wfo` derivations via `assign_wfo()`. Net effect on the row set: 624 spots gained at least one previously-null field. |
| `docs/enrichment_recovery_report.md` *(new)* | This file. |

## Out of scope (deliberately)

- Touch swell / raycast — separate work.
- Restore `tide_preference` / `break_type` / etc. for new spots — needs
  an LLM verification pass, not a geometric recompute.
- Re-derive buoys for the 185 legacy coverage gaps — the original
  Algorithm 4 already ran on those with the LOS land-mask filter
  and found nothing; reconstruction wouldn't improve.

## Prod steps (on your side)

1. Review the branch + this report. The `spots_enriched.json` diff is
   large but mechanical (231 buoy restores + 208 buoy assigns + 204
   WFO assigns).
2. Merge. (The tide-rebuild PR is a prerequisite — this branch
   already includes it via the off-branch base. If the tide PR is
   merged first, GitHub's merge UI will simplify the diff.)
3. Trigger the forecast pipeline. The first run's spots step log line
   will read something like:
   ```
   spots: upserting 666 records (skipped 2 invalid/unnamed, 0 slug collisions, 1 excluded, ~6000 cols filled from DB)
   ```
   The `cols filled from DB` count will be high on the first run
   (it's covering everything the old hardcoded list wasn't pulling)
   and drop on subsequent runs to roughly `(rows × cols_unset_in_source)`.
4. Verify in Supabase:
   ```sql
   SELECT
     COUNT(*) FILTER (WHERE nearest_buoy_id IS NULL)     AS no_buoy,
     COUNT(*) FILTER (WHERE nwps_wfo IS NULL)            AS no_wfo,
     COUNT(*) FILTER (WHERE description IS NULL)         AS no_desc
   FROM spots;
   -- expected: no_buoy ≈ 185 (coverage gaps), no_wfo ≈ 14 (Great Lakes),
   -- no_desc = whatever generate_descriptions left
   ```
5. Spot-check the rating page: buoy badges should now appear next to
   CA / FL / east-coast breaks.

## What I'd do next (not this PR)

- Fast-follow: rerun `pipeline.scrape_surf_forecast` against the
  manifest-added spots to populate `surf_forecast_url` for them.
- LLM verification pass for the 200 manifest-added spots that have
  no `tide_preference` / `break_type` / `crowd_factor` / `hazards`.
- The 13 coastal tide-gap spots from the tide rebuild are still NULL;
  closing them needs `enrich.py` on a machine with NOAA egress so
  Algorithm 5 sees the real `tide_stations.json`. Same goes for the
  ~handful of legacy buoy gaps — but those have been NULL since
  `5177b0e` so they're not regressions and don't block anything.
