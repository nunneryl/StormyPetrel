# CDIP MOP integration — how a spot gets `swell_window_source = "cdip_mop"`

This documents the MOP (CDIP *Monitoring and Prediction*) nearshore source so it's
reproducible and not stranded in git history. Nothing here changes the adoption
logic — it points at the code that owns it.

## Files

Tooling (restored to `scripts/`; these are the batch/analysis stage — run on a box
with open CDIP THREDDS egress, e.g. the Mac):

| file | role |
|---|---|
| `scripts/mop_blacks_slice.py` | The proven single-spot nearshore chain + the MOP-point cache builder. Provides `rate_nearshore`, `split_swell_hs`, `load_cache`/`build_cache`, `haversine_m`, `circ_offset`. Imported by the other two. |
| `scripts/mop_handful_slice.py` | The **adoption rule** and its thresholds (`verdict()`), validated on ~5 spots across MOP's skill gradient. Imports `mop_blacks_slice`. |
| `scripts/mop_ca_rollout.py` | Stage 1 — runs `verdict()` over **every** CA spot and writes the verdict table. Imports both of the above + `pipeline.forecast.buoys`, `pipeline.config`, `pipeline.http`, `pipeline.enrichment.geodata`. |

Runtime (already in the tree, not part of this restore):

| file | role |
|---|---|
| `pipeline/apply_mop_assignments.py` | Stage 2 Part A — bakes the `consume=true` rows into `spots_enriched.json`. |
| `pipeline/forecast/mop.py` | Live per-cycle override for `cdip_mop` spots (`apply_mop_overrides`) + Part C batch validation. |

## (a) Two-stage flow

```
mop_ca_rollout.py run()            pipeline.apply_mop_assignments --apply
  every CA spot ─► verdict() ─►  scripts/mop_ca_verdicts.json ─► spots_enriched.json
  (CONSUME / FALL BACK / SKIP)     (consume=true only)            swell_window_source="cdip_mop"
```

1. **Rollout (`mop_ca_rollout.py::run`)** iterates `region_hint == "California"` spots,
   matches each to the nearest MOP alongshore point (`_match`), pulls ~45 d of MOP
   through the nearshore chain, cross-checks against the spot's nearest NDBC buoy, and
   calls `verdict()` → `CONSUME` / `FALL BACK` (or `SKIP` for off-coast / no coverage).
   Writes `scripts/mop_ca_verdicts.json` (`OUT`) and `scripts/mop_ca_buoy_recovery.json`
   (`MAPPING_OUT`).
2. **Apply (`pipeline/apply_mop_assignments.py::build_plan`)** reads the verdicts and
   tags **only** `consume=true` spots (`if not r.get("consume"): continue`), writing
   `swell_window_source="cdip_mop"` + `mop_point_id` / `mop_shore_normal` /
   `mop_match_distance_m` / `mop_nowcast_url` / `mop_buoy_id`. DRY RUN by default;
   `--apply` writes. Every other spot is left exactly as-is.
3. At forecast time, `pipeline/forecast/mop.py::apply_mop_overrides` overrides those
   spots' swell rating from the point's MOP nowcast (`mop_nowcast_url`), additively and
   reversibly (any failure → the spot keeps its orientation/NWPS rating).

## (b) Adoption thresholds — the gate and where it lives

All in **`scripts/mop_handful_slice.py`** (module-level constants, consumed by
`verdict()`):

