"""Read-only calibration check: the nearshore Hs we published against measured Hs in shallow
water (sw1-raycast mode=face-calibration). Step 2 of the face-height work: before any face
multiplier is chosen, whether the Hs it would multiply is biased.

WHAT IS COMPARED

  ours      the published forecasts row for the spot and hour (source 'nwps', the rated row):
            hs, NWPS's total significant height at the spot's sample point, with swell_hs, tp
            and swell_source carried beside it. A past hour keeps the LAST value published for
            it, so this is the shortest-lead forecast each hour had, not a fixed lead.
            hs IS THE HEIGHT THE FACE IS SIZED FROM ONLY ON THE NWPS OVERRIDE'S HOURS. The
            rater that fed an hour, read off swell_source (RATER):
              NWPS override  (nwps, nwps_height_ww3_dir)  face from this hs
              MOP            (cdip_mop)                   face from MOP's Hs, not stored
              WW3 path       (ww3)                        face from the WW3 partitions, not stored
              base           (anything else)              face from NWPS's swell_hs or this hs
            so on MOP and WW3-path hours this measures NWPS's height beside the one rated.
  measured  the instrument's Hs over the hour: every reading flagged good whose time is within
            30 minutes of the hour, averaged (FRF's 8 m array reads hourly; CDIP buoys every
            30 minutes, so a reading at :30 counts toward both hours it sits between).

  PAIRS (spot -> instrument, depth)
    Duck Pier               FRF 8 m array (15 pressure gauges, ~8 m), 0.7 km
    Bethany Beach           CDIP 263 Bethany Beach (10.6 m), 0.8 km
    Doheny State Beach      CDIP 284 Capistrano Beach nearshore (11.1 m), 1.9 km
    Avila Beach             CDIP 285 Avila Beach nearshore (15.0 m), 1.5 km
    Jetty Park Cocoa Beach  CDIP 143 Cape Canaveral nearshore (9.8 m), 5.5 km
    Cocoa Beach Pier        CDIP 143 Cape Canaveral nearshore (9.8 m), 7.5 km

STATISTICS, per spot, per spot and period band (the MEASURED peak period), per spot and rater,
by band, by rater, and over all pairs

  n             paired hours
  bias_m        mean(ours - measured), metres
  ratio_median  median(ours / measured)
  rmse_m        sqrt(mean((ours - measured)^2))
  scatter_index rmse / mean(measured)
  r             Pearson correlation
  within_20 / within_30   share of hours with ours / measured within 20% / 30% of 1

Nothing is written but the report: stdout, --out (JSON: the summary and every paired hour,
for an accuracy page later) and --csv (the paired hours). Supabase is read with SELECT only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from datetime import datetime, timezone
from statistics import median

FRF_DODS = "https://chldata.erdc.dren.mil/thredds/dodsC/frf/oceanography/waves/"
CDIP_DODS = "https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/realtime/"

INSTRUMENTS = {
    "frf-8m": {"name": "FRF 8 m array, Duck NC", "depth_m": 8.0, "kind": "frf"},
    "cdip-263": {"name": "CDIP 263 Bethany Beach DE", "depth_m": 10.6, "kind": "cdip"},
    "cdip-284": {"name": "CDIP 284 Capistrano Beach nearshore CA", "depth_m": 11.1, "kind": "cdip"},
    "cdip-285": {"name": "CDIP 285 Avila Beach nearshore CA", "depth_m": 15.0, "kind": "cdip"},
    "cdip-143": {"name": "CDIP 143 Cape Canaveral nearshore FL", "depth_m": 9.8, "kind": "cdip"},
}
PAIRS = [
    ("Duck Pier", "frf-8m"),
    ("Bethany Beach", "cdip-263"),
    ("Doheny State Beach", "cdip-284"),
    ("Avila Beach", "cdip-285"),
    ("Jetty Park Cocoa Beach", "cdip-143"),
    ("Cocoa Beach Pier", "cdip-143"),
]
BANDS = [(8.0, "<8 s"), (10.0, "8-10 s"), (12.0, "10-12 s"), (14.0, "12-14 s"), (math.inf, "14+ s")]
HALF_HOUR = 1800.0
RATER = {"nwps": "NWPS override", "nwps_height_ww3_dir": "NWPS override", "cdip_mop": "MOP",
         "ww3": "WW3 path"}
RATERS = ["NWPS override", "MOP", "WW3 path", "base"]
SHELTERED_TP_S = 3.0       # our peak period under this: a sound or harbour cell, not the ocean
FORECAST_COLS = "spot_id, valid_time, hs, tp, swell_hs, swell_tp, swell_source, nwps_cycle"


# --------------------------------------------------------------------------- #
# Pure                                                                          #
# --------------------------------------------------------------------------- #
def period_band(tp: float | None) -> str | None:
    """The band a peak period falls in: under 8, 8-10, 10-12, 12-14, 14 s and over (a band
    holds its lower edge: 8.0 s is 8-10). A NaN period is below no edge, so it is None too."""
    if tp is None:
        return None
    for upper, name in BANDS:
        if tp < upper:
            return name
    return None


def hourly(times: list[float], hs: list[float], tp: list[float], good: list[bool]) -> dict:
    """{hour (epoch s, on the hour): (mean Hs, mean Tp, readings)} from readings flagged
    good, each counted toward every hour within HALF_HOUR of it (inclusive)."""
    acc: dict[int, list] = {}
    for t, h, p, ok in zip(times, hs, tp, good):
        if not ok or h is None or h != h:
            continue
        lo = math.ceil((t - HALF_HOUR) / 3600.0) * 3600
        hi = math.floor((t + HALF_HOUR) / 3600.0) * 3600
        for hour in range(int(lo), int(hi) + 1, 3600):
            a = acc.setdefault(hour, [0.0, 0.0, 0, 0])
            a[0] += h
            a[2] += 1
            if p is not None and p == p:
                a[1] += p
                a[3] += 1
    return {k: (a[0] / a[2], (a[1] / a[3]) if a[3] else None, a[2]) for k, a in acc.items()}


def stats(pairs: list[dict]) -> dict:
    """The statistics above over *pairs*, each {"ours": m, "measured": m}."""
    xs = [(p["ours"], p["measured"]) for p in pairs
          if p["ours"] is not None and p["measured"] and p["measured"] > 0]
    n = len(xs)
    if n == 0:
        return {"n": 0}
    d = [a - b for a, b in xs]
    ratios = [a / b for a, b in xs]
    mean_obs = sum(b for _, b in xs) / n
    rmse = math.sqrt(sum(e * e for e in d) / n)
    out = {"n": n, "bias_m": sum(d) / n, "ratio_median": median(ratios), "rmse_m": rmse,
           "scatter_index": rmse / mean_obs, "mean_measured_m": mean_obs,
           "mean_ours_m": sum(a for a, _ in xs) / n,
           "within_20": sum(1 for r in ratios if abs(r - 1) <= 0.2) / n,
           "within_30": sum(1 for r in ratios if abs(r - 1) <= 0.3) / n, "r": None}
    if n >= 3:
        ma, mb = out["mean_ours_m"], mean_obs
        sab = sum((a - ma) * (b - mb) for a, b in xs)
        saa = sum((a - ma) ** 2 for a, _ in xs)
        sbb = sum((b - mb) ** 2 for _, b in xs)
        out["r"] = sab / math.sqrt(saa * sbb) if saa > 0 and sbb > 0 else None
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in out.items()}


def pair_rows(spot: str, instrument: str, forecasts: list[dict], obs: dict) -> list[dict]:
    """Every forecast hour with a measured hour, as one record each."""
    out = []
    for f in forecasts:
        if f.get("hs") is None:
            continue
        hour = int(datetime.fromisoformat(f["valid_time"].replace("Z", "+00:00")).timestamp())
        m = obs.get(hour)
        if m is None:
            continue
        out.append({"spot": spot, "instrument": instrument,
                    "valid_time": datetime.fromtimestamp(hour, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                    "ours": float(f["hs"]), "measured": round(m[0], 3),
                    "ours_tp": None if f.get("tp") is None else float(f["tp"]),
                    "measured_tp": None if m[1] is None else round(m[1], 2),
                    "band": period_band(m[1]), "readings": m[2],
                    "swell_hs": None if f.get("swell_hs") is None else float(f["swell_hs"]),
                    "swell_source": f.get("swell_source"), "nwps_cycle": f.get("nwps_cycle")})
    return out


def rater_of(swell_source) -> str:
    """Which rater fed an hour, from its swell_source (RATER; anything else is 'base')."""
    return RATER.get(swell_source, "base")


def summarize(pairs: list[dict]) -> dict:
    by_spot: dict[str, list] = {}
    by_band: dict[tuple, list] = {}
    by_rater: dict[tuple, list] = {}
    for p in pairs:
        by_spot.setdefault(p["spot"], []).append(p)
        by_band.setdefault((p["spot"], p["band"]), []).append(p)
        by_rater.setdefault((p["spot"], rater_of(p.get("swell_source"))), []).append(p)
    band_order = [b for _, b in BANDS] + [None]
    return {
        "all": stats(pairs),
        "by_spot": {s: stats(v) for s, v in by_spot.items()},
        "by_spot_band": {s: {str(b): stats(by_band[(s, b)]) for b in band_order if (s, b) in by_band}
                         for s in by_spot},
        "by_band": {str(b): stats([p for p in pairs if p["band"] == b])
                    for b in band_order if any(p["band"] == b for p in pairs)},
        "by_spot_rater": {s: {r: stats(by_rater[(s, r)]) for r in RATERS if (s, r) in by_rater}
                          for s in by_spot},
        "by_rater": {r: stats([p for p in pairs if rater_of(p.get("swell_source")) == r])
                     for r in RATERS if any(rater_of(p.get("swell_source")) == r for p in pairs)},
        "swell_source": {s: _count(p["swell_source"] for p in v) for s, v in by_spot.items()},
    }


def _count(it):
    c: dict = {}
    for x in it:
        c[str(x)] = c.get(str(x), 0) + 1
    return dict(sorted(c.items()))


# --------------------------------------------------------------------------- #
# Fetching                                                                      #
# --------------------------------------------------------------------------- #
def _open_nc(url):
    import netCDF4
    return netCDF4.Dataset(url)


def _arr(ds, name):
    import numpy as np
    return np.ma.filled(ds.variables[name][:].astype(float), np.nan).tolist()


def months(since: datetime, until: datetime) -> list[str]:
    """YYYYMM for every month from *since*'s through *until*'s."""
    out, y, m = [], since.year, since.month
    while (y, m) <= (until.year, until.month):
        out.append(f"{y:04d}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def frf_8m_hourly(since, until, open_nc=_open_nc) -> tuple[dict, list[str]]:
    """The FRF 8 m array's hourly Hs and Tp (qcFlagE == 1, 'passed'), and the months: each
    read, or 'unread YYYYMM: error' for one that failed twice (run 47 lost September and
    October 2026 this way, silently, though both open)."""
    times, hs, tp, good, read = [], [], [], [], []
    for ym in months(since, until):
        url = f"{FRF_DODS}8m-array/{ym[:4]}/FRF-ocean_waves_8m-array_{ym}.nc"
        ds, err = None, None
        for _ in range(2):
            try:
                ds = open_nc(url)
                break
            except OSError as e:
                err = e
        if ds is None:
            read.append(f"unread {ym}: {err}"[:120])
            continue
        read.append(ym)
        times += _arr(ds, "time")
        hs += _arr(ds, "waveHs")
        tp += _arr(ds, "waveTp")
        good += [q == 1 for q in _arr(ds, "qcFlagE")]
    return hourly(times, hs, tp, good), read


def cdip_hourly(station: str, open_nc=_open_nc) -> tuple[dict, list[str]]:
    """A CDIP buoy's hourly Hs and Tp from its realtime file (the current deployment),
    readings with waveFlagPrimary == 1 ('good')."""
    ds = open_nc(f"{CDIP_DODS}{station}p1_rt.nc")
    t = _arr(ds, "waveTime")
    good = [q == 1 for q in _arr(ds, "waveFlagPrimary")]
    span = [datetime.fromtimestamp(min(t), timezone.utc).strftime("%Y-%m-%d"),
            datetime.fromtimestamp(max(t), timezone.utc).strftime("%Y-%m-%d")] if t else []
    return hourly(t, _arr(ds, "waveHs"), _arr(ds, "waveTp"), good), span


def fetch_forecasts(client, names, since):
    """{name: (spot row, [forecast rows])} for the spots named, SELECT only, one spot at a
    time on the forecasts table's (spot_id, valid_time, source) key."""
    spots = (client.table("spots").select("id, name, lat, lng").in_("name", list(names))
             .execute().data or [])
    out = {}
    for s in spots:
        rows, frm = [], 0
        while True:
            page = (client.table("forecasts").select(FORECAST_COLS)
                    .eq("spot_id", s["id"]).eq("source", "nwps").gte("valid_time", since.isoformat())
                    .order("valid_time").range(frm, frm + 999).execute().data or [])
            rows += page
            if len(page) < 1000:
                break
            frm += 1000
        out[s["name"]] = (s, rows)
    return out


