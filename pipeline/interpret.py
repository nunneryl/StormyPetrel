"""Phase 2: per-spot, per-hour surf rating engine.

Reads spots_enriched.json plus the three forecast files (nwps.json,
buoys.json, tides.json) and emits forecast_data/ratings.json — for every
spot, every hour, a dict carrying all raw inputs plus five computed
components and a 0-5 star composite.

Rating pipeline per hour:

1. Breaking face height (face_ft)  = hs * period_factor(tp) * 3.281
2. Directional gain (dir_gain)     ∈ [0, 1]; 0 if dp is outside all
                                     swell_window_arcs, cos²(offset) inside.
3. Wind quality (wind_mult)        ∈ [0.4, 1.2]; blended toward 1.0 when
                                     wind_speed < 5 m/s.
4. Tide quality (tide_mult)        ∈ [0.6, 1.0] using tide_preference and
                                     the current normalized tide position.
5. Composite stars (0 or 1-5, 0.5 increments) from size_score × mults.

Usage:
    python -m pipeline.interpret
    python -m pipeline.interpret --sample "Mavericks"
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import (
    BUOYS_FORECAST_FILE,
    DEFAULT_ENRICHED_OUTPUT,
    NWPS_FORECAST_FILE,
    RATINGS_FILE,
    TIDES_FORECAST_FILE,
)

log = logging.getLogger("pipeline.interpret")

M_TO_FT = 3.281


# ---------------------------------------------------------------------------
# Scoring components
# ---------------------------------------------------------------------------

def period_factor(tp: float) -> float:
    """Linear from 1.3 at Tp=6s to 2.0 at Tp=16s, clamped at both ends."""
    if tp <= 6.0:
        return 1.3
    if tp >= 16.0:
        return 2.0
    return 1.3 + (tp - 6.0) / 10.0 * (2.0 - 1.3)


def face_ft(hs_m: float, tp_s: float) -> float:
    return hs_m * period_factor(tp_s) * M_TO_FT


def _in_any_arc(dp: float, arcs: list[dict]) -> bool:
    # swell_window.py may emit an arc that wraps through 0° as {min: 340,
    # max: 20} (hi < lo). The fallback splits such arcs, but be defensive.
    for arc in arcs:
        try:
            lo, hi = arc["min"], arc["max"]
        except (KeyError, TypeError):
            continue
        if lo <= hi:
            if lo <= dp <= hi:
                return True
        else:
            if dp >= lo or dp <= hi:
                return True
    return False


def directional_gain(
    dp: float,
    swell_window_arcs: list[dict] | None,
    optimal_swell_dir: float | None,
    orientation_deg: float | None,
) -> float:
    """0.0 if dp is outside every arc; cos²(offset) inside, floor 0.1.

    Falls back to *orientation_deg* when *optimal_swell_dir* is missing,
    so spots with no resolved fetch still get a meaningful gain.
    """
    if not swell_window_arcs:
        return 0.0
    if not _in_any_arc(dp, swell_window_arcs):
        return 0.0

    target = optimal_swell_dir if optimal_swell_dir is not None else orientation_deg
    if target is None:
        return 0.5  # neutral inside-window gain when we can't resolve a target

    # Smallest signed angular difference in (-180, 180].
    diff = ((dp - target + 540.0) % 360.0) - 180.0
    gain = math.cos(math.radians(diff)) ** 2
    return max(0.1, gain)


def _angle_off(a: float, b: float) -> float:
    """Smallest positive angle between two bearings, in [0, 180]."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def wind_multiplier(
    wind_dir: float,
    wind_speed_ms: float,
    offshore_wind_deg: float | None,
) -> float:
    """0.4–1.2 based on offset from the spot's offshore bearing, blended
    toward neutral 1.0 when winds are light (< 5 m/s).
    """
    if offshore_wind_deg is None:
        return 1.0

    ang = _angle_off(wind_dir, offshore_wind_deg)
    if ang < 30.0:
        base = 1.2
    elif ang < 60.0:
        base = 1.0
    elif ang < 120.0:
        base = 0.8
    elif ang < 150.0:
        base = 0.6
    else:
        base = 0.4

    if wind_speed_ms < 5.0:
        blend = max(0.0, wind_speed_ms) / 5.0
        base = 1.0 * (1.0 - blend) + base * blend
    return base


