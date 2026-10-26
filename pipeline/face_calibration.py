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
  sized     THE HEIGHT THE FACE WAS SIZED FROM, recovered from the stored face_ft_raw (the face
            before any calibration factor divided it; stored since 2026-09-07) by inverting the
            rater's own face formula (sized_from). None of these formulas changed after
            2026-09-06, so every row with a face_ft_raw inverts with today's rule:
              MOP            face = Hs x 3.281 (no period factor since 2026-09-03)
                             -> Hs = face_ft_raw / 3.281
              NWPS override  face = Hs x period_factor(swell_tp, "ww3") x 3.281
              WW3 path       face = Hs x period_factor(swell_tp, "ww3") x 3.281
                             (Hs the RMS of the in-window WW3 partitions; swell_tp their period)
              base           face = Hs x period_factor(period, "nwps") x 3.281, period swell_tp
                             if above 0, else tp
            On NWPS override hours the recovered height must equal the stored hs (the override
            sizes from it): recovery_check counts how often it does, within 0.01 m.

SHELTERED-WATER CHECK (roster-wide). Every spot's stored period (tp) over the last
--sheltered-days: a spot whose period is under SHELTERED_TP_S on SHELTERED_SHARE or more of at
least SHELTERED_MIN_HOURS hours is a suspect (Duck Pier sampled the sound behind the island this
way until 2026-08-22: about 1 s and 0.09 m). Each suspect is listed with its baked NWPS sample
point (spots_enriched.json nwps_node_lat/lng), the distance and bearing to it, and whether it
lies within 90 deg of the spot's orientation_deg (the seaward half-plane forecast/nwps.py and
nwps_nearshore.select_node use). Nothing is changed.
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
from datetime import datetime, timedelta, timezone
from statistics import median

from .forecast.nwps_nearshore import _ang_within, _bearing, _haversine_km
from .config import DEFAULT_ENRICHED_OUTPUT
from .interpret import M_TO_FT, period_factor

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
SHELTERED_SHARE = 0.25     # ... on this share of a spot's recent hours or more: a suspect
SHELTERED_MIN_HOURS = 24
RECOVERY_TOL_M = 0.01
FORECAST_COLS = ("spot_id, valid_time, hs, tp, swell_hs, swell_tp, swell_source, nwps_cycle, "
                 "face_ft_raw")


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


def sized_from(row: dict) -> float | None:
    """The height (m) the hour's face was sized from, recovered from face_ft_raw by inverting
    the face formula of the rater that fed it (see the module docstring); None with no
    face_ft_raw or no period to invert at."""
    raw = row.get("face_ft_raw")
    if raw is None:
        return None
    rater = rater_of(row.get("swell_source"))
    if rater == "MOP":
        return float(raw) / M_TO_FT
    tp = row.get("swell_tp")
    if rater in ("NWPS override", "WW3 path"):
        return None if tp is None else float(raw) / (period_factor(float(tp), "ww3") * M_TO_FT)
    if tp is None or float(tp) <= 0:
        tp = row.get("tp")
    return None if tp is None else float(raw) / (period_factor(float(tp), "nwps") * M_TO_FT)


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
                    "swell_source": f.get("swell_source"), "nwps_cycle": f.get("nwps_cycle"),
                    "swell_tp": None if f.get("swell_tp") is None else float(f["swell_tp"]),
                    "face_ft_raw": None if f.get("face_ft_raw") is None else float(f["face_ft_raw"]),
                    "sized_from": None if (h := sized_from(f)) is None else round(h, 3)})
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
        "sized": summarize_sized(pairs),
    }


