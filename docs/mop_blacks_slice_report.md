# MOP vertical slice — Blacks Beach end-to-end

Prove ONE CA spot (Blacks Beach, San Diego) from its CDIP MOP nearshore point
through to a star rating, settle the reference-frame question, and leave a
template. **Not scaled to other spots. Read-only on MOP. No prod touched**
(`spots_enriched.json` and the live pipeline are untouched; the rating logic is
*imported* from `pipeline.interpret`, not modified).

Why Blacks: San Clemente Basin / north San Diego is where MOP skill is highest
(O'Reilly 2016 total mean-direction R² > 0.9) — the opposite of Rincon (R² ~0.04).
If the chain isn't sane here, it won't be anywhere.

> **Egress, same discipline as before.** THREDDS (`thredds.cdip.ucsd.edu`) is
> blocked from the dev sandbox (`403`). I did **not** fabricate live MOP numbers.
> What's proven here is offline: the matching/caching design, the frame math, the
> swell/chop split, and that the **reused rating chain moves the right way**
> (`--selftest`, all pass). The live Blacks tables come from running the script on
> the Mac that already pulls MOP. Deliverables: `scripts/mop_blacks_slice.py` +
> this report.

---

## 0. Run it (on the Mac with THREDDS egress)
```
python scripts/mop_blacks_slice.py build-cache     # one-time: ALL MOP points -> scripts/mop_points.json
python scripts/mop_blacks_slice.py match           # Blacks -> nearest point id + distance (full set)
python scripts/mop_blacks_slice.py slice            # frame offset + per-hour Hs/Tp/dir->stars + sanity days
python scripts/mop_blacks_slice.py --selftest       # offline math/chain proof (works anywhere)
```

## 1. Matching-bug fix: resolve ALL points, cache once
The prototype resolved coords for only **4,000 / 11,677** points (the MOP catalog
carries no per-point lat/lon, so it reads each over OPeNDAP and the prototype
capped at 4,000) — so Blacks matched `D0527 @ 458 m` against a *partial* set,
which may not even be its true nearest.

`build-cache` fixes this: it walks the full catalog, then resolves **every**
point's `metaLatitude/Longitude`, `metaWaterDepth`, `metaShoreNormal` over
OPeNDAP into `scripts/mop_points.json` — **threaded** (`--workers`, default 8) and
**resumable** (re-run continues; writes every 500). `match`/`slice` then run
against the full set. **Confirm on the Mac:** `match` should return Blacks'
nearest `D0527` (or a closer neighbour the partial set missed) at **≤ ~200 m** for
an open-coast point on the 100 m-spaced 10 m contour; if it's still ~458 m, the
stored Blacks coord sits a bit inland and the point is still the right one.
`mop_points.json` is the reusable artifact every later CA spot matches against.

## 2. THE FRAME DECISION — **nearshore path**, and why

MOP's `waveDp`/`waveDm` are **refracted to the 10 m contour**; the existing rating
and the hand `orientation_deg` (Blacks = 263°) are **deep-water-framed**. You
cannot mix them. Snell's-law refraction turns waves *toward* shore-normal as they
shoal, so at 10 m the wave direction is compressed into a narrow band near the
local shore-normal regardless of the deep-water angle — a deep-water WNW 290°
swell arrives at Blacks' 10 m point as roughly 270–278° (a **~15–25° rotation
toward normal**, larger for more oblique deep-water angles). The script measures
this directly (`slice` prints `nearshore is rotated X° from deep-water` against an
offshore buoy, default CDIP 100 / Torrey Pines Outer) — **the measured offset
table is a Mac-run output; I'm not faking it.** The structural point doesn't need
the exact number: **deep-water Dp spans a wide range; nearshore Dp is a tight band
around shore-normal.** Feeding one into a target defined in the other frame is
exactly the error.

Two ways to resolve it:

- **(a) NEARSHORE (chosen).** Rate MOP's refracted `waveDp` against MOP's
  `metaShoreNormal` — a self-consistent nearshore frame — and let the existing
  `directional_gain` run unchanged (empty arcs + `optimal = metaShoreNormal` →
  `cos²((Dp − normal)/2)`). MOP's refracted **Hs** is used directly as the
  at-the-break height. The deep-water "swell window" that the raycast got wrong is
  not needed at all: a swell that can't reach Blacks arrives at the 10 m point with
  near-zero Hs, so MOP's Hs *is* the directional gate.
- **(b) DEEP-WATER.** Un-refract MOP back offshore (ill-posed — refraction isn't
  uniquely invertible and CDIP publishes no inverse transfer), or keep MOP only for
  Hs/Tp and take direction from WW3/NWPS. The latter keeps the rating unchanged but
  **re-introduces the deep-water window/optimal — the exact directional logic the
  raycast failed at.** It throws away MOP's whole reason for existing.

**Recommendation: (a).** It uses MOP's strengths (refracted height *and*
direction at the break), sidesteps the failed deep-water window, and — proven in
§3 — needs **no change** to the break-response math, only consistent nearshore
inputs (`dp = waveDp`, `optimal = metaShoreNormal`).

