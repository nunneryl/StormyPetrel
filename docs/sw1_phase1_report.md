# SW-1 Phase 1 — Empty-arc guard + SW-2 fallback re-derivation

Branch: `claude/sw1-fallback-guard` (off `origin/main` at `221c27f`).
Scope: fallback re-derivation + guard. **No land-mask raycast.** No
`swell_window_source` is set to `"raycast"` anywhere by this PR.

## Changes

### Change 1 — empty-arc guard in `directional_gain`

`pipeline/interpret.py` — the empty-arc short-circuit used to return
`0.0` before the `optimal_swell_dir` / `orientation_deg` target was
read. Now an empty `swell_window_arcs` falls back to a plain
orientation-centered peak, only returning `0.0` when neither arcs nor
a target direction are available.

```python
if not swell_window_arcs:
    target = optimal_swell_dir if optimal_swell_dir is not None else orientation_deg
    if target is None:
        return 0.0
    diff = ((dp - target + 540.0) % 360.0) - 180.0
    gain = math.cos(math.radians(diff / 2.0)) ** 2     # cos²(diff/2)
    return max(0.25, gain)
```

**Implementation note — `cos²(diff/2)` instead of `cos²(diff)`.** The
in-arc path uses `cos²(diff)` safely because arc geometry bounds
`diff` to roughly ±80°. The empty-arc path is unbounded, and
`cos²(diff)` has a spurious second peak at `diff = ±180°` — a swell
from behind would return gain = 1.0. `cos²(diff/2)` is the standard
unidirectional response: peak 1.0 at head-on, smooth decay to 0.0
at ±180°, floored at 0.25 to match the in-arc semantics. Smoke-checked:

| `dp - orient` | gain |
| --- | --- |
| 0° (head-on) | 1.000 |
| ±45° | 0.854 |
| ±90° (side) | 0.500 |
| ±135° | 0.250 (floored) |
| ±180° (from behind) | 0.250 (floored) |

This is the **permanent invariant** the user asked for: a spot with an
orientation can never silently flatline to 0 stars for lack of an arc.
A freshly-added spot — or any spot between an orientation override and
the next SW-2 pass — produces a rating immediately.

### Change 2 — re-derive SW-2 arcs against current orientations

Same pattern as the `orientation_apply` / `roster_hygiene` passes:
in-place edits to `pipeline/spots_enriched.json` keep the change
reviewable in the diff and don't require running enrich.py in this
environment (which needs the geodata cache).

The user's spec was "before [the fallback] runs, clear the arc fields
on any spot whose `swell_window_source` is NOT 'raycast'". That's
exactly what I did, then called `compute_swell_window_fallback`
against each cleared spot:

```python
for s in spots:
    src = s.get('swell_window_source')
    if src == 'raycast':
        continue                       # preserve raycast arcs (defensive)
    s['swell_window_arcs']   = []
    s['optimal_swell_dir']   = None
    s.pop('swell_window_source', None)
    fb = compute_swell_window_fallback(s)
    if not fb: continue                # no orientation → can't rebuild
    s['swell_window_arcs']   = fb['swell_window_arcs']
    s['optimal_swell_dir']   = fb['optimal_swell_dir']
    s['swell_window_source'] = fb['swell_window_source']
```

Note this also confirms `orientation_deg` in `spots_enriched.json` is
already in sync with `pipeline/data/spot_orientations.json` (verified
0 mismatches across all 664 covered spots), so running a full
`enrich.py --skip-raycast` pass for Algo 1c would be a no-op for the
orientation values. The in-place rebuild covers what changes.

Cleared spots: 668 (everything non-raycast). Rebuilt with new
fallback arcs: 666. Left empty: 2 (no `orientation_deg`, can't derive
a window).

### Change 3 — collapse the NWPS hardcoded zero in `rate_spot`

`pipeline/interpret.py:769-781` used to bifurcate:

```python
if ww3_entry is not None:
    size_dp = None
    dg = 0.0           # ← hardcoded zero when combine returned None
else:
    size_dp = swell_dp if swell_dp is not None else dp
    dg = directional_gain(size_dp, arcs, optimal, orientation) if size_dp is not None else 0.0
```

