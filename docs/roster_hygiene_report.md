# Roster hygiene — pre-SW-1 dedup fix

Branch: `claude/roster-hygiene` (off `origin/main` at `f7c0427`).
Goal: clean source-file slug collisions so the SW-1 raycast runs on a roster
with one row per real spot — no silent `_dedupe_by_slug` drops.

## Post-edit state

`pipeline/spots_enriched.json`: 670 → **668** rows, **668 unique slugs, 0
collisions.** Verified by re-running the slug map after edits.

| Slug | Resolution | DB impact |
| --- | --- | --- |
| `rockpile` | distinct — disambiguated | upsert keeps existing row |
| `shell-beach` | distinct — disambiguated | upsert keeps existing row |
| `spyder` | both are surf shops — excluded | no row to remove (never landed in DB) |

No DELETE SQL needed. All edits flow through the next `db_import` upsert.

---

## Per collision

### `rockpile` — distinct spots, disambiguated

Two real breaks 100 km apart on the SoCal coast:

| Source row | Lat / Lng | Origin | Decision |
| --- | --- | --- | --- |
| `Rockpile` (idx 91) | 34.4203, -119.9002 — Santa Barbara stretch (Carpinteria area) | seeded (no `sources.osm_id`, no `manifest_addition`); current live DB survivor | **Keep as-is.** Name `Rockpile`, slug `rockpile`. |
| `Rockpile` (idx 476) | 33.5414, -117.7918 — South Laguna (near Crescent Bay) | `source: "manifest_addition"` from manifest 2 | **Rename → `Rockpile (Laguna)`, slug `rockpile-laguna`.** Orientation/window fields cleared so next enrich rebuilds them; flagged in the row as `disambiguated_from: rockpile`. |

`llm_spots.json` side:
- Two `Rockpile` entries in `southern_california` — both pointed at Laguna (one had coords, one was a no-coord nearest-town seed for "Laguna Beach, CA").
- Kept the coord-bearing entry, renamed it to `Rockpile (Laguna)`.
- Dropped the no-coord duplicate (it would have geocoded to the same Laguna spot and slug-collided post-rename).

The SB Rockpile (idx 91) doesn't appear in `llm_spots.json` — it came from a different seed path. No `llm_spots` change for it.

### `shell-beach` — distinct spots, disambiguated

Two breaks in the Shell Beach community of Pismo Beach, ~2.6 km apart:

| Source row | Lat / Lng | Origin | Decision |
| --- | --- | --- | --- |
| `Shell Beach` (idx 107) | 35.1515, -120.6670 — well-known reef south end | seeded, current live DB survivor | **Keep as-is.** Name `Shell Beach`, slug `shell-beach`. |
| `Shell Beach` (idx 507) | 35.1632, -120.6918 — ~1.3 km N, ~2.3 km W (Dinosaur Caves / Margo Dodd Park stretch) | `source: "manifest_addition"` from manifest 2 | **Rename → `Shell Beach (North)`, slug `shell-beach-north`.** Orientation/window fields cleared. |

`llm_spots.json` side:
- `Shell Beach` (no coords, town "Shell Beach, CA") — kept as-is; geocodes to the Pismo reef.
- `Shell Beach` (lat 35.163, no town) — renamed to `Shell Beach (North)`.

### `spyder` — both surf shops, excluded

Both rows are already flagged `is_valid_surf_spot: false`, `invalid_reason: "surf_shop"`, `verification_confidence: "high"` — the LLM verification pass correctly caught them at seed time, which is why neither lands in the DB.

| Source row | Lat / Lng | OSM | Reading |
| --- | --- | --- | --- |
| `Spyder` (idx 110) | 33.8846, -118.4104 — Manhattan Beach | `node/9773757139`, `sport=surfing` | Spyder Surf retail location |
| `Spyder` (idx 111) | 33.8623, -118.4006 — Hermosa Beach | `node/12349687627`, `sport=surfing` | Spyder Surf retail location |

