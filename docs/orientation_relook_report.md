# Orientation relook list + reloaded tool

Two deliverables: (1) a ranked **relook list** of only the spots whose hand
`orientation_deg` most disagrees with physics, and (2) the existing orientation
tool **reloaded** with just that list for fast re-checking. **Read-only analysis +
tool data files; nothing touches prod orientations** (`spots_enriched.json`,
`spot_orientations.json`) until the user exports and we apply via the reviewed
`apply_orientation_fixes` path.

## Files
- `scripts/orientation_relook.py` — builds the ranked list → `orientation_relook.json`.
- `scripts/orientation_relook.json` — the list (per spot: slug, name, lat, lon,
  orientation_deg, seed_normal, seed_delta, mop_shore_normal, mop_delta, zone, reason),
  worst-first, with `_meta` counts at Δ 30/40/50.
- `scripts/build_orient_relook.py` → `scripts/orient_relook.html` — the reloaded tool.

## Part 1 — the disagreement score (corrected)
- **a. seed Δ**, **folded** = `min(Δ, 180−Δ)` of `|orientation_deg − geometric
  seaward-normal seed|`. The fold is the key correction: a seed Δ near 180° is the
  seaward-normal computation resolving **backwards (a sign flip)**, not a real
  disagreement, so 177→3, 92→88, 135→45 — genuine 30–90° disagreements survive,
  flips collapse. Seed = `compute_orientation()` (GSHHG) when available, else the
  stored geometric windows.
- **b. MOP Δ** (CA only) = `|orientation_deg − matched MOP metaShoreNormal|`
  (`scripts/mop_points.json`, ≤ 1500 m). Real CDIP physics.
- **score = MOP Δ if present, else folded seed Δ** — MOP is preferred over the
  seed wherever it exists. Any spot whose folded-seed and MOP disagree by > 30° is
  **flagged "seed unreliable here — trust MOP"** (it doesn't rank up on the bad seed).
- **c.** soft "point-optimal?" glance flag (CA point breaks, shore-normal agrees).

Worst-first; keep score ≥ threshold (default 30°). `--selftest` (16/16) proves the
fold and the Chart-House case (seed Δ177 + MOP Δ4 → folded 3 ≈ MOP → score 4, drops).
**Fold confirmation:** the script reports, across spots that have *both* a
folded-seed and a MOP Δ, `mean|folded−MOP|` and how many agree ≤ 15° — if they
agree, the fold is right (the seed and the real CDIP physics now line up).

`rerank` recomputes the ranking from the seeds **already in
`orientation_relook.json`** — no GSHHG re-run:
```
python3 scripts/orientation_relook.py rerank      # cheap; reuse existing seeds
```

## ⚠️ Run-location reality (drives the headline result)
- **MOP Δ needs `mop_points.json`** → the Mac (the cache lives there).
- **The seed needs a *fresh* GSHHG recompute.** It is stored for only **53/664**
  spots, and — verified this run — those stored windows are **stale**: they
  predate the orientation-apply manifest and are frequently ~180° off the current
  hand value (e.g. Leadbetter Beach: hand 144°, stored seed 315°, Δ171°). So a
  stored-window seed is **not trustworthy** — it surfaces staleness, not genuine
  hand-vs-physics disagreement. **The real list requires running on the Mac/CI
  where `compute_orientation` recomputes the seed against GSHHG.**

So this is the same egress discipline as before: I built and proved the pipeline,
but the **trustworthy ranked list is a Mac run**, not a sandbox fabrication.

### The fold fixes the artifact (demonstrated on the partial list)
`rerank` on the 14 sandbox candidates (all ~157–171° = sign flips, no MOP) — the
fold collapses the artifacts and only **genuine** disagreements survive:
```
reranking 14 existing candidates (reusing seeds; no GSHHG re-run)
on the list (≥30°): 3   |   count at Δ 30/40/50: 3 / 2 / 0      (was 14 / 13 / 12)
worst 3:
  Tres Palmas   orient 275  foldedΔ 47   (genuine; PR point — likely wrap-optimal)
  Fiji          orient   0  foldedΔ 40
  Kiahuna Beach orient 180  foldedΔ 37
```
14 → 3, and the survivors are real 37–47° disagreements (point-style spots aimed
off the normal), exactly as expected — the ~180° flips are gone. **On the Mac**
(full GSHHG seed + MOP) the same correction applies to the full list: run
`orientation_relook.py rerank` against your Mac `orientation_relook.json` to get
the real corrected 30/40/50 counts and worst-15, with MOP preferred and the
Chart-House-style flips (seed Δ177 / MOP Δ4) collapsing to their true ~4°.

### To get the real list (Mac, with GSHHG + mop_points.json present)
```
python3 scripts/orientation_relook.py                 # full seed + MOP Δ, all spots
python3 scripts/orientation_relook.py --threshold 40  # if 30° is too many
python3 scripts/build_orient_relook.py                # regenerate the tool from the real list
```
`orientation_relook.json._meta` reports `count_at_30/40/50` and whether the run
was `partial`. Start at 30°; if the count is huge, the report prints 30/40/50 so
you can raise it.

## Part 2 — the reloaded tool (`orient_relook.html`)
Self-contained (open in a browser). Seeded **only** from `orientation_relook.json`,
worst-first. Same UX as the original — Leaflet **satellite + labels**, **drag to
reorient**, **Enter = confirm + auto-advance** to the next unset, **hover = name** —
plus the requested per-spot context:
- the **reason** it's flagged;
- **current vs geometry-seed vs MOP shore-normal** as numbers *and* as arrows on a
  compass rose: blue = your current (draggable), faint green dashed = geometry
  seed, faint magenta dashed = MOP — so you see "geometry says this way, MOP says
  that way, you say this" and decide;
- for CA spots, **MOP match distance + skill zone** (with a "low-skill: trust MOP
  arrow less" note in the SB Channel), so the MOP arrow is weighted by zone.
- a live sight-line on the satellite in your current orientation; `r` resets to seed.

**Export** matches the original tool's shape so it flows through the unchanged
apply path — `{"orientations": {slug: {orientation_deg, cardinal, name,
source:"manual_relook"}}}`, only the confirmed spots. **Not auto-applied:** the
user exports `orientation_relook_export.json` and we apply it via
`python -m pipeline.apply_orientation_fixes --input … --dry-run` first, then for real.

## Honest limits
- The **real ranked list is pending the Mac run** — the sandbox has neither GSHHG
  (fresh seed) nor `mop_points.json` (MOP Δ). The partial list here is
  stale-seed-artifact-dominated and labeled as such; not fabricated, but not the
  answer.
- The soft "point-optimal?" flag (c) is a light heuristic; the true
  refraction-offset flag (Malibu-style) needs the handful slice's per-spot offset
  (a future join), noted not faked.
- Nothing was applied; `spots_enriched.json` and `spot_orientations.json` are
  untouched.
