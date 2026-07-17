#!/usr/bin/env python3
"""Scheduled buoy-readiness monitor (READ-ONLY on public NDBC data) — rewired for the rebuilt
trust gate (height-primary; direction = energy-weighted, spot-tiered, ROLLING).

A zone is "worth checking now" (the alert trigger) only when a watched NDBC buoy shows a genuine
SWELL EVENT — not merely a wave-height range that a ramping wind-chop produces with no real swell.
Concretely, all of:

  * SWELL, reusing the trust gate's OWN per-hour precondition (nwps_nearshore._swell_precondition,
    swell-band Hs >= SWELL_HS_FLOOR_M AND swell fraction >= SWELL_FRAC_FLOOR — imported, one source
    of truth), SUSTAINED: >= MONITOR_MIN_QUALIFYING_HOURS such hours in the last
    MONITOR_SWELL_WINDOW_H (a real event, not a one-hour blip);
  * a USABLE buoy reference: VALID or MARGINAL per the --pairing-audit scorer. A STRUCTURALLY
    INVALID or RETIRED buoy (e.g. 44098's box/gyx zones — a 76 m offshore bank) is NEVER flagged:
    there is no valid instrument to test against, so an alert would be pure noise;
  * a zone whose ROLLING direction verdict is still ACCUMULATING. A settled PASS (or FAIL /
    INCOHERENT) needs no more events — don't nag.

Running the emitted `--trustcheck` on a flagged zone CONTRIBUTES ONE swell EVENT toward that zone's
rolling verdict — it is NOT a one-shot PASS/FAIL anymore. The gate needs TRUST_MIN_EVENTS
independent events before it settles.

Why the old trigger was wrong: it fired on 24h Hs RANGE, which a wind-chop ramp produces with no
real swell — it flagged mhx/44095, mhx/41025, ilm/41108, ilm/41110 (issue #58), all of which then
FAILED on 0.1-0.2 m of actual swell under a bigger chop. Hs range is retired; swell energy is the
trigger, and the monitor now only ever wakes us for a genuine, direction-checkable swell.

Guardrails (by construction): READ-ONLY on public NDBC data. No Supabase / prod DB / NOMADS /
secrets; no --apply; no tagging or untagging; no spots_enriched.json or rating writes. Reuses the
gate's precondition, pairing scorer, retirement registry, and rolling history verbatim (single
source of truth); the swell reader is pipeline.forecast.ndbc_spectral (pure). Live imports are lazy
so --selftest needs no third-party deps.

    python3 scripts/buoy_ready_monitor.py             # live (needs public NDBC egress)
    python3 scripts/buoy_ready_monitor.py --selftest   # offline fixture test (no third-party deps)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make ``import pipeline...`` work when run as ``python3 scripts/buoy_ready_monitor.py``
# (that puts scripts/ on sys.path[0], not the repo root).
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# --------------------------------------------------------------------------- #
# Trigger constants — the SWELL-EVENT shape (the per-hour swell FLOORS are the  #
# gate's, imported live; the monitor only defines what makes an EVENT).        #
# --------------------------------------------------------------------------- #
MONITOR_SWELL_WINDOW_H = 12        # look back this many hours for a swell event
MONITOR_MIN_QUALIFYING_HOURS = 3   # >= this many qualifying (swell-present) hours in the window = a
                                   #   SUSTAINED event. Hourly obs; a swell train persists for many
                                   #   hours, so requiring >=3 filters one-hour spikes / chop noise.
                                   #   This is NOT a total-Hs range check — that was the #58 bug.
_USABLE_PAIRINGS = ("VALID REFERENCE", "MARGINAL")   # STRUCTURALLY INVALID / RETIRED are excluded

# --------------------------------------------------------------------------- #
# Watch list — add buoys / a future region by editing this ONE list.          #
# "status" is now informational only (live vs candidate); alert gating is by   #
# computed swell event + pairing + rolling verdict, NOT by this field.         #
# --------------------------------------------------------------------------- #
WATCH = [
    # phi — Mid-Atlantic / NJ.
    {"id": "44025", "zone": "Monmouth", "wfo": "phi", "status": "live"},
    {"id": "44065", "zone": "Monmouth", "wfo": "phi", "status": "live"},
    {"id": "44091", "zone": "Ocean County + LBI (interim Absecon)", "wfo": "phi", "status": "live"},
    {"id": "44009", "zone": "Absecon->Cape May", "wfo": "phi", "status": "candidate"},
    {"id": "44084", "zone": "Delaware", "wfo": "phi", "status": "candidate"},
    # box — Southern New England. 44098 is RETIRED both axes (deep bank) — never flagged.
    {"id": "44097", "zone": "RI south coast (Point Judith to Misquamicut, Newport, Block Island)", "wfo": "box", "status": "live"},
    {"id": "44013", "zone": "Massachusetts Bay (Boston / inner North Shore)", "wfo": "box", "status": "candidate"},
    {"id": "44098", "zone": "North of Boston (Salisbury / Plum Island / Gloucester)", "wfo": "box", "status": "live"},
    {"id": "44008", "zone": "Outer Cape + Islands (offshore SE Nantucket; far offshore, weakest-fit zone)", "wfo": "box", "status": "candidate"},
    # gyx — Southern Maine / New Hampshire. 44098 appears again (shared buoy) — also RETIRED.
    {"id": "44007", "zone": "Southern Maine (Portland / Old Orchard / Higgins)", "wfo": "gyx", "status": "candidate"},
    {"id": "44098", "zone": "NH coast + far southern Maine (Hampton / York / Ogunquit)", "wfo": "gyx", "status": "live"},
    # akq — Wakefield VA (Delmarva / Virginia Beach). 44084 shared with phi's Delaware zone.
    {"id": "44099", "zone": "Virginia Beach (North End / Oceanfront / Sandbridge)", "wfo": "akq", "status": "live"},
    {"id": "44084", "zone": "Delmarva / Ocean City MD + Assateague", "wfo": "akq", "status": "candidate"},
    # mhx — Newport/Morehead City NC (Outer Banks). The #58 pair — swell-poor high-chop seas.
    {"id": "44095", "zone": "Northern Outer Banks (Corolla / Nags Head / Rodanthe)", "wfo": "mhx", "status": "candidate"},
    {"id": "41025", "zone": "Cape Hatteras + south (Avon / Buxton / Hatteras / Ocracoke) — Diamond Shoals, prone to going adrift", "wfo": "mhx", "status": "candidate"},
    # ilm — Wilmington NC (Cape Fear / Brunswick Islands). The other #58 pair.
    {"id": "41110", "zone": "Northern ilm — Wrightsville / Carolina Beach / Topsail (Masonboro nearshore)", "wfo": "ilm", "status": "candidate"},
    {"id": "41013", "zone": "Cape Fear / Brunswick Islands — offshore (Frying Pan Shoals)", "wfo": "ilm", "status": "candidate"},
    {"id": "41108", "zone": "Brunswick Islands — Holden/Ocean Isle/Sunset (Wilmington Harbor nearshore, southern-fit candidate)", "wfo": "ilm", "status": "candidate"},
    # sgx — San Diego CA (West Coast; CDIP 462xx nearshore buoys).
    {"id": "46254", "zone": "Central San Diego — La Jolla / PB / Blacks (Scripps Nearshore)", "wfo": "sgx", "status": "candidate"},
    {"id": "46266", "zone": "North County — Carlsbad / Encinitas / Ponto (Del Mar Nearshore) — candidate", "wfo": "sgx", "status": "candidate"},
    {"id": "46235", "zone": "South SD — Coronado / Imperial Beach / Tijuana Slough (Imperial Beach Nearshore) — candidate", "wfo": "sgx", "status": "candidate"},
    {"id": "46242", "zone": "Far North SD — San Onofre / Cottons / Dana Point (Camp Pendleton Nearshore) — candidate", "wfo": "sgx", "status": "candidate"},
    # mtr — Monterey Bay / Santa Cruz. 46240 (Cabrillo Point nearshore Waverider, 18 m, VALID) anchors
    # 24 spots placed on NWPS HEIGHT with direction PENDING (option B): no swell has verified direction
    # yet, so no trust PASS. Watch it so a swell event flags the zone to accumulate direction events
    # (rolling ACCUMULATING until it settles). NOT retired → the monitor watches it normally; being
    # live-on-height + direction-pending does NOT exclude it (gating is pairing + event + rolling).
    {"id": "46240", "zone": "Monterey Bay + Santa Cruz (Steamer Lane / Pleasure Point / Capitola / Manresa)", "wfo": "mtr", "status": "candidate"},
    # mtr — SF / Point Reyes. 46237 (San Francisco Bar Waverider, 17 m, VALID) anchors 13 spots
    # (Bodega → Half Moon Bay) and 46284 (Soquel Cove South Waverider, 24 m, VALID) anchors 2
    # (Pigeon Point + Scotts Creek). Both placed on NWPS HEIGHT with direction PENDING (option B):
    # no swell has verified direction yet, so no trust PASS. Watch both for a swell to accumulate
    # direction events (rolling ACCUMULATING). NOT retired → watched normally.
    {"id": "46237", "zone": "SF / Point Reyes (Bodega / Salmon Creek / Bolinas / Ocean Beach / Pacifica / Half Moon Bay)", "wfo": "mtr", "status": "candidate"},
    {"id": "46284", "zone": "San Mateo south coast (Pigeon Point + Scotts Creek)", "wfo": "mtr", "status": "candidate"},
    # lox — Malibu / Santa Monica Bay. 46268 (Topanga Nearshore Waverider, 20 m, VALID) anchors 18
    # spots (County Line → Bruce's Beach) placed on NWPS HEIGHT with direction PENDING (option B):
    # no swell has verified direction yet, so no trust PASS. Watch for a swell to accumulate direction
    # events (rolling ACCUMULATING). NOT retired → watched normally.
    {"id": "46268", "zone": "Malibu + Santa Monica Bay (County Line / Surfrider / Santa Monica / Venice / El Porto)", "wfo": "lox", "status": "candidate"},
    # mtr — SLO / Diablo Canyon. 46215 (Diablo Canyon Nearshore Waverider, 27 m, VALID) anchors 12
    # spots (Big Sur → Grover) placed on NWPS HEIGHT with direction PENDING (option B); no trust PASS.
    # Grid-crossing: 8 take height from the mtr grid, 4 (Shell Beach → Grover) from the lox grid, but
    # all 12 keep nwps_wfo='mtr' and buoy 46215 — so the monitor watches ONE (mtr, 46215) zone for all
    # 12 (the far lox nodes don't change where the buoy's direction is verified). NOT retired → watched.
    {"id": "46215", "zone": "SLO / Diablo Canyon (Big Sur / San Simeon / Cayucos / Morro / Avila / Pismo / Grover)", "wfo": "mtr", "status": "candidate"},
]


# --------------------------------------------------------------------------- #
# Pure decision logic (no network / no third-party deps — used by --selftest)  #
# --------------------------------------------------------------------------- #
def evaluate(watch, up_set, swell_fn, pairing_fn, rolling_fn, retired_set, now=None):
    """Per watched (buoy, zone), decide whether it has a qualifying swell EVENT worth a trust check
    now. FLAG iff: UP; a sustained swell event exists (swell_fn.has_event); the buoy is a USABLE
    reference (pairing_fn in VALID/MARGINAL) and NOT retired (retired_set); and the zone's rolling
    DIRECTION verdict is still ACCUMULATING (rolling_fn). Live calls are made lazily — swell_fn only
    for UP buoys, pairing_fn/rolling_fn only once a swell event exists — so a quiet ocean costs
    almost no NDBC fetches. Pure: inject the four callables + up_set + now for offline testing."""
    results = []
    for b in watch:
        bid, wfo = b["id"], b.get("wfo")
        up = bid.lower() in up_set
        swell = (swell_fn(bid) if up else
                 {"n_qualifying": 0, "current_swell_hs": None, "current_frac": None, "has_event": False})
        has_event = bool(swell.get("has_event"))
        retired = (wfo, str(bid)) in retired_set
        pairing = pairing_fn(bid) if (up and has_event and not retired) else None
        usable = pairing in _USABLE_PAIRINGS
        rolling = rolling_fn(wfo, bid) if (up and has_event and not retired and usable) else None
        verdict = (rolling or {}).get("verdict")
        flag = bool(up and has_event and not retired and usable and verdict == "ACCUMULATING")
        results.append({"id": bid, "zone": b["zone"], "wfo": wfo, "status": b.get("status"),
                        "up": up, "retired": retired, "pairing": pairing, "usable": usable,
                        "swell": swell, "rolling": rolling, "flag": flag,
                        "why": _state_label(up, has_event, retired, usable, verdict, flag)})
    return results


def _state_label(up, has_event, retired, usable, verdict, flag):
    """Short, legible reason a zone is / isn't flagged — so nothing is silently dropped."""
    if flag:
        return f"FLAG — swell event; rolling {verdict}"
    if not up:
        return "down"
    if retired:
        return "retired (both axes) — not monitorable"
    if not has_event:
        return "no swell event (chop / calm)"
    if not usable:
        return "buoy STRUCTURALLY INVALID — not monitorable"
    if verdict and verdict != "ACCUMULATING":
        return f"rolling {verdict} — settled, no more events needed"
    return "not flagged"


