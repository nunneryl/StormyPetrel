# Surf-height merge gate, run 54

- Run: sw1-raycast run 54, mode `surf-preview`, commit 4036ad5355befb98299ee2150022e34ed5d28d2c
  (PR #283), 2026-10-27 18:06Z to 18:12Z. The replay starts at 2026-10-27 18:07:03Z. The archive
  runs from 2026-09-07 00:00Z to 2026-10-27 18:00Z.
- Artifact `surf-preview-54`, sha256 279728f1ce50d664e1c4cdef9b46a475a63fd3f285bd74c9134f6d83574cf063
  (expires 2027-01-25).
- The only error was the NWS API's 404 for zone CAZ364, as in runs 52 and 53.

## Files

- `surf_preview.json` and `face_compare.json`: the artifact's JSON, byte for byte.
- `face_compare_frf.csv`: the artifact's file, byte for byte.
- `surf_preview_hours.csv.gz` and `face_compare_srf.csv.gz`: the artifact's CSVs, gzipped. Each
  decompresses to the artifact's file byte for byte.
  - `surf_preview_hours.csv`: 58,393 rewritten hours, each with its height before and after.
  - `face_compare_srf.csv`: 265,469 rows. Since run 54 each row carries `display`, `source` and
    `calibrated`.

## The gate: PASS

Replay (82,784 published hours):

| Check | Result |
|---|---|
| Hours rewritten | 58,393 (spots rewritten: 457) |
| Star differences | 0 |
| Other-field differences | 0 |
| Unexpected writes (face moved where it should not, or not where it should) | 0 |
| Hours that became calibrated | 0 |
| Hours that lost calibrated | 0 |
| Uncalibrated hours whose published face differed from the raw one | 0 |

- The 141 calibrated spots kept their faces.
- 6,235 hours were kept because another rater fed them.
- No hour lacked a face or a period.
- Badges are 18,156 calibrated and 64,628 model, the same before and after.

Reproduction of run 53: 0.70 x typical on uncalibrated spots, mainland headline hours up to
2026-10-27 16:00Z. Every region matches run 53 exactly in count and shares. Overall: 171,534
hours, 60.4% inside, 34.0% below and 5.6% above.

## Against the NWS Surf Zone Forecast ranges (mainland headline hours, 7 Sep to 27 Oct 2026)

Inside / below / above, for today's published height and for the height the site will show:

| Region | Hours | Published | Display |
|---|---|---|---|
| All | 234,897 | 61.0 / 30.9 / 8.1 | 59.9 / 35.7 / 4.4 |
| Carolinas and Georgia | 25,482 | 77.9 / 3.4 / 18.7 | 81.4 / 7.0 / 11.7 |
| Florida Atlantic | 29,339 | 74.2 / 4.4 / 21.4 | 77.4 / 10.3 / 12.3 |
| Gulf | 10,610 | 73.7 / 20.9 / 5.4 | 70.5 / 25.3 / 4.2 |
| Mid-Atlantic | 25,090 | 71.1 / 6.2 / 22.8 | 78.8 / 12.2 / 9.0 |
| New England | 34,912 | 41.0 / 58.4 / 0.6 | 33.9 / 65.4 / 0.7 |
| Northern California | 32,477 | 34.2 / 64.2 / 1.6 | 34.1 / 64.4 / 1.4 |
| Pacific Northwest | 11,497 | 41.4 / 57.9 / 0.7 | 17.1 / 82.9 / 0.0 |
| Puerto Rico | 12,144 | 17.4 / 81.5 / 1.1 | 9.6 / 90.4 / 0.0 |
| Southern California | 53,346 | 81.9 / 16.8 / 1.3 | 82.5 / 16.8 / 0.8 |

- Uncalibrated spots alone: 172,022 hours. Published 61.8 / 27.6 / 10.7; display 60.3 / 34.0 / 5.7.
- Hawaii (HFO's table; whether its numbers are face heights is not established): 27,500 hours.
  Published 40.0 / 6.1 / 53.9; display 53.0 / 13.3 / 33.7.

The methodology post's "How our height compares" paragraph takes its figures from the "All"
display row and the four East Coast and Southern California display rows. Southern California's
inside share is 82.465% computed from the CSV, so the post says 82%.