def tide_multiplier(tide_norm: float | None, preference: str | None) -> float:
    """0.6–1.0 based on normalized tide position vs preference bucket."""
    if tide_norm is None or preference in (None, "all", ""):
        return 1.0
    if preference == "low":
        if tide_norm < 0.3:
            return 1.0
        if tide_norm < 0.7:
            return 0.8
        return 0.6
    if preference == "mid":
        if 0.3 <= tide_norm <= 0.7:
            return 1.0
        return 0.7
    if preference == "high":
        if tide_norm > 0.7:
            return 1.0
        if tide_norm > 0.3:
            return 0.8
        return 0.6
    return 1.0


# Piecewise size_score anchors (face_ft → stars contribution before mults).
_SIZE_POINTS = [(0.0, 0.0), (2.0, 2.5), (4.0, 3.5), (6.0, 4.5), (8.0, 5.0)]


def size_score(effective_face_ft: float) -> float:
    if effective_face_ft <= 0.0:
        return 0.0
    if effective_face_ft >= 8.0:
        return 5.0
    for (x0, y0), (x1, y1) in zip(_SIZE_POINTS, _SIZE_POINTS[1:]):
        if x0 <= effective_face_ft <= x1:
            return y0 + (effective_face_ft - x0) / (x1 - x0) * (y1 - y0)
    return 5.0


def composite_stars(
    effective_face_ft: float,
    wind_mult: float,
    tide_mult: float,
) -> float:
    """0 if flat (< 0.5ft effective), else 1–5 in 0.5 increments."""
    if effective_face_ft < 0.5:
        return 0.0
    raw = size_score(effective_face_ft) * wind_mult * tide_mult
    stars = round(raw * 2.0) / 2.0
    return max(1.0, min(5.0, stars))


# ---------------------------------------------------------------------------
# Tide lookup
# ---------------------------------------------------------------------------

def _tz_offset_hours(lng: float) -> int:
    """Rough standard-time offset from longitude (≈15°/hour)."""
    return round(lng / 15.0)


def _parse_coops_time(t: str) -> datetime | None:
    """CO-OPS hourly/hilo `t` strings are 'YYYY-MM-DD HH:MM' in LST/LDT."""
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


def _cosine_interp(v1: float, v2: float, progress: float) -> float:
    """Cosine easing between two extremes — matches real tide-curve shape
    (zero slope at H/L, max slope in between) far better than linear interp.
    """
    return v1 + (v2 - v1) * (1.0 - math.cos(progress * math.pi)) / 2.0


def _sample_points(
    points: list[tuple[datetime, float]],
    target: datetime,
    max_gap_hours: float,
) -> float | None:
    """Return interpolated v at *target*.

    When *target* falls strictly between two points, cosine-interpolate.
    When it falls before/after the series, return the endpoint iff it is
    within *max_gap_hours*. Returns None when out of range.
    """
    from bisect import bisect_left

    if not points:
        return None
    times = [p[0] for p in points]
    i = bisect_left(times, target)

    if i == 0:
        gap_h = (times[0] - target).total_seconds() / 3600.0
        return points[0][1] if gap_h <= max_gap_hours else None
    if i == len(points):
        gap_h = (target - times[-1]).total_seconds() / 3600.0
        return points[-1][1] if gap_h <= max_gap_hours else None

    t1, v1 = points[i - 1]
    t2, v2 = points[i]
    span_s = (t2 - t1).total_seconds()
    if span_s <= 0:
        return v1
    progress = (target - t1).total_seconds() / span_s
    return _cosine_interp(v1, v2, progress)


