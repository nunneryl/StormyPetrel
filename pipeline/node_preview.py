"""Node preview: what the PUBLISHED forecast window would rate with moved NWPS sample points.

READ-ONLY. It SELECTs from the spots and forecasts tables, reads NWPS cycles from NOMADS and
writes nothing to Supabase. The only files it writes are its own report (--out, --csv).

    python -m pipeline.node_preview --baseline BASELINE.json [--roster ROSTER.json]
        [--since ISO] [--reference SLUG,...] [--out OUT.json] [--csv HOURS.csv]

BASELINE is the roster the published window was rated with (main's spots_enriched.json);
ROSTER is the one to preview (default: this checkout's). A spot is MOVED when its
nwps_node_lat/lng differ between the two.

HOW AN HOUR IS REPLAYED. Every published hour of a moved spot that apply_nwps_overrides wrote
(swell_source nwps_height_ww3_dir or nwps) carries the NWPS cycle it was rated from
(nwps_cycle). Where NOMADS still holds that cycle, the hour is rated twice through
apply_nwps_overrides itself, the code that wrote it, with its fetch given that cycle's series:

  old  the baseline node. The entry it starts from is the one rate_spot handed the override:
       swell_source "ww3" with the stored swell_dp / swell_tp on an nwps_height_ww3_dir hour
       (the WW3 identity the override kept), no WW3 identity on an nwps hour (the override took
       dirpw), and the stored wind, wind_mult and tide_mult. Series values are rounded to 3
       decimals, as nwps.fetch writes them into forecast_data/nwps.json.
  new  the moved node, from the same cycle. Where the hour's wind was NWPS's own (its stored
       wind equals the old node's NWPS wind; HRRR's replaces it inside 48 h), the new node's
       NWPS wind replaces it too.

THE MERGE GATE. At the old node the replay must give, hour for hour:
  inputs  the stored hs, tp, dp and swell_hs: the old node's swh, perpw, dirpw and shts at
          the hour the override rated from (the cycle and cell the row was rated from);
  stars   the published stars.
Both must have 0 differences on at least one hour. The hour the override rated from is the one
nwps_nearshore.series_hour gives, the function apply_nwps_overrides itself calls: the hour, else
the hour before, else the hour after. A neighbour's values are accepted only where the hour's
own swh, perpw or dirpw is blank in the cycle's file at the old node's cell; a fill over an hour
the file holds is a difference. The gate line counts the filled hours. Then every replayed hour is listed with its
stars, face, height and period at the old and the new node, and the homepage's "Best
conditions right now" (each spot's soonest hour from --since, ten highest) before and after.
Each new node is also checked against every cycle read: a wet cell centre, and the same cell
nwps.fetch samples (nearest grid row and column), not a neighbour.

REFERENCE SPOTS (--reference, comma-separated slugs). Spots that did not move, shown beside the
moved ones: their published stars and face (face_ft_raw, the face the replay gives) on the same
hours as each moved spot's replayed ones.

WHAT IT CANNOT SEE. Hours another rater wrote (MOP, WW3, rate_spot's NWPS path) and hours whose
cycle NOMADS no longer holds are counted, by reason, not replayed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .forecast.nwps_nearshore import (_haversine_km, _iso_to_epoch, _nearest_cell,
                                      _node_value, _region_for, apply_nwps_overrides,
                                      load_cycle, nwps_series_by_hour, recent_cycles,
                                      series_hour)

NWPS_KINDS = ("nwps_height_ww3_dir", "nwps")
NODE_FIELDS = ("nwps_node_lat", "nwps_node_lng")
CYCLES_LISTED = 16            # more than NOMADS keeps of any office (run 50: 6, ilm 12)
INPUT_TOL = 5e-4              # stored values carry 3 decimals
SAME_CELL_KM = 0.01
FORECAST_COLS = ("spot_id, valid_time, hs, tp, dp, swell_hs, swell_tp, swell_dp, swell_source, "
                 "face_ft, face_ft_raw, wind_speed, wind_dir, wind_mult, tide_mult, stars, "
                 "nwps_cycle")


def _num(v):
    return None if v is None else float(v)


def moved_spots(baseline, roster):
    """[(old_spot, new_spot)] for every NWPS spot whose node differs between the two rosters,
    in roster order. Both are lists of spots; a spot is matched by name."""
    before = {s.get("name"): s for s in baseline}
    out = []
    for s in roster:
        b = before.get(s.get("name"))
        if b is None or s.get("swell_window_source") != "nwps":
            continue
        if any(b.get(k) != s.get(k) for k in NODE_FIELDS):
            out.append((b, s))
    return out


def cycle_key(stamp):
    """('YYYYMMDD', 'HH') of an nwps_cycle stamp, or None."""
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    return dt.strftime("%Y%m%d"), dt.strftime("%H")


def node_series(spot, cycle):
    """{hour_bucket: (swh, perpw, dirpw, shts)} at the spot's node, every value rounded to 3
    decimals as nwps.fetch writes them, or None."""
    raw = nwps_series_by_hour(spot, cycle)
    if not raw:
        return None
    return {k: tuple(None if v is None else round(v, 3) for v in vals) for k, vals in raw.items()}


def node_wind(cycle, i, j, epoch):
    """(wind_speed, wind_dir) of NWPS at cell (i, j) for the hour at *epoch*, rounded to 3
    decimals, or None where the cycle has no wind there."""
    fh = round((epoch - cycle["cycle_dt"].timestamp()) / 3600)
    ws, wd = cycle["fields"].get(("ws", fh)), cycle["fields"].get(("wdir", fh))
    if ws is None or wd is None:
        return None
    a, b = float(ws[i, j]), float(wd[i, j])
    if a != a or b != b:
        return None
    return round(a, 3), round(b, 3)


def node_cell(cycle, spot):
    """(i, j) of the cell nwps_series_by_hour samples for the spot's baked node, or None."""
    cell = _nearest_cell(cycle, spot["nwps_node_lat"], spot["nwps_node_lng"])
    return None if cell is None else (cell[0], cell[1])


