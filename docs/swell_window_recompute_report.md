# Swell-window recompute: before and after

The own-land ray-cast fix (`SWELL_OWN_LAND_CROSSING_KM`, `pipeline/enrichment/swell_window.py`):
a ray that starts inside the spot's own landmass and crosses more of it than the spot's depth
plus 2 km is blocked at its start, whatever it meets afterwards. Recomputed over the whole
roster by sw1-raycast run 13 (2026-10-15, branch `sw1-raycast/full-13`), then
`enrich --skip-raycast` over the new arcs so the ten hand overrides in
`pipeline/data/spot_swell_windows.json` apply with their pruning. (Run 11, branch
`sw1-raycast/full-11`, applied the rule only to rays that never came back to the land; a ray
across the Florida peninsula or the California coast that re-entered the same continent more
than 100 km away was still graded open. Run 13 closes that.)

**Before** is `pipeline/spots_enriched.json` on main at 635ba9c. **After** is this branch's
roster: main's roster with three fields taken from run 13: `swell_window_arcs`,
`optimal_swell_dir` and `swell_window_source_override`. 646 valid spots (two invalid spots
are passed through untouched). Regenerate the numbers with

    python -m pipeline.swell_window_report --before <main roster> --after pipeline/spots_enriched.json --md OUT.md

## What enrich changed that is NOT kept

`enrich --skip-raycast` recomputes every algorithm, not only the swell window. Against the
raycast commit it changed these fields too (run 13 changed the same fields, by the same counts, as run 11), none of which this change is about, so they are
left at main's values:

| field | spots changed | example |
|---|---|---|
| orientation_200m | 608 | 15th Street Del Mar: None -> 259.6 |
| fallback_buoy_ids | 605 | 15th Street Del Mar: None -> 46254, 46225, 46274 |
| orientation_50m | 604 | 15th Street Del Mar: None -> 260.7 |
| coord_adjusted | 589 | 15th Street Del Mar: None -> True |
| orientation_flipped | 589 | 15th Street Del Mar: None -> False |
| nearest_buoy_dist_km | 569 | 38th Avenue: 22.84 -> 22.26 |
| nearest_tide_station_dist_km | 510 | 15th Street Del Mar: 10.34 -> 10.28 |
| enrichment_confidence | 346 | Asilomar State Beach: nearest_buoy 0.75 -> 1.0 |
| nearest_buoy_id | 315 | Asilomar State Beach: 46012 -> 46236 |
| tags | 202 | Tijuana Slough: None -> {} |
| nearest_tide_station_id | 153 | Blackies: 9410599 -> 9410583 |
| orientation_note | 4 | Rincon: None -> "Optimal swell from WNW (290°) wraps around point. Cove faces SSW." |

The buoy reassignments come from the live NDBC station list the run downloaded; the tide
stations from the live CO-OPS list. `orientation_deg`, `offshore_wind_deg`,
`swell_window_source` and `mop_shore_normal` did not change.

## The ten hand overrides

All ten reach the roster, each with `swell_window_source_override: "manual"`, and
`test_every_override_is_applied_in_the_committed_roster` reads the committed file to keep it
so. Gain at the researched optimal, with the run-13 arcs after pruning: 1.00 at honolua-bay,
spoils, suicide-s, crash-boat-beach, gas-chambers-aguadilla and lovers-point; 0.40 at
hapuna-beach, thousand-peaks-maui and table-tops-aguadilla (optimal outside every arc, within
45 degrees of an edge). monterey-beach has no window at all: run 13 blocks 88 of its 90 rays
and the two open ones shrink away, so it runs on directional_gain's no-arcs branch (cos² about
320, floor 0.25) — the only spot in the roster without arcs.

## Run 13 against run 11

| | run 11 | run 13 |
|---|---|---|
| windows opening >90 deg from facing | 303 | 121 |
| windows opening >135 deg from facing | 154 | 11 |
| optimal >90 deg from facing | 6 | 2 |
| facing outside every arc | 68 | 68 (the same 68 spots) |
| tier exposed / point / sheltered | 27 / 552 / 67 | 4 / 546 / 96 |
| Hawaii / Puerto Rico spots narrower within 90 deg of facing (vs the previous run) | 36 (vs main) | 0 (vs run 11) |

Monterey Beach: run 11 left one arc, [129-147], cast south-east across California; run 13
blocks it, and the window is empty. Florida east coast (Hobe Beach to Haulover Inlet): every
arc that crossed the peninsula to the south-west (185-259) or west-north-west (285-315) is
gone. The seaward arcs are unchanged except Ocean Reef Park ([357-111] -> [5-111]),
Hillsboro Beach ([145-179] -> [145-175]) and the 2-degree slivers [349-351] at Pompano Beach
Pier and Commercial Pier, which went. Spoils keeps [13-83] only.

Star preview, run 13 (new roster against main, window from 2026-10-15T11:58Z) against run 14
(run-11 roster against main, window from 12:53Z — 646 rows, one forecast hour, apart):
changed spot-hours 13,754 against 9,215, changed spots 410 against 330. The extra changes are
continental: California +3,073 hours, Florida +689, Washington +373, Oregon +106, North
Carolina +89; Hawaii +24 and Puerto Rico +7, where the rays do not re-enter the same land.

## Totals against main

| | before | after |
|---|---|---|
| windows opening >90 deg from facing | 427 | 121 |
| windows opening >135 deg from facing | 308 | 11 |
| optimal >90 deg from facing | 31 | 2 |
| facing outside every arc | 68 | 68 |
| tier: exposed | 76 | 4 |
| tier: point | 546 | 546 |
| tier: sheltered | 24 | 96 |

Tier moves (before -> after): exposed->exposed 4, exposed->point 71, exposed->sheltered 1, point->point 475, point->sheltered 71, sheltered->sheltered 24

Hawaii / Puerto Rico spots whose window within 90 deg of facing got narrower: 36

| spot | state | degrees lost |
|---|---|---|
| hanalei-bay | Hawaii | 78.5 |
| canoes-waikiki | Hawaii | 44 |
| table-tops-aguadilla | Puerto Rico | 40 |
| tunnels-beach | Hawaii | 28.5 |
| waimea-bay | Hawaii | 28.5 |
| dogmans-rincon | Puerto Rico | 28 |
| honolua-bay | Hawaii | 25.5 |
| hapuna-beach | Hawaii | 19 |
| kanaha-beach | Hawaii | 18.5 |
| queens-waikiki | Hawaii | 16.5 |
| yokohama-bay | Hawaii | 16.5 |
| makapuu-beach-park | Hawaii | 16 |
| puaena-point | Hawaii | 14.5 |
| haleiwa-ali-i-beach | Hawaii | 13.5 |
| kiahuna-beach | Hawaii | 12.5 |
| polihale-beach | Hawaii | 10.5 |
| laniakea | Hawaii | 8.5 |
| pakala | Hawaii | 8.5 |
| makaha | Hawaii | 8 |
| suicide-s | Hawaii | 8 |
| flemings-beach | Hawaii | 7.5 |
| lymans | Hawaii | 5.5 |
| turtle-bay | Hawaii | 4.5 |
| backdoor-pipeline | Hawaii | 4 |
| banzai-pipeline | Hawaii | 4 |
| ho-okipa | Hawaii | 4 |
| kolekole-beach-park | Hawaii | 4 |
| log-cabins | Hawaii | 4 |
| off-the-wall | Hawaii | 4 |
| p-p-kea-hawaii | Hawaii | 4 |
| rocky-point | Hawaii | 4 |
| sunset-beach-hawaii | Hawaii | 4 |
| banyans | Hawaii | 3.5 |
| chuns-reef | Hawaii | 3.5 |
| electric-beach | Hawaii | 2.5 |
| kaisers | Hawaii | 1.5 |

Descriptions the next db_import flags for a rewrite: 416 (416 of them because of this change)

Descriptions: the payload hashes `optimal_swell_deg`, so every spot whose whole-degree optimal
moved gets a new signature; db_import blanks those on its next run and
`python -m pipeline.generate_descriptions` rewrites them from the template.

## Per spot

The table lists every spot: facing, arcs before (main) and after (this branch), total window
width, optimal, and whether the facing direction is inside a window.

