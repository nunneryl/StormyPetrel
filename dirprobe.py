#!/usr/bin/env python3
"""THROWAWAY diagnostic — do NOT merge to main. Read-only.

For ANY NWPS grid + buoy + spot list, put three views side by side so we can tell
whether a model swell-direction bias lives at the buoy cell or across the spots:
  1. model dirpw + swh sampled at the buoy (where trust_check samples),
  2. model dirpw + swh at each requested spot node (nwps_node_lat/lng if present,
     else the spot's lat/lng),
  3. model dirpw + swh at points stepped seaward (south) of the buoy, toward open ocean,
  and — NEW — what the BUOY itself is actually reporting over the last few hours
  (WVHT / MWD / swell_dir_deg), from the same NDBC feed the trust check uses.

Reuses existing helpers only (load_cycle / _nearest_cell / _node_value / _haversine_km
/ _slug / ENRICHED / _buoy_latlng / _buoy_hourly) — no new fetch code, tags nothing.
Needs NOMADS + NDBC + cfgrib/eccodes, so run it on the Mac:

    python3 dirprobe.py --wfo box --buoy 44097 \
        --slugs point-judith,narragansett-beach,matunuck,misquamicut-state-beach
    python3 dirprobe.py --wfo gyx --buoy 44098 --slugs hampton-beach,jenness-beach --fh 0
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Find the repo root so `import pipeline...` resolves, whether this file lives in the
# repo or is copied elsewhere and run from within a checkout.
_start = Path(__file__).resolve()
REPO = None
for base in (Path.cwd(), *Path.cwd().parents, *_start.parents):
    if (base / "pipeline" / "forecast" / "nwps_nearshore.py").exists():
        REPO = base
        break
if REPO is None:
    sys.exit("run this from inside the StormyPetrel checkout (couldn't find pipeline/)")
sys.path.insert(0, str(REPO))

from pipeline.forecast.nwps_nearshore import (   # noqa: E402
    load_cycle, _nearest_cell, _node_value, _slug, ENRICHED, _buoy_latlng, _buoy_hourly,
)

SEAWARD_STEPS = (0.1, 0.2, 0.3)   # degrees south of the buoy, toward open ocean
FAR_NODE_KM = 3.0                 # warn if the buoy's nearest wet node is beyond this


def _resolve_spots(slugs):
    """(label, lat, lng, src) per requested slug; None lat when absent from roster.
    Uses nwps_node_lat/lng when the spot carries them, else the spot's own lat/lng."""
    roster = {}
    for s in json.loads(ENRICHED.read_text()):
        roster.setdefault(_slug(s.get("name")), s)
        if s.get("slug"):
            roster.setdefault(s["slug"], s)
    pts = []
    for slug in slugs:
        s = roster.get(slug)
        if not s:
            pts.append((f"{slug} (NOT in roster)", None, None, "-"))
            continue
        nlat, nlng = s.get("nwps_node_lat"), s.get("nwps_node_lng")
        if nlat is not None and nlng is not None:
            pts.append((slug, float(nlat), float(nlng), "nwps_node"))
        else:
            pts.append((slug, float(s["lat"]), float(s["lng"]), "spot lat/lng"))
    return pts


