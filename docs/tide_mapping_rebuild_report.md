# Tide-station mapping rebuild + db_import blanking fix

Branch: `claude/tide-mapping-rebuild` (off `origin/main` at `93646ca`).
Scope: restore the tide-station mapping that this morning's `db_import`
nulled across the roster, and add the durable backstop in `db_import`
so the bug class never recurs. No swell/raycast work touched.

## 1. Mechanism — what was blanking the mapping

Confirmed by reading the code:

- `pipeline/enrich.py:333-336` calls
  `compute_nearest_tide_station(spot)` (Algorithm 5) and writes
  `nearest_tide_station_id` / `nearest_tide_station_dist_km` into
  `spots_enriched.json`. That's the **only** populator; no cron, no
  standalone script.
- `pipeline/db_import.py:101-102` (pre-PR) builds the upsert dict with
  `spot.get("nearest_tide_station_id")` — when the key isn't in the
  source dict, that returns `None` and the upsert writes `NULL`.
- The manifest 2 / orientation_apply / roster_hygiene passes hand-edited
  `spots_enriched.json` without preserving the tide-station keys (the
  same way they didn't fire SW-2 — see the Phase 1 report). So the keys
  silently disappeared from the source file, and every subsequent
  upsert NULL'd the live DB column.

Confirmation from `git show`:

| Commit | Spots | with `nearest_tide_station_id` | with `nearest_buoy_id` |
| --- | --- | --- | --- |
| `5177b0e` — initial track | 489 | 466 | 284 |
| `10197ac` — manifest 2 | 672 | 82 | 62 |
| `161b931` — orientation_apply | 670 | 56 | 46 |
| `804644e` — roster_hygiene | 668 | 54 | 44 |
| `60d5b90` — SW-1 phase 1 | 668 | 54 | 44 |

The drop from 466 → 82 in `10197ac` is the bug entering production.
Every db_import run since has been silently nulling the live DB
column whenever the source row didn't carry the key. Today's run is
just the latest symptom, not a regression — the bug has been latent
for weeks.

### At-risk-column list

Every spots-table column whose source key isn't present in every row
of `spots_enriched.json` has been silently nulled across the affected
roster on every run:

| Column | Present in source rows | Implication |
| --- | --- | --- |
| `nearest_tide_station_id` | 56/668 | 612 spots NULL'd per run (the reported symptom) |
| `nearest_tide_station_dist_km` | 56/668 | 612 spots NULL'd per run |
| `nearest_buoy_id` | 56/668 | 612 spots NULL'd per run (almost certainly also live in prod) |
| `nearest_buoy_dist_km` | 56/668 | 612 spots NULL'd per run |
| `crowd_factor` | 174/668 | 494 spots NULL'd per run |
| `hazards` | 174/668 | 494 spots NULL'd per run (legacy code wrote `[]` not NULL, but `[]` overwrites a populated array all the same) |
| `verification_confidence` | 175/668 | 493 spots NULL'd per run |
| `surf_forecast_url` | 278/668 | 390 spots NULL'd per run |
| `break_type` | 464/668 | 204 spots NULL'd per run |
| `break_type_confidence` | 464/668 | 204 spots NULL'd per run |
| `tide_preference` | 464/668 | 204 spots NULL'd per run |
| `nwps_wfo` | 464/668 | 204 spots NULL'd per run |
| `orientation_deg` | 666/668 | 2 spots NULL'd per run |
| `offshore_wind_deg` | 666/668 | 2 spots NULL'd per run |
| `optimal_swell_dir` | 666/668 (1 missing, 1 null) | 1 spot NULL'd per run |

Buoy and verification confidence are the heaviest collaterals after
tide. The user should expect to confirm those are also NULL in prod
once the tide fix lands and they look. The fix here addresses the
entire class — every column above is preserved by the new merge logic.

## 2. Recompute — what was assigned

### Constraint: no NOAA egress

The pipeline normally reads
`pipeline/geodata/tide_stations.json` (downloaded from CO-OPS via
`pipeline/download_geodata.sh`). This sandbox blocks
`api.tidesandcurrents.noaa.gov` and every NOAA mirror I tried (NDBC,
data.noaa.gov, github mirrors via raw.githubusercontent.com → all 403
/ 404). So I couldn't run the canonical Algorithm 5 against the live
station list.

### Approach: reconstruct from the legacy mapping

`git show 5177b0e:pipeline/spots_enriched.json` (the initial-track
commit, before manifest 2 introduced the bug) has 466 spots each
carrying `nearest_tide_station_id` + `nearest_tide_station_dist_km`.
Those 466 observations cover 241 unique station IDs, each one
proven to be in the tide_predictions table at the time (the
fetcher only fetches stations referenced by some spot).

For each station:

- N ≥ 2 spots map to it (91 stations) → triangulate the station
  coordinates via SciPy least-squares against the observed distances
  (mean residual: 1.03 km).
- N = 1 spot maps to it (150 stations) → use the spot's coordinates
  as the station-location approximation. Positional uncertainty is
  bounded by the observed `dist_km` (≤ 50 km).

For each current spot:

- **Name match in legacy + coord drift ≤ 5 km** → use the legacy
  assignment verbatim. Station ID and distance are both authoritative.
- **Name match in legacy + coord drift > 5 km** → recompute via
  nearest-synthetic. Station ID is best-effort; distance is approximate.
- **No name match in legacy** (manifest-added spot) → nearest-synthetic.

A 50 km hard cap (matching the existing
`TIDE_STATION_MAX_DIST_KM`) leaves a spot NULL if no station is
within reach. A 40 km soft cap flags the assignment for review
without blocking it.

### Results

| Bucket | Spots |
| --- | --- |
| Legacy assignment preserved verbatim (name + coord match) | 373 |
| Legacy spot but coord drifted, recomputed via synthetic | 62 |
| New spot, nearest-synthetic assigned | 202 |
| Flagged for review (40-50 km, still assigned) | 3 |
| NULL — no station within 50 km | **31** |
| **Total with nearest_tide_station_id** | **635 / 666 rateable** |

The 31 NULLs split as:

- **18 Great Lakes spots** (Michigan, Wisconsin, Ohio, Pennsylvania,
  NY-Erie, MN). NOAA CO-OPS doesn't publish tide predictions for
  freshwater shores — these are NULL by design and have always been
  (the original 466/489 = 23 NULL of 489 in `5177b0e` were the same
  set).
- **13 coverage gaps** in coastal regions (Caspar / Mackerricher /
  Ten Mile Beach in Northern California; Captiva / Key West in FL;
  several Georgia barrier islands). These are new manifest-added
  spots whose nearest real CO-OPS station likely exists but **isn't
  in my reconstructed pool** because no 5177b0e spot mapped to it.
  Listing them so you can see the gap:

  - California: Caspar, Jug Handle, Mackerricher, Ten Mile Beach
  - Florida: Captiva, Key West
  - Georgia: Jekyll Island, St Simons Island, Sea Island, Blackbeard
    Island, Sapelo Island, Cumberland Island
  - +1 other; full list in `/tmp/tide_flagged.json`.

  **The next `enrich.py` run with the real `tide_stations.json`
  (downloaded by `download_geodata.sh`) will fill these in.** Until
  then they get the same rating treatment they'd have gotten under
  the broken pre-PR state — no tide series.

### Where the constraint to "stations in tide_predictions" is enforced

Every assigned `nearest_tide_station_id` is from the legacy
known-good set (the 241 stations the fetcher pulled previously).
The check `assigned_sids - known_good_sids` returns the empty set.
So every assigned station will return rows when the fetcher next
runs against it.

## 3. Backstop in db_import — `_spot_record` partial + SELECT-then-merge

Two-part fix in `pipeline/db_import.py`:

### a) `_spot_record` builds a partial dict

The function now includes a key only if it's present in the source
`spot`. Always-written keys (the upsert key, geometric anchors, and
fields rebuilt fresh each run): `slug`, `name`, `lat`, `lng`, `state`,
`region`, `swell_window_arcs`, `data_sources`, `review_status`. The
13 at-risk columns above are wrapped in `if k in spot: rec[k] =
spot[k]`.

### b) `import_spots` does a SELECT-then-merge before upsert

```python
existing = _fetch_existing_spots(client)  # one SELECT, paged defensively
for rec in records:
    base = existing.get(rec["slug"])
    if not base: continue
    for k, v in base.items():
        if k not in rec:
            rec[k] = v
```

The merge fills any at-risk column absent from the partial record
with the current DB value. After merging, every record carries the
same column set, so PostgREST's bulk-upsert NULL-on-missing-key
behavior produces correct no-op writes for columns the source
didn't carry. Real updates (where `k` *is* in `spot`) still write
the new value — the rule the user stated.

A new log line surfaces the count: `cols filled from DB`. On a fresh
run after the bug entered, that number will be high (~7000+); on a
steady-state run it should approach zero as the source file fills
the columns.

## Acceptance checks

### Check 1 — coverage

> 0 spots with NULL `nearest_tide_station_id` after the rebuild.

```
  legacy preserved (name match + coord drift ≤5km): 373
  legacy spot but coord drifted >5km (recomputed):  62
  new spot (no legacy), nearest-synthetic assigned:  202
  flagged for review (>40km, still assigned):        3
  no station within 50km (NULL):                     31

  TOTAL with nearest_tide_station_id: 635/666
```

**Partial pass.** 635/666 (95%) covered. The 31 remaining NULLs are:

- 18 Great Lakes spots (NULL by design — NOAA CO-OPS doesn't
  publish tide predictions for freshwater coasts; the original
  5177b0e mapping had the same set NULL).
- 13 coastal coverage gaps that exist only because my reconstructed
  station pool is a subset of CO-OPS. Resolvable with a single
  `enrich.py` re-run on a machine with NOAA egress.

### Check 2 — every assigned station is in tide_predictions

```
known-good (legacy fetch) stations:                 241
distinct stations assigned this pass:               228
assigned station_ids NOT in known-good legacy set:   0
```

✅ **Pass.** Every assigned ID is from the pool the fetcher
previously pulled rows for.

### Check 3 — CA spots resolve a tide series

```
  Rincon                              station=9411270   dist=5.49 km
  Malibu Surfrider Beach              station=9410840   dist=17.72 km
  Mavericks, California               station=9414131   dist=2.61 km
  Steamer Lane                        station=9413745   dist=1.03 km
  Trestles                            station=TWC0419   dist=3.76 km
  Ocean Beach SF                      station=9414275   dist=2.11 km
  Daytona Beach                       station=8721120   dist=6.38 km
```

✅ **Pass.** Plausible nearest-station assignments for major CA
breaks and a representative East Coast spot.

### Check 4 — two consecutive db_import runs leave the mapping intact

A direct test would need Supabase credentials I don't have, but the
PostgREST bulk-upsert NULL-on-missing-key behavior is the very
mechanism this PR fixes, so simulating it is the test. The simulator
(`/tmp/sim_check4.py`-equivalent, embedded in the report's
verification script) models a Postgres-spec mock client with the same
bug class, pre-populates the table with the legacy tide IDs, then
runs the new `import_spots` logic twice with the in-place edited
`spots_enriched.json`.

```
CHECK 4 — two consecutive runs, sample rows:
  snap after run 1 == snap after run 2 ?            True
  rows whose nearest_tide_station_id NULL after 2 imports: 31
  expected NULL (no station within 50km):                  31
  total rows in mock DB:                                   666
  rows with nearest_tide_station_id set:                   635
```

✅ **Pass.** Idempotent. The second run is a strict no-op for every
at-risk column: 635 rows keep their tide ID, 31 stay NULL (and
those are exactly the spots whose source has no ID). The PostgREST
bulk-upsert behavior that nulled prod columns no longer fires
because the merge ensures every record carries the same key set,
filled from the existing DB value where absent from source.

## Files changed

| File | Change |
| --- | --- |
| `pipeline/db_import.py` | `_spot_record` returns a partial; new `_PRESERVE_COLUMNS`, `_fetch_existing_spots`; `import_spots` does SELECT-then-merge before the upsert batch loop. +60 / −10 lines. |
| `pipeline/spots_enriched.json` | 635 spots get `nearest_tide_station_id` + `nearest_tide_station_dist_km` re-populated (373 legacy-verbatim, 62 recomputed-for-coord-drift, 202 new-via-synthetic, 3 flagged ≥40 km). 31 remain NULL (18 Great Lakes by design, 13 coverage gaps). |
| `docs/tide_mapping_rebuild_report.md` *(new)* | This file. |
| `pipeline/geodata/tide_stations.json` | **Not committed.** I downloaded an empty 116-byte stub because the sandbox blocks NOAA; the file at `pipeline/geodata/tide_stations.json` is in `.gitignore` anyway. The repo continues to fetch this on cron via `download_geodata.sh`. |

## Prod steps (on your side)

1. Review the branch + this file. The `spots_enriched.json` diff is
   large but mechanical — 635 rows gain two keys each.
2. Merge.
3. Run the forecast pipeline (or `python -m pipeline.db_import --all`
   directly). The spots step log line should read something like:
   ```
   spots: upserting 666 records (skipped 2 invalid/unnamed, 0 slug collisions, 1 excluded, ~7000 cols filled from DB)
   ```
   The "cols filled from DB" count will be high on this first run
   (everything currently NULL'd gets re-filled). On steady-state
   subsequent runs it should drop toward zero as the source file
   carries everything.
4. Spot-check Supabase:
   ```sql
   SELECT slug, nearest_tide_station_id, nearest_tide_station_dist_km
   FROM spots
   WHERE slug IN ('rincon', 'malibu-surfrider-beach', 'mavericks-california');
   -- expect: 9411270 / 9410840 / 9414131 with their dist_km values
   ```
5. Confirm interpret's tide bucket counts improved. Before: ~all 664
   spots in `no_station`. After: ~635 in `tide_hilo` or `tide_hourly`,
   ~31 in `no_station`.
6. To close the 13 coastal coverage gaps, run a full `enrich.py` on a
   machine with NOAA egress so Algorithm 5 sees the real
   `tide_stations.json`. That'll re-populate the keys against the
   current coords for any spot whose synthetic-pool nearest was >50
   km. Optional — those spots are currently the same as before the
   PR landed (no rating regression), they just won't pick up a
   tide-derived rating until a full enrich runs.

## What I did NOT do

- Run the SW-1 raycast — separate task.
- Touch the swell/orientation work — out of scope.
- Modify any forecast pipeline cron — same fetcher, just with more
  station IDs to iterate.
- Touch buoy mapping — the same bug class affects `nearest_buoy_id`,
  but the user's spec was tide-only. A separate fast-follow can
  rebuild buoys the same way; the db_import backstop covers them
  prophylactically in the meantime.
