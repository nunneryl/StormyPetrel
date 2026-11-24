# Run 82: node preview of the scoreboard's sample-point review (sw1-raycast mode node-preview)

- **Run:** sw1-raycast run 82 (Actions run 45286162816), branch `claude/practical-dirac-bshfbr` at `51c3c36`, 2026-11-23 23:08Z, `baseline_ref` main. The job ended in failure because the gate failed; every failing hour is at the two spots held (below).
- **Artifact:** `node-preview-82`, zip sha256 `b91543819afa3e438ebda34134d360aeef0141879b80d1cfe28dd294d1a5b58d` (GitHub's digest). Both files are saved here.
- **Stored gzipped since 2026-11-12** (the rule for every saved file): `node_preview.json` and `node_preview_hours.csv` are kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte); the sha256 of each is in `../CHECKSUMS.sha256`.
- **Window:** the published forecasts from 2026-11-23T23:09Z, replayed from 2026-11-21T23:09Z because buoys were named. Cycles read: chs 2026112200, 2026112206, 2026112212, 2026112218, 2026112300, 2026112306, 2026112312; ilm 2026112200, 2026112206, 2026112212, 2026112218, 2026112300, 2026112306, 2026112312; mfr 2026112200, 2026112212, 2026112300, 2026112312; mhx 2026112200, 2026112212, 2026112300, 2026112312; mtr 2026112200, 2026112212, 2026112300, 2026112312; pqr 2026112114, 2026112202, 2026112214, 2026112302, 2026112314; sju 2026112200, 2026112206, 2026112212, 2026112218, 2026112300, 2026112306, 2026112312.
- **Moved in the run:** 21 spots. **Kept in this PR:** 13. **Held:** Dewees Island, Fripp Island, Hunting Island, Berts Bar, Isle of Palms, Windjammer (South Carolina: a whole-coast review instead) and Lovers Point, Monterey Beach (sheltered by the Monterey Peninsula, and calibrated). The eight held fail both bars in this run as well: the South Carolina six read 1.55 to 1.76 of their neighbours (Folly Beach Pier, Folly Beach Washout, Wild Dunes, Hilton Head Island) and 1.30 to 1.52 of buoys 41065 and 41067, with 9 to 21% of hours within 20%; Lovers Point and Monterey Beach read 1.14 of Moss Landing (1.27 of all three neighbours) and 1.30 of buoy 46236 (35% within 20%; 1.01 and 56% at the old cell).
- **Why these spots:** the first weekly accuracy report and spot_gap_query (7 days to 2026-11-23, lead day 1) found them reading far below NWPS at their own buoy.

## The gate

- **Whole run:** 3666 hours, 0 input, 208 star and 0 face differences, 95 filled from a neighbouring hour (the hour blank in the file). No double-posted cycle, no old node on land, every new node a wet cell and the cell nwps.fetch samples in every cycle read.
- **All 208 star differences are at Lovers Point (32) and Monterey Beach (176),** both held. Their inputs and faces match on every hour. The run's roster moved them, so their face factors (1.6732 and 2.1707) were suspended, as the log's first lines say; the old-node replay then rated without the calibration the published stars had. They are not in this PR.
- **On the 13 spots kept:** 2284 hours, 0 input, 0 star and 0 face differences.

## Per spot, old point -> new point

Face is face_ft_raw, mean over the replayed hours. Neighbours: the unmoved NWPS spots within 30 km whose own sample point is at least 400 m from the GSHHG shoreline and not on land (the pattern the review found: points within about 400 m of shore read low); the mean over all of them is beside it. Buoy: the median of node / buoy over the replayed hours already past, and the share within 20%.

| Spot | Hours | Stars | Face, ft | Neighbours (face), new (before) | All neighbours | Buoy: node / buoy, within 20% | Scoreboard gap, m |
|---|---|---|---|---|---|---|---|
| Brookings Beach | 162 | 2.48 -> 3.62 (up 115, down 7) | 4.40 -> 14.57 | none | – | 46027: 0.366 -> 0.956, 0% -> 64% | -2.07 |
| Carolina Beach | 131 | 1.35 -> 1.92 (up 84, down 9) | 1.87 -> 5.89 | 1.033 (0.329) | 1.522 | 41110: 0.393 -> 0.934, 0% -> 100% | -1.01 |
| Oregon Inlet | 181 | 2.11 -> 2.19 (up 47, down 19) | 4.34 -> 7.28 | 1.074 (0.640) | 1.119 | 44095: 0.506 -> 0.829, 4% -> 64% | -0.52 |
| Nye Beach | 183 | 3.38 -> 2.87 (up 8, down 89) | 9.81 -> 13.74 | 0.996 (0.710) | 0.996 | 46097: 0.702 -> 0.826, 24% -> 59% | -0.63 |
| Agate Beach Newport | 183 | 3.50 -> 3.50 (up 20, down 11) | 9.81 -> 13.78 | 0.998 (0.710) | 0.998 | 46097: 0.702 -> 0.837, 24% -> 61% | -0.63 |
| Indicators | 179 | 0.38 -> 1.10 (up 145, down 0) | 1.18 -> 2.87 | 0.932 (0.385) | 1.356 | 41115: 0.426 -> 0.973, 0% -> 64% | -0.54 |
| Rincon Domes | 179 | 0.38 -> 1.12 (up 152, down 0) | 1.18 -> 2.87 | 0.932 (0.385) | 1.356 | 41115: 0.426 -> 0.973, 0% -> 64% | -0.54 |
| Maria's | 181 | 0.21 -> 1.16 (up 180, down 0) | 1.04 -> 3.13 | 1.019 (0.337) | 1.481 | 41115: 0.264 -> 1.059, 0% -> 66% | -0.48 |
| Steps Beach Rincon | 181 | 0.21 -> 1.14 (up 175, down 0) | 1.04 -> 2.97 | 0.968 (0.337) | 1.407 | 41115: 0.264 -> 1.018, 0% -> 68% | -0.48 |
| Tres Palmas | 181 | 0.27 -> 1.01 (up 135, down 0) | 1.04 -> 2.79 | 0.908 (0.339) | 1.319 | 41115: 0.264 -> 0.959, 0% -> 64% | -0.48 |
| Table Tops Aguadilla | 181 | 0.92 -> 2.06 (up 181, down 0) | 0.92 -> 3.31 | 1.080 (0.299) | 1.569 | 41115: 0.193 -> 1.092, 0% -> 60% | -0.48 |
| Gas Chambers Aguadilla | 181 | 0.29 -> 1.68 (up 181, down 0) | 0.84 -> 3.32 | 1.082 (0.275) | 1.572 | 41115: 0.189 -> 1.092, 0% -> 60% | -0.53 |
| Crash Boat Beach | 181 | 0.27 -> 1.52 (up 181, down 0) | 0.85 -> 3.34 | 1.089 (0.276) | 1.583 | 41115: 0.189 -> 1.092, 0% -> 60% | -0.53 |

