# SW-1 raycast — Phase 1: GSHHG workflow + validate gate

Branch: `claude/practical-dirac-bshfbr`.
Builds the dedicated GitHub Actions job that runs the Phase-0 raycast against
the **real GSHHG** land mask, with a 5-spot validate gate in front of the full
roster run. **No roster arcs committed.**

## Why a dedicated job

The raycast needs GSHHG L1 (~150 MB, network-blocked in the dev sandbox) and
more wall-clock than the normal pipeline allows, and there is no enrich step in
any cron. A GitHub-hosted runner has the open egress to download GSHHG and the
headroom to run longer — so both GSHHG validation and the eventual run live in
`.github/workflows/sw1-raycast.yml`.

## What was added

| File | Purpose |
| --- | --- |
| `.github/workflows/sw1-raycast.yml` | Manual (`workflow_dispatch`) job; `mode` = `validate` (default) or `full`. |
| `pipeline/sw1_raycast.py` | `--mode validate` (5-spot gate, no writes) / `--mode full` (roster, multiprocessing, writes). |
| `pipeline/enrichment/swell_window.py` | Perf path: `compute_swell_window(spot, land=None, ray_step=None)`, prepared-geometry intersects, `local_land_index()` bbox pre-clip, shared `_classify_bearings()`. |

The Phase-0 blocker geometry (area filter, 30 km local-coast guard, distance-
aware island wrap, chain handling) and `swell_window_source="raycast"` are
unchanged — Phase 1 only adds the runner, the perf path, and the workflow.

## How to run the validate gate (do this first)

1. GitHub → **Actions** → **sw1-raycast** → **Run workflow**.
2. Branch: `claude/practical-dirac-bshfbr`. **mode: `validate`** (default). Run.
3. Read the job log. For each of the 5 spots it prints **width**, the
   all-islands-removed **ceiling**, the open **arcs**, and **optimal_swell_dir**:

   ```
   spot                 width  ceil   target  opt  nrm  source    arcs
   Huntington Beach        ..    ..  150-180   ..  220  raycast   [...]
   Malibu Surfrider        ..    ..  100-140   ..  184  raycast   [...]
   Rincon (CA)             ..    ..   80-100   ..  210  raycast   [...]
   Blacks Beach            ..    ..  140-170   ..  263  raycast   [...]
   Second Beach (RI)       ..    ..      80-+  ..  209  raycast   [...]
   ```
   It writes nothing — this is purely the gate.

## Re-tuning against GSHHG (expected)

The thresholds (500 km², the **30 km local-coast guard**, wrap/bridge in
`pipeline/config.py`) were tuned on a Natural Earth proxy. Phase-0 also showed
the step matters: at the production **4° / 90 rays** the proxy numbers shift vs
2° (e.g. Rincon moved). So expect to re-check the gate output against GSHHG and
tune the thresholds if a spot is off — then re-run validate.

**Guardrail:** if a window is too narrow, widen via the wrap/area params — do
**not** loosen the 30 km local-coast guard, which is what keeps a spot on a
small island (Second Beach RI on Aquidneck, 108 km²) from blowing open to 360°.

## Full run (only after the gate looks right)

**mode: `full`**, optional `workers` (default 4). It casts the whole roster with
the perf path — **4° / 90 rays, prepared geometries, per-spot bbox pre-clip,
multiprocessing** — writes `swell_window_arcs` + `optimal_swell_dir` +
`swell_window_source="raycast"` for solved spots, keeps the orientation-derived
fallback for the rest, leaves `orientation_deg` untouched, and **commits the
result on a branch `sw1-raycast/full-<run#>` (never main)** for diff review. The
log prints raycast-vs-fallback counts, CA/RI median widths, and the achieved
per-spot / total time.

Perf budget: ~35 s/spot baseline → 4° (≈1.9×) + prepared geoms + bbox pre-clip
→ ~5–9 s/spot single-core → ÷ workers. Comfortably under the 6 h cap
(`timeout-minutes: 350`).
