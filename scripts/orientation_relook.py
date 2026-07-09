#!/usr/bin/env python3
"""Orientation relook list — rank spots whose hand orientation_deg most
disagrees with physics, so only the genuinely-off ones get re-checked.

READ-ONLY analysis + a tool data file (scripts/orientation_relook.json). Touches
NO prod: not spots_enriched.json, not spot_orientations.json. Nothing is applied
here — the user reorients in the tool, exports, and we apply via the reviewed
apply_orientation_fixes path.

Disagreement score per spot = max of:
  a. SEED Δ   = |orientation_deg − geometric seaward-normal seed|.
     Seed = compute_orientation() (Algorithm 1, needs GSHHG) when available;
     otherwise the stored geometric windows (circular mean of orientation_50m /
     orientation_200m). So this runs FULLY on the Mac/CI (GSHHG) and PARTIALLY
     anywhere (the spots whose 50m/200m windows are stored).
  b. MOP Δ    = |orientation_deg − matched MOP metaShoreNormal| (CA only, from
     scripts/mop_points.json, match ≤ ~1500 m). Real CDIP physics.
  c. soft flag (CA, optional): a "worth a glance" marker (e.g. a point break whose
     shore-normal agrees but whose break-optimal may differ).

Rank worst-first by the score; keep score ≥ threshold (default 30°). Output
scripts/orientation_relook.json. Reports counts at Δ 30/40/50.

  python3 scripts/orientation_relook.py            # build the list (run on Mac for full seed+MOP)
  python3 scripts/orientation_relook.py --threshold 40
  python3 scripts/orientation_relook.py --selftest # offline math proof
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SPOTS_JSON = os.path.join(ROOT, "pipeline", "spots_enriched.json")
MOP_CACHE = os.path.join(HERE, "mop_points.json")
OUT_JSON = os.path.join(HERE, "orientation_relook.json")

MOP_MATCH_MAX_M = 1500.0
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


def ang_dist(a, b):
    """Smallest unsigned angle between two bearings, [0,180]."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def circular_mean(degs):
    r = [math.radians(d) for d in degs]
    x = sum(math.cos(t) for t in r); y = sum(math.sin(t) for t in r)
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    return math.degrees(math.atan2(y, x)) % 360.0


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def ca_zone(lat, lng):
    """Coarse CA skill zone (O'Reilly 2016 gradient) by latitude band."""
    if lng > -114 or lat < 32 or lat > 42:
        return None  # not CA coast
    if 32.5 <= lat <= 33.5:
        return "HIGH"      # San Diego / San Clemente Basin
    if 33.5 < lat <= 34.05:
        return "MEDIUM"    # San Pedro / Santa Monica
    if 34.05 < lat <= 34.6:
        return "HARD"      # Santa Barbara Channel
    return "UNKNOWN"       # Central / Northern CA


def seed_normal(spot):
    """(seed_deg, source) — geometric seaward normal. compute_orientation when
    GSHHG is present; else the stored geometric windows; else (None, None)."""
    try:
        from pipeline.enrichment.orientation import compute_orientation
        r = compute_orientation(spot)
        if r and r.get("orientation_deg") is not None:
            return float(r["orientation_deg"]), "geom"
    except Exception:  # noqa: BLE001
        pass
    ws = [spot.get(k) for k in ("orientation_50m", "orientation_200m")]
    ws = [float(w) for w in ws if w is not None]
    if ws:
        m = circular_mean(ws)
        return (m, "stored_50_200") if m is not None else (None, None)
    return None, None


def load_mop_points():
    if not os.path.exists(MOP_CACHE):
        return None
    raw = json.load(open(MOP_CACHE))
    return [(pid, m) for pid, m in raw.items()
            if m.get("lat") is not None and m.get("shore_normal") is not None]


def mop_match(spot, mop):
    if not mop:
        return None, None, None, None
    lat, lng = spot["lat"], spot["lng"]
    pid, m = min(mop, key=lambda kv: haversine_m(lat, lng, kv[1]["lat"], kv[1]["lon"]))
    d = haversine_m(lat, lng, m["lat"], m["lon"])
    if d > MOP_MATCH_MAX_M:
        return None, None, None, d
    return pid, float(m["shore_normal"]), d, d


