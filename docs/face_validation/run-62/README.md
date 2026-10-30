# Run 62: node preview of the Hatteras sample-point move (the merge gate)

- **Run:** sw1-raycast run 62, mode node-preview, `baseline_ref` main, `spots` outer-banks-fishing-pier.
- **Commit:** `5b0da4d` (before the rebase onto main), dispatched 2026-10-30 11:37Z.
- **Artifact:** `node-preview-62`; `node_preview.json` and `node_preview_hours.csv` saved here byte for byte (the CSV gzipped).
- **What it read:** the published window from 2026-10-30 11:37Z, rated from the MHX cycle 2026-10-30 00Z. NOMADS listed 9 cycles; that is the only one the window was rated from, so it is the only one read.

## The gate: PASS

396 hours, 0 input differences (hs, tp, dp, swell_hs), 0 star differences, 0 face differences, 15 filled from a neighbouring hour (the hour blank in the file).

- **The 15 filled hours** are all Rodanthe's. Its old cell (35.5911, -75.45482) is blank in the file about twice a day.
- **How a blank hour is stored:** height, period and direction empty; swell height, stars and face from the hour before or after.
- **Runs 60 and 61 failed** on 11 of these hours. Their gate compared each row with the exact hour (run 60), then with the neighbour's values (run 61), not with the row as the pipeline stores it.
- **Not replayed:** 3 Rodanthe hours another rater (WW3) wrote.

## Old point, new point, and Outer Banks Fishing Pier (published), on the same hours

| Spot | Hours | Stars: old / new / pier | Face (ft): old / new / pier | NWPS Hs (m): old / new |
|---|---|---|---|---|
| Rodanthe | 130 | 0.60 / 2.00 / 1.87 | 0.62 / 4.43 / 4.19 | 0.18 / 1.27 |
| S-Turns | 133 | 0.64 / 1.96 / 1.88 | 0.70 / 4.47 / 4.21 | 0.20 / 1.28 |
| Salvo | 133 | 1.13 / 2.04 / 1.88 | 1.56 / 4.46 / 4.21 | 0.45 / 1.28 |

- **No star goes down.** Stars go up on every hour at Rodanthe and S-Turns; at Salvo on 128 hours, with 5 the same.
- **The new points read a little above the pier**, about 0.1 to 0.2 stars and 0.25 ft of face. That matches run 58's grid reading: the new cells held 1.09 to 1.10 of the pier's Hs.
- **The water-cell check passed** in the cycle read: each new point is a wet cell centre, and the cell the pipeline samples.
- **The homepage's "Best conditions right now" is unchanged:** no spot drops out or comes in.
