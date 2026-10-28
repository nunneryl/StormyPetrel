# The shown swell train: merge gate, runs 55 to 57

The period and direction the site shows, and the period the surf height is worked out at, are
the wind sea's on some hours rated on a WW3 swell partition. PR #284 shipped this; it was merged
as d18f4ead4f77f3387fdad838e1f2b18f63e471c0 on 2026-10-28. Stars are unchanged: they stay on the
rated partition. The rule is in `pipeline/forecast/shown_period.py` and
`frontend/lib/shownPeriod.ts`, held to `frontend/lib/shownPeriod.cases.json`.

The rule, as shipped. On an hour whose swell_source is `ww3` or `nwps_height_ww3_dir`, the wind
sea is shown when either:

- **bigger-and-longer** ("LONGER"): its Hs is above the rated partition's and its period is at
  least 0.5 s longer; or
- **dominant**: its Hs is at least 4 times the rated partition's (K = 4), whatever its period.
  Here the shown period can go down.

Neither applies when the wind sea comes from 90° or more outside the spot's swell window (the
rating's blocked rung).

Each run is sw1-raycast in mode `period-preview` on `claude/practical-dirac-bshfbr-swell-period`.
The rule changed between runs:

| Run | Commit | Rule tested | Started (UTC) | Archive | Artifact sha256 |
|---|---|---|---|---|---|
| 55 | 243e438d71cf71d22152fdaecafa43a2f91ba8c7 | bigger and longer by any margin | 2026-10-28 13:38 | 7 Sep to 28 Oct 13:00Z | 82d0fcf8cc8c44cd7dabcc1ab62a20ebcd24c2b3505766dc7455558386ae7134 |
| 56 | e87ba2379084037b9eb10c68696e6a83518ef496 | 0.5 s margin, plus dominant at K = 4 | 2026-10-28 16:56 | 7 Sep to 28 Oct 16:00Z | 9c2a8765212f255064feaada90b9875daa2156bf53def35429b44a7ba2ffaa41 |
| 57 | 40fef5623d2e283ea4d68c25b6887e26122196ab | as 56, plus the 90° window exclusion (shipped) | 2026-10-28 17:44 | 7 Sep to 28 Oct 17:00Z | d988921a89d30f379402ca6e41d460edca29b8c453fec085684c20ed6e138d36 |

- The artifacts are `period-preview-55`, `-56` and `-57`. They expire on 2027-01-26.
- The only error in each run was the NWS API's 404 for zone CAZ364, as in runs 52 to 54.

## Files

In `run-55/`, `run-56/` and `run-57/`:

- `period_preview.json` and `face_compare.json`: the artifact's JSON, byte for byte.
- `period_preview_hours.csv.gz`: the published window's switched hours, each with its period,
  direction and surf height before and after.

In `run-57/` only:

- `period_preview_archive.csv.gz`: the archive's 59,380 switched hours at K = 4, each with both
  trains, the window, the surf height before and after, and the nearest buoy.

Each `.csv.gz` decompresses to the artifact's file byte for byte. The archive CSVs of runs 55 and
56 are left out; their figures are in each run's `period_preview.json`.

## How to read the figures

- **Within 2 s of the buoy.** This compares the shown period with the nearest buoy's dominant
  period (DPD), observed within 30 minutes of the hour. The buoy's DPD follows the bigger train by
  definition, so this checks consistency; it doesn't prove the result. A buoy offshore also sees
  a wind sea that may never reach a sheltered beach.
- **Surf / buoy Hs.** The surf height over the buoy's Hs, median.
- **NWS figures.** These are inside / below / above the NWS Surf Zone Forecast's daytime range,
  in percent of hours: mainland headline hours, 7 Sep to 27 Oct 18:00Z.
  - "#283" is the surf height at the rated period, as #283 shipped it.
  - "Full rule" is the height #284 ships.
  - The NWS ranges are forecasts, not measurements.
- **Archive coverage.** The archive keeps the last write of each hour only.

## The gate: PASS in all three runs

Every published row was taken back to before the surf step and replayed through
`apply_surf_height`.