## 3. Run through the EXISTING rating (reused, not reimplemented)
The slice imports `face_ft`, `directional_gain`, `composite_stars`,
`chop_ratio/chop_multiplier`, `period_quality` from `pipeline.interpret` and feeds
them nearshore-framed:
```
swell_Hs           = 4√∫E(f≤0.125Hz)df          # from MOP spectrum (Tp≥8s band)
dir_gain           = directional_gain(waveDp, [], metaShoreNormal, metaShoreNormal)
effective_face_ft  = face_ft(waveHs, waveTp, "ww3") · dir_gain
stars              = composite_stars(effective_face_ft, wind=1.0, tide=1.0,
                                     chop_multiplier(chop_ratio(waveHs, swell_Hs)),
                                     period_quality(waveTp))
```
Wind/tide are neutral (MOP carries neither; this slice is the swell→stars spine).
`--selftest` exercises the chain on representative inputs and it orders correctly:

| input (nearshore) | stars |
|---|---|
| 2.5 m / 17 s, on shore-normal | **5.0** |
| 1.5 m / 15 s, on-axis | 4.0 |
| 1.5 m / 15 s, 50° off-normal | 3.5 |
| 0.4 m / 7 s, oblique short-period junk | **1.0** |

Bigger + longer-period + on-axis ⇒ higher; small short-period junk ⇒ floor. The
**real Blacks per-hour table** (a few distinct real swells over ~45 d, with
`when / Hs / Tp / Dp / off-normal / swellHs / dirgain / stars`) prints from
`slice` on the Mac — left unfaked here.

## 4. Sanity bar (not a magic number)
`slice` ranks the span, prints the biggest-Hs distinct days plus the two smallest
hours, and the `min/median/max` stars. The bar: **do ratings move the right way,
and are they better-grounded than the orientation fallback?** Two grounding
references the script surfaces: the **frame-offset** vs the offshore buoy (shows
the rating is reading the refracted field, not a guessed normal), and the spot's
current `orientation_derived` fallback (Blacks today: window `183–343`,
optimal 263, source `orientation_derived` — i.e. it never got a real raycast, so
*anything physically-driven beats it*). Eyeball the printed days; if a clean
3 m/16 s WNW day tops the table and a 0.4 m/6 s windchop hour bottoms it, it's
sane. (A known calibration seam: the empty-arc directional response is broad
`cos²(Δ/2)`; refracted directions are already compressed, so a tighter nearshore
response is a tuning lever — see §5.)

## 5. Template + seams for scaling (NOT built here)

**Per-spot MOP-fed record (the data shape):**
```
static  (bake at enrichment, once, like orientation/buoy-assignment):
   mop_point_id, mop_lat, mop_lon, match_distance_m,
   metaShoreNormal, metaWaterDepth, derived_swell_window
dynamic (read live per cycle, like buoy/tide/WW3):
   per hour: waveHs, waveTp, waveDp, waveDm, swell_Hs(from spectrum)
   -> directional_gain(nearshore) -> effective_face_ft -> stars
```

**Enrichment-time vs live — my read (now that I've seen the data): split it.**
The *point assignment + shore-normal + window* are static geometry → **bake once
at enrichment** (a new per-spot block, exactly like the buoy/tide assignment). But
the thing that actually drives stars — `Hs/Tp/Dp` — changes every forecast, so
MOP is fundamentally a **live swell SOURCE read per cycle**, a sibling of the
WW3/NWPS/buoy fetchers, not a one-time bake. (This refines the prototype's
"bake the window" read: the window bakes, but the *rating feed* is live.)

**Refresh cadence.** MOP forecast refreshes **every 6 h** from ECMWF; nowcast/
hindcast hourly. The pipeline already runs on a 6 h cron — so a MOP forecast read
slots in cleanly alongside the existing fetch step, one OPeNDAP read per spot per
cycle (server-side subset → small). No new clock.

**Match-distance seam.** Open-coast spots match the 10 m contour at ≤ ~200 m. Set
a threshold (start **~500 m**, tune from the `mop_points.json` distance
distribution): beyond it the nearest MOP point is across a headland / inside a
harbor or deep bay and isn't representative → **fall back to the existing
orientation path** for that spot. Non-CA spots have no MOP at all → fallback. So
MOP is a CA-open-coast source; `match_distance_m` per spot is the switch.

**Skill seam (carry the O'Reilly caveat forward).** Blacks sits in a high-skill
zone; Rincon (Santa Barbara Channel, R² ~0.04) does not. When scaling, gate MOP
adoption per spot on the published MOP validation skill, not just match distance —
a clean match in a low-skill basin can still be wrong.

## Honest limits
- **Not measured here:** the full-set match distance, the frame-offset numbers,
  and the real per-hour Blacks star table — all need THREDDS (Mac run). The
  chain, the frame math, and the monotonicity **are** proven offline.
- Deep-water reference defaults to CDIP **100** (Torrey Pines Outer); verify it's
  the right offshore buoy for Blacks on the Mac and adjust `--deepwater-station`.
- `face_ft`'s period-factor curve was tuned for NWPS/WW3 inputs; MOP's 10 m Hs is
  already partly shoaled, so absolute star *levels* may need recalibration when
  scaling — the **relative** ordering (the sanity bar) holds regardless.
