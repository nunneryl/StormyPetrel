# Run 75: buoy assignment with the 250 m line-of-sight standoff (sw1-raycast mode buoy-assign, spots = all)

- **Run:** sw1-raycast run 75 (Actions run 43446786819), branch `claude/practical-dirac-bshfbr` at `c6f87a5` (#323), 2026-11-13 14:52Z. The job's environment shows `SPOTS: all`.
- **Artifact:** `buoy-assign-75`, zip sha256 `942d2222f645b7db759a3bd654ab35109dfab2314e1cb1ea200a9294ef05e7bb` (GitHub's digest says the same).
- **Stored gzipped since 2026-11-12:** `buoy_assign.json` is kept as `.gz` (`gunzip -c` gives the artifact's file byte for byte); the sha256 of every saved file is in `../CHECKSUMS.sha256`. `buoy_assign_outcomes.csv` is made here from it, one line per spot.
- **Scope: all 648 spots** on the roster after #320: 591 with a buoy, 57 without. Outcomes on fresh+7d (gain / loss / change / same / none): 13 / 10 / 160 / 421 / 44, against the roster as it now stands (run 74 compared against the roster before #320).
- **What the fix changed, and what the lists changed.** Every candidate's line of sight was recomputed locally over GSHHG v2.3.7 full resolution (the runner's file) with the standoff at 0 and at 250 m: it agrees with the runner on 3,835 of 3,836 candidates (Narrow River, below). With the standoff at 0, run 75's station list gives every spot the same choice as run 74's: the lists changed nothing, so every difference from run 74 is the fix's.

## The 12 the fix was expected to clear

| Spot | Buoy | km | Run 75 |
|---|---|---|---|
| Trinidad State Beach | 46244 | 25.21 | same |
| Upper Trestles | 46285 | 10.53 | change: 46285 now in sight; the rule prefers 46277 at 7.87 km, nearer |
| Makaha | 51212 | 18.85 | same |
| Richardson Beach | 51206 | 6.68 | same |
| Cape Kiwanda | 46050 | 74.37 | same |
| Short Sands Beach Oswald West | 46278 | 22.55 | same |
| Pipes | 46274 | 4.69 | same |
| Hillsboro Beach | 41122 | 33.89 | same |
| Fenwick Island | 44084 | 8.85 | same |
| Gordon Pond | 44084 | 24.02 | same |
| North Wildwood | 44084 | 56.34 | same |
| Narrow River | 44085 | 34.71 | change: still blocked (see below) |

- **Narrow River stays blocked.** The rule draws from the spot's exact coordinate (41.440273, -71.442171), in the river's mouth; 250 m from it, away from the nearest bank, lands on the far bank, so the rule keeps the spot's own point and the line still crosses 0.15 km of land. Pushed from the report's rounded point (41.44027, -71.44217), 3 m away, it clears, which is how the local trace before the run counted 12. 11 clear.

## Other spots whose choice the fix changed

| Spot | Region | Roster buoy | km | Run 74 (no standoff) | km | Run 75 | km | Outcome |
|---|---|---|---|---|---|---|---|---|
| C Street Ventura | California | 46025 | 60.02 | 46025 | 60.02 | 46053 | 50.52 | change |
| Cardiff Reef | California | 46225 | 13.40 | 46225 | 13.40 | 46274 | 6.02 | change |
| Crystal Cove | California | 46285 | 18.25 | 46253 | 33.39 | 46277 | 29.00 | change |
| Law Street | California | ljpc1 | 7.26 | 46258 | 23.25 | 46225 | 18.96 | change |
| Leadbetter Beach | California | 46025 | 90.98 | 46025 | 90.98 | 46053 | 21.55 | change |
| Lovers Point | California | — | — | — | — | 46236 | 15.16 | gain |
| Malibu Surfrider Beach | California | 46221 | 19.54 | 46221 | 19.54 | 46268 | 10.30 | change |
| Mondos | California | 46251 | 63.60 | 46251 | 63.60 | 46053 | 43.61 | change |
| PB Point | California | ljpc1 | 6.72 | 46258 | 22.81 | 46225 | 18.09 | change |
| Salt Creek | California | 46253 | 43.78 | 46253 | 43.78 | 46277 | 16.71 | change |
| San Elijo State Beach | California | 46266 | 7.10 | 46266 | 7.10 | 46274 | 5.23 | change |
| Strands Beach | California | 46253 | 44.48 | 46253 | 44.48 | 46277 | 15.61 | change |
| Swamis | California | 46266 | 8.59 | 46266 | 8.59 | 46274 | 3.66 | change |
| Ten Mile Beach | California | 46213 | 117.85 | 46213 | 117.85 | 46014 | 39.01 | change |
| Tourmaline | California | 46258 | 23.11 | 46258 | 23.11 | 46225 | 18.57 | change |
| Zuma Beach | California | 46251 | 73.80 | 46025 | 36.07 | 46221 | 23.38 | change |
| Dewey Beach | Delaware | — | — | — | — | 44084 | 18.05 | gain |
| Rehoboth Beach | Delaware | — | — | — | — | 44084 | 19.66 | gain |
| Bathtub Beach | Florida | 41009 | 147.03 | 41009 | 147.03 | 41114 | 42.40 | change |
| Jupiter Inlet | Florida | — | — | — | — | 41114 | 70.21 | gain |
| Lake Worth Pier | Florida | — | — | — | — | 41122 | 68.23 | gain |
| Barbers Point | Hawaii | 51211 | 15.23 | 51211 | 15.23 | 51212 | 5.35 | change |
| Kiahuna Beach | Hawaii | — | — | — | — | 51212 | 148.73 | gain |
| Kolekole Beach Park | Hawaii | — | — | — | — | 51206 | 19.38 | gain |
| Poipu Beach | Hawaii | — | — | — | — | 51212 | 148.22 | gain |
| Puaena Point | Hawaii | — | — | — | — | 51201 | 7.72 | gain |
| Fortunes Rocks | Maine | 44098 | 71.40 | 44098 | 71.40 | 44007 | 22.13 | change |
| Long Sands Beach | Maine | 44098 | 54.36 | 44098 | 54.36 | 44030 | 15.53 | change |
| Reid State Park | Maine | 44098 | 114.32 | — | — | 44030 | 87.08 | change |
| Scituate | Massachusetts | 44090 | 50.08 | 44090 | 50.08 | 44013 | 18.47 | change |
| The Wall Hampton | New Hampshire | 44098 | 53.04 | 44098 | 53.04 | 44030 | 42.13 | change |
| Atlantic City | New Jersey | 44084 | 105.77 | 44084 | 105.77 | 44091 | 72.58 | change |
| Strathmere | New Jersey | 44091 | 99.18 | 44091 | 99.18 | 44084 | 80.83 | change |
| Topsail Beach | North Carolina | 41159 | 65.57 | 41159 | 65.57 | 41110 | 24.82 | change |
| Hubbard Creek | Oregon | — | — | — | — | 46015 | 29.69 | gain |
| Indian Beach Ecola | Oregon | 46029 | 50.61 | 46029 | 50.61 | 46243 | 38.21 | change |
| Steps Beach Rincon | Puerto Rico | — | — | — | — | 41115 | 2.93 | gain |
| Tres Palmas | Puerto Rico | — | — | — | — | 41115 | 3.64 | gain |
| Baileys Beach | Rhode Island | 44085 | 24.65 | — | — | 44097 | 56.53 | change |
| Misquamicut State Beach | Rhode Island | — | — | — | — | 44085 | 64.20 | gain |

## The 51 still blocked (run 74's 62, less the 11), sorted by land the line crosses from 250 m out

| Spot | Region | Buoy now | km | Land crossed, km | Rule's choice | km | Outcome |
|---|---|---|---|---|---|---|---|
| Narrow River | Rhode Island | 44085 | 34.71 | 0.15 | 44097 | 59.11 | change |
| Rialto Beach (near-miss) | Washington | 46206 | 143.79 | 0.31 | — | — | loss (no line of sight) |
| First Beach | Rhode Island | 44085 | 24.16 | 0.46 | 44097 | 59.47 | change |
| Pirates Cove (near-miss) | New Hampshire | 44098 | 52.01 | 0.51 | 44030 | 30.12 | change |
| Baileys Beach (near-miss) | Rhode Island | 44085 | 24.65 | 0.60 | 44097 | 56.53 | change |
| East Beach | Rhode Island | 44085 | 51.76 | 0.66 | 44097 | 61.50 | change |
| Morro Rock | California | 46259 | 87.62 | 0.70 | 46028 | 103.03 | change |
| Breachway | Rhode Island | 44085 | 50.79 | 0.74 | 44097 | 61.12 | change |
| Reid State Park | Maine | 44098 | 114.32 | 0.78 | 44030 | 87.08 | change |
| Dana Point, California (near-miss) | California | 46285 | 4.14 | 0.90 | 46253 | 44.73 | change |
| Doran Beach | California | 46214 | 54.94 | 0.98 | — | — | loss (no line of sight) |
| The K's (near-miss) | Rhode Island | 44085 | 38.80 | 1.15 | 44097 | 54.45 | change |
| Caesars Pier | New Jersey | 44091 | 73.60 | 2.13 | 44084 | 104.95 | change |
| Agate Beach | California | 46244 | 33.24 | 2.37 | 46027 | 79.50 | change |
| Deep Hole | Rhode Island | 44085 | 42.06 | 2.39 | 44097 | 56.83 | change |
| Weekapaug | Rhode Island | 44085 | 61.23 | 2.44 | — | — | loss (no line of sight) |
| Pigeon Point | California | 46236 | 61.30 | 2.50 | 46042 | 43.80 | change |
| Salty Brine | Rhode Island | 44085 | 40.10 | 2.80 | — | — | loss (no line of sight) |
| Bird Rock | California | 46254 | 6.04 | 4.16 | 46225 | 17.11 | change |
| Dunes Park | Rhode Island | 44085 | 61.50 | 4.68 | — | — | loss (no line of sight) |
| Corona Del Mar | California | 46285 | 24.40 | 4.81 | 46253 | 28.49 | change |
| Squibnocket | Massachusetts | 44085 | 23.68 | 5.21 | — | — | loss (no line of sight) |
| PB Point | California | ljpc1 | 6.72 | 5.78 | 46225 | 18.09 | change |
| Law Street | California | ljpc1 | 7.26 | 6.54 | 46225 | 18.96 | change |
| Crystal Cove | California | 46285 | 18.25 | 6.55 | 46277 | 29.00 | change |
| Madaket | Massachusetts | 44085 | 70.20 | 6.65 | 44097 | 83.84 | change |
| Rockpile (Laguna) | California | 46285 | 14.91 | 7.80 | 46277 | 25.95 | change |
| Hatteras Ferry Docks | North Carolina | 44095 | 69.27 | 8.45 | 41025 | 35.19 | change |
| Miacomet | Massachusetts | 44085 | 77.98 | 8.78 | 44097 | 89.55 | change |
| Lucy Vincent | Massachusetts | 44085 | 26.45 | 8.81 | — | — | loss (no line of sight) |
| Avalanches | California | ljpc1 | 12.84 | 8.83 | 46258 | 23.17 | change |
| Long Point | Massachusetts | 44085 | 33.84 | 10.89 | 44097 | 59.00 | change |
| Ventnor Pier | New Jersey | 44091 | 77.72 | 11.55 | 44084 | 101.42 | change |
| Half Moon Bay | California | 46237 | 38.12 | 12.73 | 46012 | 40.23 | change |
| Margate Pier | New Jersey | 44091 | 79.50 | 14.05 | 44084 | 99.86 | change |
| Rat Beach | California | 46256 | 21.59 | 14.40 | 46221 | 23.33 | change |
| Palm Beach | Florida | 41122 | 78.31 | 14.40 | — | — | loss (no line of sight) |
| Haggarty's | California | 46256 | 21.96 | 14.63 | 46221 | 22.83 | change |
| 32nd Street | New Jersey | 44091 | 82.15 | 15.94 | 44084 | 97.49 | change |
| Quincy Ave | New Jersey | 44091 | 81.58 | 15.96 | 44084 | 98.01 | change |
| Nobadeer | Massachusetts | 44085 | 81.96 | 17.11 | 44097 | 93.53 | change |
| South Beach State Park | Massachusetts | 44085 | 41.94 | 17.57 | 44097 | 65.06 | change |
| Keyes Beach | Massachusetts | 44085 | 67.59 | 25.46 | 44020 | 15.29 | change |
| Port Hueneme | California | 46268 | 58.26 | 49.96 | 46025 | 42.92 | change |
| Silver Strand | California | 46268 | 61.00 | 53.97 | 46025 | 44.73 | change |
| Oxnard Beach | California | 46268 | 63.40 | 57.97 | 46025 | 48.33 | change |
| Mendocino | California | 46013 | 125.59 | 59.92 | 46014 | 17.80 | change |
| Caspar | California | 46013 | 132.52 | 67.83 | — | — | loss (no line of sight) |
| Jug Handle | California | 46013 | 134.23 | 69.72 | — | — | loss (no line of sight) |
| Mackerricher | California | 46013 | 145.74 | 86.92 | 46213 | 120.32 | change |
| Westport | California | 46244 | 147.86 | 106.17 | 46014 | 48.91 | change |

Land crossed is measured from the rule's point with no standoff (the trace over run 74's report, 2026-11-13), the shoreline-grazing figure #323 addresses; the rule's verdict above is run 75's, with the standoff.
