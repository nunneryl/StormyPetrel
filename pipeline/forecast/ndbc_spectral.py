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

BAND SPLIT (task 2): PRIMARY is NDBC's OWN per-record separation frequency (the Sep_Freq
column of .data_spec), but ONLY when it is a real frequency — 9.999 / 999 / 9999 / MM are
MISSING sentinels (station 44095 publishes 9.999), so a valid Sep_Freq must fall in
0.03–0.40 Hz. THAT PATH NEVER FIRES IN PRACTICE: every CDIP-mirrored station in our roster
publishes Sep_Freq as the 9.999 sentinel, so the WORKING split everywhere is the fallback.
The fallback is the fixed SWELL_WINDSEA_CUTOFF_HZ (0.125 Hz ≈ 8 s), and measured over 45
days at ten reference buoys it is the accurate one: worst-case mean|Δ| against the
published .spec SwH is 0.36 m, against 1.16 m for the wave-age split — roughly 3× better
at its worst. WAVE AGE IS AN EXPLICIT OPT-IN ONLY (prefer_wave_age=True), never the
default: at 46278 it read Hs_swell 1.46 m against .spec SwH 0.30 m, folding a 7–8 s wind
sea (WWH 1.5 m) into swell. It is kept because it is the physically-motivated criterion
and can capture a genuine sub-8 s swell a fixed cutoff cannot, but it over-assigns badly
enough that it has to be asked for.
KNOWN AND ACCEPTED — the fixed cutoff calls sub-8 s energy WIND SEA where NDBC's own
partitioner calls it swell (44095 reports SwH 0.8–1.0 m at SwP 4.5–5.6 s with STEEPNESS
VERY_STEEP). That is a CONVENTION DIFFERENCE, not an error: for a surf forecast 5 s waves
are chop, so ours is the appropriate convention and the cutoff is NOT changed to match.
ACCEPTANCE TEST: whatever split is used, Hs_swell should track NDBC's published .spec SwH
— the --diag prints mean|Δ| and flags the split SUSPECT if it doesn't, AFTER excluding
NDBC's null swell partitions (SwH 0.0 paired with a physically impossible SwP) and
labelling the convention difference above as such (nwps_trkng.band_split_verdict).

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

# ── Band split (wind-sea vs swell) ──────────────────────────────────────────
# PRIMARY: NDBC's OWN per-record separation frequency (the Sep_Freq column of
# .data_spec) — but ONLY when it is a real frequency. 9.999 / 999 / 9999 / MM are
# MISSING sentinels, not frequencies (the 44095 trap). A real Sep_Freq lives here:
_SEP_FREQ_MIN_HZ, _SEP_FREQ_MAX_HZ = 0.03, 0.40
# THE WORKING SPLIT — what actually runs, because no CDIP-mirrored station in our roster
# publishes a real Sep_Freq. A fixed cutoff: 0.125 Hz = 8 s, the common "swell = period
# > 8 s" threshold. Measured over 45 days at ten reference buoys its WORST-CASE mean|Δ|
# against the published .spec SwH is 0.36 m, against 1.16 m for wave age — about 3× better
# at its worst. Known limitation, deliberately accepted: it cannot capture a genuine sub-8 s
# swell, and it calls sub-8 s energy wind sea where NDBC's partitioner calls it swell
# (44095). For a surf forecast that is the RIGHT convention — 5 s waves are chop.
SWELL_WINDSEA_CUTOFF_HZ = 0.125
# OPT-IN ONLY (prefer_wave_age=True), never the default: a per-band WAVE-AGE split from the
# LOCAL WIND (Hanson & Phillips 2001; the wind-sea/swell criterion WW3's partitioning uses).
# A component is wind-sea while its phase speed is below 1.2·U projected on the wind; once
# it outruns the wind it is swell. Physically motivated and wind-adaptive, and it CAN catch
# the 7.5 s (0.133 Hz) swell a fixed cutoff misses — but it OVER-ASSIGNS to swell: at 46278
# it read Hs_swell 1.46 m against .spec SwH 0.30 m, folding a 7–8 s wind sea into swell.
WAVE_AGE_FACTOR = 1.2          # Hanson & Phillips (2001) wind-sea/swell wave-age threshold
_G = 9.80665                   # gravity (m/s²); deep-water phase speed c = g/(2πf)

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


