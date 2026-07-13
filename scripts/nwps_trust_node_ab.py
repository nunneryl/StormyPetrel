#!/usr/bin/env python3
"""NWPS trust gate — plain-vs-seaward node A/B (Mac; needs NOMADS + NDBC).

READ-ONLY. Changes NOTHING: not the gate, not its default node selection, not a
tag, not spots_enriched.json. It answers ONE question for the zones the
blast-radius audit (scripts/nwps_node_audit.py) flagged as divergent:

    Is the node the culprit? Run the trust correlation TWICE for a buoy —
    once at the PLAIN-NEAREST cell (the gate's current control) and once at the
    nearest-SEAWARD cell — over the SAME cycles, SAME hours, SAME pairing, and
    the SAME verdict math. The ONLY thing that differs is which (i,j) the model
    is sampled at. That isolates node selection as the variable.

Why this and not a fix: circ_std measures SPREAD, not mean. A *constant*
refraction offset from a shadowed node gives circ_std ≈ 0 and PASSes anyway, so
"the node is shoreward" does NOT by itself explain a verdict. Only re-running the
correlation at the seaward cell shows whether the node actually moves r / circ_std.

The assembly loop below is copied VERBATIM from trust_check (nwps_nearshore, the
`for date, cc, url in recent_cycles(...)` block) and both cells are filled in a
SINGLE pass, so plain and seaward are guaranteed identical except for (i,j). The
'plain' column is therefore the control and MUST reproduce the production gate's
verdict for that buoy — if it doesn't, the harness drifted (a built-in check).

    python scripts/nwps_trust_node_ab.py                 # the 4 audit-divergent buoys
    python scripts/nwps_trust_node_ab.py --buoys box:44097,phi:44065
    python scripts/nwps_trust_node_ab.py --verbose        # per-hour dirpw−MWD at both cells
    python scripts/nwps_trust_node_ab.py --selftest       # OFFLINE wiring check (no NOMADS)

A non-reporting buoy (no NDBC feed right now) is noted and skipped — never
fabricated. Grid/mask is static, so recent cycles are fetched once per buoy.
"""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
# load_cycle lazily imports cfgrib inside the call → importing the module is safe offline.
from pipeline.forecast import nwps_nearshore as nn  # noqa: E402

# The buoys the blast-radius audit reported as plain-nearest ≠ seaward (the only
# buoys where an A/B can differ). Overridable with --buoys.
_DIVERGENT = [("box", "44097"), ("phi", "44065"), ("box", "44098"), ("mhx", "44095")]


def _cells(cyc, blat, blng):
    """((i,j,lat,lng,dist) plain, (i,j,lat,lng,dist) seaward | None). The seaward
    pick IS the one _node_diag reports (reused, so it can't drift); its (i,j) is
    recovered via _nearest_cell on the seaward cell's own coords (dist 0 → itself)."""
    p = nn._nearest_cell(cyc, blat, blng)
    if p is None:
        return None, None
    d = nn._node_diag(cyc, blat, blng, p[0], p[1], p[2])
    plain = (p[0], p[1], d["lat"], d["lng"], d["dist_km"])
    if d.get("seaward_nearest_lat") is None or not d.get("seaward_differs"):
        return plain, None
    s = nn._nearest_cell(cyc, d["seaward_nearest_lat"], d["seaward_nearest_lng"])
    seaward = (s[0], s[1], d["seaward_nearest_lat"], d["seaward_nearest_lng"],
               d["seaward_nearest_dist_km"])
    return plain, seaward