| constant | value | meaning |
|---|---|---|
| `MATCH_FALLBACK_M` | `1200.0` m | Hard far-outlier veto. MOP points sit on the 10 m contour (0.5–1.5 km offshore is normal); beyond this the match isn't the break's contour → FALL BACK. |
| `SHORE_NORMAL_MAX_DELTA` | `35.0`° | `|orientation_deg − point metaShoreNormal|`; beyond this the matched point faces a different stretch than the break → FALL BACK. |
| `HS_CORR_MIN` | `0.80` | MOP-vs-buoy significant-height Pearson r must clear this (MOP tracks the buoy's swell events). |
| `DIR_STD_MAX` | `25.0`° | MOP-vs-buoy direction-offset `circ_std` must be a stable refraction (below this). |
| `HARD_HS_CORR` | `0.85` | Low-skill zones need **stronger** height agreement to override. |
| `HARD_DIR_STD` | `20.0`° | Low-skill zones need tighter direction agreement to override. |

`verdict(zone, r2_dir, dist_m, hs_corr, dir_std, n_aligned, has_buoy, sn_delta)`
(same file) applies them in order: distance veto → shore-normal → buoy verification
(`has_buoy and n_aligned >= 24`, else FALL BACK unless a clean `HIGH`-skill match →
`CONSUME (unverified)`) → low-skill vs normal agreement test.

**Coverage sanity** lives in **`scripts/mop_ca_rollout.py`**:

| constant | value | meaning |
|---|---|---|
| `MATCH_SANITY_M` | `25_000.0` m | Nearest MOP point beyond this ⇒ `SKIP "no MOP coverage"` (off-coast coord / genuine gap), recorded but not counted as CONSUME/FALL BACK. |

**Skill tiers** are `ca_zone(lat, lng)` in `mop_ca_rollout.py` (latitude bands, mirrors
`orientation_relook.ca_zone`), with nominal direction R² per tier in `ZONE_R2`:

| zone | latitude band | nominal dir R² (`ZONE_R2`) |
|---|---|---|
| `HIGH` | 32.5–33.5 (San Diego / San Clemente Basin) | 0.9 |
| `MEDIUM` | 33.5–34.05 (San Pedro / Santa Monica) | 0.6 |
| `HARD` | 34.05–34.6 (Santa Barbara Channel) | 0.04 |
| `UNKNOWN` | Central / Northern CA | `None` |

(`low_skill = zone == "HARD" or r2_dir < 0.3` selects the stricter `HARD_*` gate.)

## (c) Derived data artifacts — Mac-local, rebuild-on-demand (do NOT commit)

These are large/derived and are **not** in the repo (they were only ever produced
Mac-side). They are currently **not** in `.gitignore` — add them there if you want to
prevent an accidental commit of the ~11.7k-point cache.

| artifact | produced by | consumed by |
|---|---|---|
| `scripts/mop_points.json` | `python3 scripts/mop_blacks_slice.py build-cache` (or `mop_handful_slice.py build-cache`, which delegates to it) — resolves lat/lon/water_depth/shore_normal for all ~11.7k MOP alongshore points. One-time, resumable, needs open THREDDS egress. | `_match` in the rollout; `mop_nowcast_url` at apply time. |
| `scripts/mop_ca_verdicts.json` | `python3 scripts/mop_ca_rollout.py` (`run()`) | `apply_mop_assignments.build_plan` |
| `scripts/mop_ca_buoy_recovery.json` | rollout `--recover` (re-cross-check unverified spots vs nearest ACTIVE buoy) | optional buoy mapping in `build_plan` |

Rebuild sequence (Mac / CI with CDIP THREDDS + NDBC egress):

```bash
python3 scripts/mop_blacks_slice.py build-cache      # -> scripts/mop_points.json (~11.7k points; slow, resumable)
python3 scripts/mop_ca_rollout.py                    # -> scripts/mop_ca_verdicts.json (+ --recover for the mapping)
python -m pipeline.apply_mop_assignments             # dry run diff
python -m pipeline.apply_mop_assignments --apply     # write spots_enriched.json
```

## (d) Why only ~48 CA spots are adopted today

It is a **per-spot quality / buoy-verification gate, not a domain cap.** MOP covers the
entire CA mainland 10 m contour as ~11,700 alongshore points, and the rollout evaluates
**all** ~200 `region_hint=California` spots. A spot is adopted only if `verdict()`
returns CONSUME: a matched point within `MATCH_FALLBACK_M` that faces the break
(`SHORE_NORMAL_MAX_DELTA`) **and** whose MOP nowcast agrees with the nearest NDBC buoy
(`HS_CORR_MIN` / `DIR_STD_MAX`; `HIGH`-skill San Diego may CONSUME unverified when no
buoy is in range). The rest FALL BACK — usually **no buoy in range to verify** or
**MOP-vs-buoy disagreement**, not a lack of MOP coverage. That's why the adopted set
skews to San Diego (`HIGH`-skill, dense buoy array): sgx 30, mtr 11, lox 6, eka 1. To
adopt more, a FALL BACK spot must actually pass the buoy cross-check on a re-run, or the
thresholds above must be recalibrated (a deliberate decision — not a code gap).
