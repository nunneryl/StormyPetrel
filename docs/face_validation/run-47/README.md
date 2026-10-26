# Face-height calibration, run 47

Our published nearshore Hs against measured Hs in shallow water. Kept here so the numbers
outlast the run's artifact (it expires 2027-01-24).

- Run: sw1-raycast run 47, mode `face-calibration`, commit 44479d33ba99a02136dc222a10d1d2c342f3652d,
  2026-10-26 15:19Z. Window from 2025-06-01; our forecast rows start 2026-04-22 (Avila
  2026-06-16).
- Artifact `face-calibration-47`, sha256 293849ff01874cfb7747efc84c0a3c107f1a518af4f5013bcb5b355be5f0e374.

## Files

- `face_calibration_pairs.csv`: every paired hour (21,354), byte for byte the artifact's CSV.
- `face_calibration_summary.json`: the artifact's JSON with its `pairs` list taken out (the
  same hours are the CSV); `since`, `until`, `coverage` and `summary` are as the run wrote them.
- `rater_split.json`: derived after the run from the CSV with `pipeline.face_calibration`'s own
  `summarize` and `stats`. It has the split by the rater that fed each hour, Duck Pier's hours
  with our period of 3 s or more, and everything with Duck Pier's sheltered hours left out.

## How to read it

- "Ours" is the stored `hs`: NWPS's total Hs at the spot's sample point. The face is sized
  from this `hs` only on the NWPS override's hours. On MOP hours it is sized from MOP's Hs, and
  on WW3-path hours from the WW3 partitions; neither height is stored. On those hours this
  compares NWPS's height, not the one the face was sized from.
- A past hour holds the last value published for it, the shortest-lead forecast it had.
- The instruments sit at 8 to 15 m depth. Our NWPS sample points' depths are not stored.
- Duck Pier: on 2,471 of its 2,708 paired hours our period is under 3 s and our Hs about
  0.09 m. That is a sheltered cell behind the island, not the ocean. It last happened on
  2026-08-22 11:00Z; Duck Pier's hours after that read the ocean.
- The FRF 8 m array was read from June 2025 to August 2026 only. September and October 2026
  were silently skipped on the runner, though both open; the next run retries a month and
  names any month it cannot read.