| Check | Run 55 | Run 56 | Run 57 |
|---|---|---|---|
| Published hours replayed | 84,658 | 84,184 | 83,538 |
| Star differences | 0 | 0 | 0 |
| Other-field differences | 0 | 0 | 0 |
| Unexpected writes | 0 | 0 | 0 |
| Surf rows not switched, identical to #283 | 57,274 (0 mismatches) | 53,661 (0) | 53,471 (0) |
| Surf rows switched (gain `:windsea`) | 2,492 | 5,767 | 5,501 |
| ... of which the height changed | 1,887 | 5,758 | 5,492 |
| Badge flips | 0 | 0 | 0 |
| Badges, calibrated / model | 18,471 / 66,187 | 18,438 / 65,746 | 18,297 / 65,241 |
| Run 54's NWS table reproduced | yes | yes | yes |

- **Run 54's table reproduced:** 235,014 hours against run 54's 234,897, every region's shares
  within 0.001.
- **The 141 calibrated spots** kept their faces in every run.
- **Hours kept because another rater fed them:** 6,421, 6,318 and 6,269.
- **Why the rule changed after run 55.**
  - 605 of its 2,492 switched surf rows kept the same height: the wind sea was only a few
    hundredths of a second longer. That led to the 0.5 s margin.
  - 204,341 archive hours had a small long-period scrap rated over a bigger, shorter wind sea.
    That led to the dominant case.

## The dominance ratio: K = 3, 4 and 6

Archive switched hours. Run 56 is without the window exclusion; run 57 is with it.

| K | Run | Switched | Bigger-and-longer | Dominant | Within 2 s of buoy DPD | Surf height, median ft | Surf / buoy Hs |
|---|---|---|---|---|---|---|---|
| 3 | 56 | 84,876 | 6,375 | 78,501 | 28.9% → 82.2% | 3.13 → 2.73 | 0.7 → 0.61 |
| 3 | 57 | 81,166 | 6,337 | 74,829 | 28.3% → 83.4% | 3.13 → 2.73 | 0.7 → 0.61 |
| 4 | 56 | 61,779 | 6,375 | 55,404 | 28.4% → 84.2% | 3.31 → 2.92 | 0.7 → 0.62 |
| 4 | 57 | 59,380 | 6,337 | 53,043 | 27.9% → 85.2% | 3.32 → 2.93 | 0.7 → 0.62 |
| 6 | 56 | 39,643 | 6,375 | 33,268 | 29.7% → 84.7% | 3.57 → 3.23 | 0.69 → 0.63 |
| 6 | 57 | 38,361 | 6,337 | 32,024 | 29.4% → 85.4% | 3.58 → 3.24 | 0.69 → 0.63 |

California and Hawaii, the states where K mattered most:

| K | Run | California: switched, within 2 s | Hawaii: switched, within 2 s |
|---|---|---|---|
| 3 | 56 | 7,254, 17.7% → 73.6% | 10,486, 41.6% → 66.1% |
| 3 | 57 | 7,207, 17.6% → 73.8% | 8,762, 41.1% → 74.3% |
| 4 | 56 | 3,651, 13.8% → 81.5% | 6,706, 39.9% → 76.3% |
| 4 | 57 | 3,631, 14.0% → 81.6% | 5,631, 39.6% → 82.3% |
| 6 | 56 | 946, 1.3% → 93.9% | 2,472, 44.1% → 86.8% |
| 6 | 57 | 946, 1.3% → 93.9% | 1,988, 43.0% → 92.2% |

- **K = 4 was chosen.** Hours that switch at K = 4 but not at K = 6 agreed with the buoy far
  better after switching: 25% → 83% within 2 s in run 56.
- **K = 3 was not.** The extra hours it adds barely improved in Hawaii (45% → 48%, run 56).

## The 90° window exclusion (run 57)

The exclusion removed 2,423 archive hours at K = 4: 43 bigger-and-longer and 2,380 dominant. The
table gives them as they would have been shown if switched.

