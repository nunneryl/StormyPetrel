# Run 83: node preview of the six neighbours #353 left out, and two New Jersey cells blank at low tide (sw1-raycast mode node-preview)

- **Run:** sw1-raycast run 83 (Actions run 45627771658), branch `claude/run83-neighbour-points` at `44bc35f`, 2026-11-25 13:51–13:56Z, `baseline_ref` main. The job succeeded: the gate passed.
- **Artifact:** `node-preview-83`, zip sha256 `1e0f9d2991285577d60c354a5bf6ecc00229c74172c491baaed9fd7b57c2a605` (GitHub's digest). Both files are saved here.
- **Stored gzipped since 2026-11-12** (the rule for every saved file): `node_preview.json` and `node_preview_hours.csv` are kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte); the sha256 of each is in `../CHECKSUMS.sha256`.
- **Window:** the published forecasts from 2026-11-25T13:52Z, replayed from 2026-11-23T13:52Z because buoys were named. Cycles read: ilm 2026112312, 2026112318, 2026112400, 2026112406, 2026112412, 2026112418, 2026112500; mhx 2026112312, 2026112400, 2026112412, 2026112500; phi 2026112312, 2026112318, 2026112400, 2026112406, 2026112412, 2026112418, 2026112500; sju 2026112312, 2026112318, 2026112400, 2026112406, 2026112412, 2026112418, 2026112500.
- **Moved in the run:** 8 spots. **Kept:** 7. **Held:** Surfers Beach Aguadilla (below).
- **Why these spots:**
  - **Six neighbours run 82 left out of its bar.** Their own points are within 400 m of the GSHHG shore or on land: Dogmans Rincon (inside the outline), Surfers Beach Aguadilla (391 m), Fort Fisher (321 m), Wrightsville Beach (316 m), North Pea Island (395 m) and Bath House (170 m).
  - **Two New Jersey spots whose cells go blank at low tide.** Long Beach Township and Sea Bright are blank for 4 to 19 hours at a time in the 00Z and 12Z phi cycles, longer than `series_hour`'s one-hour fill, so hours had no surf height (26 Nov 06–08Z, #362).
- **The rule** is runs 58, 67 and 82's:
  - the nearest cell within 30 deg of the facing;
  - at least 10 m deep by ETOPO 2022;
  - at least 0.9 of the next cell seaward;
  - wet every hour of every cycle read (for the New Jersey two, four cycles including two 00Z/12Z);
  - no GSHHG land on the line from the spot once it reaches water.

## The gate

- **Whole run:** 1275 hours, 0 input, 0 star and 0 face differences.
  - 76 hours were filled from a neighbouring hour (blank in the file): Fort Fisher 24, Long Beach Township 25 and Sea Bright 27, all at their old cells.
  - No double-posted cycle, and no old node on land.
  - Every new node is a wet cell and the cell nwps.fetch samples, in every cycle read.
- **Not replayed** (another rater wrote the hour):
  - Long Beach Township: 62 WAVEWATCH III, 2 buoy.
  - Sea Bright: 68 WAVEWATCH III, 1 buoy.
  - Fort Fisher: 24 WAVEWATCH III.

  For the New Jersey two these are the blank stretches at the old cell: the hours the NWPS override could not fill. At the new cells, which are wet every hour, NWPS rates them.

## Per spot, old point -> new point

- **Face** is face_ft_raw, mean over the replayed hours.
- **Neighbours** are the unmoved NWPS spots within 30 km whose own sample point is at least 400 m from the GSHHG shoreline and not on land; the face is their mean.
- **Buoy** is the median of node / buoy over the replayed hours already past, and the share within 20%.

