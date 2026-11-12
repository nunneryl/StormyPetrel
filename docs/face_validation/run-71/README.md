# Run 71: node preview of the six NWPS v1.5 moves, with the land-node rule (sw1-raycast mode node-preview)

- **Run:** sw1-raycast run 71 (Actions run 43138697054), branch `claude/practical-dirac-bshfbr-nodes` at `9fc0652`, 2026-11-12 02:03Z, `baseline_ref` main.
- **Artifact:** `node-preview-71`, zip sha256 `c4e18af59f03a4960d46ee029d49c325a793e095390088d879c3761921cf959c`. `node_preview.json` is saved here byte for byte (sha256 `790b43c98d23182a2bb8018316b66938669525f442c65bb88856836ec4b8f27c`); `node_preview_hours.csv` is gzipped.
- **Moved spots:** Pacifica Linda Mar and Rockaway Beach Pacifica (one shared node), Avila Beach (mtr); Hobuck Beach, Westport Jetty and Westport Light State Park (sew).
- **Window:** the published forecasts from 02:04Z, replayed from 48 hours earlier (2026-11-10 02:04Z) because buoys were named. Cycles read: mtr and sew 11-10 00Z, 11-10 12Z, 11-11 00Z, 11-11 12Z.
- **References:** neighbours Sharp Park, Half Moon Bay Jetty, Half Moon Bay, Cable Landing, Morro Rock, Shi Shi Beach, Ocean Shores, Tokeland; buoys 46237 (Pacifica Linda Mar), 46215 (Avila Beach), 46087 (Hobuck Beach), 46211 (Westport Jetty).

## The gate: PASS

- **On the spots it covers** (Hobuck Beach, Westport Jetty, Westport Light State Park): 534 hours, 0 input, 0 star and 0 face differences, 0 filled from a neighbouring hour.
- **Cell choice:** every new node is a wet cell, and the cell `nwps.fetch` samples, in all 4 cycles read.
- **Double-posted cycles:** none. The publication-logger log was given; every file read had the size NOMADS's HEAD gave.

### Excluded by the land-node rule (old node is land in every cycle read)

| Spot | Evidence | Hours (4 cycles) | Differences: input / star / face |
|---|---|---|---|
| Pacifica Linda Mar | node (37.61754, -122.5) is cell (116, 120), centre (37.61742, -122.5), 0.013 km away; no swh or shts at any of 145 steps; replay read cell (116, 119), centre (37.61742, -122.5275), 2.422 km away | 9 + 12 + 12 + 145 = 178 | 178 / 28 / 178 |
| Rockaway Beach Pacifica | same node and cell as Pacifica Linda Mar | 178 | 0 / 0 / 0 |
| Avila Beach | node (35.15796, -120.74) is cell (7, 184), centre (35.15795, -120.74), 0.001 km away; no swh or shts at any of 145 steps; replay read cell (6, 183), centre (35.13538, -120.7675), 3.543 km away | 178 | 0 / 0 / 0 |

- **Rockaway Beach Pacifica and Avila Beach match exactly** because the cell the replay falls back to is the cell `nwps.fetch`'s seaward walk already reaches for them, and it is their proposed node. Their published hours already come from the proposed cell, so the move changes nothing for them.
- **Pacifica Linda Mar's walk reaches a different cell,** so its published hours differ from the replay. Its "old" figures below are therefore the published ones.

## Against each spot's own references and buoy

Face is face_ft_raw, mean over the same 178 hours; ratio is the spot's mean face over the reference's.

| Spot | Face, ft: published → new | Stars: published → new | New / each neighbour (face) | Buoy, past 48 h (median node / buoy) |
|---|---|---|---|---|
| Pacifica Linda Mar | 13.70 → 13.35 | 2.74 → 2.82 | Sharp Park 1.254, Half Moon Bay Jetty 1.360, Half Moon Bay 1.356 (mean 1.323; published 1.359) | 46237, 2.23 m: 0.996 → 0.974 (96% → 94% within 20%) |
| Rockaway Beach Pacifica | 13.35 → 13.35 | 4.13 → 4.13 | the same: mean 1.323, unchanged | |
| Avila Beach | 11.74 → 11.74 | 3.43 → 3.43 | Cable Landing 0.938, Morro Rock 0.938 | 46215, 1.74 m: 1.205 → 1.205 (49% within 20%) |
| Hobuck Beach | 14.94 → 14.78 | 2.26 → 2.31 | Shi Shi Beach 0.904 → 0.894 | 46087: 0 paired hours |
| Westport Jetty | 9.90 → 13.00 | 3.05 → 2.55 | Ocean Shores 0.673 → 0.884; Tokeland 2.614 → 3.434 | 46211, 3.63 m: 0.678 → 0.873 (9% → 81% within 20%) |
| Westport Light State Park | 9.90 → 13.57 | 2.94 → 2.34 | Ocean Shores 0.673 → 0.923; Tokeland 2.614 → 3.584 | (46211, as Westport Jetty) |

- **Over 1.15 of their neighbours: the Pacifica pair,** 1.25 to 1.36 of Sharp Park and both Half Moon Bay spots. They are over today as published (Pacifica Linda Mar 1.359, Rockaway Beach Pacifica 1.323); the move lowers Pacifica Linda Mar slightly and leaves Rockaway unchanged. Against buoy 46237 the cell reads 0.974 of the buoy, 94% of hours within 20%.
- **Westport pair against Tokeland,** 3.4 to 3.6 (2.6 today): Tokeland sits inside the Willapa Bay mouth and reads 0.29 to 0.44 of its neighbours and of the next cell seaward (the v1.5 cell scan). Against Ocean Shores, the open-coast neighbour, both read under 1.
- **Westport's stars drop** by 0.5 to 0.6 on average (Jetty: 99 hours down, 2 up; Light: 97 down, 2 up) while the face rises by 3 to 3.7 ft.
- **Hobuck Beach and buoy 46087:** no buoy reading was paired. NDBC's realtime file for 46087 carries wave heights (3.0 m at 2026-11-12 01:20Z); why none were paired was not checked.

## Homepage, "Best conditions right now"

- **Unchanged:** the same ten before and after: Surf Beach Vandenberg, Trinidad State Beach, Cable Landing, Piedras Blancas, Jug Handle, Mackerricher, Ten Mile Beach, Westport (California) and Agate Beach at 5.0 stars; Morro Rock at 4.5. None drops out and none comes in.
- Replayed soonest hours: Avila Beach, Hobuck Beach, Rockaway Beach Pacifica, Westport Jetty, Westport Light State Park.