def exact_hour_blank(cycle, cell, epoch):
    """Is the hour at *epoch* blank in the cycle's file at *cell*: no step for it, or its swh,
    perpw or dirpw NaN there (the hours nwps_series_by_hour leaves out)?"""
    fh = round((epoch - cycle["cycle_dt"].timestamp()) / 3600)
    if fh not in cycle["steps"]:
        return True
    return any(_node_value(cycle, short, fh, cell[0], cell[1]) is None
               for short in ("swh", "perpw", "dirpw"))


def node_check(cycle, lat, lng):
    """{wet, same_cell, offset_km} for a baked node in one cycle: is the cell nwps.fetch takes
    (nearest grid row and column) wet, is it the cell nwps_series_by_hour takes (nearest wet
    cell), and how far is the node from that cell's centre."""
    lats, lons, mask = cycle["lats"], cycle["lons"], cycle["mask"]
    li = min(range(lats.shape[0]), key=lambda r: abs(float(lats[r, 0]) - lat))
    lj = min(range(lons.shape[1]), key=lambda c: abs(float(lons[0, c]) - lng))
    near = _nearest_cell(cycle, lat, lng)
    return {"wet": not bool(mask[li, lj]),
            "same_cell": near is not None and (near[0], near[1]) == (li, lj),
            "offset_km": round(_haversine_km(lat, lng, float(lats[li, lj]),
                                             float(lons[li, lj])), 3)}


def pre_override_entry(row):
    """The entry rate_spot handed apply_nwps_overrides for a published NWPS-override hour."""
    e = {"valid_time": row["valid_time"],
         "wind_dir": _num(row.get("wind_dir")), "wind_speed": _num(row.get("wind_speed")),
         "wind_mult": 1.0 if row.get("wind_mult") is None else float(row["wind_mult"]),
         "tide_mult": 1.0 if row.get("tide_mult") is None else float(row["tide_mult"])}
    if row.get("swell_source") == "nwps_height_ww3_dir":
        e.update(swell_source="ww3", swell_dp=_num(row.get("swell_dp")),
                 swell_tp=_num(row.get("swell_tp")))
    else:
        e["swell_source"] = None
    return e


