"""Before-and-after report for a swell-window recompute: two spots files, per spot and in total.

    python -m pipeline.swell_window_report --before OLD.json --after NEW.json \\
        [--md REPORT.md] [--csv PER_SPOT.csv] [--facing-outside]

Read-only: it reads the two files (and the committed description cache) and writes only the
report files named on the command line. --facing-outside prints, comma-separated, the slugs
of the AFTER spots whose facing direction lies outside every arc — the list the sw1-raycast
workflow feeds to --debug-blockers.

Per spot: arcs, total window width, optimal, and whether the facing (orientation_deg) is
inside a window. Totals, before and after:
  * windows opening more than 90 (and 135) degrees from the facing,
  * optimals more than 90 degrees from the facing,
  * spots whose facing is outside every arc,
  * NWPS trust tiers (nwps_nearshore._spot_tier),
  * descriptions the next db_import would flag for a rewrite: the stored v2 signature in
    pipeline/data/spot_descriptions.json no longer matches the spot's payload.
The regression check lists every Hawaii and Puerto Rico spot whose open window WITHIN 90
degrees of its facing got narrower, with the degrees lost. Membership is
interpret.in_any_arc, sampled every half degree.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter

from .db_import import _slugify
from .descriptions import description_payload, description_signature, is_v2, spot_fields
from .forecast.nwps_nearshore import _spot_tier
from .generate_descriptions import OUTPUT_FILE as DESCRIPTION_CACHE
from .interpret import in_any_arc

STEP = 0.5
SAMPLES = [i * STEP for i in range(int(360 / STEP))]
REGRESSION_STATES = ("Hawaii", "Puerto Rico")


def off(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def arcs_text(arcs):
    return " ".join(f"[{a['min']:g}-{a['max']:g}]" for a in arcs or []) or "-"


def width(arcs):
    return sum(a.get("span", 0) for a in arcs or [])


def measure(spot):
    """The numbers the report reads for one spot."""
    arcs, o, opt = spot.get("swell_window_arcs") or [], spot.get("orientation_deg"), spot.get("optimal_swell_dir")
    open_b = [b for b in SAMPLES if in_any_arc(b, arcs)]
    worst = max((off(b, o) for b in open_b), default=0.0) if o is not None else None
    front = sum(STEP for b in open_b if o is not None and off(b, o) <= 90)
    return {
        "arcs": arcs_text(arcs),
        "width": width(arcs),
        "optimal": opt,
        "facing": o,
        "facing_in_window": o is not None and in_any_arc(o, arcs),
        "opens_past_90": worst is not None and worst > 90,
        "opens_past_135": worst is not None and worst > 135,
        "optimal_past_90": o is not None and opt is not None and off(o, opt) > 90,
        "open_within_90": front,
        "tier": _spot_tier(spot),
    }


def stale_descriptions(spots, cache):
    """Slugs whose stored v2 signature no longer matches the spot's payload."""
    out = []
    for s in spots:
        slug = _slugify(s.get("name"))
        rec = cache.get(slug) or {}
        sig = rec.get("signature")
        if is_v2(sig) and sig != description_signature(description_payload(spot_fields(s))):
            out.append(slug)
    return sorted(out)