| State | Removed | Bigger-and-longer | Dominant | Buoy hours | Within 2 s of buoy DPD, if switched | Surf height if switched, median ft |
|---|---|---|---|---|---|---|
| California | 20 | 0 | 20 | 20 | 0.0% → 75.0% | 2.65 → 2.45 |
| Florida | 549 | 0 | 549 | 84 | 7.1% → 95.2% | 0.87 → 0.63 |
| Hawaii | 1,075 | 40 | 1,035 | 179 | 41.3% → 40.8% | 4.76 → 4.28 |
| Massachusetts | 275 | 1 | 274 | 211 | 34.6% → 56.4% | 1.45 → 1.13 |
| New Jersey | 362 | 0 | 362 | 359 | 71.0% → 23.1% | 2.25 → 1.53 |
| New York | 7 | 0 | 7 | 6 | 50.0% → 66.7% | 2.19 → 1.51 |
| North Carolina | 20 | 0 | 20 | 20 | 30.0% → 100.0% | 1.81 → 1.62 |
| Puerto Rico | 69 | 2 | 67 | 0 | — | 0.85 → 0.67 |
| Rhode Island | 46 | 0 | 46 | 35 | 31.4% → 57.1% | 1.89 → 1.2 |
| **All** | 2,423 | 43 | 2,380 | 914 | 46.8% → 45.3% | 2.65 → 1.91 |

- **No overall gain against the buoys.** Within 2 s of the buoy went from 46.8% to 45.3%.
  - It removed clearly wrong switches in New Jersey (71% → 23%).
  - In Florida, Massachusetts and Rhode Island the buoy would have agreed with the wind sea.
  - The buoy can't show whether a wind sea reaches a beach behind the coast, so the exclusion
    rests on the window geometry and the rating's own blocked rung.
- **Distance outside the window**, switched hours at K = 4 with the exclusion (from
  `run-57/period_preview_archive.csv.gz`):

  | Wind sea's distance outside the window | Hours | Buoy hours | Within 2 s of buoy DPD |
  |---|---|---|---|
  | Inside the window | 40,959 | 26,896 | 28.3% → 87.2% |
  | Under 45° | 12,893 | 5,717 | 26.4% → 80.3% |
  | 45° to 90° | 5,521 | 2,083 | 27.1% → 72.3% |
  | 90° or more | 0 | 0 | — |
  | Spot has no window | 7 | 7 | 0% → 100% |

  The 90°-and-up band is zero at K = 3, 4 and 6.

## Switched hours at K = 4, as shipped (run 57)

Archive, by state:

| State | Switched | Bigger-and-longer | Dominant | Blocked | Buoy hours | Within 2 s of buoy DPD | Surf height, median ft | Surf / buoy Hs |
|---|---|---|---|---|---|---|---|---|
| California | 3,631 | 15 | 3,616 | 20 | 1,404 | 14.0% → 81.6% | 2.67 → 2.3 | 0.59 → 0.53 |
| Delaware | 1,228 | 115 | 1,113 | 0 | 606 | 32.3% → 93.9% | 3.88 → 3.5 | 0.83 → 0.73 |
| Florida | 11,160 | 1,990 | 9,170 | 549 | 5,547 | 26.3% → 82.5% | 2.91 → 2.48 | 0.87 → 0.78 |
| Georgia | 935 | 202 | 733 | 0 | 909 | 11.8% → 78.4% | 3.35 → 2.91 | 0.67 → 0.6 |
| Hawaii | 5,631 | 555 | 5,076 | 1,075 | 1,067 | 39.6% → 82.3% | 5.52 → 5.18 | 0.85 → 0.81 |
| Maine | 991 | 8 | 983 | 0 | 782 | 48.0% → 97.6% | 3.65 → 3.56 | 0.53 → 0.47 |
| Maryland | 364 | 61 | 303 | 0 | 0 | — | 4.33 → 4.2 | — |
| Massachusetts | 2,836 | 116 | 2,720 | 275 | 1,418 | 33.7% → 84.4% | 3.25 → 2.87 | 0.82 → 0.73 |
| New Hampshire | 600 | 0 | 600 | 0 | 450 | 52.0% → 98.7% | 4.27 → 4.21 | 0.54 → 0.49 |
| New Jersey | 13,175 | 1,313 | 11,862 | 362 | 10,436 | 32.0% → 82.5% | 3.7 → 3.39 | 0.64 → 0.58 |
| New York | 3,995 | 457 | 3,538 | 7 | 2,654 | 28.6% → 90.2% | 2.95 → 2.58 | 0.6 → 0.52 |
| North Carolina | 5,183 | 749 | 4,434 | 20 | 3,810 | 15.7% → 89.1% | 3.02 → 2.62 | 0.67 → 0.61 |
| Oregon | 420 | 6 | 414 | 0 | 30 | 10.0% → 93.3% | 4.1 → 3.76 | 0.58 → 0.56 |
| Puerto Rico | 822 | 56 | 766 | 69 | 58 | 22.4% → 100.0% | 2.44 → 1.99 | 1.08 → 0.94 |
| Rhode Island | 2,399 | 8 | 2,391 | 46 | 1,875 | 22.2% → 88.4% | 2.3 → 1.65 | 0.86 → 0.64 |
| South Carolina | 2,803 | 204 | 2,599 | 0 | 2,229 | 25.8% → 81.7% | 3.05 → 2.69 | 0.74 → 0.66 |
| Texas | 2,381 | 324 | 2,057 | 0 | 747 | 52.9% → 99.6% | 1.98 → 1.76 | 0.59 → 0.53 |
| Virginia | 567 | 143 | 424 | 0 | 567 | 18.5% → 82.7% | 3.3 → 3.06 | 0.73 → 0.67 |
| Washington | 259 | 15 | 244 | 0 | 114 | 3.5% → 87.7% | 7.02 → 6.72 | 0.86 → 0.82 |
| **All** | 59,380 | 6,337 | 53,043 | 2,423 | 34,703 | 27.9% → 85.2% | 3.32 → 2.93 | 0.7 → 0.62 |

