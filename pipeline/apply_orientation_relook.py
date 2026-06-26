#!/usr/bin/env python3
"""Apply a slug-keyed orientation RELOOK export to the durable Algo-1c override
(``pipeline/data/spot_orientations.json``) — the file ``enrich.py`` reads LAST,
so a slug match there is the final word on a spot's ``orientation_deg``.

This is the slug-keyed sibling of ``apply_orientation_fixes.py``. That script
merges the NAME-keyed ``manual_orientations.json`` (Algo 1b) and updates
Supabase; the relook export is SLUG-keyed and targets the comprehensive
slug-keyed override (Algo 1c, enrich.py), so a separate, focused applier keeps
both flows simple. Same reviewed discipline: **DRY RUN by default**, then
``--apply``.

Input — the relook tool's export, shape unchanged::

    {"orientations": {slug: {orientation_deg, cardinal, name, source}}}

Dry run (default) prints, per spot that WILL CHANGE: slug, old orientation_deg,
new, Δ (circular, worst-first), a count, and two flags:

  * ``SWING``     new value is >90° from the current value — a big swing worth
                  re-confirming before it lands.
  * ``NO-MATCH``  slug is absent from spot_orientations.json AND from the spot
                  roster (spots_enriched.json) — a typo / renamed spot. These
                  are SKIPPED on apply (never written) and listed so you can fix
                  the name.

Apply (``--apply``) merges each matched entry into spot_orientations.json
(``orientation_deg`` + ``cardinal`` + ``name`` + ``source="manual_relook"``),
preserving every other entry and the file envelope, so the next
``enrich``/full-pipeline run picks them up. By default it does **not** write
spots_enriched.json and does **not** touch Supabase.

``--also-patch-enriched`` (with ``--apply``) additionally patches
spots_enriched.json **in place, orientation-only**, for the export slugs:
``orientation_deg`` = the new value, ``offshore_wind_deg`` = ``(deg+180)%360``,
``orientation_source`` = ``"manual"``. Nothing else on those spots is touched
(``optimal_swell_dir`` / ``swell_window_arcs`` stay as-is) and no other spot is
touched. This is the surgical alternative to a full ``enrich`` — it propagates
the corrected orientations into the file ``db_import`` actually reads, with zero
collateral on the other ~628 spots, and needs no GSHHG/geodata. It deliberately
does NOT reshift orientation-derived swell-window arcs; run a full ``enrich``
later if you want those recomputed. Without ``--apply`` it prints the enriched
diff too (dry-run parity). Matches by slug exactly; never creates new entries.

    python -m pipeline.apply_orientation_relook --input EXPORT.json            # dry run (default)
    python -m pipeline.apply_orientation_relook --input EXPORT.json --apply    # write the override only
    python -m pipeline.apply_orientation_relook --input EXPORT.json --apply --also-patch-enriched
        # write the override AND surgically patch spots_enriched.json (orientation-only)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
SPOT_ORIENTATIONS_PATH = DATA_DIR / "spot_orientations.json"
ENRICHED_PATH = Path(__file__).parent / "spots_enriched.json"

# Mirror enrich._slug_for / db_import._slugify so our roster keys match the
# override-lookup key the pipeline uses. Inlined (not imported) so this runs
# without the supabase dependency, exactly like enrich.py does.
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_CARD = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
         "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _slug_for(name: str | None) -> str:
    if not name:
        return ""
    return _SLUG_RE.sub("-", name.lower()).strip("-")


def _cardinal(deg: float) -> str:
    return _CARD[round((deg % 360) / 22.5) % 16]


def _circular_delta(a: float, b: float) -> float:
    """Smallest angular distance between two bearings, in [0, 180]."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _load_orientations(path: Path) -> dict[str, dict]:
    """Return the {slug: {orientation_deg, ...}} map from the export.
    Tolerates either an ``orientations`` envelope or a bare map at the root."""
    payload = json.loads(path.read_text())
    inner = payload["orientations"] if isinstance(payload, dict) and "orientations" in payload else payload
    if not isinstance(inner, dict):
        raise ValueError(f"expected a mapping in {path}; got {type(inner).__name__}")
    return inner


def _load_current() -> dict[str, dict]:
    if not SPOT_ORIENTATIONS_PATH.exists():
        return {}
    return json.loads(SPOT_ORIENTATIONS_PATH.read_text()).get("orientations", {})


