"""Our stored tide times against NOAA's published high/low times, with this change and before it.

Read-only: asks CO-OPS for each station's published high/low tide table (local time, as NOAA
prints it) and for the hourly predictions the pipeline stores, then finds the highs and lows on
our hourly series the way the site does (lib/tideTiming.ts: the parabola through three hours)
and prints each beside NOAA's. Run from the repo root: python3 scripts/tide_times_check.py
Results of 2026-11-25: docs/tide_times_check.md."""
import json, math, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
sys.path.insert(0, ".")
from pipeline import interpret

EP = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATIONS = [("8658163", "Wrightsville Beach, NC", "America/New_York", -77.786, "Wrightsville Beach"),
            ("9413745", "Santa Cruz, CA", "America/Los_Angeles", -122.017, "Steamer Lane"),
            ("1612340", "Honolulu, HI", "Pacific/Honolulu", -157.867, "Ala Moana Bowls")]
DATES = ["2026-07-15", "2026-12-15"]

def get(station, interval, tz, begin, days):
    q = dict(station=station, product="predictions", datum="MLLW", units="english", time_zone=tz,
             interval=interval, begin_date=begin.replace("-", ""), range=24 * days, format="json")
    return json.load(urllib.request.urlopen(EP + "?" + urllib.parse.urlencode(q), timeout=60)).get("predictions", [])

def old_lookup(series, vt, lng):           # the code before this change, verbatim in effect
    local = (vt + timedelta(hours=round(lng / 15.0))).replace(tzinfo=None)
    v = interpret._sample_points(series["points"], local, 6.0 if series["source"] == "hilo" else 3.0)
    return v

def turns(hours):                          # highs/lows on the hourly series, parabola refined (as the site does)
    out = []
    for i in range(1, len(hours) - 1):
        (ta, a), (tb, b), (tc, c) = hours[i - 1], hours[i], hours[i + 1]
        if None in (a, b, c):
            continue
        kind = "H" if b >= a and b > c else "L" if b <= a and b < c else None
        if not kind:
            continue
        curve = a - 2 * b + c
        off = 0 if curve == 0 else max(-0.5, min(0.5, (a - c) / (2 * curve)))
        out.append((kind, tb + timedelta(hours=off)))
    return out

rows = []
for sid, sname, tz, lng, spot in STATIONS:
    z = ZoneInfo(tz)
    for d in DATES:
        begin = (datetime.fromisoformat(d) - timedelta(days=1)).date().isoformat()
        noaa = get(sid, "hilo", "lst_ldt", d, 1)          # NOAA's published tide table, local time
        new_block = {"station_id": sid, "time_zone": "gmt", "hourly": get(sid, "h", "gmt", begin, 3),
                     "hilo": get(sid, "hilo", "gmt", begin, 3)}
        old_block = {"station_id": sid, "hourly": get(sid, "h", "lst_ldt", begin, 3),
                     "hilo": get(sid, "hilo", "lst_ldt", begin, 3)}
        ns = interpret.build_tide_series(new_block)
        # the old series: the same parse, of the local-clock rows (the marker only gates the read)
        os_ = interpret.build_tide_series({**old_block, "time_zone": "gmt"})
        day0 = datetime.fromisoformat(d).replace(tzinfo=z).astimezone(timezone.utc) - timedelta(hours=2)
        hrs = [day0 + timedelta(hours=k) for k in range(28)]
        new_h = [(h, interpret.lookup_tide_norm(ns, h)[0]) for h in hrs]
        old_h = [(h, old_lookup(os_, h, lng)) for h in hrs]
        nt, ot = turns(new_h), turns(old_h)
        for ev in noaa:
            t_local = datetime.strptime(ev["t"], "%Y-%m-%d %H:%M").replace(tzinfo=z)
            def near(ts):
                c = [x for k, x in ts if k == ev["type"]]
                return min(c, key=lambda x: abs((x - t_local).total_seconds())) if c else None
            n, o = near(nt), near(ot)
            fmt = lambda x: x.astimezone(z).strftime("%H:%M")
            mins = lambda x: round((x - t_local).total_seconds() / 60)
            rows.append(dict(source=ns["source"], station=f"{sname} ({sid})", spot=spot, date=d, type=ev["type"],
                             noaa=t_local.strftime("%H:%M %Z"), ours_new=fmt(n), new_off=mins(n),
                             ours_old=fmt(o), old_off=mins(o)))
print(json.dumps(rows, indent=1))
