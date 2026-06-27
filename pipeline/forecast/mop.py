"""CDIP MOP nearshore forecast integration (Stage 2).

For spots tagged ``swell_window_source == "cdip_mop"`` in spots_enriched.json
(set by ``pipeline.apply_mop_assignments`` from the validated rollout), override
the spot's swell rating with its CDIP MOP point nowcast, run through the SAME
break-response chain as the normal path (interpret.py: face_ft × directional_gain
in the nearshore frame, period quality, chop), while KEEPING the per-hour wind
and tide multipliers the normal rater already computed. Spots without a fresh MOP
read this cycle are left exactly as the orientation path produced them.

This is the productionised form of the validated prototypes
(scripts/mop_blacks_slice.py rate_nearshore + scripts/mop_ca_rollout.py
pull/_norm_epoch); the logic is ported here so the pipeline has no dependency on
scripts/. interpret.py is reused directly for the rating primitives.

Design guarantees (additive + reversible):
  * No-op until a spot carries swell_window_source == "cdip_mop".
  * Any failure (no URL, THREDDS hiccup, empty/forecast-only window, bad rows) →
    that spot keeps its orientation-path rating for the cycle. Never errors,
    never blanks a rating.
  * Reuses the proven hour-bucket join + _norm_epoch fill-rejection so a stale
    window or fill tail can't corrupt the rating.

Standalone batch validation (Part C — does NOT write prod):
    python -m pipeline.forecast.mop --validate            # ~12 spots across zones
    python -m pipeline.forecast.mop --validate --batch slug1,slug2,...
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import re
from pathlib import Path

import numpy as np

from ..interpret import (
    chop_multiplier, chop_ratio, composite_stars, directional_gain, face_ft,
    period_quality,
)
from urllib.error import HTTPError, URLError

log = logging.getLogger("pipeline.forecast.mop")

RATING_SOURCE = "ww3"          # face_ft shoaling factor — same as the validated chain
SWELL_MAX_FREQ_HZ = 0.10       # swell band cutoff for the energy-spectrum split
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Data files live in scripts/ (Mac analysis artifacts) for the validation path;
# the live pipeline reads everything it needs from spots_enriched.json fields.
_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
SCRIPTS_DIR = _ROOT / "scripts"
ENRICHED = _ROOT / "pipeline" / "spots_enriched.json"
RATINGS = _ROOT / "pipeline" / "forecast_data" / "ratings.json"


def _slug(name):
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


def _iso_to_epoch(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _norm_epoch(t):
    """Epoch SECONDS, or None if implausible. Tolerates ms; rejects fill/garbage
    (e.g. 9.96e36 from a masked waveTime tail). Ported from mop_ca_rollout."""
    if t is None:
        return None
    try:
        t = float(t)
    except (TypeError, ValueError):
        return None
    if t > 1e12:
        t /= 1000.0
    if not (9.5e8 < t < 4.1e9):
        return None
    return t


def nowcast_url(url):
    """Swap a cached MOP url's flavor to nowcast (reaches the live window)."""
    if not url:
        return url
    for fl in ("_hindcast", "_forecast", "_ecmwf_fc"):
        if fl in url:
            return url.replace(fl, "_nowcast")
    return url


def _split_swell_hs(energy_row, freq):
    """(total_Hs, swell_Hs) from a 1-D energy-density spectrum. Ported verbatim."""
    df = np.gradient(freq)
    m0_total = float(np.nansum(energy_row * df))
    band = freq <= SWELL_MAX_FREQ_HZ
    m0_swell = float(np.nansum(energy_row[band] * df[band]))
    return 4.0 * (max(m0_total, 0) ** 0.5), 4.0 * (max(m0_swell, 0) ** 0.5)


