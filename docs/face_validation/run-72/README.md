# Run 72: buoy assignment (sw1-raycast mode buoy-assign), the 66 spots with no buoy

- **Run:** sw1-raycast run 72 (Actions run 43263839198), main at `d81a4cf`, 2026-11-12 16:55Z.
- **Artifact:** `buoy-assign-72`, zip sha256 `ff8166145002fb66d82be952b6d5bf47e30395fd92d9654dc1bdf184be5ba5a8`.
- **Stored gzipped since 2026-11-12:** `buoy_assign.json` is kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte); the sha256 of every saved file is in `../CHECKSUMS.sha256`.
- **Scope: 66 spots, not 648.** The spots field reached the job empty (the job's environment shows `SPOTS:` with no value). So the report covers its default scope, the roster spots with no buoy: `{"none": 66}`. Run 74 repeated it with `all`: `docs/face_validation/run-74`.
- **Outcomes (fresh+7d, then gain / loss / change / same / none):** 15 / 0 / 0 / 0 / 51. cached+7d is the same; fresh 6 / 0 / 0 / 0 / 60; cached 0 / 0 / 0 / 0 / 66.
- **The 15 gains** are the same buoys at the same distances in runs 72, 73 and 74. Run 74's README lists them, and nothing in the 66 is over 150 km (largest: Bathtub Beach, 41009 at 147.03 km).