def _sample(cyc, fh, lat, lng):
    """(node_lat, node_lng, dist_km, dirpw, swh) at the nearest wet cell, or None."""
    cell = _nearest_cell(cyc, lat, lng)
    if not cell:
        return None
    i, j = cell[0], cell[1]
    return (float(cyc["lats"][i, j]), float(cyc["lons"][i, j]), cell[2],
            _node_value(cyc, "dirpw", fh, i, j), _node_value(cyc, "swh", fh, i, j))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wfo", default="box", help="NWPS grid to sample")
    ap.add_argument("--buoy", default="44097", help="NDBC buoy id (coords via _buoy_latlng)")
    ap.add_argument("--slugs", default="", help="comma-separated spot slugs from spots_enriched.json")
    ap.add_argument("--fh", type=int, default=0, help="forecast hour to sample (default f000)")
    a = ap.parse_args()

    # Buoy coords come from NDBC active-station metadata — never hardcoded.
    try:
        blat, blng = _buoy_latlng(a.buoy)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"could not resolve buoy {a.buoy} coords via _buoy_latlng "
                 f"({type(e).__name__}: {e}) — needs the NDBC roster (run on the Mac)")

    slugs = [s.strip() for s in a.slugs.split(",") if s.strip()]
    points = [(f"{a.buoy} buoy (trust_check samples here)", blat, blng, "given (buoy)")]
    points += _resolve_spots(slugs)
    for d in SEAWARD_STEPS:
        points.append((f"seaward {d:.1f} deg S of {a.buoy}", round(blat - d, 4), blng, "offset"))

    try:
        cyc = load_cycle(a.wfo)   # cycle=None -> find_latest_cycle(wfo); needs NOMADS+cfgrib
    except Exception as e:  # noqa: BLE001
        cyc = None
        print(f"\n[!] could not load a live {a.wfo} cycle ({type(e).__name__}: {e}).")
        print("    This probe needs NOMADS + cfgrib/eccodes — run it on the Mac.")
        print("    (Buoy coords + spot resolution below are still real; model columns are '—'.)")

    fh = a.fh
    if cyc and fh not in cyc["steps"]:
        fh = min(cyc["steps"])
        print(f"\n[i] fh {a.fh} not in cycle; using nearest available fh={fh}")

    when = f"cycle {cyc['cycle_dt']:%Y-%m-%d %HZ}" if cyc else "(no live cycle — Mac only)"
    print(f"\nNWPS {a.wfo.upper()} dirpw / swh probe vs buoy {a.buoy} @ {blat:.4f},{blng:.4f}"
          f"  —  fh={fh}  ·  {when}")
    hdr = (f"  {'label':46}{'req lat,lng':21}{'node lat,lng':21}"
           f"{'dist_m':>7}{'dirpw':>7}{'swh_m':>7}  src")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    buoy_node_dist_km = None
    for idx, (label, lat, lng, src) in enumerate(points):
        if lat is None:
            print(f"  {label:46}{'—':21}{'—':21}{'—':>7}{'—':>7}{'—':>7}  {src}")
            continue
        req = f"{lat:.4f},{lng:.4f}"
        if not cyc:
            print(f"  {label:46}{req:21}{'—':21}{'—':>7}{'—':>7}{'—':>7}  {src}")
            continue
        s = _sample(cyc, fh, lat, lng)
        if s is None:
            print(f"  {label:46}{req:21}{'no wet cell':21}{'—':>7}{'—':>7}{'—':>7}  {src}")
            continue
        nlat, nlng, dkm, dirpw, swh = s
        if idx == 0:                      # the buoy row is always points[0]
            buoy_node_dist_km = dkm
        node = f"{nlat:.4f},{nlng:.4f}"
        dd = f"{dirpw:.0f}" if dirpw is not None else "—"
        sh = f"{swh:.2f}" if swh is not None else "—"
        print(f"  {label:46}{req:21}{node:21}{round(dkm * 1000):>7}{dd:>7}{sh:>7}  {src}")

    # Grid-coverage warning: a distant nearest node means the grid barely covers the buoy.
    if buoy_node_dist_km is not None and buoy_node_dist_km > FAR_NODE_KM:
        print(f"\n[!] WARNING: buoy {a.buoy}'s nearest {a.wfo} wet node is {buoy_node_dist_km:.2f} km "
              f"away (> {FAR_NODE_KM:.0f} km) — the grid barely covers this point; the model sample "
              "at the buoy may be unreliable.")

    # What the BUOY itself is reporting (same NDBC feed the trust check uses).
    print(f"\n  buoy {a.buoy} — latest reported (NDBC realtime2; the feed _buoy_hourly reads):")
    try:
        bh = _buoy_hourly(a.buoy)
    except Exception as e:  # noqa: BLE001
        bh = None
        print(f"    (buoy feed unavailable: {type(e).__name__}: {e} — needs NDBC egress on the Mac)")
    if bh:
        print(f"      {'hour (UTC)':17}{'WVHT_m':>8}{'MWD':>7}{'swell_dir_deg':>15}")
        for hb in sorted(bh, reverse=True)[:6]:   # most recent few hours
            hs, mwd, swd = bh[hb]
            ts = datetime.fromtimestamp(hb * 3600, timezone.utc).strftime("%Y-%m-%d %HZ")
            hs_s = f"{hs:.2f}" if hs is not None else "—"
            mwd_s = f"{mwd:.0f}" if mwd is not None else "—"
            swd_s = f"{swd:.0f}" if swd is not None else "—"
            print(f"      {ts:17}{hs_s:>8}{mwd_s:>7}{swd_s:>15}")
    elif bh is not None:
        print("    (no hourly obs parsed)")


if __name__ == "__main__":
    main()
