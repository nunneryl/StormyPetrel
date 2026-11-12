# Run 64: buoy assignment from the runner (sw1-raycast mode buoy-assign)

- **Run:** sw1-raycast run 64 (Actions run 41244809449), branch `claude/practical-dirac-bshfbr` at `b032574`, 2026-10-31 21:42Z.
- **Artifact:** `buoy-assign-64`, sha256 `0dfa31c2ab0334a4e99f91cdbe2c251309b4959e49b630c3eae3fee06b56bba6`; `buoy_assign.json` saved here byte for byte.
- **Stored gzipped since 2026-11-12:** `buoy_assign.json` is kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte; the sha256 of each is in `../CHECKSUMS.sha256`). Nothing else changed.
- **What it did:** reran enrich's buoy rule (`enrichment/buoys.compute_nearest_buoy`) for the 39 spots whose buoy db_import drops and the 173 with no buoy.
  - The rule starts from the seaward-adjusted point and looks only at NDBC wave stations: the 10 nearest, within the region's cap, with line of sight over GSHHG land.
  - It ran on two station lists:
    - **fresh:** NDBC files downloaded during the run, 164 wave stations;
    - **cached:** the runner's geodata copy from 2026-07-20, 200 wave stations.
- **Cross-check:** the step-by-step diagnosis agreed with the real function on every spot, in both lists.
- `proposal.csv`: one row per spot, with the current buoy, both lists' choices, and the recommended assignment.

## The 39 dropped spots (fresh list)

- **21 keep their buoy,** with the distance corrected.
- **15 get a different buoy.**
- **3 end up with none:** Devereaux, Revere Beach and Miramar.
  - Their buoys, 44013 and 46053, are not wave stations in either list.
  - No other wave buoy within the cap has line of sight.

## The 173 with no buoy (fresh list)

- **105 would get a buoy.**
- **68 would not:**
  - 62 have no line of sight (Hawaii 19, Puerto Rico 12);
  - 6 are beyond the cap;
  - none for lack of wave stations.
- **Cached list:** 106 would get a buoy; 64 have no line of sight and 3 are beyond the cap.

## Fresh against cached

- **29 spots differ.**
- **Buoys that report in only one list:**
  - The fresh list counts a station only if it reported a wave height at the moment of download.
  - It has new buoys the July copy lacks: 41114, phct2, 46229.
  - It lacks buoys that were not reporting at that moment:
    - 44084 is reporting today (the Bethany Beach page shows an Oct 31 reading);
    - 51205 last reported Oct 13 (the Kanaha Beach and Hoʻokipa pages);
    - 46453, 46041, 42358 and 46054 were not checked.

## Also found

- **New York gets the Great Lakes cap.** `buoys._regional_cap_km` gives every New York spot north of 40°N and west of 70°W the 100 km Great Lakes cap, because New York is in `_GREAT_LAKES_STATES`. That includes Long Island and Montauk. Mecox Beach is out of reach at 100.89 km for that reason.
- **The Nearest buoy box** shows the station id and the reading, but no distance and no warning when the reading is old. Kanaha Beach and Hoʻokipa show 51205's Oct 13 reading as current.
- **117 other spots already show a buoy more than 60 km away,** 39 of them more than 100 km. Siesta Key's is 380 km.
