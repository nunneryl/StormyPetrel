# Run 70: node preview of the six moves, with each NOMADS cycle file's time and size (sw1-raycast mode node-preview)

- **Run:** sw1-raycast run 70 (Actions run 42523943497), branch `claude/practical-dirac-bshfbr` at `5bc80e9`, 2026-11-08 21:11Z, `baseline_ref` main.
  - It ran after the full pipeline runs at 20:15Z and 20:41Z (manual) had published the 2026-11-08 12Z cycles.
- **Artifact:** `node-preview-70`, zip sha256 `c097b4cce5f9dd80bf12f999cb22aa67fa827b564ba9eec71da59e47ff6d7589`. `node_preview.json` is saved here byte for byte; `node_preview_hours.csv` is gzipped.
- **Moved spots:** Bandon Beach, Moonstone Beach Humboldt, Short Sands Beach York, Revere Beach, Hubbard Creek and Galveston Seawall. Point Lookout is held (run 69).
- **Window:** the published forecasts from 21:12Z, replayed from 48 hours earlier because buoys were named.
- **References:**
  - neighbours: Whiskey Run, Bastendorff, Trinidad State Beach, Agate Beach, Long Sands, Harbor Beach, Nahant, Winthrop Jetty, Lynn Beach;
  - buoys: 46015 (Bandon Beach, Hubbard Creek), 46244 (Moonstone Beach Humboldt), 42035 (Galveston Seawall).

## The gate: FAIL, on one MFR cycle that NOMADS serves in two versions

- **Totals:** 940 hours replayed. 23 input differences, 4 star differences, 21 face differences; 97 hours filled from a neighbouring hour.
- **Cell choice:** every new node is a wet cell, and the cell `nwps.fetch` samples, in every cycle read.
- **Where:** all 23, 21 and 4 are on MFR's 2026-11-07 12Z cycle: Bandon Beach (11 hours) and Hubbard Creek (12), from 11-07 12Z to 23Z.
  - Every other hour matches: 917 of 940, at all five offices, on every other cycle, MFR's included.
- **Examples, stored → expected:**

| Spot, hour | hs | tp | dp | swell_hs | stars |
|---|---|---|---|---|---|
| Bandon, 11-07 18Z | 1.283 → 1.314 | 13.79 → 13.70 | 275 → 275 | 1.041 → 1.236 | 3.0 → 3.5 |
| Bandon, 11-07 23Z | 0.133 → 0.111 | 15.761 → 16.268 | 275 → 285 | 0.103 → 0.086 | 1.0 → 0.0 |
| Hubbard Creek, 11-07 12Z | 0.123 → blank | 16.8 → blank | 275 → blank | 0.105 → 0.281 | 0.0 → 1.0 |
| Hubbard Creek, 11-07 20Z | 0.936 → 0.971 | 13.63 → 13.53 | 255 → 255 | 0.771 → 0.909 | 2.0 → 2.5 |

- **The file:** node-preview downloaded 55,879,399 bytes for MFR 2026-11-07 12Z. A few seconds later, its HEAD request to the same URL gave 43,017,313 bytes, last modified 2026-11-07 20:01:05Z.
  - The report prints "SIZE DIFFERS from the file read" for this cycle only.
- **The NOMADS publication logger** (workflow `nwps-publication-log`, every 6 hours) saw the same URL as two different files:
  - at 2026-11-07 22:24Z: 55,879,399 bytes, last modified 2026-11-07 19:07:09Z;
  - at 2026-11-08 06:48Z, 18:11Z and 22:35Z: 43,017,313 bytes, last modified 2026-11-07 20:01:05Z.
  - So MFR posted the cycle twice, 54 minutes apart, and NOMADS still serves either copy more than a day later, depending on which server answers.
- **The pipeline** read that cycle through NOMADS's GRIB filter (`filter_wrnwps.pl`) at 2026-11-07 20:22Z and 2026-11-08 01:52Z.
  - Both reads came after both posts, so either copy could have answered either read.
  - The published hours do not match the 55.9 MB copy, so the pipeline's last read was most likely the 43.0 MB one. That isn't confirmed: the pipeline does not record which copy it got.