- **Neighbours used:** Carolina Beach: Kure Beach, Carolina Beach Pier (Fort Fisher 321 m and Wrightsville Beach 316 m from shore are left out). Oregon Inlet: Outer Banks Fishing Pier (North Pea Island 395 m and Bath House 170 m left out). Nye Beach, Agate Beach Newport: Otter Rock, South Beach Newport. Puerto Rico: Antonio's, Sandy Beach, Pools, Wilderness, Shacks Beach, Jobos Beach Isabela (Dogmans Rincon, whose own point is inside the GSHHG outline, and Surfers Beach Aguadilla, 391 m, left out). Brookings Beach: none within 30 km; judged on buoy 46027.
- **Against single neighbours,** the highest new ratios are the Aguadilla three against Antonio's (1.36 to 1.37, 15 km south in Rincon) and Maria's against Antonio's (1.28); every Puerto Rico spot reads 0.66 to 0.96 of the Isabela and Wilderness spots. Each spot's figure against every reference is in node_preview.json.
- **Nye Beach's stars fall** (89 hours down, 8 up) while its face rises from 9.8 to 13.7 ft; Agate Beach's do not. Nye Beach is a sand beach and Agate Beach's bottom is mixed, which is what the sand-break closeout ceiling would do (as at Westport in run 71); not checked hour by hour here.
- **Not replayed** (another rater wrote the hour): Carolina Beach 50 (ww3), Brookings Beach 17 (ww3) and 2 (buoy), Indicators and Rincon Domes 2 each (ww3).

## The surf height the page shows, and the sample point's depth

The rating sizes the face from the node's Hs with WAVEWATCH III's period factor, and the page's surf height at an uncalibrated spot carries that Hs to breaking from a fixed 10 m (forecast/surf_height.py); neither reads the cell's depth. Run 82 adds, for each hour, the surf height at the old and the new cell (as the page would show them) and the new cell's Hs carried to breaking from its own ETOPO depth.

| Spot | Cell depth, m | Surf shown, ft (old -> new) | From the cell's depth, ft | Ratio |
|---|---|---|---|---|
| Brookings Beach | 15 | 3.67 -> 9.67 | 10.27 | 1.062 |
| Carolina Beach | 13.3 | 1.89 -> 4.72 | 4.86 | 1.029 |
| Oregon Inlet | 18.2 | 3.74 -> 5.63 | 5.91 | 1.049 |
| Nye Beach | 22.6 | 7.04 -> 9.20 | 10.21 | 1.110 |
| Agate Beach Newport | 23.5 | 7.04 -> 9.22 | 10.27 | 1.114 |
| Indicators | 33.6 | 1.30 -> 2.66 | 2.75 | 1.033 |
| Rincon Domes | 33.6 | 1.30 -> 2.66 | 2.75 | 1.033 |
| Maria's | 344.2 | 1.17 -> 2.86 | 2.80 | 0.978 |
| Steps Beach Rincon | 639.6 | 1.17 -> 2.74 | 2.69 | 0.979 |
| Tres Palmas | 645.5 | 1.18 -> 2.60 | 2.56 | 0.982 |
| Table Tops Aguadilla | 440 | 1.05 -> 2.99 | 2.94 | 0.983 |
| Gas Chambers Aguadilla | 440 | 0.98 -> 3.00 | 2.95 | 0.985 |
| Crash Boat Beach | 440 | 0.99 -> 3.01 | 2.98 | 0.992 |

- **Over this window** the 10 m assumption reads the deep Puerto Rico cells 1 to 2% high (the week's periods were short to moderate) and the Oregon cells (23 m) about 11% low (long-period swell). Worked separately at fixed periods: from 440 m, -7% at 6 s, -1% at 10 s, +9% at 14 s, +18% at 18 s. Shoaling from each moved spot's recorded depth is on the backlog.

## Homepage, "Best conditions right now"

- **The 13 kept spots change nothing:** none is in the ten before or after. The only change in the run is Lovers Point moving up (held, so not in this PR).