- **Published window:** 5,509 switched hours (5,501 NWPS, 8 WW3): 1,015 bigger-and-longer and
  4,494 dominant. The case comes from the period change: a switched hour is bigger-and-longer
  exactly when the wind sea is at least 0.5 s longer.
  - Period tile, median: 11.2 → 7.06 s.
  - Surf height, median: 3.29 → 3.13 ft.

## Against the NWS, by region and case (run 57)

Switched hours that have an NWS range. Each cell is inside / below / above, in percent. "Variant"
was a comparison only and was not shipped: in it the dominant case switches the shown period
and direction but keeps the surf height at the rated period.

| Region | Case | Hours | #283 (rated period) | Full rule | Variant | Moves under the full rule |
|---|---|---|---|---|---|---|
| **All** | bigger-and-longer | 2,026 | 60.8 / 32.7 / 6.5 | 69.5 / 23.0 / 7.5 | 69.5 / 23.0 / 7.5 | below → inside 196, inside → above 19 |
| **All** | dominant | 14,037 | 66.3 / 24.9 / 8.8 | 61.3 / 35.0 / 3.7 | 66.3 / 24.9 / 8.8 | above → inside 712, below → inside 1, inside → below 1,415 |
| Carolinas and Georgia | bigger-and-longer | 413 | 73.8 / 13.3 / 12.8 | 75.8 / 11.1 / 13.1 | 75.8 / 11.1 / 13.1 | below → inside 9, inside → above 1 |
| Carolinas and Georgia | dominant | 2,671 | 81.2 / 1.0 / 17.7 | 89.6 / 2.4 / 8.1 | 81.2 / 1.0 / 17.7 | above → inside 258, inside → below 35 |
| Florida Atlantic | bigger-and-longer | 823 | 52.0 / 39.4 / 8.6 | 67.8 / 21.6 / 10.6 | 67.8 / 21.6 / 10.6 | below → inside 146, inside → above 16 |
| Florida Atlantic | dominant | 1,970 | 76.6 / 12.5 / 10.9 | 77.0 / 20.0 / 3.0 | 76.6 / 12.5 / 10.9 | above → inside 156, inside → below 148 |
| Gulf | bigger-and-longer | 153 | 62.7 / 35.9 / 1.3 | 73.9 / 24.8 / 1.3 | 73.9 / 24.8 / 1.3 | below → inside 17 |
| Gulf | dominant | 1,504 | 71.3 / 24.6 / 4.1 | 63.3 / 34.4 / 2.3 | 71.3 / 24.6 / 4.1 | above → inside 28, below → inside 1, inside → below 149 |
| Mid-Atlantic | bigger-and-longer | 542 | 73.2 / 25.6 / 1.1 | 77.3 / 21.2 / 1.5 | 77.3 / 21.2 / 1.5 | below → inside 24, inside → above 2 |
| Mid-Atlantic | dominant | 3,996 | 76.1 / 12.8 / 11.1 | 75.8 / 19.1 / 5.1 | 76.1 / 12.8 / 11.1 | above → inside 238, inside → below 250 |
| New England | bigger-and-longer | 53 | 11.3 / 88.7 / 0.0 | 11.3 / 88.7 / 0.0 | 11.3 / 88.7 / 0.0 | none |
| New England | dominant | 3,114 | 42.7 / 56.5 / 0.8 | 18.7 / 81.1 / 0.2 | 42.7 / 56.5 / 0.8 | above → inside 20, inside → below 767 |
| Northern California | dominant | 98 | 21.4 / 78.6 / 0.0 | 19.4 / 80.6 / 0.0 | 21.4 / 78.6 / 0.0 | inside → below 2 |
| Pacific Northwest | bigger-and-longer | 11 | 0.0 / 100.0 / 0.0 | 0.0 / 100.0 / 0.0 | 0.0 / 100.0 / 0.0 | none |
| Pacific Northwest | dominant | 288 | 13.9 / 86.1 / 0.0 | 10.8 / 89.2 / 0.0 | 13.9 / 86.1 / 0.0 | inside → below 9 |
| Puerto Rico | bigger-and-longer | 31 | 0.0 / 100.0 / 0.0 | 0.0 / 100.0 / 0.0 | 0.0 / 100.0 / 0.0 | none |
| Puerto Rico | dominant | 304 | 20.4 / 79.6 / 0.0 | 4.6 / 95.4 / 0.0 | 20.4 / 79.6 / 0.0 | inside → below 48 |
| Southern California | dominant | 92 | 70.7 / 13.0 / 16.3 | 76.1 / 20.7 / 3.3 | 70.7 / 13.0 / 16.3 | above → inside 12, inside → below 7 |

