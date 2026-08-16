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

``--promote-to-manual`` is a DIFFERENT job on the same export (BU-17). enrich.py
applies name-keyed ``manual_orientations.json`` first and slug-keyed
``spot_orientations.json`` second, so a slug row silently wins and 39 hand values
never reach the rating. This mode writes the reviewed value into the MANUAL file
and DELETES the slug row, so the two files agree and a re-seed of the bulk cache
cannot flip them back — the pattern already recorded for North Jetty and Jetty
Park Cocoa Beach, neither of which has a slug row. It preserves each entry's
``notes`` and ``source`` verbatim, stamps a ``bu17`` provenance object, and
verifies — in dry run AND apply — that no promoted slug would move
``orientation_deg``/``offshore_wind_deg`` in spots_enriched.json, aborting if any
would. It never writes spots_enriched.json and never touches Supabase.

    python -m pipeline.apply_orientation_relook --input EXPORT.json            # dry run (default)
    python -m pipeline.apply_orientation_relook --input EXPORT.json --apply    # write the override only
    python -m pipeline.apply_orientation_relook --input EXPORT.json --apply --also-patch-enriched
        # write the override AND surgically patch spots_enriched.json (orientation-only)
    python -m pipeline.apply_orientation_relook --input EXPORT.json --promote-to-manual
    python -m pipeline.apply_orientation_relook --input EXPORT.json --promote-to-manual --apply
        # BU-17: manual_orientations.json gets the value, spot_orientations.json loses the row
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
MANUAL_ORIENTATIONS_PATH = DATA_DIR / "manual_orientations.json"
ENRICHED_PATH = Path(__file__).parent / "spots_enriched.json"

# --promote-to-manual provenance, stamped on every promoted entry (BU-17).
BU17_METHOD = "satellite drag review"
BU17_REVIEWED = "2026-08-16"

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


# ---------------------------------------------------------------------------
# --promote-to-manual: end the name-vs-slug override collision (BU-17)
# ---------------------------------------------------------------------------
# enrich.py applies the NAME-keyed manual_orientations.json first and the
# SLUG-keyed spot_orientations.json second, so a slug row silently wins — see
# _load_spot_orientations' own docstring, "a slug match here beats every other
# source". 41 manual entries have a slug row and 39 disagree with it, so those
# hand values never reach the rating. Promotion writes the reviewed value into
# the manual file AND DELETES the slug row, so the two files agree and a re-seed
# of the bulk cache cannot flip them back. That is the pattern manual_orientations
# already records for North Jetty and Jetty Park Cocoa Beach, neither of which
# has a slug row — the half of it that actually makes the manual value effective
# is the deletion, not the copy.


def _enrich_slug_for():
    """Return enrich._slug_for — imported by name, never reimplemented.

    The module-level _slug_for above is a deliberate inline copy so the default
    modes run without enrich's geodata dependencies. THIS MODE MUST NOT USE IT:
    the slug decides which manual entry a reviewed value binds to, and a silent
    divergence between the two implementations would bind the wrong entry, or
    none, with nothing to notice. So import the real one and fail loudly."""
    from .enrich import _slug_for as enrich_slug_for   # noqa: PLC0415  (lazy on purpose)
    return enrich_slug_for


def _as_stored(deg: float, prior: Any) -> float | int:
    """Match the neighbouring entries' numeric style: manual_orientations stores
    whole degrees as ints. Integral values become ints when the entry it replaces
    was one, so promotion does not sprinkle 120.0 through a file of 120s."""
    v = round(float(deg) % 360.0, 1)
    if v == int(v) and (prior is None or isinstance(prior, int)):
        return int(v)
    return v


def promote_plan(export: dict[str, dict], manual_entries: dict[str, dict],
                 slug_entries: dict[str, dict], slug_for) -> dict:
    """Bind each export slug to a manual entry BY APPLYING slug_for TO THE MANUAL
    ENTRY NAME, which is the same lookup enrich performs. Returns rows + no-match."""
    by_slug: dict[str, list[str]] = {}
    for name in manual_entries:
        by_slug.setdefault(slug_for(name), []).append(name)
    rows, nomatch, bad, ambiguous = [], [], [], []
    for slug, entry in export.items():
        adopted = _validate_deg(slug, entry)
        if adopted is None:
            bad.append(slug)
            continue
        names = by_slug.get(slug) or []
        if not names:
            nomatch.append({"slug": slug, "adopted": adopted, "name": entry.get("name")})
            continue
        if len(names) > 1:                    # two manual names slugging identically
            ambiguous.append({"slug": slug, "names": names})
            continue
        name = names[0]
        rec = manual_entries[name]
        prior = rec.get("orientation_deg")
        prior_deg = float(prior) % 360.0 if isinstance(prior, (int, float)) else None
        rows.append({
            "name": name, "slug": slug, "prior": prior_deg, "adopted": adopted,
            "delta": _circular_delta(prior_deg, adopted) if prior_deg is not None else None,
            "deletes_slug_row": slug in slug_entries,
            "has_cardinal": "cardinal" in rec,
        })
    rows.sort(key=lambda r: (r["delta"] is None, -(r["delta"] or 0)))
    return {"rows": rows, "nomatch": nomatch, "bad": bad, "ambiguous": ambiguous,
            "n_export": len(export)}


