"""ECMWF HRES-WAM deterministic wave ingestion — isolated second-opinion source.

Pulls the ECMWF open-data HRES-WAM (``stream="wave"``, ``type="fc"``)
deterministic significant wave height / peak period / mean direction,
interpolates the 0.25° global grid onto every spot in the ``spots``
table, and upserts the RAW model values into the existing ``forecasts``
table tagged ``source = 'ecmwf'``.

This module is deliberately isolated from the NWPS pipeline:

  * It never imports or runs ``interpret`` / any rating or breaker-height
    math. ECMWF rows are a raw comparison feed, not a rated forecast.
  * It writes ONLY ``hs`` (← swh, metres), ``tp`` (← pp1d, s), ``dp``
    (← mwd, deg). Every other ``forecasts`` column is left NULL.
  * It uses the existing ``UNIQUE(spot_id, valid_time, source)`` key as
    the upsert conflict target — no schema change, no ``model`` column.
  * It triggers NO revalidation. The rows aren't displayed yet.

Run:
    python -m pipeline.ecmwf_wam            # latest available 00Z/12Z cycle
    python -m pipeline.ecmwf_wam --dry-run  # fetch+interp, skip the DB write

Requires the ``ecmwf-opendata`` client plus the cfgrib/eccodes GRIB
stack (see the ecmwf-wam workflow for the runner setup).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ecmwf_wam")

# US bounding box applied BEFORE interpolation. Longitudes here are in the
# -180..180 convention (we normalise the ECMWF 0..360 grid to match).
BBOX_LAT_MIN, BBOX_LAT_MAX = 18.0, 72.0
BBOX_LON_MIN, BBOX_LON_MAX = -180.0, -65.0

# HRES-WAM open-data step ladder for the 00Z/12Z cycles: 3-hourly to
# 144h, then 6-hourly to the 240h (10-day) horizon. We store each step
# at its native valid_time — no interpolation to hourly.
WAVE_STEPS = list(range(0, 145, 3)) + list(range(150, 241, 6))

WAVE_PARAMS = ["swh", "pp1d", "mwd"]

# Param → forecasts column. RAW passthrough, no unit conversion (swh is
# already metres, matching the hs column).
PARAM_TO_COLUMN = {"swh": "hs", "pp1d": "tp", "mwd": "dp"}

# Nearest-ocean fallback search radius, in degrees. At 0.25° (~25 km/cell)
# this lets a coastal spot sitting on a land-masked cell borrow the
# closest valid ocean cell up to ~1° (~4 cells, ~100 km) away.
FALLBACK_RADIUS_DEG = 1.0

UPSERT_BATCH = 500


# ---------------------------------------------------------------------------
# Spots (read straight from the spots table — id + coordinates only)
# ---------------------------------------------------------------------------

def fetch_spots(client) -> list[dict]:
    """Return [{id, slug, lat, lng}] for every spot, paginated."""
    rows: list[dict] = []
    page = 1000
    offset = 0
    while True:
        res = (
            client.table("spots")
            .select("id,slug,lat,lng")
            .order("id")
            .range(offset, offset + page - 1)
            .execute()
        )
        data = res.data or []
        rows.extend(data)
        if len(data) < page:
            break
        offset += page
    return rows


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_latest_wave(target: Path) -> datetime:
    """Retrieve the most recent available 00Z/12Z HRES-WAM cycle.

    Uses the client's ``latest()`` resolver keyed on step 240 — only the
    00Z and 12Z cycles run out to 240h, so "latest cycle that has step
    240" naturally resolves to the most recent 00/12 run and skips the
    short 06/18 cycles. If the nominal cycle hasn't landed on the AWS
    mirror yet, ``latest()`` returns the previous one instead of failing.
    """
    from ecmwf.opendata import Client

    client = Client(source="aws")

    request = dict(
        stream="wave",
        type="fc",
        param=WAVE_PARAMS,
    )

    # Resolve the newest cycle that carries the full 240h horizon.
    base = client.latest(step=240, **request)
    log.info("ecmwf: latest available HRES-WAM wave cycle = %s", base.isoformat())

    client.retrieve(
        date=base,
        step=WAVE_STEPS,
        target=str(target),
        **request,
    )
    size_mb = target.stat().st_size / 1e6
    log.info("ecmwf: downloaded %.1f MB to %s", size_mb, target)
    return base


# ---------------------------------------------------------------------------
# GRIB parse + grid prep
# ---------------------------------------------------------------------------

def open_wave_grib(path: Path):
    """Open the multi-step wave GRIB as a single (step, lat, lon) dataset.

    swh / pp1d / mwd share level + step type so they normally open as one
    xarray dataset. Falls back to merging cfgrib's split datasets if the
    single-open path raises on a heterogeneous hypercube.
    """
    import xarray as xr

    try:
        ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
        if all(p in ds.data_vars for p in WAVE_PARAMS):
            return ds
    except Exception as e:  # noqa: BLE001
        log.debug("ecmwf: single open_dataset failed (%s); merging split datasets", e)

    import cfgrib

    datasets = cfgrib.open_datasets(path, backend_kwargs={"indexpath": ""})
    merged = xr.merge(datasets, compat="override", join="outer")
    return merged


def prep_grid(ds):
    """Normalise longitudes to -180..180, sort, subset to the US bbox.

    Returns a dataset transposed to (step, latitude, longitude) so the
    downstream nearest-ocean extraction can index [:, i, j] directly.
    """
    import numpy as np

    lon = ds["longitude"].values
    if float(np.nanmax(lon)) > 180.0:
        ds = ds.assign_coords(
            longitude=(((ds["longitude"] + 180.0) % 360.0) - 180.0)
        )
    ds = ds.sortby("longitude").sortby("latitude")
    ds = ds.sel(
        latitude=slice(BBOX_LAT_MIN, BBOX_LAT_MAX),
        longitude=slice(BBOX_LON_MIN, BBOX_LON_MAX),
    )
    # Ensure a step dim even when a single step came back as a scalar.
    if "step" not in ds.dims:
        ds = ds.expand_dims("step")
    return ds.transpose("step", "latitude", "longitude")


def step_valid_times(ds) -> list[str]:
    """Per-step valid_time as ISO-8601 'Z' strings (base time + step)."""
    import numpy as np
    import pandas as pd

    # cfgrib serves base time as a naive (UTC) datetime64 and step as a
    # timedelta64, so the sum is naive UTC — append 'Z' directly.
    base = pd.to_datetime(np.asarray(ds["time"].values).reshape(-1)[0])
    steps = pd.to_timedelta(np.asarray(ds["step"].values).reshape(-1))
    return [(base + td).strftime("%Y-%m-%dT%H:%M:%SZ") for td in steps]


# ---------------------------------------------------------------------------
# Interpolation (bilinear) + nearest-ocean land-mask fallback
# ---------------------------------------------------------------------------

def interpolate_to_spots(ds, spots: list[dict]):
    """Bilinear-interp every spot; fall back to nearest ocean cell on NaN.

    Returns (records, counts) where records are ready-to-upsert dicts and
    counts is {exact, nearest_ocean, no_data}.

    NOTE: mean wave direction (mwd) is a circular quantity, so the
    exact-bilinear path interpolates its sin/cos components separately
    and recombines with atan2 — plain bilinear averaging would turn e.g.
    350° + 10° into 180°. swh and pp1d use plain bilinear. Nearest-ocean
    fallback spots copy a single grid cell, so they're unaffected either
    way.
    """
    import numpy as np
    import xarray as xr
    from scipy.spatial import cKDTree

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    valid_iso = step_valid_times(ds)
    n_steps = len(valid_iso)

    lat_vals = np.asarray(ds["latitude"].values, dtype=float)
    lon_vals = np.asarray(ds["longitude"].values, dtype=float)

    # Gridded arrays (step, nlat, nlon) for the nearest-ocean fallback path.
    grid = {p: np.asarray(ds[p].values, dtype=float) for p in WAVE_PARAMS}
    swh_grid = grid["swh"]

    # Ocean mask is constant across steps (land is land); take step 0.
    ocean_mask = np.isfinite(swh_grid[0])  # (nlat, nlon)
    ii, jj = np.where(ocean_mask)
    ocean_pts = np.column_stack([lat_vals[ii], lon_vals[jj]])
    tree = cKDTree(ocean_pts) if ocean_pts.shape[0] else None

    # Decompose direction into unit-vector components so the bilinear
    # interpolation below averages headings correctly across the 0/360
    # seam. atan2(sin, cos) after interpolation recovers the angle.
    mwd_rad = np.deg2rad(ds["mwd"])
    ds = ds.assign(_mwd_sin=np.sin(mwd_rad), _mwd_cos=np.cos(mwd_rad))

    # Vectorised bilinear interpolation for all spots at once.
    spot_lats = np.array([float(s["lat"]) for s in spots])
    spot_lons = np.array([float(s["lng"]) for s in spots])
    lat_da = xr.DataArray(spot_lats, dims="spot")
    lon_da = xr.DataArray(spot_lons, dims="spot")
    interp = ds.interp(latitude=lat_da, longitude=lon_da, method="linear")
    interp = interp.transpose("spot", "step")

    # swh / pp1d: plain bilinear. mwd: recombine interpolated sin/cos.
    bil = {
        "swh": np.asarray(interp["swh"].values, dtype=float),
        "pp1d": np.asarray(interp["pp1d"].values, dtype=float),
    }
    mwd_deg = np.rad2deg(
        np.arctan2(
            np.asarray(interp["_mwd_sin"].values, dtype=float),
            np.asarray(interp["_mwd_cos"].values, dtype=float),
        )
    )
    bil["mwd"] = np.where(mwd_deg < 0, mwd_deg + 360.0, mwd_deg)  # (nspot, nstep)

    records: list[dict] = []
    counts = {"exact": 0, "nearest_ocean": 0, "no_data": 0}

    for si, spot in enumerate(spots):
        sid = spot.get("id")
        if sid is None:
            counts["no_data"] += 1
            continue

        hs_vec = bil["swh"][si]
        if np.isfinite(hs_vec[0]):
            tp_vec, dp_vec = bil["pp1d"][si], bil["mwd"][si]
            counts["exact"] += 1
        else:
            # Bilinear hit a land-masked cell — borrow nearest ocean cell.
            placed = False
            if tree is not None:
                dist, idx = tree.query([spot_lats[si], spot_lons[si]])
                if np.isfinite(dist) and dist <= FALLBACK_RADIUS_DEG:
                    gi, gj = ii[idx], jj[idx]
                    hs_vec = swh_grid[:, gi, gj]
                    tp_vec = grid["pp1d"][:, gi, gj]
                    dp_vec = grid["mwd"][:, gi, gj]
                    counts["nearest_ocean"] += 1
                    placed = True
            if not placed:
                counts["no_data"] += 1
                continue

        for k in range(n_steps):
            hs = hs_vec[k]
            if not np.isfinite(hs):
                continue
            tp = tp_vec[k]
            dp = dp_vec[k]
            records.append(
                {
                    "spot_id": int(sid),
                    "valid_time": valid_iso[k],
                    "hs": float(hs),
                    "tp": float(tp) if np.isfinite(tp) else None,
                    "dp": float(dp) if np.isfinite(dp) else None,
                    "source": "ecmwf",
                    "fetched_at": fetched_at,
                }
            )

    return records, counts


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_forecasts(client, records: list[dict]) -> int:
    """Chunked upsert into forecasts on (spot_id, valid_time, source)."""
    written = 0
    for i in range(0, len(records), UPSERT_BATCH):
        chunk = records[i : i + UPSERT_BATCH]
        client.table("forecasts").upsert(
            chunk, on_conflict="spot_id,valid_time,source"
        ).execute()
        written += len(chunk)
    return written


# ---------------------------------------------------------------------------
# Verification summary
# ---------------------------------------------------------------------------

def print_summary(base, n_steps, spots, records, counts, written, dry_run):
    cycle_hh = base.strftime("%HZ")
    spots_written = len({r["spot_id"] for r in records})

    print("\n" + "=" * 64)
    print("ECMWF HRES-WAM ingestion summary")
    print("=" * 64)
    print(f"  Cycle ingested      : {base.strftime('%Y-%m-%d')} {cycle_hh}")
    print(f"  Forecast steps      : {n_steps}")
    print(f"  Spots in table      : {len(spots)}")
    print(f"  Spots written       : {spots_written}")
    print(f"  Rows {'prepared' if dry_run else 'upserted':<14}: {written}")
    print("  Land-mask handling:")
    print(f"    exact cell        : {counts['exact']}")
    print(f"    nearest-ocean     : {counts['nearest_ocean']}")
    print(f"    no valid point    : {counts['no_data']}")

    # 5-spot near-term sample (first step of each sampled spot).
    print("  Sample (near-term step):")
    seen: set[int] = set()
    shown = 0
    for r in records:
        if r["spot_id"] in seen:
            continue
        seen.add(r["spot_id"])
        tp = f"{r['tp']:.1f}" if r["tp"] is not None else "  -"
        dp = f"{r['dp']:.0f}" if r["dp"] is not None else "  -"
        print(
            f"    spot {r['spot_id']:>5}  {r['valid_time']}  "
            f"hs={r['hs']:.2f}m  tp={tp}s  dp={dp}°"
        )
        shown += 1
        if shown >= 5:
            break
    print("=" * 64 + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch + interpolate but skip the Supabase upsert.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Reuse the existing Supabase connection setup from db_import.
    from pipeline.db_import import get_client

    client = get_client()
    spots = fetch_spots(client)
    if not spots:
        log.error("ecmwf: no spots returned from the spots table; aborting")
        return 2
    log.info("ecmwf: %d spots loaded from the spots table", len(spots))

    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "ecmwf_wave.grib2"
        base = download_latest_wave(target)
        ds = open_wave_grib(target)
        ds = prep_grid(ds)
        n_steps = ds.sizes.get("step", 0)
        log.info(
            "ecmwf: grid subset to %d×%d cells, %d steps",
            ds.sizes.get("latitude", 0),
            ds.sizes.get("longitude", 0),
            n_steps,
        )
        records, counts = interpolate_to_spots(ds, spots)

    if args.dry_run:
        log.info("ecmwf: --dry-run, skipping upsert (%d rows prepared)", len(records))
        written = len(records)
    else:
        written = upsert_forecasts(client, records)
        log.info("ecmwf: upserted %d rows into forecasts (source=ecmwf)", written)

    print_summary(base, n_steps, spots, records, counts, written, args.dry_run)
    return 0 if records else 2


if __name__ == "__main__":
    sys.exit(main())