def _enriched_orientations() -> dict[str, float]:
    """Fallback "old hand value" + roster: {slug: orientation_deg} from the
    enriched prod file (name-keyed list), so a spot not yet in the slug override
    still has a real current value to diff against and counts as a real spot."""
    if not ENRICHED_PATH.exists():
        return {}
    try:
        spots = json.loads(ENRICHED_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    out: dict[str, float] = {}
    for s in spots if isinstance(spots, list) else []:
        slug = _slug_for(s.get("name"))
        deg = s.get("orientation_deg")
        if slug and isinstance(deg, (int, float)):
            out[slug] = float(deg) % 360.0
    return out


def _validate_deg(slug: str, entry: Any) -> float | None:
    if not isinstance(entry, dict):
        return None
    deg = entry.get("orientation_deg")
    if not isinstance(deg, (int, float)):
        return None
    return float(deg) % 360.0


def plan(export: dict[str, dict], current: dict[str, dict],
         enriched: dict[str, float]) -> dict:
    """Build the change plan: rows (worst-first), unmatched, no-ops, bad."""
    rows, unmatched, noops, bad = [], [], [], []
    for slug, entry in export.items():
        new_deg = _validate_deg(slug, entry)
        if new_deg is None:
            bad.append(slug)
            continue
        cur_rec = current.get(slug)
        if cur_rec is not None and isinstance(cur_rec.get("orientation_deg"), (int, float)):
            old_deg, old_src = float(cur_rec["orientation_deg"]) % 360.0, "override"
        elif slug in enriched:
            old_deg, old_src = enriched[slug], "enriched"
        else:
            old_deg, old_src = None, None
        matched = cur_rec is not None or slug in enriched
        if not matched:
            unmatched.append({"slug": slug, "new": new_deg, "name": entry.get("name")})
            continue
        delta = _circular_delta(old_deg, new_deg) if old_deg is not None else None
        row = {
            "slug": slug, "old": old_deg, "old_src": old_src, "new": new_deg,
            "delta": delta, "name": entry.get("name") or (cur_rec or {}).get("name"),
            "cardinal": entry.get("cardinal") or _cardinal(new_deg),
            "swing": delta is not None and delta > 90.0,
        }
        if delta is not None and round(delta, 1) == 0.0:
            noops.append(row)
        else:
            rows.append(row)
    rows.sort(key=lambda r: (r["delta"] is None, -(r["delta"] or 0)))
    return {"rows": rows, "unmatched": unmatched, "noops": noops, "bad": bad,
            "n_export": len(export)}


def print_dry_run(p: dict) -> None:
    rows, unmatched, noops, bad = p["rows"], p["unmatched"], p["noops"], p["bad"]
    print(f"\nDRY RUN — orientation relook → spot_orientations.json (Algo 1c, slug-keyed)")
    print(f"export entries: {p['n_export']}   will change: {len(rows)}   "
          f"no-op (unchanged): {len(noops)}   unmatched: {len(unmatched)}   bad: {len(bad)}\n")
    if rows:
        print(f"  {'slug':30} {'old':>6}  {'new':>6}  {'Δ':>5}   flag")
        print(f"  {'-'*30} {'-'*6}  {'-'*6}  {'-'*5}   {'-'*12}")
        for r in rows:
            old = f"{r['old']:.0f}" if r["old"] is not None else "—"
            dlt = f"{r['delta']:.0f}" if r["delta"] is not None else "—"
            star = "*" if r["old_src"] == "enriched" else " "
            flag = "⚠ SWING >90°" if r["swing"] else ""
            print(f"  {r['slug']:30} {old:>5}{star} {r['new']:>5.0f}°  {dlt:>4}°   {flag}")
        print(f"\n  ({len(rows)} spots will change)")
        if any(r["old_src"] == "enriched" for r in rows):
            print("  * old value from spots_enriched.json (slug not yet in the override file — this ADDS one)")
    swings = [r for r in rows if r["swing"]]
    if swings:
        print(f"\n  ⚠ {len(swings)} BIG SWING (>90° from current — re-confirm these):")
        for r in swings:
            print(f"      {r['slug']:30} {r['old']:.0f}° → {r['new']:.0f}°  (Δ{r['delta']:.0f}°)")
    if unmatched:
        print(f"\n  ⚠ {len(unmatched)} NO-MATCH (slug not a known spot — typo/renamed; SKIPPED on apply):")
        for u in unmatched:
            print(f"      {u['slug']:30} (new {u['new']:.0f}°, name={u['name']!r})")
    if bad:
        print(f"\n  ⚠ {len(bad)} malformed entries (no numeric orientation_deg; SKIPPED): {bad}")
    if noops:
        print(f"\n  {len(noops)} unchanged (export == current): "
              + ", ".join(r["slug"] for r in noops[:12]) + (" …" if len(noops) > 12 else ""))
    print()


def apply(p: dict) -> dict:
    """Merge matched, changed rows into spot_orientations.json. Returns counts.
    Unmatched (typo) and malformed entries are never written."""
    doc = json.loads(SPOT_ORIENTATIONS_PATH.read_text()) if SPOT_ORIENTATIONS_PATH.exists() \
        else {"_schema_version": 1, "orientations": {}}
    existing = doc.setdefault("orientations", {})
    added = replaced = 0
    for r in p["rows"]:  # noops are unchanged → no need to rewrite; rows are the real changes
        rec = existing.get(r["slug"], {})
        if r["slug"] in existing:
            replaced += 1
        else:
            added += 1
        existing[r["slug"]] = {
            "orientation_deg": round(r["new"], 1),
            "cardinal": r["cardinal"],
            "name": rec.get("name") or r["name"],
            "source": "manual_relook",
        }
    SPOT_ORIENTATIONS_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return {"added": added, "replaced": replaced, "total": len(existing),
            "skipped_unmatched": len(p["unmatched"]), "skipped_bad": len(p["bad"])}


# ---------------------------------------------------------------------------
# --also-patch-enriched: surgical orientation-only patch of spots_enriched.json
# ---------------------------------------------------------------------------

def _load_enriched_list() -> tuple[list | None, bool]:
    """Return (spots_list, original_had_trailing_newline). (None, True) if the
    file is missing/unreadable/not a list."""
    if not ENRICHED_PATH.exists():
        return None, True
    raw = ENRICHED_PATH.read_text()
    try:
        spots = json.loads(raw)
    except json.JSONDecodeError:
        return None, True
    if not isinstance(spots, list):
        return None, True
    return spots, raw.endswith("\n")


def enriched_plan(export: dict[str, dict], enriched_spots: list) -> dict:
    """Plan an orientation-only in-place patch of spots_enriched.json for the
    export slugs. Computed against the CURRENT enriched value (independent of
    spot_orientations.json), so it stays correct even after the override file is
    already merged. Matches by slug exactly; never creates new entries."""
    by_slug: dict[str, list[int]] = {}
    for i, s in enumerate(enriched_spots):
        sl = _slug_for(s.get("name"))
        if sl:
            by_slug.setdefault(sl, []).append(i)
    changes, noops, unmatched = [], [], []
    for slug, entry in export.items():
        new_deg = _validate_deg(slug, entry)
        if new_deg is None:
            continue  # malformed already surfaced by the orientation plan
        idxs = by_slug.get(slug)
        if not idxs:
            unmatched.append({"slug": slug, "new": new_deg})
            continue
        old = enriched_spots[idxs[0]].get("orientation_deg")
        old_deg = float(old) % 360.0 if isinstance(old, (int, float)) else None
        delta = _circular_delta(old_deg, new_deg) if old_deg is not None else None
        rec = {"slug": slug, "old": old_deg, "new": new_deg, "delta": delta,
               "idxs": idxs, "swing": delta is not None and delta > 90.0}
        if delta is not None and round(delta, 1) == 0.0:
            noops.append(rec)
        else:
            changes.append(rec)
    changes.sort(key=lambda r: (r["delta"] is None, -(r["delta"] or 0)))
    return {"changes": changes, "noops": noops, "unmatched": unmatched,
            "n_enriched": len(enriched_spots)}


def reconcile_enriched(ep: dict, override: dict[str, dict]) -> dict:
    """Cross-check each enriched change against the spot_orientations.json
    override (safety req): confirm the patched value matches the merged
    override, so the two files end up consistent. Returns matches + any gap."""
    matches, gap = 0, []
    for r in ep["changes"]:
        ov = override.get(r["slug"])
        ovd = ov.get("orientation_deg") if isinstance(ov, dict) else None
        if isinstance(ovd, (int, float)) and _circular_delta(float(ovd) % 360.0, r["new"]) < 0.6:
            matches += 1
        else:
            gap.append((r["slug"], ovd))
    return {"matches": matches, "gap": gap}


def print_enriched_dry_run(ep: dict, rc: dict) -> None:
    ch, noops, un = ep["changes"], ep["noops"], ep["unmatched"]
    print("ENRICHED PATCH (--also-patch-enriched) — orientation-only, in place → spots_enriched.json")
    print(f"  will patch: {len(ch)}   already in sync: {len(noops)}   "
          f"unmatched (skip, no entry created): {len(un)}\n")
    if ch:
        print(f"  {'slug':30} {'old(enr)':>9}  {'new':>6}  {'Δ':>5}   flag")
        print(f"  {'-'*30} {'-'*9}  {'-'*6}  {'-'*5}")
        for r in ch:
            old = f"{r['old']:.0f}" if r["old"] is not None else "—"
            dlt = f"{r['delta']:.0f}" if r["delta"] is not None else "—"
            flag = "⚠ SWING >90°" if r["swing"] else ""
            print(f"  {r['slug']:30} {old:>8}° {r['new']:>5.0f}°  {dlt:>4}°   {flag}")
        print(f"\n  ({len(ch)} entries would get orientation_deg + offshore_wind_deg patched, "
              f"orientation_source→'manual'; nothing else touched)")
    if un:
        print(f"\n  ⚠ {len(un)} not found in spots_enriched.json (SKIPPED, no entry created):")
        for u in un:
            print(f"      {u['slug']:30} (new {u['new']:.0f}°)")
    if noops:
        print(f"\n  {len(noops)} already in sync (enriched == new): "
              + ", ".join(r["slug"] for r in noops[:12]) + (" …" if len(noops) > 12 else ""))
    gap = rc["gap"]
    print(f"\n  cross-check vs spot_orientations.json: {rc['matches']}/{len(ch)} patches match the override"
          + ("  (consistent)" if not gap else
             "  ·  ⚠ " + str(len(gap)) + " gap: " + ", ".join(f"{s}(override={o})" for s, o in gap[:6])))
    print()


def patch_enriched(ep: dict, enriched_spots: list, had_trailing_nl: bool) -> dict:
    """Patch spots_enriched.json IN PLACE for the changed slugs only:
    orientation_deg, offshore_wind_deg, orientation_source. No other field on
    those spots, and no other spot, is touched. Serialized exactly like
    enrich.py (indent=2, ensure_ascii=False) for a minimal diff."""
    patched = 0
    for r in ep["changes"]:
        deg = round(r["new"], 1)
        off = round((deg + 180.0) % 360.0, 1)
        for i in r["idxs"]:
            s = enriched_spots[i]
            s["orientation_deg"] = deg
            s["offshore_wind_deg"] = off
            s["orientation_source"] = "manual"
            patched += 1
    text = json.dumps(enriched_spots, indent=2, ensure_ascii=False)
    if had_trailing_nl:
        text += "\n"
    ENRICHED_PATH.write_text(text)
    return {"patched": patched, "unmatched": len(ep["unmatched"]), "noops": len(ep["noops"])}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, required=True,
                    help="orientation_relook_export.json (slug-keyed)")
    ap.add_argument("--apply", action="store_true",
                    help="Write the merge into spot_orientations.json. Omit for a dry run.")
    ap.add_argument("--also-patch-enriched", action="store_true",
                    help="With --apply, ALSO patch spots_enriched.json in place (orientation-only) "
                         "for the export slugs so db_import sees the new orientations without a full "
                         "enrich. Without --apply, additionally shows the enriched diff.")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.input.exists():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2
    try:
        export = _load_orientations(args.input)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"error: could not parse {args.input}: {e}", file=sys.stderr)
        return 2

    override = _load_current()
    p = plan(export, override, _enriched_orientations())
    print_dry_run(p)

    # Optional enriched-patch plan (dry-run parity even without --apply).
    ep = enriched_spots = None
    had_nl = True
    if args.also_patch_enriched:
        enriched_spots, had_nl = _load_enriched_list()
        if enriched_spots is None:
            print(f"⚠ --also-patch-enriched: {ENRICHED_PATH} missing/unreadable — cannot patch enriched.\n")
        else:
            ep = enriched_plan(export, enriched_spots)
            print_enriched_dry_run(ep, reconcile_enriched(ep, override))

    if not args.apply:
        tail = "spot_orientations.json" + ("  +  spots_enriched.json" if ep is not None else "")
        print(f"dry run only — nothing written. Re-run with --apply to write {tail}.")
        return 0

    res = apply(p)
    print(f"APPLIED → {SPOT_ORIENTATIONS_PATH}")
    print(f"  {res['added']} added · {res['replaced']} replaced · {res['total']} total entries")
    if res["skipped_unmatched"] or res["skipped_bad"]:
        print(f"  skipped {res['skipped_unmatched']} unmatched + {res['skipped_bad']} malformed (not written)")

    if ep is not None:
        pres = patch_enriched(ep, enriched_spots, had_nl)
        print(f"ENRICHED PATCHED → {ENRICHED_PATH}")
        print(f"  {pres['patched']} entries patched (orientation_deg + offshore_wind_deg + "
              f"orientation_source='manual'); {pres['noops']} already in sync; "
              f"{pres['unmatched']} unmatched skipped")
        print("  NOTE: swell-window arcs NOT reshifted for these spots — optimal_swell_dir and "
              "swell_window_arcs left exactly as-is.")
        print("        Run a full `python -m pipeline.enrich` later to recompute their "
              "orientation-derived arcs.")
    elif not args.also_patch_enriched:
        print("  spots_enriched.json NOT written; Supabase NOT touched — "
              "run enrich (then db_import) to propagate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
