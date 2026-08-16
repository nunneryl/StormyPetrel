#!/usr/bin/env python3
"""
nwps_okx_probe.py - read-only diagnostic for the NWPS OKX pilot.
Runs on the Mac (needs live NOMADS egress). No DB writes, no pipeline changes.

Discovers the latest OKX run by WALKING the real NOMADS directory listing
(no guessed filenames), downloads the CG1 nearshore field file, prints the
field inventory + grid spacing, and samples each pilot spot's nearest node
for Hs / peak period / mean direction at f000.

NOMADS NWPS layout (per NWS SCN17-84):
  /pub/data/nccf/com/nwps/prod/er.YYYYMMDD/okx/CC/CG1/okx_nwps_CG1_YYYYMMDD_HH00.grib2
  (CG0 also carries a separate ..._Trkng_... tracking file; we want the plain field file.)

Sampling needs pygrib (`pip install pygrib`). Without it, the file is still
downloaded and its size/message count reported.

Save the query-4 JSON beside this script as okx_pilot.json. If absent, a 3-spot
fallback (Rockaway, Lido, Montauk Point) is used.
"""
import sys, os, re, json, math, urllib.request

PROD = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/"
WFO = "okx"
REGION = "er"
FALLBACK_SPOTS = [
    {"slug": "rockaway-beach", "lat": 40.58329,  "lng": -73.806882, "nwps_wfo": "okx"},
    {"slug": "lido-beach",     "lat": 40.583714, "lng": -73.606746, "nwps_wfo": "okx"},
    {"slug": "montauk-point",  "lat": 41.071004, "lng": -71.855135, "nwps_wfo": "okx"},
]

def load_spots():
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "okx_pilot.json")
    if os.path.exists(p):
        return json.load(open(p))
    print("okx_pilot.json not found; using 3-spot fallback.\n")
    return FALLBACK_SPOTS

def http_get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "stormy-petrel-probe"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def listdir(url):
    """Return href names from an Apache directory index; [] on failure."""
    try:
        html = http_get(url, timeout=60).decode("utf-8", "replace")
    except Exception as e:
        print(f"  listdir {url}: {e}")
        return []
    return re.findall(r'href="([^"?][^"]*)"', html)

def find_latest_run(spots_present_only=True):
    """Walk prod -> latest er.DATE with an okx run -> latest cycle -> CG1 file URL."""
    names = listdir(PROD)
    dates = sorted({m for n in names for m in re.findall(rf'^{REGION}\.(\d{{8}})/$', n)},
                   reverse=True)
    if not dates:
        print("  no er.YYYYMMDD directories found under prod.")
        return None
    for date in dates[:4]:                       # look back a few days max
        wfo_url = f"{PROD}{REGION}.{date}/{WFO}/"
        cycles = sorted({c for n in listdir(wfo_url)
                         for c in re.findall(r'^(\d\d)/$', n)}, reverse=True)
        for cc in cycles:
            cg1_url = f"{wfo_url}{cc}/CG1/"
            files = [n for n in listdir(cg1_url)
                     if n.endswith(".grib2") and "Trkng" not in n and "CG1" in n]
            if files:
                f = sorted(files)[-1]
                print(f"latest OKX run: {date} {cc}Z  ->  {f}\n")
                return cg1_url + f
        if cycles:
            print(f"  {date}: okx cycles {cycles} present but no CG1 field file yet")
        else:
            print(f"  {date}: no okx run")
    return None