def pull_mop_window(url, t0, t1):
    """MOP rows [{t,hs,tp,dp,swell_hs}] with waveTime in [t0,t1] (epoch sec).
    Real data only — forecast beyond t1 and fill values excluded. Reads only the
    windowed slice over OPeNDAP. Ported from the validated mop_ca_rollout pull."""
    import netCDF4
    nc = netCDF4.Dataset(url)
    try:
        times = np.asarray(nc.variables["waveTime"][:])
        lo = int(np.searchsorted(times, t0))
        hi = int(np.searchsorted(times, t1, side="right"))
        if hi <= lo:
            return []
        freq = np.asarray(nc.variables["waveFrequency"][:]) if "waveFrequency" in nc.variables else None

        def v(n):
            return np.asarray(nc.variables[n][lo:hi]) if n in nc.variables else None

        hs, tp, dp = v("waveHs"), v("waveTp"), v("waveDp")
        ed = v("waveEnergyDensity")
        tsl = times[lo:hi]
        rows = []
        for k in range(hi - lo):
            t = _norm_epoch(tsl[k])
            if t is None or not (t0 <= t <= t1):
                continue
            s_hs = _split_swell_hs(ed[k], freq)[1] if (ed is not None and freq is not None) else None
            rows.append(dict(
                t=t,
                hs=float(hs[k]) if hs is not None else None,
                tp=float(tp[k]) if tp is not None else None,
                dp=float(dp[k]) if dp is not None else None,
                swell_hs=s_hs,
            ))
        return rows
    finally:
        nc.close()


def mop_stars(hs, tp, dp, swell_hs, shore_normal, wind_mult=1.0, tide_mult=1.0):
    """Nearshore-frame star rating for one MOP hour, reusing interpret.py exactly
    (face_ft × directional_gain(dp vs shore-normal), chop from the MOP spectrum,
    period quality), with the per-hour wind/tide multipliers injected from the
    normal rater. Returns (stars, face_ft, dir_gain, chop_mult, period_quality)
    or (None, …) if inputs are unusable."""
    if hs is None or tp is None or dp is None or shore_normal is None:
        return None, None, None, None, None
    dg = directional_gain(dp, [], shore_normal, shore_normal)   # cos²((dp−normal)/2)
    face = face_ft(hs, tp, RATING_SOURCE)
    eff = face * dg
    cm = chop_multiplier(chop_ratio(hs, swell_hs if swell_hs else hs))
    pq = period_quality(tp)
    stars = composite_stars(eff, wind_mult, tide_mult, cm, pq)
    return stars, face, dg, cm, pq