def build_tide_series(station_block: dict) -> dict | None:
    """Return a tide series built from the station's hourly predictions when
    available, or interpolated from hilo extremes as a fallback.

    CO-OPS subordinate stations publish hilo-only predictions — ~275 spots
    in our data were losing tide multipliers because `hourly` was empty.
    hilo is ~4 samples/day; cosine interpolation between bracketing H/L
    points recovers a usable tide curve.

    Returns {"min", "max", "points": [(dt, v)], "source": "hourly"|"hilo"}
    or None when neither source has parseable predictions.
    """
    for source in ("hourly", "hilo"):
        rows = station_block.get(source) or []
        points: list[tuple[datetime, float]] = []
        for row in rows:
            t = _parse_coops_time(row.get("t"))
            if t is None:
                continue
            try:
                v = float(row.get("v"))
            except (TypeError, ValueError):
                continue
            points.append((t, v))
        if not points:
            continue
        points.sort(key=lambda p: p[0])
        vs = [v for _, v in points]
        # Min/max from hilo is actually more accurate than hourly (hilo hits
        # the true extremes; hourly samples may miss peak/trough by up to 30m).
        return {
            "min": min(vs),
            "max": max(vs),
            "points": points,
            "source": source,
        }
    return None


def lookup_tide_norm(
    series: dict,
    valid_time_utc: datetime,
    lng: float,
) -> tuple[float | None, float | None]:
    """Return (raw_tide_ft, tide_norm in 0-1) for *valid_time_utc*."""
    min_v = series["min"]
    max_v = series["max"]
    if max_v - min_v < 1e-9:
        return None, None

    local = valid_time_utc + timedelta(hours=_tz_offset_hours(lng))
    local_naive = local.replace(tzinfo=None)

    # Hilo points are ~6h apart — a hourly forecast will always be strictly
    # bracketed, so allow a 6h gap on the endpoints. Hourly stays tight.
    max_gap_h = 6.0 if series["source"] == "hilo" else 3.0
    v = _sample_points(series["points"], local_naive, max_gap_h)
    if v is None:
        return None, None
    norm = (v - min_v) / (max_v - min_v)
    return round(v, 3), round(norm, 3)


# ---------------------------------------------------------------------------
# Per-spot rating
# ---------------------------------------------------------------------------

def rate_spot(
    spot: dict,
    forecast: list[dict],
    tide_series: dict | None,
) -> list[dict]:
    orientation = spot.get("orientation_deg")
    offshore = spot.get("offshore_wind_deg")
    arcs = spot.get("swell_window_arcs") or []
    optimal = spot.get("optimal_swell_dir")
    preference = spot.get("tide_preference")
    lng = float(spot.get("lng") or 0.0)

    out: list[dict] = []
    for entry in forecast:
        vt_iso = entry.get("valid_time")
        if not vt_iso:
            continue
        try:
            vt = datetime.fromisoformat(vt_iso.replace("Z", "+00:00"))
        except ValueError:
            continue
        if vt.tzinfo is None:
            vt = vt.replace(tzinfo=timezone.utc)

        hs = entry.get("hs")
        tp = entry.get("tp")
        dp = entry.get("dp")
        wspd = entry.get("wind_speed")
        wdir = entry.get("wind_dir")

        # Face height + directional gain require wave data.
        if hs is not None and tp is not None:
            fft = face_ft(float(hs), float(tp))
        else:
            fft = None
        dg = directional_gain(float(dp), arcs, optimal, orientation) if dp is not None else 0.0

        wm = (
            wind_multiplier(float(wdir), float(wspd), offshore)
            if wdir is not None and wspd is not None
            else 1.0
        )

        tide_raw, tide_norm = (
            lookup_tide_norm(tide_series, vt, lng) if tide_series else (None, None)
        )
        tm = tide_multiplier(tide_norm, preference)

        effective = (fft or 0.0) * dg
        stars = composite_stars(effective, wm, tm) if fft is not None else 0.0

        rated = dict(entry)
        rated.update({
            "face_ft": round(fft, 2) if fft is not None else None,
            "dir_gain": round(dg, 3),
            "wind_mult": round(wm, 3),
            "tide_level_ft": tide_raw,
            "tide_norm": tide_norm,
            "tide_mult": round(tm, 3),
            "effective_size_ft": round(effective, 2),
            "stars": stars,
        })
        out.append(rated)
    return out


