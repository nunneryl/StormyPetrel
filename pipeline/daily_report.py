"""Daily AI surf reports — one per region per day.

For each region bucket (10 buckets covering the US + Hawaii + Puerto
Rico) this script:

  1. Pulls every spot in the bucket from Supabase along with that spot's
     latest forecast and the forecast row 24h from now.
  2. Picks the top 10 spots by current stars; computes a region-wide
     trend (building / steady / fading) from the mean face_ft delta.
  3. Asks Claude for a <100-word morning summary.
  4. Upserts {region, region_label, report_date, summary, top_spots,
     trend} into the daily_reports table (UNIQUE on region+report_date,
     so a same-day re-run overwrites the prior report).

CLI:
    python -m pipeline.daily_report [--regions a,b,...] [--dry-run] [-v]

Env:
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required.
    ANTHROPIC_API_KEY                   — required.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import anthropic

from .db_import import get_client

log = logging.getLogger("pipeline.daily_report")

# Claude Sonnet 4.6 — same model class we use for tide classification.
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 400
TOP_N = 10

# Point Conception split for socal / norcal.
POINT_CONCEPTION_LAT = 34.45

# Trend thresholds in feet of face_ft change between now and +24h.
TREND_BUILDING_FT = 0.5
TREND_FADING_FT = -0.5


@dataclass(frozen=True)
class RegionDef:
    key: str
    label: str
    matcher: Callable[[dict], bool]


def _is_state(spot: dict, *names: str) -> bool:
    return (spot.get("state") or "") in names


REGIONS: list[RegionDef] = [
    RegionDef(
        "northeast", "Northeast",
        lambda s: _is_state(s, "Maine", "New Hampshire", "Massachusetts", "Rhode Island"),
    ),
    RegionDef(
        "mid_atlantic", "Mid-Atlantic",
        lambda s: _is_state(s, "New York", "New Jersey", "Delaware", "Maryland", "Virginia"),
    ),
    RegionDef(
        "southeast", "Southeast",
        lambda s: _is_state(s, "North Carolina", "South Carolina"),
    ),
    RegionDef("florida", "Florida", lambda s: _is_state(s, "Florida")),
    RegionDef("gulf", "Gulf", lambda s: _is_state(s, "Texas")),
    RegionDef(
        "socal", "Southern California",
        lambda s: _is_state(s, "California") and (s.get("lat") or 0) < POINT_CONCEPTION_LAT,
    ),
    RegionDef(
        "norcal", "Northern California",
        lambda s: (
            (_is_state(s, "California") and (s.get("lat") or 0) >= POINT_CONCEPTION_LAT)
            or _is_state(s, "Oregon")
        ),
    ),
    RegionDef("pacific_northwest", "Pacific Northwest", lambda s: _is_state(s, "Washington")),
    RegionDef("hawaii", "Hawaii", lambda s: _is_state(s, "Hawaii")),
    RegionDef("puerto_rico", "Puerto Rico", lambda s: _is_state(s, "Puerto Rico")),
]

SYSTEM_PROMPT = (
    "You are a surf forecaster writing a concise morning report for "
    "{region}. Write 2-3 sentences about current conditions, name the "
    "best spots with their ratings, note the wind and when it goes "
    "onshore, and mention what's coming in the next 2-3 days. Sound "
    "like a knowledgeable surfer, not a weather robot. Keep it under "
    "100 words. No hashtags, no emojis."
)

_CARDINAL_16 = [
    "N", "NNE", "NE", "ENE",
    "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW",
    "W", "WNW", "NW", "NNW",
]


def _cardinal(deg: float | None) -> str:
    if deg is None or (isinstance(deg, float) and math.isnan(deg)):
        return "—"
    norm = (deg % 360 + 360) % 360
    return _CARDINAL_16[round(norm / 22.5) % 16]


def _classify_wind(wind_dir: float | None, offshore_deg: float | None) -> str:
    if wind_dir is None or offshore_deg is None:
        return "unknown"
    diff = abs(((wind_dir - offshore_deg + 540) % 360) - 180)
    if diff < 30: return "offshore"
    if diff < 60: return "cross-offshore"
    if diff < 120: return "cross"
    if diff < 150: return "cross-onshore"
    return "onshore"


def _ms_to_mph(ms: float | None) -> float | None:
    return None if ms is None else ms * 2.23694


# ---------------------------------------------------------------------------
# Data fetch
# ---------------------------------------------------------------------------

_SPOT_COLS = (
    "id, slug, name, state, lat, lng, offshore_wind_deg"
)

_FCAST_COLS = (
    "spot_id, valid_time, hs, swell_hs, tp, dp, swell_tp, swell_dp, "
    "wind_speed, wind_dir, face_ft, stars, tide_level_ft"
)


def fetch_all_spots(client) -> list[dict]:
    """Pull every spot, paginated past Supabase's REST cap."""
    out: list[dict] = []
    page = 1000
    frm = 0
    while True:
        resp = (
            client.table("spots")
            .select(_SPOT_COLS)
            .order("id")
            .range(frm, frm + page - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        frm += page
    return out


def fetch_forecasts_window(client) -> dict[int, dict]:
    """Per spot, keep the first forecast row at/after now AND the row
    closest to +24h. Returns a dict keyed by spot_id with both rows.
    """
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    iso_end = (now.replace(microsecond=0)).isoformat()
    iso_cap = (datetime.fromtimestamp(now.timestamp() + 30 * 3600, tz=timezone.utc)).isoformat()

    by_spot: dict[int, list[dict]] = {}
    page = 1000
    frm = 0
    while True:
        resp = (
            client.table("forecasts")
            .select(_FCAST_COLS)
            .gte("valid_time", iso_end)
            .lte("valid_time", iso_cap)
            .order("valid_time")
            .range(frm, frm + page - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        for r in rows:
            by_spot.setdefault(r["spot_id"], []).append(r)
        if len(rows) < page:
            break
        frm += page

    target_24h = now.timestamp() + 24 * 3600
    out: dict[int, dict] = {}
    for spot_id, rows in by_spot.items():
        rows.sort(key=lambda r: r["valid_time"])
        latest = rows[0]
        # Pick row whose valid_time is closest to now + 24h.
        plus24 = min(
            rows,
            key=lambda r: abs(datetime.fromisoformat(r["valid_time"].replace("Z", "+00:00")).timestamp() - target_24h),
        )
        out[spot_id] = {"latest": latest, "plus24": plus24}
    return out


# ---------------------------------------------------------------------------
# Region report
# ---------------------------------------------------------------------------

def _compute_trend(spots_with_fc: list[dict]) -> str:
    """building / steady / fading from mean face_ft delta across top spots."""
    deltas: list[float] = []
    for s in spots_with_fc:
        latest = (s.get("latest") or {}).get("face_ft")
        plus24 = (s.get("plus24") or {}).get("face_ft")
        if latest is None or plus24 is None:
            continue
        deltas.append(plus24 - latest)
    if not deltas:
        return "steady"
    avg = sum(deltas) / len(deltas)
    if avg >= TREND_BUILDING_FT: return "building"
    if avg <= TREND_FADING_FT:   return "fading"
    return "steady"


def _build_user_prompt(region_label: str, trend: str, top: list[dict]) -> str:
    """Compact, structured payload for the model. We give it everything
    it needs to write the summary without burning tokens on prose."""
    lines = [
        f"REGION: {region_label}",
        f"TREND (next 24h, avg face change across top spots): {trend}",
        "",
        "TOP SPOTS (by current rating):",
    ]
    for s in top:
        latest = s.get("latest") or {}
        plus24 = s.get("plus24") or {}
        stars = latest.get("stars") or 0
        face = latest.get("face_ft")
        tp = latest.get("swell_tp") or latest.get("tp")
        dp = latest.get("swell_dp") or latest.get("dp")
        wind_dir = latest.get("wind_dir")
        wind_mph = _ms_to_mph(latest.get("wind_speed"))
        wind_q = _classify_wind(wind_dir, s.get("offshore_wind_deg"))

        face_str = f"{face:.1f}ft" if face is not None else "—"
        tp_str = f"{tp:.0f}s" if tp is not None else "—"
        wind_str = (
            f"{wind_mph:.0f} mph {_cardinal(wind_dir)} ({wind_q})"
            if wind_mph is not None else "—"
        )
        face24 = plus24.get("face_ft")
        delta = (face24 - face) if (face is not None and face24 is not None) else None
        delta_str = (
            f" Δ24h {('+' if delta >= 0 else '')}{delta:.1f}ft" if delta is not None else ""
        )
        lines.append(
            f"- {s['name']} ({s.get('state') or '?'}) "
            f"★{stars:.1f} · {face_str} @ {tp_str} from {_cardinal(dp)} · "
            f"wind {wind_str}{delta_str}"
        )
    lines.append("")
    lines.append(
        "Write the report. <100 words. 2-3 sentences. Mention the best spots "
        "with their ratings (use the ★ value rounded to 0.5), call out wind "
        "quality, and forecast the next 2-3 days using the trend signal."
    )
    return "\n".join(lines)


def generate_summary(client_anthropic, region_label: str, trend: str, top: list[dict]) -> str:
    """Call Claude for the region summary. Returns the plain text body."""
    prompt = _build_user_prompt(region_label, trend, top)
    message = client_anthropic.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT.format(region=region_label),
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in message.content if b.type == "text"), "").strip()
    if not text:
        raise RuntimeError(f"empty response from {MODEL} for region {region_label}")
    return text


def build_region_report(
    region: RegionDef,
    spots: list[dict],
    forecasts: dict[int, dict],
    client_anthropic,
) -> dict | None:
    """Compose one region's report row. Returns None if the region has
    no spots with current forecasts (e.g. a brand-new region with no
    data plumbed yet)."""
    in_region = [s for s in spots if region.matcher(s)]
    if not in_region:
        log.warning("%s: no spots match", region.key)
        return None

    enriched: list[dict] = []
    for s in in_region:
        fc = forecasts.get(s["id"])
        if not fc or not fc.get("latest"):
            continue
        enriched.append({**s, "latest": fc["latest"], "plus24": fc["plus24"]})

    if not enriched:
        log.warning("%s: %d spots but no current forecasts", region.key, len(in_region))
        return None

    enriched.sort(key=lambda s: (s["latest"].get("stars") or 0), reverse=True)
    top = enriched[:TOP_N]
    trend = _compute_trend(top)

    log.info(
        "%s: %d spots, %d with forecasts, top star=%.1f, trend=%s",
        region.key, len(in_region), len(enriched),
        top[0]["latest"].get("stars") or 0, trend,
    )

    summary = generate_summary(client_anthropic, region.label, trend, top)

    top_spots_payload = [
        {
            "name": s["name"],
            "slug": s["slug"],
            "state": s.get("state"),
            "stars": s["latest"].get("stars"),
            "face_ft": s["latest"].get("face_ft"),
        }
        for s in top
    ]

    return {
        "region": region.key,
        "region_label": region.label,
        "report_date": datetime.now(timezone.utc).date().isoformat(),
        "summary": summary,
        "top_spots": top_spots_payload,
        "trend": trend,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def upsert_report(client_supabase, row: dict) -> None:
    """Upsert on (region, report_date). Same-day re-runs overwrite."""
    client_supabase.table("daily_reports").upsert(
        row, on_conflict="region,report_date"
    ).execute()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate daily AI surf reports per region.")
    p.add_argument(
        "--regions",
        type=str,
        default=None,
        help="Comma-separated region keys to run (default: all).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Build reports + call Claude but skip the Supabase upsert.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is required")
        return 1

    selected: list[RegionDef]
    if args.regions:
        wanted = {r.strip() for r in args.regions.split(",") if r.strip()}
        unknown = wanted - {r.key for r in REGIONS}
        if unknown:
            log.error("unknown region keys: %s", ", ".join(sorted(unknown)))
            return 2
        selected = [r for r in REGIONS if r.key in wanted]
    else:
        selected = REGIONS

    client_supabase = get_client()
    client_anthropic = anthropic.Anthropic()

    log.info("fetching spots + forecasts")
    spots = fetch_all_spots(client_supabase)
    forecasts = fetch_forecasts_window(client_supabase)
    log.info("loaded %d spots, %d with current forecasts", len(spots), len(forecasts))

    written = 0
    skipped = 0
    failed = 0
    for region in selected:
        try:
            row = build_region_report(region, spots, forecasts, client_anthropic)
        except Exception:  # noqa: BLE001
            log.exception("%s: report generation failed", region.key)
            failed += 1
            continue
        if row is None:
            skipped += 1
            continue
        if args.dry_run:
            log.info("DRY RUN — %s report:\n%s\n%s", region.key, row["summary"],
                     json.dumps(row["top_spots"], indent=2))
        else:
            upsert_report(client_supabase, row)
        written += 1

    log.info("done. wrote=%d, skipped=%d, failed=%d", written, skipped, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
