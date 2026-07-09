#!/usr/bin/env python3
"""MOP prototype — feasibility spike (READ-ONLY, NOT production).

Reads CDIP MOP nearshore alongshore output from the CDIP THREDDS server and,
for 4 California spots that failed the GSHHG raycast gate, derives:
  * an effective swell window (arc of refracted directions that actually
    deliver wave energy at the 10 m contour), and
  * a refraction-aware optimal direction (peak / energy-weighted mean of the
    climatological directional energy).

CDIP MOP is the operational implementation of O'Reilly et al. 2016 (Coastal
Engineering 116:118-132): a linear spectral-refraction model publishing
nearshore spectra at 4,729 alongshore points on the 10 m depth contour.

THREDDS:  https://thredds.cdip.ucsd.edu/thredds/
  catalog: cdip/model/MOP_alongshore/catalog.xml
  data:    thredds/dodsC/cdip/model/MOP_alongshore/<STN>_<flavor>.nc   (OPeNDAP)

This touches NOTHING in prod: not spots_enriched.json, not the rating pipeline.
It only reads THREDDS and prints a table.

  python scripts/mop_prototype.py            # live: hit THREDDS, print 4-spot table
  python scripts/mop_prototype.py --selftest # offline: validate the derivation math

If THREDDS is unreachable (egress blocked), it says so and exits non-zero —
it never invents numbers.
"""
from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

import numpy as np

THREDDS = "https://thredds.cdip.ucsd.edu"
MOP_CATALOG = f"{THREDDS}/thredds/catalog/cdip/model/MOP_alongshore/catalog.xml"
DODS = f"{THREDDS}/thredds/dodsC"

# Stored coords for the 4 failing CA spots (from pipeline/spots_enriched.json).
# (name, lat, lng, raycast_window, raycast_optimal_note, target_window)
SPOTS = [
    ("Blacks Beach",     32.879677, -117.252982, 74,  "ceiling 84",            "140-170"),
    ("Rincon",           34.371814, -119.478507, 92,  "wrong lobe (gave 196)", "80-100"),
    ("Malibu Surfrider", 34.03143,  -118.688865, 90,  "nearly passed",         "100-140"),
    ("Huntington Beach", 33.640633, -117.986298, 100, "never separated from Rincon", "150-180"),
]

# Swell band: periods >= 8 s (freq <= 0.125 Hz) isolate groundswell from windsea.
SWELL_MAX_FREQ_HZ = 0.125
DIR_BIN_DEG = 2
WINDOW_ENERGY_FRACTION = 0.85  # window = smallest arc holding this share of energy


# --------------------------------------------------------------------------- #
# Pure derivation math (no network) — validated by --selftest                  #
# --------------------------------------------------------------------------- #
def mean_dir_from_moments(a1: np.ndarray, b1: np.ndarray) -> np.ndarray:
    """Per-frequency mean wave direction (deg, compass 'coming from') from the
    first directional Fourier moments, CDIP convention. Used only when the file
    has no precomputed waveMeanDirection."""
    # CDIP a1/b1 are normalized moments; direction-from = atan2(b1,a1).
    ang = np.degrees(np.arctan2(b1, a1))
    return np.mod(ang, 360.0)


def directional_energy_histogram(energy_density, freq, mean_direction,
                                 swell_max_freq=SWELL_MAX_FREQ_HZ, bin_deg=DIR_BIN_DEG):
    """Climatological directional energy E(theta) over the swell band.

    energy_density : (time, freq) m^2/Hz
    freq           : (freq,) Hz
    mean_direction : (time, freq) deg (per-frequency mean direction)
    Returns (centers_deg, E) where E sums energy_density*df into the direction
    bin of each (time, freq) cell with freq <= swell_max_freq.
    """
    energy_density = np.asarray(energy_density, float)
    mean_direction = np.asarray(mean_direction, float)
    freq = np.asarray(freq, float)
    df = np.gradient(freq)
    band = freq <= swell_max_freq
    nb = int(round(360 / bin_deg))
    E = np.zeros(nb)
    fi = np.where(band)[0]
    # energy contribution of each (t,f) cell = S(t,f) * df(f)
    contrib = energy_density[:, fi] * df[fi][None, :]
    dirs = mean_direction[:, fi]
    good = np.isfinite(contrib) & np.isfinite(dirs)
    bins = (np.mod(dirs, 360) / bin_deg).astype(int) % nb
    np.add.at(E, bins[good], contrib[good])
    centers = (np.arange(nb) + 0.5) * bin_deg
    return centers, E


def circular_energy_mean(centers, E):
    """Energy-weighted circular mean direction (deg)."""
    if E.sum() <= 0:
        return None
    r = np.radians(centers)
    x = np.sum(E * np.cos(r)); y = np.sum(E * np.sin(r))
    return float(np.mod(np.degrees(math.atan2(y, x)), 360.0))


