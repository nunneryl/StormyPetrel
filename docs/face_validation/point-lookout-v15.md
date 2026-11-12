# Point Lookout under NWPS v1.5: resolved without a move (2026-11-12)

- **The question.** Point Lookout's sample cell (40.58319, -73.58416) was 40% land and dried at
  low tide: wet only 127 to 139 of 145 hours per cycle under NWPS v1.4 (run 67). The qualifying
  cells further out read too high against Lido Beach and Jones Beach: 1.163 (run 68) and, for the
  4.37 km cell at 209 deg, 1.191 (run 69), both over 1.15, so it was held (run 70).
- **What v1.5 changed.** On the OKX cycles of 2026-11-11 12Z and 2026-11-12 00Z (NOMADS), every
  cell within 3 of the current one, beside run 67's v1.4 cells:
  - the current cell is wet every hour of both cycles (145 of 145);
  - it reads 1.00 of Lido Beach's cell and 0.86 of Jones Beach's, hour by hour (Hs);
  - the 4.37 km cell reads 1.52 of Lido's and 1.29 of Jones's on the same hours;
  - the other cells that turned wet lie in the bay behind the barrier (0.21 to 0.26 of Lido).
- **Decision.** No move. The current cell is now wet every hour and in line with its neighbours,
  so node-preview run 73 was not run, and the branch commit that set up the 4.37 km cell was
  removed. Buoy 44065 (New York Harbor Entrance) reports waves and can check it later, through
  node-preview's NDBC fallback.