# --------------------------------------------------------------------------- #
# Report                                                                        #
# --------------------------------------------------------------------------- #
def _fmt(s: dict) -> str:
    if not s.get("n"):
        return "n 0"
    r = "-" if s.get("r") is None else f"{s['r']:.2f}"
    return (f"n {s['n']:5d}  bias {s['bias_m']:+.2f} m  ratio {s['ratio_median']:.2f}  "
            f"rmse {s['rmse_m']:.2f} m  SI {s['scatter_index']:.2f}  r {r}  "
            f"within 20% {s['within_20']:.0%}  30% {s['within_30']:.0%}  "
            f"(mean measured {s['mean_measured_m']:.2f} m, ours {s['mean_ours_m']:.2f} m)")


def print_report(report: dict) -> None:
    print(f"=== our published nearshore Hs against measured Hs, from {report['since']} ===")
    for c in report["coverage"]:
        print(f"  {c['spot']:24s} <- {c['instrument']:40s} {c['km']:.1f} km, {c['depth_m']} m: "
              f"forecast hours {c['forecast_hours']} (from {c['first_forecast']}), measured hours "
              f"{c['measured_hours']} ({c['measured_span']}), paired {c['paired']}, "
              f"our period under 3 s (a sheltered sample) {c['our_tp_under_3s']}")
    s = report["summary"]
    print(f"\nall pairs: {_fmt(s['all'])}")
    print("\nby spot (ours - measured; ratio = ours / measured):")
    for spot, v in s["by_spot"].items():
        print(f"  {spot:24s} {_fmt(v)}")
        print(f"  {'':24s} fed by {s['swell_source'][spot]}")
    print("\nby spot and measured peak period:")
    for spot, bands in s["by_spot_band"].items():
        for band, v in bands.items():
            print(f"  {spot:24s} {band:8s} {_fmt(v)}")
    print("\nby spot and the rater that fed the hour:")
    for spot, raters in s["by_spot_rater"].items():
        for rater, v in raters.items():
            print(f"  {spot:24s} {rater:14s} {_fmt(v)}")
    print("\nall spots by the rater that fed the hour:")
    for rater, v in s["by_rater"].items():
        print(f"  {rater:14s} {_fmt(v)}")
    print("\nall spots by measured peak period:")
    for band, v in s["by_band"].items():
        print(f"  {band:8s} {_fmt(v)}")