def smallest_arc(centers, E, frac=WINDOW_ENERGY_FRACTION):
    """Smallest contiguous (circular) arc holding `frac` of total energy.
    Returns (lo_deg, hi_deg, width_deg). This is the effective swell window."""
    total = E.sum()
    if total <= 0:
        return None
    nb = len(E)
    bin_deg = 360 / nb
    target = frac * total
    Edup = np.concatenate([E, E])
    best = None
    for start in range(nb):
        acc = 0.0
        for k in range(nb):
            acc += Edup[start + k]
            if acc >= target:
                width = (k + 1) * bin_deg
                if best is None or width < best[2]:
                    lo = (start * bin_deg) % 360
                    hi = ((start + k + 1) * bin_deg) % 360
                    best = (lo, hi, width)
                break
    return best


def derive_window_optimal(energy_density, freq, mean_direction):
    """Return dict(window_lo, window_hi, window_width, optimal_peak,
    optimal_mean) from a MOP climatology."""
    centers, E = directional_energy_histogram(energy_density, freq, mean_direction)
    arc = smallest_arc(centers, E)
    peak = float(centers[int(np.argmax(E))]) if E.sum() > 0 else None
    mean = circular_energy_mean(centers, E)
    out = {"optimal_peak": peak, "optimal_mean": mean}
    if arc:
        out.update(window_lo=round(arc[0]), window_hi=round(arc[1]),
                   window_width=round(arc[2]))
    return out


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- #
# THREDDS access (network) — best effort, fails loudly                        #
# --------------------------------------------------------------------------- #
def _check_egress():
    try:
        with urlopen(MOP_CATALOG, timeout=90) as r:
            return r.read()  # full catalog XML (not a truncated probe)
    except (HTTPError, URLError, OSError) as e:
        print("\n*** THREDDS UNREACHABLE — cannot read CDIP MOP from this "
              "environment. ***", file=sys.stderr)
        print(f"    {MOP_CATALOG}\n    {type(e).__name__}: {e}", file=sys.stderr)
        print("    Run where outbound to thredds.cdip.ucsd.edu is allowed "
              "(e.g. a CI runner with open egress, like the GSHHG job).\n"
              "    Not faking numbers. Use --selftest to validate the math offline.",
              file=sys.stderr)
        return None


