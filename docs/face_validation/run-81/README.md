# Run 81: star preview of the hand-set swell windows (sw1-raycast mode star-preview)

- **Run:** sw1-raycast run 81 (Actions run 43995266298), branch `claude/practical-dirac-bshfbr` at `a368ee0`, 2026-11-17 01:23Z. Passed. Spots files: ARCS = this branch (`a368ee0`), BASELINE = `origin/main` (`98d5584`).
- **Artifact:** `star-preview-81`, zip sha256 `66d1a35861dd2154d5a88437cc711f1195562cacfe904375660515402a996492` (GitHub's digest says the same).
- **Saved:** `star_preview.json` (the artifact's only file) and `run_log.txt` (the job log, timestamps cut); both are under 100 KB, so neither is gzipped (Stored gzipped since 2026-11-12 applies to files of 100 KB or more). The sha256 of every saved file is in `../CHECKSUMS.sha256`.
- **Compared:** the published window from 01:24:38Z (84,514 rows), main's windows against the hand-set ones: Kanaha Beach and Hoʻokipa 315-90, Lovers Point 270-0 (was 317-319), Monterey Beach 270-0 (had none; optimal 320 -> 315).

## The gate

- 0 rows NOT reproduced; 95 within the stored values' rounding (NWPS 94, WW3 1); the stored wind_mult reproduced on every row.
- **No other spot moved.** The self-check line is worded for facing ("rows at spots whose facing does not change that moved: 16"); no facing changed, so it counts every moved hour at every spot. All 16 are at the window spots (Lovers Point 9, Monterey Beach 7), and they equal every non-zero change in that section (16), checked from `star_preview.json` after the run. The swell-window replay agrees: 9 changed hours, at Lovers Point and Monterey Beach only.

## The four spots (every hour NWPS-rated)

| Spot | Swell-window replay | Full rater, wind recomputed |
|---|---|---|
| Kanaha Beach | 125 hours, no change | no hour moved |
| Hoʻokipa | 125 hours, no change | no hour moved |
| Lovers Point | 100 hours: 8 at -2.5 | 9 moved: 8 at -2.5, 1 at -0.5 |
| Monterey Beach | 2 hours: 1 at +0.5 | 7 moved, all +0.5 |

## Lovers Point's 8 hours at -2.5 (accepted 2026-11-17)

4.5 -> 2.0 stars on 19 Nov 05-07Z and 19 Nov 21Z - 20 Nov 01Z (one more, 19 Nov 20Z, -0.5). Under the old window (317-319, padded 315-321) the WW3 partition from 193 deg was 122 deg outside: gain 0, no partition qualified, and the rater fell back to NWPS's direction at the spot's sample point, 304 deg, near the optimal 315 (alignment 0.991) -> 4.5. Under 270-0 the 193 deg partition is 77 deg outside, on the 0.15 rung, so it is chosen and the NWPS height is aligned on 193 (0.25) -> 2.0. Accepted: a south swell from behind Point Pinos does not reach this NE-facing point, and the old 4.5 rested on that sample point, which is not out to sea (the cell it shares with Monterey Beach, 1.8 km alongshore; a node review is in the backlog).