def verify_against_enriched(rows: list[dict], enriched_spots: list, slug_for) -> dict:
    """Recompute what orientation_deg / offshore_wind_deg WOULD be after promotion
    and diff against what spots_enriched.json holds now. Runs in dry run AND apply.

    All 21 BU-17 values were reviewed to equal the live slug value, which is what
    spots_enriched.json already carries, so this must come back ZERO. A non-zero
    count means the reviewed set is not what it was believed to be and the
    promotion would silently move live ratings — the run stops rather than writes."""
    by_slug: dict[str, dict] = {}
    for s in enriched_spots if isinstance(enriched_spots, list) else []:
        sl = slug_for(s.get("name"))
        if sl and sl not in by_slug:
            by_slug[sl] = s
    checked, diffs, absent = 0, [], []
    for r in rows:
        s = by_slug.get(r["slug"])
        if s is None:
            absent.append(r["slug"])
            continue
        checked += 1
        want_o = round(r["adopted"] % 360.0, 1)
        want_w = round((want_o + 180.0) % 360.0, 1)
        cur_o, cur_w = s.get("orientation_deg"), s.get("offshore_wind_deg")
        for field, cur, want in (("orientation_deg", cur_o, want_o),
                                 ("offshore_wind_deg", cur_w, want_w)):
            got = round(float(cur) % 360.0, 1) if isinstance(cur, (int, float)) else None
            if got is None or _circular_delta(got, want) >= 0.05:
                diffs.append({"slug": r["slug"], "field": field, "current": cur, "would_be": want})
    return {"checked": checked, "diffs": diffs, "absent": absent}


def print_promote_dry_run(p: dict, v: dict) -> None:
    rows = p["rows"]
    print("\nDRY RUN — PROMOTE TO MANUAL (BU-17)")
    print("  writes the reviewed value into manual_orientations.json AND deletes the slug row from")
    print("  spot_orientations.json, so enrich's slug-wins-last precedence can no longer override it.\n")
    print(f"export entries: {p['n_export']}   promotable: {len(rows)}   "
          f"NO-MATCH: {len(p['nomatch'])}   ambiguous: {len(p['ambiguous'])}   bad: {len(p['bad'])}\n")
    if rows:
        print(f"  {'name':28} {'slug':28} {'prior':>6} {'adopt':>6} {'Δ':>5}  slug row")
        print(f"  {'-'*28} {'-'*28} {'-'*6} {'-'*6} {'-'*5}  {'-'*8}")
        for r in rows:
            prior = f"{r['prior']:.0f}" if r["prior"] is not None else "—"
            dlt = f"{r['delta']:.0f}" if r["delta"] is not None else "—"
            print(f"  {r['name'][:28]:28} {r['slug'][:28]:28} {prior:>6} {r['adopted']:>6.0f} "
                  f"{dlt:>5}  {'DELETE' if r['deletes_slug_row'] else 'none'}")
    if p["nomatch"]:
        print(f"\n  ⚠ {len(p['nomatch'])} NO-MATCH (no manual entry whose name slugs to this; "
              "NOTHING is written for these):")
        for u in p["nomatch"]:
            print(f"      {u['slug']:30} (adopted {u['adopted']:.0f}°, name={u['name']!r})")
    if p["ambiguous"]:
        print(f"\n  ⚠ {len(p['ambiguous'])} AMBIGUOUS (several manual names slug identically; SKIPPED):")
        for a in p["ambiguous"]:
            print(f"      {a['slug']:30} ← {a['names']}")
    if p["bad"]:
        print(f"\n  ⚠ {len(p['bad'])} malformed (no numeric orientation_deg; SKIPPED): {p['bad']}")
    n_del = sum(1 for r in rows if r["deletes_slug_row"])
    print(f"\n  TOTALS: {len(rows)} manual entries updated · {n_del} slug rows deleted · "
          f"{len(rows) - n_del} already had no slug row · {len(p['nomatch'])} NO-MATCH skipped")
    print(f"\n  VERIFICATION vs spots_enriched.json ({v['checked']} promoted slugs checked): "
          f"{len(v['diffs'])} field(s) would change")
    if v["absent"]:
        print(f"    ({len(v['absent'])} promoted slug(s) not in spots_enriched.json, not checked: "
              f"{', '.join(v['absent'][:6])})")
    if v["diffs"]:
        print("    ⚠ EXPECTED ZERO — every reviewed value was said to equal the live value:")
        for d in v["diffs"][:20]:
            print(f"      {d['slug']:28} {d['field']:18} live {d['current']} → would be {d['would_be']}")
    else:
        print("    ✓ zero — promotion moves no live orientation or offshore wind value")
    print()


