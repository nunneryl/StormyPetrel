"""Apply a buoy-assign report's choices to the roster, for the spots whose buoy is silent, past
its cap, or missing. Writes pipeline/spots_enriched.json only with --apply.

    python -m pipeline.apply_buoy_assign [--report R.json[.gz]] [--list fresh+7d] [--apply]

WHICH SPOTS. sw1-raycast run 74 (buoy-assign, spots = all; docs/face_validation/run-74) ran
enrich's buoy rule over every roster spot against NDBC's fresh station lists plus the 7-day
rule. Approved on 2026-11-12, the rule's choice is taken where the spot's current buoy fails the
rule for a reason that is not in doubt:

  gain      the spot has no buoy and the rule picks one;
  silent    its buoy is not a wave station on the list (no wave height in the past 7 days);
  past cap  its buoy is beyond the region's cap (buoys._regional_cap_km).

The choice may be no buoy: then the spot's buoy is cleared and its page says "No nearby buoy"
instead of pointing at a silent one (the five Hawaii spots on 51205 and 51208, and Siesta Key).
NOT TAKEN, held for review: a working buoy within the cap that the rule finds blocked by land
(62 spots, some of them suspect: Richardson Beach against 51206 at 6.7 km), and a working buoy
with a nearer one passing (97 spots), which also feeds the rating's swell-direction fallback.

WHAT IT WRITES, as enrich does (enrichment/buoys.compute_nearest_buoy): nearest_buoy_id,
nearest_buoy_dist_km (from the rule's point, 2 decimals) and fallback_buoy_ids (the next up to
three that pass); None, None and [] when the choice is none. Where the spot carries
enrichment_confidence, its nearest_buoy becomes the rule's confidence: 1.0 within half the cap,
0.75 beyond, 0.0 with no buoy (enrich.py writes the same).
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "face_validation" / "run-74" / "buoy_assign.json.gz"
ENRICHED = ROOT / "pipeline" / "spots_enriched.json"
LIST = "fresh+7d"


def load_report(path=None):
    path = Path(path or REPORT)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def why(row):
    """'gain', 'silent' or 'past cap' when the report row is one to apply, else None."""
    if row["outcome"] == "gain":
        return "gain"
    if row["outcome"] not in ("change", "loss"):
        return None
    cur = row.get("current") or {}
    if not cur.get("is_wave_station"):
        return "silent"
    if not cur.get("within_cap"):
        return "past cap"
    return None


def fields(row):
    """The roster fields for the rule's choice in *row*."""
    if not row.get("choice"):
        return {"nearest_buoy_id": None, "nearest_buoy_dist_km": None, "fallback_buoy_ids": []}
    return {"nearest_buoy_id": row["choice"],
            "nearest_buoy_dist_km": round(float(row["choice_km"]), 2),
            "fallback_buoy_ids": list(row.get("fallbacks") or [])}


def confidence(row):
    """compute_nearest_buoy's buoy_confidence for *row*'s choice."""
    if not row.get("choice"):
        return 0.0
    return 1.0 if float(row["choice_km"]) <= float(row["cap_km"]) * 0.5 else 0.75


def plan(report, label=LIST):
    """[(name, why, fields, confidence)] for every row to apply, in report order."""
    return [(r["name"], why(r), fields(r), confidence(r))
            for r in report["lists"][label]["spots"] if why(r)]


def apply(enriched, rows):
    """Write each row's fields onto the roster spot of that name. Returns the count written."""
    by_name = {s["name"]: s for s in enriched}
    missing = [n for n, *_ in rows if n not in by_name]
    if missing:
        raise KeyError(f"not on the roster: {missing}")
    for name, _, f, conf in rows:
        spot = by_name[name]
        spot.update(f)
        if isinstance(spot.get("enrichment_confidence"), dict):
            spot["enrichment_confidence"]["nearest_buoy"] = conf
    return len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--list", default=LIST)
    ap.add_argument("--apply", action="store_true", help="write pipeline/spots_enriched.json")
    a = ap.parse_args(argv)
    rows = plan(load_report(a.report), a.list)
    enriched = json.loads(ENRICHED.read_text(encoding="utf-8"))
    current = {s["name"]: s for s in enriched}
    for name, reason, f, _conf in rows:
        s = current[name]
        print(f"{reason:8s} {name}: {s.get('nearest_buoy_id')} {s.get('nearest_buoy_dist_km')} -> "
              f"{f['nearest_buoy_id']} {f['nearest_buoy_dist_km']} (fallbacks {f['fallback_buoy_ids']})")
    print(f"{len(rows)} spots")
    if a.apply:
        apply(enriched, rows)
        ENRICHED.write_text(json.dumps(enriched, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {ENRICHED}")


if __name__ == "__main__":
    main()
