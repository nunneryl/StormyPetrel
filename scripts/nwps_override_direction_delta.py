"""How much does keeping WW3 swell identity through the NWPS override move swell_dp / swell_tp?

READ-ONLY. Fetches nothing, writes nothing, touches no prod data. Reports, per nwps_wfo, the
spot-hours whose swell_dp moves by more than --dp-deg and whose swell_tp moves by more than
--tp-sec between the OLD override (dirpw/perpw overwrite the WW3 values) and the NEW one (WW3
identity preserved, dirpw only where WW3 resolved nothing).

DATA SOURCES, in order of preference:
  --ratings / --nwps-series   real pre-override ratings.json + a real NWPS node series dump.
                              This is the only mode that yields PRODUCTION magnitudes.
  (neither given)             a synthetic harness over REAL spot geometry — real orientations,
                              real swell_window_arcs, real WFO assignments from
                              spots_enriched.json — with a modelled wave field. It measures
                              that the change lands on the right rows and how the deltas
                              distribute for a given wind-sea/swell separation; it does NOT
                              estimate production magnitudes, because the delta per hour is
                              |ww3_dp - dirpw|, which is a property of the INPUT feeds.

The synthetic field is parameterised from the measured 2026-08-19 cases rather than from noise:
Huntington 255 deg / 7.8 s mean against a 15 s south at 191 (64 deg, 7.2 s); Blacks 285 / 6.0
against a 14 s SW at 220 (65 deg, 8.0 s); Pipeline / Sunset / Waimea 95 / 6.8 at spots facing
300-315. --sep-deg and --sep-sec set the mean separation; the per-hour spread around it is
deterministic, so two runs of the same command agree exactly.

    python -m scripts.nwps_override_direction_delta
    python -m scripts.nwps_override_direction_delta --ratings forecast_data/ratings.json \
        --nwps-series /tmp/nwps_series.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.forecast import nwps_nearshore as nn   # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENRICHED = os.path.join(REPO, "pipeline", "spots_enriched.json")


def _ang_delta(a, b):
    """Smallest absolute angular difference in degrees, 0..180."""
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def _synthetic(spots, hours, sep_deg, sep_sec):
    """(ratings, series_by_spot) over REAL geometry with a modelled wave field.

    Deterministic: every value derives from the spot index and hour, no RNG, so reruns agree.
    The WW3 swell sits near the spot's own optimal direction (a real swell the window was cut
    for); dirpw sits sep_deg away at a short period, which is what a wind-sea-dragged
    whole-spectrum mean looks like. Both wander hour to hour so the delta has a spread rather
    than a single value.
    """
    ratings, series = {}, {}
    for i, s in enumerate(spots):
        name = s["name"]
        opt = s.get("optimal_swell_dir")
        if opt is None:
            opt = s.get("orientation_deg")
        if opt is None:
            continue
        base_h = 1767225600 // 3600
        ent, ser = [], {}
        for h in range(hours):
            k = base_h + h
            # MIXEDNESS in [0, 1]: 0 = a clean single-swell sea, where the whole-spectrum
            # mean IS the swell and dirpw ~ ww3_dp; 1 = a fully mixed sea, where the mean is
            # dragged all the way onto the wind sea. Real coasts sit across this range, and a
            # single fixed separation would make every hour cross any threshold, which says
            # nothing. Deterministic in (spot, hour).
            mix = 0.5 + 0.5 * math.sin(i * 0.37 + h * 0.11 + math.cos(i * 0.13))
            ww3_dp = float(opt) + 12.0 * math.sin(i * 0.7 + h * 0.31)
            ww3_tp = 12.0 + 5.0 * (0.5 + 0.5 * math.sin(i * 1.3 + h * 0.17))
            dpw = (ww3_dp + sep_deg * mix) % 360.0
            perpw = max(3.0, ww3_tp - sep_sec * mix)
            # ~2.1% of hours carry NO WW3 identity, matching the measured ww3_used 97.9%,
            # so the dirpw fallback and its per-WFO counting are exercised at roster scale.
            has_ww3 = ((i * 57 + h) % 48) != 0
            e = {"valid_time": _iso(k * 3600), "stars": 1.0,
                 "wind_mult": 1.0, "tide_mult": 1.0}
            if has_ww3:
                e.update(swell_source="ww3", swell_dp=round(ww3_dp % 360.0, 3),
                         swell_tp=round(ww3_tp, 3))
            else:
                e["swell_source"] = "nwps_total"
            ent.append(e)
            ser[k] = (1.4, round(perpw, 3), round(dpw, 3), 1.0)
        ratings[name] = ent
        series[name] = ser
    return ratings, series


def _iso(epoch):
    import datetime
    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:00:00Z")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ratings", help="real pre-override ratings.json (production magnitudes)")
    ap.add_argument("--nwps-series", help="JSON {spot: {epoch_hour: [swh, per, dirpw, shts]}}")
    ap.add_argument("--dp-deg", type=float, default=20.0)
    ap.add_argument("--tp-sec", type=float, default=3.0)
    ap.add_argument("--hours", type=int, default=57)
    ap.add_argument("--sep-deg", type=float, default=64.0,
                    help="synthetic mean |ww3_dp - dirpw| (default: the Huntington case)")
    ap.add_argument("--sep-sec", type=float, default=7.2,
                    help="synthetic mean (ww3_tp - perpw) (default: the Huntington case)")
    a = ap.parse_args(argv)

    spots = [s for s in json.load(open(ENRICHED)) if s.get("name")]
    nwps_spots = [s for s in spots if s.get("swell_window_source") == "nwps"]
    print(f"roster: {len(spots)} spots, {len(nwps_spots)} tagged swell_window_source=nwps "
          f"({100.0 * len(nwps_spots) / max(1, len(spots)):.1f}%)")

    real = bool(a.ratings and a.nwps_series)
    if real:
        ratings = json.load(open(a.ratings))
        raw = json.load(open(a.nwps_series))
        series = {k: {int(h): tuple(v) for h, v in d.items()} for k, d in raw.items()}
        print(f"MODE: REAL feeds — ratings={a.ratings} series={a.nwps_series}")
    else:
        ratings, series = _synthetic(nwps_spots, a.hours, a.sep_deg, a.sep_sec)
        print(f"MODE: SYNTHETIC wave field over REAL geometry "
              f"(sep {a.sep_deg:.0f} deg / {a.sep_sec:.1f} s, {a.hours} h). "
              "Magnitudes below describe THIS field, not production.")

    wfo_of = {s["name"]: (s.get("nwps_wfo") or "?") for s in spots}
    before = {n: [dict(e) for e in es] for n, es in ratings.items()}

    stats = nn.apply_nwps_overrides(ratings, nwps_spots, _fetch=lambda s: series.get(s["name"]))

    agg = defaultdict(lambda: {"hours": 0, "dp_moved": 0, "tp_moved": 0,
                               "ww3_dir": 0, "dirpw_dir": 0,
                               "dp_sum": 0.0, "tp_sum": 0.0})
    for name, after in ratings.items():
        w = wfo_of.get(name, "?")
        for b, af in zip(before.get(name, []), after):
            if af.get("swell_source") not in (nn.SWELL_SOURCE_NWPS_WW3, nn.SWELL_SOURCE_NWPS_DIRPW):
                continue          # hour not overridden
            c = agg[w]
            c["hours"] += 1
            c["ww3_dir"] += 1 if af["swell_source"] == nn.SWELL_SOURCE_NWPS_WW3 else 0
            c["dirpw_dir"] += 1 if af["swell_source"] == nn.SWELL_SOURCE_NWPS_DIRPW else 0
            # OLD behaviour wrote dirpw/perpw for every overridden hour; the series entry IS
            # the old value, so the delta is new-vs-dirpw directly.
            m = (series.get(name) or {}).get(_hour_of(af)) or None
            if not m:
                continue
            _swh, perpw, dpw, _shts = m
            ddp = _ang_delta(af.get("swell_dp", dpw), dpw)
            dtp = abs(float(af.get("swell_tp", perpw)) - float(perpw))
            c["dp_sum"] += ddp
            c["tp_sum"] += dtp
            c["dp_moved"] += 1 if ddp > a.dp_deg else 0
            c["tp_moved"] += 1 if dtp > a.tp_sec else 0

    tot = {k: sum(c[k] for c in agg.values())
           for k in ("hours", "dp_moved", "tp_moved", "ww3_dir", "dirpw_dir")}
    print(f"\noverride: fed={stats['fed']} fell_back={stats['fell_back']} "
          f"errored={stats['errored']}  |  direction from WW3 {stats['hours_ww3_dir']} hrs, "
          f"from dirpw {stats['hours_dirpw_dir']} hrs")
    print(f"\nspot-hours overridden: {tot['hours']}")
    if tot["hours"]:
        print(f"  swell_dp moves > {a.dp_deg:g} deg : {tot['dp_moved']} "
              f"({100.0 * tot['dp_moved'] / tot['hours']:.1f}%)")
        print(f"  swell_tp moves > {a.tp_sec:g} s   : {tot['tp_moved']} "
              f"({100.0 * tot['tp_moved'] / tot['hours']:.1f}%)")

    print(f"\n{'wfo':<5} {'hours':>7} {'ww3dir':>7} {'dirpw':>6} "
          f"{'dp>' + format(a.dp_deg, 'g'):>9} {'%':>6} {'tp>' + format(a.tp_sec, 'g'):>8} {'%':>6} "
          f"{'mean|dp|':>9} {'mean dtp':>9}")
    for w, c in sorted(agg.items(), key=lambda kv: -kv[1]["dp_moved"]):
        h = c["hours"] or 1
        print(f"{w:<5} {c['hours']:>7} {c['ww3_dir']:>7} {c['dirpw_dir']:>6} "
              f"{c['dp_moved']:>9} {100.0 * c['dp_moved'] / h:>5.1f}% "
              f"{c['tp_moved']:>8} {100.0 * c['tp_moved'] / h:>5.1f}% "
              f"{c['dp_sum'] / h:>8.1f}d {c['tp_sum'] / h:>8.1f}s")
    if not real:
        print("\nNOTE: synthetic wave field. The per-hour delta is |ww3_dp - dirpw|, a property "
              "of the INPUT feeds, so these magnitudes measure the modelled separation, not\n"
              "production. For production numbers re-run with --ratings and --nwps-series from "
              "a real cycle on a machine with NOMADS access.")
    return 0


def _hour_of(entry):
    t = nn._iso_to_epoch(entry.get("valid_time"))
    return None if t is None else int(t // 3600)


if __name__ == "__main__":
    raise SystemExit(main())