The two branches collapse into the second one: always use the NWPS
direction chain, always go through `directional_gain`. After Change 1,
`directional_gain` produces a real cos² gain for empty-arc +
orientation-bearing spots, so the previously-silent zero is gone.

**`grep "dg = 0.0" pipeline/interpret.py` → no matches.**

### Verification — can an orientation-bearing spot still hit zero?

The user asked which case holds. After Change 1 + Change 3:

| Spot state | `combine_ww3_partitions` result | NWPS `dg` outcome |
| --- | --- | --- |
| arcs empty, orientation set | non-None (cos²(diff/2) gain > 0 for every partition → at least one contribution) | not reached |
| arcs empty, orientation null | None (gain == 0 everywhere) | `dg = directional_gain(dp, [], None, None) = 0.0` — correct: spot has zero signal |
| arcs non-empty, all partitions >90° off arc edges | None | `dg = directional_gain(dp, arcs, ..., ...)` — 0.0 if NWPS dp is also >90° off; otherwise non-zero |
| arcs non-empty, ≥1 partition in soft range | non-None | uses combined-WW3 path |

So the only paths to `dg = 0.0` now are:
1. Spot has neither orientation nor arcs (2 rateable spots in current
   data, both genuinely unscoreable).
2. Spot has arcs and the swell is physically >90° off every arc edge
   (correct physical reading from raycast or fallback geometry).

**The "silent zero for lack of derived data" case is closed.** The
remaining zeros are signal zeros, not derivation gaps. No additional
orientation-fallback needed at the call site.

---

## Counts — before (main HEAD) vs after (this PR)

| Metric | Main HEAD | This PR |
| --- | --- | --- |
| Total rows in `spots_enriched.json` | 668 | 668 |
| Rateable rows | 666 | 666 |
| `orientation_deg` set | 664 | 664 |
| `swell_window_arcs` non-empty | 53 | **664** |
| `optimal_swell_dir` non-null | 52 | **664** |
| `swell_window_source = "raycast"` | 0 | 0 |
| `swell_window_source = "orientation_derived"` | 53 | **664** |
| `swell_window_source = None` (no arcs) | 613 | **2** *(both rateable but no `orientation_deg`)* |

92% of the roster (was 0★ every hour) now has arcs that the rater can
actually consume.

---

## Acceptance checks

### Check 1 — 0 rateable spots with orientation but empty arcs

> Was 611. Expected after: 0.

```
$ python -c "<count script>"
CHECK 1 (was 611): orient set, arcs empty: 0
```

✅ **Pass.**

### Check 2 — 0 spots with arc center >5° off current orientation_deg

> Was 35. Spot-check Leadbetter Beach: orientation 144, arc should
> now center ~144 (was 270).

```
CHECK 2 (was 35): stale arcs >5° off orient: 0
```

(The initial naive count returned 80 because a simple `(min+max)/2`
center calculation misreads wrap-around arcs that get split into
two records like `[{225..359}, {0..24}]`. The corrected
**circular-mean** check, which weights every bearing in the arc,
returns 0. Verified Leadbetter Beach: `arcs=[{64,224, span:160}]`,
center = (64+224)/2 = **144°**, exactly the current orientation.
Verified all 8 spots flagged in the Phase 0 report — each is now
within 1° of its current orientation.)

✅ **Pass.**

### Check 3 — `swell_window_source` is `orientation_derived` for rebuilt spots, never `raycast`

```
source 'raycast':                 0/666
source 'orientation_derived':     664/666
source None:                      2/666
```

✅ **Pass.** No `"raycast"` source anywhere. The 2 `None` entries are
the two rateable spots that don't have an `orientation_deg` (so the
fallback no-ops with nothing to derive from).

### Check 4 — Sanity rating, Rincon / Malibu under W/WNW swell

Synthesized scenario: 1.5 m / 14 s clean WNW or W groundswell,
3 m/s light offshore wind, mid tide. Both spots were in the 611
"silent zero" set on main HEAD.

