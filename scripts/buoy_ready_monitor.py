#!/usr/bin/env python3
"""Scheduled buoy-LIVENESS monitor (READ-ONLY on public NDBC data).

Detects when a reference buoy that anchors one or more NWPS-placed surf zones has stopped
producing wave observations — the failure mode that silently cost ~6 weeks when 46240
(Cabrillo Point) and 46284 (Soquel Cove) went quiet in June 2026 and NOTHING noticed: the
zones showed `pending`, zero direction events accumulated, and the only reason it surfaced
was the eventual roster removal throwing KeyErrors in CI. Two distinct signals, one loud and
one quiet — the quiet one is the whole point:

  * SILENT-BUT-LISTED (the primary, quiet failure): the buoy is still in NDBC's
    activestations.xml, but its most recent ACTUAL wave observation — the newest hour in
    ndbc_spectral.by_hour, the SAME spectral source the trust gate reads — is old. Liveness
    is time-since-last-observation, NOT roster membership. Tiered purely by age:
        < 7 days   — healthy / recently quiet: NO alert (surf is flat for a week all the time).
        7–30 days  — informational note (printed; no issue).
        > 30 days  — open a GitHub issue PROPOSING manual retirement.
  * DROPPED-FROM-ROSTER (the louder, secondary signal): the buoy is gone from
    activestations.xml entirely. Reported and issue-opened regardless of age — it is louder,
    but it is NOT the primary check (by the time a buoy is dropped it has usually been silent
    for weeks, which the age check above catches first).

DETECT AND REPORT ONLY. This monitor NEVER retires a buoy and NEVER edits trust_by_buoy,
buoy_reference, spots_enriched.json, or the DB. Retiring a reference is a both-axes
production change, buoys come back after servicing, and an upstream NDBC outage must not be
able to rewrite trust state unattended — so it follows the trust-loop pattern: open an issue,
a human decides (move the zone into buoy_reference.retired, then apply_nwps_assignments
--apply).

The watched roster is DERIVED from the live assignments — every NWPS-placed zone that has a
real buoy on the row: _tagged_nwps_zones() (which already covers both the trust_by_buoy PASS
zones and the buoy_reference.pending[] zones once placed), unioned with pending[] itself (a
pending buoy not yet placed), MINUS the zones that deliberately have no buoy — retired
both-axes (44098's box/gyx) and unverifiable (island-shadowed / no-buoy-exists). Alerting on
a zone that has no buoy by design would be pure noise. The hand-written WATCH list below is
now used ONLY to attach a human-readable zone label; it is NOT the source of the roster (so a
new SE-rollout zone is covered automatically, without editing this file).

Every run prints a positive HEALTH line even when nothing is wrong — e.g. "8 zones checked,
6 reporting within 48h, 0 silent 7–30d, 0 silent >30d, 0 dropped" — because the reason 46240
survived six weeks is that silence and health produced identical output. A clean run must say
"0 silent >30d" out loud.

Accumulation of direction events is handled elsewhere and IS scheduled: the
reverify-trust-accumulate workflow runs `--reverify-tagged` twice daily (cron 0 5,17). This
monitor does not flag swell events for a manual run anymore — that job is redundant.

Guardrails (by construction): READ-ONLY on public NDBC data. No Supabase / prod DB / NOMADS /
secrets beyond GITHUB_TOKEN; no --apply; no tagging; no writes to spots_enriched.json. Live
imports are lazy so --selftest needs no network or third-party deps.

    python3 scripts/buoy_ready_monitor.py             # live (needs public NDBC egress)
    python3 scripts/buoy_ready_monitor.py --selftest   # offline fixture test (no third-party deps)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make ``import pipeline...`` work when run as ``python3 scripts/buoy_ready_monitor.py``
# (that puts scripts/ on sys.path[0], not the repo root).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# --------------------------------------------------------------------------- #
# Liveness tiers — time since the buoy's last ACTUAL wave observation.         #
# --------------------------------------------------------------------------- #
LIVENESS_HEALTHY_H = 48        # < this many hours silent = "reporting within 48h" (health line)
LIVENESS_NOTE_DAYS = 7         # >= this many days silent = informational NOTE (printed, no issue)
LIVENESS_ALERT_DAYS = 30       # >= this many days silent = ALERT (open an issue proposing retirement)

# --------------------------------------------------------------------------- #
# Label lookup ONLY — NOT the roster. The roster is derived from the live      #
# assignments (see _derive_watch_zones); this list just supplies a readable    #
# zone label for a (wfo, id) when one exists. Adding a zone does NOT require    #
# editing this list — an unlabeled zone simply prints its wfo/id.              #
# --------------------------------------------------------------------------- #
WATCH = [
    # phi — Mid-Atlantic / NJ.
    {"id": "44025", "zone": "Monmouth", "wfo": "phi"},
    {"id": "44065", "zone": "Monmouth", "wfo": "phi"},
    {"id": "44091", "zone": "Ocean County + LBI (interim Absecon)", "wfo": "phi"},
    {"id": "44009", "zone": "Absecon->Cape May", "wfo": "phi"},
    {"id": "44084", "zone": "Delaware", "wfo": "phi"},
    # box — Southern New England. 44098 is RETIRED both axes (deep bank) — excluded from the roster.
    {"id": "44097", "zone": "RI south coast (Point Judith to Misquamicut, Newport, Block Island)", "wfo": "box"},
    {"id": "44013", "zone": "Massachusetts Bay (Boston / inner North Shore)", "wfo": "box"},
    {"id": "44008", "zone": "Outer Cape + Islands (offshore SE Nantucket)", "wfo": "box"},
    # gyx — Southern Maine / New Hampshire.
    {"id": "44007", "zone": "Southern Maine (Portland / Old Orchard / Higgins)", "wfo": "gyx"},
    # akq — Wakefield VA (Delmarva / Virginia Beach).
    {"id": "44099", "zone": "Virginia Beach (North End / Oceanfront / Sandbridge)", "wfo": "akq"},
    {"id": "44084", "zone": "Delmarva / Ocean City MD + Assateague", "wfo": "akq"},
    # mhx — Newport/Morehead City NC (Outer Banks).
    {"id": "44095", "zone": "Northern Outer Banks (Corolla / Nags Head / Rodanthe)", "wfo": "mhx"},
    {"id": "41025", "zone": "Cape Hatteras + south (Avon / Buxton / Hatteras / Ocracoke)", "wfo": "mhx"},
    # ilm — Wilmington NC (Cape Fear / Brunswick Islands).
    {"id": "41110", "zone": "Northern ilm — Wrightsville / Carolina Beach / Topsail", "wfo": "ilm"},
    {"id": "41013", "zone": "Cape Fear / Brunswick Islands — offshore (Frying Pan Shoals)", "wfo": "ilm"},
    {"id": "41108", "zone": "Brunswick Islands — Holden/Ocean Isle/Sunset", "wfo": "ilm"},
    # sgx — San Diego CA (CDIP 462xx nearshore buoys).
    {"id": "46254", "zone": "Central San Diego — La Jolla / PB / Blacks (Scripps Nearshore)", "wfo": "sgx"},
    {"id": "46266", "zone": "North County — Carlsbad / Encinitas / Ponto (Del Mar Nearshore)", "wfo": "sgx"},
    {"id": "46235", "zone": "South SD — Coronado / Imperial Beach / Tijuana Slough (Imperial Beach Nearshore)", "wfo": "sgx"},
    {"id": "46242", "zone": "Far North SD — San Onofre / Cottons / Dana Point (Camp Pendleton Nearshore)", "wfo": "sgx"},
    # mtr — Monterey Bay / Santa Cruz (46240), SF / Point Reyes (46237), San Mateo south (46284),
    # SLO / Diablo Canyon (46215). The June-2026 outage that motivated this monitor: 46240 + 46284.
    {"id": "46240", "zone": "Monterey Bay + Santa Cruz (Steamer Lane / Pleasure Point / Capitola / Manresa)", "wfo": "mtr"},
    {"id": "46237", "zone": "SF / Point Reyes (Bodega / Ocean Beach / Pacifica / Half Moon Bay)", "wfo": "mtr"},
    {"id": "46284", "zone": "San Mateo south coast (Pigeon Point + Scotts Creek)", "wfo": "mtr"},
    {"id": "46215", "zone": "SLO / Diablo Canyon (Big Sur / San Simeon / Cayucos / Morro / Avila / Pismo / Grover)", "wfo": "mtr"},
    # lox — Malibu / Santa Monica Bay (46268), South Bay / PV / Orange County (46256).
    {"id": "46268", "zone": "Malibu + Santa Monica Bay (County Line / Surfrider / Santa Monica / Venice / El Porto)", "wfo": "lox"},
    {"id": "46256", "zone": "South Bay + Palos Verdes + Orange County (Hermosa / Redondo / Seal Beach / Huntington / Newport)", "wfo": "lox"},
    # mlb — Melbourne FL (Southeast rollout). 41113 anchors the pending Brevard zones.
    {"id": "41113", "zone": "Central Brevard (Cocoa Beach / Sebastian Inlet area)", "wfo": "mlb"},
]


def _label_lookup():
    """(wfo, id) -> human zone label, from the hand-written WATCH list above (labels ONLY —
    not the roster). Keyed by (wfo, id) so a buoy shared across wfos is two distinct labels."""
    return {(w.get("wfo"), str(w["id"])): w.get("zone", "") for w in WATCH}


# --------------------------------------------------------------------------- #
# Pure decision logic (no network / no third-party deps — used by --selftest)  #
# --------------------------------------------------------------------------- #
def _liveness_tier(age_hours):
    """Pure: classify a buoy by hours since its last ACTUAL wave observation.
      None       -> 'alert'      (no observation in the realtime spectral window at all —
                                   silent beyond it, or the spectra are gone)
      < 48 h     -> 'reporting'  (healthy)
      2–7 days   -> 'quiet'      (recently quiet — NO alert; flat surf does this constantly)
      7–30 days  -> 'note'       (informational)
      > 30 days  -> 'alert'      (open an issue)"""
    if age_hours is None:
        return "alert"
    days = age_hours / 24.0
    if age_hours < LIVENESS_HEALTHY_H:
        return "reporting"
    if days < LIVENESS_NOTE_DAYS:
        return "quiet"
    if days < LIVENESS_ALERT_DAYS:
        return "note"
    return "alert"


def evaluate(watch_zones, roster_ids, lastobs_fn):
    """Per watched zone {id, wfo, zone, spots}, decide its liveness state. Pure: inject
    *roster_ids* (lowercased id set from activestations.xml) + *lastobs_fn* (buoy_id ->
    hours-since-last-wave-obs, or None) for offline tests.

    Liveness (the PRIMARY check) is age-tiered via _liveness_tier. Roster membership is a
    SECONDARY, louder signal: a buoy absent from the roster is DROPPED and always alerts —
    but ONLY when the roster is actually known (non-empty). If the roster fetch failed
    (empty set) we CANNOT assess membership, so we never mark anything dropped (a failed
    fetch must not manufacture a storm of false 'dropped' alerts). ALERT = age tier 'alert'
    (>30 d / no data) OR dropped."""
    roster_known = bool(roster_ids)
    results = []
    for z in watch_zones:
        bid = str(z["id"]).lower()
        in_roster = bid in roster_ids
        age_h = lastobs_fn(bid)
        tier = _liveness_tier(age_h)
        dropped = roster_known and not in_roster
        results.append({
            "id": z["id"], "wfo": z.get("wfo"), "zone": z.get("zone", ""),
            "spots": z.get("spots"), "age_hours": age_h,
            "age_days": (round(age_h / 24.0, 1) if age_h is not None else None),
            "roster_known": roster_known, "dropped": dropped, "tier": tier,
            "alert": bool(dropped or tier == "alert"),
        })
    return results


def _counts(results):
    """Tier tallies for the health line. 'dropped' is orthogonal to the age tier."""
    c = {"n": len(results), "reporting": 0, "quiet": 0, "note": 0, "alert": 0, "dropped": 0}
    for r in results:
        c[r["tier"]] = c.get(r["tier"], 0) + 1
        if r["dropped"]:
            c["dropped"] += 1
    return c


def _health_line(results):
    """The positive health line — printed EVERY run, including all-zero (that is the point:
    a healthy run must explicitly say '0 silent >30d', so silence never looks like health)."""
    c = _counts(results)
    return (f"HEALTH: {c['n']} zones checked, {c['reporting']} reporting within 48h, "
            f"{c['note']} silent 7–30d, {c['alert']} silent >30d, "
            f"{c['dropped']} dropped from roster")


def _alert_entry(r):
    """Compact shape emitted for the issue logic — one per >30d / no-data / dropped buoy."""
    return {"id": str(r["id"]), "wfo": r["wfo"], "zone": r["zone"], "spots": r.get("spots"),
            "age_days": r["age_days"], "dropped": r["dropped"]}


def _alerts(results):
    return [_alert_entry(r) for r in results if r["alert"]]


# --------------------------------------------------------------------------- #
# Output                                                                       #
# --------------------------------------------------------------------------- #
def _age_str(r):
    if r["age_hours"] is None:
        return "no data"
    if r["age_hours"] < 24:
        return f"{r['age_hours']}h"
    return f"{r['age_days']:.1f}d"


def _print_report(results, roster_known):
    print("=== buoy-LIVENESS monitor — hours since last actual wave observation per NWPS "
          "reference buoy (READ-ONLY) ===")
    print(f"    tiers: <48h reporting · <{LIVENESS_NOTE_DAYS}d quiet (no alert) · "
          f"{LIVENESS_NOTE_DAYS}–{LIVENESS_ALERT_DAYS}d note · >{LIVENESS_ALERT_DAYS}d ALERT "
          f"(issue) · absent-from-roster = DROPPED (issue)")
    if not roster_known:
        print("  warn: activestations.xml roster unavailable — DROPPED detection skipped this "
              "run (age-only); no false 'dropped' alerts will be raised.")
    print(f"  {'buoy':7}{'wfo':5}{'age':>9}{'state':>10}  zone")

    def _sort_key(r):   # worst first: no-data (inf), then oldest -> newest
        return r["age_hours"] if r["age_hours"] is not None else float("inf")

    for r in sorted(results, key=_sort_key, reverse=True):
        state = "DROPPED" if r["dropped"] else r["tier"].upper()
        print(f"  {str(r['id']):7}{(r['wfo'] or '—'):5}{_age_str(r):>9}{state:>10}  "
              f"{(r['zone'] or '')[:58]}")
    print()
    print(_health_line(results))
    alerts = _alerts(results)
    if alerts:
        zs = ", ".join(sorted(f"{a['wfo']}/{a['id']}" for a in alerts))
        print(f"\nALERT: {len(alerts)} buoy(s) need review (issue opened/updated): {zs}")
        print("  (detect + report only — no buoy is retired and no trust state is changed; a human decides.)")
    else:
        print("\nno buoy is silent >30d or dropped from the roster — nothing to alert")


def _emit_github_output(results):
    """Write any_alert / alert_json / health_line to $GITHUB_OUTPUT for the issue logic.
    alert_json carries only the >30d / no-data / dropped buoys (the ones that get an issue)."""
    alerts = _alerts(results)
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"any_alert={'true' if alerts else 'false'}\n")
            f.write("alert_json=" + json.dumps(alerts, separators=(",", ":"), ensure_ascii=False) + "\n")
            f.write("health_line=" + _health_line(results) + "\n")
    return alerts


# --------------------------------------------------------------------------- #
# Live wiring (lazy imports so --selftest needs no network / third-party deps) #
# --------------------------------------------------------------------------- #
def _live_roster_ids():
    """Lowercased id set from NDBC activestations.xml (roster membership — for DROPPED
    detection). Empty set on any failure → evaluate() then skips dropped-detection (age only)."""
    try:
        from pipeline.enrichment.geodata import load_ndbc_active_stations
        return {str(s["id"]).lower() for s in load_ndbc_active_stations()}
    except Exception as e:  # noqa: BLE001
        print(f"warn: NDBC active-station roster unavailable ({type(e).__name__}: {e}); "
              "roster membership cannot be assessed this run", file=sys.stderr)
        return set()


def _live_lastobs_fn(now=None):
    """buoy_id -> hours since its last ACTUAL wave observation. Thin wrapper over the ONE shared
    implementation (pipeline.forecast.ndbc_spectral.hours_since_last_obs — newest epoch-hour from
    by_hour, the SAME spectral source the trust gate and --find-buoy read), so the monitor and
    find-buoy can never disagree on 'alive'. None when there are no realtime spectra (dropped, or
    silent beyond the realtime window) — never a fabricated age."""
    from pipeline.forecast.ndbc_spectral import hours_since_last_obs
    now = now or datetime.now(timezone.utc)
    now_eh = int(now.timestamp() // 3600)
    return lambda buoy_id: hours_since_last_obs(buoy_id, now_epoch_hour=now_eh)


def _derive_watch_zones():
    """The roster to check — DERIVED from the live assignments, NOT the WATCH list.

    Sources (per the spec): every NWPS-placed zone with a real buoy on the row, taken from
    _tagged_nwps_zones() — which already surfaces both the trust_by_buoy PASS zones and the
    placed buoy_reference.pending[] zones (it keys on swell_window_source=='nwps', not on
    trust_by_buoy) — unioned with buoy_reference.pending[] itself (to catch a pending buoy
    not yet placed). EXCLUDES the zones that deliberately have no buoy: retired both-axes
    (buoy_reference.retired) and unverifiable (nwps_buoy_id is None on the row). WATCH supplies
    labels only. Returns [{id, wfo, zone, spots}]."""
    from pipeline.forecast.nwps_nearshore import (
        _tagged_nwps_zones, _retired_reference_zones, NWPS_ASSIGNMENTS)
    labels = _label_lookup()
    # id-only fallback: WATCH's wfo can differ from the live tag (e.g. 44025 is labelled
    # 'phi' in WATCH but tagged 'okx' live) — a cosmetic label should still resolve.
    labels_by_id = {}
    for (_w, i), z in labels.items():
        labels_by_id.setdefault(i, z)
    retired = set(_retired_reference_zones().keys())   # {(wfo, str(buoy))}

    zones = {}   # (wfo, str(buoy)) -> spot_count
    for wfo, buoy, nspots in _tagged_nwps_zones():
        if buoy is None:                      # unverifiable (no buoy on the row) — never alert
            continue
        if (wfo, str(buoy)) in retired:        # retired both-axes — no valid buoy to test
            continue
        zones[(wfo, str(buoy))] = nspots

    # Union pending[] (a pending buoy that has no placed spot yet), and use it to read
    # trust_by_buoy for a completeness cross-check below.
    try:
        doc = json.loads(NWPS_ASSIGNMENTS.read_text())
    except (OSError, ValueError):
        doc = {}
    for r in ((doc.get("buoy_reference") or {}).get("pending") or []):
        wfo, buoy = r.get("wfo"), r.get("buoy")
        if wfo and buoy is not None and (wfo, str(buoy)) not in retired:
            zones.setdefault((wfo, str(buoy)), r.get("spots"))

    # trust_by_buoy completeness guard: every PASS buoy (bar the retired 44098) should already
    # be covered via a tagged zone. Warn — don't silently drop — if one isn't (that would mean
    # a verified buoy with no placed spot, which is unexpected and worth a human's eye).
    covered = {b for (_w, b) in zones}
    retired_buoys = {b for (_w, b) in retired}
    for bid in (doc.get("trust_by_buoy") or {}):
        if str(bid) not in covered and str(bid) not in retired_buoys:
            print(f"warn: trust_by_buoy PASS buoy {bid} has no placed nwps zone — not covered by "
                  "liveness (unexpected; a verified buoy should anchor spots)", file=sys.stderr)

    out = []
    for (wfo, buoy), nspots in sorted(zones.items()):
        out.append({"id": buoy, "wfo": wfo, "spots": nspots,
                    "zone": labels.get((wfo, buoy)) or labels_by_id.get(buoy, "")})
    return out


def run():
    watch_zones = _derive_watch_zones()
    roster = _live_roster_ids()
    results = evaluate(watch_zones, roster, _live_lastobs_fn())
    _print_report(results, roster_known=bool(roster))
    _emit_github_output(results)
    return 0


# --------------------------------------------------------------------------- #
# Offline selftest (injected stubs; no network / no third-party deps)          #
# --------------------------------------------------------------------------- #
def _selftest():
    # Ages in HOURS since last wave obs, keyed by buoy id. Covers every tier + the two
    # signals that cost six weeks (silent-but-listed 46240; dropped-from-roster 46284).
    ages = {
        "44025": 12,          # reporting  (<48h)
        "46237": 3,           # reporting  (<48h)
        "44097": 96,          # quiet      (4d — recently quiet, NO alert)
        "46215": 14 * 24,     # note       (14d — informational)
        "46240": 42 * 24,     # alert      (>30d silent-but-listed — the primary failure mode)
        "46284": None,        # alert      (no realtime spectra) AND dropped from roster below
    }
    lastobs_fn = lambda bid: ages.get(str(bid).lower())
    roster = {"44025", "46237", "44097", "46215", "46240"}   # 46284 ABSENT -> dropped
    watch = [
        {"id": "44025", "wfo": "phi", "zone": "Monmouth", "spots": 5},
        {"id": "46237", "wfo": "mtr", "zone": "SF / Point Reyes", "spots": 13},
        {"id": "44097", "wfo": "box", "zone": "RI south coast", "spots": 4},
        {"id": "46215", "wfo": "mtr", "zone": "SLO / Diablo Canyon", "spots": 12},
        {"id": "46240", "wfo": "mtr", "zone": "Monterey Bay", "spots": 24},
        {"id": "46284", "wfo": "mtr", "zone": "San Mateo south coast", "spots": 2},
    ]
    res = evaluate(watch, roster, lastobs_fn)
    by = {(str(r["id"]), r["wfo"]): r for r in res}
    alerts = {f'{a["wfo"]}/{a["id"]}' for a in _alerts(res)}
    c = _counts(res)

    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    # --- per-buoy tiering ---
    check("reporting <48h, no alert (phi/44025)",
          by[("44025", "phi")]["tier"] == "reporting" and not by[("44025", "phi")]["alert"])
    check("reporting <48h (mtr/46237)", by[("46237", "mtr")]["tier"] == "reporting")
    check("quiet 2–7d, NO alert (box/44097)",
          by[("44097", "box")]["tier"] == "quiet" and not by[("44097", "box")]["alert"])
    check("note 7–30d, NO issue but printed (mtr/46215)",
          by[("46215", "mtr")]["tier"] == "note" and not by[("46215", "mtr")]["alert"])
    check("ALERT >30d silent-but-listed, NOT dropped (mtr/46240)",
          by[("46240", "mtr")]["tier"] == "alert" and by[("46240", "mtr")]["alert"]
          and not by[("46240", "mtr")]["dropped"])
    check("ALERT no-data AND dropped-from-roster (mtr/46284)",
          by[("46284", "mtr")]["alert"] and by[("46284", "mtr")]["dropped"])

    # --- the alert set is exactly the >30d / no-data / dropped buoys ---
    check("exactly the two failing buoys alert (mtr/46240, mtr/46284)",
          alerts == {"mtr/46240", "mtr/46284"})

    # --- health line: a healthy count and a failing count must LOOK different ---
    check("health count reporting-within-48h = 2", c["reporting"] == 2)
    check("health count silent 7–30d = 1", c["note"] == 1)
    check("health count silent >30d = 2", c["alert"] == 2)
    check("health count dropped = 1", c["dropped"] == 1)
    check("health line names every bucket (incl. '0'-capable >30d)",
          "reporting within 48h" in _health_line(res) and "silent >30d" in _health_line(res))

    # --- _liveness_tier boundaries ---
    check("tier boundary 47h -> reporting", _liveness_tier(47) == "reporting")
    check("tier boundary 48h -> quiet", _liveness_tier(48) == "quiet")
    check("tier boundary 7d -> note", _liveness_tier(7 * 24) == "note")
    check("tier boundary 30d -> alert", _liveness_tier(30 * 24) == "alert")
    check("tier None (no data) -> alert", _liveness_tier(None) == "alert")

    # --- a failed roster fetch must NOT manufacture 'dropped' alerts ---
    res_noroster = evaluate(watch, set(), lastobs_fn)   # empty roster = fetch failed
    nd = {(str(r["id"]), r["wfo"]): r for r in res_noroster}
    check("empty roster -> nothing marked dropped (no false storm)",
          not any(r["dropped"] for r in res_noroster))
    check("empty roster -> 46284 still alerts on age (no-data), just not as 'dropped'",
          nd[("46284", "mtr")]["alert"] and not nd[("46284", "mtr")]["dropped"])
    check("empty roster -> 46240 still alerts on >30d age",
          nd[("46240", "mtr")]["alert"])

    # --- a fully healthy run still emits a health line that says '0 silent >30d' out loud ---
    healthy = evaluate(
        [{"id": "44025", "wfo": "phi", "zone": "Monmouth", "spots": 5}],
        {"44025"}, lambda bid: 6)
    check("healthy run says '0 silent >30d' explicitly (silence != health)",
          "0 silent >30d" in _health_line(healthy) and not _alerts(healthy))

    print("\nself-test:",
          "ALL PASS — liveness tiers; silent-but-listed + dropped both alert; quiet/note never "
          "over-alert; failed roster fetch raises no false drop; health line is always explicit."
          if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--selftest", action="store_true", help="offline fixture test (no third-party deps)")
    a = ap.parse_args(argv)
    return _selftest() if a.selftest else run()


if __name__ == "__main__":
    raise SystemExit(main())
