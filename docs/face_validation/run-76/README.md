# Run 76: the wind-sea switch against the spot's facing (sw1-raycast mode period-preview)

- **Run:** sw1-raycast run 76 (Actions run 43895878607), branch `claude/practical-dirac-bshfbr` at `be4ee11`, 2026-11-16 15:24Z. Passed.
- **Artifact:** `period-preview-76`, zip sha256 `ffa0d9c48e6d02b54ab72f3efde42e6dcb0526d59663f9cc04bc7be15737592c` (GitHub's digest says the same).
- **Stored gzipped since 2026-11-12:** `period_preview.json`, `period_preview_away.csv`, `period_preview_archive.csv`, `period_preview_hours.csv`, `face_compare.json` and the job log `run_log.txt` (timestamps cut) are kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte); the sha256 of every saved file is in `../CHECKSUMS.sha256`.
- **Made here, not by the run:** `removed_by_window_side.txt` (the removed switches split by whether the wind sea came from inside the spot's window, from `period_preview_away.csv`'s `wind_in_window`), `facing_audit.json` (the 70 spots whose facing is more than 45 deg from the middle of their window or outside it, from the roster at `66ad5ac`, with the coastline's own bearing beside each) and `window_vs_facing.txt` (the 128 spots part of whose window is 90 deg or more off their facing).

## What it compared

The rule on `main` (#284 with run 57's window exclusion: no switch from 90 deg or more outside the window) against run 76's first version: no switch either when the wind sea comes from 90 deg or more off the spot's facing (`orientation_deg`), wherever it comes from. Archive 2026-09-07 to 2026-11-16 15:00Z; published window from 15:24Z.

## The gate

0 star differences, 0 other-field differences, 0 unexpected, 0 badge flips, run 54's NWS table reproduced. 2,684 published rows lose the wind-sea mark by the facing rule (2,669 of them change their surf height), and nothing else changes.

## Switches removed: 18,405 of 75,547 archive hours (24%)

On the removed hours with a buoy reading (9,244), the shown period within 2 s of the buoy's dominant period: **81.4% under the current rule (the wind sea's period), 37.0% under the facing rule (the rated swell's).**

| Wind sea | Hours | Spots | With buoy | Within 2 s, current | Within 2 s, facing rule |
|---|---|---|---|---|---|
| Inside the window | 987 | 46 | 351 | 84.9% | 42.5% |
| Outside the window | 17,418 | 394 | 8,893 | 81.2% | 36.8% |

By state, and the top 20 spots each side: `removed_by_window_side.txt`.

**Reading the buoy check.** The buoys are offshore. A wind sea running away from the beach passes the buoy, so the buoy's period matches it whether or not it reaches the break; on these hours the check says how well the model describes the sea at the buoy, not at the beach. It cannot by itself say the switch was right.

## Decided after this run (2026-11-16)

The rule is refined: no switch only when the wind sea is **outside the window and** 90 deg or more off the facing; inside the window the switch is as on `main`. That keeps the 987 inside-window hours above switching (Sandy Beach Oahu, Tunnels Beach, Third Beach Newport, Montauk Point, Narragansett, Cape Hatteras Lighthouse and others where swell wraps in from beside the facing) and still removes the 17,418 outside it. Run 77 measures the refined rule. Facing and window fixes (MacKerricher, Hillsboro Inlet, Richardson Beach, Lovers Point, Monterey Beach) are a separate change with a star preview.