def mop_swell_by_hour(spot, days_back=2, days_fwd=8):
    """{hour_bucket: (hs,tp,dp,swell_hs)} from the spot's MOP nowcast around now,
    or None on any failure / no usable rows. hour_bucket = floor(epoch/3600)."""
    url = spot.get("mop_nowcast_url") or nowcast_url(spot.get("mop_point_url"))
    if not url:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    rows = pull_mop_window(url, now - days_back * 86400, now + days_fwd * 86400)
    out = {}
    for r in rows:
        if r["tp"] and r["dp"] is not None and r["hs"] is not None:
            out[int(r["t"] // 3600)] = (r["hs"], r["tp"], r["dp"], r["swell_hs"])
    return out or None


def apply_mop_overrides(ratings, spots, *, dry_run=False, only=None, _fetch=mop_swell_by_hour):
    """Override the swell rating of every cdip_mop spot with its MOP nowcast, for
    the hours MOP covers, keeping each hour's wind/tide. Mutates *ratings* in
    place unless dry_run. *only* = a set of slugs to restrict to (batch). *_fetch*
    is injectable for tests. Returns stats {fed, fell_back, errored, details}."""
    fed = fell_back = errored = 0
    details = []
    for s in spots:
        if s.get("swell_window_source") != "cdip_mop":
            continue
        name = s.get("name")
        slug = _slug(name)
        if only is not None and slug not in only:
            continue
        entries = ratings.get(name)
        if not entries:
            details.append((slug, "no base ratings (skip)", 0))
            continue
        sn = s.get("mop_shore_normal")
        if sn is None:
            sn = s.get("orientation_deg")
        try:
            mop = _fetch(s)
        except (HTTPError, URLError, OSError, KeyError, ValueError) as e:  # never let MOP break a spot
            errored += 1
            details.append((slug, f"error: {type(e).__name__} → fallback", 0))
            continue
        if not mop:
            fell_back += 1
            details.append((slug, "no fresh MOP data → fallback", 0))
            continue
        n_over = 0
        for e in entries:
            t = _norm_epoch(_iso_to_epoch(e.get("valid_time")))
            if t is None:
                continue
            k = int(t // 3600)
            m = mop.get(k) or mop.get(k - 1) or mop.get(k + 1)
            if not m:
                continue
            hs, tp, dp, swh = m
            st, face, dg, cm, pq = mop_stars(hs, tp, dp, swh, sn,
                                             e.get("wind_mult", 1.0), e.get("tide_mult", 1.0))
            if st is None:
                continue
            if not dry_run:
                e.update(
                    face_ft=round(face, 2), dir_gain=round(dg, 3), chop_mult=round(cm, 3),
                    period_quality=round(pq, 3), effective_size_ft=round(face * dg, 2),
                    stars=st, swell_dp=round(dp, 3), swell_tp=round(tp, 3),
                    swell_hs=round(swh, 3) if swh is not None else None,
                    swell_source="cdip_mop",
                )
            n_over += 1
        if n_over:
            fed += 1
            details.append((slug, f"{n_over} hrs MOP-fed", n_over))
        else:
            fell_back += 1
            details.append((slug, "MOP had no overlapping hour → fallback", 0))
    return {"fed": fed, "fell_back": fell_back, "errored": errored, "details": details}


# --------------------------------------------------------------------------- #
# Part C — batch validation (dry/staging; prints MOP vs fallback, no prod write)#
# --------------------------------------------------------------------------- #
def _load_consume_spots():
    """Build cdip_mop 'spots' from the rollout verdicts + mop_points cache so the
    batch can be validated BEFORE the enrichment patch is applied."""
    vpath, mpath = SCRIPTS_DIR / "mop_ca_verdicts.json", SCRIPTS_DIR / "mop_points.json"
    if not vpath.exists() or not mpath.exists():
        return None, f"need {vpath.name} + {mpath.name} in scripts/ (Mac artifacts)"
    verdicts = json.loads(vpath.read_text()).get("spots", [])
    points = json.loads(mpath.read_text())
    roster = {_slug(s["name"]): s for s in json.loads(ENRICHED.read_text())}
    out = []
    for r in verdicts:
        if not r.get("consume"):
            continue
        rs = roster.get(r["slug"])
        pt = points.get(str(r.get("mop_point")))
        if not rs or not pt:
            continue
        out.append({
            "name": rs["name"], "orientation_deg": rs.get("orientation_deg"),
            "zone": r.get("zone"), "swell_window_source": "cdip_mop",
            "mop_point_id": r.get("mop_point"), "mop_shore_normal": r.get("shore_normal"),
            "mop_nowcast_url": nowcast_url(pt.get("url")),
        })
    return out, None


def validate_batch(batch=None, n_per_zone=3):
    if not RATINGS.exists():
        print(f"need {RATINGS} (run pipeline.interpret first) to compare against fallback", flush=True)
        return 2
    spots, err = _load_consume_spots()
    if spots is None:
        print(err)
        return 3
    ratings = json.loads(RATINGS.read_text())

    if batch:
        want = set(batch)
        chosen = [s for s in spots if _slug(s["name"]) in want]
    else:  # ~n_per_zone across zones
        chosen, seen = [], {}
        for s in sorted(spots, key=lambda s: (s.get("zone") or "Z", _slug(s["name"]))):
            z = s.get("zone") or "UNKNOWN"
            if seen.get(z, 0) < n_per_zone:
                chosen.append(s); seen[z] = seen.get(z, 0) + 1
    print(f"Part C batch validation — {len(chosen)} cdip_mop spots (no prod write)\n")
    print(f"  {'slug':26}{'zone':8}{'pt':>7}  {'when (UTC)':16}{'Hs':>5}{'Tp':>4}{'Dp':>5}"
          f"{'MOP★':>6}{'fb★':>6}  note")

    fed = fell = 0
    for s in chosen:
        name, slug = s["name"], _slug(s["name"])
        base = ratings.get(name) or []
        try:
            mop = mop_swell_by_hour(s)
        except (HTTPError, URLError, OSError) as e:
            print(f"  {slug:26}{s.get('zone') or '—':8}{str(s['mop_point_id']):>7}  "
                  f"{'':16}{'':5}{'':4}{'':5}{'':6}{'':6}  MOP fetch error: {type(e).__name__} → fallback")
            fell += 1
            continue
        if not mop:
            print(f"  {slug:26}{s.get('zone') or '—':8}{str(s['mop_point_id']):>7}  "
                  f"{'':16}{'':5}{'':4}{'':5}{'':6}{'':6}  no MOP overlap → fallback")
            fell += 1
            continue
        # pick the first base hour MOP covers
        shown = False
        for e in base:
            t = _norm_epoch(_iso_to_epoch(e.get("valid_time")))
            if t is None:
                continue
            m = mop.get(int(t // 3600)) or mop.get(int(t // 3600) - 1) or mop.get(int(t // 3600) + 1)
            if not m:
                continue
            hs, tp, dp, swh = m
            sn = s.get("mop_shore_normal") if s.get("mop_shore_normal") is not None else s.get("orientation_deg")
            st, *_ = mop_stars(hs, tp, dp, swh, sn, e.get("wind_mult", 1.0), e.get("tide_mult", 1.0))
            when = datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M")
            print(f"  {slug:26}{s.get('zone') or '—':8}{str(s['mop_point_id']):>7}  {when:16}"
                  f"{hs:5.2f}{tp:4.0f}{dp:5.0f}{st:6.1f}{e.get('stars', 0):6.1f}  "
                  f"MOP-fed ({len([1 for x in base if (lambda tt: tt is not None and (int(tt//3600) in mop or int(tt//3600)-1 in mop or int(tt//3600)+1 in mop))(_norm_epoch(_iso_to_epoch(x.get('valid_time'))))])} hrs overlap)")
            shown = True
            fed += 1
            break
        if not shown:
            print(f"  {slug:26}{s.get('zone') or '—':8}{str(s['mop_point_id']):>7}  "
                  f"{'':16}{'':5}{'':4}{'':5}{'':6}{'':6}  MOP data but no base-hour overlap → fallback")
            fell += 1

    # forced-empty test: prove the fallback path is clean (no error, no blanking)
    print("\nforced-empty MOP test (fallback must engage, no error):")
    test_ratings = {chosen[0]["name"]: [dict(valid_time="2026-06-27T12:00:00Z", stars=2.5,
                                             wind_mult=1.0, tide_mult=1.0)]} if chosen else {}
    st = apply_mop_overrides(test_ratings, chosen[:1], _fetch=lambda _s: None)
    ok = (st["fed"] == 0 and st["fell_back"] == 1 and st["errored"] == 0
          and test_ratings[chosen[0]["name"]][0]["stars"] == 2.5) if chosen else True
    print(f"  empty MOP → fed={st['fed']} fell_back={st['fell_back']} errored={st['errored']}; "
          f"base stars preserved: {'YES' if ok else 'NO'}")

    print(f"\nbatch: {fed} would be MOP-fed, {fell} fall back. "
          f"Review these before flipping all {sum(1 for s in spots)} CONSUME spots. No prod written.")
    return 0


def _selftest():
    ok = True

    def check(n, c):
        nonlocal ok; ok = ok and c; print(f"  {'PASS' if c else 'FAIL'}  {n}")

    check("nowcast_url swap", nowcast_url("x/D0045_hindcast.nc") == "x/D0045_nowcast.nc")
    check("norm_epoch ms->s", _norm_epoch(1767225600000) == 1767225600)
    check("norm_epoch rejects fill", _norm_epoch(9.969e36) is None)
    check("iso->epoch", abs(_iso_to_epoch("2026-01-01T00:00:00Z") - 1767225600) < 1)

    st, face, dg, cm, pq = mop_stars(2.0, 15, 270, 1.9, 270, 1.0, 1.0)
    st_off, *_ = mop_stars(2.0, 15, 180, 1.9, 270, 1.0, 1.0)   # 90° off-axis
    check(f"mop_stars on-axis > off-axis ({st} > {st_off})", st > st_off)
    check("mop_stars unusable -> None", mop_stars(None, 15, 270, 1.9, 270)[0] is None)
    # wind/tide injected: a wind penalty lowers stars vs neutral
    st_neutral, *_ = mop_stars(2.0, 15, 270, 1.9, 270, 1.0, 1.0)
    st_windy, *_ = mop_stars(2.0, 15, 270, 1.9, 270, 0.5, 1.0)
    check(f"wind_mult injected ({st_windy} < {st_neutral})", st_windy < st_neutral)

    # apply_mop_overrides: MOP-fed for overlapping hour, fallback otherwise, all reversible
    base = 1767225600
    spots = [{"name": "Test Spot", "swell_window_source": "cdip_mop",
              "mop_shore_normal": 270, "orientation_deg": 270, "mop_nowcast_url": "x"}]
    ratings = {"Test Spot": [
        {"valid_time": "2026-01-01T00:00:00Z", "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0},
        {"valid_time": "2030-01-01T00:00:00Z", "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0},  # no MOP
    ]}
    fake = {int(base // 3600): (2.0, 16, 270, 1.9)}   # only the first hour has MOP
    stats = apply_mop_overrides(ratings, spots, _fetch=lambda _s: fake)
    e0, e1 = ratings["Test Spot"]
    check(f"override: 1 spot fed ({stats['fed']})", stats["fed"] == 1)
    check("override hour got cdip_mop swell_source", e0["swell_source"] == "cdip_mop" and e0["stars"] != 1.0)
    check("non-overlapping hour untouched (reversible)", "swell_source" not in e1 and e1["stars"] == 1.0)
    nstats = apply_mop_overrides({"Test Spot": [dict(e0)]}, spots, _fetch=lambda _s: None)
    check("no MOP -> fell_back, no error", nstats["fell_back"] == 1 and nstats["errored"] == 0)
    estats = apply_mop_overrides({"Test Spot": [dict(e0)]}, spots,
                                 _fetch=lambda _s: (_ for _ in ()).throw(OSError("thredds")))
    check("MOP error -> errored, never raises", estats["errored"] == 1)
    # non-cdip_mop spot is ignored entirely
    plain = apply_mop_overrides({"P": [{"valid_time": "2026-01-01T00:00:00Z", "stars": 3.0}]},
                                [{"name": "P", "swell_window_source": "orientation_derived"}],
                                _fetch=lambda _s: fake)
    check("non-cdip_mop spot ignored", plain["fed"] == 0 and plain["fell_back"] == 0)

    print("\nself-test:", "ALL PASS — MOP rating + additive/reversible override sound." if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--validate", action="store_true", help="Part C batch validation (no prod write)")
    ap.add_argument("--batch", default=None, help="comma-separated slugs to validate (default: ~3/zone)")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.validate:
        return validate_batch(a.batch.split(",") if a.batch else None)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