def _valid_sep_freq(sep):
    """A published Sep_Freq is usable only inside a sane band — 9.999 / 999 / 9999 / MM
    (None) are MISSING sentinels, NOT frequencies (task 1, the 44095 trap)."""
    return sep is not None and math.isfinite(sep) and _SEP_FREQ_MIN_HZ <= sep <= _SEP_FREQ_MAX_HZ


def _wrap180(a):
    return ((a + 180.0) % 360.0) - 180.0


def classify_bands(freqs, dirs, *, sep_freq=None, wind_speed=None, wind_dir=None,
                   cutoff_hz=SWELL_WINDSEA_CUTOFF_HZ, prefer_wave_age=False):
    """Per-band wind-sea/swell split → (is_swell[bool per band], method, sep_repr_hz).

    Priority: (1) a VALID published Sep_Freq → scalar split f < sep. (2) else the FIXED
    cutoff, f < cutoff_hz. (3) WAVE AGE only when *prefer_wave_age* is set AND a wind is
    available — a band is wind-sea iff its deep-water phase speed c = g/(2πf) is below
    WAVE_AGE_FACTOR·U·cos(Δ), where Δ is the angle between the band's direction and the
    wind. Both alpha1 and NDBC WDIR are 'from' bearings, so the +180° propagation offset
    cancels and Δ = alpha1 − WDIR; a band opposing the wind (cosΔ ≤ 0) can't be
    wind-driven → swell.

    PATH (1) NEVER FIRES IN PRACTICE: every CDIP-mirrored station in our roster publishes
    Sep_Freq as the 9.999 MISSING sentinel, so the fixed cutoff is the working split
    everywhere and this ordering decides every real hour. WAVE AGE IS RETAINED ONLY AS AN
    EXPLICIT OPT-IN because it OVER-ASSIGNS to swell: over 45 days at ten reference buoys
    the fixed cutoff's worst-case mean|Δ| against .spec SwH is 0.36 m against wave age's
    1.16 m, and at 46278 wave age folded a 7–8 s wind sea into swell (Hs_swell 1.46 m vs
    SwH 0.30 m). Pure."""
    if _valid_sep_freq(sep_freq):
        return [f < sep_freq for f in freqs], "ndbc_sep_freq", sep_freq
    if prefer_wave_age and wind_speed is not None and math.isfinite(wind_speed) and wind_speed > 0:
        is_swell = []
        for f in freqs:
            if f <= 0:
                is_swell.append(True)
                continue
            c = _G / (2.0 * math.pi * f)                 # deep-water phase speed (m/s)
            a1 = dirs.get(f)
            if a1 is not None and 0.0 <= a1 < 360.0 and wind_dir is not None:
                cosd = math.cos(math.radians(_wrap180(a1 - wind_dir)))
            else:
                cosd = 1.0                               # direction unknown → treat as along-wind
            windsea = cosd > 0.0 and c < WAVE_AGE_FACTOR * wind_speed * cosd
            is_swell.append(not windsea)
        sep_repr = _G / (2.0 * math.pi * WAVE_AGE_FACTOR * wind_speed)   # omni f_sep, for display
        return is_swell, "wave_age", sep_repr
    return [f < cutoff_hz for f in freqs], "fixed_cutoff", cutoff_hz


