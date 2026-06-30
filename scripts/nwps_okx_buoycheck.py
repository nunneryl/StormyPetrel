#!/usr/bin/env python3
"""
nwps_okx_buoycheck.py - trust gate for the OKX NWPS pilot (read-only).
Runs on the Mac (needs NOMADS + NDBC egress). No DB writes.

Does OKX NWPS reproduce NDBC buoy 44025 over the available window?
Assembly (NWPS has no long archive; NOMADS keeps ~5 days):
  - pull the last few OKX cycles, take each cycle's ALREADY-ELAPSED forecast
    hours (f000..f_now), sample NWPS Hs+dir at the buoy node, valid = cycle+fh,
  - keep the shortest-lead estimate per valid hour,
  - join to 44025 hourly obs, compute Pearson r (Hs) and circular std of the
    direction offset. Compare to MOP-style thresholds (r>=0.80, circ_std<=25).

If the window is flat (low Hs variance) or has few points, the result is
reported as INCONCLUSIVE rather than pass/fail. Needs pygrib.
"""
import sys, os, re, math, datetime as dt, urllib.request

PROD   = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/"
WFO, REGION = "okx", "er"
BUOY, BLAT, BLNG = "44025", 40.251, -73.164
R_MIN, CIRC_MAX = 0.80, 25.0
MAX_CYCLES = 4           # ~last 2 days; each adds its elapsed hours (~75-100 MB total)