def fold_seed(delta):
    """Fold a seaward-normal seed delta onto [0,90]. A delta near 180° is the
    normal computation resolving BACKWARDS (a sign flip), not a real
    disagreement, so min(Δ, 180−Δ): 177→3, 92→88, 135→45."""
    return None if delta is None else min(delta, 180.0 - delta)


def score_one(orientation_deg, seed_normal, seed_delta, mop_delta, zone, break_type, threshold):
    """Corrected score: fold the seed, then PREFER MOP (real CDIP physics) where
    present. score = mop_delta if present else folded_seed_delta."""
    folded = fold_seed(seed_delta)
    score = mop_delta if mop_delta is not None else (folded if folded is not None else 0.0)
    # seed_display = the seed axis end nearest the current orientation, so the
    # rose arrow shows the seaward-consistent side and the visible gap == folded Δ.
    seed_display = None
    if seed_normal is not None and orientation_deg is not None:
        seed_display = (seed_normal if ang_dist(seed_normal, orientation_deg) <= 90
                        else (seed_normal + 180.0) % 360.0)
    seed_unreliable = (folded is not None and mop_delta is not None and abs(folded - mop_delta) > 30.0)
    reasons = []
    if mop_delta is not None:
        if mop_delta >= threshold:
            reasons.append(f"MOP Δ{mop_delta:.0f}°")
        if seed_unreliable:
            reasons.append("seed unreliable here — trust MOP")
    elif folded is not None and folded >= threshold:
        reasons.append(f"seed Δ{folded:.0f}°")
    soft = (zone is not None and break_type == "point" and mop_delta is not None
            and mop_delta < threshold and folded is not None and 15 <= folded < threshold)
    if soft:
        reasons.append("point-optimal?")
    return {
        "folded_seed_delta": round(folded, 1) if folded is not None else None,
        "seed_display": round(seed_display, 1) if seed_display is not None else None,
        "score": round(score, 1),
        "seed_unreliable": seed_unreliable,
        "reason": ", ".join(reasons) or f"score {score:.0f}°",
        "on_list": (score >= threshold or soft),
    }


def _finalize(rows, threshold, meta, agree_pairs):
    """Sort, count, print the corrected summary + the fold confirmation, write JSON."""
    rows.sort(key=lambda r: r["score"], reverse=True)
    c30 = sum(1 for r in rows if r["score"] >= 30)
    c40 = sum(1 for r in rows if r["score"] >= 40)
    c50 = sum(1 for r in rows if r["score"] >= 50)
    meta.update(threshold_deg=threshold, on_list=len(rows),
                count_at_30=c30, count_at_40=c40, count_at_50=c50)
    payload = {
        "_comment": "Orientation relook — worst-first by CORRECTED score: seed delta "
                    "folded onto [0,90] (180° = sign flip, not disagreement); MOP delta "
                    "preferred where present. Tool data file; not prod.",
        "_meta": meta, "spots": rows,
    }
    json.dump(payload, open(OUT_JSON, "w"), indent=2)
    print(f"on the list (≥{threshold:.0f}°): {len(rows)}   |   count at Δ 30/40/50: {c30} / {c40} / {c50}")
    if agree_pairs:
        import statistics
        diffs = [abs(f - m) for f, m in agree_pairs]
        agree = sum(1 for d in diffs if d <= 15)
        verdict = ("CORRECT (folded seed ≈ MOP)" if agree >= 0.7 * len(diffs) else "suspect — inspect")
        print(f"fold check: {len(agree_pairs)} spots have both folded-seed & MOP; "
              f"mean|folded−MOP|={statistics.mean(diffs):.0f}°, {agree}/{len(diffs)} agree ≤15° → fold is {verdict}")
    print(f"\nworst {min(15, len(rows))}:")
    print(f"  {'name':30}{'orient':>7}{'fSeed':>6}{'MOPΔ':>6}{'zone':>8}  reason")
    for r in rows[:15]:
        fs = "—" if r.get("folded_seed_delta") is None else f"{r['folded_seed_delta']:.0f}"
        md = "—" if r.get("mop_delta") is None else f"{r['mop_delta']:.0f}"
        print(f"  {r['name'][:29]:30}{r['orientation_deg']:7.0f}{fs:>6}{md:>6}{str(r['zone']):>8}  {r['reason']}")
    print(f"\nwrote {OUT_JSON}")
    return 0


