# SW-1 Phase 0 — Swell-window diagnosis (read-only)

Branch: `claude/sw1-phase0-diagnosis` (off `origin/main` at `c2bf626`).
**No code changed.** This is the answer to the two pre-rewire questions.

## TL;DR

1. **Arcs are consumed as a hard gate, not just a peak shape.** Empty
   `swell_window_arcs` returns `dir_gain = 0.0` before `optimal_swell_dir`
   or `orientation_deg` is ever read. The cos² peak (which is what falls
   back to `orientation_deg`) lives *inside* a non-empty arc — you only
   reach it once you're already in window.
2. **It's a derivation gap, not "fewer windows than assumed".** 664/666
   rateable spots have `orientation_deg` set, but only 53/666 have any
   arcs at all. The SW-2 orientation-derived fallback never ran on the
   611 spots whose orientation was injected by the manifest 1 / 1b / 1c
   passes — those passes hand-edit `spots_enriched.json` directly and
   don't invoke `compute_swell_window_fallback`. Of the 53 spots that
   *do* have arcs, **35 are stale** — built from an older orientation
   that the orientation-apply manifest has since overwritten.

The next SW-1 step (full raycast) is the right move; even before
that, a one-time re-run of the SW-2 fallback against the current
orientations would lift ~611 spots out of "always 0 stars" without
any geometry work.

---

## 1. How `swell_window_arcs` are actually consumed in the rating

### Path A — `directional_gain`

`pipeline/interpret.py:129` — same function for both branches below.

```python
def directional_gain(dp, swell_window_arcs, optimal_swell_dir, orientation_deg, ...):
    if not swell_window_arcs:
        return 0.0                              # ← HARD GATE

    in_window = _in_any_arc(dp, swell_window_arcs)
    if not in_window:
        offset = _min_offset_from_arcs(dp, swell_window_arcs)
        if offset < 45.0:   return 0.40         # refracted-swell penalty
        if offset < 90.0:   return 0.15
        return 0.0                              # >90° off: physically blocked

    target = optimal_swell_dir if optimal_swell_dir is not None else orientation_deg
    if target is None:      return 0.5          # neutral inside-window gain
    diff = ((dp - target + 540) % 360) - 180
    return max(0.25, cos²(diff))                # in-window cos² peak
```

So three things are true at once:

- **Outside the arc** → graduated 0.40 / 0.15 / 0.0 penalty by edge distance.
- **Inside the arc** → cos²(offset from target) with a 0.25 floor.
- **Empty arcs (`not swell_window_arcs`)** → 0.0 immediately. You
  never reach the `target = optimal_swell_dir if … else orientation_deg`
  fallback. The `orientation_deg`-as-target backstop only matters
  *once you already have arcs*.

### Path B — WW3 partition combiner

`pipeline/interpret.py:529` — `combine_ww3_partitions` calls
`directional_gain` for each swell partition (`swell_1`, `swell_2`,
`swell_3`); a partition with `gain <= 0` is dropped. With empty arcs
every partition's gain is 0 → contributions list is empty → combine
returns `None`.

### Path C — NWPS fallback in `rate_spot`

`pipeline/interpret.py:744-766` — runs when `combine_ww3_partitions`
returns `None`. Two sub-branches:

```python
if ww3_entry is not None:
    size_dp = None
    dg = 0.0           # "WW3 covered the hour but every partition blocked"
else:
    size_dp = swell_dp if swell_dp is not None else dp
    dg = directional_gain(size_dp, arcs, optimal, orientation) if size_dp else 0.0
```

Both sub-branches collapse to `dg = 0.0` when arcs are empty: the first
because the conflation of "every partition >90° off the configured
arcs" with "no arcs at all" produces a hard zero, the second because
`directional_gain` short-circuits on empty arcs (the very first
check, before the optimal/orientation fallback).

### Behavioral consequence

`interpret.py:793-799`:

```python
if face_source == "ww3":   effective = fft or 0.0
else:                       effective = (fft or 0.0) * dg
stars = composite_stars(effective, wm, tm, cm, pq) if fft is not None else 0.0
```

…and `composite_stars` gates at `if effective_face_ft < 0.5: return 0.0`.

⇒ A spot with empty `swell_window_arcs` produces **`stars = 0.0` for
every hour, every forecast cycle**, regardless of incoming swell
height, period, direction, wind, tide, or any other input. The
orientation_deg-derived target inside the cos² block exists in the
code, but for 92% of the roster it is unreachable.

---

## 2. Field-population counts (current `pipeline/spots_enriched.json`)

Filter: same `is_valid_surf_spot is not False` + non-null lat/lng that
`db_import.import_spots` and `interpret.compute_ratings` apply, so
counts reflect what actually enters the rating pipeline.

| Metric | Count | Share of rateable |
| --- | --- | --- |
| Total rows in `spots_enriched.json` | 668 | — |
| Rateable rows | 666 | 100% |
| `orientation_deg` set | 664 | 99.7% |
| `swell_window_arcs` non-empty | 53 | **8.0%** |
| `optimal_swell_dir` non-null | 52 | **7.8%** |
| `orientation_source == "manual"` | 664 | 99.7% (664 with arcs path + without) |

`swell_window_source` distribution:

| Source | Rows |
| --- | --- |
| `orientation_derived` (SW-2 fallback) | 53 |
| `None` (no arcs ever computed) | 613 |
| `raycast` | **0** — SW-1 has never run on this roster |

Cross-tab of arcs × `optimal_swell_dir`:

