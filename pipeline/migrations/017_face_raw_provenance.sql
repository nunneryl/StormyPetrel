-- Migration 017 — the PRE-CORRECTION face, and which pipeline produced the row.
--
-- WHAT WENT WRONG. The per-spot MOP face correction (face_correction.py) divides face_ft in
-- place and the raw value is destroyed at that line. forecasts is UNIQUE(spot_id, valid_time,
-- source) and every run UPSERTs over the hours it covers, so a past hour freezes at whatever
-- the last run to cover it wrote. The correction shipped 2026-09-02 03:30 UTC (seam 1873e49
-- + factors 865665c), which means any window straddling that instant holds a MIXTURE of
-- corrected and uncorrected faces in one column, with nothing on the row to say which.
--
-- scripts/mop_face_validation.py measures face_ft / (MOP Hs x 3.281). Run over a straddling
-- window it returns neither the offset nor the residual but a blend of the two. Measured
-- 2026-09-07 it read steamer-lane at 2.188 against a committed factor of 2.81 — a number that
-- is not wrong-looking enough to stop anyone, and it nearly produced the conclusion that the
-- calibration was fundamentally unstable. It was not. The measurement was contaminated.
--
-- UN-CORRECTING AFTER THE FACT CANNOT RECOVER IT, which is why this is a schema change and
-- not a script change. The applied divisor is not one number per spot. steamer-lane's rows in
-- this table span at least three regimes inside a single 14-day window:
--
--     before 2026-09-02 03:30   not corrected
--     865665c (2026-09-01)      divided by 2.8702
--     724f442 (2026-09-03)      ABSENT from the factor file — not corrected at all
--     2ff93e6 (2026-09-03)      divided by 2.8084
--
-- A script dividing by "the committed factor" would be right for the last regime and wrong
-- for the other three, and would leave no trace of having been wrong. The only fix that
-- survives a second, third and fourth regeneration is to stop destroying the input.
--
-- face_ft_raw IS NOT A DERIVED COLUMN. It is written by the correction seam itself, before it
-- divides, for EVERY rated hour — corrected, uncorrected, MOP-tier and all. Where no factor
-- applies it equals face_ft exactly, and that equality is the assertion that no arithmetic
-- ran, not a placeholder.
--
-- ORDER OF OPERATIONS — READ THIS BEFORE MERGING THE CODE:
--
--     RUN THIS MIGRATION FIRST, THEN DEPLOY.
--
-- db_import sends every key in its record dict to PostgREST in one bulk upsert. If the code
-- ships before these columns exist, PGRST204 ("column not found") fails the whole forecasts
-- upsert and the run publishes NO forecast rows at all — not a degraded run, an empty one.
-- The reverse order is harmless: the columns sit NULL until the first run that writes them,
-- which is exactly what every pre-existing row will hold anyway.
--
-- Idempotent. Run in the Supabase SQL editor.

ALTER TABLE forecasts
  ADD COLUMN IF NOT EXISTS face_ft_raw DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS face_correction_version TEXT;

COMMENT ON COLUMN forecasts.face_ft_raw IS
  'face_ft as it stood BEFORE the per-spot MOP correction divided it — the quantity '
  'scripts/mop_face_validation.py must measure. Written by the correction seam for every '
  'rated hour, so it is meaningful in all four cases the seam distinguishes: a corrected '
  'spot holds the pre-division value; a spot with no factor, a held-out spot and a MOP-tier '
  'spot hold a value EQUAL to face_ft (nothing divided, and the equality says so); an hour '
  'with no face at all holds NULL, as face_ft does. The applied divisor is recoverable as '
  'face_ft_raw / face_ft, so no separate factor column is needed. Stored verbatim, with '
  'whatever rounding the upstream producer applied and none of its own, so it is directly '
  'comparable to the face_ft of a row written before the correction existed. NULL means the '
  'row predates this migration and its correction state is UNKNOWN — see '
  'face_correction_version.';

COMMENT ON COLUMN forecasts.face_correction_version IS
  'Which correction regime produced this row: "<code>:<measured_on>:<fingerprint>", e.g. '
  '"1:2026-09-01:3f9a2c11". <code> is face_correction.FACE_CORRECTION_VERSION, bumped when '
  'the seam ARITHMETIC changes. <measured_on> is the newest measured_on in the factor file. '
  '<fingerprint> is 8 hex of a sha256 over the sorted slug=factor pairs, so a regeneration '
  'that moves any divisor changes it and one that moves none does not. Written on EVERY row '
  'the seam sees, including uncorrected and MOP-tier rows, so it identifies the run rather '
  'than the outcome; "1:none:none" means the seam ran with no factors loaded at all. '
  'A contaminated window is therefore a QUERY, not a thing someone notices: '
  '  SELECT DISTINCT face_correction_version FROM forecasts '
  '  WHERE valid_time >= $1 AND valid_time < $2; '
  'more than one row back means the window spans regimes. NULL is one such value and means '
  'the row predates this migration — the case that cannot be repaired, because NULL spans '
  'both the pre-correction era and the contaminated one and nothing distinguishes them. '
  'Note that once face_ft_raw is populated a differing <fingerprint> is BENIGN for the '
  'measurement: raw is pre-correction regardless of which divisor was applied. It is a '
  'differing <code> that matters.';