| spot | state | facing | arcs before | arcs after | width | optimal | facing in window |
|---|---|---|---|---|---|---|---|
| 15th-street-del-mar | California | 260 | [93-151] [193-311] | [193-311] | 184 -> 122 | 212 -> 252 | yes -> yes |
| 38th-avenue | California | 139 | [189-251] [285-327] | [189-251] | 112 -> 66 | 256 -> 220 | no -> no |
| 56th-street | New Jersey | 115 | [51-187] | [53-187] | 138 -> 138 | 116 -> 120 | yes -> yes |
| andrew-molera-state-park | California | 235 | [101-111] [165-299] [321-323] | [165-299] | 158 -> 138 | 224 -> 232 | yes -> yes |
| asilomar-state-beach | California | 304 | [129-143] [161-167] [193-199] [221-319] | [233-319] | 140 -> 90 | 232 -> 276 | yes -> yes |
| bird-rock | California | 238 | [93-131] [181-319] | [181-319] | 184 -> 142 | 224 -> 248 | yes -> yes |
| blackies | California | 238 | [93-103] [149-219] [245-279] [325-343] | [149-219] [245-279] | 148 -> 112 | 216 -> 212 | no -> no |
| bolinas | California | 129 | [169-283] [333-355] | [169-227] | 144 -> 62 | 252 -> 196 | no -> no |
| bolsa-chica-state-beach | California | 232 | [97-119] [141-207] [241-267] [329-343] | [149-207] [241-267] | 144 -> 92 | 188 -> 204 | no -> no |
| bombora | Hawaii | 210 | [59-67] [81-83] [139-267] [333-341] | [141-267] | 154 -> 130 | 188 -> 204 | yes -> yes |
| brooks-street | California | 225 | [93-123] [157-283] [317-343] | [157-283] | 194 -> 130 | 216 -> 220 | yes -> yes |
| c-street-ventura | California | 193 | [97-103] [165-183] [201-275] [313-343] | [165-183] [201-267] | 144 -> 92 | 236 -> 216 | no -> no |
| campus-point-ucsb | California | 113 | [125-139] [177-263] [305-347] | [125-139] [177-263] | 154 -> 108 | 240 -> 204 | no -> no |
| cardiff-reef | California | 250 | [93-151] [189-311] | [189-311] | 188 -> 126 | 216 -> 248 | yes -> yes |
| carlsbad-state-beach | California | 240 | [93-143] [177-307] [333-339] | [177-307] | 198 -> 134 | 220 -> 240 | yes -> yes |
| carmel-beach | California | 266 | [105-111] [129-139] [157-163] [225-287] | [225-287] | 100 -> 66 | 220 -> 256 | yes -> yes |
| carpinteria-state-beach | California | 214 | [97-99] [141-191] [225-263] [313-347] | [141-191] [225-263] | 140 -> 96 | 220 -> 196 | no -> no |
| cayucos-pier | California | 207 | [97-111] [185-259] [317-351] | [185-259] | 134 -> 78 | 248 -> 220 | yes -> yes |
| church | California | 218 | [93-115] [165-291] [325-343] | [165-283] | 178 -> 122 | 228 -> 224 | yes -> yes |
| coronado-shores | California | 225 | [173-191] [209-243] | [173-191] [209-243] | 60 -> 60 | 208 -> 208 | yes -> yes |
| cottons | California | 234 | [93-119] [165-291] [333-343] | [165-291] | 174 -> 130 | 220 -> 228 | yes -> yes |
| county-line | California | 200 | [129-283] [317-343] | [129-283] | 188 -> 158 | 224 -> 204 | yes -> yes |
| cowell-s-beach-children-s-surfing-area | California | 142 | [181-231] [249-283] [313-315] | [181-231] | 98 -> 54 | 248 -> 204 | no -> no |
| crescent-city-beach | California | 221 | [189-275] [313-331] [1-7] | [189-275] | 122 -> 90 | 260 -> 232 | yes -> yes |
| d-street-encinitas | California | 258 | [93-151] [185-307] | [185-307] | 188 -> 126 | 208 -> 244 | yes -> yes |
| dana-point-california | California | 263 | [161-287] [337-343] | [161-287] | 140 -> 130 | 232 -> 224 | yes -> yes |
| davenport-landing | California | 225 | [169-295] | [169-295] | 130 -> 130 | 240 -> 232 | yes -> yes |
| del-mar-beach | California | 260 | [93-151] [193-311] | [193-311] | 184 -> 122 | 212 -> 252 | yes -> yes |
| devereux | California | 168 | [125-135] [173-267] [309-347] | [125-135] [173-267] | 154 -> 112 | 236 -> 204 | no -> no |
| dillon-beach | California | 276 | [161-163] [253-295] [341-355] | [253-295] | 70 -> 46 | 272 -> 272 | yes -> yes |
| doheny-state-beach | California | 190 | [93-115] [161-259] [281-287] [321-343] | [161-259] | 164 -> 102 | 220 -> 208 | yes -> yes |
| doran-beach | California | 152 | [185-287] [305-311] [345-355] | [201-215] | 130 -> 18 | 260 -> 208 | no -> no |
| el-capitan-state-beach | California | 210 | [121-131] [153-259] [289-347] | [121-131] [153-259] | 186 -> 124 | 232 -> 192 | yes -> yes |
| el-porto | California | 255 | [97-115] [189-279] | [189-279] | 116 -> 94 | 208 -> 232 | yes -> yes |
| emma-wood-state-beach | California | 214 | [97-103] [157-179] [197-275] [317-343] | [157-179] [197-275] | 148 -> 108 | 232 -> 216 | yes -> yes |
| first-peak | California | 154 | [189-255] [313-315] | [189-255] | 76 -> 70 | 248 -> 220 | no -> no |
| fort-point | California | 334 | [165-179] [233-267] | [233-267] | 56 -> 38 | 228 -> 248 | no -> no |
| garbage-beach | California | 257 | [161-171] [189-327] | [189-327] | 156 -> 142 | 244 -> 252 | yes -> yes |
| half-moon-bay-jetty | California | 211 | [105-111] [177-251] [285-311] | [177-251] | 118 -> 78 | 220 -> 212 | yes -> yes |
| hammonds | California | 180 | [133-179] [217-243] [289-347] | [133-179] [217-243] | 142 -> 80 | 244 -> 184 | yes -> yes |
| hermosa-beach-pier | California | 260 | [97-107] [201-283] | [201-283] | 100 -> 86 | 224 -> 240 | yes -> yes |
| hermosa-beach-california | California | 252 | [97-111] [197-283] | [197-283] | 108 -> 90 | 216 -> 240 | yes -> yes |
| humboldt-bay-jetty | California | 308 | [181-195] [213-359] | [213-359] | 168 -> 150 | 272 -> 284 | yes -> yes |
| huntington-beach-pier | California | 220 | [93-111] [145-211] [241-275] [325-343] | [145-211] [241-275] | 152 -> 108 | 204 -> 208 | no -> no |
| huntington-beach-california | California | 220 | [93-107] [145-215] [241-279] [325-343] | [145-215] [241-279] | 156 -> 116 | 212 -> 212 | no -> no |
| huntington-cliffs | California | 220 | [93-115] [145-211] [241-271] [329-343] | [145-211] [241-271] | 148 -> 104 | 192 -> 204 | no -> no |
| imperial-beach-pier | California | 272 | [93-147] [181-199] [233-307] | [181-199] [233-307] | 158 -> 100 | 200 -> 248 | yes -> yes |
| imperial-beach-california | California | 270 | [93-147] [181-199] [233-303] | [189-199] [233-303] | 154 -> 88 | 200 -> 248 | yes -> yes |
| jalama-beach | California | 250 | [109-139] [165-287] | [165-287] | 160 -> 126 | 200 -> 224 | yes -> yes |
| jenner-beach | California | 239 | [105-111] [177-295] [325-359] | [177-295] | 170 -> 122 | 260 -> 236 | yes -> yes |
| la-jolla-shores | California | 285 | [93-139] [189-227] [245-319] | [249-319] | 170 -> 74 | 220 -> 284 | yes -> yes |
| leadbetter-beach | California | 144 | [129-171] [217-263] [289-347] | [129-171] [217-243] | 158 -> 76 | 248 -> 176 | yes -> yes |
| leo-carrillo-state-beach | California | 200 | [133-283] [317-343] | [133-279] | 184 -> 150 | 228 -> 204 | yes -> yes |
| leucadia | California | 250 | [93-151] [181-307] | [181-307] | 192 -> 130 | 208 -> 244 | yes -> yes |
| wind-and-sea | California | 190 | [189-255] [309-311] | [189-255] | 76 -> 70 | 248 -> 220 | yes -> yes |
| lovers-point | California | 29 | [165-175] [213-219] [253-319] | [317-319] | 94 -> 6 | 248 -> 315.0 | no -> no |
| lower-trestles | California | 205 | [93-115] [165-291] [325-343] | [165-291] | 178 -> 130 | 228 -> 228 | yes -> yes |
| lunada-bay | California | 260 | [137-143] [173-291] | [173-291] | 132 -> 122 | 216 -> 232 | yes -> yes |
| malibu-surfrider-beach | California | 202 | [149-247] [305-343] | [149-247] | 144 -> 102 | 240 -> 196 | yes -> yes |
| manhattan-beach-pier | California | 250 | [97-111] [149-151] [193-283] | [193-283] | 118 -> 94 | 212 -> 236 | yes -> yes |
| manresa-state-beach | California | 243 | [105-115] [133-143] [205-271] | [205-271] | 98 -> 70 | 204 -> 236 | yes -> yes |
| marina-state-beach | California | 285 | [105-115] [133-183] [245-307] | [245-307] | 134 -> 66 | 188 -> 276 | yes -> yes |
| mavericks-california | California | 245 | [173-315] | [173-315] | 146 -> 146 | 244 -> 244 | yes -> yes |
| mission-beach | California | 270 | [93-119] [193-323] | [193-323] | 164 -> 134 | 240 -> 256 | yes -> yes |
| mondos | California | 197 | [97-107] [149-215] [233-271] [313-347] | [149-215] [233-271] | 164 -> 112 | 220 -> 208 | yes -> yes |
| montara-state-beach | California | 276 | [197-311] | [201-311] | 118 -> 114 | 236 -> 256 | yes -> yes |
| moonlight-beach | California | 258 | [93-151] [185-307] | [185-307] | 188 -> 126 | 208 -> 244 | yes -> yes |
| moonstone-beach-humboldt | California | 255 | [141-183] [209-299] [341-343] | [209-299] | 146 -> 94 | 228 -> 252 | yes -> yes |
| morro-rock | California | 296 | [97-111] [129-131] [201-299] | [201-299] | 126 -> 102 | 224 -> 248 | yes -> yes |
| moss-landing | California | 265 | [105-115] [133-159] [177-183] [221-295] | [221-295] | 132 -> 78 | 216 -> 256 | yes -> yes |
| natural-bridges | California | 185 | [177-279] | [177-271] | 106 -> 98 | 244 -> 224 | yes -> yes |
| new-brighton-reef | California | 185 | [193-223] [265-311] | [193-223] | 84 -> 34 | 260 -> 208 | no -> no |
| newport-beach-california | California | 206 | [93-99] [149-283] [321-343] | [149-283] | 174 -> 138 | 224 -> 216 | yes -> yes |
| newport-point | California | 210 | [93-95] [149-219] [245-283] [333-343] | [149-219] [245-283] | 136 -> 116 | 220 -> 216 | yes -> yes |
| ocean-beach-pier-sd | California | 265 | [93-99] [177-323] | [197-323] | 160 -> 130 | 244 -> 260 | yes -> yes |
| ocean-beach-sf | California | 259 | [153-159] [189-295] | [189-295] | 120 -> 110 | 232 -> 240 | yes -> yes |
| oceanside-harbor | California | 231 | [93-135] [173-295] [333-339] | [173-295] | 182 -> 126 | 216 -> 232 | yes -> yes |
| oceanside-pier | California | 235 | [93-139] [173-299] [333-339] | [173-299] | 190 -> 130 | 216 -> 236 | yes -> yes |
| pacific-beach | California | 257 | [93-127] [189-315] | [189-315] | 168 -> 130 | 232 -> 252 | yes -> yes |
| pacifica-linda-mar | California | 295 | [133-155] [189-307] | [253-307] | 148 -> 58 | 224 -> 280 | yes -> yes |
| palos-verdes-cove | California | 275 | [133-135] [153-291] | [241-291] | 148 -> 54 | 212 -> 264 | yes -> yes |
| pismo-beach-pier | California | 248 | [97-119] [137-151] [193-275] [333-347] | [193-275] | 148 -> 86 | 208 -> 232 | yes -> yes |
| pleasure-point | California | 175 | [189-259] [313-315] | [189-259] | 80 -> 74 | 244 -> 224 | no -> no |
| point-arena | California | 240 | [109-111] [149-303] [333-339] | [149-303] | 174 -> 158 | 228 -> 224 | yes -> yes |
| ponto-beach | California | 250 | [93-151] [181-307] | [181-307] | 192 -> 130 | 208 -> 244 | yes -> yes |
| redondo-beach-california | California | 262 | [97-103] [153-155] [173-179] [217-287] | [217-287] | 100 -> 74 | 220 -> 252 | yes -> yes |
| redondo-breakwater | California | 253 | [97-103] [149-159] [209-287] | [209-287] | 106 -> 82 | 224 -> 248 | yes -> yes |
| refugio-state-beach | California | 180 | [121-131] [149-259] [285-315] [333-347] | [121-131] [149-259] | 180 -> 128 | 228 -> 192 | yes -> yes |
| rincon | California | 210 | [145-191] [237-267] [317-347] | [145-191] [237-267] | 118 -> 84 | 232 -> 196 | no -> no |
| river-jetties | California | 215 | [93-107] [145-215] [245-279] [321-343] | [145-215] [245-279] | 156 -> 112 | 212 -> 208 | yes -> yes |
| rockaway-beach-pacifica | California | 289 | [133-155] [177-219] [237-307] | [237-307] | 146 -> 74 | 220 -> 272 | yes -> yes |
| rockpile | California | 200 | [137-147] [201-267] [317-347] | [137-147] [201-267] | 118 -> 84 | 248 -> 216 | yes -> yes |
| salmon-creek-beach | California | 278 | [181-311] | [213-311] | 134 -> 102 | 244 -> 260 | yes -> yes |
| salt-creek | California | 236 | [93-107] [161-287] [329-343] | [161-287] | 166 -> 130 | 224 -> 224 | yes -> yes |
| san-diego-blacks-beach | California | 263 | [93-143] [225-315] | [225-315] | 148 -> 94 | 224 -> 268 | yes -> yes |
| san-elijo-state-beach | California | 250 | [93-151] [185-311] | [185-311] | 192 -> 130 | 212 -> 248 | yes -> yes |
| san-onofre | California | 211 | [93-119] [165-283] [321-339] | [165-283] | 174 -> 122 | 224 -> 224 | yes -> yes |
| san-simeon | California | 220 | [101-111] [157-287] [333-351] | [157-287] | 170 -> 134 | 220 -> 220 | yes -> yes |
| sand-dollar-beach | California | 287 | [101-111] [157-159] [181-311] [349-351] | [181-311] | 160 -> 134 | 228 -> 244 | yes -> yes |
| sandspit | California | 98 | [129-171] [217-263] [289-347] | [129-171] [217-227] | 158 -> 60 | 248 -> 172 | no -> no |
| santa-monica-beach | California | 225 | [97-115] [173-259] [317-343] | [173-259] | 142 -> 90 | 220 -> 216 | yes -> yes |
| scotts-creek | California | 225 | [169-299] | [169-299] | 134 -> 134 | 236 -> 232 | yes -> yes |
| seal-beach-pier | California | 212 | [97-111] [141-207] [225-251] [317-343] | [141-207] [225-251] | 148 -> 100 | 196 -> 196 | no -> no |
| second-peak | California | 153 | [189-255] [313-315] | [189-255] | 76 -> 70 | 244 -> 220 | no -> no |
| sewer-peak | California | 186 | [189-259] [313-315] | [189-259] | 80 -> 74 | 244 -> 224 | no -> no |
| sharp-park | California | 269 | [133-159] [177-179] [217-303] | [217-303] | 126 -> 90 | 224 -> 260 | yes -> yes |
| shell-beach | California | 193 | [97-107] [189-271] [333-347] | [189-271] | 118 -> 86 | 244 -> 228 | yes -> yes |
| shelter-cove | California | 184 | [109-111] [173-307] [1-3] | [173-291] | 150 -> 122 | 248 -> 232 | yes -> yes |
| solana-beach | California | 255 | [93-151] [189-311] | [189-311] | 188 -> 126 | 212 -> 248 | yes -> yes |
| steamer-lane | California | 128 | [181-251] [317-331] | [181-251] | 92 -> 74 | 244 -> 216 | no -> no |
| stinson-beach | California | 213 | [173-255] [313-315] [333-355] | [173-255] | 118 -> 86 | 248 -> 212 | yes -> yes |
| stone-steps | California | 249 | [93-151] [181-307] | [181-307] | 192 -> 130 | 208 -> 244 | yes -> yes |
| strands-beach | California | 245 | [93-99] [161-287] [333-343] | [161-287] | 154 -> 130 | 228 -> 224 | yes -> yes |
| suicide-s | Hawaii | 309 | [65-99] [113-175] [237-265] [283-315] | [245-263] [285-315] | 164 -> 56 | 160 -> 292.0 | yes -> yes |
| sunset-cliffs | California | 250 | [181-327] | [189-327] | 150 -> 142 | 248 -> 256 | yes -> yes |
| surf-beach-vandenberg | California | 282 | [97-103] [121-155] [173-187] [209-331] | [209-331] | 192 -> 126 | 220 -> 268 | yes -> yes |
| surfside-jetty | California | 213 | [97-119] [145-207] [229-255] [317-343] | [145-207] [229-255] | 152 -> 96 | 192 -> 200 | no -> no |
| surfside-california | California | 223 | [97-119] [145-207] [229-255] [317-343] | [145-207] [229-255] | 152 -> 96 | 192 -> 200 | no -> no |
| swamis | California | 234 | [93-151] [185-311] | [185-311] | 192 -> 130 | 212 -> 248 | yes -> yes |
| t-street-beach | California | 225 | [93-127] [165-291] [321-343] | [165-291] | 194 -> 130 | 224 -> 228 | yes -> yes |
| tamarack | California | 240 | [93-143] [177-307] [333-339] | [177-307] | 198 -> 134 | 220 -> 240 | yes -> yes |
| thalia-street | California | 225 | [93-123] [157-283] [317-343] | [157-283] | 194 -> 130 | 216 -> 220 | yes -> yes |
| the-hook | California | 149 | [189-251] [293-327] | [189-251] | 104 -> 66 | 256 -> 220 | no -> no |
| the-pier-beach | California | 240 | [93-127] [165-291] [317-343] | [165-291] | 198 -> 130 | 224 -> 228 | yes -> yes |
| the-wedge | California | 210 | [93-111] [153-223] [245-279] [317-343] | [153-223] [245-279] | 164 -> 112 | 220 -> 216 | yes -> yes |
| toes-over | California | 162 | [189-219] [237-243] [281-287] [305-311] | [189-219] | 64 -> 34 | 248 -> 204 | no -> no |
| topanga-beach | California | 151 | [161-251] [301-343] | [161-251] | 140 -> 94 | 248 -> 204 | no -> no |
| torrance-beach | California | 285 | [141-159] [177-211] [233-287] | [233-287] | 118 -> 58 | 208 -> 260 | yes -> yes |
| torrey-pines-state-beach | California | 265 | [93-147] [197-315] | [197-315] | 180 -> 122 | 220 -> 256 | yes -> yes |
| tourmaline | California | 255 | [93-131] [185-311] | [185-311] | 172 -> 130 | 224 -> 248 | yes -> yes |
| 3-mile | California | 229 | [173-287] [321-323] | [173-279] | 124 -> 110 | 248 -> 224 | yes -> yes |
| trestles | California | 220 | [93-111] [165-291] [329-343] | [165-291] | 170 -> 130 | 228 -> 228 | yes -> yes |
| trinidad-state-beach | California | 269 | [205-339] | [205-339] | 138 -> 138 | 276 -> 272 | yes -> yes |
| upper-trestles | California | 220 | [93-111] [165-291] [333-343] | [165-291] | 166 -> 130 | 224 -> 228 | yes -> yes |
| venice-beach-breakwater | California | 230 | [177-267] [337-343] | [177-267] | 104 -> 94 | 236 -> 220 | yes -> yes |
| ventura-point | California | 177 | [157-183] [201-275] [317-343] | [157-183] [201-275] | 138 -> 108 | 236 -> 216 | yes -> yes |
| waddell-creek | California | 225 | [105-107] [165-275] | [165-275] | 120 -> 114 | 220 -> 224 | yes -> yes |
| windansea-beach | California | 265 | [93-131] [177-319] | [177-319] | 188 -> 146 | 224 -> 248 | yes -> yes |
| zuma-beach | California | 238 | [137-279] [317-343] | [137-279] | 176 -> 146 | 228 -> 208 | yes -> yes |
| bethany-beach | Delaware | 90 | [37-163] | [37-163] | 130 -> 130 | 112 -> 100 | yes -> yes |
| dewey-beach | Delaware | 84 | [4-164] | [4-164] | 160 -> 160 | 84 -> 84 | yes -> yes |
| indian-river-inlet | Delaware | 70 | [25-115] | [25-115] | 90 -> 90 | 70 -> 70 | yes -> yes |
| rehoboth-beach | Delaware | 87 | [45-163] [293-311] | [45-163] | 144 -> 122 | 100 -> 104 | yes -> yes |
| amelia-island | Florida | 98 | [18-178] | [18-178] | 160 -> 160 | 98 -> 98 | yes -> yes |
| atlantic-beach | Florida | 87 | [9-159] [189-215] [245-271] | [9-159] | 214 -> 154 | 120 -> 84 | yes -> yes |
| bathtub-beach | Florida | 70 | [349-155] | [349-155] | 170 -> 170 | 72 -> 72 | yes -> yes |
| boca-raton-inlet | Florida | 114 | [13-91] [145-179] [205-219] [293-315] | [13-91] [145-179] | 164 -> 120 | 68 -> 84 | no -> no |
| boynton-beach-inlet | Florida | 100 | [21-111] [153-179] [197-215] [289-311] | [21-111] [153-179] | 172 -> 124 | 112 -> 92 | yes -> yes |
| cocoa-beach-pier | Florida | 95 | [45-159] | [45-159] | 118 -> 118 | 100 -> 100 | yes -> yes |
| dania-beach-pier | Florida | 98 | [13-123] [141-175] [209-223] [297-315] | [13-123] [141-175] | 192 -> 152 | 100 -> 92 | yes -> yes |
| daytona-beach | Florida | 65 | [173-199] [265-283] [349-143] | [349-135] | 210 -> 150 | 84 -> 60 | yes -> yes |
| deerfield-beach-pier | Florida | 89 | [17-127] [145-175] [205-223] [293-315] | [17-127] [145-175] | 196 -> 148 | 100 -> 96 | yes -> yes |
| delray-beach | Florida | 95 | [17-127] [153-179] [201-219] [289-311] | [17-127] [153-179] | 192 -> 144 | 112 -> 96 | yes -> yes |
| destin-jetty | Florida | 184 | [104-264] | [104-264] | 160 -> 160 | 184 -> 184 | yes -> yes |
| flagler-beach-pier | Florida | 65 | [169-199] [257-279] [353-147] | [353-147] | 218 -> 158 | 96 -> 68 | yes -> yes |
| fort-lauderdale-beach | Florida | 95 | [17-87] [145-175] [205-219] [297-315] | [17-87] [145-175] | 148 -> 108 | 56 -> 84 | no -> no |
| fort-pierce-inlet | Florida | 75 | [349-151] | [349-151] | 166 -> 166 | 68 -> 68 | yes -> yes |
| haulover-inlet | Florida | 95 | [13-79] [137-167] [289-303] | [13-79] [137-167] | 122 -> 104 | 56 -> 80 | no -> no |
| indian-rocks-beach | Florida | 278 | [189-275] [329-355] | [197-275] [329-355] | 120 -> 112 | 260 -> 268 | no -> no |
| jacksonville-beach-pier | Florida | 80 | [5-155] [181-203] [249-271] | [5-155] | 206 -> 154 | 116 -> 80 | yes -> yes |
| jetty-park-cocoa-beach | Florida | 90 | [7-25] [57-159] | [57-159] | 124 -> 106 | 92 -> 108 | yes -> yes |
| juno-beach-pier | Florida | 75 | [193-215] [285-307] [353-155] | [353-155] | 218 -> 166 | 84 -> 72 | yes -> yes |
| jupiter-inlet | Florida | 75 | [145-159] [185-207] [241-259] [353-111] | [145-159] [353-111] | 188 -> 140 | 92 -> 72 | yes -> yes |
| lake-worth-pier | Florida | 90 | [9-131] [157-179] [229-255] [289-311] | [9-131] [157-175] | 208 -> 148 | 88 -> 88 | yes -> yes |
| lori-wilson-park | Florida | 101 | [37-155] | [37-155] | 122 -> 122 | 96 -> 96 | yes -> yes |
| marineland | Florida | 79 | [169-199] [257-275] [357-147] | [357-147] | 210 -> 154 | 104 -> 72 | yes -> yes |
| matanzas-inlet | Florida | 75 | [181-203] [245-267] [357-147] | [357-147] | 206 -> 154 | 96 -> 72 | yes -> yes |
| mayport-poles | Florida | 96 | [9-159] [193-215] [245-271] | [9-159] | 210 -> 154 | 116 -> 84 | yes -> yes |
| mexico-beach | Florida | 224 | [37-75] [197-287] | [197-287] | 136 -> 94 | 220 -> 240 | yes -> yes |
| monster-hole | Florida | 70 | [1-151] | [1-151] | 154 -> 154 | 76 -> 76 | yes -> yes |
| naples-pier | Florida | 265 | [9-119] [181-319] | [185-319] | 256 -> 138 | 296 -> 252 | yes -> yes |
| navarre-beach | Florida | 172 | [109-251] | [109-251] | 146 -> 146 | 180 -> 180 | yes -> yes |
| new-smyrna-beach-inlet | Florida | 67 | [177-203] [221-247] [345-143] | [345-143] | 222 -> 162 | 100 -> 64 | yes -> yes |
| north-jetty | Florida | 45 | [359-149] | [1-147] | 152 -> 150 | 74 -> 72 | yes -> yes |
| okaloosa-island-pier | Florida | 188 | [117-255] | [117-255] | 142 -> 142 | 184 -> 184 | yes -> yes |
| palm-beach | Florida | 90 | [9-107] [153-175] [241-291] | [9-107] [153-175] | 182 -> 128 | 68 -> 80 | yes -> yes |
| panama-city-beach-pier | Florida | 210 | [149-275] | [149-275] | 130 -> 130 | 216 -> 212 | yes -> yes |
| patrick-air-force-base-beach | Florida | 81 | [25-155] | [25-155] | 134 -> 134 | 88 -> 88 | yes -> yes |
| pensacola-beach | Florida | 170 | [97-251] | [97-251] | 158 -> 158 | 172 -> 172 | yes -> yes |
| playalinda-beach | Florida | 55 | [237-255] [273-291] [341-151] | [341-139] | 218 -> 162 | 48 -> 60 | yes -> yes |
| pompano-beach-pier | Florida | 95 | [25-123] [145-175] [205-219] [293-315] [349-351] | [25-123] [145-175] | 186 -> 136 | 96 -> 96 | yes -> yes |
| ponce-inlet | Florida | 55 | [177-203] [265-283] [345-139] | [345-139] | 210 -> 158 | 76 -> 60 | yes -> yes |
| ponte-vedra-beach | Florida | 75 | [5-155] [181-207] [249-271] | [5-155] | 210 -> 154 | 112 -> 80 | yes -> yes |
| satellite-beach | Florida | 80 | [17-151] | [17-151] | 138 -> 138 | 84 -> 84 | yes -> yes |
| sebastian-inlet | Florida | 80 | [1-147] | [1-147] | 150 -> 150 | 72 -> 72 | yes -> yes |
| south-beach-miami | Florida | 114 | [17-75] [141-183] | [17-75] [141-183] | 108 -> 108 | 80 -> 80 | no -> no |
| st-augustine-beach-pier | Florida | 87 | [357-151] | [357-151] | 158 -> 158 | 72 -> 72 | yes -> yes |
| st-pete-beach | Florida | 240 | [189-315] | [189-315] | 130 -> 130 | 252 -> 252 | yes -> yes |
| treasure-island-fl | Florida | 230 | [181-311] | [181-303] | 134 -> 126 | 244 -> 240 | yes -> yes |
| venice-beach-fl | Florida | 245 | [37-79] [169-323] | [169-323] | 204 -> 158 | 252 -> 244 | yes -> yes |
| vilano-beach | Florida | 70 | [245-275] [357-143] | [357-143] | 184 -> 150 | 64 -> 68 | yes -> yes |
| ala-moana-bowls | Hawaii | 210 | [41-67] [141-267] [329-339] | [141-267] | 174 -> 130 | 196 -> 204 | yes -> yes |
| anahola-bay | Hawaii | 48 | [165-175] [205-219] [237-263] [297-307] [353-147] | [353-147] | 234 -> 158 | 72 -> 68 | yes -> yes |
| backdoor-pipeline | Hawaii | 311 | [61-111] [141-147] [181-203] [257-271] [301-35] | [257-271] [301-31] | 206 -> 112 | 24 -> 328 | yes -> yes |
| banyans | Hawaii | 240 | [33-107] [125-151] [181-303] [341-359] | [181-303] | 256 -> 126 | 242 -> 240 | yes -> yes |
| banzai-pipeline | Hawaii | 315 | [61-87] [105-111] [141-147] [181-203] [257-271] [301-35] | [257-271] [301-31] | 192 -> 112 | 20 -> 328 | yes -> yes |
| barbers-point | Hawaii | 210 | [113-263] [285-319] [345-39] | [113-263] [285-315] | 250 -> 188 | 240 -> 212 | yes -> yes |
| bellows-beach | Hawaii | 72 | [25-95] [181-183] [201-231] [285-299] | [25-95] | 132 -> 74 | 60 -> 60 | yes -> yes |
| big-beach-makena | Hawaii | 236 | [9-83] [101-107] [189-211] [269-295] | [189-211] [269-295] | 144 -> 56 | 200 -> 264 | no -> no |
| canoes-waikiki | Hawaii | 205 | [41-75] [117-267] [321-347] | [161-267] | 222 -> 110 | 176 -> 212 | yes -> yes |
| chuns-reef | Hawaii | 320 | [49-67] [89-99] [177-215] [265-279] [301-19] | [265-279] [301-19] | 178 -> 100 | 348 -> 324 | yes -> yes |
| diamond-head-cliffs | Hawaii | 180 | [45-51] [117-271] [325-335] | [117-271] | 182 -> 158 | 192 -> 192 | yes -> yes |
| ehukai-beach-park | Hawaii | 315 | [61-83] [105-111] [141-143] [181-203] [257-271] [301-27] | [257-271] [301-27] | 176 -> 108 | 8 -> 328 | yes -> yes |
| electric-beach | Hawaii | 245 | [21-67] [117-155] [181-263] [285-307] [341-3] | [181-263] [285-307] | 230 -> 112 | 248 -> 240 | yes -> yes |
| flemings-beach | Hawaii | 348 | [73-87] [161-199] [261-271] [349-31] | [261-271] [349-31] | 120 -> 60 | 10 -> 344 | yes -> yes |
| haleiwa-ali-i-beach | Hawaii | 330 | [53-59] [81-103] [129-135] [169-199] [217-235] [273-27] | [273-23] | 220 -> 114 | 332 -> 328 | yes -> yes |
| hanalei-bay | Hawaii | 348 | [89-135] [161-187] [205-235] [265-279] [301-63] | [309-11] | 258 -> 66 | 0 -> 340 | yes -> yes |
| hapuna-beach | Hawaii | 285 | [9-99] [117-203] [237-263] [285-311] | [237-263] [285-311] | 244 -> 60 | 128 -> 280.0 | yes -> yes |
| honoli-i | Hawaii | 94 | [13-95] [189-203] [221-263] [281-283] [325-339] | [13-95] | 174 -> 86 | 54 -> 52 | yes -> yes |
| honolua-bay | Hawaii | 278 | [77-91] [161-211] [265-271] [341-47] | [265-271] [341-47] | 152 -> 80 | 84 -> 350.0 | no -> no |
| ho-okipa | Hawaii | 340 | [121-135] [157-239] [313-47] | [313-43] | 202 -> 94 | 0 -> 0 | yes -> yes |
| kaisers | Hawaii | 210 | [9-11] [45-67] [117-119] [145-267] [329-339] | [145-267] | 178 -> 126 | 180 -> 204 | yes -> yes |
| kanaha-beach | Hawaii | 337 | [105-131] [153-171] [189-259] [325-51] | [325-47] | 216 -> 86 | 8 -> 4 | yes -> yes |
| keaau-beach | Hawaii | 266 | [5-35] [57-87] [133-135] [165-263] [301-323] | [165-263] [301-323] | 202 -> 128 | 184 -> 236 | no -> no |
| kiahuna-beach | Hawaii | 217 | [17-39] [77-271] [297-359] | [97-271] | 290 -> 178 | 200 -> 184 | yes -> yes |
| kolekole-beach-park | Hawaii | 57 | [177-259] [277-291] [341-115] | [345-115] | 242 -> 134 | 0 -> 48 | yes -> yes |
| lahaina-breakwall | Hawaii | 240 | [9-51] [69-71] [89-107] [149-227] | [149-227] | 156 -> 82 | 188 -> 192 | no -> no |
| laniakea | Hawaii | 325 | [49-95] [133-139] [173-211] [265-279] [301-23] | [265-279] [301-23] | 206 -> 104 | 20 -> 328 | yes -> yes |
| launiupoko | Hawaii | 230 | [57-63] [81-99] [149-151] [185-239] [357-35] | [149-151] [185-239] | 138 -> 64 | 212 -> 204 | yes -> yes |
| log-cabins | Hawaii | 302 | [53-71] [93-107] [141-143] [181-203] [257-271] [301-27] | [257-271] [301-23] | 180 -> 104 | 12 -> 324 | yes -> yes |
| lymans | Hawaii | 254 | [25-107] [125-151] [181-307] [341-359] | [181-307] | 268 -> 130 | 244 -> 244 | yes -> yes |
| makaha | Hawaii | 240 | [49-67] [129-131] [157-263] [301-323] [357-31] | [161-263] [301-319] | 202 -> 128 | 208 -> 232 | yes -> yes |
| makapuu-beach-park | Hawaii | 60 | [129-203] [229-239] [257-259] [293-299] [1-91] | [17-91] [129-187] | 202 -> 140 | 46 -> 96 | yes -> yes |
| off-the-wall | Hawaii | 315 | [61-75] [93-111] [181-203] [257-271] [301-35] | [257-271] [301-31] | 182 -> 112 | 12 -> 328 | yes -> yes |
| pakala | Hawaii | 234 | [77-99] [129-247] [281-287] [325-51] | [153-247] [281-287] | 248 -> 108 | 188 -> 212 | yes -> yes |
| peahi-jaws | Hawaii | 10 | [133-139] [165-199] [217-247] [305-95] | [305-95] | 236 -> 154 | 40 -> 20 | yes -> yes |
| pine-trees-hawaii | Hawaii | 245 | [41-131] [177-323] [349-7] | [177-323] | 266 -> 150 | 268 -> 248 | yes -> yes |
| poipu-beach | Hawaii | 189 | [13-23] [81-271] [301-355] | [93-271] | 266 -> 182 | 200 -> 180 | yes -> yes |
| polihale-beach | Hawaii | 305 | [41-51] [69-131] [149-223] [241-263] [281-15] | [241-263] [281-15] | 282 -> 124 | 212 -> 308 | yes -> yes |
| puaena-point | Hawaii | 335 | [53-63] [81-91] [169-203] [269-283] [301-31] | [269-283] [301-31] | 178 -> 112 | 12 -> 332 | yes -> yes |
| publics-waikiki | Hawaii | 232 | [25-35] [53-59] [117-271] [329-347] | [129-271] | 204 -> 146 | 184 -> 200 | yes -> yes |
| p-p-kea-hawaii | Hawaii | 315 | [61-83] [101-111] [145-155] [181-207] [253-271] [301-35] | [253-271] [301-31] | 204 -> 116 | 16 -> 328 | yes -> yes |
| queens-waikiki | Hawaii | 233 | [53-63] [117-267] [321-347] | [157-267] | 198 -> 114 | 184 -> 212 | yes -> yes |
| richardson-beach | Hawaii | 345 | [169-251] [337-131] | [337-111] | 244 -> 138 | 96 -> 44 | yes -> yes |
| rocky-point | Hawaii | 310 | [57-79] [101-111] [145-147] [181-207] [253-271] [301-35] | [253-271] [301-31] | 196 -> 116 | 8 -> 328 | yes -> yes |
| sandy-beach-oahu | Hawaii | 169 | [29-39] [57-87] [125-219] [297-299] [353-3] | [57-87] [125-219] | 166 -> 132 | 172 -> 144 | yes -> yes |
| sunset-beach-hawaii | Hawaii | 315 | [57-87] [109-115] [145-147] [185-211] [253-271] [301-35] | [253-271] [301-31] | 200 -> 116 | 12 -> 328 | yes -> yes |
| thousand-peaks-maui | Hawaii | 210 | [33-83] [149-159] [205-251] [349-15] | [149-159] [205-251] | 148 -> 64 | 60 -> 200.0 | yes -> yes |
| tunnels-beach | Hawaii | 319 | [121-235] [265-75] | [285-75] | 292 -> 154 | 288 -> 0 | yes -> yes |
| turtle-bay | Hawaii | 307 | [157-163] [189-215] [253-271] [301-103] | [261-275] [301-55] | 228 -> 136 | 8 -> 344 | yes -> yes |
| velzyland | Hawaii | 320 | [65-87] [109-115] [185-211] [253-271] [301-35] | [253-271] [301-35] | 186 -> 120 | 352 -> 328 | yes -> yes |
| waimea-bay | Hawaii | 300 | [49-67] [137-143] [177-215] [261-275] [301-31] | [261-275] [301-7] | 186 -> 88 | 4 -> 316 | yes -> yes |
| white-plains-beach | Hawaii | 164 | [113-255] [277-279] [297-307] [325-11] | [113-255] | 216 -> 146 | 232 -> 184 | yes -> yes |
| yokohama-bay | Hawaii | 245 | [77-79] [113-115] [141-143] [181-263] [281-299] [321-47] | [181-263] [281-299] | 216 -> 108 | 222 -> 240 | yes -> yes |
| fortunes-rocks | Maine | 90 | [10-170] | [10-170] | 160 -> 160 | 90 -> 90 | yes -> yes |
| gooch-s-beach | Maine | 167 | [87-247] | [87-247] | 160 -> 160 | 167 -> 167 | yes -> yes |
| higgins-beach | Maine | 164 | [84-244] | [84-244] | 160 -> 160 | 164 -> 164 | yes -> yes |
| long-sands-beach | Maine | 124 | [44-204] | [44-204] | 160 -> 160 | 124 -> 124 | yes -> yes |
| old-orchard-beach | Maine | 115 | [35-195] | [35-195] | 160 -> 160 | 115 -> 115 | yes -> yes |
| scarborough-beach | Maine | 121 | [41-201] | [41-201] | 160 -> 160 | 121 -> 121 | yes -> yes |
| short-sands-beach-york | Maine | 56 | [336-359] [0-136] | [336-359] [0-136] | 161 -> 161 | 56 -> 56 | yes -> yes |
| assateague-island-md | Maryland | 106 | [33-187] | [33-187] | 158 -> 158 | 108 -> 108 | yes -> yes |
| ocean-city-md-28th-street | Maryland | 105 | [29-195] | [33-195] | 170 -> 166 | 116 -> 112 | yes -> yes |
| ocean-city-md-inlet | Maryland | 105 | [29-195] | [29-195] | 170 -> 170 | 112 -> 112 | yes -> yes |
| cisco-beach | Massachusetts | 200 | [69-103] [121-279] | [121-279] | 200 -> 162 | 176 -> 200 | yes -> yes |
| coast-guard-beach | Massachusetts | 85 | [5-165] | [5-165] | 160 -> 160 | 85 -> 85 | yes -> yes |
| good-harbor-beach | Massachusetts | 135 | [90-180] | [90-180] | 90 -> 90 | 135 -> 135 | yes -> yes |
| horseneck-beach | Massachusetts | 206 | [161-235] [349-351] | [161-235] | 84 -> 78 | 204 -> 196 | yes -> yes |
| marconi-beach | Massachusetts | 73 | [349-159] | [349-159] | 174 -> 174 | 72 -> 72 | yes -> yes |
| nahant-beach | Massachusetts | 96 | [16-176] | [16-176] | 160 -> 160 | 96 -> 96 | yes -> yes |
| nantasket-beach | Massachusetts | 48 | [328-359] [0-128] | [328-359] [0-128] | 161 -> 161 | 48 -> 48 | yes -> yes |
| nantucket-surf | Massachusetts | 70 | [350-359] [0-150] | [350-359] [0-150] | 161 -> 161 | 70 -> 70 | yes -> yes |
| nauset-beach | Massachusetts | 92 | [12-172] | [12-172] | 160 -> 160 | 92 -> 92 | yes -> yes |
| plum-island | Massachusetts | 85 | [5-165] | [5-165] | 160 -> 160 | 85 -> 85 | yes -> yes |
| hampton-beach | New Hampshire | 100 | [20-180] | [20-180] | 160 -> 160 | 100 -> 100 | yes -> yes |
| jenness-beach | New Hampshire | 121 | [41-201] | [41-201] | 160 -> 160 | 121 -> 121 | yes -> yes |
| north-beach-rye | New Hampshire | 99 | [19-179] | [19-179] | 160 -> 160 | 99 -> 99 | yes -> yes |
| the-wall-hampton | New Hampshire | 129 | [49-209] | [49-209] | 160 -> 160 | 129 -> 129 | yes -> yes |
| asbury-park | New Jersey | 105 | [25-185] | [25-185] | 160 -> 160 | 105 -> 105 | yes -> yes |
| atlantic-city | New Jersey | 141 | [49-207] | [49-207] | 162 -> 162 | 128 -> 128 | yes -> yes |
| avalon-nj | New Jersey | 120 | [49-195] | [49-195] | 150 -> 150 | 120 -> 120 | yes -> yes |
| avon-by-the-sea | New Jersey | 99 | [19-179] | [19-179] | 160 -> 160 | 99 -> 99 | yes -> yes |
| barnegat-light | New Jersey | 129 | [29-199] | [29-199] | 174 -> 174 | 112 -> 112 | yes -> yes |
| bay-head | New Jersey | 100 | [53-183] | [53-183] | 134 -> 134 | 116 -> 116 | yes -> yes |
| belmar | New Jersey | 104 | [24-184] | [24-184] | 160 -> 160 | 104 -> 104 | yes -> yes |
| bradley-beach | New Jersey | 100 | [20-180] | [20-180] | 160 -> 160 | 100 -> 100 | yes -> yes |
| brigantine | New Jersey | 132 | [45-211] | [45-211] | 170 -> 170 | 128 -> 128 | yes -> yes |
| deal | New Jersey | 105 | [25-185] | [25-185] | 160 -> 160 | 105 -> 105 | yes -> yes |
| harvey-cedars | New Jersey | 104 | [29-199] | [29-199] | 174 -> 174 | 112 -> 112 | yes -> yes |
| island-beach-state-park | New Jersey | 100 | [41-179] | [41-179] | 142 -> 142 | 108 -> 108 | yes -> yes |
| long-beach-island | New Jersey | 130 | [37-203] | [37-203] | 170 -> 170 | 120 -> 120 | yes -> yes |
| long-branch | New Jersey | 95 | [15-175] | [15-175] | 160 -> 160 | 95 -> 95 | yes -> yes |
| manasquan-inlet | New Jersey | 80 | [57-183] [245-247] | [57-183] | 136 -> 130 | 128 -> 120 | yes -> yes |
| margate | New Jersey | 145 | [69-203] | [69-203] | 138 -> 138 | 136 -> 136 | yes -> yes |
| ocean-city-nj | New Jersey | 140 | [61-199] | [61-199] | 142 -> 142 | 128 -> 128 | yes -> yes |
| pier-village | New Jersey | 95 | [15-175] | [15-175] | 160 -> 160 | 95 -> 95 | yes -> yes |
| point-pleasant-beach | New Jersey | 105 | [53-183] | [53-183] | 134 -> 134 | 120 -> 116 | yes -> yes |
| sandy-hook | New Jersey | 70 | [77-163] | [77-163] | 90 -> 90 | 124 -> 120 | no -> no |
| sea-bright | New Jersey | 90 | [69-175] | [69-175] | 110 -> 110 | 120 -> 120 | yes -> yes |
| sea-isle-city | New Jersey | 120 | [53-191] | [53-191] | 142 -> 142 | 120 -> 120 | yes -> yes |
| seaside-heights | New Jersey | 97 | [45-179] | [45-179] | 138 -> 138 | 112 -> 112 | yes -> yes |
| seaside-park | New Jersey | 100 | [45-179] | [45-179] | 138 -> 138 | 112 -> 112 | yes -> yes |
| ship-bottom | New Jersey | 119 | [37-203] | [37-203] | 170 -> 170 | 120 -> 120 | yes -> yes |
| spring-lake | New Jersey | 100 | [20-180] | [20-180] | 160 -> 160 | 100 -> 100 | yes -> yes |
| stone-harbor | New Jersey | 125 | [49-191] | [49-191] | 146 -> 146 | 120 -> 120 | yes -> yes |
| strathmere | New Jersey | 125 | [57-195] | [57-195] | 142 -> 142 | 124 -> 124 | yes -> yes |
| surf-city-nj | New Jersey | 119 | [37-203] | [37-203] | 170 -> 170 | 120 -> 120 | yes -> yes |
| wildwood | New Jersey | 135 | [49-191] | [49-191] | 146 -> 146 | 120 -> 120 | yes -> yes |
| cupsogue-beach | New York | 160 | [81-239] | [81-239] | 162 -> 162 | 160 -> 160 | yes -> yes |
| ditch-plains | New York | 160 | [81-239] | [81-239] | 162 -> 162 | 156 -> 160 | yes -> yes |
| fire-island | New York | 170 | [81-219] | [81-219] | 142 -> 142 | 148 -> 148 | yes -> yes |
| flying-point-beach | New York | 152 | [73-239] | [73-239] | 170 -> 170 | 156 -> 156 | yes -> yes |
| georgica-beach | New York | 155 | [75-235] | [75-235] | 160 -> 160 | 155 -> 155 | yes -> yes |
| gilgo-beach | New York | 159 | [93-207] | [93-207] | 118 -> 118 | 148 -> 148 | yes -> yes |
| jones-beach | New York | 155 | [75-235] | [75-235] | 160 -> 160 | 155 -> 155 | yes -> yes |
| lido-beach | New York | 182 | [102-262] | [102-262] | 160 -> 160 | 182 -> 182 | yes -> yes |
| long-beach-ny | New York | 181 | [101-195] | [101-195] | 98 -> 98 | 148 -> 148 | yes -> yes |
| main-beach-east-hampton | New York | 155 | [73-235] | [73-235] | 166 -> 166 | 152 -> 152 | yes -> yes |
| mecox-beach | New York | 155 | [75-235] | [75-235] | 160 -> 160 | 155 -> 155 | yes -> yes |
| montauk-point | New York | 90 | [77-235] | [77-207] | 162 -> 134 | 156 -> 140 | yes -> yes |
| point-lookout | New York | 196 | [116-276] | [116-276] | 160 -> 160 | 196 -> 196 | yes -> yes |
| ponquogue-beach | New York | 169 | [85-239] | [85-239] | 158 -> 158 | 160 -> 160 | yes -> yes |
| robert-moses-state-park | New York | 174 | [85-215] | [89-215] | 134 -> 130 | 148 -> 152 | yes -> yes |
| rockaway-67th-street | New York | 170 | [101-187] | [101-187] | 90 -> 90 | 140 -> 144 | yes -> yes |
| rockaway-90th-street | New York | 165 | [97-187] | [97-187] | 94 -> 94 | 140 -> 140 | yes -> yes |
| rockaway-beach | New York | 161 | [97-187] | [97-187] | 94 -> 94 | 140 -> 140 | yes -> yes |
| shinnecock-inlet | New York | 176 | [73-243] | [73-243] | 174 -> 174 | 156 -> 156 | yes -> yes |
| smith-point-beach | New York | 156 | [76-236] | [76-236] | 160 -> 160 | 156 -> 156 | yes -> yes |
| turtle-cove-montauk | New York | 121 | [77-215] [233-235] | [77-215] | 148 -> 142 | 156 -> 144 | yes -> yes |
| atlantic-beach-nc | North Carolina | 185 | [129-239] | [129-239] | 114 -> 114 | 184 -> 184 | yes -> yes |
| avalon-pier | North Carolina | 63 | [349-143] | [349-143] | 158 -> 158 | 64 -> 64 | yes -> yes |
| avon-pier | North Carolina | 97 | [17-183] | [17-183] | 170 -> 170 | 100 -> 100 | yes -> yes |
| cape-hatteras-lighthouse | North Carolina | 161 | [25-231] | [53-231] | 210 -> 182 | 124 -> 140 | yes -> yes |
| carolina-beach | North Carolina | 110 | [53-187] | [53-187] | 138 -> 138 | 120 -> 120 | yes -> yes |
| corolla-beach | North Carolina | 77 | [1-151] | [1-151] | 154 -> 154 | 76 -> 76 | yes -> yes |
| duck-pier | North Carolina | 72 | [353-147] | [353-147] | 158 -> 158 | 68 -> 68 | yes -> yes |
| emerald-isle | North Carolina | 170 | [89-223] | [89-223] | 138 -> 138 | 156 -> 156 | yes -> yes |
| frisco-pier | North Carolina | 163 | [13-19] [69-71] [101-227] | [101-227] | 146 -> 130 | 148 -> 164 | yes -> yes |
| holden-beach | North Carolina | 175 | [17-39] [109-223] [349-355] | [109-223] | 154 -> 118 | 132 -> 164 | yes -> yes |
| jennettes-pier | North Carolina | 65 | [345-359] [0-145] | [345-359] [0-145] | 161 -> 161 | 65 -> 65 | yes -> yes |
| kill-devil-hills | North Carolina | 62 | [349-147] | [349-143] | 162 -> 158 | 68 -> 64 | yes -> yes |
| kitty-hawk-pier | North Carolina | 69 | [353-143] | [353-143] | 154 -> 154 | 68 -> 68 | yes -> yes |
| kure-beach | North Carolina | 108 | [49-187] | [49-187] | 142 -> 142 | 116 -> 116 | yes -> yes |
| nags-head-pier | North Carolina | 66 | [346-359] [0-146] | [346-359] [0-146] | 161 -> 161 | 66 -> 66 | yes -> yes |
| ocean-isle-beach | North Carolina | 170 | [17-47] [97-215] [349-355] | [97-215] | 166 -> 122 | 124 -> 156 | yes -> yes |
| ocracoke-beach | North Carolina | 141 | [69-219] | [69-219] | 154 -> 154 | 140 -> 144 | yes -> yes |
| oregon-inlet | North Carolina | 82 | [181-215] [349-151] | [181-215] [349-131] | 204 -> 184 | 92 -> 80 | yes -> yes |
| rodanthe | North Carolina | 90 | [213-219] [237-239] [353-183] | [213-219] [237-239] [1-171] | 210 -> 190 | 104 -> 100 | yes -> yes |
| salvo | North Carolina | 100 | [5-179] [205-207] | [13-179] | 184 -> 170 | 108 -> 96 | yes -> yes |
| sunset-beach-nc | North Carolina | 155 | [75-235] | [75-235] | 160 -> 160 | 155 -> 155 | yes -> yes |
| surf-city-nc | North Carolina | 145 | [77-211] [345-351] | [77-211] | 148 -> 138 | 140 -> 144 | yes -> yes |
| the-point-buxton | North Carolina | 92 | [17-235] | [17-235] | 222 -> 222 | 124 -> 124 | yes -> yes |
| topsail-beach | North Carolina | 140 | [73-207] | [73-207] | 138 -> 138 | 140 -> 140 | yes -> yes |
| wrightsville-beach | North Carolina | 115 | [61-195] | [61-195] | 138 -> 138 | 128 -> 128 | yes -> yes |
| agate-beach-newport | Oregon | 260 | [37-47] [193-351] | [193-347] | 176 -> 158 | 288 -> 268 | yes -> yes |
| bandon-beach | Oregon | 279 | [41-43] [145-171] [209-359] | [209-351] | 190 -> 146 | 272 -> 280 | yes -> yes |
| bastendorff-beach | Oregon | 305 | [145-187] [205-215] [245-3] | [257-3] | 182 -> 110 | 256 -> 308 | yes -> yes |
| brookings-beach | Oregon | 230 | [37-39] [185-279] [321-335] [357-7] | [185-263] | 136 -> 82 | 276 -> 228 | yes -> yes |
| cannon-beach | Oregon | 272 | [149-155] [201-335] | [201-335] | 148 -> 138 | 260 -> 268 | yes -> yes |
| cape-kiwanda | Oregon | 275 | [37-47] [197-343] | [197-343] | 164 -> 150 | 276 -> 268 | yes -> yes |
| florence-south-jetty | Oregon | 268 | [37-43] [145-171] [201-355] | [201-355] | 198 -> 158 | 272 -> 276 | yes -> yes |
| gold-beach | Oregon | 260 | [141-151] [189-339] | [189-335] | 168 -> 150 | 256 -> 260 | yes -> yes |
| hubbard-creek | Oregon | 235 | [181-263] [305-319] [337-3] | [181-263] | 134 -> 86 | 260 -> 220 | yes -> yes |
| indian-beach-ecola | Oregon | 275 | [149-159] [197-331] | [197-331] | 152 -> 138 | 252 -> 264 | yes -> yes |
| lincoln-city-beach | Oregon | 275 | [37-47] [149-171] [201-351] | [201-351] | 194 -> 154 | 272 -> 276 | yes -> yes |
| nelscott-reef | Oregon | 275 | [37-47] [149-163] [201-355] | [201-355] | 190 -> 158 | 284 -> 276 | yes -> yes |
| nye-beach | Oregon | 270 | [37-47] [193-327] | [193-327] | 152 -> 138 | 272 -> 260 | yes -> yes |
| otter-rock | Oregon | 295 | [37-47] [145-159] [189-359] | [189-359] | 206 -> 174 | 272 -> 272 | yes -> yes |
| pacific-city-beach | Oregon | 275 | [37-47] [149-163] [201-343] | [201-343] | 178 -> 146 | 276 -> 272 | yes -> yes |
| roads-end | Oregon | 275 | [37-47] [149-171] [201-347] | [201-347] | 190 -> 150 | 272 -> 272 | yes -> yes |
| seaside-cove | Oregon | 295 | [149-167] [201-223] [241-335] | [261-335] | 146 -> 78 | 252 -> 296 | yes -> yes |
| short-sands-beach-oswald-west | Oregon | 241 | [45-47] [189-339] | [189-339] | 160 -> 154 | 268 -> 264 | yes -> yes |
| south-beach-newport | Oregon | 270 | [145-163] [193-347] | [193-347] | 180 -> 158 | 256 -> 268 | yes -> yes |
| whiskey-run-beach | Oregon | 280 | [145-171] [205-351] | [205-351] | 180 -> 150 | 256 -> 276 | yes -> yes |
| antonio-s | Puerto Rico | 341 | [89-115] [189-239] [257-263] [281-27] | [257-263] [281-27] | 204 -> 120 | 292 -> 324 | yes -> yes |
| aviones | Puerto Rico | 19 | [137-179] [197-215] [297-83] | [297-83] | 218 -> 150 | 48 -> 8 | yes -> yes |
| crash-boat-beach | Puerto Rico | 254 | [65-75] [97-131] [153-159] [233-247] [317-47] | [233-247] [301-339] | 174 -> 60 | 40 -> 305.0 | no -> no |
| dogmans-rincon | Puerto Rico | 251 | [57-67] [89-115] [181-239] [257-271] [289-23] | [181-239] [257-271] [289-311] | 222 -> 106 | 304 -> 244 | no -> no |
| gas-chambers-aguadilla | Puerto Rico | 262 | [97-123] [233-247] [317-79] | [233-247] [301-351] | 174 -> 72 | 36 -> 315.0 | no -> no |
| indicators | Puerto Rico | 272 | [77-107] [181-235] [257-267] [285-31] | [181-235] [257-267] [285-31] | 216 -> 182 | 300 -> 288 | no -> no |
| jobos-beach-isabela | Puerto Rico | 5 | [113-143] [201-207] [237-239] [269-83] | [269-83] | 228 -> 178 | 8 -> 356 | yes -> yes |
| la-pared-luquillo | Puerto Rico | 44 | [205-227] [249-271] [313-67] | [313-67] | 170 -> 118 | 324 -> 8 | yes -> yes |
| maria-s | Puerto Rico | 256 | [89-107] [181-235] [257-267] [285-31] | [181-235] [257-267] [285-31] | 204 -> 182 | 304 -> 288 | yes -> yes |
| middles-isabela | Puerto Rico | 10 | [109-127] [145-167] [221-235] [253-271] [293-83] | [293-83] | 242 -> 154 | 4 -> 8 | yes -> yes |
| pine-grove-beach | Puerto Rico | 4 | [133-151] [169-187] [205-219] [237-251] [293-43] | [293-43] | 194 -> 114 | 348 -> 348 | yes -> yes |
| playa-guajataca | Puerto Rico | 5 | [109-127] [145-151] [185-195] [221-239] [257-263] [293-83] | [293-83] | 232 -> 154 | 16 -> 8 | yes -> yes |
| playa-kikita | Puerto Rico | 5 | [145-163] [217-231] [281-87] | [281-87] | 210 -> 170 | 8 -> 4 | yes -> yes |
| playa-manati | Puerto Rico | 22 | [133-139] [173-195] [225-251] [281-79] | [281-79] | 228 -> 162 | 328 -> 0 | yes -> yes |
| pools | Puerto Rico | 328 | [89-119] [181-235] [253-263] [281-31] | [213-235] [253-263] [281-31] | 220 -> 154 | 288 -> 304 | yes -> yes |
| rincon-domes | Puerto Rico | 290 | [81-107] [181-235] [257-267] [285-31] | [181-235] [257-267] [285-31] | 212 -> 182 | 296 -> 288 | yes -> yes |
| sandy-beach | Puerto Rico | 342 | [89-123] [181-235] [253-263] [281-31] | [245-263] [281-31] | 224 -> 136 | 284 -> 320 | yes -> yes |
| shacks-beach | Puerto Rico | 350 | [109-139] [173-179] [201-203] [233-235] [265-83] | [265-83] | 238 -> 182 | 4 -> 352 | yes -> yes |
| steps-beach-rincon | Puerto Rico | 250 | [69-107] [181-235] [257-271] [289-31] | [181-235] [257-271] [289-27] | 224 -> 178 | 312 -> 288 | no -> no |
| surfers-beach-aguadilla | Puerto Rico | 335 | [105-139] [241-255] [317-75] | [241-255] [317-75] | 178 -> 140 | 44 -> 356 | yes -> yes |
| table-tops-aguadilla | Puerto Rico | 282 | [69-75] [93-115] [245-251] [301-327] [345-51] | [301-327] | 146 -> 30 | 36 -> 350.0 | no -> no |
| tres-palmas | Puerto Rico | 230 | [89-127] [181-235] [257-271] [289-27] | [181-235] [257-271] [289-7] | 220 -> 158 | 296 -> 276 | yes -> yes |
| wilderness | Puerto Rico | 305 | [85-143] [225-243] [317-63] | [225-243] [317-47] | 194 -> 116 | 56 -> 344 | no -> no |
| charlestown-town-beach | Rhode Island | 146 | [97-151] [181-203] [221-227] [333-15] | [97-151] [181-203] [221-227] | 140 -> 94 | 140 -> 156 | yes -> yes |
| conant-ave | Rhode Island | 92 | [85-187] [217-235] | [85-187] [217-235] | 128 -> 128 | 156 -> 160 | yes -> yes |
| east-matunuck-state-beach | Rhode Island | 178 | [98-258] | [98-258] | 160 -> 160 | 178 -> 178 | yes -> yes |
| marys | Rhode Island | 180 | [100-260] | [100-260] | 160 -> 160 | 180 -> 180 | yes -> yes |
| matunuck | Rhode Island | 175 | [95-255] | [95-255] | 160 -> 160 | 175 -> 175 | yes -> yes |
| misquamicut-state-beach | Rhode Island | 160 | [93-111] [149-183] [209-223] [333-23] | [93-111] [149-183] [209-223] | 132 -> 78 | 116 -> 160 | yes -> yes |
| monahans | Rhode Island | 102 | [89-187] [333-355] | [89-167] | 128 -> 82 | 132 -> 128 | yes -> yes |
| moonstone-beach-ri | Rhode Island | 161 | [81-241] | [81-241] | 160 -> 160 | 161 -> 161 | yes -> yes |
| narragansett-beach | Rhode Island | 130 | [50-210] | [50-210] | 160 -> 160 | 130 -> 130 | yes -> yes |
| pilgrim-ave | Rhode Island | 92 | [85-187] [217-235] | [85-187] [217-235] | 128 -> 128 | 156 -> 160 | yes -> yes |
| point-judith | Rhode Island | 121 | [81-187] [217-235] [353-355] | [81-187] [217-235] | 138 -> 132 | 152 -> 156 | yes -> yes |
| ruggles | Rhode Island | 135 | [109-203] [221-227] | [109-203] | 108 -> 98 | 168 -> 160 | yes -> yes |
| second-beach-newport | Rhode Island | 209 | [133-223] | [133-223] | 94 -> 94 | 176 -> 176 | yes -> yes |
| third-beach-newport | Rhode Island | 90 | [141-223] | [141-171] [193-223] | 86 -> 68 | 180 -> 176 | no -> no |
| cherry-grove-pier | South Carolina | 155 | [75-235] | [75-235] | 160 -> 160 | 155 -> 155 | yes -> yes |
| edisto-beach | South Carolina | 138 | [77-207] [353-359] | [77-207] | 144 -> 134 | 132 -> 140 | yes -> yes |
| folly-beach-pier | South Carolina | 154 | [69-219] | [69-219] | 154 -> 154 | 140 -> 144 | yes -> yes |
| folly-beach-washout | South Carolina | 135 | [61-223] | [61-223] | 166 -> 166 | 144 -> 140 | yes -> yes |
| garden-city-pier | South Carolina | 125 | [17-31] [77-195] [233-255] [349-355] | [77-195] | 176 -> 122 | 132 -> 136 | yes -> yes |
| hilton-head-island | South Carolina | 140 | [61-203] | [61-203] | 146 -> 146 | 132 -> 132 | yes -> yes |
| hunting-island | South Carolina | 105 | [65-183] | [65-183] | 122 -> 122 | 124 -> 124 | yes -> yes |
| isle-of-palms | South Carolina | 154 | [73-215] | [73-215] | 146 -> 146 | 140 -> 144 | yes -> yes |
| kiawah-island-beach | South Carolina | 160 | [89-215] [349-359] | [89-215] | 144 -> 130 | 140 -> 152 | yes -> yes |
| myrtle-beach-state-park | South Carolina | 130 | [17-35] [81-199] [233-251] [349-355] | [81-199] | 176 -> 122 | 132 -> 140 | yes -> yes |
| pawleys-island | South Carolina | 115 | [17-19] [65-183] [253-255] [349-355] | [65-183] | 144 -> 122 | 112 -> 124 | yes -> yes |
| surfside-beach-sc | South Carolina | 130 | [17-31] [77-195] [233-255] [349-355] | [77-195] | 176 -> 122 | 132 -> 136 | yes -> yes |
| 61st-street-galveston | Texas | 145 | [69-223] | [69-223] | 158 -> 158 | 144 -> 144 | yes -> yes |
| bob-hall-pier | Texas | 113 | [41-187] | [41-187] | 150 -> 150 | 112 -> 112 | yes -> yes |
| galveston-seawall | Texas | 137 | [73-223] | [73-223] | 154 -> 154 | 148 -> 148 | yes -> yes |
| horace-caldwell-pier | Texas | 137 | [49-199] | [49-199] | 154 -> 154 | 124 -> 124 | yes -> yes |
| isla-blanca-park | Texas | 70 | [1-167] | [1-167] | 170 -> 170 | 84 -> 84 | yes -> yes |
| jp-luby-surf-park | Texas | 107 | [45-191] | [45-191] | 150 -> 150 | 116 -> 116 | yes -> yes |
| matagorda-beach | Texas | 156 | [73-235] | [73-235] | 166 -> 166 | 152 -> 152 | yes -> yes |
| mustang-island | Texas | 114 | [49-195] | [49-195] | 150 -> 150 | 120 -> 120 | yes -> yes |
| port-a-beach | Texas | 125 | [49-199] | [49-199] | 154 -> 154 | 124 -> 124 | yes -> yes |
| port-aransas-jetty | Texas | 135 | [49-199] | [49-199] | 154 -> 154 | 124 -> 124 | yes -> yes |
| south-padre-island | Texas | 78 | [1-163] | [1-163] | 166 -> 166 | 80 -> 80 | yes -> yes |
| surfside-beach-tx | Texas | 140 | [57-211] [265-299] | [57-211] | 196 -> 158 | 160 -> 132 | yes -> yes |
| croatan-beach | Virginia | 80 | [0-160] | [0-160] | 160 -> 160 | 80 -> 80 | yes -> yes |
| first-street-jetty-vb | Virginia | 81 | [25-155] [221-243] | [25-155] | 160 -> 134 | 100 -> 84 | yes -> yes |
| sandbridge-beach | Virginia | 77 | [357-359] [0-157] | [357-359] [0-157] | 161 -> 161 | 77 -> 77 | yes -> yes |
| virginia-beach-oceanfront | Virginia | 75 | [25-155] [217-243] | [25-155] | 164 -> 134 | 104 -> 84 | yes -> yes |
| cape-disappointment | Washington | 249 | [181-347] | [181-347] | 170 -> 170 | 264 -> 264 | yes -> yes |
| hobuck-beach | Washington | 245 | [117-131] [153-163] [205-295] | [205-279] | 126 -> 78 | 216 -> 240 | yes -> yes |
| kalaloch-beach | Washington | 260 | [57-91] [109-115] [181-323] [353-355] | [181-323] | 200 -> 146 | 248 -> 252 | yes -> yes |
| la-push-first-beach | Washington | 197 | [69-87] [105-119] [177-299] | [177-299] | 166 -> 126 | 224 -> 248 | yes -> yes |
| la-push-second-beach | Washington | 228 | [81-103] [165-307] | [165-307] | 172 -> 146 | 220 -> 236 | yes -> yes |
| la-push-third-beach | Washington | 192 | [77-115] [169-279] | [169-279] | 156 -> 114 | 200 -> 224 | yes -> yes |
| long-beach-wa | Washington | 275 | [197-347] | [197-347] | 154 -> 154 | 272 -> 272 | yes -> yes |
| ocean-shores | Washington | 263 | [185-335] | [185-335] | 154 -> 154 | 264 -> 260 | yes -> yes |
| pacific-beach-wa | Washington | 257 | [41-59] [181-327] [1-15] | [181-327] | 190 -> 150 | 284 -> 252 | yes -> yes |
| rialto-beach | Washington | 275 | [81-119] [177-287] [305-311] | [177-287] [305-311] | 166 -> 124 | 220 -> 248 | yes -> yes |
| shi-shi-beach | Washington | 285 | [109-131] [153-155] [221-311] | [225-311] | 126 -> 90 | 224 -> 268 | yes -> yes |
| tokeland | Washington | 237 | [209-275] [293-327] | [209-275] | 108 -> 70 | 276 -> 240 | yes -> yes |
| westport-jetty | Washington | 268 | [185-307] | [185-307] | 126 -> 126 | 244 -> 244 | yes -> yes |
| westport-light-state-park | Washington | 262 | [185-315] | [185-315] | 134 -> 134 | 248 -> 248 | yes -> yes |
| fiji | Puerto Rico | 43 | [149-163] [281-87] | [281-87] | 188 -> 170 | 16 -> 4 | yes -> yes |
| tijuana-slough | California | 260 | [93-143] [181-199] [237-311] | [181-199] [237-311] | 154 -> 100 | 204 -> 252 | yes -> yes |
| avalanches | California | 274 | [93-103] [177-323] | [197-323] | 164 -> 130 | 240 -> 260 | yes -> yes |
| pb-point | California | 212 | [93-131] [181-319] | [181-315] | 184 -> 138 | 224 -> 248 | yes -> yes |
| law-street | California | 255 | [93-131] [185-315] | [185-311] | 176 -> 130 | 224 -> 248 | yes -> yes |
| scripps-pier | California | 285 | [93-143] [189-203] [237-319] | [237-319] | 158 -> 86 | 208 -> 276 | yes -> yes |
| seaside-reef | California | 250 | [93-151] [189-311] | [189-311] | 188 -> 126 | 212 -> 248 | yes -> yes |
| pipes | California | 250 | [93-151] [185-311] | [185-311] | 192 -> 130 | 212 -> 248 | yes -> yes |
| trails | California | 220 | [93-123] [165-291] [321-339] | [165-291] | 186 -> 130 | 224 -> 228 | yes -> yes |
| san-clemente-beach | California | 231 | [93-123] [165-291] [325-343] | [165-291] | 186 -> 130 | 224 -> 228 | yes -> yes |
| middles | California | 207 | [93-115] [165-291] [325-343] | [165-291] | 178 -> 130 | 228 -> 228 | yes -> yes |
| rockpile-laguna | California | 220 | [93-123] [157-283] [317-343] | [157-283] | 194 -> 130 | 216 -> 220 | yes -> yes |
| crystal-cove | California | 225 | [93-115] [157-283] [317-343] | [157-283] | 186 -> 130 | 224 -> 220 | yes -> yes |
| corona-del-mar | California | 175 | [93-115] [153-279] [317-343] | [153-279] | 186 -> 130 | 220 -> 216 | yes -> yes |
| newport-jetties | California | 228 | [93-107] [149-279] [321-343] | [149-279] | 178 -> 134 | 220 -> 212 | yes -> yes |
| seal-beach-jetty | California | 217 | [141-207] [225-251] [317-343] | [141-207] [225-251] | 130 -> 100 | 212 -> 196 | no -> no |
| cabrillo-beach | California | 164 | [129-191] [217-283] [341-343] | [129-191] [217-283] | 142 -> 136 | 212 -> 212 | yes -> yes |
| white-point | California | 217 | [129-191] [217-283] [341-343] | [129-191] [217-283] | 142 -> 136 | 212 -> 212 | yes -> yes |
| haggarty-s | California | 322 | [141-215] [233-291] | [233-291] | 140 -> 62 | 208 -> 260 | no -> no |
| rat-beach | California | 296 | [133-219] [237-287] | [237-287] | 144 -> 54 | 204 -> 260 | no -> no |
| bruce-s-beach | California | 260 | [97-111] [193-283] | [193-283] | 112 -> 94 | 220 -> 236 | yes -> yes |
| shitpipe | California | 245 | [97-115] [185-279] | [185-279] | 120 -> 98 | 208 -> 232 | yes -> yes |
| dockweiler-beach | California | 251 | [97-119] [181-275] | [181-275] | 124 -> 98 | 204 -> 228 | yes -> yes |
| venice-beach-pier | California | 236 | [177-267] [337-343] | [177-267] | 104 -> 94 | 236 -> 220 | yes -> yes |
| santa-monica-pier | California | 225 | [97-111] [173-263] [325-343] | [173-263] | 134 -> 94 | 220 -> 216 | yes -> yes |
| will-rogers | California | 205 | [97-115] [169-255] [301-343] | [169-255] | 158 -> 90 | 236 -> 212 | yes -> yes |
| sunset-point | California | 200 | [97-99] [165-255] [281-343] | [165-255] | 166 -> 94 | 248 -> 208 | yes -> yes |
| chart-house | California | 174 | [161-251] [277-343] | [161-251] | 164 -> 94 | 248 -> 204 | yes -> yes |
| malibu-point | California | 170 | [149-247] [305-343] | [149-247] | 144 -> 102 | 240 -> 196 | yes -> yes |
| point-dume | California | 176 | [137-283] [321-343] | [137-283] | 176 -> 150 | 228 -> 208 | yes -> yes |
| port-hueneme | California | 220 | [129-223] [245-283] [341-343] | [133-223] [245-283] | 146 -> 136 | 204 -> 208 | yes -> yes |
| silver-strand | California | 230 | [133-215] [241-283] | [133-215] [241-283] | 132 -> 132 | 200 -> 204 | no -> no |
| oxnard-beach | California | 240 | [141-143] [161-203] [233-283] | [165-203] [233-283] | 106 -> 96 | 204 -> 228 | yes -> yes |
| ventura-harbor | California | 252 | [97-103] [137-151] [177-191] [221-279] | [177-191] [221-279] | 108 -> 80 | 196 -> 232 | yes -> yes |
| pitas-point | California | 215 | [149-215] [233-271] [325-347] | [149-215] [233-271] | 138 -> 112 | 228 -> 208 | yes -> yes |
| faria-beach | California | 215 | [97-107] [149-211] [241-271] [321-347] | [153-211] [241-271] | 144 -> 96 | 216 -> 208 | no -> no |
| mussel-shoals | California | 211 | [97-111] [145-203] [229-271] [329-347] | [145-203] [229-271] | 148 -> 108 | 204 -> 208 | no -> no |
| tarpits | California | 205 | [97-99] [141-191] [225-267] [309-347] | [141-191] [225-267] | 148 -> 100 | 228 -> 196 | no -> no |
| miramar | California | 180 | [137-179] [217-243] [289-347] | [137-179] [217-243] | 138 -> 76 | 248 -> 184 | yes -> yes |
| mesa-lane | California | 175 | [129-167] [225-267] [301-347] | [129-167] [225-267] | 138 -> 88 | 252 -> 220 | no -> no |
| avila-beach | California | 207 | [181-219] [289-291] [309-311] [341-347] | [181-219] | 64 -> 42 | 268 -> 200 | yes -> yes |
| grover-beach | California | 263 | [97-119] [137-159] [193-283] | [193-283] | 146 -> 94 | 200 -> 236 | yes -> yes |
| shell-beach-north | California | 222 | [97-107] [185-259] [337-347] | [185-259] | 106 -> 78 | 232 -> 220 | yes -> yes |
| cable-landing | California | 287 | [97-123] [173-191] [213-311] | [213-311] | 154 -> 102 | 224 -> 260 | yes -> yes |
| piedras-blancas | California | 246 | [153-315] | [153-315] | 166 -> 166 | 232 -> 232 | yes -> yes |
| mole-point | California | 322 | [105-111] [129-143] [185-187] [217-323] | [217-323] | 144 -> 110 | 232 -> 268 | yes -> yes |
| boneyard | California | 3 | [161-167] [197-199] [225-319] | [225-319] | 114 -> 98 | 244 -> 272 | no -> no |
| monterey-beach | California | 350 | [105-111] [129-195] [225-247] [265-303] | - | 148 -> 0 | 200 -> 320.0 | no -> no |
| mitchell-s-cove | California | 180 | [181-251] [281-287] [317-319] | [181-251] | 90 -> 74 | 252 -> 216 | yes -> yes |
| 4-mile | California | 205 | [173-287] | [173-287] | 118 -> 118 | 244 -> 228 | yes -> yes |
| pigeon-point | California | 203 | [161-331] | [161-319] | 174 -> 162 | 240 -> 240 | yes -> yes |
| pescadero | California | 270 | [105-111] [185-319] | [209-319] | 148 -> 114 | 240 -> 264 | yes -> yes |
| half-moon-bay | California | 251 | [105-111] [181-287] | [181-287] | 120 -> 110 | 208 -> 232 | yes -> yes |
| pedro-point | California | 298 | [133-151] [197-307] | [197-307] | 136 -> 114 | 224 -> 252 | yes -> yes |
| south-ocean-beach | California | 260 | [149-159] [189-295] | [189-295] | 124 -> 110 | 228 -> 240 | yes -> yes |
| north-ocean-beach | California | 268 | [153-155] [189-291] | [189-291] | 112 -> 106 | 232 -> 240 | yes -> yes |
| cronkhite | California | 231 | [185-287] [329-355] | [185-287] | 136 -> 106 | 260 -> 236 | yes -> yes |
| point-reyes-north | California | 285 | [157-167] [213-319] | [213-319] | 124 -> 110 | 248 -> 264 | yes -> yes |
| mendocino | California | 227 | [109-111] [137-155] [181-323] | [201-323] | 174 -> 126 | 228 -> 260 | yes -> yes |
| caspar | California | 298 | [109-111] [137-147] [193-331] | [209-331] | 162 -> 126 | 236 -> 268 | yes -> yes |
| jug-handle | California | 284 | [109-111] [137-147] [205-331] | [205-331] | 150 -> 130 | 244 -> 268 | yes -> yes |
| mackerricher | California | 331 | [109-111] [137-151] [205-323] | [205-323] | 146 -> 122 | 236 -> 264 | no -> no |
| ten-mile-beach | California | 280 | [109-111] [137-167] [217-319] | [217-319] | 146 -> 106 | 228 -> 268 | yes -> yes |
| westport | California | 277 | [109-111] [137-163] [193-319] | [193-319] | 166 -> 130 | 232 -> 256 | yes -> yes |
| agate-beach | California | 302 | [141-163] [209-347] | [241-347] | 168 -> 110 | 260 -> 292 | yes -> yes |
| st-andrews-park | Florida | 230 | [153-283] | [153-283] | 134 -> 134 | 216 -> 216 | yes -> yes |
| cape-san-blas | Florida | 220 | [113-251] [273-307] | [113-251] [273-307] | 180 -> 180 | 208 -> 208 | yes -> yes |
| redington-pier | Florida | 235 | [177-319] | [177-311] | 146 -> 138 | 244 -> 240 | yes -> yes |
| madeira-beach | Florida | 225 | [177-299] | [177-299] | 126 -> 126 | 244 -> 236 | yes -> yes |
| spring-ave | Florida | 239 | [161-327] | [161-327] | 170 -> 170 | 244 -> 244 | yes -> yes |
| twin-piers | Florida | 254 | [173-323] | [173-323] | 154 -> 154 | 248 -> 248 | yes -> yes |
| whitney-beach | Florida | 240 | [157-327] | [157-327] | 174 -> 174 | 240 -> 240 | yes -> yes |
| coolidge | Florida | 240 | [181-315] | [181-311] | 138 -> 134 | 248 -> 244 | yes -> yes |
| siesta-key | Florida | 222 | [45-47] [77-95] [161-319] | [161-299] | 190 -> 142 | 232 -> 228 | yes -> yes |
| englewood | Florida | 246 | [166-326] | [166-326] | 160 -> 160 | 246 -> 246 | yes -> yes |
| gasparilla | Florida | 231 | [181-323] | [181-323] | 146 -> 146 | 252 -> 252 | yes -> yes |
| captiva | Florida | 258 | [177-331] | [177-331] | 158 -> 158 | 252 -> 252 | yes -> yes |
| key-west | Florida | 152 | [93-99] [117-135] [209-227] [245-255] | [93-99] [117-135] [209-227] [245-251] | 68 -> 64 | 208 -> 204 | no -> no |
| sunny-isles | Florida | 95 | [13-171] [233-247] [301-319] | [13-171] | 202 -> 162 | 72 -> 92 | yes -> yes |
| spoils | Florida | 95 | [13-83] [145-175] [209-243] [261-279] [297-315] | [13-83] | 190 -> 74 | 292 -> 40.0 | no -> no |
| commercial-pier | Florida | 95 | [17-87] [145-175] [205-219] [297-315] [349-351] | [17-87] [145-175] | 154 -> 108 | 48 -> 84 | no -> no |
| hillsboro-inlet | Florida | 134 | [21-119] [141-179] [205-223] [293-315] | [21-119] [141-179] | 192 -> 144 | 96 -> 96 | no -> no |
| hillsboro-beach | Florida | 95 | [17-127] [145-179] [205-223] [293-315] | [17-127] [145-175] | 200 -> 148 | 104 -> 96 | yes -> yes |
| palmetto-pavilion | Florida | 95 | [13-91] [149-179] [205-219] [289-311] | [13-91] [149-179] | 160 -> 116 | 60 -> 84 | no -> no |
| ocean-reef-park | Florida | 85 | [157-171] [261-275] [357-111] | [5-111] [157-171] | 154 -> 128 | 72 -> 80 | yes -> yes |
| hobe-beach | Florida | 70 | [121-155] [221-239] [285-307] [349-103] | [121-155] [349-103] | 204 -> 156 | 56 -> 72 | yes -> yes |
| jensen-beach | Florida | 70 | [345-147] | [345-147] | 166 -> 166 | 64 -> 64 | yes -> yes |
| vero-pier | Florida | 75 | [357-151] | [357-151] | 158 -> 158 | 72 -> 72 | yes -> yes |
| spessard | Florida | 65 | [9-147] | [9-147] | 142 -> 142 | 76 -> 76 | yes -> yes |
| ocean-ave | Florida | 70 | [13-147] | [13-147] | 138 -> 138 | 80 -> 80 | yes -> yes |
| indiatlantic | Florida | 70 | [13-147] | [13-147] | 138 -> 138 | 80 -> 80 | yes -> yes |
| canova-beach-park | Florida | 80 | [17-151] | [17-151] | 138 -> 138 | 84 -> 84 | yes -> yes |
| pelican-beach | Florida | 80 | [17-151] | [17-151] | 138 -> 138 | 84 -> 84 | yes -> yes |
| 16th-street-south | Florida | 90 | [33-155] | [33-155] | 126 -> 126 | 92 -> 92 | yes -> yes |
| flagler-ave | Florida | 65 | [177-203] [269-287] [345-139] | [345-139] | 210 -> 158 | 72 -> 60 | yes -> yes |
| wilbur-by-the-sea | Florida | 70 | [173-199] [265-283] [353-139] | [353-139] | 202 -> 150 | 84 -> 64 | yes -> yes |
| a-street | Florida | 89 | [357-151] | [1-151] | 158 -> 154 | 72 -> 76 | yes -> yes |
| atlantic-blvd | Florida | 85 | [5-155] [181-203] [241-271] | [5-155] | 214 -> 154 | 120 -> 80 | yes -> yes |
| fort-clinch | Florida | 85 | [5-165] | [5-165] | 160 -> 160 | 85 -> 85 | yes -> yes |
| jekyll-island | Georgia | 110 | [37-171] | [37-171] | 138 -> 138 | 104 -> 104 | yes -> yes |
| st-simons-island | Georgia | 141 | [49-171] | [49-171] | 126 -> 126 | 108 -> 108 | yes -> yes |
| sea-island | Georgia | 135 | [65-175] | [65-175] | 114 -> 114 | 120 -> 120 | yes -> yes |
| blackbeard-island | Georgia | 110 | [41-187] | [41-187] | 150 -> 150 | 112 -> 112 | yes -> yes |
| st-catherines-island | Georgia | 119 | [41-191] | [41-191] | 154 -> 154 | 116 -> 116 | yes -> yes |
| tybee-island | Georgia | 93 | [57-183] | [57-183] | 130 -> 130 | 124 -> 120 | yes -> yes |
| fripp-island | South Carolina | 156 | [81-215] | [81-215] | 138 -> 138 | 140 -> 148 | yes -> yes |
| berts-bar | South Carolina | 151 | [69-207] [349-359] | [69-207] | 156 -> 142 | 124 -> 140 | yes -> yes |
| windjammer | South Carolina | 145 | [73-215] | [73-215] | 146 -> 146 | 144 -> 144 | yes -> yes |
| wild-dunes | South Carolina | 157 | [65-219] | [69-219] | 158 -> 154 | 140 -> 144 | yes -> yes |
| dewees-island | South Carolina | 135 | [65-195] | [65-195] | 134 -> 134 | 132 -> 128 | yes -> yes |
| south-island | South Carolina | 140 | [60-220] | [60-220] | 160 -> 160 | 140 -> 140 | yes -> yes |
| oak-island-pier | North Carolina | 200 | [133-239] [349-355] | [133-239] | 120 -> 110 | 188 -> 184 | yes -> yes |
| fort-fisher | North Carolina | 105 | [49-187] | [49-187] | 142 -> 142 | 116 -> 116 | yes -> yes |
| carolina-beach-pier | North Carolina | 110 | [53-191] | [53-191] | 142 -> 142 | 120 -> 120 | yes -> yes |
| wrightsville-beach-south | North Carolina | 115 | [61-195] | [61-195] | 138 -> 138 | 128 -> 128 | yes -> yes |
| jolly-roger-pier | North Carolina | 133 | [73-207] | [73-207] | 138 -> 138 | 140 -> 140 | yes -> yes |
| seaview-pier | North Carolina | 150 | [81-215] [345-351] | [81-215] | 148 -> 138 | 144 -> 148 | yes -> yes |
| hatteras-ferry-docks | North Carolina | 153 | [29-31] [89-227] | [89-227] | 148 -> 142 | 148 -> 160 | yes -> yes |
| north-buxton | North Carolina | 98 | [9-231] | [17-183] | 226 -> 170 | 120 -> 100 | yes -> yes |
| s-turns | North Carolina | 78 | [209-219] [1-163] | [209-219] [1-163] | 180 -> 180 | 104 -> 96 | yes -> yes |
| north-pea-island | North Carolina | 64 | [193-223] [349-159] | [209-223] [349-143] | 208 -> 176 | 96 -> 76 | yes -> yes |
| outer-banks-fishing-pier | North Carolina | 66 | [349-147] | [349-147] | 162 -> 162 | 72 -> 68 | yes -> yes |
| abalone-street | North Carolina | 62 | [342-359] [0-142] | [342-359] [0-142] | 161 -> 161 | 62 -> 62 | yes -> yes |
| bath-house | North Carolina | 60 | [349-147] | [349-147] | 162 -> 162 | 68 -> 68 | yes -> yes |
| black-pelican | North Carolina | 64 | [349-139] | [349-139] | 154 -> 154 | 72 -> 64 | yes -> yes |
| north-end-virginia-beach | Virginia | 74 | [29-155] [217-239] | [29-155] | 156 -> 130 | 100 -> 84 | yes -> yes |
| fenwick-island | Delaware | 94 | [29-175] | [29-175] | 150 -> 150 | 100 -> 100 | yes -> yes |
| gordon-pond | Delaware | 88 | [49-163] [249-251] | [49-163] | 124 -> 118 | 116 -> 104 | yes -> yes |
| the-cove | New Jersey | 200 | [17-19] [85-183] | [97-183] | 108 -> 90 | 124 -> 140 | no -> no |
| broadway-beach | New Jersey | 180 | [17-19] [89-183] | [89-183] | 104 -> 98 | 124 -> 136 | yes -> yes |
| grant-street | New Jersey | 175 | [13-19] [81-183] | [81-183] | 116 -> 106 | 120 -> 132 | yes -> yes |
| queen-street | New Jersey | 170 | [81-183] | [81-183] | 106 -> 106 | 124 -> 132 | yes -> yes |
| poverty-beach | New Jersey | 155 | [73-187] | [73-187] | 118 -> 118 | 124 -> 128 | yes -> yes |
| the-rocks | New Jersey | 150 | [53-187] | [53-187] | 138 -> 138 | 120 -> 120 | yes -> yes |
| diamond-beach | New Jersey | 140 | [53-191] | [53-191] | 142 -> 142 | 120 -> 120 | yes -> yes |
| north-wildwood | New Jersey | 130 | [45-191] | [45-191] | 150 -> 150 | 116 -> 116 | yes -> yes |
| 30th-street-pier | New Jersey | 120 | [49-195] | [49-195] | 150 -> 150 | 120 -> 120 | yes -> yes |
| avalon-jetty | New Jersey | 120 | [49-195] | [49-195] | 150 -> 150 | 120 -> 120 | yes -> yes |
| south-end | New Jersey | 125 | [57-195] | [57-195] | 142 -> 142 | 124 -> 124 | yes -> yes |
| 32nd-street | New Jersey | 135 | [69-199] | [69-199] | 134 -> 134 | 132 -> 132 | yes -> yes |
| quincy-ave | New Jersey | 150 | [69-199] | [69-199] | 134 -> 134 | 132 -> 132 | yes -> yes |
| margate-pier | New Jersey | 150 | [69-203] | [69-203] | 138 -> 138 | 136 -> 136 | yes -> yes |
| ventor-pier | New Jersey | 145 | [69-203] | [69-203] | 138 -> 138 | 132 -> 136 | yes -> yes |
| caesars-pier | New Jersey | 155 | [53-207] | [69-207] | 158 -> 142 | 128 -> 140 | yes -> yes |
| brigantine-jetty | New Jersey | 142 | [53-207] | [53-207] | 158 -> 158 | 128 -> 128 | yes -> yes |
| seaside-road | New Jersey | 131 | [49-207] | [49-207] | 162 -> 162 | 128 -> 128 | yes -> yes |
| wooden-jetty | New Jersey | 120 | [41-203] | [41-203] | 166 -> 166 | 120 -> 120 | yes -> yes |
| holyoke | New Jersey | 137 | [41-203] | [41-203] | 166 -> 166 | 120 -> 120 | yes -> yes |
| beach-haven | New Jersey | 122 | [37-203] | [37-203] | 170 -> 170 | 120 -> 120 | yes -> yes |
| long-beach-township | New Jersey | 130 | [37-203] | [37-203] | 170 -> 170 | 120 -> 120 | yes -> yes |
| mantoloking | New Jersey | 105 | [49-179] | [49-179] | 134 -> 134 | 112 -> 112 | yes -> yes |
| sea-girt | New Jersey | 100 | [57-183] [233-247] | [57-183] | 148 -> 130 | 136 -> 120 | yes -> yes |
| ocean-grove | New Jersey | 105 | [25-185] | [25-185] | 160 -> 160 | 105 -> 105 | yes -> yes |
| monmouth | New Jersey | 95 | [69-179] | [69-179] | 114 -> 114 | 132 -> 124 | yes -> yes |
| breezy-point | New York | 178 | [89-179] | [89-179] | 94 -> 94 | 132 -> 132 | yes -> yes |
| west-end-long-beach | New York | 175 | [101-191] | [101-191] | 94 -> 94 | 144 -> 144 | yes -> yes |
| saltaire | New York | 160 | [81-219] | [81-219] | 142 -> 142 | 148 -> 148 | yes -> yes |
| ocean-bay-park | New York | 165 | [81-223] | [81-223] | 146 -> 146 | 152 -> 152 | yes -> yes |
| davis-park | New York | 161 | [81-241] | [81-241] | 160 -> 160 | 161 -> 161 | yes -> yes |
| hither-hills | New York | 160 | [77-239] | [77-235] | 166 -> 162 | 156 -> 156 | yes -> yes |
| terrace | New York | 162 | [89-235] | [89-235] | 150 -> 150 | 156 -> 160 | yes -> yes |
| poles | New York | 162 | [73-235] | [85-235] | 166 -> 154 | 152 -> 160 | yes -> yes |
| radars | New York | 115 | [77-219] | [77-219] | 146 -> 146 | 152 -> 148 | yes -> yes |
| black-rock-point | Rhode Island | 179 | [69-239] | [93-239] | 174 -> 150 | 156 -> 168 | yes -> yes |
| dunes-park | Rhode Island | 174 | [97-115] [153-187] [213-223] [333-23] | [101-115] [153-187] [213-223] | 128 -> 70 | 120 -> 164 | yes -> yes |
| weekapaug | Rhode Island | 188 | [97-115] [153-187] [213-223] [333-23] | [97-115] [153-187] [213-223] | 128 -> 74 | 120 -> 164 | yes -> yes |
| east-beach | Rhode Island | 166 | [93-143] [177-223] [333-15] | [93-143] [177-223] | 150 -> 104 | 136 -> 148 | no -> no |
| breachway | Rhode Island | 170 | [93-147] [181-203] [221-227] [333-15] | [93-147] [181-203] [221-227] | 140 -> 94 | 132 -> 152 | no -> no |
| deep-hole | Rhode Island | 162 | [82-242] | [82-242] | 160 -> 160 | 162 -> 162 | yes -> yes |
| salty-brine | Rhode Island | 146 | [66-226] | [66-226] | 160 -> 160 | 146 -> 146 | yes -> yes |
| the-k-s | Rhode Island | 179 | [85-183] [213-235] | [85-183] [213-235] | 128 -> 128 | 160 -> 160 | yes -> yes |
| point-judith-south | Rhode Island | 152 | [81-187] [217-235] | [81-187] [217-235] | 132 -> 132 | 152 -> 156 | yes -> yes |
| scarborough | Rhode Island | 127 | [85-183] [213-235] [337-355] | [85-183] | 150 -> 102 | 148 -> 132 | yes -> yes |
| narrow-river | Rhode Island | 160 | [93-187] [333-351] | [93-187] | 120 -> 98 | 140 -> 140 | yes -> yes |
| baileys-beach | Rhode Island | 208 | [97-203] [221-227] | [97-203] [221-227] | 120 -> 120 | 160 -> 160 | no -> no |
| first-beach | Rhode Island | 165 | [125-191] [209-211] | [125-187] | 76 -> 66 | 172 -> 156 | yes -> yes |
| squibnocket | Massachusetts | 127 | [47-207] | [47-207] | 160 -> 160 | 127 -> 127 | yes -> yes |
| lucy-vincent | Massachusetts | 155 | [75-235] | [75-235] | 160 -> 160 | 155 -> 155 | yes -> yes |
| long-point | Massachusetts | 184 | [101-227] | [101-227] | 130 -> 130 | 172 -> 168 | yes -> yes |
| south-beach-state-park | Massachusetts | 178 | [117-239] | [117-239] | 126 -> 126 | 180 -> 180 | yes -> yes |
| madaket | Massachusetts | 235 | [155-315] | [155-315] | 160 -> 160 | 235 -> 235 | yes -> yes |
| miacomet | Massachusetts | 195 | [53-79] [101-275] | [101-275] | 208 -> 178 | 168 -> 188 | yes -> yes |
| nobadeer | Massachusetts | 185 | [37-255] [273-279] | [105-255] | 232 -> 154 | 156 -> 180 | yes -> yes |
| keyes-beach | Massachusetts | 180 | [129-135] [157-163] [193-199] | [157-163] [193-199] | 30 -> 20 | 188 -> 196 | no -> no |
| green-harbor | Massachusetts | 153 | [108-198] | [108-198] | 90 -> 90 | 153 -> 153 | yes -> yes |
| scituate | Massachusetts | 96 | [16-176] | [16-176] | 160 -> 160 | 96 -> 96 | yes -> yes |
| winthrop-jetty | Massachusetts | 96 | [16-176] | [16-176] | 160 -> 160 | 96 -> 96 | yes -> yes |
| revere-beach | Massachusetts | 111 | [31-191] | [31-191] | 160 -> 160 | 111 -> 111 | yes -> yes |
| lynn-beach | Massachusetts | 148 | [68-228] | [68-228] | 160 -> 160 | 148 -> 148 | yes -> yes |
| devereaux | Massachusetts | 166 | [86-246] | [86-246] | 160 -> 160 | 166 -> 166 | yes -> yes |
| salisbury | Massachusetts | 90 | [10-170] | [10-170] | 160 -> 160 | 90 -> 90 | yes -> yes |
| costellos | New Hampshire | 115 | [35-195] | [35-195] | 160 -> 160 | 115 -> 115 | yes -> yes |
| fox-hill | New Hampshire | 115 | [35-195] | [35-195] | 160 -> 160 | 115 -> 115 | yes -> yes |
| straws-point | New Hampshire | 120 | [40-200] | [40-200] | 160 -> 160 | 120 -> 120 | yes -> yes |
| pirates-cove | New Hampshire | 120 | [75-165] | [75-165] | 90 -> 90 | 120 -> 120 | yes -> yes |
| harbor-beach | Maine | 115 | [70-160] | [70-160] | 90 -> 90 | 115 -> 115 | yes -> yes |
| ogunquit | Maine | 100 | [20-180] | [20-180] | 160 -> 160 | 100 -> 100 | yes -> yes |
| moody-point | Maine | 120 | [40-200] | [40-200] | 160 -> 160 | 120 -> 120 | yes -> yes |
| crescent-beach | Maine | 120 | [40-200] | [40-200] | 160 -> 160 | 120 -> 120 | yes -> yes |
| wells-beach | Maine | 130 | [50-210] | [50-210] | 160 -> 160 | 130 -> 130 | yes -> yes |
| drakes-island | Maine | 130 | [50-210] | [50-210] | 160 -> 160 | 130 -> 130 | yes -> yes |
| pine-point-beach | Maine | 175 | [95-255] | [95-255] | 160 -> 160 | 175 -> 175 | yes -> yes |
| reid-state-park | Maine | 140 | [60-220] | [60-220] | 160 -> 160 | 140 -> 140 | yes -> yes |