|   | optimal YES | optimal NO |
| --- | --- | --- |
| arcs YES | 52 | 1 *(Mondos — see "Staleness" below)* |
| arcs NO | 0 | 613 |

So `optimal_swell_dir` is purely a function of `arcs` — it's set
exactly when the SW-2 fallback fires (which writes both fields in the
same call: `swell_window_fallback.py:78-82`). The 52 / 666 figure is a
faithful proxy for "spots that have arcs at all."

### Why are 611 rateable spots without arcs even though they have orientation?

`pipeline/enrichment/swell_window_fallback.py:62`:

```python
def compute_swell_window_fallback(spot):
    arcs = spot.get("swell_window_arcs") or []
    if arcs:               return {}     # no-op if arcs already present
    orientation = spot.get("orientation_deg")
    if orientation is None: return {}    # no-op if no orientation
    # else: build a centered arc from orientation, return fields to patch
```

The fallback *would* produce arcs for all 611 of those spots — they have
`orientation_deg` set and empty arcs, which is exactly its trigger.

The reason it didn't is that `manifest 1` / `manifest 2` /
`orientation_apply` all wrote `orientation_deg` directly into
`spots_enriched.json` by hand-edit, **without re-invoking
`enrich.py`**. The fallback only runs inside the enrichment loop
(`enrich.py:296`). So 611 spots had their orientation set in-place but
never had the SW-2 fallback fire against it.

That answers the user's three-way question:
- Derivation gap: **yes, this is the dominant cause.**
- Clearing/staleness: **secondary** — see below.
- Fewer real windows than assumed: **no** — virtually every rateable
  spot has a usable orientation; the arcs just weren't built off it.

---

## 3. Staleness of the 53 spots that *do* have arcs

Of the 53 spots with arcs, the arc center can be compared against
the current `orientation_deg`:

| Arc–orientation alignment | Count |
| --- | --- |
| Within 5° (fresh) | 18 |
| 5–10° off | (in stale below) |
| **>5° off (stale)** | **35** |

Worst offenders (sample):

| Spot | `orientation_deg` | Arc center | Off-by |
| --- | --- | --- | --- |
| Leadbetter Beach | 144° | 270° | **126°** |
| Second Peak | 153° | 225° | 72° |
| Cayucos Pier | 207° | 270° | 63° |
| First Peak | 154° | 210° | 56° |
| The Hook | 149° | 202° | 53° |
| Mondos | 197° | 157° | 40° |
| Sewer Peak | 186° | 225° | 39° |
| Wind and Sea | 190° | 225° | 35° |
| Huntington Beach Pier | 220° | 247° | 27° |
| Mavericks, California | 245° | 270° | 25° |

These are the spots whose arcs were built before the
`orientation_apply` pass overwrote their `orientation_deg`. The arcs
are still consulted by `directional_gain` as written — meaning the
rater is gating swells against a window that no longer matches the
break's actual aim. The `Leadbetter Beach` row is the extreme case:
the arc points roughly N, the orientation says S — i.e. the in-window
check is effectively inverted.

Mondos (the one `arcs YES / optimal_swell_dir NO` row) is the same
story plus a half-finished cleanup: its arcs survived the
orientation override but `optimal_swell_dir` got cleared. That looks
like the `cleanup_spots.apply_cleanup` stale-field reset firing
selectively — worth confirming but not on the critical path.

---

## 4. What this means for the rest of SW-1

Two distinct sub-tasks fall out of this, and they are independently
deliverable:

1. **Re-derive SW-2 arcs against current orientations.** Cheap. No
   geometry — just run `enrich.py` (or a focused `compute_swell_window_fallback`
   pass) on the 611 + 35 = ~646 spots that need their arcs rebuilt
   from current `orientation_deg`. This alone moves ~92% of the
   roster out of "always 0 stars" and corrects the 35 stale ones.
2. **Run the full raycast (SW-1 proper).** What the backlog calls for.
   Replaces the orientation-derived fallback with the real
   land-mask raycast for every spot, populating `swell_window_arcs`
   and `optimal_swell_dir` at higher fidelity, with
   `swell_window_source = "raycast"` instead of `"orientation_derived"`.

If (1) is done first, (2) just improves an already-functional roster.
If (2) is done without (1) first, the raycast subsumes (1) for every
spot it covers — but if any spot fails the raycast (Great Lakes,
ambiguous coastlines, etc.) the fallback should still patch in, so
the same code path matters either way.

One bug worth fixing alongside whichever step you do first:

- **`directional_gain` line 161 should not zero on `not swell_window_arcs`
  when orientation is available.** Today, `dg = 0.0` is returned
  before the `target = optimal_swell_dir if … else orientation_deg`
  fallback is reached. Adding a "no arcs but have orientation →
  use orientation as a soft 160° implicit window" would have
  prevented all 611 rows from being silently flatlined to 0 stars
  while we wait for the proper arcs. That's not a substitute for
  fixing the derivation gap — it's a belt-and-braces guarantee that
  a missing arc field is never a silent zero.

No code changed in this PR. Decision points for you:

- Option A — re-enrich first, then raycast. Smallest delta, fastest
  win.
- Option B — raycast directly, accept that the orientation-derived
  fallback may also need a refresh for any leftover spots.
- Option C — both, with the soft-zero guard added to
  `directional_gain` so a missing arc never silently zeros the rating
  again.

Recommendation: **Option C.** The guard is a 4-line change and locks
in invariant-by-construction that "spots with orientation always rate."
The re-enrich is a single command. The raycast is the bigger geometry
pass — and it's safer to do once a roster-wide rating baseline already
exists for diff-comparison.
