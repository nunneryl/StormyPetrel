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

## When 8665387 was marked, and by which request (forecast-pipeline logs)

Read from the full-pipeline job logs (downloaded whole, searched for `pipeline.forecast.tides`):

| Run (UTC start) | Known-bad skipped | 8665387 |
|---|---|---|
| 2026-10-04 15:53 | 0 | cached |
| 2026-10-08 17:55 | 0 | cached |
| 2026-10-08 23:49 | 0 | cached |
| **2026-10-09 10:47** (run 37919710629) | 0 | **marked** |
| 2026-10-09 17:29 | 1 | skipped |
| 2026-10-10 10:02 | 1 | skipped |
| 2026-10-11 10:12 | 1 | skipped |
| 2026-10-30 13:45 | 1 | skipped (the only known-bad station; the other 232 were cached) |

- **The request.** At 10:48:17Z on 2026-10-09 the station's cache had run out, so the fetcher asked NOAA for `product=predictions`, `interval=hilo`, `begin_date=20261009` and its own horizon of 643 h, on MLLW, then STND, then MSL.
  - All three answered HTTP 200: "No Predictions data was found. Please make sure the Datum input is valid."
  - Hourly was not asked: the cache said hilo-only. So the 30-day mark rested on those three requests.
- **The same station answers today.** On 2026-10-30, `interval=hilo`, MLLW, 72 h returned 11 rows from the runner (this run).
- **Why NOAA answered that way on Oct 9 is not in the log.** It answered "no predictions" (not a throttle or an error) to the long request. An earlier fetch over the same 643 h horizon (fixed per station) had worked: the station was served from cache through 2026-10-08. Run 63 did not repeat the Oct 9 request (643 h from 20261009), so this does not establish whether the long range or the day was the cause.
- **What changed.** `forecast/tides.py` now asks again before marking: the 7-day output window (168 h), then 72 h. Rows on either and the station is not marked (`test_tide_known_bad_recheck.py`). The five spots move to the stations above, so none of them uses 8665387.
