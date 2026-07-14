"""NDBC realtime2 directional-spectrum reader (Stage 2c — READER + DIAGNOSTIC ONLY).

Builds a DEGREE-VALUED, energy-weighted buoy swell direction (and a swell/wind-sea
energy split) from NDBC's raw directional spectra, so the CG0_Trkng direction thesis
can be tested against a reference that isn't quantized to 22.5° compass bins.

WHY: the buoy .spec SwD is a 16-point cardinal string (ENE/E/ESE → 68/90/112°); MWD is
the mean direction at the single DOMINANT frequency bin only. Neither is a like-for-like
partner for a smooth model swell direction. NDBC also publishes the per-frequency
directional spectra that let us compute the right statistics.

FILES (NDBC realtime2, per station):
  {ID}.data_spec  C11(f), spectral energy density (m²/Hz), with a SEPARATION FREQUENCY
  {ID}.swdir      alpha1(f), mean wave direction per band, DEGREES
  ({ID}.swdir2 = alpha2, {ID}.swr1/{ID}.swr2 = r1/r2 describe directional SPREAD; they
   are NOT needed for an energy-weighted MEAN of alpha1, so we don't fetch them —
   politeness, task 5. Add them only if a full 2-D D(f,θ) reconstruction is ever wanted.)

.data_spec row layout (verified from NDBC's data_spec description + the Web Data Guide):
    YY MM DD hh mm  Sep_Freq  e1 (f1)  e2 (f2)  …           # energy value (freq Hz) pairs
.swdir row layout:
    YY MM DD hh mm  a1_1 (f1)  a1_2 (f2)  …                 # direction value (freq Hz) pairs

DIRECTION CONVENTION (critical — a wrong call flips everything 180°): NDBC mean wave
direction (MWD and the per-band alpha1) is the compass bearing the waves are coming
FROM, degrees clockwise from true North (0°=from N, 270°=from W) — NDBC "Observation
Data Descriptions" / "Measurement Descriptions and Units". This is the SAME "from"
convention as the buoy MWD/SwD and the NWPS model swell direction, so NO 180° flip is
applied; our output inherits alpha1's convention directly. The --diag is itself a check:
if spectral swell_dir came out ~180° from the buoy SwD, the convention would be wrong.

BAND SPLIT (task 2): we use NDBC's OWN per-record separation frequency (the Sep_Freq
column of .data_spec) as the wind-sea/swell boundary — swell = f < Sep_Freq, wind-sea =
f ≥ Sep_Freq. That is defensible because it is computed by NDBC from the actual spectrum
each hour (sea-state adaptive, not a magic constant) AND it is the same boundary NDBC
uses for its published SwH/WWH, so our Hs_swell should match the buoy's SwH by
construction (the diag prints that comparison). When Sep_Freq is missing we fall back to
SWELL_WINDSEA_CUTOFF_HZ (0.10 Hz ≈ 10 s), the common oceanographic swell threshold.

READER + DIAGNOSTIC ONLY: changes nothing in the rating, trust gate, interpret.py, or
spots_enriched.json. GUARDRAIL: this is a CORRECT reference, not a way to flatter the
model — no outlier rejection, no hour filtering.

  python -m pipeline.forecast.ndbc_spectral --selftest            # offline
  python -m pipeline.forecast.ndbc_spectral --buoy 44095          # Mac (NDBC)
"""
from __future__ import annotations

import argparse
import datetime
import math

# Fixed fallback ONLY (primary split is NDBC's per-record Sep_Freq). ~10 s period.
SWELL_WINDSEA_CUTOFF_HZ = 0.10

# NDBC realtime2 missing sentinels: 999.0 for per-band direction, 9999.0 elsewhere; "MM"
# in text. A direction is valid only in [0, 360); energy must be finite and ≥ 0.
_DIR_MISSING = 900.0     # any alpha1 ≥ this (e.g. 999.0) is missing, not a bearing
_ENERGY_MISSING = 9999.0


# --------------------------------------------------------------------------- #
# Pure parse (unit-tested; no network)                                         #
# --------------------------------------------------------------------------- #
def _f(tok):
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None   # "MM" and friends