All cases together, with the hours that moved between inside, below and above under the full
rule:

| Region | Hours | #283 | Full rule | Variant | inside → below | above → inside | inside → above | below → inside |
|---|---|---|---|---|---|---|---|---|
| **All** | 16,063 | 65.6 / 25.9 / 8.5 | 62.3 / 33.5 / 4.2 | 66.7 / 24.7 / 8.6 | 1,415 | 712 | 19 | 197 |
| Carolinas and Georgia | 3,084 | 80.2 / 2.7 / 17.1 | 87.7 / 3.5 / 8.8 | 80.5 / 2.4 / 17.1 | 35 | 258 | 1 | 9 |
| Florida Atlantic | 2,793 | 69.4 / 20.4 / 10.2 | 74.3 / 20.5 / 5.2 | 74.0 / 15.2 / 10.8 | 148 | 156 | 16 | 146 |
| Gulf | 1,657 | 70.5 / 25.6 / 3.9 | 64.3 / 33.6 / 2.2 | 71.5 / 24.6 / 3.9 | 149 | 28 | 0 | 18 |
| Mid-Atlantic | 4,538 | 75.7 / 14.4 / 9.9 | 76.0 / 19.3 / 4.7 | 76.2 / 13.8 / 9.9 | 250 | 238 | 2 | 24 |
| New England | 3,167 | 42.2 / 57.0 / 0.8 | 18.6 / 81.2 / 0.2 | 42.2 / 57.0 / 0.8 | 767 | 20 | 0 | 0 |
| Northern California | 98 | 21.4 / 78.6 / 0.0 | 19.4 / 80.6 / 0.0 | 21.4 / 78.6 / 0.0 | 2 | 0 | 0 | 0 |
| Pacific Northwest | 299 | 13.4 / 86.6 / 0.0 | 10.4 / 89.6 / 0.0 | 13.4 / 86.6 / 0.0 | 9 | 0 | 0 | 0 |
| Puerto Rico | 335 | 18.5 / 81.5 / 0.0 | 4.2 / 95.8 / 0.0 | 18.5 / 81.5 / 0.0 | 48 | 0 | 0 | 0 |
| Southern California | 92 | 70.7 / 13.0 / 16.3 | 76.1 / 20.7 / 3.3 | 70.7 / 13.0 / 16.3 | 7 | 12 | 0 | 0 |

- **Every move is case-specific.** All inside → below and above → inside moves are dominant-case
  hours. All inside → above and all but one below → inside move are bigger-and-longer.
- **New England and Puerto Rico.** Their drops come entirely from the dominant case.
  - New England: 767 inside → below and 20 above → inside.
  - Puerto Rico: 48 inside → below.
  - Their bigger-and-longer hours didn't move.

## Full rule (shipped) against the variant, all hours (run 57)

