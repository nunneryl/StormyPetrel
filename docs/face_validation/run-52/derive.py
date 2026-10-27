"""Rebuild derived.json from run 52's own files (python3 docs/face_validation/run-52/derive.py).

From face_compare_srf.csv.gz, headline hours outside Hawaii: the share of hours inside the
NWS range for typical scaled by k (inside when k x typical rounds to a whole foot within the
range, as face_compare.classify), the k with the most hours inside, the median of the range's
middle over typical, and the k at which as many hours sit above the range as below; by region,
office and spot type; and the inside / below / above shares for the published face and for
typical x 0.75, 0.8 and 0.9 by region. From face_compare_frf.csv: the lidar's breaking Hs over
the FRF 8 m array's Hs (both in feet) on the small-day hours, and the days they fall on.
"""
import csv
import gzip
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
KS = [0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.1, 1.2]


def side(v, lo, hi):
    return "below" if v < lo - 0.5 else ("above" if v >= hi + 0.5 else "inside")


def scaling(rs):
    t = [(float(r["typical"]), float(r["lo"]), float(r["hi"])) for r in rs]
    if len(t) < 200:
        return {"n": len(t)}
    inside = {k: round(sum(side(k * a, lo, hi) == "inside" for a, lo, hi in t) / len(t), 3)
              for k in KS}
    best = max(inside, key=inside.get)

    def excess(k):          # hours above minus hours below
        s = [side(k * a, lo, hi) for a, lo, hi in t]
        return s.count("above") - s.count("below")

    a, b = 0.3, 2.0
    for _ in range(30):
        m = (a + b) / 2
        a, b = (a, m) if excess(m) > 0 else (m, b)
    return {"n": len(t), "inside_by_k": inside, "best_k": best,
            "median_mid_over_typical": round(st.median((lo + hi) / 2 / a_ for a_, lo, hi in t
                                                      if a_ > 0), 3),
            "balanced_k": round(a, 2)}


def shares(rs, f):
    s = [side(f(r), float(r["lo"]), float(r["hi"])) for r in rs]
    return {k: round(s.count(k) / len(s), 3) for k in ("inside", "below", "above")}


rows = [r for r in csv.DictReader(gzip.open(HERE / "face_compare_srf.csv.gz", "rt"))
        if r["group"] == "headline" and r["office"] != "HFO" and r["typical"]]
out = {"srf_rows": len(rows), "scaling": {"all": scaling(rows)}, "sides_by_region": {}}
for key in ("region", "office", "type"):
    g = defaultdict(list)
    for r in rows:
        g[r[key]].append(r)
    out["scaling"][key] = {k: scaling(v) for k, v in sorted(g.items())}
    if key == "region":
        g["all"] = rows
        for k, v in sorted(g.items()):
            out["sides_by_region"][k] = {
                "published": shares(v, lambda r: float(r["published"])),
                **{f"typical x{m}": shares(v, lambda r, m=m: m * float(r["typical"]))
                   for m in (0.75, 0.8, 0.9)}}
frf = list(csv.DictReader(open(HERE / "face_compare_frf.csv")))
out["frf_small_days"] = {
    "hours": len(frf), "days": sorted({r["hour"][:10] for r in frf}),
    "median_lidar_over_8m_hs": round(st.median(float(r["observed_ft"]) /
                                               (float(r["frf8_hs"]) * 3.281) for r in frf), 3),
    "median_typical_from_8m_over_lidar": round(st.median(float(r["frf8_typical"]) /
                                                         float(r["observed_ft"]) for r in frf), 3),
    "frf8_tp_range_s": [min(float(r["frf8_tp"]) for r in frf), max(float(r["frf8_tp"]) for r in frf)],
    "frf8_hs_range_m": [min(float(r["frf8_hs"]) for r in frf), max(float(r["frf8_hs"]) for r in frf)]}
(HERE / "derived.json").write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out["scaling"]["all"]), json.dumps(out["frf_small_days"]))