def wind_was_nwps(row, wind):
    """Did the published hour carry NWPS's own wind? True when its stored wind equals *wind*,
    the old node's NWPS wind at that hour; HRRR's replaces NWPS's inside its 48 h."""
    if wind is None or row.get("wind_speed") is None or row.get("wind_dir") is None:
        return False
    return (abs(float(row["wind_speed"]) - wind[0]) <= INPUT_TOL
            and abs(float(row["wind_dir"]) - wind[1]) <= INPUT_TOL)


def inputs_match(row, vals):
    """Do the stored hs, tp, dp and swell_hs equal the node's (swh, perpw, dirpw, shts)?"""
    if vals is None:
        return False
    for col, v in zip(("hs", "tp", "dp", "swell_hs"), vals):
        stored = row.get(col)
        if (stored is None) != (v is None):
            return False
        if v is not None and abs(float(stored) - v) > INPUT_TOL:
            return False
    return True


def replay(spot, entries, series, ceiling_factors, median_factor, apply=apply_nwps_overrides):
    """*entries* rated in place by apply_nwps_overrides on *spot* with *series* as its fetch.
    An entry the override did not reach carries no 'stars' afterwards."""
    apply({spot["name"]: entries}, [spot], _fetch=lambda _s: series,
          ceiling_factors=ceiling_factors, median_factor=median_factor)
    return entries


def gate(hours):
    """The merge gate over replayed hours: inputs and stars at the old node, 0 differences;
    and how many matching hours were filled from a neighbouring hour."""
    n = len(hours)
    bad_inputs = sum(not h["inputs_match"] for h in hours)
    bad_stars = sum(h["stars_old"] != h["stars_published"] for h in hours)
    filled = sum(h["inputs_match"] and h.get("filled_from") in (-1, 1) for h in hours)
    return {"hours": n, "input_differences": bad_inputs, "star_differences": bad_stars,
            "filled": filled, "pass": n > 0 and bad_inputs == 0 and bad_stars == 0}


def spot_summary(hours):
    """Per spot: hours replayed, stars up / down / same, and the mean stars, face and height
    at the old and the new node."""
    out = {}
    by = defaultdict(list)
    for h in hours:
        by[h["spot"]].append(h)
    for name, hs in by.items():
        def mean(key):
            vals = [h[key] for h in hs if h[key] is not None]
            return round(sum(vals) / len(vals), 3) if vals else None
        both = [(h["stars_old"], h["stars_new"]) for h in hs
                if h["stars_old"] is not None and h["stars_new"] is not None]
        out[name] = {"hours": len(hs),
                     "stars_up": sum(b > a for a, b in both),
                     "stars_down": sum(b < a for a, b in both),
                     "stars_same": sum(b == a for a, b in both),
                     "mean_stars_old": mean("stars_old"), "mean_stars_new": mean("stars_new"),
                     "mean_face_old": mean("face_old"), "mean_face_new": mean("face_new"),
                     "mean_hs_old": mean("hs_old"), "mean_hs_new": mean("hs_new")}
    return out


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def reference_summary(hours, refs):
    """{moved spot: {reference: {...}}}: on the moved spot's replayed hours the reference has
    published stars for, the mean stars and face at the old node, the new node and the
    reference (face_ft_raw, the face the replay gives). *refs*: {name: {valid_time: row}}."""
    by = defaultdict(list)
    for h in hours:
        by[h["spot"]].append(h)
    out = {}
    for name, hs in by.items():
        for ref, rows in refs.items():
            both = [(h, rows[h["valid_time"]]) for h in hs if h["valid_time"] in rows]
            out.setdefault(name, {})[ref] = {
                "hours": len(both),
                "stars_old": _mean([h["stars_old"] for h, _ in both]),
                "stars_new": _mean([h["stars_new"] for h, _ in both]),
                "stars_reference": _mean([_num(r.get("stars")) for _, r in both]),
                "face_old": _mean([h["face_old"] for h, _ in both]),
                "face_new": _mean([h["face_new"] for h, _ in both]),
                "face_reference": _mean([_num(r.get("face_ft_raw")) for _, r in both])}
    return out


