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

THE MERGE GATE. At the old node (the cycle and cell the row was rated from) the replay must give,
hour for hour, the row as the pipeline stored it:
  hs, tp, dp  the file's own swh, perpw and dirpw at the EXACT hour, to 3 decimals: nwps.fetch
              writes them into the hour's record and leaves a blank one out, and interpret
              stores the record as it is, so a blank value is stored empty;
  swell_hs    the shts of the hour the override rated from, which it writes over the record's;
  stars, face the published stars and face_ft_raw, from that same hour.
All must have 0 differences on at least one hour. The hour the override rated from is the one
nwps_nearshore.series_hour gives, the function apply_nwps_overrides itself calls: the hour, else
the hour before, else the hour after. A neighbour's values count only where the hour is blank
in the cycle's file at the old node's cell (no step, or swh, perpw or dirpw NaN); a fill over
an hour the file holds is a difference, and so is a stored value where the file is blank. Every
difference is printed with the stored and the expected values. The gate line counts the filled
hours. Then every replayed hour is listed with its
stars, face, height and period at the old and the new node, and the homepage's "Best
conditions right now" (each spot's soonest hour from --since, ten highest) before and after.
Each new node is also checked against every cycle read: a wet cell centre, and the same cell
nwps.fetch samples (nearest grid row and column), not a neighbour.

REFERENCE SPOTS (--reference, comma-separated slugs). Spots that did not move, shown beside the
moved ones: their published stars and face (face_ft_raw, the face the replay gives) on the same
hours as each moved spot's replayed ones, averaged and hour by hour.

BUOY REFERENCES (a token SLUG=BUOY in --reference, e.g. hubbard-creek=46015). For a moved spot
with no NWPS neighbour to stand beside, an NDBC buoy: the published hours are replayed from
BUOY_LOOKBACK_H hours before --since instead of from --since (NOMADS keeps about two days of
cycles), and on every replayed hour already past, the buoy's reading from buoy_observations
nearest the hour within BUOY_PAIR_MIN minutes is set beside the old and the new node's NWPS Hs.
Only hours the cell itself holds count (not ones filled from a neighbouring hour). Per spot: the
paired hours, the buoy's mean Hs, and for each node the median of node / buoy and the share of
hours within 20% of the buoy. The homepage list still starts at --since.
The readings come from buoy_observations, which holds only the buoys some spot is assigned
(nearest_buoy_id or fallback_buoy_ids: the ones the pipeline fetches). For any other buoy it holds
nothing, so the readings are then read from NDBC's realtime2 file for it (45 days), the file the
pipeline itself reads; report["buoys"][spot]["source"] names where they came from. Run 71's
Hobuck Beach and 46087 (Neah Bay) got 0 paired hours this way: 46087 is no spot's buoy.

THE CYCLE FILES. For each cycle read, a HEAD request to NOMADS right after the download gives
the file's Last-Modified time and Content-Length, set beside the size of the file read and the
time of the check (report["cycles"][wfo]["files"]). A Last-Modified later than the time the
pipeline read a cycle means NOMADS re-posted it since, and the published rows were rated from an
earlier copy. A failed HEAD is recorded as an error and does not stop the replay.

DOUBLE-POSTED CYCLES. A WFO can post a cycle twice, and NOMADS then serves either copy from the
same URL for days (MFR 2026-11-07 12Z: 55,879,399 bytes last modified 19:07:09Z, and 43,017,313
bytes last modified 20:01:05Z; run 70). The pipeline does not record which copy it read, so the
replay cannot be held to the published hours of such a cycle. A cycle is EXCLUDED from the gate,
and reported apart with its evidence and its own difference counts, when either
  * the file node-preview downloaded differs in size from what NOMADS's HEAD gave for it, or
  * the NOMADS publication logger (workflow nwps-publication-log; its NWPSPUB lines, passed with
    --logger-log) saw the file in two different versions: two sightings whose sizes differ, or
    whose last-modified times are REPOST_MIN_GAP_S (10 minutes) or more apart.
The 10 minutes: over the logger's cycles of 2026-10-30 to 11-09, 46 files were seen with the
same size and last-modified times 1 to 62 seconds apart (NOMADS servers each holding their own
copy of one post), and 5 were re-posted, 25 minutes to 6 h 20 min apart, 4 of them at a new
size (EKA 11-03 12Z, HGX 11-03 00Z, TAE 11-03 12Z, MFR 11-07 12Z, MFR 11-08 00Z).
Its hours are still replayed and listed, and still count in the per-spot and reference figures.

