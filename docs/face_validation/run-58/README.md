# Run 58: NWPS cells at Hatteras (sw1-raycast mode nwps-cells)

- **Run:** sw1-raycast run 58, commit `e244d2a`, dispatched 2026-10-29 13:29Z.
- **Artifact:** `nwps-cells-58`; `nwps_cells.json` and `nwps_cells.csv` saved here byte for byte (the CSV gzipped).
- **What it read (read-only):**
  - the NWPS-sized hours since 2026-09-07;
  - the MHX CG1 grid from the cycles 2026-10-27 12Z, 10-28 00Z, 10-28 12Z and 10-29 00Z;
  - other offices' grids for the flagged spots.
- **Run 59:** the same mode on the same commit (19:01Z). It gave the same Hatteras pair figures; its neighbour scan had a few more hours.
- **Depths** (`etopo_join.txt`): ETOPO 2022 15 arc-second, from ERDDAP (`ETOPO_2022_v1_15s`), joined afterwards.
  - For each cell: the depth at the point nearest its centre, and over the cell's footprint, the share of land and the median water depth.
  - Columns of each cell line: the cell's Hs as the mean over the 4 cycles; that Hs over the pier's sample cell, overall and per cycle; peak period; direction.

## Rodanthe, Salvo and S-Turns against Outer Banks Fishing Pier (NWPS Hs, hour for hour)

| Spot | Paired hours | Under 0.4 of pier | Median ratio | Pier at 1 m or more: under 0.4 | Median |
|---|---|---|---|---|---|
| Rodanthe | 1294 | 1189 (92%) | 0.16 | 630 of 630 | 0.11 |
| Salvo | 1393 | 678 (49%) | 0.41 | 534 of 674 (79%) | 0.26 |
| S-Turns | 1378 | 1237 (90%) | 0.18 | 672 of 672 | 0.13 |

- **By month** (pier at 1 m or more): Rodanthe 0.11, 0.11, 0.12; S-Turns 0.14, 0.12, 0.15; Salvo 0.27, 0.25, 0.35, for September, October and the early-November forecast hours.
- **Salvo depends on size.** With the pier under 1 m, Salvo read under 0.4 in 144 of 719 hours. Rodanthe did in 559 of 664, S-Turns in 565 of 706.

## The grid

The MHX CG1 cells are about 1.8 km square. Rows are listed north first:

| Row (lat) | Island column (-75.4744) | Beach column Hs (-75.45482) | One column seaward (-75.43524) | Beach / seaward |
|---|---|---|---|---|
| 35.65619 | land | 1.21 m | 1.25 m | 0.97 |
| 35.63992 | land | 0.94 m | 1.24 m | 0.76 |
| 35.62365 | land | 0.31 m | 1.23 m | 0.25 |
| 35.60738 (S-Turns) | **water, sound values** | 0.21 m | 1.22 m | 0.17 |
| 35.59110 (Rodanthe) | **water, sound values** | 0.20 m | 1.22 m | 0.16 |
| 35.57483 | **water, sound values** | 0.32 m | 1.21 m | 0.26 |
| 35.55856 | **water, sound values** | 0.33 m | 1.21 m | 0.27 |
| 35.54229 (Salvo) | land | 0.45 m | 1.21 m | 0.37 |
| 35.52602 | land | 1.06 m | 1.23 m | 0.87 |
| 35.50974 | land | 1.20 m | 1.25 m | 0.96 |

- **The island is missing on the grid.** From 35.559 to 35.607 (about 6.5 km), MHX's grid has water where the island is. Those cells hold Pamlico Sound values: Hs 0.1 m at a 1.3 to 1.4 s period.
- **The beach column collapses alongside it.** Beside that stretch, and one row past each end, the beach column reads 16 to 37% of the cell seaward. The period there stays ocean-like (8.0 s), and ETOPO puts those cells in 10 to 13 m, with little land in their footprints (Rodanthe 20%, Salvo 10%, S-Turns 5%; the pier's 10%).
- **Elsewhere it doesn't.** North and south, where the island is land on the grid, the beach column keeps 87 to 97%. So does the pier's sample cell: 0.96 of the cells within two seaward, at 11.8 m.
- **Every cycle.** The ratio is the same in each of the four cycles: Rodanthe 0.17 to 0.18 of the pier, S-Turns 0.18 to 0.20, Salvo 0.38 to 0.42.
- **No depth field.** The GRIB carries no depth. Its one unnamed static field is 0 in near-shore cells and blank offshore.

## The move (`pipeline/data/nwps_node_overrides.json`)

The rule for each new cell:
- the nearest cell within 30 deg of the spot's facing;
- at least 10 m deep at its centre (ETOPO 2022);
- NWPS Hs at least 0.9 of the cell one column further seaward, in every cycle read.

Depth alone would keep the current points.

| Spot | From | To | Distance and bearing | ETOPO at centre | NWPS Hs vs pier (per cycle) | Vs next cell seaward |
|---|---|---|---|---|---|---|
| Rodanthe | 35.5911, -75.45482 | 35.5911, -75.43524 | 2.46 km at 99 deg | 16.9 m | 1.09 (1.09 to 1.10) | 0.98 |
| Salvo | 35.54229, -75.45482 | 35.52602, -75.43524 | 3.00 km at 112 deg | 10.9 m | 1.10 (1.09 to 1.12) | 0.97 |
| S-Turns | 35.60738, -75.45482 | 35.60738, -75.43524 | 2.68 km at 91 deg | 16.5 m | 1.10 (1.09 to 1.11) | 0.98 |

- **Salvo's same-row cell seaward** is 9.3 m at its centre, so the next row south is used.
- **Gate:** sw1-raycast mode node-preview, with `baseline_ref` main.
