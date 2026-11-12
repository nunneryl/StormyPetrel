# HRRR and RRFS: what the pipeline reads, what NOAA has announced, and a switch-over plan

Document only; no code changes. Written 2026-11-12.

## The short answer

- **NOAA has not announced an end to HRRR.** The Rapid Refresh Forecast System (RRFS) went operational on 2026-11-03 at 12Z (SCN 26-48), replacing NAM, HiresW, HREF, SREF and NARRE (SCN 26-47, SCN 26-89). None of the three notices retires HRRR, and SCN 26-48 keeps HRRR as two members of the new RRFS ensemble products (REFS) for CONUS and Alaska.
- **The only HRRR notice of the past year, PNS 26-70,** proposes moving two NOMADS first-guess files (`hrrr/prod/nwges/…`, 1-hour forecasts for the 08Z cycle moving to 17Z). The pipeline does not read those files.
- **So nothing forces a switch now.** RRFS is worth adding for two reasons: it covers Hawaii and Puerto Rico, which HRRR does not, and it gives a standby for the day HRRR goes. The weekly notice watch (`noaa-notices.yml`) opens an issue for any notice naming HRRR or RRFS, so an HRRR retirement notice will not pass unseen.

## Where the pipeline reads HRRR

| Step | What it does | Spots |
|---|---|---|
| `pipeline/forecast/hrrr.py` (fetch, every full run) | Newest 00/06/12/18Z cycle with all 49 steps (f000–f048) from `nomads…/hrrr/prod/hrrr.YYYYMMDD/conus/hrrr.tHHz.wrfsfcfFF.grib2`, subset by NOMADS's GRIB filter (`filter_hrrr_2d.pl`) to 10 m UGRD and VGRD, plus the f00 LAND mask. A spot on a land cell reads the nearest water cell seaward (`hrrr_land_cell_spots.json` keeps 5 on their land cell). u/v are grid-relative and turned to true north. Writes `forecast_data/hrrr.json`. | The 569 of 648 spots inside the CONUS box (22–50 N, 130–65 W). |
| `pipeline/interpret.py` (`_merge_hrrr_into_nwps`) | Replaces NWPS's wind with HRRR's on every hour both have (`wind_source` "hrrr"); the rest keep NWPS wind. The wind then sets `wind_mult` (and its cap under heavy chop) and the strong-wind star cap. | Same 569. On 2026-11-10's 19:58Z run, 27,881 spot-hours used HRRR wind and 66,079 kept NWPS wind (hours past f048, and the 79 spots outside CONUS: 55 Hawaii, 24 Puerto Rico). |
| `pipeline/data_quality.py` (sw1-raycast mode data-quality) | Read-only checks of HRRR's cells, land mask, true-north turn and the published wind, from NOAA's AWS archive (`noaa-hrrr-bdp-pds`). | Tooling only. |
| `pipeline/node_preview.py` | Treats a published hour's wind as HRRR's when it differs from NWPS's (HRRR replaces NWPS inside 48 h). | Tooling only. |

Timing on that run: the 18Z cycle's 49 steps were all on NOMADS when the 20:15 slot's run looked at 20:25Z, and the HRRR stage took about 35 seconds.

## What RRFS provides

- **Model:** deterministic RRFS, hourly cycles; the 00/06/12/18Z cycles run to 84 h, the others to 18 h (SCN 26-48). A 5-member ensemble runs to 60 h at 00/06/12/18Z; REFS ensemble products (mean, spread, probabilities) at 00/06/12/18Z.
- **Grids:** 3 km for CONUS and Alaska, 2.5 km for Hawaii and Puerto Rico, 13 km for North America (SCN 26-48).
- **Files** (NOMADS, live since 2026-11-03: `https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs/prod/`):
  - `rrfs.YYYYMMDD/CC/rrfs.tCCz.2dfld.3km.fFFF.conus.grib2` (surface fields, which is where 10 m wind would be);
  - `rrfs.YYYYMMDD/CC/rrfs.tCCz.2dfld.2p5km.fFFF.{hi,pr}.grib2`;
  - each with a `.grib2.idx` index. The variable list is at `https://www.nco.ncep.noaa.gov/pmb/products/rrfs/`.