def assemble_ab(wfo, blat, blng, n_cycles=4, *, _cycles=None, _now=None):
    """Model series at BOTH cells over identical cycles/hours. Returns (series, cells)
    keyed 'plain'/'seaward'. The inner loop is trust_check's, copied verbatim; both
    cells are sampled in ONE pass so nothing but (i,j) differs. _cycles (list of cycle
    dicts) and _now are injectable for the offline selftest."""
    now = _now or datetime.datetime.now(datetime.timezone.utc)
    if _cycles is not None:
        cycles = _cycles
    else:
        cycles = [nn.load_cycle(wfo, c) for c in nn.recent_cycles(wfo, n_cycles, nn._region_for(wfo))]
    series = {"plain": {}, "seaward": {}}
    cells = {"plain": None, "seaward": None}
    for cyc in cycles:
        elapsed = int((now - cyc["cycle_dt"]).total_seconds() // 3600)
        if elapsed < 0:
            continue
        plain, seaward = _cells(cyc, blat, blng)
        for key, cell in (("plain", plain), ("seaward", seaward)):
            if cell is None:
                continue
            cells[key] = cell
            i, j = cell[0], cell[1]
            s = series[key]
            for fh in cyc["steps"]:               # ── verbatim from trust_check ──
                if fh > elapsed:
                    continue
                hs = nn._node_value(cyc, "swh", fh, i, j)
                if hs is None:
                    continue
                valid = int((cyc["cycle_dt"] + datetime.timedelta(hours=fh)).timestamp() // 3600)
                if valid in s and s[valid]["lead"] <= fh:
                    continue
                s[valid] = {"hs": hs, "dir": nn._node_value(cyc, "dirpw", fh, i, j),
                            "shts": nn._node_value(cyc, "shts", fh, i, j), "lead": fh}
    return series, cells


def ab_for_buoy(wfo, buoy, n_cycles=4, *, _cycles=None, _buoy=None, _now=None, _latlng=None):
    """Run the A/B for one buoy. Returns a dict with a row per cell, or an
    'unavailable' note (buoy non-reporting / unknown) — never fabricated numbers.
    _cycles/_buoy/_now/_latlng are injectable for the offline selftest."""
    if _latlng is not None:
        blat, blng = _latlng
    else:
        try:
            blat, blng = nn._buoy_latlng(buoy)
        except Exception as e:  # noqa: BLE001
            return {"wfo": wfo, "buoy": buoy, "unavailable": f"coords unresolved ({type(e).__name__})"}
    buoyobs = _buoy if _buoy is not None else nn._buoy_hourly(buoy)
    if not buoyobs:
        return {"wfo": wfo, "buoy": buoy, "unavailable": "buoy feed unavailable (non-reporting?)"}
    series, cells = assemble_ab(wfo, blat, blng, n_cycles, _cycles=_cycles, _now=_now)
    rows = {}
    for key in ("plain", "seaward"):
        cell = cells[key]
        if cell is None:
            rows[key] = None
            continue
        v, r, cs, n, reason = nn.trust_verdict(nn._pair_samples(series[key], buoyobs))
        rows[key] = {"dist_km": cell[4], "lat": cell[2], "lng": cell[3],
                     "r": r, "circ_std": cs, "pairs": n, "verdict": v, "reason": reason,
                     "series": series[key]}
    return {"wfo": wfo, "buoy": buoy, "rows": rows}


def classify(rows):
    """Honest three-way read (plus two in-between labels). Returns (label, sentence)."""
    p, s = rows.get("plain"), rows.get("seaward")
    if not p:
        return "NO_CONTROL", "plain-nearest produced no series — can't A/B."
    if not s:
        return "NO_DIVERGENCE", ("no distinct seaward cell this cycle — plain already seaward; "
                                 "node cannot be the variable here.")
    fin = lambda x: isinstance(x, (int, float)) and x == x  # not NaN
    dcs = (p["circ_std"] - s["circ_std"]) if fin(p["circ_std"]) and fin(s["circ_std"]) else float("nan")
    dr = (s["r"] - p["r"]) if fin(p["r"]) and fin(s["r"]) else float("nan")
    if "INCONCLUSIVE" in (p["verdict"], s["verdict"]):
        return "INCONCLUSIVE", "one/both sides INCONCLUSIVE (too few pairs / flat sea) — rerun on a real swell."
    if p["verdict"] == "FAIL" and s["verdict"] == "PASS":
        return ("NODE_IS_CULPRIT",
                f"seaward PASSes where plain FAILs (Δcirc_std {dcs:+.1f}°, Δr {dr:+.3f}) → node selection "
                "IS the driver; the guarded-seaward fix is justified and the live zones can be re-verified.")
    if p["verdict"] == "FAIL" and s["verdict"] == "FAIL":
        if fin(dcs) and dcs >= 8.0:
            return ("NODE_HELPS_INSUFFICIENT",
                    f"seaward improves circ_std by {dcs:.1f}° but still FAILs → node is part of it, but "
                    "not the whole story; investigate the residual before trusting.")
        return ("BOTH_FAIL_BAD_PAIRING",
                f"both cells FAIL and seaward barely moves it (Δcirc_std {dcs:+.1f}°) → the buoy↔zone "
                "PAIRING is bad, not the node. Those live spots were shipped on a bad match — consider UNTAGGING.")
    if p["verdict"] == "PASS" and s["verdict"] == "FAIL":
        # The live zones PASSed at plain (that's why they're tagged). If the SEAWARD
        # cell we actually ship (select_node) FAILs, we validated a cell we don't serve
        # and the served cell disagrees with the buoy — the more worrying direction.
        return ("SHIPPED_NODE_FAILS",
                f"the gate PASSed at the plain (shoreward) cell, but the SEAWARD cell we actually SHIP "
                f"FAILs (Δcirc_std {dcs:+.1f}°, Δr {dr:+.3f}) → the zone was trusted on a cell we don't "
                "serve, and the served cell disagrees with the buoy. UNTAG territory.")
    if p["verdict"] == "PASS" and s["verdict"] == "PASS":
        if fin(dcs) and abs(dcs) < 3.0 and fin(dr) and abs(dr) < 0.05:
            return ("NODE_NOT_THE_DRIVER",
                    f"both cells PASS and the node barely moves r/circ_std (Δcirc_std {dcs:+.1f}°, "
                    f"Δr {dr:+.3f}) → node selection is NOT what drives these verdicts; the PASS stands "
                    "on its own. If a zone still looks wrong, the culprit is elsewhere (MWD outliers / height bias).")
        return ("PASS_BOTH_NODE_MOVES",
                f"both cells PASS but the node shifts the numbers (Δcirc_std {dcs:+.1f}°, Δr {dr:+.3f}) → "
                "benign here; the seaward (shipped) cell also trusts the model.")
    return ("MIXED", f"plain={p['verdict']} seaward={s['verdict']} Δcirc_std {dcs:+.1f}° Δr {dr:+.3f} "
                     "— read the row; no clean bucket.")


def _fmt(v, spec="{:.3f}", dash="  —  "):
    return spec.format(v) if isinstance(v, (int, float)) and v == v else dash


def _print_buoy(res, verbose=False):
    print(f"\nbuoy {res['buoy']}  (wfo {res['wfo']})")
    if res.get("unavailable"):
        print(f"  ⚠ {res['unavailable']} — skipped (not fabricated).")
        return None
    print(f"  {'node':<8} {'d_km':>5} {'r':>7} {'circ_std':>9} {'pairs':>6}  verdict")
    for key in ("plain", "seaward"):
        row = res["rows"].get(key)
        if row is None:
            print(f"  {key:<8} {'—':>5} {'—':>7} {'—':>9} {'—':>6}  (no distinct cell)")
            continue
        print(f"  {key:<8} {_fmt(row['dist_km'],'{:.2f}'):>5} {_fmt(row['r']):>7} "
              f"{_fmt(row['circ_std'],'{:.1f}°'):>9} {row['pairs']:>6}  {row['verdict']}"
              + (f"  ({row['reason']})" if row.get("reason") else ""))
    label, sentence = classify(res["rows"])
    print(f"  → {label}: {sentence}")
    if verbose and res["rows"].get("plain") and res["rows"].get("seaward"):
        _print_perhour(res["rows"])
    return label


def _print_perhour(rows):
    """Per-hour dirpw−MWD at both cells (the scatter that drives circ_std)."""
    ps, ss = rows["plain"]["series"], rows["seaward"]["series"]
    print(f"    {'valid_hr':>10} {'plain Δdir':>11} {'seaward Δdir':>13}")
    for t in sorted(set(ps) | set(ss)):
        pv = ps.get(t, {}).get("dir")
        sv = ss.get(t, {}).get("dir")
        print(f"    {t:>10} {_fmt(pv,'{:.0f}°'):>11} {_fmt(sv,'{:.0f}°'):>13}")


def run(buoys, n_cycles=4, verbose=False):
    print("=== NWPS trust gate — plain vs seaward node A/B (READ-ONLY; needs NOMADS+NDBC) ===")
    print("Same cycles/hours/pairing/verdict math; the ONLY variable is which (i,j) is sampled.")
    print("'plain' = the gate's current control and should reproduce the production verdict.")
    labels = []
    for wfo, buoy in buoys:
        try:
            res = ab_for_buoy(wfo, buoy, n_cycles)
        except Exception as e:  # noqa: BLE001 — degrade per buoy, keep going
            print(f"\nbuoy {buoy}  (wfo {wfo})\n  ⚠ skipped ({type(e).__name__}: {e}) — run on the Mac.")
            labels.append((wfo, buoy, "SKIPPED"))
            continue
        labels.append((wfo, buoy, _print_buoy(res, verbose=verbose) or "UNAVAILABLE"))

    print("\n==== overall read ====")
    for wfo, buoy, lab in labels:
        print(f"  {wfo}/{buoy}: {lab}")
    pick = lambda lab: [f"{w}/{b}" for w, b, l in labels if l == lab]
    culprit, bad_pair, not_drv = pick("NODE_IS_CULPRIT"), pick("BOTH_FAIL_BAD_PAIRING"), pick("NODE_NOT_THE_DRIVER")
    shipped_fail = pick("SHIPPED_NODE_FAILS")
    if culprit:
        print(f"  → NODE IS THE CULPRIT for: {', '.join(culprit)} — guarded-seaward fix justified; re-verify these zones.")
    if shipped_fail:
        print(f"  → SHIPPED NODE FAILS for: {', '.join(shipped_fail)} — gate passed the plain cell but the "
              "seaward cell we serve disagrees; UNTAG territory.")
    if bad_pair:
        print(f"  → BAD PAIRING (not the node) for: {', '.join(bad_pair)} — shipped on a bad buoy match; consider UNTAGGING.")
    if not_drv:
        print(f"  → NODE NOT THE DRIVER for: {', '.join(not_drv)} — we've been chasing the wrong thing here.")
    print("\n(Read-only: default node selection unchanged; nothing tagged; spots_enriched.json untouched.)")
    return 0


def _parse_pairs(spec):
    out = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" not in tok:
            raise SystemExit(f"--buoys token {tok!r} must be wfo:buoy (e.g. box:44097)")
        w, b = tok.split(":", 1)
        out.append((w.strip().lower(), b.strip()))
    return out


def _selftest():
    """OFFLINE — proves the A/B wiring on synthetic cycles (no NOMADS): two cells
    with different dirpw yield two independent verdicts, and classify() buckets the
    three outcomes. Also demonstrates the caveat: a CONSTANT seaward offset PASSes
    (circ_std insensitive to mean), a SCATTERED plain cell FAILs."""
    import numpy as np
    ok = True

    def check(msg, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("  PASS " if cond else "  FAIL ") + msg)

    # classify() unit cases (pure)
    def _row(v, r, cs):
        return {"dist_km": 1, "lat": 0, "lng": 0, "r": r, "circ_std": cs, "pairs": 20, "verdict": v}
    check("classify: plain FAIL + seaward PASS → NODE_IS_CULPRIT",
          classify({"plain": _row("FAIL", 0.9, 40), "seaward": _row("PASS", 0.9, 5)})[0] == "NODE_IS_CULPRIT")
    check("classify: both FAIL, seaward flat → BOTH_FAIL_BAD_PAIRING",
          classify({"plain": _row("FAIL", 0.4, 45), "seaward": _row("FAIL", 0.4, 44)})[0] == "BOTH_FAIL_BAD_PAIRING")
    check("classify: both FAIL, seaward much better → NODE_HELPS_INSUFFICIENT",
          classify({"plain": _row("FAIL", 0.9, 45), "seaward": _row("FAIL", 0.9, 33)})[0] == "NODE_HELPS_INSUFFICIENT")
    check("classify: both PASS, ~no move → NODE_NOT_THE_DRIVER",
          classify({"plain": _row("PASS", 0.9, 10), "seaward": _row("PASS", 0.9, 11)})[0] == "NODE_NOT_THE_DRIVER")
    check("classify: plain PASS + seaward FAIL → SHIPPED_NODE_FAILS (live-zone worry)",
          classify({"plain": _row("PASS", 0.9, 12), "seaward": _row("FAIL", 0.9, 40)})[0] == "SHIPPED_NODE_FAILS")
    check("classify: no distinct seaward → NO_DIVERGENCE",
          classify({"plain": _row("PASS", 0.9, 10), "seaward": None})[0] == "NO_DIVERGENCE")

    # end-to-end on a synthetic cycle: land north (row0), plain cell shoreward (row1)
    # with SCATTERED dir, seaward cell (row2) with a CONSTANT +15° offset.
    lat = np.array([[40.030], [40.008], [39.980], [39.960]])
    lng = np.array([[-73.0], [-73.0], [-73.0], [-73.0]])
    mask = np.array([[True], [False], [False], [False]])
    cdt = datetime.datetime(2026, 7, 1, 0, tzinfo=datetime.timezone.utc)
    steps = list(range(0, 48, 3))                      # 16 hours
    fields = {}
    for k, fh in enumerate(steps):
        swh = np.full((4, 1), np.nan, dtype="float32")
        dpw = np.full((4, 1), np.nan, dtype="float32")
        hs = 0.6 + 0.05 * k                            # ramps → height range ≥ 0.5 m, r≈1
        swh[1, 0] = swh[2, 0] = hs
        dpw[1, 0] = 110.0 if k % 2 else 190.0          # plain: ±40° scatter about 150
        dpw[2, 0] = 165.0                              # seaward: constant +15° offset
        fields[("swh", fh)] = swh
        fields[("dirpw", fh)] = dpw
        fields[("shts", fh)] = swh
    cyc = {"lats": lat, "lons": lng, "mask": mask, "cycle_dt": cdt, "steps": steps, "fields": fields}
    now = cdt + datetime.timedelta(hours=48)           # all steps elapsed
    buoy = {int((cdt + datetime.timedelta(hours=fh)).timestamp() // 3600):
            {"hs": 0.6 + 0.05 * k, "mwd": 150.0, "swell_dir": 150.0, "swell_hs": 0.6 + 0.05 * k}
            for k, fh in enumerate(steps)}
    res = ab_for_buoy("test", "SYNTH", _cycles=[cyc], _buoy=buoy, _now=now, _latlng=(40.000, -73.000))
    rp, rs = res["rows"]["plain"], res["rows"]["seaward"]
    check(f"end-to-end: two distinct cells sampled (plain {rp['dist_km']:.2f} km, seaward {rs['dist_km']:.2f} km)",
          rp["dist_km"] < rs["dist_km"])
    check(f"end-to-end: plain FAILs on scatter (circ_std {rp['circ_std']:.1f}°)",
          rp["verdict"] == "FAIL" and rp["circ_std"] > 25)
    check(f"end-to-end: seaward PASSes on constant offset (circ_std {rs['circ_std']:.1f}° — caveat shown)",
          rs["verdict"] == "PASS" and rs["circ_std"] < 25)
    check("end-to-end: same pair count both cells (only (i,j) differed)", rp["pairs"] == rs["pairs"])
    check("end-to-end: classify → NODE_IS_CULPRIT", classify(res["rows"])[0] == "NODE_IS_CULPRIT")

    print("\nself-test:", "ALL PASS — A/B wiring sound (offline)." if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--buoys", default=None, help="wfo:buoy,... (default = the 4 audit-divergent buoys)")
    ap.add_argument("--cycles", type=int, default=4, help="recent NWPS cycles to assemble (default 4)")
    ap.add_argument("--verbose", action="store_true", help="print per-hour dirpw−MWD at both cells")
    ap.add_argument("--selftest", action="store_true", help="offline wiring check (no NOMADS)")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(_parse_pairs(a.buoys) or _DIVERGENT, n_cycles=a.cycles, verbose=a.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
