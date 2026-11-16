# Run 78: star preview of the facing fixes (sw1-raycast mode star-preview)

- **Run:** sw1-raycast run 78 (Actions run 43949068615), branch `claude/practical-dirac-bshfbr` at `2a502b6`, 2026-11-16 20:22Z. Passed. Spots files: ARCS = this branch (`2a502b6`), BASELINE = `origin/main` (`baff3c9`).
- **Artifact:** `star-preview-78`, zip sha256 `0824abce0fb067f55621866b5c15884019382d50ed50ace8c8558d2d282990c2` (GitHub's digest says the same).
- **Saved:** `star_preview.json` (the artifact's only file) and `run_log.txt` (the job log, timestamps cut); both are under 100 KB, so neither is gzipped (Stored gzipped since 2026-11-12 applies to files of 100 KB or more). The sha256 of every saved file is in `../CHECKSUMS.sha256`.
- **Compared:** the published window from 20:22:57Z (81,576 rows), re-rated with the wind multiplier and wind cap recomputed at main's facing and at this branch's: Hillsboro Inlet 134 -> 108, La Push First Beach 197 -> 240, Topanga Beach 151 -> 170.

## The gate

- BASELINE reproduced the stored wind_mult on every row (81,576; none "wind_mult NOT reproduced").
- It reproduced the published stars on 81,507 rows; **69 did not** (NWPS 68 of 75,003, WW3 1 of 6,573), each with the wind multiplier matching. The 20 the run lists are in California, Florida, Hawaii and Massachusetts, 19 of them a half-star step and one 1.0 -> 0.0 (Plum Island, a face at the 0.5 ft flat line). Not caused by the facing: these are main's own geometry against main's published stars. One is at a changed spot (La Push First Beach, 1 of 124 hours), so La Push is compared on 123 hours. The stored face (2 decimals) and multipliers (3 decimals) the replay reads are the likely cause, rows on a half-star edge; not verified.
- Rows at spots whose facing does not change that moved: **0**.
- The swell-window replay (stored wind_mult) changed nothing: 0 changed spot-hours.

## Star change at the three spots (all NWPS rows)

| Spot | Hours | Up | Down | Change (delta: hours) |
|---|---|---|---|---|
| Hillsboro Inlet | 124 | 4 | 13 | -0.5: 13, 0: 107, +0.5: 4 |
| La Push First Beach | 123 | 54 | 0 | 0: 69, +0.5: 32, +1.0: 8, +1.5: 13, +2.0: 1 |
| Topanga Beach | 130 | 2 | 6 | -0.5: 6, 0: 122, +0.5: 2 |

- **Hillsboro Inlet:** a northeast wind (50-60 deg, 17-20 kt) was cross-shore at 134 and is onshore at 108 (38 hours cross -> onshore): 2.5 -> 2.0 stars on 9 hours at <=20 kt, 4 more at <=13 kt.
- **La Push First Beach:** a southeast wind (131-147 deg) was onshore at 197 and is cross-offshore at 240 (26 hours onshore -> cross, 40 cross -> offshore); at 19-22 kt the cap moves from the onshore rung to the offshore one: 1.5 -> 3.0 and 2.0 -> 4.0 on 17 Nov.
- **Topanga Beach:** small and both ways (14 hours offshore -> cross, 12 cross -> onshore, 7 cross -> offshore).

By wind band and the wind-class moves: `run_log.txt` or `star_preview.json` `facing.per_spot`.
