# Run 79: star-preview fidelity, main against main (sw1-raycast mode star-preview)

- **Run:** sw1-raycast run 79 (Actions run 43984789251), branch `claude/practical-dirac-bshfbr` at `02826cb`, 2026-11-17 00:05Z. Passed. Spots files: ARCS = this branch (`02826cb`, the same roster as main), BASELINE = `origin/main` (`2106e9f`).
- **Artifact:** `star-preview-79`, zip sha256 `63da41e52270422fd5b9933a3ec33e52cf91715436c9a25a8f9963217592255a` (GitHub's digest says the same).
- **Saved:** `star_preview.json` (the artifact's only file) and `run_log.txt` (the job log, timestamps cut); both are under 100 KB, so neither is gzipped (Stored gzipped since 2026-11-12 applies to files of 100 KB or more). The sha256 of every saved file is in `../CHECKSUMS.sha256`.
- **What changed since run 78:** the replay recovers an NWPS row's height without the float round trip that dropped a stored 0.50 ft under the flat line (`_hs_for_face`); a miss reproduced only for some reading of the stored face (2 decimals, +-0.005 ft) and multipliers (3 decimals, +-0.0005) is counted apart as "within stored precision"; the gate runs on every preview, so this run is main against main.

## The gate

- Published window from 00:06:29Z: 85,160 rows (NWPS 78,265, WW3 6,895).
- **0 rows NOT reproduced.** The stored wind_mult reproduced on every row.
- **95 within stored precision** (NWPS 94, WW3 1; 0.11%): listed apart and left out of comparisons. The 20 the run lists are each one half-star step (9 replay higher, 11 lower), none at the flat line.
- 0 rows moved at spots whose facing is unchanged (no facing changed).
- Run 78 (a different window, 81,576 rows) had 69 misses unexplained; this run has none. The counts are not comparable hour for hour.

## Reading

Every main-against-main miss is now accounted for by the stored values' precision, so a star preview's "reproduced" set can be trusted for window, facing and position changes; the "within stored precision" hours are about 0.1% of the window and are reported, not compared.
