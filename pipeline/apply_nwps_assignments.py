#!/usr/bin/env python3
"""Stage 2 (NWPS OKX pilot) Part A — bake validated NWPS assignments into
spots_enriched.json. Sibling of apply_mop_assignments.py.

For each placement-OK spot from the pilot run (scripts/nwps_okx_assignments.json,
written by `pipeline.forecast.nwps_nearshore --validate`) — and ONLY once the
WFO's buoy trust gate has PASSED — write, IN PLACE and only for those spots:
    swell_window_source   = "nwps"      (provenance tag — drives apply_nwps_overrides)
    nwps_wfo, nwps_grid="CG1"
    nwps_node_lat, nwps_node_lng        (the seaward node the rater samples)
    nwps_node_distance_m                (spot → node distance)
    nwps_buoy_id                        (the buoy that anchored the trust check)

    nwps_direction_status = "verified" | "pending" | "unverifiable"   (see below)

Every other spot is left exactly as-is — a surgical patch like apply_mop_assignments.
DRY RUN by default; --apply to write. A spot is PLACED (height-live) if ANY of:
  (c) its slug is in buoy_reference.unverifiable[] -> direction_status "unverifiable"
      (island-shadowed: NWPS HEIGHT now, NO trust PASS, and direction can NEVER be
      buoy-verified — the nearest valid buoy is around a headland in different exposure).
      Checked BEFORE (a) so a PASS buoy can never relabel it "verified"; the row's
      nwps_buoy_id is nulled (B2 — no untrusted buoy id on the row).
  (a) its nwps_buoy_id is "PASS" in trust_by_buoy -> "verified" (height AND direction
      buoy-verified) — UNLESS that buoy is retired on the "direction" axis in
      buoy_reference.retired[], in which case the PASS is a HEIGHT verification only and
      direction_status is "unverifiable" (the 44098 case: no valid reference to verify
      direction against, so it is never labeled "verified").
  (b) its (nwps_wfo, nwps_buoy_id) is in buoy_reference.pending[] -> "pending" (OPTION-B:
      NWPS HEIGHT now, direction NOT yet buoy-verified, awaiting a swell + --trustcheck).
HEIGHT placement is identical for (a)/(b)/(c). A buoy in buoy_reference.retired[] never
auto-places via the pending path. None of the above -> HELD. --force places held spots
anyway (testing only). db_import folds nwps_direction_status into
data_sources.nwps_direction_status so a later reader can tell buoy-verified direction from
still-pending from never-verifiable.

    python -m pipeline.apply_nwps_assignments               # dry run (diff)
    python -m pipeline.apply_nwps_assignments --apply        # write (PASS or pending spots)

Read-only on prod until --apply; does not touch the DB. Propagate after: next
`pipeline.interpret` picks up nwps spots automatically (additive), then db_import.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
ENRICHED = _ROOT / "pipeline" / "spots_enriched.json"
ASSIGNMENTS = _ROOT / "scripts" / "nwps_okx_assignments.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FIELDS = ("nwps_wfo", "nwps_grid", "nwps_node_lat", "nwps_node_lng",
           "nwps_node_distance_m", "nwps_buoy_id")


def _slug(name):
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


def build_plan(force=False, doc=None, enriched=None):
    """(rows, problems, held, trust_by_buoy, enriched, by_slug). A spot is PLACED
    (swell_window_source=nwps + the NWPS node fields) if ANY of:
      (c) its slug is in buoy_reference.unverifiable[] -> direction_status "unverifiable"
          (island-shadowed: height-live NOW, direction can NEVER be buoy-verified). CHECKED
          FIRST — before PASS — so a PASS buoy can never relabel it "verified"; the row's
          nwps_buoy_id is set to None (B2: no untrusted buoy id on the row).
      (a) trust_by_buoy[its nwps_buoy_id] == "PASS"  -> "verified" (height-live AND direction
          buoy-verified), UNLESS the buoy is retired on the "direction" axis in
          buoy_reference.retired[] -> "unverifiable" (44098: the PASS verifies HEIGHT only;
          there is no valid reference to verify direction, so it is never "verified").
      (b) its (nwps_wfo, nwps_buoy_id) is listed in buoy_reference.pending[]
          -> "pending" (height-live NOW; direction NOT yet buoy-verified — OPTION-B).
    HEIGHT placement is identical for all three. Node coords are REQUIRED. A buoy in
    buoy_reference.retired[] is NEVER auto-placed via the pending path (retired = no valid
    buoy; those spots place only via trust_by_buoy PASS, the 44098 handling). held = spots
    that hit none of the above; problems = spots that can't be matched. *doc* / *enriched*
    are injectable for tests (default: read the on-disk files). Raises ValueError on an
    old-format (global "trust") file — the per-buoy format is required."""
    doc = json.loads(ASSIGNMENTS.read_text()) if doc is None else doc
    if "trust_by_buoy" not in doc:
        raise ValueError(
            f"{ASSIGNMENTS.name} is old-format: top-level 'trust'={doc.get('trust')!r} "
            "but no 'trust_by_buoy' map. The per-buoy format is required — migrate to "
            "trust_by_buoy {buoy_id: verdict}. Refusing to tag on the legacy global flag.")
    trust_by_buoy = doc.get("trust_by_buoy") or {}
    ref = doc.get("buoy_reference") or {}
    pending_set = {(p.get("wfo"), str(p.get("buoy"))) for p in (ref.get("pending") or [])
                   if p.get("wfo") and p.get("buoy") is not None}
    retired_set = {(r.get("wfo"), str(r.get("buoy"))) for r in (ref.get("retired") or [])
                   if r.get("wfo") and r.get("buoy") is not None}
    # retired records whose 'axes' include "direction": a PASS buoy here verifies HEIGHT only,
    # so its spots must read "unverifiable", never "verified" (the 44098 fix). 'axes' is now
    # load-bearing — a height-only retirement does NOT relabel direction.
    retired_direction_set = {(r.get("wfo"), str(r.get("buoy"))) for r in (ref.get("retired") or [])
                             if r.get("wfo") and r.get("buoy") is not None
                             and "direction" in (r.get("axes") or [])}
    # unverifiable[] is keyed by SLUG (B2): island-shadowed spots placed on NWPS HEIGHT with NO
    # trust PASS and direction that can never be buoy-verified. The far 'nearest_candidate' in
    # each record is audit-only and is NEVER read here.
    unverifiable_slugs = set()
    for u in (ref.get("unverifiable") or []):
        unverifiable_slugs.update(u.get("slugs") or [])
    enriched = json.loads(ENRICHED.read_text()) if enriched is None else enriched
    by_slug = {}
    for s in enriched:
        by_slug.setdefault(_slug(s.get("name")), s)
    rows, problems, held = [], [], []
    for a in doc.get("spots", []):
        slug = a.get("slug") or _slug(a.get("name"))
        spot = by_slug.get(slug)
        if spot is None:
            problems.append((slug, "no spots_enriched.json match"))
            continue
        if a.get("nwps_node_lat") is None or a.get("nwps_node_lng") is None:
            problems.append((slug, "assignment missing node lat/lng"))
            continue
        buoy = a.get("nwps_buoy_id")
        key = (a.get("nwps_wfo"), str(buoy))
        verdict = trust_by_buoy.get(str(buoy)) if buoy is not None else None
        # retired NEVER auto-places via the pending path (retired = no valid buoy)
        is_pending = key in pending_set and key not in retired_set
        buoy_out = buoy
        if slug in unverifiable_slugs:
            # (c) island-shadowed — height-live, direction NEVER buoy-verifiable. Checked BEFORE
            # PASS so a PASS buoy can never relabel it "verified". B2: null the buoy id on the row.
            direction_status = "unverifiable"
            buoy_out = None
        elif verdict == "PASS":
            # (a) PASS: height-live + direction verified — UNLESS the buoy's direction axis is
            # retired (44098), where PASS verifies HEIGHT only and direction is "unverifiable".
            direction_status = "unverifiable" if key in retired_direction_set else "verified"
        elif is_pending:
            direction_status = "pending"       # (b) height-live now; direction pending a swell + --trustcheck
        elif force:
            direction_status = "forced"        # --force override (testing only)
        else:
            held.append((slug, buoy, verdict))  # none of the above -> never placed
            continue
        fields = {"swell_window_source": "nwps", "nwps_direction_status": direction_status}
        for k in _FIELDS:
            fields[k] = a.get(k)
        fields["nwps_grid"] = fields.get("nwps_grid") or "CG1"
        fields["nwps_buoy_id"] = buoy_out       # B2: None for unverifiable[]; unchanged otherwise
        rows.append({"slug": slug, "name": spot.get("name"),
                     "old_source": spot.get("swell_window_source"), "fields": fields,
                     "buoy": buoy_out, "direction_status": direction_status,
                     "forced": direction_status == "forced"})
    return rows, problems, held, trust_by_buoy, enriched, by_slug


def print_dry_run(rows, problems, held, trust_by_buoy):
    n_ver = sum(1 for r in rows if r["direction_status"] == "verified")
    n_pend = sum(1 for r in rows if r["direction_status"] == "pending")
    n_unver = sum(1 for r in rows if r["direction_status"] == "unverifiable")
    n_forced = sum(1 for r in rows if r["direction_status"] == "forced")
    tail = f", {n_forced} forced" if n_forced else ""
    print(f"\nDRY RUN — NWPS assignments → spots_enriched.json ({len(rows)} spots to place: "
          f"{n_ver} verified, {n_pend} pending, {n_unver} unverifiable{tail})")
    passed = sorted(b for b, v in trust_by_buoy.items() if v == "PASS")
    print(f"  per-buoy trust gate (PASS = height-live + direction VERIFIED): {json.dumps(trust_by_buoy)}")
    print(f"  PASS buoys: {', '.join(passed) or '(none)'}")
    pend_zones = sorted({f"{r['fields']['nwps_wfo']}/{r['buoy']}" for r in rows if r["direction_status"] == "pending"})
    print(f"  PENDING zones (height-live, direction NOT yet verified — buoy_reference.pending[]): "
          f"{', '.join(pend_zones) or '(none)'}")
    unver_slugs = sorted(r["slug"] for r in rows if r["direction_status"] == "unverifiable")
    print(f"  UNVERIFIABLE (height-live, direction NEVER buoy-verifiable — unverifiable[] slug or a "
          f"retired direction-axis buoy): {', '.join(unver_slugs) or '(none)'}\n")
    print(f"  {'slug':24}{'wfo':5}{'grid':5}{'node lat,lng':22}{'dist_m':>7}{'buoy':>7}{'direction':>14}  old→nwps")
    print(f"  {'-'*24} {'-'*4} {'-'*4} {'-'*20} {'-'*6} {'-'*6} {'-'*12}")
    for r in rows:
        f = r["fields"]
        node = f"{f['nwps_node_lat']:.4f},{f['nwps_node_lng']:.4f}"
        print(f"  {r['slug']:24}{(f['nwps_wfo'] or '—'):5}{(f['nwps_grid'] or '—'):5}{node:22}"
              f"{(f['nwps_node_distance_m'] or 0):>7}{str(f['nwps_buoy_id'] or '—'):>7}{r['direction_status']:>14}"
              f"  {r['old_source'] or '(none)'} → nwps")
    if rows:
        print(f"\n  fields written per spot: swell_window_source, nwps_direction_status, {', '.join(_FIELDS)}")
        print("  direction_status: 'verified' (buoy trust PASS), 'pending' (buoy in buoy_reference.pending[]), "
              "or 'unverifiable' (slug in buoy_reference.unverifiable[], or a retired direction-axis buoy — "
              "direction never buoy-verifiable). db_import folds it into data_sources.nwps_direction_status.")
    else:
        print("  (nothing to write)")
    print("  Every non-listed spot: untouched.")
    if held:
        print(f"\n  ⊘ {len(held)} HELD — buoy neither PASS nor in buoy_reference.pending[] (NOT placed):")
        for slug, buoy, verdict in held:
            why = f"verdict {verdict!r}" if verdict is not None else "absent from trust_by_buoy + pending"
            print(f"      {slug:24} held (buoy {buoy or '—'}: {why})")
    if problems:
        print(f"\n  ⚠ {len(problems)} not assignable (skipped):")
        for slug, why in problems:
            print(f"      {slug:24} {why}")


def apply_plan(rows, enriched, by_slug, had_trailing_nl):
    n = 0
    for r in rows:
        spot = by_slug.get(r["slug"])
        if spot is None:
            continue
        spot.update(r["fields"])
        n += 1
    text = json.dumps(enriched, indent=2, ensure_ascii=False)
    if had_trailing_nl:
        text += "\n"
    ENRICHED.write_text(text)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write spots_enriched.json (default: dry run)")
    ap.add_argument("--force", action="store_true",
                    help="tag spots even if their buoy is not PASS in trust_by_buoy (testing only)")
    a = ap.parse_args(argv)
    for p in (ASSIGNMENTS, ENRICHED):
        if not p.exists():
            print(f"error: missing {p}"
                  + (" — run `nwps_nearshore --validate` on the Mac first" if p is ASSIGNMENTS else ""),
                  file=sys.stderr)
            return 2

    raw = ENRICHED.read_text()
    try:
        rows, problems, held, trust_by_buoy, enriched, by_slug = build_plan(force=a.force)
    except ValueError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 2
    print_dry_run(rows, problems, held, trust_by_buoy)

    if not a.apply:
        print("\ndry run only — nothing written. Re-run with --apply to patch spots_enriched.json "
              "(places spots whose buoy is PASS *or* listed in buoy_reference.pending[]).")
        return 0
    n_pend = sum(1 for r in rows if r["direction_status"] == "pending")
    n = apply_plan(rows, enriched, by_slug, raw.endswith("\n"))
    print(f"\nAPPLIED → {ENRICHED}: {n} spots placed nwps (+ node fields, nwps_direction_status). "
          f"{n - n_pend} verified, {n_pend} pending. {len(held)} held, {len(problems)} skipped. "
          f"All others untouched.{'  [FORCED]' if a.force else ''}")
    print("Next: pipeline.interpret picks up nwps spots (additive); then db_import "
          "(nwps_direction_status → data_sources). DB not touched here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