def apply_promotion(p: dict, manual_doc: dict, slug_doc: dict) -> dict:
    """Write both files. Every envelope key and every entry not named in the plan
    is preserved untouched; on a promoted entry only orientation_deg, cardinal
    (when it already had one) and bu17 are set — notes and source are left exactly
    as they were, never edited, appended to or removed."""
    manual_entries = manual_doc["orientations"]
    slug_entries = slug_doc.get("orientations", {})
    updated = deleted = carded = 0
    for r in p["rows"]:
        rec = manual_entries[r["name"]]
        prior_raw = rec.get("orientation_deg")
        rec["orientation_deg"] = _as_stored(r["adopted"], prior_raw)
        if r["has_cardinal"]:
            rec["cardinal"] = _cardinal(r["adopted"])
            carded += 1
        rec["bu17"] = {
            "prior_manual_deg": prior_raw,
            "adopted_deg": rec["orientation_deg"],
            "method": BU17_METHOD,
            "reviewed": BU17_REVIEWED,
        }
        updated += 1
        if slug_entries.pop(r["slug"], None) is not None:
            deleted += 1
    MANUAL_ORIENTATIONS_PATH.write_text(json.dumps(manual_doc, indent=2, ensure_ascii=False) + "\n")
    SPOT_ORIENTATIONS_PATH.write_text(json.dumps(slug_doc, indent=2, ensure_ascii=False) + "\n")
    return {"updated": updated, "deleted": deleted, "carded": carded,
            "manual_total": len(manual_entries), "slug_total": len(slug_entries)}


def _run_promote(args, export: dict[str, dict]) -> int:
    try:
        slug_for = _enrich_slug_for()
    except Exception as e:  # noqa: BLE001
        print(f"error: --promote-to-manual needs pipeline.enrich for its slug helper "
              f"({type(e).__name__}: {e}). Refusing to fall back to this module's inline copy — "
              "binding a reviewed value to the wrong manual entry would be silent.", file=sys.stderr)
        return 2
    if not MANUAL_ORIENTATIONS_PATH.exists():
        print(f"error: {MANUAL_ORIENTATIONS_PATH} not found", file=sys.stderr)
        return 2
    manual_doc = json.loads(MANUAL_ORIENTATIONS_PATH.read_text())
    slug_doc = json.loads(SPOT_ORIENTATIONS_PATH.read_text()) if SPOT_ORIENTATIONS_PATH.exists() \
        else {"_schema_version": 1, "orientations": {}}
    p = promote_plan(export, manual_doc.get("orientations", {}),
                     slug_doc.get("orientations", {}), slug_for)
    enriched_spots, _ = _load_enriched_list()
    v = verify_against_enriched(p["rows"], enriched_spots or [], slug_for)
    print_promote_dry_run(p, v)

    if v["diffs"]:
        print(f"ABORT — verification found {len(v['diffs'])} field(s) that would change in "
              "spots_enriched.json.\n"
              "  All 21 BU-17 values were reviewed as equal to the live value, so this must be zero.\n"
              "  Nothing was written. Re-check the export before promoting.", file=sys.stderr)
        return 1
    if not args.apply:
        print(f"dry run only — nothing written. Re-run with --apply to write "
              f"{MANUAL_ORIENTATIONS_PATH.name} + {SPOT_ORIENTATIONS_PATH.name}.")
        return 0
    res = apply_promotion(p, manual_doc, slug_doc)
    print(f"APPLIED → {MANUAL_ORIENTATIONS_PATH}  +  {SPOT_ORIENTATIONS_PATH}")
    print(f"  {res['updated']} manual entries updated (orientation_deg + bu17"
          + (f" + cardinal on {res['carded']}" if res["carded"] else "") + "; notes/source untouched)")
    print(f"  {res['deleted']} slug rows deleted — manual now wins because nothing outranks it")
    print(f"  totals: manual {res['manual_total']} entries · slug file {res['slug_total']} entries")
    print("  spots_enriched.json NOT written; Supabase NOT touched; enrich.py precedence unchanged.")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, required=True,
                    help="orientation_relook_export.json (slug-keyed)")
    ap.add_argument("--apply", action="store_true",
                    help="Write the merge into spot_orientations.json. Omit for a dry run.")
    ap.add_argument("--promote-to-manual", action="store_true",
                    help="BU-17: write each reviewed value into manual_orientations.json AND delete "
                         "that slug's row from spot_orientations.json, so enrich's slug-wins-last "
                         "precedence can no longer override the hand value. Dry run without --apply. "
                         "Never touches spots_enriched.json or Supabase.")
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

    if args.promote_to_manual:
        return _run_promote(args, export)

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