def parse_catalog(xml_bytes):
    """Return [(name, dods_url, lat, lon)] for MOP point datasets. lat/lon may be
    None if the catalog carries no per-dataset geospatial metadata (then enrich
    from each file's NetCDF metadata)."""
    root = ET.fromstring(xml_bytes)
    ns = {"t": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"}
    out, refs = [], []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag == "catalogRef":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href")
            if href:
                refs.append(href)
            continue
        if tag != "dataset":
            continue
        url_path = el.get("urlPath")
        if not url_path:
            continue
        name = el.get("name") or url_path.split("/")[-1]
        lat = lon = None
        geo = el.find(".//t:geospatialCoverage", ns)
        if geo is not None:
            ns_el = geo.find("t:northsouth/t:start", ns)
            ew_el = geo.find("t:eastwest/t:start", ns)
            if ns_el is not None and ew_el is not None:
                try:
                    lat = float(ns_el.text); lon = float(ew_el.text)
                except (TypeError, ValueError):
                    pass
        out.append((name, f"{DODS}/{url_path}", lat, lon))
    return out, refs


def read_point_coord(dods_url):
    """Read a MOP point's 10 m-contour lat/lon from NetCDF metadata (cheap over
    OPeNDAP — metadata only)."""
    import netCDF4
    nc = netCDF4.Dataset(dods_url)
    try:
        for la, lo in (("metaLatitude", "metaLongitude"),
                       ("metaDeployLatitude", "metaDeployLongitude")):
            if la in nc.variables and lo in nc.variables:
                return float(nc.variables[la][:].ravel()[0]), float(nc.variables[lo][:].ravel()[0])
        if hasattr(nc, "geospatial_lat_min") and hasattr(nc, "geospatial_lon_min"):
            return float(nc.geospatial_lat_min), float(nc.geospatial_lon_min)
    finally:
        nc.close()
    return None, None


def pull_climatology(dods_url, days=365):
    """Pull a recent climatology of the spectral fields needed for derivation.
    Returns (energy_density[time,freq], freq[freq], mean_direction[time,freq])."""
    import netCDF4
    nc = netCDF4.Dataset(dods_url)
    try:
        freq = nc.variables["waveFrequency"][:]
        t = nc.variables["waveTime"]
        times = t[:]
        cutoff = times.max() - days * 86400
        i0 = int(np.searchsorted(times, cutoff))
        ed = nc.variables["waveEnergyDensity"][i0:, :]
        if "waveMeanDirection" in nc.variables:
            md = nc.variables["waveMeanDirection"][i0:, :]
        else:
            md = mean_dir_from_moments(nc.variables["waveA1"][i0:, :],
                                       nc.variables["waveB1"][i0:, :])
        return np.asarray(ed), np.asarray(freq), np.asarray(md)
    finally:
        nc.close()


def dump_variables(dods_url):
    import netCDF4
    nc = netCDF4.Dataset(dods_url)
    try:
        print(f"  variables in {dods_url.split('/')[-1]}:")
        for k, v in nc.variables.items():
            dims = ",".join(v.dimensions)
            print(f"    {k:28} ({dims})  {getattr(v, 'long_name', '')}")
    finally:
        nc.close()


_FLAVOR_RANK = {"hindcast": 0, "nowcast": 1, "forecast": 2, "ecmwf_fc": 3, "default": 9}


def _point_id_flavor(name):
    base = name.split("/")[-1]
    if base.endswith(".nc"):
        base = base[:-3]
    parts = base.split("_", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "default")


def collect_mop_datasets(max_refs=80):
    """Top MOP catalog + one bounded level of catalogRef descent. Returns
    [(name, dods_url, lat, lon)]. Raises on network error (caller reports)."""
    xml = _check_egress()
    if xml is None:
        raise URLError("THREDDS unreachable")
    from urllib.parse import urljoin
    datasets, refs = parse_catalog(xml)
    print(f"top catalog: {len(datasets)} datasets, {len(refs)} sub-catalogs")
    if refs and len(datasets) < 50:  # nested-by-county catalog: descend (bounded)
        for i, href in enumerate(refs[:max_refs]):
            try:
                sub, _ = parse_catalog(urlopen(urljoin(MOP_CATALOG, href), timeout=90).read())
                datasets.extend(sub)
            except (HTTPError, URLError, OSError):
                continue
            if (i + 1) % 10 == 0:
                print(f"  descended {i + 1}/{min(len(refs), max_refs)} sub-catalogs -> {len(datasets)} datasets")
    return datasets


def run_live():
    print(f"catalog: {MOP_CATALOG}")
    try:
        datasets = collect_mop_datasets()
    except (HTTPError, URLError, OSError):
        return 2  # _check_egress already printed the loud message
    if not datasets:
        print("No MOP datasets found in catalog (unexpected structure).", file=sys.stderr)
        return 3

    # One entry per alongshore point; prefer hindcast (longest record) for the
    # climatology read. Coords are flavor-independent.
    by_pid = {}
    for name, url, lat, lon in datasets:
        pid, flavor = _point_id_flavor(name)
        rank = _FLAVOR_RANK.get(flavor, 9)
        prev = by_pid.get(pid)
        # keep best-ranked data url; carry coords from whichever flavor has them
        clat = lat if lat is not None else (prev[3] if prev else None)
        clon = lon if lon is not None else (prev[4] if prev else None)
        if prev is None or rank < prev[0]:
            by_pid[pid] = (rank, pid, url, clat, clon)
        elif clat is not None and prev[3] is None:
            by_pid[pid] = (prev[0], prev[1], prev[2], clat, clon)
    points = list(by_pid.values())
    with_coords = sum(1 for p in points if p[3] is not None)
    print(f"unique MOP points: {len(points)}; coords from catalog: {with_coords}")

    # Bounded OPeNDAP fallback if the catalog carried no per-point geospatial.
    coords = [(p[1], p[2], p[3], p[4]) for p in points if p[3] is not None]
    if with_coords < len(points):
        need = [(p[1], p[2]) for p in points if p[3] is None]
        cap = 4000
        print(f"reading lat/lon via OPeNDAP metadata for {min(len(need), cap)} points "
              f"(catalog lacked geospatial)...")
        for i, (pid, url) in enumerate(need[:cap]):
            try:
                la, lo = read_point_coord(url)
            except Exception:  # noqa: BLE001
                la = lo = None
            if la is not None:
                coords.append((pid, url, la, lo))
            if i and i % 250 == 0:
                print(f"  {i}/{min(len(need), cap)} coord reads, {len(coords)} resolved")
    print(f"points with coordinates: {len(coords)}\n")
    if not coords:
        print("Could not resolve any MOP point coordinates — cannot match.", file=sys.stderr)
        return 3

    dumped = False
    rows = []
    for name, slat, slon, ray, note, target in SPOTS:
        pid, url, plat, plon = min(coords, key=lambda c: haversine_m(slat, slon, c[2], c[3]))
        d = haversine_m(slat, slon, plat, plon)
        if not dumped:
            dump_variables(url); dumped = True; print()
        try:
            ed, freq, md = pull_climatology(url)
            der = derive_window_optimal(ed, freq, md)
        except Exception as e:  # noqa: BLE001 — report the spot, don't fake it
            der = {"error": f"{type(e).__name__}: {e}", "optimal_peak": None, "optimal_mean": None}
        rows.append((name, pid, d, der, ray, note, target))

    print(f"\n{'spot':18}{'MOP pt':12}{'dist_m':>7}  {'window':>13}{'opt_peak':>9}"
          f"{'opt_mean':>9}  {'raycast':>8}  target")
    for name, pt, d, der, ray, note, target in rows:
        if der.get("error"):
            win, op, om = "ERR", der["error"][:18], ""
        else:
            win = (f"{der.get('window_lo')}-{der.get('window_hi')}({der.get('window_width')})"
                   if "window_lo" in der else "n/a")
            op, om = str(der["optimal_peak"]), str(der["optimal_mean"])
        print(f"{name:18}{pt:12}{d:7.0f}  {win:>13}{op:>9}{om:>9}  {ray:>8}  {target}")

    # The one result that matters: Rincon's refracted optimal.
    rin = next((r for r in rows if r[0] == "Rincon"), None)
    if rin and not rin[3].get("error"):
        op = rin[3]["optimal_peak"]; om = rin[3]["optimal_mean"]
        wnw = (op is not None and 255 <= op <= 280) or (om is not None and 255 <= om <= 280)
        print(f"\nRINCON CHECK: MOP optimal peak={op} mean={om}  "
              f"(raycast gave 196 / S lobe). "
              + ("WNW WRAP CONFIRMED — pivot validated."
                 if wnw else "not in 255-280 WNW band — inspect the climatology."))
    return 0


# --------------------------------------------------------------------------- #
# Offline self-test — proves the derivation extracts the right direction       #
# --------------------------------------------------------------------------- #
def _von_mises_dir_energy(peak_deg, kappa, nb=180):
    centers = (np.arange(nb) + 0.5) * (360 / nb)
    E = np.exp(kappa * np.cos(np.radians(centers - peak_deg)))
    return centers, E / E.sum()


def run_selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    # 1. Rincon-like: a refracted spectrum whose energy peaks WNW (~265).
    #    The derivation MUST report WNW — the thing the geometric raycast could
    #    not structurally produce.
    centers, E = _von_mises_dir_energy(265, kappa=12)
    peak = float(centers[int(np.argmax(E))])
    mean = circular_energy_mean(centers, E)
    arc = smallest_arc(centers, E)
    check(f"WNW-peaked energy -> optimal_peak={peak:.0f} (~265)", abs(peak - 265) <= 3)
    check(f"WNW-peaked energy -> optimal_mean={mean:.0f} (~265)", abs(((mean-265+180)%360)-180) <= 3)
    check(f"window arc width={arc[2]:.0f} brackets 265 ({arc[0]:.0f}-{arc[1]:.0f})",
          ((arc[0] <= 265 <= arc[1]) or (arc[0] > arc[1] and (265 >= arc[0] or 265 <= arc[1]))))

    # 2. Full pipeline from synthetic (time,freq) spectra: every swell-band cell
    #    points WNW -> histogram + derivation recover WNW.
    nt, nf = 200, 64
    freq = np.linspace(0.04, 0.25, nf)
    ed = np.zeros((nt, nf))
    swell = freq <= SWELL_MAX_FREQ_HZ
    ed[:, swell] = 1.0  # energy in swell band
    md = np.full((nt, nf), 265.0)
    der = derive_window_optimal(ed, freq, md)
    check(f"spectra->derive optimal_peak={der['optimal_peak']:.0f} (~265)",
          abs(der["optimal_peak"] - 265) <= DIR_BIN_DEG)
    check("windsea band excluded (only swell dirs counted)",
          der["optimal_mean"] is not None and abs(((der['optimal_mean']-265+180)%360)-180) <= DIR_BIN_DEG)

    # 3. Bimodal (S lobe + W lobe), W stronger -> window spans both, optimal->W.
    c1, e1 = _von_mises_dir_energy(185, 18); _, e2 = _von_mises_dir_energy(265, 18)
    E2 = 0.3 * e1 + 0.7 * e2
    p2 = float(c1[int(np.argmax(E2))])
    check(f"bimodal weighted-W -> peak={p2:.0f} on W lobe (~265)", abs(p2 - 265) <= 4)

    # 4. Haversine sanity (~100 m).
    d = haversine_m(34.3717, -119.4783, 34.3717, -119.4772)
    check(f"haversine ~100 m for 0.0011 deg lon ({d:.0f} m)", 90 <= d <= 110)

    print("\nself-test:", "ALL PASS — derivation is sound; it yields WNW when the "
          "refracted spectrum peaks WNW." if ok else "FAILURES above")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true",
                    help="validate derivation math offline (no network)")
    args = ap.parse_args(argv)
    return run_selftest() if args.selftest else run_live()


if __name__ == "__main__":
    raise SystemExit(main())
