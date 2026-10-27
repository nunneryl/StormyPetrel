# Face-height follow-up, run 50

- Run: sw1-raycast run 50, mode `face-seaward` (`--sections deeper`), commit
  133cde0e678a43ca6dd6f0b430c85d4ea7086c53, 2026-10-27 00:34Z to 00:39Z. Suspects window (run
  48's): 2026-10-12 17:02Z to 2026-10-26 17:02Z.
- Artifact `face-seaward-50`, sha256 76d28960c4cc0795fad8c89148cf4d4821371aea1d29a6e127220b5a7cfe13ad.
  No section failed (`errors` is empty).

## Files

- `face_seaward.json` and `face_seaward/deeper.csv`: the artifact's files, byte for byte.

## How to read it

- `depth_probe` is one live ERDDAP read from the runner at Duck Pier's sample point: 7.4 m. Every
  other depth comes from the bundled ETOPO 2022 extract (`../etopo/etopo_15s.json`), and each
  carries its source. Run 49's depths all came back None because the tool then turned any
  failure into None without recording why; that cause is not recoverable.
- For each spot, "current point" is the NWPS sample point baked in `spots_enriched.json`, and
  "cell" is the nearest NWPS cell at least 10 m deep (ETOPO, one decimal) within 30 deg of the
  spot's facing, with open water between it and the spot. Both are read from the same NWPS
  cycles: the 6 NOMADS held per office on 2026-10-27 (12 for ilm), 72 to 73 paired hours.
- Measurements: Duck Pier against the FRF 8 m array; the rest against the nearest NDBC buoy,
  20 to 143 km away. Away from Duck Pier the heights are a rough guide only. A period does not
  shorten between a buoy and the beach, so a period under 3 s is a sign of sheltered water.
- St. Pete Beach, Treasure Island FL and Madeira Beach find the same cell (27.7373, -82.86304),
  so their cell figures are identical.
- The three days were small and short-period on the Gulf (NDBC 42098 median 5.0 s); nothing
  here says how the cells do in a long-period swell.