# ---------------------------------------------------------------------------
# Orchestration + CLI
# ---------------------------------------------------------------------------

def compute_ratings(
    spots: list[dict],
    nwps: dict[str, list[dict]],
    tides: dict[str, dict],
) -> dict[str, list[dict]]:
    """Rate every spot that has NWPS forecast data.

    Buckets every spot by how its tide source resolved so the dominant
    failure mode is visible in the log:
      - tide_hourly:     primary path — hourly predictions present
      - tide_hilo:       fallback path — hilo extremes interpolated
      - no_station:      spot has no `nearest_tide_station_id` at all
      - station_missing: station_id is set but not a key in tides.json
      - no_tide_data:    station in tides.json has neither hourly nor hilo
    """
    spot_by_name = {s.get("name"): s for s in spots if s.get("name")}
    results: dict[str, list[dict]] = {}
    rated = 0
    no_spot = 0
    no_station = 0
    station_missing = 0
    no_tide_data = 0
    tide_hourly = 0
    tide_hilo = 0
    missing_examples: list[str] = []

    for name, forecast in nwps.items():
        spot = spot_by_name.get(name)
        if spot is None:
            no_spot += 1
            continue

        station_id = spot.get("nearest_tide_station_id")
        tide_series = None
        if not station_id:
            no_station += 1
        else:
            # Defensive string-cast — some legacy writers stored ints.
            station_block = tides.get(str(station_id))
            if station_block is None:
                station_missing += 1
                if len(missing_examples) < 5:
                    missing_examples.append(f"{name}→{station_id}")
            else:
                tide_series = build_tide_series(station_block)
                if tide_series is None:
                    no_tide_data += 1
                elif tide_series.get("source") == "hilo":
                    tide_hilo += 1
                else:
                    tide_hourly += 1

        series = rate_spot(spot, forecast, tide_series)
        if series:
            results[name] = series
            rated += 1

    log.info(
        "interpret: rated %d spots (no_spot=%d, no_station=%d, "
        "station_missing=%d, no_tide_data=%d, tide_hourly=%d, tide_hilo=%d)",
        rated, no_spot, no_station, station_missing, no_tide_data,
        tide_hourly, tide_hilo,
    )
    if missing_examples:
        log.info("interpret: station_missing sample: %s", ", ".join(missing_examples))
    return results


def _star_histogram(ratings: dict[str, list[dict]]) -> Counter:
    c: Counter = Counter()
    for series in ratings.values():
        for entry in series:
            c[entry.get("stars", 0.0)] += 1
    return c


def _zero_star_breakdown(
    ratings: dict[str, list[dict]],
    spots: list[dict],
) -> tuple[int, int, int]:
    """Split 0-star spot-hours into three buckets:

    - null_orientation: spot has orientation_deg == None → dir_gain is
      always 0 by design, so every hour is 0 stars regardless of swell.
    - null_arcs: orientation resolved but no arcs (shouldn't happen
      post-fallback, but counted for completeness).
    - flat_conditions: orientation AND arcs resolved — the hour is 0 star
      because swell/size genuinely don't meet the threshold. This is the
      actionable number.
    """
    orient_by_name = {
        s.get("name"): s for s in spots if s.get("name")
    }
    null_orient = 0
    null_arcs = 0
    flat = 0
    for name, series in ratings.items():
        spot = orient_by_name.get(name) or {}
        has_orient = spot.get("orientation_deg") is not None
        has_arcs = bool(spot.get("swell_window_arcs"))
        for entry in series:
            if entry.get("stars", 0.0) != 0.0:
                continue
            if not has_orient:
                null_orient += 1
            elif not has_arcs:
                null_arcs += 1
            else:
                flat += 1
    return null_orient, null_arcs, flat


