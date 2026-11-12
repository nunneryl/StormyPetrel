# Run 67: the NWPS cells around the blank and low sample points (sw1-raycast mode nwps-cells)

- **Run:** sw1-raycast run 67 (Actions run 42289868236), branch `claude/practical-dirac-bshfbr` at `bf3ca68`, 2026-11-06 20:37–20:45Z.
- **Spots read:** Bandon Beach, Rincon, Tarpits, Revere Beach, Tokeland, Short Sands Beach York, Hubbard Creek, Galveston Seawall, Point Lookout and Moonstone Beach Humboldt, plus the spots it flags lowest.
- **Artifact:** `nwps-cells-67`, zip sha256 `ee17c6088f82fe6e70c464418626e58afd7641958688d6c39a5ec7ca824d7a89`. `nwps_cells.json` is saved here byte for byte; `nwps_cells.csv` is gzipped.
- **Stored gzipped since 2026-11-12:** `etopo_join.txt`, `nwps_cells.json` are kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte; the sha256 of each is in `../CHECKSUMS.sha256`). Nothing else changed.
- **What it read:**
  - the 4 newest NOMADS cycles of each office (Nov 5 to Nov 6);
  - every cell within 3 of each spot's sample cell: wet hours, and NWPS's mean Hs, period and direction;
  - the neighbour comparison since Sep 7.
- **Depths** (`etopo_join.txt`): ETOPO 2022 15 arc-second from ERDDAP, joined afterwards. For each cell: the depth at its centre, and over the cell, the share of land and the median water depth.
- **The rule** (run 58's, plus a wet test), checked in every cycle:
  - the nearest cell within 30 deg of the spot's facing;
  - at least 10 m deep at its centre;
  - NWPS Hs at least 0.9 of the next cell seaward (the neighbour whose bearing is nearest the facing);
  - wet every hour of every cycle, meaning Hs, period and direction all set.
- `etopo_join.txt` lists every cell of each candidate with the rule's verdict.

## The candidates

| Spot | Current cell | Nearest qualifying cell | New vs current | New vs neighbours (same cycles) | Recommendation |
|---|---|---|---|---|---|
| Bandon Beach | 1.4 m, 33% land, wet 120–127 of 145 h, 0.24–0.26 of seaward | 2.12 km at 266 deg, 33.0 m, 0.98 of seaward | 3.9–4.2x | neighbours not in the window; buoy 46015 in run 68 | move |
| Moonstone Beach Humboldt | 2.4 m, 25% land, always wet, 0.35–0.36 of seaward | 6.36 km at 283 deg, 29.0 m, 0.97–0.98 (Trinidad State Beach's cell) | 2.8x | 1.00 of Trinidad, 0.99 of Agate Beach | move |
| Short Sands Beach York | -0.8 m (land at centre), wet 72–75 h, 0.78–0.81 | 3.95 km at 66 deg, 35.8 m, 0.95–0.96 | 1.24–1.29x | 1.21–1.28 of Long Sands, 1.08–1.11 of Harbor Beach | move |
| Point Lookout | 2.0 m, 40% land, wet 127–139 h, 0.65–0.70 | 3.82 km at 185 deg, 13.5 m, 0.96–0.97 | 1.48–1.63x | 1.21–1.24 of Lido Beach, 1.08–1.11 of Jones Beach | move |
| Revere Beach | 2.3 m, 47% land, wet 95–99 h, 0.78–0.81 | 5.75 km at 134 deg, 14.1 m, 0.91–0.93 | 1.71–2.09x | 1.08 of Nahant, 1.17–1.26 of Winthrop Jetty, 1.15–1.26 of Lynn Beach | move, watch shelter |
| Hubbard Creek | 1.7 m, 20% land, wet 110–112 h, 0.24–0.26 | 3.29 km at 234 deg, 37.3 m, 0.93–0.94 | 3.9–4.2x | no NWPS neighbour; buoy 46015 (29.7 km) in run 68 | move, pending the buoy |
| Galveston Seawall | -1.9 m (land at centre), 31% land, wet 115–120 h, 0.62–0.65 | 5.06 km at 135 deg, 10.3 m (cell median 9.9 m), 0.95 | 1.66–1.75x | 1.18 of 61st Street Galveston; buoy 42035 (37.5 km) in run 68 | move, pending the buoy |
| Rincon / Tarpits (one cell) | 21.7 m, 19% land, wet 125–129 h, 0.42–0.45 | 12.51 km at 186 deg, 69.3 m, 0.97–0.98 | 2.9–3.0x | 1.41 of Carpinteria, 1.29–1.31 of Faria and Mussel Shoals, 1.18–1.19 of Pitas Point and Mondos | hold: would lose real shelter |
| Tokeland | 1.1 m, 29% land, wet 134–139 h, 0.31–0.32 | 12.13 km at 230 deg, 13.4 m, 0.91–0.93 | 4.1–4.4x | Westport, 21.6 km, not in the window | hold: outside Willapa Bay |

- **Rincon / Tarpits.** Every closer cell within 30 deg is wet and 38 to 82 m deep, but reads only 0.82 to 0.87 of the next cell seaward. Wave height keeps growing down the Santa Barbara Channel, so the first cell that passes is 12.5 km out. There it reads well above the open beaches nearby.
  - **A closer option, outside the rule:** the cell 4.60 km at 196 deg (37.9 m, wet every hour) reads 0.71 to 0.82 m, about Carpinteria's level.
- **Tokeland** faces the mouth of Willapa Bay from inside it. The qualifying cell is in open water off the bay entrance, at 4.1 to 4.4 times the current cell. Its blank hours are few (5% in run 66).