# --------------------------------------------------------------------------- #
# Output                                                                       #
# --------------------------------------------------------------------------- #
def _flag_entry(r):
    """Compact shape used in flagged_json + the issue body — carries the rolling-event context."""
    sw, roll = r["swell"], (r["rolling"] or {})
    hs = sw.get("current_swell_hs")
    return {"id": r["id"], "zone": r["zone"], "wfo": r["wfo"],
            "swell_hs": round(hs, 2) if hs is not None else None,
            "qual_hours": sw.get("n_qualifying"),
            "n_events": roll.get("n_events", 0),
            "rolling": roll.get("verdict", "ACCUMULATING")}


def _flagged(results):
    return [_flag_entry(r) for r in results if r["flag"]]


def _print_summary(results, min_events):
    print("=== buoy-ready monitor — FLAG = sustained SWELL event + USABLE buoy + rolling ACCUMULATING ===")
    print(f"    (swell precondition = the gate's; event = >= {MONITOR_MIN_QUALIFYING_HOURS} qualifying "
          f"hrs / last {MONITOR_SWELL_WINDOW_H} h. NOT a wave-height range.)")
    print(f"  {'buoy':7}{'wfo':5}{'up':4}{'swellHs':>8}{'qHrs':>5}{'pairing':>13}{'roll':>13}  state")
    for r in results:
        sw = r["swell"]
        hs = f"{sw['current_swell_hs']:.2f}" if sw.get("current_swell_hs") is not None else "—"
        q = sw.get("n_qualifying")
        qs = str(q) if q is not None else "—"
        pr = (r["pairing"] or "—").replace(" REFERENCE", "")[:12]
        roll = r["rolling"] or {}
        rl = f"{roll.get('n_events', 0)}/{min_events} {roll.get('verdict', '')}".strip() if roll else "—"
        print(f"  {r['id']:7}{(r['wfo'] or '—'):5}{('UP' if r['up'] else 'DN'):4}{hs:>8}{qs:>5}"
              f"{pr:>13}{rl:>13}  {r['why']}")
    flagged = _flagged(results)
    not_mon = [r for r in results if (r["retired"] or (r["up"] and r["swell"].get("has_event")
               and not r["usable"] and r["pairing"]))]
    if flagged:
        zones = ", ".join(sorted({f"{f['wfo']}/{f['id']}" for f in flagged}))
        print(f"\n{len(flagged)} zone(s) with a swell EVENT to check → alert: {zones}")
        print("  (each --trustcheck run adds ONE event toward the zone's rolling verdict — not a one-shot pass.)")
    else:
        print("\nno zone has a qualifying swell event with a usable, still-accumulating buoy → no alert")
    if not_mon:
        zs = ", ".join(sorted({f"{r['wfo']}/{r['id']}" for r in not_mon}))
        print(f"not monitorable (no valid buoy reference — never flagged, by design): {zs}")