- **Seen on NOMADS on 2026-11-12 (12Z cycle):** 85 steps (f000–f084) for CONUS, Hawaii and Puerto Rico, each with its index; f000 posted at 13:47Z and f084 at 15:23Z, so the cycle is complete about 3.4 h after its nominal time. File sizes: CONUS 296–337 MB per step; Hawaii 9–10 MB; Puerto Rico 23–29 MB.
- **Not yet checked** (first steps of any build): whether NOMADS has a GRIB filter for RRFS (none was found on its front page; byte ranges from each step's `.idx` would do instead, as `data_quality.py` already does for HRRR on AWS); the exact names of the 10 m wind and land-mask records in the index; whether RRFS's u/v are grid-relative like HRRR's (`uvRelativeToGrid`), which decides whether the true-north turn applies.

## What changes for the site

| | HRRR (today) | RRFS |
|---|---|---|
| CONUS spots (569) | 3 km, hourly, 48 h | 3 km, hourly, 84 h at 00/06/12/18Z |
| Hawaii (55) and Puerto Rico (24) | none: NWPS wind | 2.5 km, 84 h |
| Hours past 48 h | NWPS wind | RRFS to 84 h, then NWPS |
| Cycle on NOMADS after nominal time | all 49 steps there when looked for at 2 h 25 min (18Z, 20:25Z); when they posted was not measured | f084 posted 3 h 23 min after nominal (12Z, 15:23Z) |
| Download per CONUS step | ~5 MB through the GRIB filter | two records by byte range, size to be measured |

## Proposed switch-over, with a fallback

1. **Now: no switch.** HRRR stays primary for CONUS. Nothing in NOAA's notices dates its end.
2. **Build an RRFS fetcher beside HRRR's** (`forecast/rrfs.py`), the same shape as `hrrr.py`: newest complete 00/06/12/18Z cycle, 10 m u/v and the land mask by byte range from the `.idx`, the same seaward water-cell rule, true-north turn if the grid needs it, written to `forecast_data/rrfs.json`. A data-quality preview (sw1-raycast) compares it with HRRR spot by spot and hour by hour over at least two weeks before anything reads it.
3. **First use: Hawaii and Puerto Rico.** Their 79 spots have no model wind today. RRFS wind for them, shown first in a rating-preview run against NWPS wind and their buoys' wind, then merged as `wind_source` "rrfs".
4. **Standby for CONUS, in interpret's merge:** HRRR first; RRFS for any CONUS spot-hour HRRR does not have (a missing or stale HRRR cycle, hours 49–84); NWPS wind last, as today. Each hour's `wind_source` says which.
5. **When NOAA announces HRRR's retirement:** the notice watch opens an issue; switch CONUS to RRFS first, with HRRR as the standby until its last cycle, after the two-week comparison in step 2. Slots: RRFS's 00/06/12/18Z cycles complete about 3.4 h after nominal, so the 01:45 / 08:15 / 13:45 / 20:15 slots would read the cycle 6 h earlier than they read HRRR's; measure that against the publication times before moving any slot (`docs/scheduling.md`).

## Sources

- SCN 26-48 (updated), "Implementation of the Rapid Refresh Forecast System (RRFS) and RRFS Ensemble Forecast System (REFS): Effective November 3, 2026": `https://www.weather.gov/media/notification/pdf_2026/scn26-048_Updated_RRFS_and_REFS_Implementation.aaf.pdf`
- SCN 26-89, "Model changes related to RRFS implementation and NAM/SREF/HiresW/HREF decommissioning: Effective Nov 3, 2026": `https://www.weather.gov/media/notification/pdf_2026/scn26-89_RRFS_Downstream.pdf`
- SCN 26-47 (updated), retirement of NAM, SREF, HREF, HiresW and NAM MOS: `https://www.weather.gov/media/notification/pdf_2026/scn26-47_Updated_Retirement_of_NAM_SREF_HREF_HiresW_NAM_MOS_aac.pdf`
- PNS 25-41, the 2025 proposal to retire the legacy models: `https://www.weather.gov/media/notification/pdf_2025/pns25-41_RRFS_legacy_model_cessation.pdf`
- PNS 26-70, proposed HRRR/HRRRDAS NOMADS product changes: `https://www.weather.gov/media/notification/pdf_2026/pns26-70_HRRR_HRRRDAS_NOMADS_Product_Changes.pdf`
- Pipeline figures: full pipeline run 42890563590 (2026-11-10 19:58Z), its full log.
