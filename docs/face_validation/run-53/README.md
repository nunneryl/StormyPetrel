# Face-height comparison, run 53

- Run: sw1-raycast run 53, mode `face-compare`, commit 3f8f7d5e5828a3a2510343c76b997dd50482be67,
  2026-10-27 16:38Z to 16:44Z. Window 2026-09-07 00:00Z to 2026-10-27 16:00Z.
- Artifact `face-compare-53`, sha256 b4c321e901dd50d24e5ff3b8d245d0a32c040073d10692e827882e8c48128e42
  (expires 2027-01-25). Errors: the NWS API's 404 for zone CAZ364 (as in run 52); and FRF's
  October lidar file could not be read ("File is truncated should be= 460683717 actual =
  368803840", FRF's server, most likely the live month mid-rewrite), so there are no FRF
  small-day hours in this run and no FRF CSV. The lidar reader now retries a month once
  (after run 53).

## Files

- `face_compare.json`: the artifact's JSON, byte for byte.
- `face_compare_srf.csv.gz`: the artifact's `face_compare_srf.csv` (264,731 rows, 47 MB),
  gzipped; it decompresses to the artifact's file byte for byte. Since run 53 each row carries
  `hs` (the height the hour was sized from), `period` (the rated period) and `band`.
- `derived.json`, made by `derive.py` from the CSV (not on the runner): inside / below / above by
  region for the published face and for typical x one flat k (0.66, 0.70, 0.75, 0.79).

## How to read it

- Section 5 of the log (`fit` in the JSON): per period band, one scale k on typical for every
  region, with at most 10% of the band's hours above the NWS range: 0.80 (under 8 s), 0.75
  (8-10 s), 0.80 (10-12 s), 0.79 (12-14 s), 0.75 (14 s and over). The strict fit (at most 10%
  above in every region): 0.71, 0.64, none, none, 0.65. One k for every band: 0.79 capped,
  0.66 strict.
- The fitted k barely moves with period; inside one office the best k moves little and without a
  common direction, while between offices it runs from about 0.5 (ILM, MFL, TBW) to about 1.2
  (MFR, PQR, BOX).
- California's calibrated spots: `hs` is the height the face was sized from before the MOP
  factor (the raw NWPS height), so these fits describe typical built on the raw height there.
