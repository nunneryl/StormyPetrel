# Run 71: node preview of the six NWPS v1.5 moves, with the land-node rule (sw1-raycast mode node-preview)

- **Run:** sw1-raycast run 71 (Actions run 43138697054), branch `claude/practical-dirac-bshfbr-nodes` at `9fc0652`, 2026-11-12 02:03Z, `baseline_ref` main.
- **Artifact:** `node-preview-71`, zip sha256 `c4e18af59f03a4960d46ee029d49c325a793e095390088d879c3761921cf959c`. `node_preview.json` is saved here byte for byte (sha256 `790b43c98d23182a2bb8018316b66938669525f442c65bb88856836ec4b8f27c`); `node_preview_hours.csv` is gzipped.
- **Stored gzipped since 2026-11-12:** `node_preview.json` is kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte; the sha256 of each is in `../CHECKSUMS.sha256`). Nothing else changed.
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
| Hobuck Beach | 14.94 → 14.78 | 2.26 → 2.31 | Shi Shi Beach 0.904 → 0.894 | 46087, 3.24 m: 1.050 → 0.999 (77% → 83% within 20%), read afterwards from NDBC (see below) |
| Westport Jetty | 9.90 → 13.00 | 3.05 → 2.55 | Ocean Shores 0.673 → 0.884; Tokeland 2.614 → 3.434 | 46211, 3.63 m: 0.678 → 0.873 (9% → 81% within 20%) |
| Westport Light State Park | 9.90 → 13.57 | 2.94 → 2.34 | Ocean Shores 0.673 → 0.923; Tokeland 2.614 → 3.584 | (46211, as Westport Jetty) |

- **Over 1.15 of their neighbours: the Pacifica pair,** 1.25 to 1.36 of Sharp Park and both Half Moon Bay spots. They are over today as published (Pacifica Linda Mar 1.359, Rockaway Beach Pacifica 1.323); the move lowers Pacifica Linda Mar slightly and leaves Rockaway unchanged. Against buoy 46237 the cell reads 0.974 of the buoy, 94% of hours within 20%.
- **Decision (2026-11-12): the Pacifica pair is accepted as an exception to the 1.15 limit.** Half Moon Bay and Half Moon Bay Jetty sit behind Pillar Point; the pair already reads 1.32 to 1.36 of them as published; the move does not raise it; and the cell reads 0.97 of 46237 with 94% of hours within 20%. The reason is written into both overrides in `pipeline/data/nwps_node_overrides.json`.
- **Westport pair against Tokeland,** 3.4 to 3.6 (2.6 today): Tokeland sits inside the Willapa Bay mouth and reads 0.29 to 0.44 of its neighbours and of the next cell seaward (the v1.5 cell scan). Against Ocean Shores, the open-coast neighbour, both read under 1.
- **Westport's stars drop** by 0.5 to 0.6 on average (Jetty: 99 hours down, 2 up; Light: 97 down, 2 up) while the face rises by 3 to 3.7 ft. Why: below.

## Why Westport's stars drop: the sand-break closeout ceiling

- **Both spots are sand breaks** (Westport Jetty a jetty, Westport Light State Park a beach), so `composite_stars` applies the closeout ceiling: the size credit is `size_score` x `closeout_factor` of the effective face, 1.0 up to 8 ft, 0.9 at 10 ft, 0.7 at 12, 0.5 at 15.
- **How it was checked.** The artifact keeps each hour's heights, chop and wind but not its direction gain or tide factor, so those two were read from the spot pages' published rows (stormypetrel.surf, 2026-11-12, NWPS cycle 11-12 00Z) for the same valid hour, and every factor of the old and the new rating was recomputed with the pipeline's own functions. Only hours where that reproduces BOTH run 71's published stars and its new stars exactly are counted: 123 of 178 for Westport Jetty, 124 of 178 for Westport Light.
- **On every hour that drops, the ceiling is lower at the new cell:** 82 of 82 for Westport Jetty and 85 of 85 for Westport Light. Mean on those hours, old → new:

| Spot | Effective face, ft | Size score | Closeout factor | Wind | Chop | Wind star cap |
|---|---|---|---|---|---|---|
| Westport Jetty | 10.3 → 13.4 | 4.91 → 5.00 | 0.855 → 0.613 | 0.820 → 0.818 | 1.000 → 0.999 | 4.53 → 4.34 |
| Westport Light State Park | 10.4 → 14.3 | 4.91 → 5.00 | 0.844 → 0.564 | 0.812 → 0.810 | 1.000 → 0.999 | 4.50 → 4.46 |

