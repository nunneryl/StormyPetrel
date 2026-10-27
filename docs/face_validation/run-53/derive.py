"""Rebuild derived.json from run 53's own SRF pairs (python3 docs/face_validation/run-53/derive.py).

Headline hours outside Hawaii (group 'headline', office not HFO): the inside / below / above
shares of the NWS Surf Zone Forecast range, by region and overall, for the published face and
for typical scaled by one flat k for every period band (0.66, 0.70, 0.75, 0.79): 0.79 is run 53's
capped flat fit (at most 10% of all hours above), 0.66 its strict flat fit (at most 10% above in
every region), 0.70 the FRF Duck small-day figure (run 52: lidar breaking Hs over typical from the
8 m array, 1 / 1.422), 0.75 between. A face is inside lo..hi when lo - 0.5 <= face < hi + 0.5
(face_compare.classify).
"""
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
KS = (0.66, 0.70, 0.75, 0.79)


def side(v, lo, hi):
    return "below" if v < lo - 0.5 else ("above" if v >= hi + 0.5 else "inside")


def shares(rs, f):
    s = [side(f(r), float(r["lo"]), float(r["hi"])) for r in rs]
    return {k: round(s.count(k) / len(s), 3) for k in ("inside", "below", "above")}


rows = [r for r in csv.DictReader(gzip.open(HERE / "face_compare_srf.csv.gz", "rt"))
        if r["group"] == "headline" and r["office"] != "HFO" and r["typical"]]
groups = defaultdict(list)
for r in rows:
    groups[r["region"]].append(r)
groups["all"] = rows
out = {"srf_rows": len(rows), "flat_k": {}}
for g, rs in sorted(groups.items()):
    out["flat_k"][g] = {"n": len(rs), "published": shares(rs, lambda r: float(r["published"])),
                        **{f"k {k}": shares(rs, lambda r, k=k: k * float(r["typical"]))
                           for k in KS}}
(HERE / "derived.json").write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out["flat_k"]["all"]))