**Conclusion: junk.** Both are Spyder Surf shops in the South Bay tagged `sport=surfing` by OSM. The high-confidence verification flag is correct.

- Removed both rows from `spots_enriched.json` (they were dead weight — never made it past `db_import`).
- Added `"Spyder"` to `excluded_spots.json` under existing reason `surf_shop` (next to `Spyder Surfboards`, which was already there) so future seed crawls drop the candidates at the seed step rather than relying on per-run LLM verification.

---

## Caveats

**Orientation overrides on the two renamed spots are intentionally cleared.**
`pipeline/data/spot_orientations.json` has only `rockpile` and `shell-beach`
keys — those are the SB / Pismo originals. The renamed Laguna / North rows
no longer match a key in that file, so the next enrich runs the geometric
algorithm seed for both. Per your spec ("for now just let it take the
geometric seed; I'll set it by hand in the next orientation touch-up").

**Confidence in the SB-vs-Laguna survivor pick.** You said the surviving
`rockpile` DB row is at 34.4203, -119.9002. The branch's idx-91 row matches
that exactly, so the rename preserves the live row by leaving it alone.
Same for `shell-beach` at 35.1515, -120.6670 (idx 107).

**`rockpile-laguna` raycast window staleness.** The row's existing
`swell_window_arcs` were built from the old (SB-derived) orientation
override. They've been cleared, and SW-1 will rebuild them against the
Laguna position and geometric orientation.

---

## Bonus sanity check (OSM shop / business pass)

Scanned `pipeline/spots_enriched.json` for `tags.shop=*` and obvious
business keywords in `name` (`surf shop`, `surfboards`, `rental`,
`school`, `academy`, `store`, `outfitters`). Among **valid** spots:
**zero** matches — the live roster contains no shops.

Among **invalid** spots in `spots_enriched.json` (which don't reach the
DB, but do consume rows in the source file):

| Name | Lat / Lng | Reason | Action taken |
| --- | --- | --- | --- |
| `Spyder` x2 | South Bay CA | `surf_shop` | Removed + excluded (this PR) |
| `Seal Beach, California` | 33.7592, -118.0825 | `duplicate` | **Listed for your review.** Likely a duplicate of plain `Seal Beach`. Not auto-removed — possibly intentional disambiguation experiment. |
| `Lewes Street Surf Beach` | 38.4677, -75.0493 | `non_surfable` | **Listed for your review.** Inland Delaware — LLM verification flagged it. Not auto-removed. |

Confirm and I'll add them to `excluded_spots.json` in the next pass.

---

## Files changed

| File | Change |
| --- | --- |
| `pipeline/spots_enriched.json` | -2 Spyder rows; idx 476 renamed → `Rockpile (Laguna)`; idx 507 renamed → `Shell Beach (North)`; orientation/window fields cleared on both renamed rows |
| `pipeline/data/llm_spots.json` | Dropped duplicate no-coord Laguna `Rockpile`; renamed coord-bearing `Rockpile` → `Rockpile (Laguna)`; renamed coord-bearing `Shell Beach` → `Shell Beach (North)` |
| `pipeline/data/excluded_spots.json` | +`"Spyder"` under `surf_shop` |
| `docs/roster_hygiene_report.md` *(new)* | This file |

## Prod steps (in order, on your side)

1. Review the branch + this report.
2. Merge via the GitHub UI. *(No DELETE SQL — no live row needs removing.)*
3. Trigger the full pipeline upsert (`python -m pipeline.db_import --all`
   via the workflow).
4. Spot-check: `SELECT slug, name, lat, lng FROM spots WHERE slug LIKE
   'rockpile%' OR slug LIKE 'shell-beach%';` — expect four rows
   (`rockpile`, `rockpile-laguna`, `shell-beach`, `shell-beach-north`).
   And `SELECT … WHERE name ILIKE '%spyder%';` — expect zero.
5. Confirm the two ambiguous rows (`Seal Beach, California`, `Lewes
   Street Surf Beach`) and I'll fold them into the next cleanup.
6. Roster is then clean for SW-1.