def build(threshold=30.0):
    spots = json.load(open(SPOTS_JSON))
    mop = load_mop_points()
    gsh_ok = False
    try:
        from pipeline.enrichment.geodata import load_land_index
        gsh_ok = load_land_index() is not None
    except Exception:  # noqa: BLE001
        pass
    print(f"inputs: {len(spots)} spots | GSHHG seed {'available' if gsh_ok else 'ABSENT (stored-window seed only)'}"
          f" | MOP cache {'loaded ('+str(len(mop))+' pts)' if mop else 'absent (no MOP Δ)'}")

    rows, agree = [], []
    scored = seed_geom = seed_stored = mop_scored = 0
    for s in spots:
        if s.get("is_valid_surf_spot") is False:
            continue
        od = s.get("orientation_deg")
        if od is None:
            continue
        name = s.get("name") or ""
        lat, lng = s.get("lat"), s.get("lng")
        seed, ssrc = seed_normal(s)
        seed_delta = ang_dist(od, seed) if seed is not None else None
        if ssrc == "geom":
            seed_geom += 1
        elif ssrc == "stored_50_200":
            seed_stored += 1

        zone = ca_zone(lat, lng) if lat is not None else None
        mop_pid = mop_sn = mop_delta = mop_dist = None
        if zone is not None and mop:
            mop_pid, mop_sn, mop_dist, _raw = mop_match(s, mop)
            if mop_sn is not None:
                mop_delta = ang_dist(od, mop_sn); mop_scored += 1

        if seed_delta is None and mop_delta is None:
            continue  # nothing to score this spot against
        scored += 1
        sf = score_one(od, seed, seed_delta, mop_delta, zone, s.get("break_type"), threshold)
        if sf["folded_seed_delta"] is not None and mop_delta is not None:
            agree.append((sf["folded_seed_delta"], mop_delta))
        if not sf["on_list"]:
            continue
        rows.append({
            "slug": slugify(name), "name": name, "lat": lat, "lon": lng,
            "orientation_deg": od,
            "seed_normal": round(seed, 1) if seed is not None else None,
            "seed_source": ssrc,
            "seed_delta": round(seed_delta, 1) if seed_delta is not None else None,
            "folded_seed_delta": sf["folded_seed_delta"],
            "seed_display": sf["seed_display"],
            "mop_point": mop_pid,
            "mop_shore_normal": round(mop_sn, 1) if mop_sn is not None else None,
            "mop_delta": round(mop_delta, 1) if mop_delta is not None else None,
            "mop_match_m": round(mop_dist) if mop_dist is not None else None,
            "zone": zone, "break_type": s.get("break_type"),
            "score": sf["score"], "seed_unreliable": sf["seed_unreliable"],
            "reason": sf["reason"],
        })

    print(f"\nscored {scored} spots (seed: {seed_geom} geom + {seed_stored} stored-window; MOP Δ on {mop_scored})")
    meta = {"scored_spots": scored, "seed_from_geom": seed_geom,
            "seed_from_stored_windows": seed_stored, "mop_scored": mop_scored,
            "gshhg_seed_available": gsh_ok, "mop_cache_available": bool(mop),
            "scoring": "folded_seed; MOP-preferred", "partial": not (gsh_ok and mop)}
    if meta["partial"]:
        print("  ** PARTIAL run — full nationwide seed needs GSHHG; MOP Δ needs the cache. "
              "Run on the Mac for the complete list. **")
    return _finalize(rows, threshold, meta, agree)


