# Spot Cleanup Manifest #2 — Apply Report
_Generated 2026-06-16._

Single transformation derived from `spot_cleanup_manifest_2.json`. No
live system was touched — all changes land in tracked files plus a
single DELETE SQL file for the user to run.

## Top-line counts

| | count |
|---|---:|
| Spots before | 489 |
| Spots after  | 672 |
| Coordinates updated | 384 |
| Additions added     | 204 |
| Deletions removed   | 21 |
| Renames applied     | 8 |
| Renames with new coords | 4 |
| Renames with new slug   | 8 |
| Excluded-list entries added | 29 |
| Slug collisions disambiguated | 2 |

## Files touched

| File | Change |
|---|---|
| `pipeline/spots_enriched.json` | coord updates in place, additions stubbed, deletions removed, renames applied |
| `pipeline/data/spot_coord_fixes.json` | +384 coord overrides + 3 rename+coord overrides, keyed by name |
| `pipeline/data/llm_spots.json` | +204 additions, -21 deletions, 8 rename name updates |
| `pipeline/data/excluded_spots.json` | +29 names under reason `manual_cleanup_2` (21 deletions + 8 rename old names) |
| `frontend/next.config.js` | +N 308 redirects for slug-changing renames |
| `docs/spot_cleanup_manifest_2.sql` | DELETE statement for the user to run in Supabase |

## DELETE SQL

Combined into one transaction at `docs/spot_cleanup_manifest_2.sql`.
It removes **29** rows from `spots` (=21 manifest deletions + 8 orphaned old-slug rows from slug-changing renames). Forecasts cascade.

## Slug disambiguations

| Addition name | base slug | final slug | source state |
|---|---|---|---|
| Rockpile | `rockpile` | `rockpile-ca` | California |
| Shell Beach | `shell-beach` | `shell-beach-ca` | California |

### Rename slug disambiguations

_No rename slug collisions._

## Rename redirects added to `next.config.js`

| Source | Destination |
|---|---|
| `/spot/trees` | `/spot/3-mile` |
| `/spot/little-wind-an-sea` | `/spot/wind-and-sea` |
| `/spot/p-b-boys-club` | `/spot/palm-beach` |
| `/spot/antonio-s-rincon` | `/spot/antonio-s` |
| `/spot/sandy-beach-rincon` | `/spot/sandy-beach` |
| `/spot/pools-rincon` | `/spot/pools` |
| `/spot/marias-rincon` | `/spot/maria-s` |
| `/spot/indicators-rincon` | `/spot/indicators` |

## Manifest entries that didn't match anything in the repo

_Every manifest slug matched an existing spot._

## All slug-changing renames (for your review)

| old slug | new name | new slug | new lat/lng |
|---|---|---|---|
| `trees` | 3 Mile | `3-mile` | `36.96135, -122.11454` |
| `little-wind-an-sea` | Wind and Sea | `wind-and-sea` | — |
| `p-b-boys-club` | Palm Beach | `palm-beach` | `26.703063, -80.031838` |
| `antonio-s-rincon` | Antonio's | `antonio-s` | — |
| `sandy-beach-rincon` | Sandy Beach | `sandy-beach` | — |
| `pools-rincon` | Pools | `pools` | — |
| `marias-rincon` | Maria's | `maria-s` | `18.358212, -67.270296` |
| `indicators-rincon` | Indicators | `indicators` | `18.360472, -67.271433` |

## Unmapped addition states (review needed)

_All additions mapped to a known region bucket._