- **Run 69's failure** fits the same pattern. The logger saw MFR 2026-11-08 00Z, at the same size (43,017,415 bytes), last modified 03:37:29Z at 06:48Z and 18:11Z, and 07:05:08Z at 22:35Z.
  - The pipeline read it at 08:23Z and 13:50Z. Run 69's differences were all on that cycle's late hours.
- **Every other cycle read** shows one size and one last-modified time in node-preview and in every logger check. Each was last modified before the pipeline's first read of it.

## Each cycle file read, beside the pipeline's reads

Last modified and size as node-preview's HEAD gave them; "read" is the size of the file node-preview downloaded. The pipeline's times are when the full pipeline run requested the cycle's subset.

| Office, cycle | Last modified | NOMADS size | Read | Pipeline read it | Logger saw |
|---|---|---|---|---|---|
| box 11-06 12Z | 11-06 19:34:51Z | 46,206,414 | same | 11-06 20:16Z, 11-07 01:47Z | one version |
| box 11-07 00Z | 11-07 07:35:17Z | 46,200,664 | same | 11-07 08:17Z, 13:46Z | one version |
| box 11-07 12Z | 11-07 19:28:52Z | 46,198,571 | same | 11-07 20:17Z, 11-08 01:47Z | one version |
| box 11-08 00Z | 11-08 07:33:40Z | 46,208,242 | same | 11-08 08:17Z, 13:46Z | one version |
| box 11-08 12Z | 11-08 19:28:23Z | 46,231,372 | same | 11-08 20:17Z, 20:43Z | one version |
| eka 11-06 12Z | 11-06 18:39:03Z | 12,492,328 | same | 11-06 20:17Z, 11-07 01:48Z | one version |
| eka 11-07 00Z | 11-07 06:44:16Z | 12,492,592 | same | 11-07 08:18Z, 13:47Z | one version |
| eka 11-07 12Z | 11-07 18:40:05Z | 12,492,532 | same | 11-07 20:18Z, 11-08 01:48Z | one version |
| eka 11-08 00Z | 11-08 06:37:40Z | 12,492,760 | same | 11-08 08:18Z, 13:47Z | one version |
| eka 11-08 12Z | 11-08 19:21:49Z | 9,541,684 | same | 11-08 20:18Z, 20:44Z | one version |
| gyx 11-06 18Z | 11-07 01:08:20Z | 22,890,956 | same | 11-07 01:49Z | one version |
| gyx 11-07 00Z | 11-07 07:05:31Z | 22,891,265 | same | 11-07 08:18Z | one version |
| gyx 11-07 06Z | 11-07 13:07:09Z | 22,889,483 | same | 11-07 13:47Z | one version |
| gyx 11-07 12Z | 11-07 19:06:32Z | 22,890,255 | same | 11-07 20:18Z | one version |
| gyx 11-07 18Z | 11-08 01:09:04Z | 22,888,799 | same | 11-08 01:48Z | one version |
| gyx 11-08 00Z | 11-08 07:05:17Z | 22,891,775 | same | 11-08 08:18Z | one version |
| gyx 11-08 06Z | 11-08 13:08:47Z | 22,891,708 | same | 11-08 13:47Z | one version |
| gyx 11-08 12Z | 11-08 19:06:02Z | 22,895,976 | same | 11-08 20:18Z, 20:44Z | one version |
| hgx 11-06 12Z | 11-06 16:56:10Z | 64,528,946 | same | 11-06 20:19Z, 11-07 01:50Z | one version |
| hgx 11-07 06Z | 11-07 04:43:40Z | 64,608,131 | same | 11-07 08:19Z, 13:48Z | one version |
| hgx 11-07 12Z | 11-07 16:01:40Z | 64,546,564 | same | 11-07 20:19Z, 11-08 01:49Z | one version |
| hgx 11-08 06Z | 11-08 03:50:38Z | 64,291,444 | same | 11-08 08:20Z, 13:48Z | one version |
| hgx 11-08 12Z | 11-08 16:49:46Z | 64,262,461 | same | 11-08 20:19Z, 20:46Z | one version |
| mfr 11-06 12Z | 11-06 18:17:07Z | 43,017,079 | same | 11-06 20:22Z, 11-07 01:53Z | one version |
| mfr 11-07 00Z | 11-07 04:30:19Z | 43,017,343 | same | 11-07 08:22Z, 13:50Z | one version |
| **mfr 11-07 12Z** | 11-07 20:01:05Z | 43,017,313 | **55,879,399** | 11-07 20:22Z, 11-08 01:52Z | **two: 55,879,399 (19:07:09Z) and 43,017,313 (20:01:05Z)** |
| mfr 11-08 00Z | 11-08 07:05:08Z | 43,017,415 | same | 11-08 08:23Z, 13:50Z | **two times: 03:37:29Z and 07:05:08Z, same size** |
| mfr 11-08 12Z | 11-08 18:49:32Z | 55,879,423 | same | 11-08 20:22Z, 20:50Z | one version (one check) |