def rerank(threshold=30.0):
    """Recompute the ranking from the seed/MOP values ALREADY in
    orientation_relook.json — no GSHHG re-run. Folds the seed, prefers MOP."""
    if not os.path.exists(OUT_JSON):
        print(f"no {OUT_JSON} to rerank — run a build first (on the Mac for the full seed).", file=sys.stderr)
        return 3
    payload = json.load(open(OUT_JSON))
    old = payload.get("spots", [])
    print(f"reranking {len(old)} existing candidates (reusing seeds; no GSHHG re-run)")
    rows, agree = [], []
    for r in old:
        od, seed = r.get("orientation_deg"), r.get("seed_normal")
        seed_delta = r.get("seed_delta")
        if seed_delta is None and seed is not None and od is not None:
            seed_delta = ang_dist(od, seed)
        mop_delta = r.get("mop_delta")
        sf = score_one(od, seed, seed_delta, mop_delta, r.get("zone"), r.get("break_type"), threshold)
        if sf["folded_seed_delta"] is not None and mop_delta is not None:
            agree.append((sf["folded_seed_delta"], mop_delta))
        if not sf["on_list"]:
            continue
        nr = dict(r)
        nr.update(seed_delta=round(seed_delta, 1) if seed_delta is not None else None,
                  folded_seed_delta=sf["folded_seed_delta"], seed_display=sf["seed_display"],
                  score=sf["score"], seed_unreliable=sf["seed_unreliable"], reason=sf["reason"])
        rows.append(nr)
    meta = dict(payload.get("_meta", {}))
    meta.update(reranked=True, scoring="folded_seed; MOP-preferred",
                reranked_from=len(old))
    return _finalize(rows, threshold, meta, agree)


def run_selftest():
    ok = True
    def check(n, c):
        nonlocal ok; ok = ok and c; print(f"  {'PASS' if c else 'FAIL'}  {n}")
    check("slugify", slugify("San Diego Blacks Beach!") == "san-diego-blacks-beach")
    check("ang_dist wrap 350 vs 10 = 20", ang_dist(350, 10) == 20)
    check("ang_dist 263 vs 90 = 173", ang_dist(263, 90) == 173)
    check("circular_mean wrap (350,10)->0", abs(((circular_mean([350, 10]) + 180) % 360) - 180) < 1e-6)
    check("ca_zone HIGH (Blacks)", ca_zone(32.88, -117.25) == "HIGH")
    check("ca_zone HARD (Rincon)", ca_zone(34.37, -119.48) == "HARD")
    check("ca_zone None (Rhode Island)", ca_zone(41.49, -71.25) is None)
    check("haversine ~100 m", 90 <= haversine_m(34.0, -119.0, 34.0, -118.9989) <= 110)
    # seed sign-flip fold
    check("fold 177 -> 3 (sign flip)", fold_seed(177) == 3)
    check("fold 92 -> 88", fold_seed(92) == 88)
    check("fold 135 -> 45", fold_seed(135) == 45)
    check("fold 45 -> 45 (genuine)", fold_seed(45) == 45)
    check("fold 90 -> 90", fold_seed(90) == 90)
    # Chart-House case: seed Δ177 + MOP Δ4 -> folded 3, MOP preferred -> score 4, drops at thr 30
    ch = score_one(263, 86, 177, 4, "HIGH", "reef", 30)
    check(f"Chart-House (seed177,MOP4): folded {ch['folded_seed_delta']}≈MOP, score {ch['score']}, off-list",
          ch["folded_seed_delta"] == 3 and ch["score"] == 4 and not ch["on_list"])
    # genuine MOP disagreement stays; seed unreliable flagged
    big = score_one(210, 30, 177, 48, "HARD", "point", 30)
    check(f"MOP48+seed-flip: score {big['score']} (MOP), unreliable flag {big['seed_unreliable']}",
          big["score"] == 48 and big["seed_unreliable"] and big["on_list"])
    # seed-only genuine 70° disagreement survives folding
    s70 = score_one(200, 130, 70, None, None, "beach", 30)
    check(f"seed-only 70° survives (folded {s70['folded_seed_delta']}, on-list)",
          s70["folded_seed_delta"] == 70 and s70["on_list"])
    print("\nself-test:", "ALL PASS" if ok else "FAILURES above")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="build", choices=["build", "rerank"],
                    help="build = full pass (needs GSHHG seed/MOP); rerank = recompute "
                         "ranking from the seeds already in orientation_relook.json (cheap)")
    ap.add_argument("--threshold", type=float, default=30.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    return rerank(a.threshold) if a.cmd == "rerank" else build(a.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