def haversine_km(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dphi = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(min(1, math.sqrt(x)))

def report(path, spots):
    try:
        import pygrib
    except Exception as e:
        n = os.path.getsize(path)
        print(f"saved {n} bytes; pygrib not installed ({e}).")
        print("install with: pip install pygrib   (then rerun)")
        return
    grbs = pygrib.open(path)
    seen = {}
    for g in grbs:
        seen.setdefault(g.shortName, g.name)
    print("fields present (shortName -> name):")
    for k, name in sorted(seen.items()):
        print(f"  {k:10s} {name}")
    grbs.seek(0)
    steps = sorted({g.forecastTime for g in grbs})
    print(f"\nforecast steps: {steps[:6]}{' ...' if len(steps) > 6 else ''} (n={len(steps)})")

    def first(short):
        grbs.seek(0)
        for g in grbs:
            if g.shortName == short and g.forecastTime == 0:
                return g
        return None
    # pygrib reports eccodes short names (lowercase CF style), not NCEP abbreviations:
    #   swh  = sig. height of combined wind waves + swell (headline Hs)
    #   shts = sig. height of total swell (swell only, excludes wind sea)
    #   perpw= primary wave period (labeled "mean"; verify vs peak before rating)
    #   dirpw= primary wave direction (deg, direction FROM)
    hs, swl, tp, dr = first("swh"), first("shts"), first("perpw"), first("dirpw")
    if hs is None:
        print("\nno swh f000 message; see inventory above for the real height name.")
        return
    import numpy as np
    lats, lons = hs.latlons()
    sp = haversine_km(float(lats[0,0]), float(lons[0,0]),
                      float(lats[1,0]) if lats.shape[0] > 1 else float(lats[0,0]),
                      float(lons[0,0]))
    print(f"\nCG1 grid spacing ~ {sp:.2f} km   grid shape {lats.shape}\n")
    hv = hs.values; wv = swl.values if swl else None
    tv = tp.values if tp else None; dv = dr.values if dr else None
    mask = np.ma.getmaskarray(hv) if np.ma.isMaskedArray(hv) else np.zeros(hv.shape, bool)

    all_nodes = [(float(lats[i,j]), float(lons[i,j]), i, j)
                 for i in range(lats.shape[0]) for j in range(lats.shape[1])]
    wet_nodes = [n for n in all_nodes if not mask[n[2], n[3]]]
    print(f"grid: {len(all_nodes)} cells, {len(wet_nodes)} wet ({len(all_nodes)-len(wet_nodes)} land/masked)\n")

    far_cap = 2.0 * sp
    land_snaps = 0
    print(f"{'slug':24s} {'wfo':4s} {'wet_km':>6s} {'swh_m':>5s} "
          f"{'swl_m':>5s} {'per_s':>5s} {'dir':>4s}  flag")
    for s in spots:
        raw = min(all_nodes, key=lambda n: haversine_km(s['lat'], s['lng'], n[0], n[1]))
        if mask[raw[2], raw[3]]:
            land_snaps += 1
        best = min(wet_nodes, key=lambda n: haversine_km(s['lat'], s['lng'], n[0], n[1]))
        d_km = haversine_km(s['lat'], s['lng'], best[0], best[1]); i, j = best[2], best[3]
        def v(arr):
            try: return float(arr[i, j])
            except Exception: return float('nan')
        nan = float('nan')
        swh = v(hv); swl_ = v(wv) if wv is not None else nan
        per = v(tv) if tv is not None else nan; di = v(dv) if dv is not None else nan
        flag = ''
        if swh < 0.05 and (per < 2 or per != per):
            flag = 'DEAD?'        # dead energy -> likely sheltered/back-bay cell
        elif d_km > far_cap:
            flag = 'FAR'          # stepped well past land; lower confidence
        print(f"{s['slug']:24s} {s.get('nwps_wfo','?'):4s} {d_km:6.2f} "
              f"{swh:5.1f} {swl_:5.1f} {per:5.1f} {di:4.0f}  {flag}")
    print(f"\nnaive nearest-cell was land for {land_snaps}/{len(spots)} spots "
          f"(now snapped to nearest wet cell instead).")

def main():
    print("=== NWPS OKX probe (read-only) ===\n")
    spots = load_spots()
    print(f"{len(spots)} pilot spots loaded.\n")
    url = find_latest_run()
    if not url:
        print("\nNo OKX CG1 file found in the last few days. OKX may not have run "
              "(on-demand), or the layout changed. Stop here and report this.")
        return 1
    out = "okx_CG1_latest.grib2"
    print(f"downloading -> {out}")
    body = http_get(url)
    if body[:4] != b"GRIB":
        print(f"  not GRIB ({len(body)} bytes); first bytes {body[:40]!r}")
        return 1
    open(out, "wb").write(body)
    print(f"  saved {len(body)} bytes\n")
    report(out, spots)
    return 0

if __name__ == "__main__":
    sys.exit(main())