def spectral_metrics(spec, dirs, *, wind_speed=None, wind_dir=None,
                     cutoff_hz=SWELL_WINDSEA_CUTOFF_HZ, prefer_wave_age=False):
    """Per-hour swell/wind-sea/total statistics from one hour's parsed spectrum + directions.
    *spec* = {"sep_freq", "freqs", "c11"}; *dirs* = {freq: alpha1_deg}; *wind_speed*/*wind_dir*
    = local NDBC wind, used ONLY for the opt-in wave-age split (*prefer_wave_age*); the
    default split is the fixed cutoff and ignores the wind entirely. Returns a dict:
      {hs_total, hs_swell, hs_windsea, swell_dir, windsea_dir, total_mean_dir,
       swell_frac, sep_freq_used, split_method, n_bands}
    Directions are energy-weighted circular means of alpha1 (degrees FROM, true North).
    Energy per band uses C11·df so it matches the Hs integral. Pure."""
    freqs = spec.get("freqs") or []
    c11 = spec.get("c11") or []
    is_swell, method, sep_repr = classify_bands(
        freqs, dirs, sep_freq=spec.get("sep_freq"),
        wind_speed=wind_speed, wind_dir=wind_dir, cutoff_hz=cutoff_hz,
        prefer_wave_age=prefer_wave_age)
    df = _bin_widths(freqs)

    tot_e, sw_e, ws_e = [], [], []              # per-bin energy (C11·df), by band
    tot_d, tot_w, sw_d, sw_w, ws_d, ws_w = [], [], [], [], [], []
    for f, e, d_f, swell in zip(freqs, c11, df, is_swell):
        if e is None or not math.isfinite(e) or e < 0 or e >= _ENERGY_MISSING:
            continue
        energy = e * d_f
        a1 = dirs.get(f)
        if a1 is not None and a1 >= _DIR_MISSING:
            a1 = None                            # 999.0 missing-direction sentinel
        tot_e.append(energy); tot_d.append(a1); tot_w.append(energy)
        if swell:
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
        "sep_freq_used": sep_repr, "split_method": method, "n_bands": len(freqs),
    }