def _km(a, b, c, d):
    r = math.radians
    h = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


INSTRUMENT_POS = {"frf-8m": (36.1872331, -75.7428842), "cdip-263": (38.537, -75.044),
                  "cdip-284": (33.445, -117.68), "cdip-285": (35.164, -120.738),
                  "cdip-143": (28.4, -80.533)}


def run(client, since, until, open_nc=_open_nc) -> dict:
    fc = fetch_forecasts(client, [n for n, _ in PAIRS], since)
    obs_cache, pairs, coverage = {}, [], []
    for name, inst in PAIRS:
        if inst not in obs_cache:
            try:
                obs_cache[inst] = (frf_8m_hourly(since, until, open_nc) if inst == "frf-8m"
                                   else cdip_hourly(inst.split("-")[1], open_nc))
            except OSError as e:
                obs_cache[inst] = ({}, [f"unreadable: {e}"[:120]])
        obs, span = obs_cache[inst]
        spot, rows = fc.get(name, ({}, []))
        p = pair_rows(name, inst, rows, obs)
        pairs += p
        coverage.append({
            "spot": name, "instrument": INSTRUMENTS[inst]["name"], "depth_m": INSTRUMENTS[inst]["depth_m"],
            "km": _km(spot["lat"], spot["lng"], *INSTRUMENT_POS[inst]) if spot else float("nan"),
            "forecast_hours": len(rows), "first_forecast": rows[0]["valid_time"][:16] if rows else None,
            "measured_hours": len(obs), "measured_span": span, "paired": len(p),
            "our_tp_under_3s": sum(1 for r in p if r["ours_tp"] is not None
                                   and r["ours_tp"] < SHELTERED_TP_S)})
    return {"since": since.isoformat(), "until": until.isoformat(), "coverage": coverage,
            "summary": summarize(pairs), "pairs": pairs}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2025-06-01T00:00:00+00:00")
    ap.add_argument("--out")
    ap.add_argument("--csv")
    args = ap.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    since = datetime.fromisoformat(args.since)
    report = run(client, since, datetime.now(timezone.utc))
    print_report(report)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=1, default=str)
    if args.csv and report["pairs"]:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(report["pairs"][0]))
            w.writeheader()
            w.writerows(report["pairs"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