def _epoch_hour(yy, mm, dd, hh, mn):
    dt = datetime.datetime(int(yy), int(mm), int(dd), int(hh), int(mn),
                           tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() // 3600)


def _parse_row(line):
    """One realtime2 spectral row → (epoch_hour, [lone floats], [(freq, value), …]).
    A '(freq)' token pairs with the value token before it; a value token NOT followed by
    a '(' is 'lone' (the Sep_Freq of .data_spec). Handles both the paired files and a
    bare-value file (all lone). Returns None for headers / short / unparseable rows."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split()
    if len(parts) < 6:
        return None
    try:
        eh = _epoch_hour(*parts[:5])
    except (ValueError, TypeError):
        return None
    toks = parts[5:]
    lone, pairs, k = [], [], 0
    while k < len(toks):
        if k + 1 < len(toks) and toks[k + 1].startswith("("):
            val = _f(toks[k])
            freq = _f(toks[k + 1].strip("()"))
            if freq is not None:
                pairs.append((freq, val))
            k += 2
        else:
            lone.append(_f(toks[k]))
            k += 1
    return eh, lone, pairs


def parse_data_spec(text):
    """{epoch_hour: {"sep_freq": float|None, "freqs": [f…], "c11": [e…]}} from a .data_spec
    file. Energy densities aligned to their frequencies; Sep_Freq is the lone leading value."""
    out = {}
    for line in (text or "").splitlines():
        row = _parse_row(line)
        if row is None:
            continue
        eh, lone, pairs = row
        if not pairs:
            continue
        pairs.sort(key=lambda p: p[0])
        out[eh] = {"sep_freq": (lone[0] if lone else None),
                   "freqs": [f for f, _ in pairs], "c11": [v for _, v in pairs]}
    return out


def parse_swdir(text):
    """{epoch_hour: {freq: alpha1_deg}} from a .swdir file (per-band mean direction)."""
    out = {}
    for line in (text or "").splitlines():
        row = _parse_row(line)
        if row is None:
            continue
        eh, _lone, pairs = row
        if pairs:
            out[eh] = {f: v for f, v in pairs}
    return out


# --------------------------------------------------------------------------- #
# Pure math (unit-tested)                                                       #
# --------------------------------------------------------------------------- #
def vector_mean_dir(dirs_deg, weights):
    """Energy-weighted CIRCULAR mean of compass bearings (degrees), convention-preserving.
    atan2(Σ w·sinθ, Σ w·cosθ) — NOT an arithmetic mean, so 350° and 10° average to 0°, not
    180°. Ignores None/out-of-range dirs and non-positive weights. Returns None if nothing
    valid or the resultant is zero-length (fully opposed)."""
    sx = sy = sw = 0.0
    for d, w in zip(dirs_deg, weights):
        if d is None or w is None or w <= 0 or not (0.0 <= d < 360.0):
            continue
        r = math.radians(d)
        sx += w * math.cos(r)
        sy += w * math.sin(r)
        sw += w
    # resultant negligible vs total weight → direction undefined (fully opposed / no data).
    # Relative test, not `== 0`: sin(180°) is ~1e-16, so opposed vectors never cancel exactly.
    if sw <= 0 or math.hypot(sx, sy) <= 1e-9 * sw:
        return None
    return math.degrees(math.atan2(sy, sx)) % 360.0


def _bin_widths(freqs):
    """Per-bin frequency width df (central differences; handles an uneven grid). A single
    bin gets width 0 → zero energy, which is the safe/degenerate answer."""
    n = len(freqs)
    if n == 0:
        return []
    if n == 1:
        return [0.0]
    df = [0.0] * n
    df[0] = freqs[1] - freqs[0]
    df[-1] = freqs[-1] - freqs[-2]
    for i in range(1, n - 1):
        df[i] = (freqs[i + 1] - freqs[i - 1]) / 2.0
    return df


def _hs_from_energy(energies):
    """Hs = 4·sqrt(Σ energy), energy per bin = C11·df ≈ ∫C11 df (m²). None if no energy."""
    tot = sum(e for e in energies if e is not None and e >= 0.0)
    return 4.0 * math.sqrt(tot) if tot > 0 else 0.0


def spectral_metrics(spec, dirs, *, cutoff_hz=SWELL_WINDSEA_CUTOFF_HZ):
    """Per-hour swell/wind-sea/total statistics from one hour's parsed spectrum + directions.
    *spec* = {"sep_freq", "freqs", "c11"}; *dirs* = {freq: alpha1_deg}. Band split uses
    spec['sep_freq'] (NDBC's own) when present, else *cutoff_hz*. Returns a dict:
      {hs_total, hs_swell, hs_windsea, swell_dir, windsea_dir, total_mean_dir,
       swell_frac, sep_freq_used, n_bands}
    Directions are energy-weighted circular means of alpha1 (degrees FROM, true North).
    Energy for a band uses C11·df so it matches the Hs integral. Pure."""
    freqs = spec.get("freqs") or []
    c11 = spec.get("c11") or []
    sep = spec.get("sep_freq")
    sep_used = sep if (sep is not None and 0.0 < sep < 1.0) else cutoff_hz
    df = _bin_widths(freqs)

    tot_e, sw_e, ws_e = [], [], []              # per-bin energy (C11·df), by band
    tot_d, tot_w, sw_d, sw_w, ws_d, ws_w = [], [], [], [], [], []
    for f, e, d_f in zip(freqs, c11, df):
        if e is None or not math.isfinite(e) or e < 0 or e >= _ENERGY_MISSING:
            continue
        energy = e * d_f
        a1 = dirs.get(f)
        if a1 is not None and a1 >= _DIR_MISSING:
            a1 = None                            # 999.0 missing-direction sentinel
        tot_e.append(energy); tot_d.append(a1); tot_w.append(energy)
        if f < sep_used:                         # swell = lower frequency (longer period)
            sw_e.append(energy); sw_d.append(a1); sw_w.append(energy)
        else:
            ws_e.append(energy); ws_d.append(a1); ws_w.append(energy)

    hs_total = _hs_from_energy(tot_e)
    hs_swell = _hs_from_energy(sw_e)
    hs_windsea = _hs_from_energy(ws_e)
    return {
        "hs_total": hs_total, "hs_swell": hs_swell, "hs_windsea": hs_windsea,
        "swell_dir": vector_mean_dir(sw_d, sw_w),
        "windsea_dir": vector_mean_dir(ws_d, ws_w),
        "total_mean_dir": vector_mean_dir(tot_d, tot_w),
        "swell_frac": (hs_swell / hs_total) if hs_total > 0 else None,
        "sep_freq_used": sep_used, "n_bands": len(freqs),
    }


def compute(data_spec_text, swdir_text, *, cutoff_hz=SWELL_WINDSEA_CUTOFF_HZ):
    """{epoch_hour: spectral_metrics} from raw .data_spec + .swdir text. Pure/offline —
    this is the whole reader minus the fetch, so it's fully unit-testable."""
    spec_by_hour = parse_data_spec(data_spec_text)
    dir_by_hour = parse_swdir(swdir_text)
    out = {}
    for eh, spec in spec_by_hour.items():
        out[eh] = spectral_metrics(spec, dir_by_hour.get(eh, {}), cutoff_hz=cutoff_hz)
    return out


# --------------------------------------------------------------------------- #
# Circular delta stats (for the diag's OLD/NEW/CONTROL comparisons)             #
# --------------------------------------------------------------------------- #
def delta_stats(model_dirs, buoy_dirs):
    """(n, mean_delta_deg, circ_std_deg) of the signed angular differences (model − buoy)
    over paired hours where both are present. mean_delta is the circular mean of the
    per-pair deltas (in −180..180); circ_std is sqrt(−2 ln R). None-safe; n counts pairs."""
    sx = sy = 0.0
    n = 0
    for m, b in zip(model_dirs, buoy_dirs):
        if m is None or b is None:
            continue
        d = math.radians(((m - b + 180.0) % 360.0) - 180.0)
        sx += math.cos(d); sy += math.sin(d); n += 1
    if n == 0:
        return 0, None, None
    mean = math.degrees(math.atan2(sy / n, sx / n))
    r = math.hypot(sx / n, sy / n)
    circ_std = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(r)))) if r > 1e-9 else float("inf")
    return n, mean, circ_std


