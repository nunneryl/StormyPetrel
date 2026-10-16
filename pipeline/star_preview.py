"""Star preview: what the PUBLISHED forecast window would rate with a different swell window.

READ-ONLY. It SELECTs from the spots and forecasts tables and writes nothing to Supabase.
The only file it writes is its own report (--out).

    python -m pipeline.star_preview --arcs ARCS.json [--baseline BASELINE.json] [--out OUT.json]

ARCS and BASELINE are spots files in the spots_enriched.json shape; each spot's
swell_window_arcs, optimal_swell_dir, orientation_deg and mop_shore_normal are what it reads.
BASELINE is the geometry the published window was rated with (the roster on main); ARCS is
the geometry to preview.

HOW A ROW IS RE-SCORED. Every rater ends in composite_stars(effective_face, wind_mult,
tide_mult, chop_mult, period_quality), and the forecasts row stores the four multipliers, the
face, the WW3 partitions and the direction it was rated on. Only the swell window moves
here, so each row is replayed through the path that wrote it — read from its swell_source —
with the geometry swapped:

  nwps_height_ww3_dir, nwps   apply_nwps_overrides / nwps_stars. Direction and period come
                              from combine_ww3_partitions over the row's partitions, and
                              fall back to NWPS dirpw/perpw (the row's dp/tp) when no
                              partition has any gain. The face scales with period_factor
                              when the dominant partition, and so the period, changes.
  cdip_mop                    apply_mop_overrides / mop_stars: the MOP direction against the
                              MOP shore normal; the face does not depend on the window.
  ww3                         rate_spot's WW3 path: the combined height and the dominant
                              partition are recomputed. If no partition keeps any gain the
                              hour would fall to rate_spot's NWPS path, whose inputs are not
                              stored: counted as NOT REPLAYABLE, never guessed.
  nwps_swell, buoy,           rate_spot's NWPS path: face x gain on the stored direction,
  nwps_total, none            unless the new window gives a partition gain, which moves the
                              hour onto the WW3 path (as rate_spot would).

THE REPLAY CHECK. Each row is first re-scored with BASELINE and compared with the stars
actually published. Only rows that reproduce are counted in the before/after comparison;
the rest are reported by path, so a path the replay gets wrong is visible rather than
silently mixed into the result. Stored multipliers are rounded to 3 decimals, which can
move a rating that sits on a half-star boundary.

WHAT IT CANNOT SEE. A swell window also feeds the NWPS trust tier (nwps_nearshore._spot_tier)
and, through it, which spots the trust gate reports on. That is not a per-row quantity and
is not previewed here.

THE TIDE PREVIEW (--tide). The same replay, with the swell window held and the stored
tide_mult replaced on every hour of a spot whose tide preference is UNVERIFIED — its
tide_preference_source in the live spots table is not one of config.TIDE_RATED_SOURCES
(reviewed, researched, scraped), the sources the rating trusts:

    keep        tide_mult unchanged (the replay check alone)
    neutral     tide_mult = 1.0, as if the preference were 'all'
    damp:K      tide_mult = 1 - K x (1 - tide_mult): K of the penalty kept, 0 < K < 1
    rated       the rater's own rule, on every spot: tide_mult = interpret.tide_multiplier(
                the row's tide_norm, interpret.rated_tide_preference(the spot in
                --tide-roster)). With every source unverified it must equal neutral hour for
                hour — the check that the rating now publishes what neutral previewed.

    python -m pipeline.star_preview --tide neutral,rated [--baseline B.json]
        [--tide-roster R.json] [--out OUT.json]

It also prints a census: the live spots' (tide_preference, tide_preference_source) counts,
and over the window the stored tide_mult values, by preference, and how many of them
interpret.tide_multiplier reproduces from the row's tide_norm and the spot's preference.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .config import TIDE_RATED_SOURCES
from .interpret import (
    combine_ww3_partitions,
    composite_stars,
    directional_gain,
    face_ft,
    period_factor,
    period_quality,
    rated_tide_preference,
    tide_multiplier,
    M_TO_FT,
)

NWPS_KINDS = ("nwps_height_ww3_dir", "nwps")
MOP_KINDS = ("cdip_mop",)
WW3_KINDS = ("ww3",)
GROUP = {**{k: "NWPS" for k in NWPS_KINDS}, **{k: "MOP" for k in MOP_KINDS},
         **{k: "WW3" for k in WW3_KINDS}}

PARTITION_COLS = tuple(f"{p}_{f}" for p in ("swell_1", "swell_2", "swell_3", "wind_wave")
                       for f in ("hs", "tp", "dp"))
FORECAST_COLS = ("spot_id, valid_time, hs, tp, dp, swell_hs, swell_tp, swell_dp, "
                 + ", ".join(PARTITION_COLS)
                 + ", swell_source, face_ft, face_ft_raw, dir_gain, wind_mult, tide_mult, "
                   "chop_mult, period_quality, effective_size_ft, stars, tide_norm")


def group_of(kind):
    """'NWPS', 'MOP', 'WW3' or 'other' (rate_spot's NWPS-path fallbacks)."""
    return GROUP.get(kind, "other")


def _num(v):
    return None if v is None else float(v)


def _face(row):
    """The face the rater produced, before any face correction divided it."""
    raw = row.get("face_ft_raw")
    return _num(raw if raw is not None else row.get("face_ft"))


def _partitions(row):
    """The row's WW3 partitions in the shape combine_ww3_partitions reads, or None."""
    entry = {c: row.get(c) for c in PARTITION_COLS if row.get(c) is not None}
    return entry or None


def _mults(row):
    return (_num(row.get("wind_mult")) if row.get("wind_mult") is not None else 1.0,
            _num(row.get("tide_mult")) if row.get("tide_mult") is not None else 1.0,
            _num(row.get("chop_mult")) if row.get("chop_mult") is not None else 1.0)


def rescore(row, spot):
    """Re-score one published row with *spot*'s geometry.

    Returns {"stars", "gain", "dir", "path"} or {"stars": None, "why": ...} when the row
    cannot be replayed from what is stored."""
    kind = row.get("swell_source")
    arcs = spot.get("swell_window_arcs") or []
    optimal = spot.get("optimal_swell_dir")
    orient = spot.get("orientation_deg")
    wm, tm, cm = _mults(row)
    parts = _partitions(row)
    face = _face(row)

    if kind in NWPS_KINDS:
        comb = combine_ww3_partitions(parts, arcs, optimal, orient) if parts else None
        if comb is not None:
            d, per, path = comb["dp"], comb["tp"], "nwps_height_ww3_dir"
        else:
            d, per, path = _num(row.get("dp")), _num(row.get("tp")), "nwps"
        old_per = _num(row.get("swell_tp"))
        if d is None or per is None or face is None or not old_per or orient is None:
            return {"stars": None, "why": "nwps: direction, period or face missing"}
        swh = face / (period_factor(old_per, "ww3") * M_TO_FT)
        new_face = face_ft(swh, per, "ww3")
        dg = directional_gain(d, arcs, optimal if optimal is not None else orient, orient)
        return {"stars": composite_stars(new_face * dg, wm, tm, cm, period_quality(per)),
                "gain": dg, "dir": d, "path": path}

    if kind in MOP_KINDS:
        sn = spot.get("mop_shore_normal")
        if sn is None:
            sn = orient
        d, pq = _num(row.get("swell_dp")), _num(row.get("period_quality"))
        if d is None or face is None or sn is None or pq is None:
            return {"stars": None, "why": "mop: direction, face, shore normal or period missing"}
        dg = directional_gain(d, arcs, optimal if optimal is not None else sn, sn)
        return {"stars": composite_stars(face * dg, wm, tm, cm, pq),
                "gain": dg, "dir": d, "path": "cdip_mop"}

    comb = combine_ww3_partitions(parts, arcs, optimal, orient) if parts else None
    if comb is not None:
        fft = face_ft(comb["hs"], comb["tp"], "ww3")
        return {"stars": composite_stars(fft, wm, tm, cm, period_quality(comb["tp"])),
                "gain": comb["dir_gain"], "dir": comb["dp"], "path": "ww3"}
    if kind in WW3_KINDS:
        return {"stars": None, "why": "ww3: no partition keeps any gain, so the hour moves to "
                                      "rate_spot's NWPS path, whose inputs are not stored"}
    d = row.get("swell_dp") if row.get("swell_dp") is not None else row.get("dp")
    pq = _num(row.get("period_quality"))
    if face is None or pq is None:
        return {"stars": None, "why": f"{kind}: face or period missing"}
    dg = directional_gain(float(d), arcs, optimal, orient) if d is not None else 0.0
    return {"stars": composite_stars(face * dg, wm, tm, cm, pq),
            "gain": dg, "dir": _num(d), "path": kind or "none"}


def compare(rows, before_spots, after_spots, names, row_after=None):
    """Replay every row with BASELINE and with ARCS. *names* maps spot_id -> (name, state).

    *row_after*, when given, maps a row to the row the AFTER replay reads (the tide preview
    swaps its tide_mult); each change then also records the tide it was rated on.

    Returns the report dict (see main for its keys)."""
    replay = Counter()
    unreplayable = Counter()
    changed = []
    by_group = defaultdict(Counter)
    by_state = defaultdict(Counter)
    for row in rows:
        name, state = names.get(row["spot_id"], (None, None))
        b, a = before_spots.get(name), after_spots.get(name)
        kind = row.get("swell_source") or "none"
        grp = group_of(kind)
        if b is None or a is None or row.get("stars") is None:
            unreplayable[f"{grp}: spot not in both spots files or no published stars"] += 1
            continue
        rb = rescore(row, b)
        if rb["stars"] is None:
            unreplayable[f"{grp}: {rb['why']}"] += 1
            continue
        ok = rb["stars"] == float(row["stars"])
        replay[(grp, ok)] += 1
        if not ok:
            continue
        row_a = row_after(row) if row_after else row
        ra = rescore(row_a, a)
        if ra["stars"] is None:
            unreplayable[f"{grp} (after): {ra['why']}"] += 1
            continue
        delta = round(ra["stars"] - rb["stars"], 1)
        by_group[grp][delta] += 1
        by_state[state][delta] += 1
        if delta:
            change = {"spot": name, "state": state, "valid_time": row["valid_time"],
                      "group": grp, "dir_before": rb["dir"], "dir_after": ra["dir"],
                      "gain_before": round(rb["gain"], 3), "gain_after": round(ra["gain"], 3),
                      "stars_before": rb["stars"], "stars_after": ra["stars"],
                      "delta": delta}
            if row_after:
                change.update(tide_norm=row.get("tide_norm"),
                              tide_mult_before=_mults(row)[1], tide_mult_after=_mults(row_a)[1])
            changed.append(change)
    per_spot = defaultdict(list)
    for c in changed:
        per_spot[c["spot"]].append(c)
    top = sorted(per_spot.items(),
                 key=lambda kv: (-len(kv[1]), -max(abs(c["delta"]) for c in kv[1]), kv[0]))[:20]
    return {
        "rows": len(rows),
        "replay": {f"{g} {'reproduced' if ok else 'NOT reproduced'}": n
                   for (g, ok), n in sorted(replay.items())},
        "unreplayable": dict(sorted(unreplayable.items())),
        "compared": sum(sum(c.values()) for c in by_group.values()),
        "changed_spot_hours": len(changed),
        "changed_spots": len(per_spot),
        "delta_by_group": {g: dict(sorted(c.items())) for g, c in sorted(by_group.items())},
        "delta_by_state": {s: dict(sorted(c.items())) for s, c in sorted(by_state.items(), key=lambda kv: str(kv[0]))},
        "top20": [{"spot": s, "changed_hours": len(cs),
                   # the largest change; the earliest such hour, since rows come in time order
                   "example": max(cs, key=lambda c: abs(c["delta"]))}
                  for s, cs in top],
    }


def tide_mode(spec):
    """'keep', 'neutral' or 'damp:K' -> (label, f) where f maps a stored tide_mult to the new one.
    ('rated' is not a map of the stored value; main builds it with rated_row_after.)"""
    if spec == "keep":
        return spec, lambda tm: tm
    if spec == "neutral":
        return spec, lambda tm: 1.0
    if spec.startswith("damp:"):
        k = float(spec.split(":", 1)[1])
        if not 0.0 < k < 1.0:
            raise ValueError(f"{spec}: K must be strictly between 0 and 1")
        return spec, lambda tm: 1.0 - k * (1.0 - tm)
    raise ValueError(f"unknown tide mode {spec!r}: keep, neutral, damp:K or rated")


def unverified(tide):
    """Is a spot's tide preference unverified — its source not one the rating trusts
    (config.TIDE_RATED_SOURCES)? *tide* is the spot's {"tide_preference",
    "tide_preference_source"} from the live table."""
    return (tide or {}).get("tide_preference_source") not in TIDE_RATED_SOURCES


def tide_row_after(tides, f):
    """The AFTER row for the tide preview: *f* applied to the stored tide_mult on a spot whose
    preference is unverified; a verified spot's row, and a row with no tide_mult, unchanged."""
    def after(row):
        tm = row.get("tide_mult")
        if tm is None or not unverified(tides.get(row["spot_id"])):
            return row
        return dict(row, tide_mult=f(float(tm)))
    return after


def rated_row_after(roster, names):
    """The AFTER row for the 'rated' mode: tide_mult recomputed by the rater's own rule from the
    row's tide_norm and the spot as *roster* (name -> spot) holds it. A row whose spot is not in
    the roster is unchanged."""
    def after(row):
        name = names.get(row["spot_id"], (None, None))[0]
        spot = roster.get(name)
        if spot is None:
            return row
        return dict(row, tide_mult=tide_multiplier(_num(row.get("tide_norm")),
                                                   rated_tide_preference(spot)))
    return after


def tide_census(rows, tides):
    """What the live table holds for the tide, and what the window's stored tide_mult did."""
    spots = Counter((t.get("tide_preference"), t.get("tide_preference_source"))
                    for t in tides.values())
    by_pref, repro, below = defaultdict(Counter), Counter(), Counter()
    for row in rows:
        pref = (tides.get(row["spot_id"]) or {}).get("tide_preference")
        tm = row.get("tide_mult")
        by_pref[str(pref)]["None" if tm is None else f"{float(tm):g}"] += 1
        if tm is None:
            continue
        if float(tm) < 1.0:
            below[str(pref)] += 1
        expected = round(tide_multiplier(_num(row.get("tide_norm")), pref), 3)
        repro["reproduced" if expected == round(float(tm), 3) else "NOT reproduced"] += 1
    return {
        "spots": {f"{p} / {s}": n for (p, s), n in sorted(spots.items(), key=str)},
        "rows": len(rows),
        "tide_mult_by_preference": {p: dict(sorted(c.items())) for p, c in sorted(by_pref.items())},
        "hours_below_1_by_preference": dict(sorted(below.items())),
        "tide_mult_from_tide_norm_and_preference": dict(sorted(repro.items())),
    }


def _by_name(path):
    return {s["name"]: s for s in json.load(open(path)) if s.get("name")}


def _fetch(client, since):
    """Every spot, then the published window ONE SPOT AT A TIME.

    One query per spot, filtered on spot_id, source and valid_time — the forecasts table's
    UNIQUE(spot_id, valid_time, source) key — so each read is a short index range. The first
    version paged the whole window with ORDER BY spot_id, valid_time and a growing OFFSET;
    Supabase cancelled it with a statement timeout (57014) partway through, in sw1-raycast
    run 11."""
    names, frm = {}, 0
    while True:
        rows = (client.table("spots").select("id, name, state").order("id")
                .range(frm, frm + 999).execute().data or [])
        names.update({r["id"]: (r["name"], r.get("state")) for r in rows})
        if len(rows) < 1000:
            break
        frm += 1000
    out = []
    for sid in sorted(names):
        frm = 0
        while True:
            rows = (client.table("forecasts").select(FORECAST_COLS)
                    .eq("spot_id", sid).eq("source", "nwps").gte("valid_time", since)
                    .order("valid_time")
                    .range(frm, frm + 999).execute().data or [])
            out.extend(rows)
            if len(rows) < 1000:
                break
            frm += 1000
    return names, out


def _fetch_tides(client):
    """{spot_id: {tide_preference, tide_preference_source}} from the live spots table, SELECT
    only. Before migration 018 there is no source column: then every source reads None (so
    every preference is unverified) and the census says so."""
    out, frm, cols = {}, 0, "id, tide_preference, tide_preference_source"
    while True:
        try:
            rows = (client.table("spots").select(cols).order("id")
                    .range(frm, frm + 999).execute().data or [])
        except Exception as e:  # noqa: BLE001 — a missing column is reported, not fatal
            if "tide_preference_source" not in cols:
                raise
            print(f"spots.tide_preference_source could not be read ({e}); reading the "
                  "preference alone, every source as None")
            out, frm, cols = {}, 0, "id, tide_preference"
            continue
        out.update({r["id"]: {"tide_preference": r.get("tide_preference"),
                              "tide_preference_source": r.get("tide_preference_source")}
                    for r in rows})
        if len(rows) < 1000:
            return out
        frm += 1000


def _print_census(c):
    print("live spots, tide_preference / tide_preference_source:")
    for k, v in c["spots"].items():
        print(f"  {k}: {v}")
    print(f"published window: {c['rows']} rows")
    print("stored tide_mult by the spot's preference (value: hours):")
    for p, d in c["tide_mult_by_preference"].items():
        print(f"  {p}: {d}")
    print(f"hours with tide_mult < 1 by preference: {c['hours_below_1_by_preference']}")
    print("stored tide_mult recomputed from tide_norm and the live preference: "
          f"{c['tide_mult_from_tide_norm_and_preference']}")


def _print(rep, since):
    print(f"published window from {since}: {rep['rows']} rows (source='nwps')")
    print("replay with BASELINE against the published stars:")
    for k, v in rep["replay"].items():
        print(f"  {k}: {v}")
    for k, v in rep["unreplayable"].items():
        print(f"  not replayable — {k}: {v}")
    print(f"compared (reproduced rows): {rep['compared']}")
    print(f"changed spot-hours: {rep['changed_spot_hours']}   changed spots: {rep['changed_spots']}")
    print("star change by source (delta: hours):")
    for g, c in rep["delta_by_group"].items():
        print(f"  {g}: {c}")
    print("star change by state (delta: hours):")
    for s, c in rep["delta_by_state"].items():
        print(f"  {s}: {c}")
    print("20 most-changed spots (changed hours; example hour: time, dir before->after, "
          "gain before->after, stars before->after):")
    for t in rep["top20"]:
        e = t["example"]
        tide = (f" tide_norm {e['tide_norm']} tide_mult {e['tide_mult_before']}->"
                f"{e['tide_mult_after']:.3f}" if "tide_mult_before" in e else "")
        print(f"  {t['spot']} ({e['state']}): {t['changed_hours']} h; {e['valid_time']} "
              f"[{e['group']}] dir {e['dir_before']}->{e['dir_after']} "
              f"gain {e['gain_before']}->{e['gain_after']}{tide} "
              f"stars {e['stars_before']}->{e['stars_after']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--arcs", default=None,
                   help="spots file whose swell windows to preview (default: --baseline)")
    p.add_argument("--baseline", default="pipeline/spots_enriched.json",
                   help="spots file the published window was rated with (default: the roster)")
    p.add_argument("--tide", default=None,
                   help="comma-separated tide modes to preview: keep, neutral, damp:K, rated")
    p.add_argument("--tide-roster", default=None,
                   help="spots file whose tide preference and source the 'rated' mode reads "
                        "(default: --baseline)")
    p.add_argument("--since", default=None, help="ISO start of the window (default: now)")
    p.add_argument("--out", default=None, help="write the report as JSON here")
    args = p.parse_args(argv)
    if args.arcs is None and args.tide is None:
        p.error("give --arcs (a swell-window preview) or --tide (a tide preview), or both")
    specs = [m.strip() for m in args.tide.split(",")] if args.tide else []
    modes = [(m, None) if m == "rated" else tide_mode(m) for m in specs]
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    since = args.since or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    names, rows = _fetch(client, since)
    before = _by_name(args.baseline)
    after = _by_name(args.arcs) if args.arcs else before
    out = {"since": since}
    if not modes:
        rep = compare(rows, before, after, names)
        _print(rep, since)
        out.update(rep)
    else:
        tides = _fetch_tides(client)
        out["census"] = tide_census(rows, tides)
        _print_census(out["census"])
        out["tide"] = {}
        roster = _by_name(args.tide_roster) if args.tide_roster else before
        for label, f in modes:
            if label == "rated":
                print(f"\n=== tide: rated — the rater's rule on the sources in "
                      f"{args.tide_roster or args.baseline} "
                      f"({sum(rated_tide_preference(s) is not None for s in roster.values())} "
                      f"spots whose preference the rating applies) ===")
                row_after = rated_row_after(roster, names)
            else:
                print(f"\n=== tide: {label} on every unverified preference "
                      f"({sum(unverified(t) and t.get('tide_preference') is not None for t in tides.values())} "
                      f"spots with a preference and no source the rating trusts) ===")
                row_after = tide_row_after(tides, f)
            rep = compare(rows, before, after, names, row_after=row_after)
            _print(rep, since)
            out["tide"][label] = rep
    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