def homepage(first_rows, names, new_stars, n=10):
    """The homepage's "Best conditions right now" before and after: each spot's soonest
    published hour, stars highest first, equal stars in spot id order, ten shown
    (frontend/app/page.tsx). *new_stars* maps spot id to its soonest hour's stars at the new
    node; every other spot keeps its published stars."""
    before, after = [], []
    for sid, row in first_rows.items():
        name, state = names.get(sid, (None, None))
        pub = float(row["stars"])
        before.append((pub, sid, name, state))
        after.append((new_stars.get(sid, pub), sid, name, state))
    before.sort(key=lambda r: (-r[0], r[1]))
    after.sort(key=lambda r: (-r[0], r[1]))
    top_b, top_a = before[:n], after[:n]
    a_ids, b_ids = {r[1] for r in top_a}, {r[1] for r in top_b}
    return {"before": [{"spot": r[2], "state": r[3], "stars": r[0]} for r in top_b],
            "after": [{"spot": r[2], "state": r[3], "stars": r[0],
                       "moved": r[1] in new_stars} for r in top_a],
            "drop_out": [r[2] for r in top_b if r[1] not in a_ids],
            "come_in": [r[2] for r in top_a if r[1] not in b_ids]}


def replay_cycle(pairs, rows_by_spot, cycle, ceiling_factors, median_factor,
                 apply=apply_nwps_overrides):
    """Every published NWPS-override hour of the moved spots *pairs* in one cycle, replayed at
    the old and the new node. Returns (hours, node_checks)."""
    hours, checks = [], {}
    for old, new in pairs:
        rows = rows_by_spot.get(new["name"]) or []
        if not rows:
            continue
        checks[new["name"]] = node_check(cycle, new["nwps_node_lat"], new["nwps_node_lng"])
        s_old, s_new = node_series(old, cycle), node_series(new, cycle)
        c_old, c_new = node_cell(cycle, old), node_cell(cycle, new)
        e_old, e_new, winds = [], [], []
        for row in rows:
            epoch = _iso_to_epoch(row["valid_time"])
            w_old = node_wind(cycle, *c_old, epoch) if c_old else None
            nwps_wind = wind_was_nwps(row, w_old)
            e_old.append(pre_override_entry(row))
            e = pre_override_entry(row)
            if nwps_wind:
                w_new = node_wind(cycle, *c_new, epoch) if c_new else None
                if w_new is not None:
                    e["wind_speed"], e["wind_dir"] = w_new
            e_new.append(e)
            winds.append("nwps" if nwps_wind else "other")
        replay(old, e_old, s_old, ceiling_factors, median_factor, apply)
        replay(new, e_new, s_new, ceiling_factors, median_factor, apply)
        for row, a, b, wind in zip(rows, e_old, e_new, winds):
            epoch = _iso_to_epoch(row["valid_time"])
            k = int(epoch // 3600)
            vo, off = series_hour(s_old or {}, k)
            vn, _ = series_hour(s_new or {}, k)
            # a neighbour's values stand for the hour only where the file leaves it blank
            blank = exact_hour_blank(cycle, c_old, epoch) if c_old else True
            fill_ok = off == 0 or (off in (-1, 1) and blank)
            hours.append({
                "spot": new["name"], "valid_time": row["valid_time"],
                "nwps_cycle": row.get("nwps_cycle"), "swell_source": row.get("swell_source"),
                "wind": wind, "wind_old": [a["wind_speed"], a["wind_dir"]],
                "wind_new": [b["wind_speed"], b["wind_dir"]],
                "inputs_match": fill_ok and inputs_match(row, vo),
                "filled_from": off, "exact_hour_blank": blank,
                "stars_published": float(row["stars"]),
                "stars_old": a.get("stars"), "stars_new": b.get("stars"),
                "face_published": _num(row.get("face_ft_raw")),
                "face_old": a.get("face_ft"), "face_new": b.get("face_ft"),
                "hs_old": None if vo is None else vo[0], "hs_new": None if vn is None else vn[0],
                "tp_old": None if vo is None else vo[1], "tp_new": None if vn is None else vn[1],
                "swell_hs_old": a.get("swell_hs"), "swell_hs_new": b.get("swell_hs"),
                "rated_tp_old": a.get("swell_tp"), "rated_tp_new": b.get("swell_tp")})
    return hours, checks


# --------------------------------------------------------------------------- #
# Supabase (SELECT only) and NOMADS                                            #
# --------------------------------------------------------------------------- #
def fetch_names(client):
    names, frm = {}, 0
    while True:
        rows = (client.table("spots").select("id, name, state").order("id")
                .range(frm, frm + 999).execute().data or [])
        names.update({r["id"]: (r["name"], r.get("state")) for r in rows})
        if len(rows) < 1000:
            return names
        frm += 1000


def fetch_rows(client, sid, since):
    out, frm = [], 0
    while True:
        rows = (client.table("forecasts").select(FORECAST_COLS)
                .eq("spot_id", sid).eq("source", "nwps").gte("valid_time", since)
                .order("valid_time").range(frm, frm + 999).execute().data or [])
        out.extend(rows)
        if len(rows) < 1000:
            return out
        frm += 1000


def fetch_first(client, sid, since):
    """The spot's soonest published hour from *since* that carries stars, or None."""
    rows = (client.table("forecasts").select("spot_id, valid_time, stars")
            .eq("spot_id", sid).eq("source", "nwps").gte("valid_time", since)
            .order("valid_time").limit(6).execute().data or [])
    return next((r for r in rows if r.get("stars") is not None), None)


def run(client, baseline, roster, since, *, factors=None, list_cycles=None, open_cycle=None,
        apply=apply_nwps_overrides, references=()):
    from .forecast.face_correction import load_face_factors, median_face_factor
    from .interpret import face_factors_by_name
    factors = load_face_factors() if factors is None else factors
    ceiling_factors = face_factors_by_name(roster, factors)
    m = median_face_factor(factors)
    list_cycles = list_cycles or (lambda w: {(d, c): u for d, c, u in
                                             recent_cycles(w, CYCLES_LISTED, _region_for(w))})
    open_cycle = open_cycle or load_cycle
    pairs = moved_spots(baseline, roster)
    names = fetch_names(client)
    ids = {n: sid for sid, (n, _) in names.items()}
    report = {"since": since, "moved": [], "not_replayed": {}, "cycles": {}, "node_checks": {}}
    hours = []
    for old, new in pairs:
        report["moved"].append({"spot": new["name"], "wfo": new.get("nwps_wfo"),
                                "old": [old["nwps_node_lat"], old["nwps_node_lng"]],
                                "new": [new["nwps_node_lat"], new["nwps_node_lng"]],
                                "new_distance_m": new.get("nwps_node_distance_m")})
    by_wfo = defaultdict(list)
    for pair in pairs:
        by_wfo[pair[1].get("nwps_wfo")].append(pair)
    for wfo, wpairs in sorted(by_wfo.items()):
        listed = list_cycles(wfo)
        report["cycles"][wfo] = {"listed": sorted(f"{d}{c}" for d, c in listed), "read": []}
        by_cycle = defaultdict(lambda: defaultdict(list))
        for old, new in wpairs:
            skipped = report["not_replayed"].setdefault(new["name"], Counter())
            for row in fetch_rows(client, ids[new["name"]], since):
                if row.get("stars") is None:
                    skipped["no stars"] += 1
                elif row.get("swell_source") not in NWPS_KINDS:
                    skipped[f"another rater ({row.get('swell_source')})"] += 1
                elif cycle_key(row.get("nwps_cycle")) not in listed:
                    skipped[f"cycle {row.get('nwps_cycle')} not on NOMADS"] += 1
                else:
                    by_cycle[cycle_key(row["nwps_cycle"])][new["name"]].append(row)
        for key in sorted(by_cycle):
            cycle = open_cycle(wfo, (key[0], key[1], listed[key]))
            report["cycles"][wfo]["read"].append("".join(key))
            got, checks = replay_cycle(wpairs, by_cycle[key], cycle, ceiling_factors, m, apply)
            hours.extend(got)
            for name, c in checks.items():
                report["node_checks"].setdefault(name, {})["".join(key)] = c
    hours.sort(key=lambda h: (h["spot"], h["valid_time"]))
    report["not_replayed"] = {k: dict(v) for k, v in report["not_replayed"].items()}
    report["gate"] = gate(hours)
    report["by_spot"] = spot_summary(hours)
    from .apply_nwps_assignments import _slug
    by_slug = {_slug(n): (sid, n) for sid, (n, _) in names.items()}
    refs = {}
    report["references_missing"] = [r for r in references if r not in by_slug]
    for r in references:
        if r in by_slug:
            sid, n = by_slug[r]
            refs[n] = {row["valid_time"]: row for row in fetch_rows(client, sid, since)
                       if row.get("stars") is not None}
    report["references"] = reference_summary(hours, refs)
    report["hours"] = hours
    first = {}
    for sid in sorted(names):
        row = fetch_first(client, sid, since)
        if row is not None:
            first[sid] = row
    soonest = {}
    for h in hours:
        sid = ids[h["spot"]]
        if sid in first and h["valid_time"] == first[sid]["valid_time"] \
                and h["stars_old"] == h["stars_published"] and h["stars_new"] is not None:
            soonest[sid] = h["stars_new"]
    report["homepage"] = homepage(first, names, soonest)
    report["homepage"]["moved_soonest_replayed"] = sorted(names[s][0] for s in soonest)
    return report


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #
HOUR_COLUMNS = ("spot", "valid_time", "nwps_cycle", "swell_source", "wind", "wind_old",
                "wind_new", "inputs_match", "filled_from", "exact_hour_blank",
                "stars_published", "stars_old", "stars_new", "face_published", "face_old",
                "face_new", "hs_old", "hs_new", "tp_old", "tp_new", "swell_hs_old",
                "swell_hs_new", "rated_tp_old", "rated_tp_new")


def write_csv(path, hours):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HOUR_COLUMNS)
        w.writeheader()
        for h in hours:
            w.writerow({k: h.get(k) for k in HOUR_COLUMNS})