def _print_summary(
    ratings: dict[str, list[dict]],
    spots: list[dict],
    sample_name: str | None = None,
) -> None:
    print()
    print("=" * 60)
    print("Interpretation summary")
    print("=" * 60)
    print(f"  spots rated: {len(ratings)}")

    total_hours = sum(len(s) for s in ratings.values())
    print(f"  total spot-hours: {total_hours}")

    hist = _star_histogram(ratings)
    print("  star distribution (spot-hours):")
    for stars in sorted(hist.keys()):
        bar = "█" * min(50, int(hist[stars] / max(1, total_hours) * 200))
        print(f"    {stars:>3.1f} stars: {hist[stars]:>6d}  {bar}")

    zero_hours = hist.get(0.0, 0)
    if zero_hours:
        null_orient, null_arcs, flat = _zero_star_breakdown(ratings, spots)
        print(f"  0-star breakdown ({zero_hours} hours):")
        print(f"    null-orientation spots (can't score):  {null_orient}")
        if null_arcs:
            print(f"    null-arcs spots:                       {null_arcs}")
        print(f"    orientation + arcs OK, just flat:      {flat}")

    peaks = sorted(
        (
            (name, max(e.get("stars", 0.0) for e in series), series)
            for name, series in ratings.items()
            if series
        ),
        key=lambda p: p[1],
        reverse=True,
    )
    if peaks:
        print("  top 5 peak ratings in window:")
        for name, peak, _ in peaks[:5]:
            print(f"    {peak:>3.1f}★  {name}")

    # 24-hour sample block
    if sample_name and sample_name in ratings:
        chosen = (sample_name, ratings[sample_name])
    elif peaks:
        chosen = (peaks[0][0], peaks[0][2])
    else:
        chosen = None
    if chosen is not None:
        name, series = chosen
        print()
        print(f"  sample forecast — {name} (first 24 hours):")
        print(f"    {'time':<22}{'hs_m':>6}{'tp_s':>6}{'dp':>5}"
              f"{'face':>7}{'gain':>6}{'wind':>6}{'tide':>6}{'★':>5}")
        for entry in series[:24]:
            print(
                f"    {entry['valid_time']:<22}"
                f"{entry.get('hs', '—'):>6}"
                f"{entry.get('tp', '—'):>6}"
                f"{entry.get('dp', '—'):>5}"
                f"{(entry.get('face_ft') or 0):>7.1f}"
                f"{entry.get('dir_gain', 0):>6.2f}"
                f"{entry.get('wind_mult', 0):>6.2f}"
                f"{entry.get('tide_mult', 0):>6.2f}"
                f"{entry.get('stars', 0):>5.1f}"
            )
    print("=" * 60)


def _load_json(path: Path, label: str) -> dict | list:
    if not path.exists():
        log.error("interpret: %s not found at %s", label, path)
        raise SystemExit(1)
    return json.loads(path.read_text())


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    p.add_argument("--spots", type=Path, default=DEFAULT_ENRICHED_OUTPUT)
    p.add_argument("--nwps", type=Path, default=NWPS_FORECAST_FILE)
    p.add_argument("--tides", type=Path, default=TIDES_FORECAST_FILE)
    p.add_argument("--buoys", type=Path, default=BUOYS_FORECAST_FILE,
                   help="Currently informational; buoys aren't used in the v1 rating.")
    p.add_argument("--output", type=Path, default=RATINGS_FILE)
    p.add_argument("--sample", type=str, default=None,
                   help="Spot name to print as the 24h sample (defaults to top peak)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    spots = _load_json(args.spots, "spots_enriched.json")
    nwps = _load_json(args.nwps, "nwps.json")
    tides = _load_json(args.tides, "tides.json")
    log.info(
        "interpret: loaded %d spots, %d nwps series, %d tide stations",
        len(spots), len(nwps), len(tides),
    )

    ratings = compute_ratings(spots, nwps, tides)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(ratings, ensure_ascii=False))
    log.info("interpret: wrote %d spots to %s", len(ratings), args.output)

    _print_summary(ratings, spots, sample_name=args.sample)
    return 0 if ratings else 2


if __name__ == "__main__":
    sys.exit(main())