def http_get(url, timeout=180):
    req = urllib.request.Request(url, headers={"User-Agent": "stormy-petrel-buoycheck"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def listdir(url):
    try: html = http_get(url, 60).decode("utf-8", "replace")
    except Exception: return []
    return re.findall(r'href="([^"?][^"]*)"', html)

def haversine_km(a, b, c, d):
    R=6371.0; p1,p2=math.radians(a),math.radians(c)
    dphi=math.radians(c-a); dl=math.radians(d-b)
    x=math.sin(dphi/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(min(1,math.sqrt(x)))

def fetch_buoy(stn):
    txt = http_get(f"https://www.ndbc.noaa.gov/data/realtime2/{stn}.txt").decode().splitlines()
    out = {}
    for ln in txt:
        if ln.startswith("#"): continue
        p = ln.split()
        if len(p) < 12: continue
        try:
            t = dt.datetime(int(p[0]),int(p[1]),int(p[2]),int(p[3]),int(p[4]),
                            tzinfo=dt.timezone.utc).replace(minute=0)
        except Exception: continue
        def f(x):
            try: return float(x)
            except Exception: return None
        wvht, mwd = f(p[8]), f(p[11])
        if wvht is None or wvht >= 90: continue          # MM/99 = missing
        out.setdefault(t, (wvht, mwd if (mwd is not None and mwd < 900) else None))
    return out

def recent_cycles(n):
    names = listdir(PROD)
    dates = sorted({m for x in names for m in re.findall(rf'^{REGION}\.(\d{{8}})/$', x)},
                   reverse=True)
    cyc = []
    for date in dates:
        wfo_url = f"{PROD}{REGION}.{date}/{WFO}/"
        for cc in sorted({c for x in listdir(wfo_url) for c in re.findall(r'^(\d\d)/$', x)},
                         reverse=True):
            files = [x for x in listdir(f"{wfo_url}{cc}/CG1/")
                     if x.endswith(".grib2") and "Trkng" not in x and "CG1" in x]
            if files:
                cyc.append((date, cc, f"{wfo_url}{cc}/CG1/{sorted(files)[-1]}"))
            if len(cyc) >= n: return cyc
    return cyc

def pearson(xs, ys):
    n=len(xs); mx=sum(xs)/n; my=sum(ys)/n
    cov=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    vx=sum((x-mx)**2 for x in xs); vy=sum((y-my)**2 for y in ys)
    return (cov/math.sqrt(vx*vy)) if vx>0 and vy>0 else float("nan"), vx, vy

def circ_std(diffs):
    n=len(diffs)
    s=sum(math.sin(math.radians(d)) for d in diffs)/n
    c=sum(math.cos(math.radians(d)) for d in diffs)/n
    Rbar=math.hypot(s,c)
    return math.degrees(math.sqrt(-2*math.log(Rbar))) if Rbar>1e-9 else float("inf")

def main():
    print("=== NWPS OKX trust check vs NDBC 44025 (read-only) ===\n")
    try: import pygrib
    except Exception as e:
        print(f"pygrib required ({e}); pip install pygrib"); return 1
    import numpy as np

    buoy = fetch_buoy(BUOY)
    print(f"buoy {BUOY}: {len(buoy)} hourly obs over last ~{len(buoy)//24} days")
    cycles = recent_cycles(MAX_CYCLES)
    if not cycles:
        print("no OKX cycles found."); return 1
    print(f"using {len(cycles)} recent cycles: "
          + ", ".join(f"{d} {c}Z" for d,c,_ in cycles) + "\n")

    now = dt.datetime.now(dt.timezone.utc)
    series = {}   # valid_hour -> (nwps_hs, nwps_dir, lead_h)
    node = None
    for date, cc, url in cycles:
        cyc_dt = dt.datetime(int(date[:4]),int(date[4:6]),int(date[6:]),int(cc),
                             tzinfo=dt.timezone.utc)
        elapsed = int((now - cyc_dt).total_seconds()//3600)
        if elapsed < 0: continue
        grbs = pygrib.open(http_save(url))
        if node is None:
            g0 = next(g for g in grbs if g.shortName=="swh" and g.forecastTime==0)
            lats, lons = g0.latlons()
            mask = np.ma.getmaskarray(g0.values) if np.ma.isMaskedArray(g0.values) \
                   else np.zeros(g0.values.shape, bool)
            wet=[(float(lats[i,j]),float(lons[i,j]),i,j)
                 for i in range(lats.shape[0]) for j in range(lats.shape[1]) if not mask[i,j]]
            bn=min(wet, key=lambda n: haversine_km(BLAT,BLNG,n[0],n[1]))
            node=(bn[2],bn[3]); ndist=haversine_km(BLAT,BLNG,bn[0],bn[1])
            print(f"buoy node: CG1 cell {haversine_km(BLAT,BLNG,bn[0],bn[1]):.2f} km from 44025"
                  + ("  [far: buoy may sit outside the CG1 nearshore nest -> consider CG0]"
                     if ndist>5 else "") + "\n")
        grbs.seek(0)
        hs_by_fh={g.forecastTime:g.values[node] for g in grbs
                  if g.shortName=="swh" and g.forecastTime<=elapsed}
        grbs.seek(0)
        dr_by_fh={g.forecastTime:g.values[node] for g in grbs
                  if g.shortName=="dirpw" and g.forecastTime<=elapsed}
        for fh,hs in hs_by_fh.items():
            valid=(cyc_dt+dt.timedelta(hours=fh)).replace(minute=0)
            if valid in series and series[valid][2]<=fh: continue
            series[valid]=(float(hs), float(dr_by_fh.get(fh, float("nan"))), fh)

    pairs=[(series[t][0], buoy[t][0], series[t][1], buoy[t][1])
           for t in sorted(series) if t in buoy]
    print(f"matched NWPS<->buoy hours: {len(pairs)}")
    if len(pairs) < 6:
        print("INCONCLUSIVE: too few overlapping hours (short archive). "
              "Rerun after more cycles accumulate."); return 0
    nhs=[p[0] for p in pairs]; bhs=[p[1] for p in pairs]
    r,vx,vy = pearson(nhs,bhs)
    dpairs=[(p[2]-p[3]) for p in pairs if p[3] is not None and p[2]==p[2]]
    cs = circ_std(dpairs) if dpairs else float("nan")
    print(f"buoy Hs range {min(bhs):.2f}-{max(bhs):.2f} m   "
          f"NWPS Hs range {min(nhs):.2f}-{max(nhs):.2f} m")
    print(f"Hs Pearson r = {r:.3f}   dir circ_std = {cs:.1f} deg   (n_dir={len(dpairs)})\n")
    buoy_range = max(bhs) - min(bhs)
    if buoy_range < 0.5:
        print(f"INCONCLUSIVE: buoy Hs spanned only {buoy_range:.2f} m over the window "
              "(flat spell). r is noise, not signal; the sign/magnitude mean nothing here. "
              "Rerun after a swell with >~0.5 m Hs range.")
    elif r>=R_MIN and cs<=CIRC_MAX:
        print(f"PASS: OKX NWPS tracks 44025 (r>={R_MIN}, circ_std<={CIRC_MAX}). "
              "Regional trust supports consuming the placed spots.")
    else:
        print(f"FAIL vs thresholds (r>={R_MIN}, circ_std<={CIRC_MAX}). "
              "Hold consume; investigate before tagging.")
    return 0

def http_save(url):
    body=http_get(url)
    p=os.path.join("/tmp", url.rsplit("/",1)[-1])
    open(p,"wb").write(body)
    return p

if __name__ == "__main__":
    sys.exit(main())
