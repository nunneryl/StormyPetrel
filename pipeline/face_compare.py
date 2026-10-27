"""Read-only comparison of the two face-height candidates against what was seen (sw1-raycast
mode=face-compare). Step 3 of the face-height work. Nothing here writes anywhere but the report
(stdout, --out and the CSVs); Supabase is SELECT only.

THE CANDIDATES, from each archived forecast hour since 2026-09-07 (when face_ft_raw began)

  Hs        the height the hour's face was sized from: the NWPS node's hs on the NWPS
            override's hours, MOP's own Hs (face_ft_raw / 3.281) on MOP hours
            (face_calibration.sized_from). No correction factor is applied: MOP read 0.96 to
            0.99 of CDIP at the buoys' own points (run 49), and the placement problems found in
            runs 48 to 50 are handled by exclusion below.
  typical   the face of the typical breaking wave: Hs carried from D0 = 10 m (MOP's own depth)
            to its breaking point by linear shoaling (Green's energy flux, Hs x
            sqrt(Cg(D0) / Cg(h))), breaking where the shoaled height reaches GAMMA = 0.78 x
            the depth, at the hour's rated period (swell_tp, else tp). Shoaling only: no
            refraction, no friction, no set factor. The multiplier this gives depends on the
            period AND on the height (a bigger wave breaks in deeper water, so shoals less);
            its spread is reported, not assumed.
  sets      typical x SETS (1.27): the mean of the highest tenth over the highest third for
            Rayleigh-distributed heights.
  published the face_ft the site showed for the hour, as the baseline.

WHAT EACH IS COMPARED WITH

  FRF lidar   Duck, the lidarHydrodynamics transect off the north tower: Hs every 0.1 m
              cross-shore, about x = 79 to 193 m. An hour counts only with
              hydrodynamicsFlag 1 ('good'), and the observed breaking Hs is the transect's
              maximum over cells with under LIDAR_MAX_MISSING of the record missing. A SMALL
              DAY needs both: the maximum lies at least LIDAR_INTERIOR_M shoreward of the most
              seaward good cell (the waves broke inside the transect), and the FRF 8 m array
              read SMALL_DAY_8M_HS_M or less that hour. The second rule is needed because a
              big day also puts a maximum inside the transect: waves that broke on the outer
              bar re-form and break again near the shore (on 2026-10-18, 8 m Hs 3.0 m, the
              lidar's maximum was 1 m Hs at x = 140 to 170 m). Other hours are counted by
              reason and not used.
              Compared with typical (both are significant heights at breaking). Also
              typical built from the FRF 8 m array's measured Hs and Tp (D0 = 8 m), which
              separates the formula's error from the input's.
              FRF xp340m has no file after 2026-07, so it cannot cover this window.
  NWS SRF     the Surf Zone Forecast's daytime surf-height range for the spot's zone, from the
              Iowa Environmental Mesonet's text archive: for each hour 08:00 to 17:59 local,
              the newest product issued before the hour. The NWS defines surf height as the
              average height of the highest third of the breaking waves, so typical is the
              like-for-like candidate. A face is INSIDE a range lo to hi when it rounds to a
              whole foot within it (lo - 0.5 <= face < hi + 0.5); 'N foot or less' is 0 to N.
              A segment split by sub-area (north/south of a cape) has no single height for a
              spot and is not used. HFO's table gives a range per shore facing (N, E, S, W from
              the spot's orientation) and AM / PM; whether its numbers are face heights is not
              established here, so Hawaii is reported apart. A spot belongs to the zone whose
              api.weather.gov outline is nearest, within ZONE_TOLERANCE_KM.
  reports     visitor surf_reports (size_bucket, 'the average of the sets ridden') against the
              archived hour, on frontend/lib/surfReport.ts's BUCKET_FACE_FT scale; a face is
              inside a bucket when it is nearer that bucket's face than either neighbour's.
              Hawaii is held out, as that file says.

HOURS LEFT OUT, and counted
  - held spots: Mexico Beach, Oak Island Pier, and the Gulf suspects held after run 50
    (Whitney Beach, Coolidge, Siesta Key, Gasparilla, Twin Piers);
  - WW3-path hours (swell_source ww3): the face is sized from deep-water partitions;
  - the five spots #280 moved: every archived hour before MOVED_AT came from the old point,
    so they are reported apart (Duck Pier's FRF comparison says so);
  - base-path hours (rate_spot's NWPS path) are reported apart from the headline;
  - run 48's other five suspects (Navarre Beach, Naples Pier, St Andrews Park, Redington Pier,
    Destin Jetty) are kept, and listed apart as well.

REGIONS are the spot's NWS office's (REGION_OF); spot type is its break_type.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from statistics import median

from .face_calibration import BANDS, FRF_DODS, _open_nc, frf_8m_hourly, months, period_band, \
    sized_from, stats
from .face_sources import USER_AGENT, ZONE_TOLERANCE_KM, _UGC_LINE, _ugc_codes, \
    parse_surf_height

M_TO_FT = 3.281
G = 9.81
D0_M = 10.0
GAMMA = 0.78
SETS = 1.27
SINCE = datetime(2026, 9, 7, tzinfo=timezone.utc)
MOVED_AT = datetime(2026, 10, 27, 13, 39, tzinfo=timezone.utc)   # #280 merged
DAY_HOURS = range(8, 18)            # local hours that start inside 08:00-17:59
AM_HOURS, PM_HOURS = range(8, 12), range(12, 18)
LIDAR_MAX_MISSING = 20.0            # percent of a cell's record missing
LIDAR_INTERIOR_M = 10.0
FRF_8M_DEPTH_M = 8.0
SMALL_DAY_8M_HS_M = 1.0
DUCK = "Duck Pier"
# SRF offices that are no roster spot's NWPS office but cover roster spots' beaches: NWS Mobile
# (Pensacola Beach, Navarre Beach, Okaloosa Island, Destin).
EXTRA_SRF_OFFICES = ("MOB",)

HELD = {"mexico-beach", "oak-island-pier", "whitney-beach", "coolidge", "siesta-key",
        "gasparilla", "twin-piers"}
MOVED = {"duck-pier", "spring-ave", "st-pete-beach", "treasure-island-fl", "madeira-beach"}
SUSPECTS_KEPT = {"navarre-beach", "naples-pier", "st-andrews-park", "redington-pier",
                 "destin-jetty"}

REGION_OF = {
    "car": "New England", "gyx": "New England", "box": "New England",
    "okx": "Mid-Atlantic", "phi": "Mid-Atlantic", "akq": "Mid-Atlantic",
    "mhx": "Carolinas and Georgia", "ilm": "Carolinas and Georgia",
    "chs": "Carolinas and Georgia",
    "jax": "Florida Atlantic", "mlb": "Florida Atlantic", "mfl": "Florida Atlantic",
    "key": "Florida Atlantic",
    "tbw": "Gulf", "tae": "Gulf", "mob": "Gulf", "lix": "Gulf", "lch": "Gulf", "hgx": "Gulf",
    "crp": "Gulf", "bro": "Gulf",
    "sgx": "Southern California", "lox": "Southern California",
    "mtr": "Northern California", "eka": "Northern California",
    "mfr": "Pacific Northwest", "pqr": "Pacific Northwest", "sew": "Pacific Northwest",
    "hfo": "Hawaii", "sju": "Puerto Rico",
}

# frontend/lib/surfReport.ts BUCKET_FACE_FT, ascending (a test reads that file and checks it).
BUCKET_FACE_FT = {"ankle": 0.5, "knee": 1.5, "thigh": 2.25, "waist": 3.0, "chest": 4.0,
                  "shoulder": 5.0, "head": 6.0, "overhead": 7.0, "well_overhead": 9.0,
                  "double_overhead": 11.0, "triple_overhead_plus": 15.0}

TZ_HOURS = {"EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "MDT": -6, "MST": -7, "PDT": -7,
            "PST": -8, "AKDT": -8, "AKST": -9, "HST": -10, "AST": -4, "CHST": 10}
WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
TODAY_PERIODS = ("TODAY", "REST OF TODAY", "THIS MORNING", "THIS AFTERNOON",
                 "REST OF THIS AFTERNOON")
CANDIDATES = ("published", "typical", "sets")
FORECAST_COLS = "spot_id, valid_time, hs, tp, swell_tp, swell_source, face_ft, face_ft_raw"


# --------------------------------------------------------------------------- #
# Linear shoaling to breaking (pure, numpy)                                    #
# --------------------------------------------------------------------------- #
def wavenumber(period, depth):
    """k (rad/m) from the linear dispersion relation w^2 = g k tanh(k h), by Newton's method
    from the Fenton-McKee approximation; elementwise over arrays."""
    import numpy as np
    t = np.asarray(period, dtype=float)
    h = np.asarray(depth, dtype=float)
    kh0 = (2 * math.pi / t) ** 2 / G * h
    kh = kh0 / np.tanh(kh0 ** 0.75) ** (2.0 / 3.0)
    for _ in range(6):
        th = np.tanh(kh)
        kh = kh - (kh * th - kh0) / (th + kh * (1 - th * th))
    return kh / h


def group_velocity(period, depth):
    """Cg (m/s) = n c, n = (1 + 2kh / sinh 2kh) / 2; elementwise."""
    import numpy as np
    t = np.asarray(period, dtype=float)
    h = np.asarray(depth, dtype=float)
    k = wavenumber(t, h)
    kh = k * h
    n = 0.5 * (1 + np.where(kh < 20, 2 * kh / np.sinh(np.minimum(2 * kh, 40)), 0.0))
    return n * (2 * math.pi / t) / k


def breaking_height(hs, period, d0=D0_M, gamma=GAMMA, levels=240, iterations=40):
    """The height (m) at breaking of a wave of height *hs* at depth *d0*, carried shoreward by
    linear shoaling until it reaches *gamma* x the depth: the deepest such depth on a
    geometric scan from d0 to 1 mm, refined by bisection. hs at or above gamma x d0 is already
    breaking (returned as is); hs or period not above 0 gives 0. Elementwise."""
    import numpy as np
    H = np.atleast_1d(np.asarray(hs, dtype=float))
    T = np.atleast_1d(np.asarray(period, dtype=float))
    out = np.zeros_like(H)
    live = (H > 0) & (T > 0) & np.isfinite(H) & np.isfinite(T)
    already = live & (H >= gamma * d0)
    out[already] = H[already]
    todo = np.where(live & ~already)[0]
    if todo.size:
        h_scan = d0 * (0.001 / d0) ** (np.arange(levels + 1) / levels)     # d0 .. 1 mm
        Hc, Tc = H[todo][:, None], T[todo][:, None]
        cg0 = group_velocity(Tc, d0)

        def excess(h):
            return Hc * np.sqrt(cg0 / group_velocity(Tc, h)) - gamma * h

        f = excess(np.broadcast_to(h_scan, (todo.size, levels + 1)))
        first = np.argmax(f >= 0, axis=1)                  # first depth where it breaks
        hi = h_scan[np.maximum(first - 1, 0)]              # deeper: not yet breaking
        lo = h_scan[first]
        hi, lo = hi[:, None], lo[:, None]
        for _ in range(iterations):
            mid = 0.5 * (hi + lo)
            brk = excess(mid) >= 0
            lo = np.where(brk, mid, lo)
            hi = np.where(brk, hi, mid)
        out[todo] = (gamma * lo)[:, 0]
    return out


def candidate_faces(hs, period, d0=D0_M):
    """(typical ft, sets ft) arrays for heights *hs* (m) at periods *period* (s), solved once per
    pair rounded to 0.01 m and 0.1 s."""
    import numpy as np
    H = np.round(np.asarray(hs, dtype=float), 2)
    T = np.round(np.asarray(period, dtype=float), 1)
    pairs = np.stack([H, T], axis=1)
    uniq, inv = np.unique(pairs, axis=0, return_inverse=True)
    hb = breaking_height(uniq[:, 0], uniq[:, 1], d0=d0)
    typical = hb[np.ravel(inv)] * M_TO_FT
    return typical, typical * SETS


# --------------------------------------------------------------------------- #
# Spots                                                                        #
# --------------------------------------------------------------------------- #
def slug(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def region_of(spot: dict) -> str:
    return REGION_OF.get((spot.get("nwps_wfo") or "").lower(), "other")


def spot_type(spot: dict) -> str:
    return spot.get("break_type") or "unknown"


def facing(orientation) -> str | None:
    """N, E, S or W: the quarter (centred on the compass point) the shore faces."""
    if orientation is None:
        return None
    return "NESW"[int(((float(orientation) + 45.0) % 360.0) // 90.0)]


def group_of(spot_slug: str, swell_source, valid: datetime) -> str:
    """'headline', or why the hour is left out of it: 'held', 'ww3', 'moved (old point)',
    'base', 'no face'."""
    if spot_slug in HELD:
        return "held"
    rater = {"nwps": "nwps", "nwps_height_ww3_dir": "nwps", "cdip_mop": "mop",
             "ww3": "ww3"}.get(swell_source, "base")
    if rater == "ww3":
        return "ww3"
    if spot_slug in MOVED and valid < MOVED_AT:
        return "moved (old point)"
    if rater == "base":
        return "base"
    return "headline"


# --------------------------------------------------------------------------- #
# NWS Surf Zone Forecasts (pure)                                               #
# --------------------------------------------------------------------------- #
_ISSUED = re.compile(r"^(\d{3,4}) (AM|PM) ([A-Z]{2,4}) [A-Z][a-z]{2} ([A-Z][a-z]{2}) (\d{1,2}) "
                     r"(\d{4})\s*$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def split_products(text: str) -> list[str]:
    """The products in an IEM text response, which wraps each in \\x01 ... \\x03: each part
    with an SRFxxx line ('ERROR: Could not Find: SRFHGX', IEM's answer for none, is not)."""
    return [p for p in re.split(r"[\x01\x03]", text)
            if re.search(r"(?m)^SRF[A-Z]{3}\s*$", p)]


def issued(product: str) -> tuple[datetime, int] | None:
    """(issue time UTC, the zone's UTC offset in hours) from the product's first time line,
    '1059 AM EDT Tue Sep 15 2026', or None."""
    for line in product.splitlines():
        m = _ISSUED.match(line.strip())
        if not m or m.group(3) not in TZ_HOURS:
            continue
        hhmm, ampm, tz, mon, day, year = m.groups()
        hh, mm = int(hhmm[:-2]), int(hhmm[-2:])
        hh = hh % 12 + (12 if ampm == "PM" else 0)
        off = TZ_HOURS[tz]
        local = datetime(int(year), _MONTHS.index(mon) + 1, int(day), hh, mm)
        return (local - timedelta(hours=off)).replace(tzinfo=timezone.utc), off
    return None


def period_date(period: str, issue_local: date) -> date | None:
    """The local date a daytime period names, or None for a night period or a heading that is
    not a period. A weekday is the first date on or after the issue date with that weekday."""
    p = period.strip().upper()
    if p in TODAY_PERIODS:
        return issue_local
    if p in WEEKDAYS:
        ahead = (WEEKDAYS.index(p) - issue_local.weekday()) % 7
        return issue_local + timedelta(days=ahead)
    return None


_FT = r"(\d+(?:\.\d+)?)"
_U = r"(?:feet|foot|ft)\b"


def surf_range(text: str) -> tuple[float, float] | None:
    """(lo, hi) feet from an SRF surf-height phrase: face_sources.parse_surf_height's ranges
    and 'around N', plus 'N foot or less' / 'less than N feet' -> (0, N) and a bare 'N feet'
    / 'N FT' -> (N, N). None when none of these is stated."""
    got = parse_surf_height(text)
    if got is not None:
        return got["lo"], got["hi"]
    t = text.lower()
    m = re.search(_FT + r"\s*" + _U + r"\s*or\s+less", t) or re.search(
        r"less than\s+" + _FT + r"\s*" + _U, t)
    if m:
        return 0.0, float(m.group(1))
    m = re.match(r"^[\s.]*" + _FT + r"\s*" + _U, t)
    if m:
        return float(m.group(1)), float(m.group(1))
    return None


def srf_periods(product: str) -> list[dict]:
    """Every (zones, period, lo, hi) the product states, from its 'Surf Height...' lines (a
    split segment, whose heading line carries no height, gives none)."""
    out, zones, period = [], None, None
    for raw in product.splitlines():
        line = raw.rstrip()
        m = _UGC_LINE.match(line.strip())
        if m:
            zones, period = _ugc_codes(m.group(1) + "-000000-"), None
            continue
        if zones is None:
            continue
        if line.strip() == "$$":
            zones, period = None, None
            continue
        pm = re.match(r"^\.([A-Z][A-Z ]+?)\.\.\.", line)
        if pm:
            period = pm.group(1).strip()
            rest = line[pm.end():]
            if re.search(r"surf height", rest, re.I):
                rng = surf_range(re.split(r"surf heights?", rest, flags=re.I)[1])
                if rng:
                    out.append({"zones": zones, "period": period, "facing": None,
                                "lo": rng[0], "hi": rng[1], "ampm": None})
                period = None
            continue
        if period and re.match(r"^\s*Surf Height", line, re.I):
            rng = surf_range(line.split("...")[-1] if "..." in line else line)
            if rng:
                out.append({"zones": zones, "period": period, "facing": None, "lo": rng[0],
                            "hi": rng[1], "ampm": None})
            period = None
    return out


def hfo_periods(product: str) -> list[dict]:
    """HFO's surf table: for each segment, every (zones, day, AM/PM, facing, lo, hi), from

        Tonight                    Monday            <- day names
        PM     AM                  AM     PM          <- one AM/PM per column
        North Facing   7-10   8-12   9-12  10-14      <- a range per column
    """
    out, zones, days, cols = [], None, None, None
    for raw in product.splitlines():
        line = raw.strip()
        m = _UGC_LINE.match(line)
        if m:
            zones, days, cols = _ugc_codes(m.group(1) + "-000000-"), None, None
            continue
        if zones is None:
            continue
        if line == "$$":
            zones = None
            continue
        words = line.upper().split()
        if words and all(w in WEEKDAYS + ("TODAY", "TONIGHT") for w in words) and days is None:
            days = words
            continue
        if days and words and all(w in ("AM", "PM") for w in words):
            cols, per_day, i = [], len(words) // len(days), 0
            for d in days:
                for _ in range(per_day):
                    cols.append((d, words[i]))
                    i += 1
            continue
        fm = re.match(r"^(North|East|South|West) Facing\s+(.*)$", line, re.I)
        if cols and fm:
            vals = fm.group(2).split()
            for (d, ampm), v in zip(cols, vals):
                r = re.fullmatch(r"(\d+)(?:-(\d+))?", v)
                if r:
                    lo = float(r.group(1))
                    out.append({"zones": zones, "period": d, "ampm": ampm,
                                "facing": fm.group(1)[0].upper(), "lo": lo,
                                "hi": float(r.group(2) or r.group(1))})
    return out


def srf_hours(products: list[str], office: str) -> tuple[dict, Counter]:
    """{(zone, facing or None, UTC hour epoch): (lo, hi, office)} for every daytime hour a
    product covers, from the newest product issued before the hour; and coverage counts."""
    cov = Counter()
    dated = []
    for p in products:
        iss = issued(p)
        if iss is None:
            cov["products without an issue time"] += 1
            continue
        dated.append((iss[0], iss[1], p))
    dated.sort(key=lambda x: x[0])
    out = {}
    for t_iss, off, p in dated:
        cov["products"] += 1
        rows = hfo_periods(p) if office.lower() == "hfo" else srf_periods(p)
        local_issue = (t_iss + timedelta(hours=off)).date()
        for r in rows:
            day = period_date(r["period"], local_issue)
            if day is None:
                cov["night or other periods"] += 1
                continue
            cov["day periods"] += 1
            hours = {"AM": AM_HOURS, "PM": PM_HOURS}.get(r["ampm"], DAY_HOURS)
            for hh in hours:
                t = datetime(day.year, day.month, day.day, hh, tzinfo=timezone.utc) \
                    - timedelta(hours=off)
                if t < t_iss:
                    continue
                for z in r["zones"]:
                    out[(z, r["facing"], int(t.timestamp()))] = (r["lo"], r["hi"], office)
    return out, cov


def classify(face: float | None, lo: float, hi: float) -> str | None:
    """'inside' when the face rounds to a whole foot within lo..hi, else 'below' / 'above'."""
    if face is None:
        return None
    if face < lo - 0.5:
        return "below"
    if face >= hi + 0.5:
        return "above"
    return "inside"


# --------------------------------------------------------------------------- #
# Visitor reports (pure)                                                       #
# --------------------------------------------------------------------------- #
def bucket_of(face_ft: float) -> str:
    """The bucket whose face is nearest (ties to the smaller)."""
    return min(BUCKET_FACE_FT, key=lambda b: (abs(BUCKET_FACE_FT[b] - face_ft),
                                              BUCKET_FACE_FT[b]))


def bucket_side(face_ft: float | None, bucket: str) -> str | None:
    """'inside', 'below' or 'above' the reported bucket on the BUCKET_FACE_FT scale."""
    if face_ft is None:
        return None
    got = list(BUCKET_FACE_FT).index(bucket_of(face_ft))
    want = list(BUCKET_FACE_FT).index(bucket)
    return "inside" if got == want else ("below" if got < want else "above")


# --------------------------------------------------------------------------- #
# FRF lidar (pure)                                                             #
# --------------------------------------------------------------------------- #
def lidar_hour(x, hs, missing, flag) -> dict | None:
    """One lidar hour as {hs_max, x_peak, x_seaward, small}, or None when the hour's flag is
    not 1 or no cell is good. hs_max is the transect's maximum over cells with Hs and under
    LIDAR_MAX_MISSING percent missing; small when it lies at least LIDAR_INTERIOR_M shoreward
    of the most seaward such cell."""
    if flag != 1:
        return None
    good = [(xi, h) for xi, h, m in zip(x, hs, missing)
            if h is not None and h == h and m is not None and m == m and m < LIDAR_MAX_MISSING]
    if not good:
        return None
    x_peak, h_max = max(good, key=lambda c: (c[1], -c[0]))
    x_sea = max(xi for xi, _ in good)
    return {"hs_max": round(h_max, 3), "x_peak": round(x_peak, 1), "x_seaward": round(x_sea, 1),
            "small": x_peak <= x_sea - LIDAR_INTERIOR_M}


def lidar_hourly(since: datetime, until: datetime, open_nc=_open_nc) -> tuple[dict, dict]:
    """{UTC hour epoch: lidar_hour(...)} for good hours, and counts per month and flag."""
    import numpy as np
    out, cov = {}, {}
    for ym in months(since, until):
        url = (f"{FRF_DODS}lidarHydrodynamics/{ym[:4]}/"
               f"FRF-ocean_waves_lidarHydrodynamics_{ym}.nc")
        try:
            ds = open_nc(url)
        except OSError as e:
            cov[ym] = f"unread: {e}"[:120]
            continue

        def arr(n):
            return np.ma.filled(ds.variables[n][:].astype(float), np.nan)

        x, t = arr("xFRF"), arr("time")
        hs, miss, flags = arr("waveHs"), arr("percentTimeSeriesMissing"), arr("hydrodynamicsFlag")
        c = Counter()
        for i, ti in enumerate(t):
            hour = int(round(ti / 3600.0)) * 3600
            if not (since.timestamp() <= hour < until.timestamp()):
                continue
            f = None if flags[i] != flags[i] else int(flags[i])
            c[f"flag {f}"] += 1
            got = lidar_hour(x.tolist(), hs[i].tolist(), miss[i].tolist(), f)
            if got is not None:
                out[hour] = got
                c["maximum inside the transect" if got["small"]
                  else "maximum at the seaward end"] += 1
        cov[ym] = dict(c)
    return out, cov


# --------------------------------------------------------------------------- #
# Aggregation                                                                  #
# --------------------------------------------------------------------------- #
def tally(rows: list[dict], key: str) -> dict:
    """{group: {n, and per candidate: inside / below / above shares and the median ratio to
    the reference}} for rows each {key, ref, <candidate>_side, <candidate>_ratio}."""
    by = defaultdict(list)
    for r in rows:
        by[r[key]].append(r)
    by["all"] = rows
    out = {}
    for g, rs in sorted(by.items(), key=lambda kv: (kv[0] != "all", str(kv[0]))):
        e = {"n": len(rs)}
        for c in CANDIDATES:
            sides = Counter(r.get(f"{c}_side") for r in rs if r.get(f"{c}_side"))
            n = sum(sides.values())
            ratios = [r[f"{c}_ratio"] for r in rs if r.get(f"{c}_ratio") is not None]
            e[c] = {"inside": round(sides["inside"] / n, 3) if n else None,
                    "below": round(sides["below"] / n, 3) if n else None,
                    "above": round(sides["above"] / n, 3) if n else None,
                    "ratio_median": round(median(ratios), 3) if ratios else None}
        out[str(g)] = e
    return out


def multiplier_spread(hs: list[float], typical: list[float], periods: list[float]) -> dict:
    """typical / (Hs in ft) by rated-period band: n, median and the quartiles."""
    by = defaultdict(list)
    for h, f, p in zip(hs, typical, periods):
        if h and h > 0:
            by[period_band(p) or "none"].append(f / (h * M_TO_FT))
            by["all"].append(f / (h * M_TO_FT))
    out = {}
    for band in ["all"] + [b for _, b in BANDS] + ["none"]:
        v = sorted(by.get(band, []))
        if v:
            out[band] = {"n": len(v), "p25": round(v[len(v) // 4], 3),
                         "median": round(median(v), 3), "p75": round(v[(3 * len(v)) // 4], 3)}
    return out


# --------------------------------------------------------------------------- #
# Fetching (SELECT only) and the run                                           #
# --------------------------------------------------------------------------- #
def _get(url, params=None, timeout=120):
    import requests
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r


IEM_AFOS = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
NWS_ZONE = "https://api.weather.gov/zones/forecast/"


def fetch_srf(office: str, since: datetime, until: datetime, get=_get) -> list[str]:
    r = get(IEM_AFOS, {"pil": f"SRF{office.upper()}", "fmt": "text", "limit": 9999,
                       "sdate": (since - timedelta(days=1)).strftime("%Y-%m-%d"),
                       "edate": (until + timedelta(days=1)).strftime("%Y-%m-%d")})
    return split_products(r.text)


def fetch_zone(zone: str, get=_get) -> dict | None:
    return (get(NWS_ZONE + zone, timeout=60).json() or {}).get("geometry")


def _page(q):
    rows, frm = [], 0
    while True:
        page = q().range(frm, frm + 999).execute().data or []
        rows += page
        if len(page) < 1000:
            return rows
        frm += 1000


def fetch_spots(client) -> list[dict]:
    return _page(lambda: client.table("spots").select("id, name, state").order("id"))


def fetch_rows(client, spot_id, since, until) -> list[dict]:
    return _page(lambda: client.table("forecasts").select(FORECAST_COLS)
                 .eq("spot_id", spot_id).eq("source", "nwps")
                 .gte("valid_time", since.isoformat()).lt("valid_time", until.isoformat())
                 .order("valid_time"))


def fetch_reports(client, since) -> list[dict]:
    return _page(lambda: client.table("surf_reports")
                 .select("spot_id, observed_hour, size_bucket")
                 .gte("observed_hour", since.isoformat()).order("id"))


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def assess(rows: list[dict], spot: dict, state: str | None) -> list[dict]:
    """Every archived hour of one spot with its group, Hs, period and the three faces."""
    s = slug(spot.get("name"))
    base = []
    for r in rows:
        t = datetime.fromisoformat(r["valid_time"].replace("Z", "+00:00"))
        h = sized_from(r)
        per = r.get("swell_tp") if r.get("swell_tp") not in (None, 0) else r.get("tp")
        base.append({"spot": spot.get("name"), "slug": s, "state": state,
                     "region": region_of(spot), "type": spot_type(spot),
                     "office": (spot.get("nwps_wfo") or "").upper(),
                     "facing": facing(spot.get("orientation_deg")),
                     "hour": int(t.timestamp()), "group": group_of(s, r.get("swell_source"), t),
                     "hs": h, "period": None if per is None else float(per),
                     "published": None if r.get("face_ft") is None else float(r["face_ft"])})
    ok = [i for i, b in enumerate(base) if b["hs"] is not None and b["period"]]
    if ok:
        typ, sets = candidate_faces([base[i]["hs"] for i in ok], [base[i]["period"] for i in ok])
        for j, i in enumerate(ok):
            base[i]["typical"] = round(float(typ[j]), 2)
            base[i]["sets"] = round(float(sets[j]), 2)
    for b in base:
        b.setdefault("typical", None)
        b.setdefault("sets", None)
        if b["group"] == "headline" and b["typical"] is None:
            b["group"] = "no face"
    return base


def _ratio(a, b):
    return None if a is None or not b else round(a / b, 3)


def prepare_zones(geoms: dict) -> dict:
    """{zone: (office, shapely shape, (minx, miny, maxx, maxy))} for the outlines read."""
    from shapely.geometry import shape
    out = {}
    for z, (office, g) in geoms.items():
        if g:
            s = shape(g)
            out[z] = (office, s, s.bounds)
    return out


def zone_distance_km(lat: float, lng: float, shp) -> float:
    """face_sources.distance_to_geometry_km's flat-earth distance, on a shape built once."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    q = nearest_points(shp, Point(lng, lat))[0]
    return math.hypot((q.x - lng) * 111.32 * math.cos(math.radians(lat)), (q.y - lat) * 110.57)


def nearest_zone(lat: float, lng: float, zones: dict) -> tuple[str, float, str] | None:
    """(zone, km, office) of the SRF zone outline nearest the spot, from any office (a spot's
    NWPS office need not be the office whose SRF covers it), within ZONE_TOLERANCE_KM.
    *zones* is prepare_zones' output; an outline whose box is farther than the tolerance is
    skipped unmeasured."""
    pad = ZONE_TOLERANCE_KM / 100.0                       # degrees, over 1.1x the tolerance
    best = None
    for z, (office, shp, (x0, y0, x1, y1)) in sorted(zones.items()):
        if not (x0 - pad / max(math.cos(math.radians(lat)), 0.2) <= lng
                <= x1 + pad / max(math.cos(math.radians(lat)), 0.2) and y0 - pad <= lat <= y1 + pad):
            continue
        d = zone_distance_km(lat, lng, shp)
        if d <= ZONE_TOLERANCE_KM and (best is None or d < best[1]):
            best = (z, d, office)
    return best


def compare_srf(hours: list[dict], srf: dict) -> list[dict]:
    out = []
    for h in hours:
        if h["office"] == "HFO":
            got = srf.get((h.get("zone"), h["facing"], h["hour"]))
        else:
            got = srf.get((h.get("zone"), None, h["hour"]))
        if not got:
            continue
        lo, hi, office = got
        mid = (lo + hi) / 2.0
        rec = {k: h[k] for k in ("spot", "slug", "state", "region", "type", "office", "group",
                                 "hour")}
        rec.update(lo=lo, hi=hi)
        for c in CANDIDATES:
            rec[c] = h[c]
            rec[f"{c}_side"] = classify(h[c], lo, hi)
            rec[f"{c}_ratio"] = _ratio(h[c], mid)
        out.append(rec)
    return out


def compare_reports(reports: list[dict], by_hour: dict, names: dict) -> list[dict]:
    out = []
    for rp in reports:
        h = by_hour.get((rp["spot_id"], _epoch(rp["observed_hour"])))
        if h is None:
            continue
        ref = BUCKET_FACE_FT.get(rp["size_bucket"])
        rec = {k: h[k] for k in ("spot", "state", "region", "type", "office", "group", "hour")}
        rec.update(bucket=rp["size_bucket"], bucket_ft=ref)
        for c in CANDIDATES:
            rec[c] = h[c]
            rec[f"{c}_side"] = bucket_side(h[c], rp["size_bucket"])
            rec[f"{c}_ratio"] = _ratio(h[c], ref)
        out.append(rec)
    return out


def compare_frf(duck: list[dict], lidar: dict, frf8: dict) -> dict:
    """Small-day lidar hours against typical from our archived hour and from the FRF 8 m
    array's measured Hs and Tp, and against the published face; every pair."""
    pairs, skipped = [], Counter()
    ideal_hs, ideal_tp, ideal_idx = [], [], []
    by_hour = {h["hour"]: h for h in duck}
    for hour, lh in sorted(lidar.items()):
        m8 = frf8.get(hour)
        if not lh["small"]:
            skipped["maximum at the transect's seaward end"] += 1
            continue
        if m8 is None:
            skipped["no FRF 8 m reading"] += 1
            continue
        if m8[0] > SMALL_DAY_8M_HS_M:
            skipped[f"FRF 8 m Hs over {SMALL_DAY_8M_HS_M} m"] += 1
            continue
        obs_ft = lh["hs_max"] * M_TO_FT
        h = by_hour.get(hour)
        p = {"hour": datetime.fromtimestamp(hour, timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
             "observed_ft": round(obs_ft, 2), "x_peak": lh["x_peak"],
             "ours_typical": None if h is None else h["typical"],
             "published": None if h is None else h["published"],
             "group": None if h is None else h["group"],
             "frf8_hs": None if m8 is None else round(m8[0], 3),
             "frf8_tp": None if m8 is None or m8[1] is None else round(m8[1], 2),
             "frf8_typical": None}
        if m8 is not None and m8[1]:
            ideal_hs.append(m8[0])
            ideal_tp.append(m8[1])
            ideal_idx.append(len(pairs))
        pairs.append(p)
    if ideal_idx:
        typ, _ = candidate_faces(ideal_hs, ideal_tp, d0=FRF_8M_DEPTH_M)
        for j, i in enumerate(ideal_idx):
            pairs[i]["frf8_typical"] = round(float(typ[j]), 2)

    def st(key):
        return stats([{"ours": p[key], "measured": p["observed_ft"]} for p in pairs
                      if p[key] is not None])

    return {"small_day_hours": len(pairs), "not_small": dict(skipped),
            "ours_typical_vs_lidar_ft": st("ours_typical"),
            "frf8_typical_vs_lidar_ft": st("frf8_typical"),
            "published_vs_lidar_ft": st("published"), "pairs": pairs}


def run(client, since: datetime, until: datetime, roster: list[dict], *, get=_get,
        open_nc=_open_nc) -> dict:
    by_name = {s["name"]: s for s in roster}
    spots = fetch_spots(client)
    names = {s["id"]: s["name"] for s in spots}
    errors = {}

    # 1. SRF products per office, and the zones they name
    offices = sorted({(s.get("nwps_wfo") or "").upper() for s in roster if s.get("nwps_wfo")}
                     | set(EXTRA_SRF_OFFICES))
    srf, srf_cov, zones_named = {}, {}, defaultdict(set)
    for o in offices:
        try:
            prods = fetch_srf(o, since, until, get)
        except Exception as e:                                     # report, never raise
            errors[f"srf {o}"] = f"{type(e).__name__}: {e}"[:200]
            continue
        hrs, cov = srf_hours(prods, o)
        srf.update(hrs)
        srf_cov[o] = dict(cov)
        for z, _f, _t in hrs:
            zones_named[o].add(z)
    geoms = {}
    for o, zs in zones_named.items():
        for z in sorted(zs):
            try:
                geoms[z] = (o, fetch_zone(z, get))
            except Exception as e:
                errors[f"zone {z}"] = f"{type(e).__name__}: {e}"[:200]

    zones = prepare_zones(geoms)

    # 2. every spot's archived hours, compared as each spot is read so that only the hours
    #    with a Surf Zone Forecast range or a report are kept
    try:
        reports = fetch_reports(client, since)
    except Exception as e:                                         # report, never raise
        reports, errors["surf_reports"] = [], f"{type(e).__name__}: {e}"[:200]
    reported = {(r["spot_id"], _epoch(r["observed_hour"])) for r in reports}
    srf_rows, by_hour, groups = [], {}, Counter()
    mult = {"hs": [], "typical": [], "period": []}
    zone_of, duck = {}, []
    for s in spots:
        spot = by_name.get(s["name"])
        if spot is None:
            continue
        best = nearest_zone(spot["lat"], spot["lng"], zones)
        zone_of[s["name"]] = None if best is None else best[0]
        try:
            rows = fetch_rows(client, s["id"], since, until)
        except Exception as e:
            errors[f"forecasts {s['name']}"] = f"{type(e).__name__}: {e}"[:200]
            continue
        kept = []
        for h in assess(rows, spot, s.get("state")):
            groups[h["group"]] += 1
            h["zone"] = zone_of[s["name"]]
            if best is not None:
                h["office"] = best[2]        # the office whose SRF covers the zone
            h["spot_id"] = s["id"]
            if s["name"] == DUCK:
                duck.append(h)
            if h["group"] == "headline":
                mult["hs"].append(h["hs"])
                mult["typical"].append(h["typical"])
                mult["period"].append(h["period"])
            if h["group"] in ("headline", "base", "moved (old point)"):
                kept.append(h)
                if (s["id"], h["hour"]) in reported:
                    by_hour[(s["id"], h["hour"])] = h
        srf_rows += compare_srf(kept, srf)

    # 3. the comparisons
    rep_rows = compare_reports(reports, by_hour, names)
    lidar, lidar_cov = lidar_hourly(since, until, open_nc)
    frf8, frf8_read = frf_8m_hourly(since, until, open_nc)
    frf = compare_frf(duck, lidar, frf8)

    head = [r for r in srf_rows if r["group"] == "headline"]
    mainland = [r for r in head if r["office"] != "HFO"]
    rep_head = [r for r in rep_rows if r["group"] == "headline" and r["state"] != "Hawaii"]
    return {
        "window": [since.isoformat(), until.isoformat()],
        "definitions": {"D0_m": D0_M, "gamma": GAMMA, "sets": SETS, "moved_at": MOVED_AT.isoformat(),
                        "day_hours_local": [DAY_HOURS.start, DAY_HOURS.stop - 1],
                        "zone_tolerance_km": ZONE_TOLERANCE_KM},
        "errors": errors,
        "hours_by_group": dict(groups),
        "multiplier": multiplier_spread(mult["hs"], mult["typical"], mult["period"]),
        "frf": {k: v for k, v in frf.items() if k != "pairs"},
        "frf_lidar_months": lidar_cov, "frf_8m_months": frf8_read,
        "srf_coverage": srf_cov,
        "zones": {"named": sum(len(z) for z in zones_named.values()),
                  "outlines": sum(1 for _o, g in geoms.values() if g),
                  "spots_mapped": sum(1 for z in zone_of.values() if z),
                  "spots_unmapped": sorted(n for n, z in zone_of.items() if z is None)},
        "srf": {"by_office": tally(mainland, "office"), "by_region": tally(mainland, "region"),
                "by_type": tally(mainland, "type"),
                "hawaii": tally([r for r in head if r["office"] == "HFO"], "type"),
                "base_path": tally([r for r in srf_rows if r["group"] == "base"], "region"),
                "moved_old_point": tally([r for r in srf_rows
                                          if r["group"] == "moved (old point)"], "spot"),
                "suspects_kept": tally([r for r in head if r["slug"] in SUSPECTS_KEPT], "spot"),
                "by_spot": tally(head, "spot")},
        "reports": {"n_reports": len(reports), "n_paired": len(rep_rows),
                    "hawaii_held_out": sum(1 for r in rep_rows if r["state"] == "Hawaii"),
                    "by_region": tally(rep_head, "region"), "by_type": tally(rep_head, "type"),
                    "by_bucket": tally(rep_head, "bucket")},
        "_pairs": {"frf": frf["pairs"], "srf": srf_rows, "reports": rep_rows},
    }


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #
def _pct(v):
    return "-" if v is None else f"{100 * v:.0f}%"


def _line(name, e):
    parts = [f"{name:28} n {e['n']:6}"]
    for c in CANDIDATES:
        x = e[c]
        parts.append(f"{c} in {_pct(x['inside'])} lo {_pct(x['below'])} hi {_pct(x['above'])}"
                     f" x{x['ratio_median'] if x['ratio_median'] is not None else '-'}")
    return " | ".join(parts)


def _table(title, t):
    print(f"\n  {title}")
    for g, e in t.items():
        print("    " + _line(g, e))


def _st(name, s):
    if not s.get("n"):
        return f"  {name}: no pairs"
    return (f"  {name}: n {s['n']}, ratio {s['ratio_median']}, bias {s['bias_m']:+.2f} ft, "
            f"within 20% {_pct(s['within_20'])}, 30% {_pct(s['within_30'])}, r {s['r']} "
            f"(observed {s['mean_measured_m']:.2f} ft, ours {s['mean_ours_m']:.2f} ft)")


def print_report(rep: dict) -> None:
    print(f"\nFace-height comparison, {rep['window'][0]} to {rep['window'][1]}")
    print(f"  typical = Hs shoaled from {rep['definitions']['D0_m']} m to breaking "
          f"(gamma {rep['definitions']['gamma']}); sets = typical x {rep['definitions']['sets']}")
    if rep["errors"]:
        print("  ERRORS: " + "; ".join(f"{k}: {v}" for k, v in rep["errors"].items()))
    print("  archived hours by group: " + ", ".join(f"{k} {v}" for k, v in
                                                     sorted(rep["hours_by_group"].items())))
    print("\n=== 1. The typical multiplier (typical face / Hs in ft), headline hours ===")
    for band, m in rep["multiplier"].items():
        print(f"  {band:8} n {m['n']:7}  p25 {m['p25']}  median {m['median']}  p75 {m['p75']}")
    f = rep["frf"]
    print(f"\n=== 2. FRF Duck lidar, small days: {f['small_day_hours']} hours (not small: "
          + ", ".join(f"{k} {v}" for k, v in f["not_small"].items()) + ") ===")
    print(f"  lidar months: {rep['frf_lidar_months']}; 8 m months: {rep['frf_8m_months']}")
    print(_st("typical from our archived Duck Pier hour (old point)", f["ours_typical_vs_lidar_ft"]))
    print(_st("typical from the FRF 8 m array's measured Hs and Tp", f["frf8_typical_vs_lidar_ft"]))
    print(_st("published face", f["published_vs_lidar_ft"]))
    z = rep["zones"]
    print(f"\n=== 3. NWS Surf Zone Forecast ranges (daytime hours; in / lo / hi share, "
          f"median ratio to the range's middle) ===")
    print(f"  zones named {z['named']}, outlines read {z['outlines']}, spots mapped "
          f"{z['spots_mapped']}, unmapped {len(z['spots_unmapped'])}")
    for o, c in rep["srf_coverage"].items():
        print(f"  {o}: " + (", ".join(f"{k} {v}" for k, v in c.items())
                            or "no products in the window"))
    s = rep["srf"]
    _table("by office (mainland and Puerto Rico)", s["by_office"])
    _table("by region", s["by_region"])
    _table("by spot type", s["by_type"])
    _table("Hawaii (HFO; whether its table is face height is not established)", s["hawaii"])
    _table("base-path hours (not in the headline)", s["base_path"])
    _table("the five moved spots, archived hours from the OLD point", s["moved_old_point"])
    _table("run 48's other suspects (kept in the headline)", s["suspects_kept"])
    r = rep["reports"]
    print(f"\n=== 4. Visitor surf reports: {r['n_reports']} reports, {r['n_paired']} with an "
          f"archived hour, {r['hawaii_held_out']} Hawaii held out ===")
    _table("by region", r["by_region"])
    _table("by spot type", r["by_type"])
    _table("by reported bucket", r["by_bucket"])


def write_csvs(rep: dict, prefix: str) -> None:
    for name, rows in rep["_pairs"].items():
        if not rows:
            continue
        cols = list(rows[0].keys())
        with open(f"{prefix}_{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)


def main(argv=None) -> int:
    import os
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--since", default=SINCE.isoformat())
    p.add_argument("--until", default=None, help="ISO end (default: now)")
    p.add_argument("--roster", default="pipeline/spots_enriched.json")
    p.add_argument("--out", default=None)
    p.add_argument("--csv-prefix", default=None)
    a = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    since = datetime.fromisoformat(a.since)
    until = (datetime.fromisoformat(a.until) if a.until
             else datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0))
    with open(a.roster) as fh:
        roster = json.load(fh)
    rep = run(client, since, until, roster)
    print_report(rep)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump({k: v for k, v in rep.items() if k != "_pairs"}, fh, indent=1)
    if a.csv_prefix:
        write_csvs(rep, a.csv_prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