# --------------------------------------------------------------------------- #
# Live fetch (reuses the existing NDBC realtime2 fetch/cache layer)             #
# --------------------------------------------------------------------------- #
def by_hour(buoy_id, *, use_cache=False, cutoff_hz=SWELL_WINDSEA_CUTOFF_HZ):
    """{epoch_hour: spectral_metrics} for a live station, or {} if the spectra are
    unavailable. Reuses pipeline.forecast.buoys._fetch_text (its cache + polite fetch) —
    no parallel fetch path. Fetches only .data_spec + .swdir (the two the math needs)."""
    from .buoys import _fetch_text
    from ..config import NDBC_REALTIME2_BASE
    up = buoy_id.upper()
    ds = _fetch_text(f"{NDBC_REALTIME2_BASE}/{up}.data_spec", buoy_id, "data_spec", use_cache)
    sd = _fetch_text(f"{NDBC_REALTIME2_BASE}/{up}.swdir", buoy_id, "swdir", use_cache)
    if not ds or not sd:
        return {}
    return compute(ds, sd, cutoff_hz=cutoff_hz)


# --------------------------------------------------------------------------- #
# Offline selftest                                                             #
# --------------------------------------------------------------------------- #
_SAMPLE_DATA_SPEC = (
    "#YY  MM DD hh mm  Sep_Freq  Spectrum (m*m/Hz) and (frequency Hz)\n"
    # sep_freq 0.100 → bands < 0.10 Hz are swell. Two swell bins (0.05, 0.08) carry the
    # energy; two wind-sea bins (0.15, 0.20) small. Frequencies span the split.
    "2026 07 13 12 40  0.100  0.000 (0.050)  8.000 (0.080)  1.000 (0.150)  0.250 (0.200)\n"
)
_SAMPLE_SWDIR = (
    "#YY  MM DD hh mm  Direction (deg) and (frequency Hz)\n"
    # swell bins near 90°; a 999.0 missing-direction sentinel on the 0.05 bin (its energy is
    # 0 anyway); wind-sea bins near 250°. Also exercises the wraparound helper separately.
    "2026 07 13 12 40  999.0 (0.050)  90.0 (0.080)  250.0 (0.150)  260.0 (0.200)\n"
)


