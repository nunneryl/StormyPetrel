# Run 66: blank NWPS hours and the tide (sw1-raycast mode nwps-blank)

- **Run:** sw1-raycast run 66 (Actions run 42251731785), branch `claude/practical-dirac-bshfbr` at `33962cd`, 2026-11-06 16:22–17:27Z.
- **Artifact:** `nwps-blank-66`, zip sha256 `4e7addeca671232065c012f8e92ccf792d76e6dc15e7bc1e04db0cd10ce121dd`. `nwps_blank.json` and `nwps_blank.csv` are saved here byte for byte.
- **What it read:**
  - **Cycles:** the 3 newest NOMADS cycles of each office (Nov 5 to Nov 6), each read by the pipeline's own NWPS reader with only that cycle on offer.
  - **Stored forecasts:** every NWPS-rated hour since Oct 7, 736 hours a spot.
  - **Tide:** the stored tide level of the same hour.
- **Errors:** one. chs's 12Z cycle on Nov 6 was listed but not yet readable, so chs counts 2 cycles.
- **A blank hour** is one where the spot's sample cell has no wave height, period or direction.
  - **Filled:** the rater filled it from the hour before or after.
  - **Not NWPS-rated:** a blank stretch longer than two hours has no neighbour for its middle hours. Those hours fall back to the non-NWPS rating.

## The counts (598 NWPS spots)

- **Recent cycles:**
  - 10 spots have blank hours; 9 of them in all 3 cycles.
  - 9 of the 10 are blank at low tide. Pompano Beach Pier had a single blank hour.
  - The run's rule said "not tidal" for Short Sands Beach York, but its blank hours sit at a median tide of 1.6 ft against 7.8 ft for the others, in two stretches a day, 12 hours apart.
  - **Why the rule missed it:** the rule asks for 60% of blank hours in the lowest quarter of the tide range. A spot blank half the time can have at most half its blank hours there.
- **Stored forecasts since Oct 7:** 15 spots have filled hours.
  - 5 of them have none since #288 (Oct 30): Rodanthe, S-Turns, Pompano Beach Pier, Indicators and Rincon Domes.

## Spots with blank hours in the recent cycles

| Spot | Office | Blank share (3 cycles) | Tide | Stretches a day, gap | Filled / not NWPS-rated since Oct 7 | Neighbour ratio (run 58) |
|---|---|---|---|---|---|---|
| Short Sands Beach York | gyx | 215/435 (49%) | low (median 1.6 vs 7.8 ft) | 2.0, 12 h | 109 / 273 | 0.76, never under 0.4 |
| Revere Beach | box | 147/435 (34%) | low (75% in low quarter) | 2.0, 12 h | 114 / 123 | 0.42, 46% under 0.4 |
| Hubbard Creek | mfr | 101/435 (23%) | low (100%) | 1.1, 24 h | 74 / 97 | no neighbours |
| Galveston Seawall | hgx | 85/435 (20%) | low (100%) | 0.9, 24 h | 27 / 27 | no neighbours |
| Bandon Beach | mfr | 62/435 (14%) | low (100%) | 0.9, 24 h | 46 / 47 | 0.35, 65% under 0.4 |
| Rincon | lox | 53/435 (12%) | low (100%) | 0.9, 25 h | 31 / 15 | 0.32, 71% under 0.4 |
| Tarpits (same cell as Rincon) | lox | 53/435 (12%) | low (100%) | 0.9, 25 h | 31 / 15 | 0.32, 71% under 0.4 |
| Point Lookout | okx | 33/435 (8%) | low (93%) | 1.3, 12.5 h | 58 / 11 | 0.51, 24% under 0.4 |
| Tokeland | sew | 22/435 (5%) | low (100%) | 0.4, 25 h | 26 / 6 | 0.47, 35% under 0.4 |
| Pompano Beach Pier | mfl | 1/435 | too few | | 10 / 2 (none since Oct 13) | 0.72, 5% under 0.4 |

- **Neighbour ratio:** from run 58 (`docs/face_validation/run-58/nwps_cells.json`), Sep 7 to Oct 29. It's the spot's stored NWPS Hs against the median of the NWPS spots within 30 km on the same office grid.
- **No buoy comparison** was made.

## Named spots

- **Rodanthe:** clean since #288.
  - No blank hour in any of the 3 cycles at its new cell (35.5911, -75.43524).
  - Its last filled hour was Oct 29 08Z; #288 merged Oct 30 12:15Z.
  - Before the move: 33 filled hours, in pairs about 12 hours apart (04–08Z and 16–20Z).
- **Moonstone Beach Humboldt:** no blank hour in any cycle and none filled since Oct 7.
  - Its problem is a low cell, not a drying one: run 58 found it at 0.26 of its neighbours, under 0.4 in 98% of hours.
- **Bandon Beach:** blank once a day, around 23Z to 01Z, for 1 to 5 hours, at the day's lower low tide (median 0.19 ft against 4.59 ft).
  - Same cause as Rodanthe's old cell (dry at low water), but once a day where Rodanthe's was twice: the Oregon coast has one much lower low tide a day.
  - It also reads low against its neighbours (0.35).
