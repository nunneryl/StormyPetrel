# Face validation

This folder holds the evidence for how the site's surf heights compare with what surfers and the National Weather Service (NWS) report. Each `run-NN/` folder keeps a sw1-raycast artifact byte for byte, with a note. This page sets the rules for surf reports.

## Design rule: surf reports never feed the forecast

Surf reports (`surf_reports`: the size answers, the optional sets answer and the star verdict) are for **checking** the forecast. Nothing the site shows is computed from them.

- **No display reads them.** No rating, height, star, tile, chart, grid or forecast reads `surf_reports` or the star verdict. That covers the page, the API routes that serve forecasts, and the daily reports.
- **No pipeline step reads them.** No step of the forecast pipeline (`.github/workflows/forecast-pipeline.yml`) reads them, and nothing it imports does either.
- **Only validation reads them**:
  - `pipeline/face_compare.py`;
  - `pipeline/surf_preview.py`, the merge gate that calls it;
  - the notes in this folder.
- **The collection path is the only other code that touches them**:
  - `frontend/app/api/reports/route.ts` and `frontend/lib/surfReport.ts` write a report, and read back only the reporter's own row (a revision) and their daily count;
  - `frontend/lib/reportSnapshot.ts` reads the *forecast* for the reported hour;
  - `frontend/components/SurfReport.tsx` is the card.
- **The schema** lives in `pipeline/migrations/`.

**Why.**
- A forecast tuned on reports could be steered by whoever files them.
- A validation figure computed from reports that also shaped the forecast would be marking its own work.
- Keeping reports out of the forecast is what lets their comparison mean anything.

**Enforced by** `pipeline/tests/test_reports_never_feed_forecasts.py`. The test fails if:
- any file outside the lists above mentions `surf_reports`, the report modules or the star verdict;
- any module the forecast workflow runs, or anything it imports, reaches `face_compare` or `surf_preview`;
- the forecast workflow itself mentions reports.

**Changing the rule** needs a decision written here first, then the test changed to match. Do not just add a file to the test's allowlist.

## If reports are ever used for calibration: bad-faith reports

No code does this yet. The rule above forbids it. If that decision is ever reversed, these protections come first:

- **Medians, not means.** One report of "triple overhead" on a knee-high day moves a mean a long way and a median not at all. Summaries should use the median and quartiles of the ordinal answer.
- **A minimum number of distinct reporters**, per spot and per region, before any figure is used. Count distinct `reporter_hash` values, not reports, because one person can file many. The hash's daily salt means one person on different days counts more than once, so treat the minimum as a floor and set it with that in mind. `face_compare.public_totals` already withholds regions under `MIN_PUBLIC_REPORTS` (20) for display.
- **Flag reports far outside what the nearest buoy supports.**
  - Compare each answer with the swell the spot's nearest buoy measured at that hour.
  - Treat an answer several body-scale steps above or below the range that swell can produce as suspect.
  - Hold suspect answers out of calibration, and count them, rather than deleting them.
- **Look for one-sided reporters.** A reporter hash whose answers sit always above, or always below, the forecast, by more than chance allows, is either a systematic reader or someone pushing the number. Weight it down or hold it out, and report how many were.
- **Watch spots with few reporters.** A spot where one or two reporters make up most of the answers can be moved by them alone. Name those spots rather than calibrating them.