def _selftest():
    ok = True

    def check(msg, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("  PASS " if cond else "  FAIL ") + msg)

    # vector mean, incl. the wraparound the task calls out
    _wrap = vector_mean_dir([350.0, 10.0], [1.0, 1.0])
    check("vector mean: 350° & 10° (equal wt) → 0°, not 180°",
          _wrap is not None and (abs(_wrap) < 1e-6 or abs(_wrap - 360.0) < 1e-6))
    check("vector mean: energy weighting dominates (90°@10, 270°@1) → near 90°",
          abs(vector_mean_dir([90.0, 270.0], [10.0, 1.0]) - 90.0) < 1e-6)
    check("vector mean: all invalid → None", vector_mean_dir([999.0, None], [1.0, 1.0]) is None)
    check("vector mean: fully opposed equal weight → None",
          vector_mean_dir([0.0, 180.0], [1.0, 1.0]) is None)

    # parse
    ds = parse_data_spec(_SAMPLE_DATA_SPEC)
    sd = parse_swdir(_SAMPLE_SWDIR)
    (eh, rec), = ds.items()
    check("parse .data_spec: Sep_Freq extracted (0.100)", abs(rec["sep_freq"] - 0.100) < 1e-9)
    check("parse .data_spec: 4 freq/energy pairs, sorted",
          rec["freqs"] == [0.05, 0.08, 0.15, 0.20] and rec["c11"][1] == 8.0)
    check("parse .swdir: 999.0 kept as raw token (masked later), 4 bands",
          len(sd[eh]) == 4 and sd[eh][0.05] == 999.0)

    # metrics
    m = spectral_metrics(ds[eh], sd[eh])
    # swell band (<0.10): freqs 0.05,0.08. df central: [0.03,0.05,0.05,0.05] → energy 0.08 bin
    # = 8.0*0.05=0.4 (0.05 bin energy 0). Hs_swell = 4*sqrt(0.4) ≈ 2.53.
    check("metrics: Hs_swell = 4√(Σ C11·df) over swell band",
          abs(m["hs_swell"] - 4.0 * math.sqrt(8.0 * 0.05)) < 1e-6)
    check("metrics: swell_dir ignores the 999 bin → 90° (only the 0.08 bin has energy+dir)",
          abs(m["swell_dir"] - 90.0) < 1e-6)
    check("metrics: sep_freq split used NDBC's own 0.100", abs(m["sep_freq_used"] - 0.100) < 1e-9)
    check("metrics: total > swell (wind-sea adds energy) and swell_frac in (0,1]",
          m["hs_total"] > m["hs_swell"] and 0.0 < m["swell_frac"] <= 1.0)
    check("metrics: wind-sea dir ~ 250–260° (both wind-sea bins have dir+energy)",
          250.0 <= m["windsea_dir"] <= 260.0)

    # band split fallback when Sep_Freq missing
    rec2 = {"sep_freq": None, "freqs": [0.05, 0.15], "c11": [5.0, 5.0]}
    m2 = spectral_metrics(rec2, {0.05: 100.0, 0.15: 200.0})
    check("band split: missing Sep_Freq falls back to 0.10 Hz",
          abs(m2["sep_freq_used"] - SWELL_WINDSEA_CUTOFF_HZ) < 1e-9
          and abs(m2["swell_dir"] - 100.0) < 1e-6 and abs(m2["windsea_dir"] - 200.0) < 1e-6)

    # missing-data: an all-missing hour yields zero/None, not a crash
    m3 = spectral_metrics({"sep_freq": 0.1, "freqs": [0.05], "c11": [None]}, {})
    check("missing: all-MM energy → Hs 0, dirs None, no crash",
          m3["hs_total"] == 0 and m3["swell_dir"] is None and m3["swell_frac"] is None)

    # delta stats: circular, wrap-correct
    n, md, cs = delta_stats([5.0, 355.0], [0.0, 0.0])   # deltas +5, −5 → mean 0, std 5
    check("delta_stats: circular mean/std of (+5,−5) ≈ (0, 5)°",
          n == 2 and abs(md) < 1e-6 and abs(cs - 5.0) < 0.5)

    print("\nself-test:", "ALL PASS — spectral reader sound (offline)." if ok else "FAILURES")
    return 0 if ok else 1


