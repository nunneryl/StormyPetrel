# Run 63: tide station 8665387 from the runner (sw1-raycast mode tide-station)

- **Run:** sw1-raycast run 63, commit `96155c3`, dispatched 2026-10-30 13:59Z.
- **Artifact:** `tide-station-63`; `tide_station.json` saved here byte for byte.
- **What it read:** NOAA CO-OPS only, from the GitHub runner. Predictions for 72 h from 2026-10-30.

## 8665387, Hamlin Creek, Isle of Palms

- **NOAA's listing:** in NOAA's prediction-station list (3502 stations). Type S, a subordinate station: predictions are offsets to reference 8665530, Charleston (Customhouse Wharf).
- **The gauge itself is gone:**
  - mdapi's details record says it was established 1975-01-28 and removed 1977-01-10;
  - the expanded record's `tidepredoffsets` is empty;
  - its predictions come from the offset table only.
- **From the runner on 2026-10-30:**
  - hilo on MLLW returned 11 rows;
  - hilo on STND and MSL, and hourly on every datum, answered "No Predictions data was found". Hourly is expected to be empty for a subordinate station.
- **So the station works from the runner today.** Every full pipeline run since at least 2026-10-14 skips it as known-bad. That entry must have been written by a run (between about 2026-09-30 and 10-14) that got "no predictions" on MLLW too. That run's request covered the pipeline's 25-30 day horizon, not 72 h, so the two answers are not the same request.
- **Water body:** the name and coordinates place it in Hamlin Creek, on the sound side of Isle of Palms, behind the island. The five spots face the ocean.

## Prediction stations near the five spots (the runner's 72 h hilo test, MLLW)

Every station listed answered with 11 or 12 extremes. All are subordinate to 8665530, with a mean range of 4.8 to 5.2 ft, except Capers Creek (8664941, type R). None is used by any roster spot yet.

| Spot | Nearest | Next |
|---|---|---|
| Berts Bar | TEC3047 Sullivans Island (outer coast), 1.03 km, 5.18 ft | 8665567 Ben Sawyer Bridge, ICWW, 1.86 km |
| Dewees Island | 8665111 South Dewees Island, Dewees Inlet, 1.71 km, 4.96 ft | 8664992 North Dewees Island, Capers Inlet, 1.84 km |
| Isle of Palms | 8665494 Isle of Palms Pier, 0.83 km, 5.02 ft | 8665552 Breach Inlet, 3.40 km |
| Wild Dunes | 8665494 Isle of Palms Pier, 3.86 km, 5.02 ft | 8665111 South Dewees Island, Dewees Inlet, 4.43 km |
| Windjammer | 8665494 Isle of Palms Pier, 0.17 km, 5.02 ft | 8665552 Breach Inlet, 2.60 km |
