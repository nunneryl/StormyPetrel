# Face-height comparison, run 52

- Run: sw1-raycast run 52, mode `face-compare`, commit 9cea835cddda86c41564dde3e8e5c64c19a96784,
  2026-10-27 15:12Z to 15:17Z. Window 2026-09-07 00:00Z to 2026-10-27 15:00Z.
- Artifact `face-compare-52`, sha256 33b33efc7c7474d867bcc5debb97fc4eebba88c7f95b55ce305bb61269ad46d8
  (expires 2027-01-25). The one error is the NWS API's 404 for zone CAZ364 (LOX's Malibu
  Coast segment names it; the API says it does not exist).

## Files

- `face_compare.json` and `face_compare_frf.csv`: the artifact's files, byte for byte.
- `face_compare_srf.csv.gz`: the artifact's `face_compare_srf.csv` (264,362 rows, 38 MB),
  gzipped; it decompresses to the artifact's file byte for byte. One row per spot-hour with an
  NWS Surf Zone Forecast range, every group (headline, base path, moved spots' old points).
- No reports CSV: there are no visitor surf_reports since 2026-09-07.
- `derived.json`, made by `derive.py` from the two CSVs above (not on the runner): the scale k on
  typical that puts the most hours inside the NWS range, the median of the range's middle over
  typical, the k with as many hours above as below, by region, office and spot type; the
  inside / below / above shares for the published face and for typical x 0.75, 0.8 and 0.9 by
  region; and the FRF small-day hours' lidar breaking Hs over the 8 m array's Hs.

## How to read it

- typical's multiplier over Hs (headline hours, 753,741): median 1.41 (quartiles 1.30 to 1.52);
  by rated period 1.34 under 8 s, 1.44 at 8 to 10 s, 1.38 at 10 to 12 s, 1.44 at 12 to 14 s,
  1.49 at 14 s and over.
- FRF: 18 small-day hours, 15 of them in one spell (2026-10-07 to 10-10), FRF 8 m Hs 0.35 to
  0.96 m, Tp 6.5 to 8.1 s. The lidar's breaking Hs was a median 1.06 x the 8 m Hs. These are the
  same 18 hours the local dry run used before run 52, so run 52 repeats that figure; it does not
  confirm it independently. Duck Pier's archived hours all came from the old sample point (#280
  moved it on 2026-10-27).
- The NWS ranges are forecasts, not measurements, and the offices write them differently (MTR,
  PQR, BOX, SJU and MFR read higher than the rest for the same nearshore height); a k fitted to an
  office's ranges partly measures that office's habit. Hawaii (HFO) is apart: whether its table is
  face height is not established.
- TBW's ranges are mostly "1 foot or less" (0 to 1), so its balanced k (0.30) is not meaningful.
- Period cannot be separated from region or office in this run: the SRF rows do not carry Hs or
  the rated period.