- Pipeline runs read: forecast-pipeline full runs 42228102811, 42286818495, 42325763298, 42354707686, 42378422421, 42410186550, 42435628220, 42462165785, 42486481328, 42519213698, 42521404496.
- Logger runs read: nwps-publication-log 42419895956, 42455803648, 42508125265, 42530404121.

## Against each spot's own references (replayed old point, then new)

| Spot | Face, ft | Stars | New / each neighbour (face) | Buoy, past 48 h (median node / buoy) |
|---|---|---|---|---|
| Bandon Beach | 3.19 → 13.24 | 2.31 → 2.79 | Whiskey Run 0.974, Bastendorff 1.106 (mean 1.040) | 46015, 4.65 m mean: 0.193 → 0.758 (39% within 20%) |
| Moonstone Beach Humboldt | 4.72 → 11.72 | 3.03 → 3.66 | Trinidad 1.000, Agate 0.985 (mean 0.993) | 46244, 3.48 m: 0.270 → 0.897 (77% within 20%) |
| Short Sands Beach York | 2.05 → 2.74 | 1.13 → 1.27 | Long Sands 1.117, Harbor 1.025 (mean 1.071) | |
| Revere Beach | 1.18 → 2.51 | 1.02 → 1.35 | Nahant 1.040, Winthrop 1.204, Lynn 1.155 (mean 1.133) | |
| Hubbard Creek | 2.85 → 12.32 | 1.84 → 3.08 | no neighbours | 46015, 4.66 m: 0.172 → 0.641 (29% within 20%) |
| Galveston Seawall | 0.88 → 1.29 | 0.82 → 0.92 | none (61st Street is the same cell) | 42035, 0.68 m: 0.514 → 0.730 (33% → 5% within 20%) |

- **None averages over 1.15 of its own neighbours.** Revere Beach is 1.204 of Winthrop Jetty and 1.155 of Lynn Beach on its own; 1.138 in run 69.
- **Galveston Seawall against 42035,** by sea state:
  - buoy 0.2 to 0.3 m (15 hours): the shared cell reads 1.30, the old point 0.97;
  - buoy 0.5 to 1.6 m (27 hours): 0.65 against 0.44, none within 20%.
  - Run 69 gave 1.28 / 0.97 and 0.67 / 0.45.
- **Bandon's and Hubbard Creek's figures include MFR 11-07 12Z's 12 hours each,** replayed from the 55.9 MB copy.
  - Without them, Bandon reads Whiskey Run 0.977 and Bastendorff 1.107 (154 hours), with stars 2.32 → 2.82.
  - Hubbard Creek's face is 2.85 → 12.41 ft and stars 1.84 → 3.05 (143 hours).

## Homepage, "Best conditions right now"

- **Unchanged:** the same ten spots before and after:
  - Shelter Cove, Agate Beach Newport, Brookings Beach, Nelscott Reef, Seaside Cove, Mendocino, Westport and Agate Beach at 5.0 stars;
  - Crescent City Beach and Sharp Park at 4.5.
- None drops out and none comes in.