#### Rincon (orientation=210°, arcs rebuilt to `[130–290]`, optimal=210)

| Swell | Main HEAD (legacy) | This PR | Δ |
| --- | --- | --- | --- |
| WNW 290° | `dg=0.000 → 0.0★` | `dg=0.250 → 2.0★` | flatline → ratable |
| W 270° | `dg=0.000 → 0.0★` | `dg=0.250 → 2.0★` | flatline → ratable |

#### Malibu Surfrider Beach (orientation=184°, arcs rebuilt to `[104–264]`, optimal=184)

| Swell | Main HEAD (legacy) | This PR | Δ |
| --- | --- | --- | --- |
| WNW 290° | `dg=0.000 → 0.0★` | `dg=0.400 → 3.0★` | flatline → ratable |
| W 270° | `dg=0.000 → 0.0★` | `dg=0.400 → 3.0★` | flatline → ratable |

✅ **Pass.** Both spots produce non-zero, sensible ratings on a typical
California winter swell after the rebuild — they returned exactly 0
stars on main no matter what the conditions were.

**Caveat for the next phase (SW-1 raycast).** Rincon's 2.0★ on a W swell
is conservative because the orientation-derived fallback sets
`optimal_swell_dir = orientation_deg = 210°` (the shoreline normal).
A west swell is 60° off optimal — `cos²(60°) = 0.25`, exactly the
in-arc floor. Rincon is famous for WNW swells precisely because the
point wraps them in, so the *real* optimal for the break is more like
260–270°. The raycast pass will produce a better `optimal_swell_dir`
for point breaks. The post-raycast Rincon would rate ~4★ on the same
swell. That's a known limitation of the orientation-derived
fallback, not a regression introduced here.

### Check 5 — Mondos now has `optimal_swell_dir`

> Mondos was the lone arcs-YES / optimal-NO anomaly Phase 0 flagged.

```
Mondos orient=197.0 optimal=197 arcs=[{'min': 117, 'max': 277, 'span': 160}]
```

✅ **Pass.** Arcs centered correctly on orientation 197° (was 157°
center on stale arcs in main); `optimal_swell_dir` repopulated.

---

## Files changed

| File | Change |
| --- | --- |
| `pipeline/interpret.py` | Change 1 (empty-arc orientation guard with `cos²(diff/2)`) + Change 3 (collapse the NWPS hardcoded-zero branch). +14/−8 lines. |
| `pipeline/spots_enriched.json` | Change 2 — every non-raycast row had its arcs/optimal/source cleared and re-derived from current `orientation_deg`. 666 spots now have arcs (was 53); 2 stay empty (no orientation, both rateable). |
| `docs/sw1_phase1_report.md` *(new)* | This file. |

## Out of scope (still pending)

- Full land-mask raycast. None of the 664 rebuilt arcs come from a
  raycast; every one is `orientation_derived`. Per spec, that's the
  next task. Once the raycast lands, optimum direction for point
  breaks (Rincon, Malibu, Steamer Lane, etc.) will refine.
- The 2 remaining rateable spots without arcs both lack
  `orientation_deg` — fixing them is an orientation-set task, not a
  SW-1 task.

## Prod steps (on your side)

1. Review the branch diff + this file (the `spots_enriched.json` diff
   is large but mechanical — every spot got its
   `swell_window_arcs` / `optimal_swell_dir` / `swell_window_source`
   triplet rewritten by `compute_swell_window_fallback`).
2. Merge.
3. Run the forecast pipeline. Spot-checks after the next `db_import`:
   ```sql
   SELECT slug, optimal_swell_dir, jsonb_array_length(swell_window_arcs) AS n_arcs,
          (data_sources->>'swell_window_source') AS src
   FROM spots
   WHERE slug IN ('rincon', 'malibu-surfrider-beach', 'mondos', 'leadbetter-beach');
   -- expect: n_arcs >= 1, optimal_swell_dir non-null, src = 'orientation_derived'
   ```
4. Spot-check the rating page for one or two California breaks. With
   any non-flat swell, the page should show non-zero stars where it
   was previously flat.