Every mainland headline hour, 7 Sep to 27 Oct 18:00Z; inside / below / above, in percent.

| Region | Hours | #283 | Full rule (shipped) | Variant (not shipped) |
|---|---|---|---|---|
| **All (site-wide)** | 235,014 | 59.9 / 35.7 / 4.4 | 59.7 / 36.2 / 4.1 | 60.0 / 35.6 / 4.4 |
| Carolinas and Georgia | 25,482 | 81.3 / 7.0 / 11.7 | 82.3 / 7.1 / 10.7 | 81.4 / 6.9 / 11.7 |
| Florida Atlantic | 29,339 | 77.4 / 10.3 / 12.3 | 77.9 / 10.3 / 11.8 | 77.9 / 9.8 / 12.3 |
| Gulf | 10,610 | 70.5 / 25.4 / 4.1 | 69.5 / 26.6 / 3.8 | 70.7 / 25.2 / 4.1 |
| Mid-Atlantic | 25,090 | 78.8 / 12.2 / 9.0 | 78.8 / 13.1 / 8.1 | 78.9 / 12.1 / 9.0 |
| New England | 34,912 | 33.9 / 65.4 / 0.7 | 31.7 / 67.6 / 0.6 | 33.9 / 65.4 / 0.7 |
| Northern California | 32,501 | 34.2 / 64.4 / 1.4 | 34.1 / 64.4 / 1.4 | 34.2 / 64.4 / 1.4 |
| Pacific Northwest | 11,497 | 17.1 / 82.9 / 0.0 | 17.0 / 83.0 / 0.0 | 17.1 / 82.9 / 0.0 |
| Puerto Rico | 12,144 | 9.6 / 90.4 / 0.0 | 9.2 / 90.8 / 0.0 | 9.6 / 90.4 / 0.0 |
| Southern California | 53,439 | 82.5 / 16.8 / 0.7 | 82.5 / 16.8 / 0.7 | 82.5 / 16.8 / 0.7 |

The full rule shipped. In the dominant case it keeps the height on the same train as the period
shown. It also lowers the share above the NWS range, the constraint the 0.70 scale was chosen
against: on dominant hours from 8.8% to 3.7%, and in the two regions over 10% (Carolinas 11.7% →
10.7%, Florida Atlantic 12.3% → 11.8%).

| | Helps | Hurts |
|---|---|---|
| Full rule | Carolinas (+1.0 point inside, −1.0 above), Florida Atlantic (+0.5, −0.5), Mid-Atlantic (above −0.9) | New England (−2.2 inside), Gulf (−1.0), Puerto Rico (−0.4) |
| Variant | New England, the Gulf, Puerto Rico | Gives up the above-the-range gain |

New England, Puerto Rico and the Pacific Northwest read below the NWS range on most hours under
any of the three heights (65%, 90% and 83%). That needs observations, not a change to this rule.

## Not changed by #284 (reported for follow-ups)

- **Calibrated California headline.** The face's period factor still reads the rated period. If
  it used the shown period, the headline would change on 3,253 calibrated hours at K = 4 (run 57),
  median ratio 0.849, from 2.78 ft to 2.38 ft median. That would also change the stars, so
  it needs its own star preview.
- **Uncalibrated WW3-path hours:** 93 hours, median ratio 0.908.
- **Stars if the rating used the wind sea (informational).** Each run replayed the NWPS-override
  hours through the rating and kept those that reproduced the published stars. Star change on
  the switched hours of the published window:

  | Run | Switched, reproduced | Unchanged | Down | Up |
  |---|---|---|---|---|
  | 55 | 2,488 | 1,564 | 148 (-2: 15, -1.5: 14, -1: 17, -0.5: 102) | 776 (+0.5: 497, +1: 238, +1.5: 14, +2: 17, +2.5: 10) |
  | 56 | 5,758 | 2,564 | 2,321 (-2.5: 19, -2: 39, -1.5: 28, -1: 607, -0.5: 1,628) | 873 (+0.5: 633, +1: 189, +1.5: 1, +2: 8, +2.5: 42) |
  | 57 | 5,492 | 2,445 | 2,174 (-2.5: 7, -2: 31, -1.5: 25, -1: 483, -0.5: 1,628) | 873 (+0.5: 633, +1: 189, +1.5: 1, +2: 8, +2.5: 42) |