OLD NODE ON LAND. nwps.fetch reads a spot's fixed (baseline) node only while that node's cell
holds a wave value (swh or shts) at some step of the cycle (nwps._cell_is_water); when the cell
has none, it walks seaward from the spot (_find_offshore_point) and the published rows come from
the cell the walk reaches. This replay does not follow the walk: it reads the nearest cell wet
at f000 (node_cell), a different cell. From NWPS v1.5 (2026-11-09 12Z) MTR's cells at the
Pacifica Linda Mar / Rockaway Beach Pacifica node (37.61754, -122.5) and at Avila Beach's
(35.15796, -120.74) hold no wave value at any step, so the replay at the old node cannot be held
to their published hours. For each cycle, a spot whose old node's cell holds no wave value at any
step is EXCLUDED from the gate for that cycle and reported apart, with the evidence (the node,
its cell and that cell's centre, the steps read, and the cell replayed instead) and its own
difference counts. Its hours are still replayed and listed, and still count in the per-spot and
reference figures. Every other spot's hours must match exactly, as before.

WHAT IT CANNOT SEE. Hours another rater wrote (MOP, WW3, rate_spot's NWPS path) and hours whose
cycle NOMADS no longer holds are counted, by reason, not replayed.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from email.utils import parsedate_to_datetime
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .forecast.nwps_nearshore import (_haversine_km, _iso_to_epoch, _nearest_cell,
                                      _node_value, _region_for, apply_nwps_overrides,
                                      cycle_path, load_cycle, recent_cycles,
                                      series_hour)

NWPS_KINDS = ("nwps_height_ww3_dir", "nwps")
NODE_FIELDS = ("nwps_node_lat", "nwps_node_lng")
CYCLES_LISTED = 16            # more than NOMADS keeps of any office (run 50: 6, ilm 12)
INPUT_TOL = 5e-4              # stored values carry 3 decimals
FACE_TOL = 5e-3               # face_ft_raw is stored to 2 decimals
INPUT_COLS = ("hs", "tp", "dp", "swell_hs")
SAME_CELL_KM = 0.01
BUOY_LOOKBACK_H = 48          # NOMADS keeps about two days of cycles
BUOY_PAIR_MIN = 30            # a buoy reading within 30 minutes of the hour
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


WAVE_FIELDS = ("swh", "shts")   # the wave variables a cycle holds; nwps._cell_is_water tests them


def _grid_cell(cycle, lat, lng):
    """(i, j): the nearest grid row and column to (lat, lng), an argmin on each axis, as
    nwps.fetch's .sel(method="nearest") takes them."""
    lats, lons = cycle["lats"], cycle["lons"]
    li = min(range(lats.shape[0]), key=lambda r: abs(float(lats[r, 0]) - lat))
    lj = min(range(lons.shape[1]), key=lambda c: abs(float(lons[0, c]) - lng))
    return li, lj


def _cell_has_water(cycle, i, j):
    """nwps._cell_is_water's test: a wave value at the cell at ANY step of the cycle. A cell on
    the wet/dry line can be blank at f000 (the cycle's land mask) and wet at later hours."""
    for (short, _fh), arr in cycle["fields"].items():
        if short in WAVE_FIELDS:
            v = float(arr[i, j])
            if v == v:
                return True
    return False


def node_series(spot, cycle):
    """{hour_bucket: (swh, perpw, dirpw, shts)} at the cell nwps.fetch reads for the spot's
    node (node_cell), every value rounded to 3 decimals as nwps.fetch writes them; an hour
    missing swh, perpw or dirpw is left out, as nwps.fetch's record leaves it. None when there
    is no cell or no complete hour."""
    cell = node_cell(cycle, spot)
    if cell is None:
        return None
    i, j = cell
    out = {}
    for fh in cycle["steps"]:
        swh, per, dpw = (_node_value(cycle, sh, fh, i, j) for sh in ("swh", "perpw", "dirpw"))
        if swh is None or per is None or dpw is None:
            continue
        shts = _node_value(cycle, "shts", fh, i, j)
        k = int((cycle["cycle_dt"] + timedelta(hours=fh)).timestamp() // 3600)
        out[k] = tuple(None if v is None else round(v, 3) for v in (swh, per, dpw, shts))
    return out or None


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
    """(i, j) of the cell nwps.fetch reads for the spot's baked node: the nearest grid row and
    column whenever that cell has water at any step of the cycle (nwps._baked_node_is_water,
    through nwps._cell_is_water). Only when it has none, the nearest cell wet at f000, as
    nwps_series_by_hour takes it (nwps.fetch would then walk to a cell this does not replay).

    WHY NOT nwps_series_by_hour's cell. It takes the nearest cell wet at f000, the cycle's land
    mask. A cell that dries at low tide is blank at f000 in a cycle that starts at low water, so
    the old node was replayed from its neighbour while nwps.fetch, and the published row, read
    the cell itself: sw1-raycast run 68's gate failed on every such cycle of Bandon Beach,
    Hubbard Creek and Short Sands Beach York and on no other spot."""
    lat, lng = spot["nwps_node_lat"], spot["nwps_node_lng"]
    li, lj = _grid_cell(cycle, lat, lng)
    if _cell_has_water(cycle, li, lj):
        return li, lj
    cell = _nearest_cell(cycle, lat, lng)
    return None if cell is None else (cell[0], cell[1])


def old_node_on_land(cycle, spot):
    """The evidence that *spot*'s fixed node is land in this cycle, or None when its cell (the
    nearest grid row and column) holds a wave value at some step, the cell nwps.fetch then reads:
    {node, cell, cell_centre, offset_km, steps, replayed_cell, replayed_centre, replayed_km}.
    replayed_* is the cell node_cell falls back to (None when the grid has no wet cell)."""
    lat, lng = spot["nwps_node_lat"], spot["nwps_node_lng"]
    i, j = _grid_cell(cycle, lat, lng)
    if _cell_has_water(cycle, i, j):
        return None

    def centre(a, b):
        return [round(float(cycle["lats"][a, b]), 5), round(float(cycle["lons"][a, b]), 5)]

    def km(a, b):
        return round(_haversine_km(lat, lng, float(cycle["lats"][a, b]),
                                   float(cycle["lons"][a, b])), 3)
    rep = node_cell(cycle, spot)
    return {"node": [lat, lng], "cell": [i, j], "cell_centre": centre(i, j),
            "offset_km": km(i, j), "steps": len(cycle["steps"]),
            "replayed_cell": None if rep is None else [rep[0], rep[1]],
            "replayed_centre": None if rep is None else centre(*rep),
            "replayed_km": None if rep is None else km(*rep)}


def land_evidence_line(ev):
    """One report line for old_node_on_land's evidence."""
    out = (f"node ({ev['node'][0]}, {ev['node'][1]}) is cell ({ev['cell'][0]}, {ev['cell'][1]}), "
           f"centre ({ev['cell_centre'][0]}, {ev['cell_centre'][1]}), {ev['offset_km']} km away: "
           f"no wave value (swh or shts) there at any of the cycle's {ev['steps']} steps, so "
           f"nwps.fetch walked seaward and the published hours are not from this node; ")
    if ev["replayed_cell"] is None:
        return out + "the replay found no wet cell to read instead"
    return out + (f"the replay read cell ({ev['replayed_cell'][0]}, {ev['replayed_cell'][1]}), "
                  f"centre ({ev['replayed_centre'][0]}, {ev['replayed_centre'][1]}), "
                  f"{ev['replayed_km']} km away, instead")


def exact_hour_values(cycle, cell, epoch):
    """(swh, perpw, dirpw) in the cycle's file at *cell* for the hour at *epoch*, each to 3
    decimals as nwps.fetch writes it into the hour's record; None where blank (NaN), all three
    None when the file has no step for the hour."""
    fh = round((epoch - cycle["cycle_dt"].timestamp()) / 3600)
    if fh not in cycle["steps"]:
        return None, None, None
    vals = (_node_value(cycle, short, fh, cell[0], cell[1]) for short in ("swh", "perpw", "dirpw"))
    return tuple(None if v is None else round(v, 3) for v in vals)


def exact_hour_blank(cycle, cell, epoch):
    """Is the hour at *epoch* blank in the cycle's file at *cell*: no step for it, or its swh,
    perpw or dirpw NaN there (the hours nwps_series_by_hour leaves out)?"""
    return any(v is None for v in exact_hour_values(cycle, cell, epoch))


def node_check(cycle, lat, lng):
    """{wet, same_cell, offset_km} for a baked node in one cycle: is the cell nwps.fetch takes
    (nearest grid row and column) wet at f000, is it the cell replayed here (node_cell), and
    how far is the node from that cell's centre."""
    lats, lons, mask = cycle["lats"], cycle["lons"], cycle["mask"]
    li, lj = _grid_cell(cycle, lat, lng)
    near = node_cell(cycle, {"nwps_node_lat": lat, "nwps_node_lng": lng})
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


def expected_inputs(exact, rated):
    """The stored inputs as the pipeline writes them for an hour: hs, tp and dp the file's own
    values at the exact hour (*exact*, from exact_hour_values; empty where blank), swell_hs the
    shts of the series values the override rated from (*rated*, from series_hour)."""
    return {"hs": exact[0], "tp": exact[1], "dp": exact[2],
            "swell_hs": None if rated is None else rated[3]}


def input_diffs(row, expected):
    """The columns of INPUT_COLS whose stored value differs from *expected*: one empty and the
    other not, or both set and more than INPUT_TOL apart."""
    out = []
    for col in INPUT_COLS:
        stored, want = _num(row.get(col)), expected[col]
        if (stored is None) != (want is None) or (
                want is not None and abs(stored - want) > INPUT_TOL):
            out.append(col)
    return out


def face_differs(published, replayed):
    """Do the published and replayed faces differ: one empty, or more than FACE_TOL apart?"""
    if published is None or replayed is None:
        return (published is None) != (replayed is None)
    return abs(published - replayed) > FACE_TOL


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
    bad_face = sum(face_differs(h["face_published"], h["face_old"]) for h in hours)
    filled = sum(h["inputs_match"] and h.get("filled_from") in (-1, 1) for h in hours)
    return {"hours": n, "input_differences": bad_inputs, "star_differences": bad_stars,
            "face_differences": bad_face, "filled": filled,
            "pass": n > 0 and bad_inputs == 0 and bad_stars == 0 and bad_face == 0}


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


def parse_references(text):
    """(reference spot slugs, {moved spot slug: buoy id}) from --reference: a token SLUG=BUOY
    names a buoy for a moved spot, any other token a reference spot."""
    spots, buoys = [], {}
    for tok in (t.strip() for t in (text or "").split(",")):
        if not tok:
            continue
        if "=" in tok:
            sl, b = (x.strip() for x in tok.split("=", 1))
            if sl and b:
                buoys[sl] = b.lower()
        else:
            spots.append(tok)
    return spots, buoys


def buoy_pairs(hours, obs, now_epoch):
    """Sets h["buoy_hs"] on each of *hours* already past at *now_epoch*: the buoy Hs nearest the
    hour within BUOY_PAIR_MIN minutes, else None. *obs*: [(epoch, hs)] with hs set, any order."""
    obs = sorted(obs)
    for h in hours:
        t = _iso_to_epoch(h["valid_time"])
        h["buoy_hs"] = None
        if t is None or t > now_epoch or not obs:
            continue
        best = min(obs, key=lambda o: (abs(o[0] - t), o[0]))
        if abs(best[0] - t) <= BUOY_PAIR_MIN * 60:
            h["buoy_hs"] = best[1]
    return hours


def buoy_summary(hours, buoy):
    """The paired hours of one moved spot against its buoy: the buoy's mean Hs, and for each
    node the hours it holds itself (not filled from a neighbouring hour), the median of
    node / buoy and the share within 20% of the buoy."""
    paired = [h for h in hours if h.get("buoy_hs")]

    def node(key, off_key):
        r = sorted(h[key] / h["buoy_hs"] for h in paired
                   if h.get(key) is not None and h.get(off_key) == 0)
        if not r:
            return {"hours": 0, "median": None, "within_20": None}
        mid = len(r) // 2
        med = r[mid] if len(r) % 2 else (r[mid - 1] + r[mid]) / 2
        return {"hours": len(r), "median": round(med, 3),
                "within_20": round(sum(abs(x - 1) <= 0.2 for x in r) / len(r), 3)}
    return {"buoy": buoy, "hours": len(paired),
            "buoy_mean": _mean([h["buoy_hs"] for h in paired]),
            "old": node("hs_old", "filled_from"), "new": node("hs_new", "filled_from_new")}


def ndbc_wave_heights(text):
    """[(epoch, hs)] from an NDBC realtime2 standard-meteorological file: every row with a wave
    height (WVHT), parsed by the pipeline's own reader (forecast/buoys._parse_realtime2)."""
    from .forecast.buoys import _STD_FIELDS, _parse_realtime2
    return [(datetime.fromisoformat(o["time"]).timestamp(), o["wave_height_m"])
            for o in _parse_realtime2(text or "", _STD_FIELDS)
            if o.get("wave_height_m") is not None]


def fetch_ndbc_obs(buoy, since, get=None):
    """[(epoch, hs)] at or after *since* from NDBC's realtime2 file for *buoy*."""
    from .config import NDBC_REALTIME2_BASE
    if get is None:
        from .http import get
    t0 = _iso_to_epoch(since)
    resp = get(f"{NDBC_REALTIME2_BASE}/{str(buoy).upper()}.txt")
    return [(t, hs) for t, hs in ndbc_wave_heights(resp.text) if t >= t0]


def buoy_readings(buoy, since, stored, ndbc):
    """(readings, source) for one buoy: *stored*(buoy, since) from buoy_observations, and when
    that has none, *ndbc*(buoy, since) from NDBC's realtime2 file. A failed NDBC read gives no
    readings and says why; it does not stop the report."""
    obs = stored(buoy, since)
    if obs:
        return obs, "buoy_observations"
    try:
        obs = ndbc(buoy, since)
    except Exception as e:  # noqa: BLE001  a report line, never a reason to stop
        return [], (f"none: buoy_observations has no {buoy} readings and NDBC realtime2 "
                    f"failed ({type(e).__name__}: {e})")
    if obs:
        return obs, "NDBC realtime2 (buoy_observations has none: no spot is assigned this buoy)"
    return [], f"none: no {buoy} wave height in buoy_observations or NDBC realtime2"


def fetch_buoy_obs(client, buoy, since):
    """[(epoch, hs)] from buoy_observations for *buoy* since *since*, hs set."""
    out, frm = [], 0
    while True:
        page = (client.table("buoy_observations").select("observed_at, hs").eq("buoy_id", buoy)
                .gte("observed_at", since).order("observed_at").range(frm, frm + 999).execute().data
                or [])
        out.extend((_iso_to_epoch(r["observed_at"]), float(r["hs"])) for r in page
                   if r.get("hs") is not None and _iso_to_epoch(r["observed_at"]) is not None)
        if len(page) < 1000:
            return out
        frm += 1000


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
            vn, off_new = series_hour(s_new or {}, k)
            exact = exact_hour_values(cycle, c_old, epoch) if c_old else (None, None, None)
            blank = any(v is None for v in exact)
            # a neighbour's values stand for the hour only where the file leaves it blank
            fill_ok = off == 0 or (off in (-1, 1) and blank)
            want = expected_inputs(exact, vo)
            diffs = input_diffs(row, want) if vo is not None else list(INPUT_COLS)
            hours.append({
                "spot": new["name"], "valid_time": row["valid_time"],
                "nwps_cycle": row.get("nwps_cycle"), "swell_source": row.get("swell_source"),
                "wind": wind, "wind_old": [a["wind_speed"], a["wind_dir"]],
                "wind_new": [b["wind_speed"], b["wind_dir"]],
                "inputs_match": fill_ok and not diffs,
                "input_diffs": diffs if fill_ok else diffs + ["fill over an hour the file holds"],
                "stored": {c: _num(row.get(c)) for c in INPUT_COLS}, "expected": want,
                "filled_from": off, "filled_from_new": off_new, "exact_hour_blank": blank,
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
def http_date_iso(value):
    """An HTTP date (Last-Modified) as ISO UTC to the second, or None when absent or unreadable."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def nomads_head(url, timeout=60):
    """The response headers of a HEAD request to *url* (case-insensitive mapping)."""
    import urllib.request
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "stormy-petrel-nwps"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.headers


def file_stamp(url, path, *, head=nomads_head, now=None):
    """What NOMADS says of a cycle file just read: {url, last_modified, content_length,
    size_read, checked_at}, and {error} in place of the first two when the HEAD fails.
    size_read is the size of the file on disk at *path* (None when absent)."""
    out = {"url": url, "checked_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "size_read": os.path.getsize(path) if path and os.path.exists(path) else None}
    try:
        h = head(url)
    except Exception as e:  # noqa: BLE001  a report line, never a reason to stop the replay
        out.update(last_modified=None, content_length=None, error=f"{type(e).__name__}: {e}")
        return out
    length = h.get("Content-Length")
    out.update(last_modified=http_date_iso(h.get("Last-Modified")),
               content_length=int(length) if length and str(length).isdigit() else None)
    return out


def parse_logger_lines(lines):
    """{(wfo, 'YYYYMMDDCC'): [{bytes, last_modified, first_seen}, ...]} from the publication
    logger's NWPSPUB lines: each distinct (bytes, last_modified) a file was seen with, in the order
    first seen. Only file HEADs that answered 200 with both values count."""
    seen = {}
    for line in lines:
        i = line.find("NWPSPUB ")
        if i < 0:
            continue
        try:
            rec = json.loads(line[i + len("NWPSPUB "):])
        except ValueError:
            continue
        if (not isinstance(rec, dict) or rec.get("kind") != "file" or rec.get("status") != 200
                or rec.get("bytes") is None or not rec.get("last_modified")):
            continue
        key = (rec.get("wfo"), f"{rec.get('date')}{rec.get('cycle')}")
        versions = seen.setdefault(key, [])
        pair = (rec["bytes"], rec["last_modified"])
        known = next((v for v in versions if (v["bytes"], v["last_modified"]) == pair), None)
        if known is None:
            versions.append({"bytes": pair[0], "last_modified": pair[1],
                             "first_seen": rec.get("checked_at")})
        elif rec.get("checked_at") and (known["first_seen"] is None
                                        or rec["checked_at"] < known["first_seen"]):
            known["first_seen"] = rec["checked_at"]
    for versions in seen.values():
        versions.sort(key=lambda v: v["first_seen"] or "")
    return seen


REPOST_MIN_GAP_S = 600       # last-modified times this far apart are two posts, not two copies


def distinct_versions(versions):
    """Did the logger see two different posts of a file: two sightings with different sizes,
    or last-modified times REPOST_MIN_GAP_S or more apart? Sightings of one size seconds apart
    are NOMADS servers' copies of one post. An unreadable time counts as different."""
    vs = list(versions or [])
    for i, a in enumerate(vs):
        for b in vs[i + 1:]:
            if a["bytes"] != b["bytes"]:
                return True
            try:
                ta = datetime.fromisoformat(str(a["last_modified"]).replace("Z", "+00:00"))
                tb = datetime.fromisoformat(str(b["last_modified"]).replace("Z", "+00:00"))
            except ValueError:
                return True
            if abs((ta - tb).total_seconds()) >= REPOST_MIN_GAP_S:
                return True
    return False


def double_post_reasons(stamp, versions):
    """Why a cycle file is excluded from the gate: [] when it is not. *stamp* is file_stamp's
    record (or None), *versions* the logger's list for the same file (or None)."""
    out = []
    st = stamp or {}
    if (st.get("size_read") is not None and st.get("content_length") is not None
            and st["size_read"] != st["content_length"]):
        out.append(f"downloaded {st['size_read']} bytes; NOMADS's HEAD gave "
                   f"{st['content_length']} bytes, last modified {st.get('last_modified') or 'not given'}")
    if versions and len(versions) > 1 and distinct_versions(versions):
        out.append(f"the publication logger saw {len(versions)} versions: " + "; ".join(
            f"{v['bytes']} bytes, last modified {v['last_modified']} (first seen "
            f"{v['first_seen'] or 'unknown'})" for v in versions))
    return out


def stat_nomads_cycle(wfo, cycle):
    """file_stamp for a (date, cc, url) cycle load_cycle has just read for *wfo*."""
    date, cc, url = cycle
    return file_stamp(url, cycle_path(wfo, date, cc))


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
        apply=apply_nwps_overrides, references=(), buoys=None, now=None, buoy_obs=None,
        stat_cycle=None, logger_versions=None, ndbc_obs=None):
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
    buoys = buoys or {}
    now_epoch = (now or datetime.now(timezone.utc)).timestamp()
    replay_since = since
    if buoys:
        start = datetime.fromtimestamp(_iso_to_epoch(since) - BUOY_LOOKBACK_H * 3600, timezone.utc)
        replay_since = start.isoformat()
    report = {"since": since, "replayed_from": replay_since, "moved": [], "not_replayed": {},
              "cycles": {}, "node_checks": {}, "excluded": {}, "excluded_land": {},
              "logger_log": logger_versions is not None}
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
        report["cycles"][wfo] = {"listed": sorted(f"{d}{c}" for d, c in listed), "read": [],
                                 "files": {}}
        by_cycle = defaultdict(lambda: defaultdict(list))
        for old, new in wpairs:
            skipped = report["not_replayed"].setdefault(new["name"], Counter())
            for row in fetch_rows(client, ids[new["name"]], replay_since):
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
            stamp = None
            if stat_cycle is not None:
                stamp = report["cycles"][wfo]["files"]["".join(key)] = stat_cycle(
                    wfo, (key[0], key[1], listed[key]))
            name = f"{wfo} {''.join(key)}"
            reasons = double_post_reasons(stamp, (logger_versions or {}).get((wfo, "".join(key))))
            if reasons:
                report["excluded"][name] = {"reasons": reasons}
            on_land = set()
            for old, new in wpairs:
                ev = old_node_on_land(cycle, old) if by_cycle[key].get(new["name"]) else None
                if ev is not None:
                    on_land.add(new["name"])
                    report["excluded_land"][f"{new['name']}, {name}"] = {
                        "spot": new["name"], "cycle_file": name, "evidence": ev}
            got, checks = replay_cycle(wpairs, by_cycle[key], cycle, ceiling_factors, m, apply)
            for h in got:
                h["cycle_file"] = name
                h["excluded_for"] = ((["double-posted"] if reasons else [])
                                     + (["old node on land"] if h["spot"] in on_land else []))
                h["gate_excluded"] = bool(h["excluded_for"])
            hours.extend(got)
            for name, c in checks.items():
                report["node_checks"].setdefault(name, {})["".join(key)] = c
    hours.sort(key=lambda h: (h["spot"], h["valid_time"]))
    report["not_replayed"] = {k: dict(v) for k, v in report["not_replayed"].items()}
    report["gate"] = gate([h for h in hours if not h.get("gate_excluded")])
    report["gate"]["excluded_cycles"] = sorted(report["excluded"])
    for name, ex in report["excluded"].items():
        ex.update(gate([h for h in hours if h.get("cycle_file") == name]))
        ex.pop("pass")
    report["gate"]["excluded_land"] = sorted(report["excluded_land"])
    for ex in report["excluded_land"].values():
        ex.update(gate([h for h in hours if h.get("cycle_file") == ex["cycle_file"]
                        and h["spot"] == ex["spot"]]))
        ex.pop("pass")
    report["by_spot"] = spot_summary(hours)
    from .apply_nwps_assignments import _slug
    by_slug = {_slug(n): (sid, n) for sid, (n, _) in names.items()}
    refs = {}
    report["references_missing"] = [r for r in references if r not in by_slug]
    for r in references:
        if r in by_slug:
            sid, n = by_slug[r]
            refs[n] = {row["valid_time"]: row for row in fetch_rows(client, sid, replay_since)
                       if row.get("stars") is not None}
    report["references"] = reference_summary(hours, refs)
    for h in hours:
        h["reference"] = {n: {"stars": _num(rows[h["valid_time"]].get("stars")),
                              "face": _num(rows[h["valid_time"]].get("face_ft_raw"))}
                          for n, rows in refs.items() if h["valid_time"] in rows}
    report["buoys"] = {}
    buoy_obs = buoy_obs or (lambda b, start: fetch_buoy_obs(client, b, start))
    ndbc_obs = ndbc_obs or fetch_ndbc_obs
    for old, new in pairs:
        b = buoys.get(_slug(new["name"]))
        if b:
            mine = [h for h in hours if h["spot"] == new["name"]]
            obs, source = buoy_readings(b, replay_since, buoy_obs, ndbc_obs)
            buoy_pairs(mine, obs, now_epoch)
            report["buoys"][new["name"]] = dict(buoy_summary(mine, b), source=source)
    report["buoys_missing"] = sorted(set(buoys) - {_slug(new["name"]) for _o, new in pairs})
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
                "wind_new", "inputs_match", "input_diffs", "stored", "expected", "filled_from",
                "exact_hour_blank", "filled_from_new", "buoy_hs", "reference",
                "stars_published", "stars_old", "stars_new", "face_published", "face_old",
                "face_new", "hs_old", "hs_new", "tp_old", "tp_new", "swell_hs_old",
                "swell_hs_new", "rated_tp_old", "rated_tp_new", "cycle_file", "gate_excluded",
                "excluded_for")


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


def file_line(wfo, key, st):
    """One report line for a cycle file's stamp."""
    head = f"{wfo} {key}: "
    if st.get("error"):
        return head + f"NOMADS HEAD failed ({st['error']}); read {_size(st.get('size_read'))} " \
                      f"at {st['checked_at']}"
    same = ("" if st.get("content_length") is None or st.get("size_read") is None
            else "; same size" if st["content_length"] == st["size_read"]
            else "; SIZE DIFFERS from the file read")
    return head + (f"last modified {st.get('last_modified') or 'not given'}, "
                   f"{_size(st.get('content_length'))} on NOMADS; read {_size(st.get('size_read'))} "
                   f"at {st['checked_at']}{same}")


def _size(n):
    return "size unknown" if n is None else f"{n} bytes"


def print_report(rep):
    print(f"\nNode preview of the published window from {rep['since']}"
          + (f" (replayed from {rep['replayed_from']} for the buoys)"
             if rep.get("replayed_from") not in (None, rep["since"]) else ""))
    for m in rep["moved"]:
        print(f"  {m['spot']} ({m['wfo']}): ({m['old'][0]}, {m['old'][1]}) -> "
              f"({m['new'][0]}, {m['new'][1]}), {m['new_distance_m']} m from the spot")
    for wfo, c in rep["cycles"].items():
        print(f"  {wfo}: NOMADS lists {len(c['listed'])} cycles {', '.join(c['listed'])}; "
              f"read {', '.join(c['read']) or 'none'}")
        for key, st in (c.get("files") or {}).items():
            print("    " + file_line(wfo, key, st))
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
          f"{g['star_differences']} star differences, {g['face_differences']} face differences, "
          f"{g['filled']} filled from a neighbouring hour (the hour blank in the file) -> "
          f"{'PASS' if g['pass'] else 'FAIL'} ===")
    if not rep.get("logger_log"):
        print("  (no publication-logger log given: only the size test applied)")
    for name, ex in (rep.get("excluded") or {}).items():
        print(f"  EXCLUDED from the gate, double-posted on NOMADS: {name}: {ex['hours']} hours, "
              f"{ex['input_differences']} input, {ex['star_differences']} star and "
              f"{ex['face_differences']} face differences")
        for r in ex["reasons"]:
            print(f"    - {r}")
    for name, ex in (rep.get("excluded_land") or {}).items():
        print(f"  EXCLUDED from the gate, old node on land in this cycle: {name}: "
              f"{ex['hours']} hours, {ex['input_differences']} input, "
              f"{ex['star_differences']} star and {ex['face_differences']} face differences")
        print(f"    - {land_evidence_line(ex['evidence'])}")
    for h in rep["hours"]:
        if (not h["inputs_match"] or h["stars_old"] != h["stars_published"]
                or face_differs(h["face_published"], h["face_old"])):
            print(f"    {h['spot']} {h['valid_time']}"
                  + (f" [{h.get('cycle_file')}, excluded]" if h.get("gate_excluded") else "")
                  + ": inputs "
                  f"{'ok' if h['inputs_match'] else 'DIFFER ' + ', '.join(h.get('input_diffs') or [])}"
                  f" (series hour {_offset(h.get('filled_from'))}, hour blank in the file "
                  f"{h.get('exact_hour_blank')}); stars published {h['stars_published']} replayed "
                  f"{h['stars_old']}; face published {_v(h['face_published'])} replayed "
                  f"{_v(h['face_old'])}")
            st, ex = h.get("stored") or {}, h.get("expected") or {}
            print("      stored   " + "  ".join(f"{c} {_v(st.get(c), '{:.3f}')}" for c in INPUT_COLS))
            print("      expected " + "  ".join(f"{c} {_v(ex.get(c), '{:.3f}')}" for c in INPUT_COLS))
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
    if rep.get("buoys") or rep.get("buoys_missing"):
        print("\n=== Beside the buoys, on the replayed hours already past: node / buoy ===")
        for name, b in rep.get("buoys", {}).items():
            o, n = b["old"], b["new"]
            print(f"  {name} vs NDBC {b['buoy']}: {b['hours']} paired hours, buoy mean "
                  f"{_v(b['buoy_mean'])} m; old node median {_v(o['median'])} "
                  f"({o['hours']} h, {_v(o['within_20'])} within 20%); new node median "
                  f"{_v(n['median'])} ({n['hours']} h, {_v(n['within_20'])} within 20%)"
                  + (f"; readings: {b['source']}" if b.get("source") else ""))
        if rep.get("buoys_missing"):
            print(f"  not a moved spot: {', '.join(rep['buoys_missing'])}")
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
              f"wind {h['wind']}"
              + "".join(f"  | {n} stars {_v(r['stars'], '{:.1f}')} face {_v(r['face'])}"
                        for n, r in (h.get("reference") or {}).items())
              + mark)
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
                   help="comma-separated slugs of spots to show beside the moved ones, and "
                        "SLUG=BUOY for a moved spot's NDBC buoy")
    p.add_argument("--out", default=None, help="write the report as JSON here")
    p.add_argument("--csv", default=None, help="write every replayed hour as CSV here")
    p.add_argument("--logger-log", default=None,
                   help="text holding the NWPS publication logger's NWPSPUB lines (its run logs)")
    args = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    since = args.since or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with open(args.baseline) as f:
        baseline = json.load(f)
    with open(args.roster) as f:
        roster = json.load(f)
    refs, buoys = parse_references(args.reference)
    logger = None
    if args.logger_log:
        with open(args.logger_log, encoding="utf-8", errors="replace") as f:
            logger = parse_logger_lines(f)
    rep = run(client, baseline, roster, since, references=refs, buoys=buoys,
              stat_cycle=stat_nomads_cycle, logger_versions=logger)
    print_report(rep)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(rep, f, indent=1, default=str)
    if args.csv:
        write_csv(args.csv, rep["hours"])
    return 0 if rep["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
