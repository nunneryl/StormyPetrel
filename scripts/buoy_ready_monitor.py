#!/usr/bin/env python3
"""Scheduled buoy-readiness monitor (READ-ONLY on public NDBC data).

Watches a small set of NDBC buoys and reports which phi trust-check ZONES are
actually testable right now. "Testable" means the buoy is UP *and* its
significant-wave-height (Hs) has moved enough over the last ~24h that the NWPS
trust gate (``pipeline.forecast.nwps_nearshore --trustcheck``) can return
PASS/FAIL instead of just INCONCLUSIVE. UP alone never counts.

Guardrails (by construction):
  * READ-ONLY on public NDBC data. No Supabase, no prod DB, no NOMADS, no secrets.
  * UP/down reuses ``pipeline.enrichment.geodata.load_ndbc_wave_stations`` — a buoy
    is UP iff it is present in that wave-station roster.
  * Hs observations reuse the SAME NDBC realtime2 fetch+parse stack that
    ``trust_check`` -> ``_buoy_hourly`` uses: ``pipeline.forecast.buoys._fetch_text``
    + ``_parse_realtime2`` + ``_STD_FIELDS``. (We call the buoys helpers directly
    rather than ``nwps_nearshore._buoy_hourly`` so the monitor doesn't drag in the
    interpret/numpy import chain — identical parsing, smaller dependency surface.)

"READY" (the alert trigger, NOT mere UP):
  UP and (24h Hs range >= READY_HS_RANGE_M). READY_HS_RANGE_M mirrors the trust
  gate's own TRUST_BUOY_RANGE_MIN_M, so "ready" means the gate won't just report
  INCONCLUSIVE.

Outputs:
  * a human-readable summary to stdout (for the Actions log), and
  * ``any_ready`` + ``ready_json`` (id/zone/current-Hs) + ``ready_hs_range`` to
    ``$GITHUB_OUTPUT`` for the workflow's issue logic.

    python3 scripts/buoy_ready_monitor.py             # live (needs public NDBC egress)
    python3 scripts/buoy_ready_monitor.py --selftest   # offline fixture test (no network)
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
# Readiness constants                                                         #
# --------------------------------------------------------------------------- #
READY_HS_RANGE_M = 0.5   # mirror nwps_nearshore.TRUST_BUOY_RANGE_MIN_M — below this
                         # Hs span the trust gate reports INCONCLUSIVE (not testable)
WINDOW_H = 24            # look back ~24h for the Hs range

# --------------------------------------------------------------------------- #
# Watch list — add buoys / a future box region by editing this ONE list.      #
# --------------------------------------------------------------------------- #
WATCH = [
    # phi — Mid-Atlantic / NJ
    {"id": "44025", "zone": "Monmouth", "wfo": "phi"},
    {"id": "44065", "zone": "Monmouth", "wfo": "phi"},
    {"id": "44091", "zone": "Ocean County + LBI (interim Absecon)", "wfo": "phi"},
    {"id": "44009", "zone": "Absecon->Cape May", "wfo": "phi"},
    {"id": "44084", "zone": "Delaware", "wfo": "phi"},
    # box — Southern New England. 44098 and 44018 are UNCONFIRMED candidates:
    # validate them with the first box --trustcheck (they may be offline or sited
    # too far offshore to track the surf zones listed).
    {"id": "44097", "zone": "RI south coast (Point Judith to Misquamicut, Newport, Block Island)", "wfo": "box"},
    {"id": "44013", "zone": "Massachusetts Bay (Boston / inner North Shore)", "wfo": "box"},
    {"id": "44098", "zone": "North of Boston (Salisbury / Plum Island / Gloucester) — candidate", "wfo": "box"},
    {"id": "44018", "zone": "Outer Cape + Islands — candidate, may be offline", "wfo": "box"},
]

_REALTIME2 = "https://www.ndbc.noaa.gov/data/realtime2"   # public NDBC, read-only


# --------------------------------------------------------------------------- #
# Pure readiness logic (no network / no third-party deps — used by --selftest) #
# --------------------------------------------------------------------------- #
def _hs_stats(obs, now=None):
    """(current_hs, hs_range_24h) from a realtime2 observation list (newest-first,
    the shape ``pipeline.forecast.buoys._parse_realtime2`` returns). current = the
    newest non-null wave_height_m; range = max-min of wave_height_m over the last
    WINDOW_H hours. Returns (None, None) when there are no wave heights."""
    if not obs:
        return None, None
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=WINDOW_H)
    hs_recent = []
    for o in obs:
        hs = o.get("wave_height_m")
        if hs is None:
            continue
        try:
            t = datetime.fromisoformat(o["time"])
        except (KeyError, ValueError):
            continue
        if t >= cutoff:
            hs_recent.append(hs)
    current = next((o.get("wave_height_m") for o in obs
                    if o.get("wave_height_m") is not None), None)
    hs_range = (max(hs_recent) - min(hs_recent)) if hs_recent else None
    return current, hs_range


def evaluate(watch, up_set, fetch_obs, now=None):
    """Readiness per watched buoy. *up_set* = lowercased UP buoy ids; *fetch_obs(id)*
    -> realtime2 obs list (or None). READY = UP and 24h Hs range >= READY_HS_RANGE_M
    — UP alone never yields ready. Pure: inject *up_set* / *fetch_obs* / *now* for
    offline testing."""
    results = []
    for b in watch:
        bid = b["id"]
        up = bid.lower() in up_set
        current_hs = hs_range = None
        if up:
            current_hs, hs_range = _hs_stats(fetch_obs(bid) or [], now=now)
        ready = bool(up and hs_range is not None and hs_range >= READY_HS_RANGE_M)
        results.append({"id": bid, "zone": b["zone"], "wfo": b.get("wfo"), "up": up,
                        "current_hs": current_hs, "hs_range_24h": hs_range, "ready": ready})
    return results


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


def _live_fetch_obs(buoy_id):
    """Reuse the NDBC realtime2 fetch+parse stack trust_check -> _buoy_hourly uses
    (pipeline.forecast.buoys). Returns a newest-first obs list, or [] on failure."""
    try:
        from pipeline.forecast.buoys import _fetch_text, _parse_realtime2, _STD_FIELDS
    except Exception as e:  # noqa: BLE001
        print(f"warn: buoys parser unavailable ({type(e).__name__}: {e})", file=sys.stderr)
        return []
    try:
        txt = _fetch_text(f"{_REALTIME2}/{buoy_id.upper()}.txt", buoy_id, "std", use_cache=False)
    except Exception as e:  # noqa: BLE001
        print(f"warn: buoy {buoy_id} fetch failed ({type(e).__name__}: {e})", file=sys.stderr)
        return []
    return _parse_realtime2(txt, _STD_FIELDS) if txt else []


# --------------------------------------------------------------------------- #
# Output                                                                      #
# --------------------------------------------------------------------------- #
def _print_summary(results):
    print(f"=== buoy-ready monitor — READY = UP and 24h Hs range >= {READY_HS_RANGE_M} m ===")
    print(f"  {'buoy':7}{'wfo':5}{'zone':38}{'state':6}{'Hs(m)':>7}{'24h rng':>9}  ready")
    for r in results:
        hs = f"{r['current_hs']:.2f}" if r["current_hs"] is not None else "—"
        rng = f"{r['hs_range_24h']:.2f}" if r["hs_range_24h"] is not None else "—"
        print(f"  {r['id']:7}{(r.get('wfo') or '—'):5}{r['zone']:38}{('UP' if r['up'] else 'DOWN'):6}"
              f"{hs:>7}{rng:>9}  {'READY' if r['ready'] else '—'}")
    ready = [r for r in results if r["ready"]]
    if ready:
        zones = ", ".join(sorted({r["zone"] for r in ready}))
        print(f"\n{len(ready)} buoy(s) READY across zone(s): {zones}")
    else:
        print("\nno zones ready")


def _emit_github_output(results):
    """Write any_ready / ready_json / ready_hs_range to $GITHUB_OUTPUT (single JSON
    line). Returns the ready list (also handy for tests)."""
    ready = [{"id": r["id"], "zone": r["zone"], "wfo": r.get("wfo"),
              "hs": round(r["current_hs"], 2) if r["current_hs"] is not None else None}
             for r in results if r["ready"]]
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"any_ready={'true' if ready else 'false'}\n")
            f.write("ready_json=" + json.dumps(ready, separators=(",", ":"), ensure_ascii=False) + "\n")
            f.write(f"ready_hs_range={READY_HS_RANGE_M}\n")
    return ready


def run():
    up_set = _live_up_set()
    results = evaluate(WATCH, up_set, _live_fetch_obs)
    _print_summary(results)
    _emit_github_output(results)
    return 0


# --------------------------------------------------------------------------- #
# Offline selftest (canned obs; no network)                                   #
# --------------------------------------------------------------------------- #
def _mk_obs(hs_oldest_to_newest, now):
    """Build a realtime2-style obs list (newest-first) from hourly Hs values."""
    n = len(hs_oldest_to_newest)
    rows = [{"time": (now - timedelta(hours=(n - 1 - i))).isoformat(), "wave_height_m": hs}
            for i, hs in enumerate(hs_oldest_to_newest)]
    rows.sort(key=lambda r: r["time"], reverse=True)
    return rows


def _selftest():
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)   # fixed 'now' for determinism
    rising = [round(0.2 + (0.9 - 0.2) * i / 23, 3) for i in range(24)]   # 0.2 -> 0.9 (range 0.7)
    flat = [round(0.3 + (0.4 - 0.3) * i / 23, 3) for i in range(24)]     # 0.3 -> 0.4 (range 0.1)
    fixtures = {"44025": _mk_obs(rising, now),   # phi, UP, rising -> READY
                "44065": _mk_obs(flat, now),     # phi, UP, flat   -> NOT ready
                "44097": _mk_obs(rising, now)}   # box, UP, rising -> READY  (44091 absent -> DOWN)
    up_set = {"44025", "44065", "44097"}
    watch = [{"id": "44025", "zone": "Monmouth", "wfo": "phi"},
             {"id": "44065", "zone": "Monmouth", "wfo": "phi"},
             {"id": "44091", "zone": "Ocean County + LBI (interim Absecon)", "wfo": "phi"},
             {"id": "44097", "zone": "RI south coast", "wfo": "box"}]
    res = evaluate(watch, up_set, lambda bid: fixtures.get(bid, []), now=now)
    by = {r["id"]: r for r in res}
    ready_by = {r["id"]: r for r in res if r["ready"]}   # exactly what ready_json is built from

    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    check(f"rising 0.2->0.9m reads READY (24h range {by['44025']['hs_range_24h']:.2f} m)",
          by["44025"]["ready"] is True)
    check(f"flat 0.3->0.4m reads NOT ready (24h range {by['44065']['hs_range_24h']:.2f} m)",
          by["44065"]["ready"] is False)
    check("down buoy reads NOT ready", by["44091"]["ready"] is False and by["44091"]["up"] is False)
    check("UP alone never triggers (flat buoy is UP but NOT ready)",
          by["44065"]["up"] is True and by["44065"]["ready"] is False)
    check("ready box buoy carries wfo 'box' into the ready list",
          "44097" in ready_by and ready_by["44097"]["wfo"] == "box")
    check("ready phi buoy carries wfo 'phi' into the ready list",
          "44025" in ready_by and ready_by["44025"]["wfo"] == "phi")

    print("\nself-test:",
          f"ALL PASS — ready = UP and 24h Hs range >= {READY_HS_RANGE_M} m; UP alone never triggers."
          if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--selftest", action="store_true", help="offline fixture test (no network)")
    a = ap.parse_args(argv)
    return _selftest() if a.selftest else run()


if __name__ == "__main__":
    raise SystemExit(main())