def _emit_github_output(results, min_events):
    """Write any_flagged / flagged_json / min_events to $GITHUB_OUTPUT for the issue logic.
    flagged_json carries only zones with a real swell event + usable buoy + ACCUMULATING rolling."""
    flagged = _flagged(results)
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"any_flagged={'true' if flagged else 'false'}\n")
            f.write("flagged_json=" + json.dumps(flagged, separators=(",", ":"), ensure_ascii=False) + "\n")
            f.write(f"min_events={min_events}\n")
    return flagged


# --------------------------------------------------------------------------- #
# Live wiring (lazy imports so --selftest needs no network / third-party deps) #
# --------------------------------------------------------------------------- #
def _live_up_set():
    """UP = present in the NDBC wave-station roster (reused verbatim from
    pipeline.enrichment.geodata). Empty set (all DOWN) on any failure."""
    try:
        from pipeline.enrichment.geodata import load_ndbc_wave_stations
        return {str(st["id"]).lower() for st in load_ndbc_wave_stations()}
    except Exception as e:  # noqa: BLE001
        print(f"warn: NDBC wave-station roster unavailable ({type(e).__name__}: {e}); "
              "treating all buoys as DOWN", file=sys.stderr)
        return set()


def _live_swell_fn(now=None):
    """SWELL-event probe per buoy, reusing the pure spectral reader (ndbc_spectral.by_hour →
    .data_spec/.swdir) + the gate's per-hour precondition (nwps_nearshore._swell_precondition).
    Returns {n_qualifying, current_swell_hs, current_frac, has_event} over the recent window.
    A quiet/absent spectrum → has_event False (never a fabricated event)."""
    from pipeline.forecast import ndbc_spectral as sp
    from pipeline.forecast.nwps_nearshore import _swell_precondition
    now = now or datetime.now(timezone.utc)
    now_eh = int(now.timestamp() // 3600)

    def fn(buoy_id):
        try:
            metrics = sp.by_hour(buoy_id)   # {epoch_hour: {hs_swell, swell_frac, ...}} or {}
        except Exception as e:  # noqa: BLE001
            print(f"warn: buoy {buoy_id} spectra unavailable ({type(e).__name__}: {e})", file=sys.stderr)
            metrics = {}
        recent = sorted(((eh, m) for eh, m in metrics.items() if 0 <= now_eh - eh < MONITOR_SWELL_WINDOW_H),
                        reverse=True)
        qual = [m for _, m in recent if _swell_precondition(m.get("hs_swell"), m.get("swell_frac"))]
        cur = recent[0][1] if recent else {}
        return {"n_qualifying": len(qual), "current_swell_hs": cur.get("hs_swell"),
                "current_frac": cur.get("swell_frac"),
                "has_event": len(qual) >= MONITOR_MIN_QUALIFYING_HOURS}
    return fn


def _live_pairing_fn():
    """Buoy pairing verdict, reusing the --pairing-audit scorer verbatim (single source of truth)."""
    from pipeline.forecast.nwps_nearshore import _score_pairing, _ndbc_station_meta

    def fn(buoy_id):
        try:
            return _score_pairing(_ndbc_station_meta(buoy_id))[0]
        except Exception:  # noqa: BLE001
            return "VALID REFERENCE"   # unknown metadata → don't block the heads-up; the gate decides
    return fn


def _live_rolling_fn(now=None):
    """Rolling DIRECTION verdict per zone from the gate's accumulated history (the JSONL under
    pipeline/forecast_data/). Reuses load_trust_history + rolling_trust_verdict + the zone's tier.
    When no history is present in this env (the log is Mac-side / gitignored) every zone reads
    0/N ACCUMULATING — the honest default (it needs its first events)."""
    from pipeline.forecast.nwps_nearshore import (
        load_trust_history, rolling_trust_verdict, _zone_tiers, TRUST_ROLLING_DAYS)
    now = now or datetime.now(timezone.utc)
    now_eh = int(now.timestamp() // 3600)

    def fn(wfo, buoy):
        try:
            _, tier = _zone_tiers(wfo, buoy)
        except Exception:  # noqa: BLE001
            tier = "point"
        recs = load_trust_history(wfo, buoy, days=TRUST_ROLLING_DAYS[0], now_epoch_hour=now_eh)
        v = rolling_trust_verdict(recs, tier=tier)
        return {"n_events": v.get("n_events", 0), "verdict": v.get("verdict", "ACCUMULATING")}
    return fn


def _live_retired_set():
    """{(wfo, buoy)} whose buoy is RETIRED as a reference (both axes) — read from the assignment
    file's buoy_reference.retired (44098's box/gyx zones). Never flagged."""
    try:
        from pipeline.forecast.nwps_nearshore import _retired_reference_zones
        return set(_retired_reference_zones().keys())
    except Exception:  # noqa: BLE001
        return set()


def _min_events():
    try:
        from pipeline.forecast.nwps_nearshore import TRUST_MIN_EVENTS
        return TRUST_MIN_EVENTS
    except Exception:  # noqa: BLE001
        return 5


def run():
    up_set = _live_up_set()
    results = evaluate(WATCH, up_set, _live_swell_fn(), _live_pairing_fn(),
                       _live_rolling_fn(), _live_retired_set())
    me = _min_events()
    _print_summary(results, me)
    _emit_github_output(results, me)
    return 0


# --------------------------------------------------------------------------- #
# Offline selftest (injected stubs; no network / no third-party deps)          #
# --------------------------------------------------------------------------- #
def _selftest():
    # A genuine swell EVENT vs the issue-#58 shape (a swell-POOR sea under a big wind-chop:
    # high total-Hs range but only ~0.15 m of swell → NOT an event under the new precondition).
    EVENT = {"n_qualifying": 5, "current_swell_hs": 1.4, "current_frac": 0.72, "has_event": True}
    CHOP = {"n_qualifying": 0, "current_swell_hs": 0.15, "current_frac": 0.12, "has_event": False}
    swell = {"44097": EVENT, "44025": EVENT, "44091": EVENT, "44013": EVENT,
             "44098": EVENT,  # even WITH a real swell, retired 44098 must never flag
             "44095": CHOP, "41025": CHOP, "41108": CHOP, "41110": CHOP}  # the four #58 zones
    pairing = {"44097": "VALID REFERENCE", "44091": "VALID REFERENCE", "44025": "MARGINAL",
               "44013": "STRUCTURALLY INVALID"}   # 44098 is retired (excluded before pairing)
    rolling = {"44091": {"n_events": 6, "verdict": "PASS"}}   # settled PASS → don't nag
    retired = {("box", "44098"), ("gyx", "44098")}
    swell_fn = lambda bid: swell.get(bid, CHOP)
    pairing_fn = lambda bid: pairing.get(bid, "VALID REFERENCE")
    rolling_fn = lambda wfo, bid: rolling.get(bid, {"n_events": 1, "verdict": "ACCUMULATING"})
    watch = [
        {"id": "44097", "zone": "RI south coast", "wfo": "box"},          # VALID + event + ACCUM → FLAG
        {"id": "44025", "zone": "Monmouth", "wfo": "phi"},                # MARGINAL + event + ACCUM → FLAG
        {"id": "44091", "zone": "Ocean County", "wfo": "phi"},            # VALID + event but rolling PASS → no
        {"id": "44013", "zone": "Mass Bay", "wfo": "box"},                # STRUCTURALLY INVALID → no
        {"id": "44098", "zone": "North Shore", "wfo": "box"},             # retired (both axes) → no
        {"id": "44098", "zone": "NH coast", "wfo": "gyx"},               # retired (shared id, other wfo) → no
        {"id": "44095", "zone": "Northern OBX", "wfo": "mhx"},            # #58 chop → no
        {"id": "41025", "zone": "Cape Hatteras", "wfo": "mhx"},           # #58 chop → no
        {"id": "41108", "zone": "Brunswick Is", "wfo": "ilm"},           # #58 chop → no
        {"id": "41110", "zone": "Wrightsville", "wfo": "ilm"},           # #58 chop → no
        {"id": "44009", "zone": "Absecon", "wfo": "phi"},                # DOWN (absent from up_set) → no
    ]
    up = {"44097", "44025", "44091", "44013", "44098", "44095", "41025", "41108", "41110"}  # 44009 DOWN
    res = evaluate(watch, up, swell_fn, pairing_fn, rolling_fn, retired, now=datetime(2026, 7, 3, tzinfo=timezone.utc))
    by = {(r["id"], r["wfo"]): r for r in res}
    flagged = {f'{f["wfo"]}/{f["id"]}' for f in _flagged(res)}

    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    check("VALID buoy + swell event + ACCUMULATING → FLAGGED (box/44097)", by[("44097", "box")]["flag"] is True)
    check("MARGINAL buoy + swell event + ACCUMULATING → FLAGGED (phi/44025)", by[("44025", "phi")]["flag"] is True)
    # THE issue-#58 fix: high-range but swell-POOR seas do NOT flag anymore
    for bid, wfo in [("44095", "mhx"), ("41025", "mhx"), ("41108", "ilm"), ("41110", "ilm")]:
        check(f"#58 swell-poor chop NOT flagged ({wfo}/{bid})", by[(bid, wfo)]["flag"] is False)
    check("RETIRED buoy 44098 NEVER flagged even with a real swell (box)", by[("44098", "box")]["flag"] is False)
    check("RETIRED buoy 44098 NEVER flagged even with a real swell (gyx)", by[("44098", "gyx")]["flag"] is False)
    check("STRUCTURALLY INVALID buoy NOT flagged (box/44013)", by[("44013", "box")]["flag"] is False)
    check("settled rolling PASS NOT nagged (phi/44091 has event but PASS)", by[("44091", "phi")]["flag"] is False)
    check("DOWN buoy NOT flagged (phi/44009)", by[("44009", "phi")]["flag"] is False and by[("44009", "phi")]["up"] is False)
    check("exactly the two usable+ACCUMULATING zones are flagged", flagged == {"box/44097", "phi/44025"})
    # retired / invalid never leak into flagged_json (the issue set-key)
    check("retired + invalid excluded from flagged_json", not (flagged & {"box/44098", "gyx/44098", "box/44013"}))
    # lazy fetch discipline: pairing/rolling are only consulted once a swell event exists (cost control)
    calls = {"pair": 0, "roll": 0}
    def _p(bid): calls["pair"] += 1; return pairing.get(bid, "VALID REFERENCE")
    def _r(wfo, bid): calls["roll"] += 1; return rolling.get(bid, {"n_events": 1, "verdict": "ACCUMULATING"})
    evaluate(watch, up, swell_fn, _p, _r, retired, now=datetime(2026, 7, 3, tzinfo=timezone.utc))
    check("pairing_fn called only for UP+event+not-retired buoys (no wasted fetches)", calls["pair"] == 4)
    check("rolling_fn called only after a usable pairing (no wasted fetches)", calls["roll"] == 3)

    print("\nself-test:",
          "ALL PASS — swell-event trigger; #58 chop excluded; invalid/retired never flag; PASS not nagged."
          if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--selftest", action="store_true", help="offline fixture test (no third-party deps)")
    a = ap.parse_args(argv)
    return _selftest() if a.selftest else run()


if __name__ == "__main__":
    raise SystemExit(main())