def _print_station(buoy_id, use_cache):
    metrics = by_hour(buoy_id, use_cache=use_cache)
    if not metrics:
        print(f"⚠ no directional spectra for {buoy_id} (.data_spec/.swdir unavailable) — "
              "run on the Mac / this station may not report directional waves.")
        return 0
    print(f"=== NDBC {buoy_id} spectral swell (degree-valued; direction = FROM, °true) ===")
    print(f"  {'valid_hr':>10} {'sepHz':>6} {'Hs_tot':>6} {'Hs_sw':>6} {'sw_dir':>6} "
          f"{'sw_frac':>7} {'tot_dir':>7} {'ws_dir':>6}")
    for eh in sorted(metrics)[-24:]:
        m = metrics[eh]
        def _n(v, s="{:.0f}"):
            return s.format(v) if v is not None else "—"
        print(f"  {eh:>10} {m['sep_freq_used']:>6.3f} {m['hs_total']:>6.2f} {m['hs_swell']:>6.2f} "
              f"{_n(m['swell_dir']):>6} {_n(m['swell_frac'],'{:.2f}'):>7} {_n(m['total_mean_dir']):>7} "
              f"{_n(m['windsea_dir']):>6}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="offline checks (no network)")
    ap.add_argument("--buoy", default=None, help="print spectral swell per hour for a station (Mac)")
    ap.add_argument("--use-cache", action="store_true", help="use the cached spectral files if present")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.buoy:
        return _print_station(a.buoy, a.use_cache)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