def _v(x, fmt="{:.2f}"):
    return "-" if x is None else fmt.format(x)


def _offset(off):
    return {0: "the hour", -1: "the hour before", 1: "the hour after"}.get(off, "none")


def print_report(rep):
    print(f"\nNode preview of the published window from {rep['since']}")
    for m in rep["moved"]:
        print(f"  {m['spot']} ({m['wfo']}): ({m['old'][0]}, {m['old'][1]}) -> "
              f"({m['new'][0]}, {m['new'][1]}), {m['new_distance_m']} m from the spot")
    for wfo, c in rep["cycles"].items():
        print(f"  {wfo}: NOMADS lists {len(c['listed'])} cycles {', '.join(c['listed'])}; "
              f"read {', '.join(c['read']) or 'none'}")
    for name, checks in rep["node_checks"].items():
        bad = [k for k, c in checks.items() if not (c["wet"] and c["same_cell"])]
        offs = sorted({c["offset_km"] for c in checks.values()})
        print(f"  {name}: new node a wet cell, the cell nwps.fetch samples, in "
              f"{len(checks) - len(bad)} of {len(checks)} cycles; offset from the cell centre "
              f"{', '.join(f'{o} km' for o in offs)}"
              + (f"; NOT in {', '.join(bad)}" if bad else ""))
    g = rep["gate"]
    print(f"\n=== MERGE GATE: the old points against the published window: {g['hours']} hours, "
          f"{g['input_differences']} input differences (hs, tp, dp, swell_hs), "
          f"{g['star_differences']} star differences, {g['filled']} filled from a neighbouring "
          f"hour (the hour blank in the file) -> {'PASS' if g['pass'] else 'FAIL'} ===")
    for h in rep["hours"]:
        if not h["inputs_match"] or h["stars_old"] != h["stars_published"]:
            print(f"    {h['spot']} {h['valid_time']}: inputs {'ok' if h['inputs_match'] else 'DIFFER'}"
                  f" (series hour {_offset(h.get('filled_from'))}, hour blank in the file "
                  f"{h.get('exact_hour_blank')}), published {h['stars_published']} replayed "
                  f"{h['stars_old']}")
    for h in rep["hours"]:
        if h["inputs_match"] and h.get("filled_from") in (-1, 1):
            print(f"    filled: {h['spot']} {h['valid_time']} from the hour "
                  f"{'before' if h['filled_from'] == -1 else 'after'}")
    print("\n  not replayed:")
    for name, c in rep["not_replayed"].items():
        print(f"    {name}: " + (", ".join(f"{k} {v}" for k, v in sorted(c.items())) or "none"))
    print("\n=== By spot, old point -> new point ===")
    for name, s in rep["by_spot"].items():
        print(f"  {name}: {s['hours']} hours; stars up {s['stars_up']}, down {s['stars_down']}, "
              f"same {s['stars_same']}; mean stars {_v(s['mean_stars_old'])} -> "
              f"{_v(s['mean_stars_new'])}, face {_v(s['mean_face_old'])} -> "
              f"{_v(s['mean_face_new'])} ft, hs {_v(s['mean_hs_old'])} -> {_v(s['mean_hs_new'])} m")
    if rep.get("references") or rep.get("references_missing"):
        print("\n=== Beside the reference spots (published), on the same hours: old / new / "
              "reference ===")
        for name, refs in rep.get("references", {}).items():
            for ref, r in refs.items():
                print(f"  {name} vs {ref}, {r['hours']} hours: stars {_v(r['stars_old'])} / "
                      f"{_v(r['stars_new'])} / {_v(r['stars_reference'])}; face "
                      f"{_v(r['face_old'])} / {_v(r['face_new'])} / {_v(r['face_reference'])} ft")
        if rep.get("references_missing"):
            print(f"  no spot named: {', '.join(rep['references_missing'])}")
    print("\n=== Every replayed hour: stars, face ft, hs m, node period s (rated period s) ===")
    for h in rep["hours"]:
        mark = "  *" if h["stars_new"] != h["stars_old"] else ""
        print(f"  {h['spot']:20} {h['valid_time'][:16]}  stars {_v(h['stars_old'], '{:.1f}')} -> "
              f"{_v(h['stars_new'], '{:.1f}')}  face {_v(h['face_old'])} -> {_v(h['face_new'])}  "
              f"hs {_v(h['hs_old'])} -> {_v(h['hs_new'])}  tp {_v(h['tp_old'])} -> "
              f"{_v(h['tp_new'])} ({_v(h['rated_tp_old'])} -> {_v(h['rated_tp_new'])})  "
              f"wind {h['wind']}{mark}")
    hp = rep["homepage"]
    print("\n=== Homepage, Best conditions right now: before | after ===")
    for i in range(max(len(hp["before"]), len(hp["after"]))):
        b = hp["before"][i] if i < len(hp["before"]) else None
        a = hp["after"][i] if i < len(hp["after"]) else None
        left = f"{b['spot']} ({b['state']}) {b['stars']}" if b else ""
        right = f"{a['spot']} ({a['state']}) {a['stars']}{' [moved]' if a['moved'] else ''}" if a else ""
        print(f"  {i + 1:2}. {left:40} | {right}")
    print(f"  drop out: {', '.join(hp['drop_out']) or 'none'}; come in: "
          f"{', '.join(hp['come_in']) or 'none'}")
    print(f"  moved spots whose soonest hour was replayed: "
          f"{', '.join(hp['moved_soonest_replayed']) or 'none'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--baseline", required=True,
                   help="spots file the published window was rated with (main's roster)")
    p.add_argument("--roster", default="pipeline/spots_enriched.json",
                   help="spots file with the moved nodes (default: this checkout's)")
    p.add_argument("--since", default=None, help="ISO start of the window (default: now)")
    p.add_argument("--reference", default="",
                   help="comma-separated slugs of spots to show beside the moved ones")
    p.add_argument("--out", default=None, help="write the report as JSON here")
    p.add_argument("--csv", default=None, help="write every replayed hour as CSV here")
    args = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    since = args.since or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with open(args.baseline) as f:
        baseline = json.load(f)
    with open(args.roster) as f:
        roster = json.load(f)
    refs = [r.strip() for r in args.reference.split(",") if r.strip()]
    rep = run(client, baseline, roster, since, references=refs)
    print_report(rep)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(rep, f, indent=1, default=str)
    if args.csv:
        write_csv(args.csv, rep["hours"])
    return 0 if rep["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
