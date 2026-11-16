# Run 77: the wind-sea switch, outside the window and 90 deg or more off the facing (sw1-raycast mode period-preview)

- **Run:** sw1-raycast run 77 (Actions run 43913298108), branch `claude/practical-dirac-bshfbr` at `d5b7cd7`, 2026-11-16 16:58Z. Passed.
- **Artifact:** `period-preview-77`, zip sha256 `1d22ea86bc1aee128ad3551b43091b03053fec83453627f6e17f92281e678972` (GitHub's digest says the same).
- **Stored gzipped since 2026-11-12:** `period_preview.json`, `period_preview_away.csv`, `period_preview_archive.csv`, `period_preview_hours.csv`, `face_compare.json` and the job log `run_log.txt` (timestamps cut) are kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte); the sha256 of every saved file is in `../CHECKSUMS.sha256`.
- **Compared:** the rule on `main` (#284 with run 57's window exclusion) against the rule adopted 2026-11-16: also no switch when the wind sea is outside the window (interpret.in_any_arc, padded edges inclusive) and 90 deg or more off the spot's facing. Archive 2026-09-07 to 2026-11-16 16:00Z; published window from 16:58Z. Run 76 (`../run-76`) measured the facing alone.

## The gate

Passed: 0 star differences, 0 other-field differences, 0 unexpected, 0 badge flips, run 54's NWS table reproduced. 2,570 published rows lose the wind-sea mark (2,555 change their surf height), and nothing else changes.

## Switches (archive)

75,642 under `main`, 58,197 under the adopted rule: 17,445 removed. 987 hours inside the window though 90 deg or more off the facing keep switching (run 76 removed them). On the removed hours with a buoy reading (8,910), the shown period within 2 s of the buoy's dominant period: 81.3% before, 36.7% after. The buoys are offshore and see the departing sea, so this check cannot judge the change; the NWS range below can.

## Against the NWS Surf Zone Forecast range (headline hours the rule unswitches, whole archive)

| Group | n | Inside before | Inside after | Below before / after | Above before / after |
|---|---|---|---|---|---|
| Mainland, all regions | 2,312 | 41.0% | 45.7% | 51.0% / 42.3% | 8.0% / 12.0% |
| New England | 688 | 5.5% | 20.1% | 94.5% / 79.9% | 0.0% / 0.0% |
| Gulf | 778 | 74.3% | 74.4% | 16.1% / 11.6% | 9.6% / 14.0% |
| Carolinas and Georgia | 259 | 69.1% | 56.8% | 0.4% / 0.0% | 30.5% / 43.2% |
| Mid-Atlantic | 249 | 27.7% | 50.6% | 66.7% / 43.4% | 5.6% / 6.0% |
| Puerto Rico | 125 | 0.0% | 0.0% | 100% / 100% | 0% / 0% |
| Northern California | 78 | 7.7% | 12.8% | 92.3% / 87.2% | 0% / 0% |
| Southern California | 55 | 94.5% | 63.6% | 0% / 0% | 5.5% / 36.4% |
| Florida Atlantic | 48 | 54.2% | 43.8% | 16.7% / 12.5% | 29.2% / 43.8% |
| Pacific Northwest | 32 | 3.1% | 3.1% | 96.9% / 96.9% | 0% / 0% |
| Hawaii (by state) | 2,368 | 36.4% | 30.2% | 7.2% / 6.2% | 56.4% / 63.7% |
| All states, Hawaii included | 4,680 | 38.7% | 37.8% | 28.8% / 24.0% | 32.5% / 38.1% |

Mainland moves: 200 below -> inside, 94 inside -> above, 2 above -> inside. By state: `run_log.txt.gz` section 6h, or `period_preview.json.gz` `facing.nws`.

## Surf height on the published rows the rule unswitches

2,555 rows move: 2,454 up, 101 down. Change after - before: median +0.26 ft (+7.4%), p10 +0.08 ft (+3.0%), p90 +0.43 ft (+35.6%), range -0.35 to +0.95 ft (-16.7% to +88.4%). Largest drops: the Rincón spots in Puerto Rico (Maria's, Steps Beach Rincon, Tres Palmas 2.34 -> 1.99 ft; Indicators, Rincon Domes 1.78 -> 1.51). Largest rises: Pine Trees Hawaii (4.71 -> 5.66), Honolua Bay (6.10 -> 6.94), Barbers Point and White Plains (3.1 -> 3.8), and the Cape Cod beaches (Nauset 0.78 -> 1.38, Coast Guard and Marconi 0.7 -> 1.3).

## Facing sensitivity

5,114 of the 17,445 removed hours (29%) have the wind sea only 90 to 110 deg off the facing, where a 20 deg facing error would flip them. All of Kanaha Beach's 189 and Hoʻokipa's 184; most of Hammonds' and Miramar's (96 of 113), Laniakea's (71 of 77), Chun's Reef's (67 of 80) and Rockpile's (64 of 64). Top 20: section 6g, or `facing.archive_sensitivity`.