def summarize_sized(pairs: list[dict]) -> dict:
    """The same statistics with 'ours' = the height the face was sized from, by rater and by
    spot and rater, over the hours that carry one; and the recovery check on the NWPS
    override's hours (the recovered height against the stored hs it was sized from)."""
    sized = [{**p, "ours": p["sized_from"]} for p in pairs if p.get("sized_from") is not None]
    by: dict[tuple, list] = {}
    for p in sized:
        by.setdefault((p["spot"], rater_of(p.get("swell_source"))), []).append(p)
    nwps = [p for p in pairs if p.get("sized_from") is not None
            and rater_of(p.get("swell_source")) == "NWPS override"]
    return {
        "hours_with_face_ft_raw": len(sized),
        "all": stats(sized),
        "by_rater": {r: stats([p for p in sized if rater_of(p.get("swell_source")) == r])
                     for r in RATERS if any(rater_of(p.get("swell_source")) == r for p in sized)},
        "by_spot_rater": {s: {r: stats(by[(s, r)]) for r in RATERS if (s, r) in by}
                          for s in dict.fromkeys(p["spot"] for p in sized)},
        "recovery_check": {"nwps_override_hours": len(nwps),
                           "within_0_01_m_of_hs": sum(1 for p in nwps
                                                      if abs(p["sized_from"] - p["ours"]) <= RECOVERY_TOL_M)},
    }


def sheltered_check(tp_by_spot: dict, roster: dict, states: dict | None = None) -> dict:
    """The roster-wide sheltered-water check (module docstring). *tp_by_spot* is {name: [tp,
    ...]} over the recent hours, *roster* {name: spots_enriched entry}, *states* {name: state}
    from the spots table (spots_enriched.json carries no state)."""
    suspects, checked = [], 0
    for name, tps in sorted(tp_by_spot.items()):
        tps = [float(t) for t in tps if t is not None]
        if len(tps) < SHELTERED_MIN_HOURS:
            continue
        checked += 1
        low = sum(1 for t in tps if t < SHELTERED_TP_S)
        share = low / len(tps)
        if share < SHELTERED_SHARE:
            continue
        s = roster.get(name, {})
        rec = {"spot": name, "state": (states or {}).get(name, s.get("state")),
               "hours": len(tps), "hours_under_3s": low,
               "share_under_3s": round(share, 3), "median_tp_s": round(median(tps), 2),
               "nwps_wfo": s.get("nwps_wfo"), "node": None}
        nl, ng = s.get("nwps_node_lat"), s.get("nwps_node_lng")
        if nl is not None and ng is not None and s.get("lat") is not None:
            km = _haversine_km(s["lat"], s["lng"], nl, ng)
            brg = _bearing(s["lat"], s["lng"], nl, ng)
            orient = s.get("orientation_deg")
            off = None if orient is None else abs(((brg - float(orient) + 180.0) % 360.0) - 180.0)
            rec["node"] = {"lat": nl, "lng": ng, "km": round(km, 2), "bearing_deg": round(brg, 1),
                           "orientation_deg": orient,
                           "off_orientation_deg": None if off is None else round(off, 1),
                           "ocean_side": None if orient is None else _ang_within(brg, float(orient), 90.0),
                           "seaward_km": None if off is None else round(km * math.cos(math.radians(off)), 2)}
        suspects.append(rec)
    suspects.sort(key=lambda r: (-r["share_under_3s"], r["spot"]))
    return {"spots_checked": checked, "suspects": suspects}


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