def _iso_epoch_hour(iso):
    try:
        return int(datetime.datetime.fromisoformat(
            str(iso).replace("Z", "+00:00")).timestamp() // 3600)
    except (TypeError, ValueError):
        return None


def parse_std_wind(text):
    """{epoch_hour: (wind_speed_ms, wind_dir_deg)} from the .txt std feed — the local wind
    the wave-age split needs. Reuses the buoys realtime2 parser. None-safe; {} on failure."""
    if not text:
        return {}
    try:
        from .buoys import _parse_realtime2, _STD_FIELDS
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for o in _parse_realtime2(text, _STD_FIELDS):
        eh = _iso_epoch_hour(o.get("time"))
        if eh is None:
            continue
        out[eh] = (o.get("wind_speed_ms"), o.get("wind_dir_deg"))
    return out


def _resolve_wind(buoy_uw, model_uw):
    """(u, wd, source): prefer COMPLETE buoy wind, else COMPLETE model wind, else none.
    Wave-age needs BOTH speed and direction, so a partial reading counts as unusable —
    a wave-only station like 44095 (WSPD/WDIR = MM) falls through to the model wind."""
    bu, bwd = buoy_uw
    if bu is not None and bwd is not None:
        return bu, bwd, "buoy"
    if model_uw is not None:
        mu, mwd = model_uw
        if mu is not None and mwd is not None:
            return mu, mwd, "model"
    return None, None, "none"


def compute(data_spec_text, swdir_text, std_text=None, *, model_wind=None,
            cutoff_hz=SWELL_WINDSEA_CUTOFF_HZ, prefer_wave_age=False):
    """{epoch_hour: spectral_metrics} from raw .data_spec + .swdir (+ optional .txt std for
    BUOY wind, + optional *model_wind* {epoch_hour: (ws, wdir)} used when the buoy reports no
    wind — a wave-only station like 44095). Each result carries 'wind_used' = (u, wd, source);
    the wind is resolved on EVERY hour so the diag can always report which wind was available,
    even though only the opt-in wave-age split consumes it. Pure/offline."""
    spec_by_hour = parse_data_spec(data_spec_text)
    dir_by_hour = parse_swdir(swdir_text)
    buoy_wind = parse_std_wind(std_text)
    out = {}
    for eh, spec in spec_by_hour.items():
        u, wd, src = _resolve_wind(buoy_wind.get(eh, (None, None)), (model_wind or {}).get(eh))
        m = spectral_metrics(spec, dir_by_hour.get(eh, {}),
                             wind_speed=u, wind_dir=wd, cutoff_hz=cutoff_hz,
                             prefer_wave_age=prefer_wave_age)
        m["wind_used"] = (u, wd, src)
        out[eh] = m
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
def by_hour(buoy_id, *, model_wind=None, use_cache=False, cutoff_hz=SWELL_WINDSEA_CUTOFF_HZ,
            prefer_wave_age=False):
    """{epoch_hour: spectral_metrics} for a live station, or {} if the spectra are
    unavailable. Reuses pipeline.forecast.buoys._fetch_text (its cache + polite fetch) —
    no parallel fetch path. Fetches .data_spec + .swdir (the spectrum) and the .txt std
    feed (BUOY wind); *model_wind* {epoch_hour: (ws, wdir)} covers hours the buoy reports no
    wind (wave-only stations). The wind only DRIVES the split under *prefer_wave_age*."""
    from .buoys import _fetch_text
    from ..config import NDBC_REALTIME2_BASE
    up = buoy_id.upper()
    ds = _fetch_text(f"{NDBC_REALTIME2_BASE}/{up}.data_spec", buoy_id, "data_spec", use_cache)
    sd = _fetch_text(f"{NDBC_REALTIME2_BASE}/{up}.swdir", buoy_id, "swdir", use_cache)
    if not ds or not sd:
        return {}
    std = _fetch_text(f"{NDBC_REALTIME2_BASE}/{up}.txt", buoy_id, "std", use_cache)  # buoy wind
    return compute(ds, sd, std, model_wind=model_wind, cutoff_hz=cutoff_hz,
                   prefer_wave_age=prefer_wave_age)


def hours_since_last_obs(buoy_id, *, now_epoch_hour=None):
    """Hours since *buoy_id*'s NEWEST actual wave observation — the newest epoch-hour in by_hour(),
    fetched LIVE (by_hour does not use the cache), or None when the buoy publishes no realtime
    spectra at all (removed from the roster / 404 / offline). This is the ONE 'how long since this
    buoy last reported' implementation, shared by the buoy-liveness monitor
    (scripts/buoy_ready_monitor.py) and --find-buoy so the two notions of 'alive' cannot drift
    apart. *now_epoch_hour* is injectable for tests (else the current UTC epoch-hour)."""
    try:
        metrics = by_hour(buoy_id)
    except Exception:  # noqa: BLE001
        return None
    if not metrics:
        return None
    if now_epoch_hour is None:
        now_epoch_hour = int(datetime.datetime.now(datetime.timezone.utc).timestamp() // 3600)
    return max(0, now_epoch_hour - max(metrics.keys()))


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

    # task 1 — Sep_Freq sentinel masking (the 44095 trap: 9.999 is MISSING, not a cutoff)
    check("sep sentinel: 9.999 / 999 / None are NOT valid separation frequencies",
          not _valid_sep_freq(9.999) and not _valid_sep_freq(999.0) and not _valid_sep_freq(None))
    check("sep valid band: 0.08 accepted; 0.02 & 0.45 rejected as out-of-band",
          _valid_sep_freq(0.08) and not _valid_sep_freq(0.02) and not _valid_sep_freq(0.45))

    # PRIORITY — the fixed cutoff is preferred over wave age, even with a wind in hand
    fq = [0.08, 0.133, 0.20, 0.25]                 # 0.133 Hz = 7.5 s (44095's real swell)
    dirs_fq = {0.08: 90.0, 0.133: 90.0, 0.20: 90.0, 0.25: 90.0}
    isw_def, m_def, sep_def = classify_bands(fq, dirs_fq, sep_freq=9.999,
                                             wind_speed=8.0, wind_dir=90.0)
    check("priority: a 9.999 Sep_Freq + a WIND still gives fixed_cutoff, not wave_age",
          m_def == "fixed_cutoff" and abs(sep_def - SWELL_WINDSEA_CUTOFF_HZ) < 1e-12)
    check("priority: the 9.999 sentinel is rejected, never coerced into a 9.999 Hz cutoff",
          isw_def == [True, False, False, False])
    _, m_sep, s_sep = classify_bands(fq, dirs_fq, sep_freq=0.10, wind_speed=8.0, wind_dir=90.0,
                                     prefer_wave_age=True)
    check("priority: a VALID published Sep_Freq still outranks both",
          m_sep == "ndbc_sep_freq" and abs(s_sep - 0.10) < 1e-12)

    # wave age still WORKS — it is an explicit opt-in, not removed
    isw, method, _ = classify_bands(fq, dirs_fq, sep_freq=9.999, wind_speed=8.0, wind_dir=90.0,
                                    prefer_wave_age=True)
    check("opt-in: prefer_wave_age fires wave_age when Sep_Freq is the sentinel",
          method == "wave_age")
    check("opt-in: 7.5 s (0.133 Hz) swell → SWELL under 8 m/s wind", isw[1] is True)
    check("opt-in: 4 s (0.25 Hz) chop → WIND-SEA", isw[3] is False)
    check("the trade, stated: the fixed cutoff calls that same 7.5 s band wind sea "
          "(sub-8 s = chop by our convention)", isw_def[1] is False)
    isw_opp, _, _ = classify_bands([0.30], {0.30: 270.0}, wind_speed=12.0, wind_dir=90.0,
                                   prefer_wave_age=True)
    check("opt-in: a band opposing the wind (cosΔ≤0) is swell, not wind-sea", isw_opp[0] is True)

    # OVER-ASSIGNMENT (the 46278 defect): wave age puts MORE energy in swell than the cutoff
    spec = {"sep_freq": 9.999, "freqs": [0.10, 0.133, 0.20, 0.28], "c11": [2.0, 12.0, 1.0, 0.5]}
    dirs_sp = {0.10: 92.0, 0.133: 90.0, 0.20: 88.0, 0.28: 90.0}
    m2 = spectral_metrics(spec, dirs_sp, wind_speed=8.0, wind_dir=90.0, prefer_wave_age=True)
    m2f = spectral_metrics(spec, dirs_sp, wind_speed=8.0, wind_dir=90.0)
    check("opt-in: a 44095-like sea reads SWELL-DOMINATED under wave age (frac > 0.6)",
          m2["split_method"] == "wave_age" and m2["swell_frac"] is not None and m2["swell_frac"] > 0.6)
    check("opt-in: swell_dir ≈ 90° (the dominant 7.5 s system)", abs(m2["swell_dir"] - 90.0) < 3.0)
    check("default is the fixed cutoff, and it assigns LESS energy to swell than wave age",
          m2f["split_method"] == "fixed_cutoff" and m2f["hs_swell"] < m2["hs_swell"])

    # BUG-1 fix — a wave-only buoy (MM wind) uses MODEL node wind; buoy wind still wins
    ds_mm = "#h\n2026 07 14 15 00 9.999 1.0 (0.08) 8.0 (0.133) 1.0 (0.25)\n"
    sd_mm = "#h\n2026 07 14 15 00 90 (0.08) 90 (0.133) 90 (0.25)\n"
    ehk = _epoch_hour("2026", "07", "14", "15", "00")
    r_model = compute(ds_mm, sd_mm, std_text=None, model_wind={ehk: (8.0, 90.0)},
                      prefer_wave_age=True)
    check("no buoy wind → MODEL node wind drives the opt-in wave_age (not fixed_cutoff)",
          r_model[ehk]["split_method"] == "wave_age" and r_model[ehk]["wind_used"][2] == "model")
    r_default = compute(ds_mm, sd_mm, std_text=None, model_wind={ehk: (8.0, 90.0)})
    check("same wind, DEFAULT call → fixed_cutoff; the wind is still reported as available",
          r_default[ehk]["split_method"] == "fixed_cutoff"
          and r_default[ehk]["wind_used"] == (8.0, 90.0, "model"))
    r_none = compute(ds_mm, sd_mm, std_text=None, model_wind=None)
    check("no wind anywhere → fixed_cutoff, source 'none'",
          r_none[ehk]["split_method"] == "fixed_cutoff" and r_none[ehk]["wind_used"][2] == "none")
    # buoy wind present (at :50, different minute offset) → BUOY wins over model, hours match
    std_buoy = "#YY MM DD hh mm WDIR WSPD\n#deg m/s\n2026 07 14 15 50 80 9.0\n"
    r_buoy = compute(ds_mm, sd_mm, std_text=std_buoy, model_wind={ehk: (8.0, 90.0)})
    check("buoy wind (:50) matches spectral (:00) by hour AND wins over model",
          r_buoy[ehk]["wind_used"][2] == "buoy" and abs(r_buoy[ehk]["wind_used"][0] - 9.0) < 1e-9)

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
