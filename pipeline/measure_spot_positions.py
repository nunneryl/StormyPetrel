"""Measure coordinate quality of every surfed spot and try two automated
recoveries for the broken ones. Pure measurement — writes NOTHING back
to spots_enriched.json or any database. Three JSON artifacts plus a
summary + log file land in the output directory.

Set definition
--------------
A spot is "broken" iff EITHER:
  * its distance to the GSHHG L1 coastline exceeds 2 km, OR
  * its reverse-geocoded US state (reverse_geocoder cities1000) does not
    match the state encoded in the seed's nearest_town (llm_spots.json).

Spots with no seed entry are still distance-checked; spots with no
distance (outside the GSHHG STRtree query envelope, e.g. open ocean
buoys with no nearby polygon) get NaN and skip the distance gate.

Recovery
--------
For every broken spot, two candidate sources are attempted:

  1. Nominatim forward-geocode with FULL context — the
     ``"<name>, <nearest_town>, <state>"`` query, not the bare name.
     One request per spot, throttled to 1 req/sec, with a contact
     User-Agent.
  2. OSM Overpass — first the seed town's centroid is Nominatim-geocoded
     (one extra request per unique town, cached), then
     ``node[sport=surfing](around:10000, town_lat, town_lon)`` runs and
     the results are name-matched with rapidfuzz.

Both candidates are gated identically before acceptance:
  * reverse_geocoder admin1 matches the seed state
  * GSHHG coastline distance <= 1 km

Output buckets
--------------
  out/geocode_fixed.json    spots where Nominatim's full-context hit
                            passed the gate (preferred over OSM since
                            it carries the exact seed town context)
  out/osm_fixed.json        spots where OSM Overpass surf-tagged node
                            passed the gate
  out/manual_needed.json    spots where neither source produced a
                            gate-passing candidate
  out/measurement_summary.json  top-line counts
  out/measurement_log.txt   per-request status; any 403/429 from
                            Nominatim or Overpass is the signal that
                            the runner has been rate-limited (cloud
                            IP) and the run should be retried locally.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

log = logging.getLogger("measure_spot_positions")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BROKEN_COAST_KM = 2.0
GATE_COAST_KM = 1.0
OVERPASS_RADIUS_M = 10000
OVERPASS_NAME_MATCH_THRESHOLD = 75  # rapidfuzz partial_ratio

NOMINATIM_BASE = "https://nominatim.openstreetmap.org/search"
NOMINATIM_DELAY_S = 1.0  # ~1 req/sec, OSM policy

OVERPASS_BASE = "https://overpass-api.de/api/interpreter"
OVERPASS_DELAY_S = 2.0  # be polite, ~30 req/min

# Contact line in User-Agent per Nominatim usage policy.
DEFAULT_CONTACT_EMAIL = "l.nunnery11@gmail.com"


# Two-letter postal -> full state name. Used to translate the seed's
# "<town>, CA" into the "California" form reverse_geocoder returns as
# admin1, and to compare candidate states symmetrically.
US_STATE_ABBR_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico",
    "VI": "U.S. Virgin Islands", "GU": "Guam", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}


# ---------------------------------------------------------------------------
# Seed flattening
# ---------------------------------------------------------------------------

def flatten_seed(llm_spots: dict) -> dict[str, dict[str, str]]:
    """Return {name -> {nearest_town, state_name}} flattened over regions.

    State_name is the full-name form; the seed encodes it as the trailing
    two-letter or "CA"-style code in nearest_town. Entries we can't parse
    are dropped from this map so the caller falls back to admin1 inference.
    """
    out: dict[str, dict[str, str]] = {}
    for region_block in (llm_spots.get("regions") or {}).values():
        for sp in region_block.get("spots") or []:
            name = sp.get("name")
            town = sp.get("nearest_town")
            if not name or not town:
                continue
            state_name: str | None = None
            if "," in town:
                tail = town.rsplit(",", 1)[1].strip()
                if len(tail) == 2 and tail.upper() in US_STATE_ABBR_TO_NAME:
                    state_name = US_STATE_ABBR_TO_NAME[tail.upper()]
                else:
                    # Some seeds may already use full names ("Cardiff-by-the-Sea,
                    # California") — keep as-is when it looks like a state name.
                    if tail in US_STATE_ABBR_TO_NAME.values():
                        state_name = tail
            if state_name is None:
                continue
            out[name] = {"nearest_town": town, "state": state_name}
    return out


# ---------------------------------------------------------------------------
# Geospatial helpers — coastline distance + reverse-geocode
# ---------------------------------------------------------------------------

def make_coast_km(land) -> "tuple[Any, Any]":
    """Build (coast_km_fn, reverse_admin1_fn) using the repo's LandIndex
    plus reverse_geocoder. Both reuse the same Geod ellipsoid.
    """
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    import pyproj

    geod = pyproj.Geod(ellps="WGS84")
    coastlines = land.coastlines
    tree = land.coastline_tree

    def coast_km(lat: float, lng: float) -> float | None:
        pt = Point(lng, lat)
        # STRtree.nearest returns an integer index in shapely 2.x.
        try:
            idx = int(tree.nearest(pt))
        except Exception:  # noqa: BLE001
            return None
        line = coastlines[idx]
        near = nearest_points(pt, line)[1]
        _, _, dist_m = geod.inv(lng, lat, near.x, near.y)
        return dist_m / 1000.0

    import reverse_geocoder  # local data, no network

    def reverse_admin1(lat: float, lng: float) -> dict[str, str | None]:
        rows = reverse_geocoder.search([(lat, lng)], mode=1, verbose=False)
        if not rows:
            return {"cc": None, "admin1": None, "name": None}
        r = rows[0]
        return {"cc": r.get("cc"), "admin1": r.get("admin1"), "name": r.get("name")}

    return coast_km, reverse_admin1


# ---------------------------------------------------------------------------
# Network clients — Nominatim + Overpass
# ---------------------------------------------------------------------------

class RateLimitError(RuntimeError):
    """Raised on a Nominatim or Overpass 403/429 — the signal to abort and
    rerun locally rather than continue producing nulls."""


def _http_get(url: str, headers: dict[str, str], timeout: int = 30) -> tuple[int, str]:
    import requests

    resp = requests.get(url, headers=headers, timeout=timeout)
    return resp.status_code, resp.text


def _http_post(url: str, data: str, headers: dict[str, str], timeout: int = 90) -> tuple[int, str]:
    import requests

    resp = requests.post(url, data=data, headers=headers, timeout=timeout)
    return resp.status_code, resp.text


class NominatimClient:
    def __init__(self, contact_email: str, logfp):
        self.contact = contact_email
        self.logfp = logfp
        self.ua = f"stormypetrel-position-measurement/1.0 ({contact_email})"
        self._last_call = 0.0
        self._town_cache: dict[tuple[str, str], dict | None] = {}

    def _throttle(self):
        wait = NOMINATIM_DELAY_S - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _request(self, params: dict[str, str]) -> list[dict] | None:
        self._throttle()
        url = NOMINATIM_BASE + "?" + urllib.parse.urlencode(params)
        try:
            status, body = _http_get(url, {"User-Agent": self.ua, "Accept-Language": "en"})
        except Exception as e:  # noqa: BLE001
            self.logfp.write(f"NOMINATIM exception: {e}  url={url}\n")
            return None
        if status in (403, 429):
            self.logfp.write(f"NOMINATIM {status} (rate-limited) url={url}\n")
            self.logfp.flush()
            raise RateLimitError(f"Nominatim {status}")
        if status >= 400:
            self.logfp.write(f"NOMINATIM {status} url={url}\n")
            return None
        try:
            return json.loads(body)
        except ValueError:
            self.logfp.write(f"NOMINATIM bad json url={url}\n")
            return None

    def geocode_full_context(self, name: str, town: str, state: str) -> dict | None:
        """Forward-geocode '<name>, <town>, <state>' restricted to the US.
        Returns {lat, lng, raw} or None.
        """
        q = f"{name}, {town}, {state}"
        rows = self._request(
            {
                "q": q,
                "format": "jsonv2",
                "limit": "3",
                "countrycodes": "us,pr,vi,gu,as,mp",
                "addressdetails": "1",
            }
        ) or []
        for r in rows:
            try:
                return {"lat": float(r["lat"]), "lng": float(r["lon"]), "raw": r}
            except (KeyError, TypeError, ValueError):
                continue
        return None

    def geocode_town(self, town: str, state: str) -> dict | None:
        """Cached town centroid lookup for the Overpass radius query."""
        key = (town, state)
        if key in self._town_cache:
            return self._town_cache[key]
        rows = self._request(
            {
                "q": f"{town}, {state}",
                "format": "jsonv2",
                "limit": "1",
                "countrycodes": "us,pr,vi,gu,as,mp",
            }
        ) or []
        result = None
        for r in rows:
            try:
                result = {"lat": float(r["lat"]), "lng": float(r["lon"])}
                break
            except (KeyError, TypeError, ValueError):
                continue
        self._town_cache[key] = result
        return result


class OverpassClient:
    def __init__(self, contact_email: str, logfp):
        self.logfp = logfp
        self.ua = f"stormypetrel-position-measurement/1.0 ({contact_email})"
        self._last_call = 0.0

    def _throttle(self):
        wait = OVERPASS_DELAY_S - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def query_surf_nodes_near(self, lat: float, lng: float) -> list[dict]:
        """node[sport=surfing](around:10km, lat, lng) → name-bearing nodes."""
        self._throttle()
        q = (
            "[out:json][timeout:25];\n"
            f'node["sport"="surfing"](around:{OVERPASS_RADIUS_M},{lat},{lng});\n'
            "out tags center;"
        )
        try:
            status, body = _http_post(
                OVERPASS_BASE,
                data="data=" + urllib.parse.quote_plus(q),
                headers={
                    "User-Agent": self.ua,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except Exception as e:  # noqa: BLE001
            self.logfp.write(f"OVERPASS exception: {e}\n")
            return []
        if status in (403, 429):
            self.logfp.write(f"OVERPASS {status} (rate-limited) lat={lat} lng={lng}\n")
            self.logfp.flush()
            raise RateLimitError(f"Overpass {status}")
        if status >= 400:
            self.logfp.write(f"OVERPASS {status} body={body[:200]}\n")
            return []
        try:
            return (json.loads(body).get("elements")) or []
        except ValueError:
            self.logfp.write(f"OVERPASS bad json (status={status})\n")
            return []


# ---------------------------------------------------------------------------
# Recovery + gating
# ---------------------------------------------------------------------------

def best_overpass_match(elements: list[dict], target_name: str) -> dict | None:
    """Pick the highest-rapidfuzz-scoring named element above threshold."""
    from rapidfuzz import fuzz

    best = None
    best_score = -1.0
    for el in elements:
        if el.get("type") != "node":
            continue
        nm = (el.get("tags") or {}).get("name")
        if not nm:
            continue
        score = max(
            fuzz.partial_ratio(target_name.lower(), nm.lower()),
            fuzz.token_set_ratio(target_name, nm),
        )
        if score >= OVERPASS_NAME_MATCH_THRESHOLD and score > best_score:
            best_score = score
            best = {
                "lat": el.get("lat"),
                "lng": el.get("lon"),
                "osm_id": el.get("id"),
                "name": nm,
                "score": score,
            }
    return best


def gate_candidate(
    cand_lat: float,
    cand_lng: float,
    expected_state: str,
    coast_km,
    reverse_admin1,
) -> tuple[bool, dict]:
    """Apply the validation gate. Returns (passed, diagnostics)."""
    rg = reverse_admin1(cand_lat, cand_lng)
    ckm = coast_km(cand_lat, cand_lng)
    diag = {
        "candidate_lat": cand_lat,
        "candidate_lng": cand_lng,
        "rg_admin1": rg.get("admin1"),
        "rg_cc": rg.get("cc"),
        "coast_km": round(ckm, 3) if ckm is not None else None,
    }
    state_ok = rg.get("admin1") == expected_state
    coast_ok = ckm is not None and ckm <= GATE_COAST_KM
    diag["state_match"] = state_ok
    diag["coast_match"] = coast_ok
    diag["passed"] = state_ok and coast_ok
    return diag["passed"], diag


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spots-file",
        type=Path,
        default=Path("pipeline/spots_enriched.json"),
    )
    parser.add_argument(
        "--seed-file",
        type=Path,
        default=Path("pipeline/data/llm_spots.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("pipeline/measurement_output"),
    )
    parser.add_argument(
        "--contact-email",
        default=os.environ.get("CONTACT_EMAIL") or DEFAULT_CONTACT_EMAIL,
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N broken spots through the recovery stage "
        "(0 = all). Useful for smoke-testing.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / "measurement_log.txt"
    logfp = open(log_path, "w")  # noqa: SIM115
    logfp.write(f"# measurement run at {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")

    log.info("loading spots + seed")
    spots = json.loads(args.spots_file.read_text())
    seed = flatten_seed(json.loads(args.seed_file.read_text()))
    log.info("spots=%d seed_entries=%d", len(spots), len(seed))

    log.info("loading GSHHG L1 land index (this is the slow step)")
    # Lazy import so missing fiona/shapely surfaces a clean error, not a
    # cascade from the unrelated argparse path.
    from pipeline.enrichment.geodata import load_land_index

    land = load_land_index()
    if land is None:
        log.error(
            "GSHHG L1 shapefile missing — run bash pipeline/download_geodata.sh "
            "before this script. Aborting."
        )
        return 2
    coast_km, reverse_admin1 = make_coast_km(land)

    # --- pass 1: classify everyone --------------------------------------
    log.info("pass 1: scoring all %d spots", len(spots))
    classified: list[dict] = []
    for sp in spots:
        name = sp.get("name")
        lat, lng = sp.get("lat"), sp.get("lng")
        if name is None or lat is None or lng is None:
            continue
        seed_entry = seed.get(name)
        seed_state = (seed_entry or {}).get("state")
        rg = reverse_admin1(lat, lng)
        rg_admin1 = rg.get("admin1")
        ckm = coast_km(lat, lng)

        reasons: list[str] = []
        if ckm is not None and ckm > BROKEN_COAST_KM:
            reasons.append(f"coast_km={round(ckm,2)}>2")
        if seed_state and rg_admin1 and seed_state != rg_admin1:
            reasons.append(f"state mismatch (rg={rg_admin1!r} vs seed={seed_state!r})")

        classified.append({
            "name": name,
            "current": {"lat": lat, "lng": lng, "coast_km": round(ckm, 3) if ckm is not None else None,
                        "rg_admin1": rg_admin1, "rg_cc": rg.get("cc")},
            "seed": seed_entry or None,
            "broken": bool(reasons),
            "broken_reasons": reasons,
        })

    broken = [c for c in classified if c["broken"]]
    log.info("broken set: %d / %d spots", len(broken), len(classified))
    logfp.write(f"broken_count={len(broken)} total={len(classified)}\n")

    if args.limit and args.limit < len(broken):
        log.info("--limit %d in effect", args.limit)
        broken_iter = broken[: args.limit]
    else:
        broken_iter = broken

    # --- pass 2: recovery ------------------------------------------------
    nom = NominatimClient(args.contact_email, logfp)
    osm = OverpassClient(args.contact_email, logfp)

    geocode_fixed: list[dict] = []
    osm_fixed: list[dict] = []
    manual_needed: list[dict] = []

    rate_limited = False

    for i, spot in enumerate(broken_iter, 1):
        name = spot["name"]
        seed_entry = spot.get("seed")
        if not seed_entry:
            # No seed -> can't run either recovery source with context.
            manual_needed.append({
                **spot,
                "attempts": [
                    {"source": "nominatim", "result": "skipped", "reason": "no seed entry (name+town+state)"},
                    {"source": "overpass",  "result": "skipped", "reason": "no seed entry"},
                ],
            })
            continue

        town = seed_entry["nearest_town"]
        state = seed_entry["state"]
        attempts: list[dict] = []

        if i % 25 == 0 or i == 1:
            log.info("recovery %d/%d: %r", i, len(broken_iter), name)

        # Attempt 1: Nominatim full-context geocode.
        try:
            cand = nom.geocode_full_context(name, town, state)
        except RateLimitError as e:
            log.error("Nominatim rate-limited — see log; aborting recovery loop")
            logfp.write(f"ABORT_REASON: nominatim rate-limit at row {i}\n")
            rate_limited = True
            attempts.append({"source": "nominatim", "result": "rate_limited", "reason": str(e)})
            manual_needed.append({**spot, "attempts": attempts})
            # don't burn more requests
            for remaining in broken_iter[i:]:
                manual_needed.append({**remaining, "attempts": [
                    {"source": "nominatim", "result": "skipped", "reason": "aborted after rate limit"},
                    {"source": "overpass",  "result": "skipped", "reason": "aborted after rate limit"},
                ]})
            break
        if cand is None:
            attempts.append({"source": "nominatim", "result": "no_hit", "reason": "empty result list"})
        else:
            ok, diag = gate_candidate(cand["lat"], cand["lng"], state, coast_km, reverse_admin1)
            attempts.append({"source": "nominatim", "result": "passed" if ok else "rejected", **diag})
            if ok:
                geocode_fixed.append({**spot, "candidate": {
                    "source": "nominatim",
                    "lat": cand["lat"], "lng": cand["lng"],
                    "coast_km": diag["coast_km"], "rg_admin1": diag["rg_admin1"],
                }, "attempts": attempts})
                continue

        # Attempt 2: Overpass surf nodes around the town centroid.
        try:
            town_pt = nom.geocode_town(town, state)
        except RateLimitError as e:
            attempts.append({"source": "overpass", "result": "rate_limited_town_lookup", "reason": str(e)})
            manual_needed.append({**spot, "attempts": attempts})
            rate_limited = True
            for remaining in broken_iter[i:]:
                manual_needed.append({**remaining, "attempts": [
                    {"source": "nominatim", "result": "skipped", "reason": "aborted after rate limit"},
                    {"source": "overpass",  "result": "skipped", "reason": "aborted after rate limit"},
                ]})
            break
        if not town_pt:
            attempts.append({"source": "overpass", "result": "no_hit", "reason": "town not geocodable"})
            manual_needed.append({**spot, "attempts": attempts})
            continue

        try:
            elements = osm.query_surf_nodes_near(town_pt["lat"], town_pt["lng"])
        except RateLimitError as e:
            attempts.append({"source": "overpass", "result": "rate_limited", "reason": str(e)})
            manual_needed.append({**spot, "attempts": attempts})
            rate_limited = True
            for remaining in broken_iter[i:]:
                manual_needed.append({**remaining, "attempts": [
                    {"source": "nominatim", "result": "skipped", "reason": "aborted after rate limit"},
                    {"source": "overpass",  "result": "skipped", "reason": "aborted after rate limit"},
                ]})
            break
        match = best_overpass_match(elements, name)
        if not match:
            attempts.append({"source": "overpass", "result": "no_hit",
                             "reason": f"{len(elements)} surf nodes near town, none name-matched"})
            manual_needed.append({**spot, "attempts": attempts})
            continue

        ok, diag = gate_candidate(match["lat"], match["lng"], state, coast_km, reverse_admin1)
        attempts.append({"source": "overpass", "result": "passed" if ok else "rejected",
                         "osm_id": match["osm_id"], "matched_name": match["name"],
                         "fuzz_score": match["score"], **diag})
        if ok:
            osm_fixed.append({**spot, "candidate": {
                "source": "osm-overpass",
                "lat": match["lat"], "lng": match["lng"],
                "osm_id": match["osm_id"], "matched_name": match["name"],
                "coast_km": diag["coast_km"], "rg_admin1": diag["rg_admin1"],
            }, "attempts": attempts})
        else:
            manual_needed.append({**spot, "attempts": attempts})

    # --- write artifacts -------------------------------------------------
    (args.out_dir / "geocode_fixed.json").write_text(json.dumps(geocode_fixed, indent=2))
    (args.out_dir / "osm_fixed.json").write_text(json.dumps(osm_fixed, indent=2))
    (args.out_dir / "manual_needed.json").write_text(json.dumps(manual_needed, indent=2))

    summary = {
        "totals": {
            "spots_total": len(classified),
            "broken_total": len(broken),
            "broken_processed": len(broken_iter),
            "geocode_fixed": len(geocode_fixed),
            "osm_fixed": len(osm_fixed),
            "manual_needed": len(manual_needed),
        },
        "rate_limited": rate_limited,
        "gate": {"coast_km_broken_threshold": BROKEN_COAST_KM, "coast_km_gate": GATE_COAST_KM},
        "files": {
            "geocode_fixed": "geocode_fixed.json",
            "osm_fixed": "osm_fixed.json",
            "manual_needed": "manual_needed.json",
            "log": "measurement_log.txt",
        },
    }
    (args.out_dir / "measurement_summary.json").write_text(json.dumps(summary, indent=2))

    logfp.write(
        f"DONE broken={len(broken)} processed={len(broken_iter)} "
        f"geocode_fixed={len(geocode_fixed)} osm_fixed={len(osm_fixed)} "
        f"manual_needed={len(manual_needed)} rate_limited={rate_limited}\n"
    )
    logfp.close()

    print("\n" + "=" * 60)
    print("Spot-position measurement summary")
    print("=" * 60)
    for k, v in summary["totals"].items():
        print(f"  {k:<22}: {v}")
    print(f"  rate_limited          : {rate_limited}")
    print(f"\nArtifacts in {args.out_dir}/")
    print("=" * 60 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
