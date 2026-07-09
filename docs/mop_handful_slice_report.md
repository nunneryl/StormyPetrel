# MOP handful slice — adoption gate (recalibrated)

~5 CA spots across O'Reilly et al. 2016's MOP skill gradient, same nearshore
chain, same prod `pipeline.interpret`. This revision **recalibrates the adoption
gate** after the first Mac run exposed an over-aggressive distance veto and a
missing shore-normal check. **Read-only on MOP; nothing near prod.**

> **Egress, same discipline.** THREDDS is blocked here (`403`); the recalibration
> is driven by the **real first-run numbers the user reported from the Mac**, not
> fabricated ones. The new logic is proven offline (`--selftest`, 12/12). The
> re-run tables come from the Mac. Deliverables: `scripts/mop_handful_slice.py` +
> this report. Re-run: `python3 scripts/mop_handful_slice.py slice`.

## First-run findings that drove this
| spot | first run | read |
|---|---|---|
| Blacks (HIGH) | CONSUME, r=0.89, 458 m | correct |
| Lower Trestles (HIGH) | FALL BACK — **only** on dist > 500 m, r=0.94 | wrong reason — better agreement than Blacks |
| Ocean Beach SF | FALL BACK — **only** on dist > 500 m, r=0.90, dir_std 9° | wrong reason — excellent agreement |
| Malibu (MEDIUM) | r=0.85 but refraction offset **−64°** | height tracks, but the matched 10 m point faces a different shore-normal than the break |
| Rincon (HARD) | buoy 111 UNREACHABLE | acid test only failed-safe — never *measured* disagreement |

Conclusion: **distance is a weak proxy** (MOP points sit on the 10 m contour,
legitimately 0.5–1.5 km offshore) and the real gate is **buoy agreement +
shore-normal agreement**.

## The three changes
1. **Distance relaxed.** Hard-disqualify only beyond **1.2 km** (`MATCH_FALLBACK_M`
   500 → 1200). Within that, distance is informational, not a veto.
2. **Shore-normal-agreement check added.** Compare each spot's hand-set
   `orientation_deg` to the matched MOP point's `metaShoreNormal`; if
   `|Δ| > 35°` (`SHORE_NORMAL_MAX_DELTA`) the point isn't representative of the
   break's facing → **FALL BACK**, even if `Hs` correlates. This is the principled
   version of Malibu's −64° offset symptom. Reported per spot.
3. **Rincon buoy fixed.** Candidate list now `["071" (Harvest), "107", "111"]`
   (first reachable wins) so the acid test **measures** MOP-vs-buoy direction
   agreement instead of failing-safe. (All `buoy` fields are now ordered candidate
   lists; OB SF gets `["142","029"]`, etc.)

## The 5 spots
| spot | coords | zone (dir R²) | buoy candidates |
|---|---|---|---|
| San Diego Blacks Beach | 32.8797,-117.2530 | HIGH ~0.92 | 100 |
| Lower Trestles | 33.3815,-117.5859 | HIGH ~0.90 | 045, 100 |
| Malibu Surfrider Beach | 34.0314,-118.6889 | MEDIUM ~0.6 | 028, 092 |
| Rincon | 34.3718,-119.4785 | HARD ~0.04 — acid test | 071, 107, 111 |
| Ocean Beach SF | 37.7540,-122.5120 | UNKNOWN | 142, 029 |

## Recalibrated verdict logic (offline-proven; re-run fills the numbers)
`verdict(zone, r2, dist, hs_corr, dir_std, n, has_buoy, sn_delta)`, in order:
1. `dist > 1200 m` → **FALL BACK** (far outlier only).
2. `|sn_delta| > 35°` → **FALL BACK** (matched point faces a different stretch than the break).
3. no buoy / `n < 24`: **CONSUME (unverified)** only if HIGH-skill *and* shore-normal ok; else **FALL BACK**.
4. low-skill (HARD / R²<0.3): **CONSUME (override)** iff `r ≥ 0.85` and `dir_std ≤ 20°`; else **FALL BACK**.
5. HIGH/MEDIUM: **CONSUME** iff `r ≥ 0.80` and `dir_std ≤ 25°`; else **FALL BACK**.

`--selftest` exercises the full matrix (12/12), including the two new cases:
shore-normal mismatch Δ70° → FALL BACK, and good agreement @900 m → CONSUME.

## Expected re-run pattern (first-run numbers + predicted Δ; Mac confirms)
| spot | dist | Hs r | dir_std | shore-normal Δ (predicted) | → verdict | vs first run |
|---|---|---|---|---|---|---|
| Blacks | 458 m | 0.89 | small | small (open coast) | **CONSUME** | unchanged ✓ |
| Lower Trestles | ~0.5–0.9 km | 0.94 | small | small | **CONSUME** | **flips** (was dist-only FALL BACK) |
| Ocean Beach SF | <1.2 km | 0.90 | 9° | small (straight open coast) | **CONSUME** | **flips** |
| Malibu | — | 0.85 | — | **large (~65–85°)** — the −64° offset's cause | **FALL BACK** | same verdict, **right reason** (shore-normal, not distance) |
| Rincon | — | *measured* | *measured* | — | **FALL BACK** (expected: HARD + high dir_std), or override iff buoy says otherwise | now **measured**, not fail-safe |

This is the pattern the user predicted; the Mac re-run confirms or refutes it. If
it refutes (e.g. Rincon's Harvest agreement is surprisingly tight, or a "HIGH"
spot's shore-normal Δ is large), **the data wins** and we adjust the thresholds,
not the conclusion.

## Recommended rollout rule (checkable per spot)
```
CONSUME MOP for a CA spot iff ALL:
  |orientation_deg - metaShoreNormal|        <= 35 deg   (matched point faces the break)
  MOP-vs-nearest-buoy Hs correlation r       >= 0.80
  direction-offset stability circ_std        <= 25 deg
  match_distance                             <= 1200 m   (far-outlier veto only)
  [HARD/low-skill zones (SB Channel, dir R^2 < 0.3): require r >= 0.85 AND circ_std <= 20 deg]
no buoy within range AND not HIGH-skill  ->  FALL BACK (unverifiable)
otherwise  ->  keep the existing orientation path.
```
Gate order matters: **shore-normal agreement first** (cheap, needs only the cache
+ our orientations — catches Malibu without any buoy), **then** buoy agreement
(needs a live pull), with distance demoted to a sanity outlier-veto. Thresholds
are the script constants; freeze them from the handful's measured numbers before
the full ~170 rollout.

## Honest limits
- **Not measured here** (egress blocked): the re-run distances, star spans, buoy
  agreements, and — critically — the actual `metaShoreNormal` deltas (they need
  the cache from a Mac build). Malibu's large Δ is *predicted* from its −64°
  offset; the Mac run confirms it. The recalibrated **logic** is proven offline.
- Rincon's buoy candidates are best-effort; if Harvest/107/111 are all
  unreachable it still reports "can't verify → FALL BACK" rather than faking — but
  now it tries the real SB-Channel options first.
- Same `face_ft` calibration caveat as the Blacks slice (relative ordering holds).
