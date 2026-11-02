# Run 65: the nine held spots, with the "last 7 days" rule (sw1-raycast mode buoy-assign)

- **Run:** sw1-raycast run 65 (Actions run 41542268600), branch `claude/practical-dirac-bshfbr` at `d15a605`, 2026-11-02 23:02Z.
- **Artifact:** `buoy-assign-65`, zip sha256 `e4d69110c93c23318cb9159f0455efe92e2f845094220acc1782f3ec32f4f5eb`. Both files are saved here byte for byte:
  - `buoy_assign.json` (the rule's answers);
  - `buoy_assign_recent_wave.json` (the 7-day check of each station).
- **Spots:** Kalaloch Beach, La Push First, Second and Third Beach, Galveston Seawall, 61st Street Galveston, Ventura Harbor, Ventura Point and Mecox Beach.
- **What it did:**
  - For every active NDBC station within a held spot's cap (190 stations), it read the station's recent-observations file (realtime2) and recorded:
    - the newest row;
    - the last wave height;
    - how many rows in the last 7 days had a wave height.
  - 31 stations had a wave height in the last 7 days.
  - It reran enrich's buoy rule (`enrichment/buoys.compute_nearest_buoy`) on four station lists:
    - **fresh+7d (the headline answer):** NDBC's station list as downloaded during the run, plus every station with a wave height in the last 7 days. 169 wave stations.
    - **cached+7d:** the runner's cached copy from 2026-07-20, plus the 7-day stations. 209.
    - **fresh:** the downloaded list alone. 157.
    - **cached:** the cached copy alone. 200.
  - The step-by-step diagnosis agreed with the real function on every spot, in every list.
- `proposal.csv`: one row per spot, with all four lists' choices and the proposed assignment.

## The five buoys asked about

| Buoy | Newest row | Last wave height | Wave rows, last 7 days |
|---|---|---|---|
| 46453 | 2026-09-28 19:55Z | 2026-09-28 19:55Z | 0 |
| 46041 | 2026-11-02 16:30Z | 2026-11-02 13:50Z | 460 |
| 42358 | 2026-11-02 21:30Z | 2026-10-24 15:00Z | 0 |
| 46054 | 2026-11-02 20:50Z | 2026-11-02 13:50Z | 556 |
| 44097 | 2026-11-02 22:30Z | 2026-11-02 22:30Z | 328 |

- **46453** has sent nothing since Sep 28.
- **42358** still reports, but its last wave height was Oct 24.
- **46041 and 46054** report waves. Their last wave height was 13:50Z, about 9 hours before the run.

## NDBC's own buoys stopped giving a wave height at about 13:50Z on Nov 2

- **NDBC-run buoys:** 42035, 44065, 46025, 46029, 46041, 46053, 46054 and 46069 all have their last wave height at 13:50Z.
  - Most still sent other readings for hours afterwards.
  - 46087's last wave height was 13:20Z, 44025's 13:40Z and 46088's 14:20Z.
- **CDIP buoys** (462xx, 44097, 46211 and others) gave wave heights up to 22:26–22:30Z.
- **Effect on the downloaded list:** at 23:03Z it counted only 157 wave stations. Without the 7-day rule, eight of the nine spots were sent to a farther buoy, four of them more than 150 km away.

## Mecox Beach

- **Result:** 44097 at 100.89 km passes the line-of-sight check, under the New York cap of 150 km.
- **All four lists agree.** The cached copy's station position gives 100.82 km.
- **Fallback:** 44025 at 101.0 km.

## The proposal (fresh+7d)

| Spot | Buoy | km | Fallbacks | Before |
|---|---|---|---|---|
| Kalaloch Beach | 46041 | 38.51 | 46099, 46029, 46248 | none |
| La Push First Beach | 46041 | 62.12 | 46099, 46243 | none |
| La Push Second Beach | 46041 | 59.92 | 46099, 46211, 46243 | none |
| La Push Third Beach | 46099 | 98.68 | 46211, 46243 | none |
| Galveston Seawall | 42035 | 37.55 | 42092 | 42035 at 42.79 (dropped every run) |
| 61st Street Galveston | 42035 | 41.3 | 42092 | none |
| Ventura Harbor | 46053 | 52.55 | 46054 | 46053 at 57.93 (dropped every run) |
| Ventura Point | 46053 | 49.46 | 46025, 46054, 46219 | none |
| Mecox Beach | 44097 | 100.89 | 44025 | none |

- None is over 150 km.
- **La Push Third Beach:** the rule finds land between it and 46041 (59.39 km), so it takes 46099. First and Second Beach have a clear line to 46041.
- **Where the lists disagree:**
  - Only Mecox Beach gets the same buoy in all four.
  - The cached lists give Kalaloch Beach and La Push Second and Third Beach 46453, which is silent.
  - The downloaded list alone gives eight spots a farther buoy. Four of those are over 150 km:
    - 61st Street Galveston 278.9 km;
    - Galveston Seawall 283.77 km;
    - Kalaloch Beach 163.34 km;
    - La Push First Beach 191.88 km.

## Site-wide

- **This run can't say.**
  - It checked only the stations near the nine spots.
  - It assigned only those nine.