def fetch_recent_tp(client, since, until) -> tuple[dict, dict]:
    """({spot name: [tp, ...]}, {spot name: state}) for every spot over [since, until), SELECT
    only, one spot at a time on the forecasts table's key."""
    spots, frm = [], 0
    while True:
        page = (client.table("spots").select("id, name, state").order("id")
                .range(frm, frm + 999).execute().data or [])
        spots += page
        if len(page) < 1000:
            break
        frm += 1000
    out = {}
    for s in spots:
        rows, frm = [], 0
        while True:
            page = (client.table("forecasts").select("valid_time, tp")
                    .eq("spot_id", s["id"]).eq("source", "nwps")
                    .gte("valid_time", since.isoformat()).lt("valid_time", until.isoformat())
                    .order("valid_time").range(frm, frm + 999).execute().data or [])
            rows += page
            if len(page) < 1000:
                break
            frm += 1000
        out[s["name"]] = [r.get("tp") for r in rows]
    return out, {s["name"]: s.get("state") for s in spots}


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
    z = s["sized"]
    print(f"\n=== the height each face was SIZED FROM (recovered from face_ft_raw), "
          f"{z['hours_with_face_ft_raw']} paired hours carry one ===")
    rc = z["recovery_check"]
    print(f"recovery check, NWPS override hours: {rc['within_0_01_m_of_hs']} of "
          f"{rc['nwps_override_hours']} recover the stored hs within {RECOVERY_TOL_M} m")
    print(f"all: {_fmt(z['all'])}")
    for rater, v in z["by_rater"].items():
        print(f"  {rater:14s} {_fmt(v)}")
    for spot, raters in z["by_spot_rater"].items():
        for rater, v in raters.items():
            print(f"  {spot:24s} {rater:14s} {_fmt(v)}")
    sh = report.get("sheltered")
    if sh is not None:
        print(f"\n=== sheltered-water check, {sh['since']} to {sh['until']}: {sh['spots_checked']} "
              f"spots with {SHELTERED_MIN_HOURS}+ hours; {len(sh['suspects'])} with our period under "
              f"{SHELTERED_TP_S:g} s on {SHELTERED_SHARE:.0%}+ of them ===")
        for r in sh["suspects"]:
            n = r["node"]
            where = ("no baked NWPS node" if n is None else
                     f"node ({n['lat']:.4f}, {n['lng']:.4f}) {n['km']} km at {n['bearing_deg']} deg, "
                     f"{n['off_orientation_deg']} deg off orientation {n['orientation_deg']}, "
                     f"seaward {n['seaward_km']} km, ocean side {n['ocean_side']}")
            print(f"  {r['spot']:28s} {str(r['state']):15s} {r['hours_under_3s']}/{r['hours']} "
                  f"({r['share_under_3s']:.0%}) under 3 s, median {r['median_tp_s']} s; {where}")


def _km(a, b, c, d):
    r = math.radians
    h = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


INSTRUMENT_POS = {"frf-8m": (36.1872331, -75.7428842), "cdip-263": (38.537, -75.044),
                  "cdip-284": (33.445, -117.68), "cdip-285": (35.164, -120.738),
                  "cdip-143": (28.4, -80.533)}


def run(client, since, until, open_nc=_open_nc, roster=None, sheltered_days=14) -> dict:
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
    out = {"since": since.isoformat(), "until": until.isoformat(), "coverage": coverage,
           "summary": summarize(pairs), "pairs": pairs}
    if roster is not None:
        start = until - timedelta(days=sheltered_days)
        tps, states = fetch_recent_tp(client, start, until)
        out["sheltered"] = {"since": start.isoformat(), "until": until.isoformat(),
                            **sheltered_check(tps, roster, states)}
    return out


SHELTERED_COLUMNS = ["spot", "state", "hours", "hours_under_3s", "share_under_3s", "median_tp_s",
                     "nwps_wfo", "node_lat", "node_lng", "node_km", "bearing_deg",
                     "orientation_deg", "off_orientation_deg", "seaward_km", "ocean_side"]


def write_sheltered_csv(path: str, suspects: list[dict]) -> None:
    """The suspects one row each, the node fields flattened (blank with no baked node)."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHELTERED_COLUMNS)
        w.writeheader()
        for r in suspects:
            n = r["node"] or {}
            w.writerow({**{k: r[k] for k in SHELTERED_COLUMNS[:7]},
                        "node_lat": n.get("lat"), "node_lng": n.get("lng"), "node_km": n.get("km"),
                        "bearing_deg": n.get("bearing_deg"), "orientation_deg": n.get("orientation_deg"),
                        "off_orientation_deg": n.get("off_orientation_deg"),
                        "seaward_km": n.get("seaward_km"), "ocean_side": n.get("ocean_side")})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", default="2025-06-01T00:00:00+00:00")
    ap.add_argument("--out")
    ap.add_argument("--csv")
    ap.add_argument("--roster", default=str(DEFAULT_ENRICHED_OUTPUT),
                    help="spots_enriched.json whose NWPS sample points the sheltered check reads")
    ap.add_argument("--sheltered-days", type=int, default=14)
    ap.add_argument("--sheltered-csv", help="write the sheltered-water suspects here")
    args = ap.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    since = datetime.fromisoformat(args.since)
    roster = {s["name"]: s for s in json.load(open(args.roster)) if s.get("name")}
    report = run(client, since, datetime.now(timezone.utc), roster=roster,
                 sheltered_days=args.sheltered_days)
    print_report(report)
    if args.sheltered_csv:
        write_sheltered_csv(args.sheltered_csv, report["sheltered"]["suspects"])
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