def build(before, after, cache):
    b_by, a_by = {s["name"]: s for s in before}, {s["name"]: s for s in after}
    names = [n for n in a_by if n in b_by and a_by[n].get("is_valid_surf_spot") is not False]
    rows = []
    for n in names:
        mb, ma = measure(b_by[n]), measure(a_by[n])
        rows.append({"slug": _slugify(n), "name": n, "state": a_by[n].get("region_hint"),
                     "before": mb, "after": ma})
    tot = {}
    for side in ("before", "after"):
        tot[side] = {
            "windows opening >90 deg from facing": sum(r[side]["opens_past_90"] for r in rows),
            "windows opening >135 deg from facing": sum(r[side]["opens_past_135"] for r in rows),
            "optimal >90 deg from facing": sum(r[side]["optimal_past_90"] for r in rows),
            "facing outside every arc": sum(not r[side]["facing_in_window"] for r in rows),
            "tiers": dict(sorted(Counter(r[side]["tier"] for r in rows).items())),
        }
    regress = sorted(
        ({"slug": r["slug"], "state": r["state"],
          "lost": r["before"]["open_within_90"] - r["after"]["open_within_90"]}
         for r in rows if r["state"] in REGRESSION_STATES
         and r["after"]["open_within_90"] < r["before"]["open_within_90"]),
        key=lambda x: (-x["lost"], x["slug"]))
    stale_b = set(stale_descriptions([b_by[n] for n in names], cache))
    stale_a = stale_descriptions([a_by[n] for n in names], cache)
    return {"rows": rows, "totals": tot, "regression": regress,
            "stale_descriptions": stale_a,
            "stale_descriptions_new": sorted(set(stale_a) - stale_b),
            "tier_moves": dict(sorted(Counter((r["before"]["tier"], r["after"]["tier"])
                                              for r in rows).items()))}


def facing_outside(rep):
    return [r["slug"] for r in rep["rows"] if not r["after"]["facing_in_window"]]


def write_csv(rep, path):
    cols = ("arcs", "width", "optimal", "facing_in_window")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["slug", "state", "facing"] + [f"{c}_before" for c in cols]
                   + [f"{c}_after" for c in cols])
        for r in rep["rows"]:
            w.writerow([r["slug"], r["state"], r["after"]["facing"]]
                       + [r["before"][c] for c in cols] + [r["after"][c] for c in cols])


def markdown(rep):
    t = rep["totals"]
    out = ["| | before | after |", "|---|---|---|"]
    for k in t["before"]:
        if k != "tiers":
            out.append(f"| {k} | {t['before'][k]} | {t['after'][k]} |")
    for tier in sorted(set(t["before"]["tiers"]) | set(t["after"]["tiers"])):
        out.append(f"| tier: {tier} | {t['before']['tiers'].get(tier, 0)} | "
                   f"{t['after']['tiers'].get(tier, 0)} |")
    out += ["", "Tier moves (before -> after): "
            + ", ".join(f"{a}->{b} {n}" for (a, b), n in rep["tier_moves"].items()), "",
            f"Hawaii / Puerto Rico spots whose window within 90 deg of facing got narrower: "
            f"{len(rep['regression'])}", ""]
    if rep["regression"]:
        out += ["| spot | state | degrees lost |", "|---|---|---|"]
        out += [f"| {x['slug']} | {x['state']} | {x['lost']:g} |" for x in rep["regression"]]
    out += ["", f"Descriptions the next db_import flags for a rewrite: "
                f"{len(rep['stale_descriptions'])} "
                f"({len(rep['stale_descriptions_new'])} of them because of this change)", "",
            "| spot | state | facing | arcs before | arcs after | width | optimal | facing in window |",
            "|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        b, a = r["before"], r["after"]
        out.append(f"| {r['slug']} | {r['state']} | {a['facing']:g} | {b['arcs']} | {a['arcs']} | "
                   f"{b['width']:g} -> {a['width']:g} | {b['optimal']} -> {a['optimal']} | "
                   f"{'yes' if b['facing_in_window'] else 'no'} -> "
                   f"{'yes' if a['facing_in_window'] else 'no'} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--md")
    p.add_argument("--csv")
    p.add_argument("--facing-outside", action="store_true")
    args = p.parse_args(argv)
    cache = json.load(open(DESCRIPTION_CACHE))
    rep = build(json.load(open(args.before)), json.load(open(args.after)), cache)
    if args.facing_outside:
        print(",".join(facing_outside(rep)))
        return 0
    if args.md:
        open(args.md, "w").write(markdown(rep))
    if args.csv:
        write_csv(rep, args.csv)
    print(json.dumps({"totals": rep["totals"], "regression": len(rep["regression"]),
                      "stale_descriptions": len(rep["stale_descriptions"]),
                      "stale_descriptions_new": len(rep["stale_descriptions_new"])}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
