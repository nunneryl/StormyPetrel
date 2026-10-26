# Face-height calibration, run 48

- Run: sw1-raycast run 48, mode `face-calibration`, commit f3ee3da043a2881e106d21c263436bde8fbfa04c,
  2026-10-26 17:02Z. Window from 2025-06-01; our forecast rows start 2026-04-22 (Avila
  2026-06-16). The sheltered-water check covers 2026-10-12 17:02Z to 2026-10-26 17:02Z.
- Artifact `face-calibration-48`, sha256 51506adaff32745af6041a764e9f23ee58e156b85ff806653a49b68f54a4e027
  (expires 2027-01-24).

## Files

- `face_calibration_pairs.csv`: every paired hour (22,682), byte for byte the artifact's CSV.
  Since run 47 each row also carries `swell_tp`, `face_ft_raw` and `sized_from`, the height the
  face was sized from.
- `face_sheltered.csv`: the 16 sheltered-water suspects, byte for byte the artifact's.
- `face_calibration_summary.json`: the artifact's JSON with its `pairs` list taken out (the
  same hours are the CSV).
- `derived.json`: worked out after the run with `pipeline.face_calibration.stats`:
  - Duck Pier's ocean hours (our period 3 s or more, from 2026-08-22 12:00Z) by measured
    period and by measured height;
  - MOP's own height at Doheny against CDIP 284, by period and by height;
  - the measured dominant period at six NDBC buoys over the sheltered check's window, from
    NDBC's realtime files fetched on 2026-10-26.

## How to read it

- Every FRF 8 m month from June 2025 to October 2026 was read; none is named as unread.
- The sized-from height exists only from 2026-09-07, when `face_ft_raw` began to be stored.
  On the NWPS override's hours it recovered the stored `hs` within 0.01 m on 5,520 of 5,520
  hours.
- On WW3-path hours the face is sized from `sqrt(sum(partition Hs^2 x directional gain))`.
  That is a deep-water, window-weighted energy, not a nearshore Hs. Only 98 such hours carry a
  `face_ft_raw`, all at Doheny.
- A sheltered-water suspect is a spot whose stored period was under 3 s on 25% or more of its
  hours. Every suspect's baked NWPS node passes the seaward half-plane rule (within 90 deg of
  orientation). The nearest buoys measured under 3 s on 0 to 3% of the same window. The
  geometry here is distance and bearing only; no coastline test decides whether a node lies
  in a bay, sound or lagoon.
