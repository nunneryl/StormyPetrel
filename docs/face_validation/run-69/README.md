# Run 69: node preview of the seven moves, Point Lookout and Galveston Seawall changed (sw1-raycast mode node-preview)

- **Run:** sw1-raycast run 69 (Actions run 42512876919), branch `claude/practical-dirac-bshfbr` at `15ab506`, 2026-11-08 19:01Z, `baseline_ref` main.
- **Artifact:** `node-preview-69`, zip sha256 `99dfdd54bac22e4145e6a7f84f7aeb0541207ff892c2a573a26bb8415f6cfa1e`. `node_preview.json` is saved here byte for byte; `node_preview_hours.csv` is gzipped.
- **Window:** the published forecasts from 19:02Z, replayed from 48 hours earlier because buoys were named.
- **Changed since run 68:**
  - Point Lookout reads the qualifying cell beside run 67's (4.37 km at 209 deg).
  - Galveston Seawall shares 61st Street Galveston's cell (4.98 km at 224 deg).
  - The replay takes the cell `nwps.fetch` reads (91afe30).
- **References:**
  - neighbours: Whiskey Run, Bastendorff, Trinidad State Beach, Agate Beach, Long Sands, Harbor Beach, Lido, Jones Beach, Nahant, Winthrop Jetty, Lynn Beach;
  - buoys: 46015 (Bandon Beach, Hubbard Creek), 46244 (Moonstone Beach Humboldt), 42035 (Galveston Seawall).
  - 61st Street is left out: it is now Galveston Seawall's own cell.

## The gate: FAIL, on one cycle's late hours

- **Totals:** 1063 hours replayed. 30 input differences, 0 star differences, 15 face differences; 107 hours filled from a neighbouring hour.
- **Cell choice:** every new node is a wet cell, and the cell `nwps.fetch` samples, in every cycle read (4 of 4; 7 of 7 for Short Sands).
- **Where:** all 30 are on MFR's 2026-11-08 00Z cycle, at Bandon Beach (13) and Hubbard Creek (17).
  - They are late in that cycle: from 2026-11-12 20Z for Bandon and 2026-11-12 09Z for Hubbard Creek, to the last hour (11-14 00Z).
  - Every earlier hour of the same cycle matches. In all, 117 of Bandon's 130 hours on that cycle match, and 107 of Hubbard Creek's 124.
  - The other 1033 hours all match, at all six offices and on every other cycle, MFR's included.
- **How big:** at most 0.018 m in hs, 0.012 s in tp, 0.020 m in swell_hs, and dp 245 stored against 225 expected on Hubbard Creek's last two hours.
  - The faces differ by at most 0.08 ft (15 hours), and no star changes.
  - The gap grows with lead time: Hubbard Creek's hs is 0.004 m apart on 11-13 13Z, 0.010 on 11-13 19Z and 0.018 on 11-13 22Z.
- **Examples, stored → expected:**

| Spot, hour | hs | tp | dp | swell_hs |
|---|---|---|---|---|
| Bandon, 11-12 20Z | 1.180 → 1.180 | 15.011 → 15.011 | 275 → 275 | 0.972 → 0.971 |
| Bandon, 11-13 18Z | 0.902 → 0.900 | 16.178 → 16.171 | 275 → 275 | 0.685 → 0.685 |
| Hubbard Creek, 11-13 21Z | 1.018 → 1.031 | 15.242 → 15.232 | 245 → 245 | 0.695 → 0.678 |
| Hubbard Creek, 11-13 23Z | 0.893 → 0.910 | 15.118 → 15.109 | 245 → 225 | 0.591 → 0.572 |

- **Likely cause, unconfirmed:** hs, tp and dp are the file's own swh, perpw and dirpw at the exact hour, read straight from NOMADS.
  - So the 00Z MFR file NOMADS serves now holds slightly different late-hour values from the one the pipeline read when it published.
  - That fits a re-run or re-post of that cycle. NOMADS can't be reached from the review machine, so this isn't confirmed.
  - The replay picks the same cell in every cycle, and every other hour of every office and cycle matches. That makes a code fault unlikely.

## Against each spot's own references (replayed old point, then new)

| Spot | Face, ft | Stars | New / each neighbour (face) | Buoy, past 48 h (median node / buoy) |
|---|---|---|---|---|
| Bandon Beach | 3.17 → 13.30 | 2.29 → 2.87 | Whiskey Run 0.965, Bastendorff 1.114 (mean 1.039) | 46015, 4.53 m mean: 0.187 → 0.765 (43% within 20%) |
| Moonstone Beach Humboldt | 4.76 → 11.74 | 3.08 → 3.67 | Trinidad 1.000, Agate 0.983 (mean 0.991) | 46244, 3.40 m: 0.262 → 0.897 (75% within 20%) |
| Short Sands Beach York | 1.76 → 2.25 | 1.09 → 1.21 | Long Sands 1.071, Harbor 1.034 (mean 1.052) | |
| Point Lookout | 2.23 → 3.46 | 1.29 → 1.64 | Lido 1.286, Jones 1.097 (mean 1.191) | |
| Revere Beach | 0.96 → 1.72 | 0.95 → 1.21 | Nahant 1.062, Winthrop 1.198, Lynn 1.154 (mean 1.138) | |
| Hubbard Creek | 2.77 → 12.20 | 1.74 → 3.17 | no neighbours | 46015, 4.52 m: 0.165 → 0.630 (29% within 20%) |
| Galveston Seawall | 0.89 → 1.31 | 0.84 → 0.95 | none (61st Street is the same cell) | 42035, 0.65 m: 0.566 → 0.758 (33% → 9% within 20%) |

- **Over 1.15 of its own neighbours: Point Lookout (1.191).** Run 67's cell ratios gave 1.141 for this cell, and run 68's faces gave 1.163 for the first one, so it is higher in this window.
  - Revere Beach averages 1.138, under 1.15, but is 1.198 of Winthrop Jetty and 1.154 of Lynn Beach.
- **Galveston Seawall against 42035** splits by sea state:
  - flat, buoy 0.2 to 0.3 m (17 hours): the shared cell reads 1.28 of the buoy, the old point 0.97;
  - building, buoy 0.7 to 1.6 m (26 hours, from 11-07 17Z): the shared cell reads 0.67, the old point 0.45, and no hour is within 20%.
  - 42035 is 37.5 km offshore, so a nearshore cell reading under it in a building sea is expected.
  - 42035 reports Hs in 0.1 m steps, so the flat-sea ratios are coarse.
- **The buoys at Bandon and Hubbard Creek:** 46015 is 15 nautical miles offshore. The new cells read 0.77 and 0.63 of it, against 0.19 and 0.17 now.

## Homepage, "Best conditions right now"

- **Unchanged:** the same ten spots before and after:
  - Shelter Cove, Trinidad State Beach, Brookings Beach, Lincoln City Beach, Nelscott Reef, Roads End, Seaside Cove, Westport and Agate Beach at 5.0 stars;
  - Crescent City Beach at 4.5.
- None drops out and none comes in.