- **Wind, chop and the strong-wind cap also move on a few hours** (Jetty: wind 12, chop 5, cap 11 of 82; Light: wind 10, chop 7, cap 5 of 85), always beside the ceiling, never alone. Direction gain, tide and period are the same at both cells by construction.
- **Representative hours** (old → new):

| Spot, hour | Hs, m | Effective face, ft | Size | Ceiling | Wind | Chop | Cap | Stars |
|---|---|---|---|---|---|---|---|---|
| Jetty, 11-12 05Z (13.6 s, 8.1 m/s from 195) | 1.93 → 2.83 | 7.5 → 11.1 | 4.39 → 5.00 | 1.00 → 0.79 | 0.80 → 0.80 | 1.00 → 1.00 | 5 → 5 | 4.0 → 3.5 |
| Jetty, 11-16 17Z (16.3 s, 6.5 m/s from 178) | 2.81 → 3.62 | 11.3 → 14.6 | 5.00 → 5.00 | 0.77 → 0.53 | 0.80 → 0.80 | 1.00 → 1.00 | 5 → 5 | 3.5 → 2.5 |
| Jetty, 11-14 06Z (18.3 s, 9.6 m/s from 149) | 2.29 → 3.24 | 9.2 → 13.0 | 4.80 → 5.00 | 0.94 → 0.64 | 0.80 → 0.80 | 1.00 → 1.00 | 5 → 3 | 4.0 → 3.0 |
| Light, 11-14 06Z (18.3 s, 9.6 m/s from 149) | 2.29 → 3.67 | 9.3 → 14.8 | 4.82 → 5.00 | 0.93 → 0.51 | 0.80 → 0.80 | 1.00 → 1.00 | 5 → 5 | 4.0 → 2.5 |
| Light, 11-16 16Z (16.6 s, 6.5 m/s from 178) | 2.87 → 4.08 | 11.7 → 16.6 | 5.00 → 5.00 | 0.73 → 0.44 | 0.80 → 0.80 | 1.00 → 1.00 | 5 → 5 | 3.5 → 2.0 |

- **Verdict: the rating doing its job.** The old point sat in the jetty's lee (0.72 of the next cell seaward, 0.678 of buoy 46211 with 9% of hours within 20%); the new cell reads 0.873 of 46211 with 81% within 20%, so the larger face is the better estimate, and an 11 to 17 ft face on a sand bar is a closeout, which is what the ceiling models.

## Why Hobuck Beach had no buoy check, and the fix

- **Cause:** node-preview read buoy readings only from `buoy_observations`, and the pipeline stores readings only for buoys some spot is assigned (`nearest_buoy_id` or `fallback_buoy_ids`; `forecast/buoys._unique_buoy_ids`). 46087 is no spot's buoy: Hobuck Beach has none (buoy-assign run 64: no candidate in line of sight, and 46087 was not in NDBC's wave-station list then), so `buoy_observations` holds no 46087 rows and every hour went unpaired. NDBC's file for 46087 does carry wave heights.
- **Fix (node-preview only):** when `buoy_observations` has no readings for a buoy, node-preview reads NDBC's realtime2 file for it with the pipeline's own parser, and the report names where the readings came from.
- **Run 71's hours with 46087 from NDBC** (read 2026-11-12, the same code path): 48 paired hours, buoy mean 3.24 m; old point 1.050 of the buoy (77% within 20%), new cell 0.999 (83% within 20%). The same path gives 46211 and 46237 within a reading of run 71's own figures (Westport Jetty 0.869 against 0.873; Pacifica Linda Mar 0.974 both).

## Homepage, "Best conditions right now"

- **Unchanged:** the same ten before and after: Surf Beach Vandenberg, Trinidad State Beach, Cable Landing, Piedras Blancas, Jug Handle, Mackerricher, Ten Mile Beach, Westport (California) and Agate Beach at 5.0 stars; Morro Rock at 4.5. None drops out and none comes in.
- Replayed soonest hours: Avila Beach, Hobuck Beach, Rockaway Beach Pacifica, Westport Jetty, Westport Light State Park.
