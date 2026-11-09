# Run 68: node preview of run 67's seven moves (sw1-raycast mode node-preview)

- **Run:** sw1-raycast run 68 (Actions run 42381611960), branch `claude/practical-dirac-bshfbr` at `be56d23`, 2026-11-07 14:26Z, `baseline_ref` main.
- **Artifact:** `node-preview-68`, zip sha256 `3c046667ca8db5b19ecde0f64bd13f36f9070faa3c35d0992ef5e4591a4292d4`. `node_preview.json` is saved here byte for byte; `node_preview_hours.csv` is gzipped.
- **Window:** the published forecasts from 14:27Z, replayed from 48 hours earlier because buoys were named.
- **References:**
  - neighbours: Whiskey Run, Bastendorff, Trinidad State Beach, Agate Beach, Long Sands, Harbor Beach, Lido, Jones Beach, Nahant, Winthrop Jetty, Lynn Beach, 61st Street Galveston;
  - buoys: 46015 (Bandon Beach, Hubbard Creek), 46244 (Moonstone Beach Humboldt), 42035 (Galveston Seawall).

## The gate: FAIL, from the preview, not the published data

- **Totals:** 1102 hours replayed. 391 input differences, 296 star differences, 390 face differences; 63 hours filled from a neighbouring hour.
- **Where:** every difference is at Bandon Beach, Hubbard Creek and Short Sands Beach York, on the cycles that start at low water.
  - Bandon: 119 of 119 future hours and 26 of 47 past.
  - Hubbard Creek: 111 of 111 and 34 of 42.
  - Short Sands: 89 of 89 and 12 of 32.
  - The other four spots replay with 0 differences: 549 hours, past and future.
- **Cause:** those three old cells dry at low tide. On a cycle starting at low water they are blank at f000, the hour the replay took its land mask from.
  - So the replay read the nearest cell wet at f000 instead, which for Bandon is the new cell (expected 2.767 m against 0.745 m stored).
  - `nwps.fetch`, which wrote the published rows, calls a cell water if it holds a wave value at any hour (`nwps._cell_is_water`), so it read the old cell itself.
- **Fix:** `node_preview` now picks its cell the way `nwps.fetch` does. The comparisons below use the published values as "old", not the faulty replay.

## Against each spot's own references (published, then new)

| Spot | Face, ft | Stars | New / each neighbour | Stormiest 10 h, face | Buoy, past 48 h (median node / buoy) |
|---|---|---|---|---|---|
| Bandon Beach | 3.04 → 12.59 | 2.28 → 3.29 | Whiskey Run 0.958, Bastendorff 1.078 | 3.81 → 21.39 (refs 18.99) | 46015, 4.33 m mean: 0.19 → 0.69 |
| Moonstone Beach Humboldt | 4.34 → 11.54 | 2.91 → 3.78 | Trinidad 0.999, Agate 0.987 | 5.81 → 19.74 (refs 19.83) | 46244, 3.39 m: 0.24 → 0.84 (66% within 20%) |
| Short Sands Beach York | 1.63 → 2.10 | 1.03 → 1.19 | Long Sands 1.145, Harbor 1.087 (mean 1.116) | 1.98 → 2.68 (refs 2.45) | |
| Point Lookout | 2.24 → 3.44 | 1.27 → 1.61 | Lido 1.224, Jones 1.102 (mean 1.163) | 2.96 → 4.73 (refs 4.09) | |
| Revere Beach | 0.84 → 1.38 | 0.89 → 1.06 | Nahant 1.072, Winthrop 1.268, Lynn 1.098 (mean 1.146) | 0.99 → 1.95 (refs 1.65) | |
| Galveston Seawall | 0.87 → 1.45 | 0.88 → 1.00 | 61st Street 1.155 | 1.47 → 2.82 (refs 2.36) | 42035, 0.27 m: 0.78 → 1.14 (47–48% within 20% both) |
| Hubbard Creek | 2.73 → 11.12 | 1.81 → 3.70 | no neighbours | 3.86 → 18.34 | 46015, 4.31 m: 0.17 → 0.58 |

- **The buoys:** 46015 is 15 nautical miles offshore, so a nearshore cell reads below it. Bandon's and Hubbard Creek's new cells read 0.69 and 0.58 of it, against 0.19 and 0.17 now.
- **Galveston's buoy comparison** was made in flat seas (0.27 m mean) and doesn't separate the two cells.

## Over 1.15 of their own neighbours

- **Point Lookout (1.163)** and **Galveston Seawall (1.155)** are over.
  - No nearer cell within 30 deg of facing is both wet every hour and at least 10 m deep.
  - Point Lookout's nearest wet cell, 2.02 km at 190 deg, is 8.6 m. Galveston Seawall's, 2.53 km at 134 deg, is 8.1 m.
- **Point Lookout, a qualifying cell next door:** 4.37 km at 209 deg, 13.0 m, 0.96 of the next cell seaward. On run 67's cycles it reads 1.141 of the two neighbours' cells (1.202 Lido, 1.080 Jones), against 1.158 for the cell moved to (run 67's cell ratios gave 1.22 and 1.10 for Lido and Jones, as run 68's faces did).
- **Under 1.15:** Short Sands Beach York (1.116) and Revere Beach (1.146).

## Homepage, "Best conditions right now"

- **Unchanged:** the same ten California spots at 5.0 stars, before and after.
- None of the moved spots is near it.