| Spot | Hours | Stars | Face, ft | Neighbours (face), new (before) | Buoy: node / buoy, within 20% | Kept |
|---|---|---|---|---|---|---|
| Dogmans Rincon | 179 | 0.11 -> 1.04 (up 171, down 0) | 0.76 -> 2.90 | 0.966 (0.252) | 41115: 0.235 -> 1.064, 0% -> 83% | yes |
| Fort Fisher | 155 | 1.28 -> 1.75 (up 88, down 1) | 1.80 -> 4.95 | 1.020 (0.371) | 41110: 0.406 -> 0.920, 0% -> 76% | yes |
| Wrightsville Beach | 179 | 1.80 -> 1.81 (up 22, down 17) | 3.62 -> 4.88 | 1.062 (0.786) | 41110: 0.730 -> 0.909, 42% -> 79% | yes |
| North Pea Island | 179 | 1.82 -> 1.82 (up 16, down 12) | 5.35 -> 6.36 | 1.015 (0.854) | 44095: 0.796 -> 0.831, 49% -> 55% | yes |
| Bath House | 179 | 1.89 -> 1.86 (up 10, down 16) | 5.66 -> 6.36 | 1.071 (0.952) | 44056: 0.906 -> 1.002, 81% -> 85% | yes |
| Long Beach Township | 115 | 1.51 -> 1.81 (up 60, down 2) | 3.37 -> 7.75 | 1.261 (0.548) | 44091: 0.509 -> 0.844, 0% -> 52% | yes |
| Sea Bright | 110 | 1.09 -> 2.03 (up 82, down 1) | 1.73 -> 6.61 | 1.061 (0.277) | 44065: 0.385 -> 1.043, 0% -> 81% | yes |
| Surfers Beach Aguadilla | 179 | 2.01 -> 2.08 (up 32, down 6) | 3.70 -> 4.33 | 1.365 (1.166) | 41121: 1.065 -> 1.097, 69% -> 71% | **held** |

- **Kept: the seven that pass the buoy-first bar.** Each reads 0.83 to 1.06 of its buoy, against 0.24 to 0.91 before.
  - **North Pea Island** passes at 0.831, as Oregon Inlet passed at 0.829 in run 82. Its gain is small (0.80 before), but the move takes its point off the shore (395 m) for a cell 15 m deep.
  - **Bath House** already passed (0.91). The move takes it from 170 m off the shore to a 16.5 m cell, at 1.00 of its buoy.
- **Long Beach Township and Sea Bright pass the bar outright** (0.84 and 1.04), so the exception allowed for the whole New Jersey coast reading low was not needed.
  - Long Beach Township's neighbour figure, 1.26, is high because those neighbours read low themselves. In the 24–25 Nov cycles they read 0.67 to 0.76 of NWPS at 44091, and Barnegat Light 0.31; Sea Bright's read 0.53 to 0.73 at 44065.
  - That is why a whole-coast New Jersey review goes on the backlog, beside the South Carolina and Georgia one.
- **Held: Surfers Beach Aguadilla.** Its own point already read 1.07 of buoy 41121 (69% of hours within 20%). The new cell reads 1.10, so the move does not bring it closer to its buoy, and its point stays.
  - Against its north-facing neighbours alone (Shacks Beach, Jobos Beach Isabela, Middles Isabela, Playa Guajataca), the old point reads 0.87 to 0.90 and the new 1.02 to 1.05.
  - The all-neighbour figure (1.17 -> 1.37) is pulled up by the west-facing Rincon and Aguadilla spots.

## The surf height the page shows, and the sample point's depth

| Spot | Cell depth, m | Surf shown, ft (old -> new) | From the cell's depth, ft | Ratio |
|---|---|---|---|---|
| Dogmans Rincon | 639.6 | 0.90 -> 2.67 | 2.62 | 0.979 |
| Fort Fisher | 11.2 | 1.82 -> 4.04 | 4.09 | 1.012 |
| Wrightsville Beach | 15.7 | 3.17 -> 4.00 | 4.16 | 1.041 |
| North Pea Island | 15.2 | 4.33 -> 4.95 | 5.14 | 1.038 |
| Bath House | 16.5 | 4.49 -> 4.95 | 5.16 | 1.043 |
| Long Beach Township | 15.8 | 3.01 -> 5.83 | 6.07 | 1.042 |
| Sea Bright | 15.7 | 1.70 -> 5.06 | 5.27 | 1.041 |

- As in run 82, the 10 m assumption reads the shelf cells (11 to 17 m) about 1 to 4% low and Dogmans Rincon's 640 m cell 2% high over this window. Shoaling from each moved spot's recorded depth is already on the backlog.

## Homepage, "Best conditions right now"

- **No change:** none of the eight is in the ten before or after.
