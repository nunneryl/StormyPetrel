"""Fixture checks for the NDBC directional-spectrum reader (pipeline.forecast.ndbc_spectral).

Covers the four behaviours the task calls out, with no network:
  * vector-mean correctness, incl. the wraparound (350° & 10° → 0°, not 180°);
  * band-split boundary (f < Sep_Freq is swell, f == Sep_Freq is wind-sea);
  * missing-data / MM / 999-direction handling (no leak, no crash);
  * a fixture-based parse of the realtime2 .data_spec + .swdir row layout.

Run: python -m pipeline.tests.test_ndbc_spectral   (or pytest)
"""
from __future__ import annotations

import math

from pipeline.forecast import ndbc_spectral as sp

# realtime2 sample rows (one timestamp). .data_spec: date, Sep_Freq, then energy (freq)
# pairs. .swdir: date, then direction (freq) pairs on the SAME frequency grid.
_DATA_SPEC = (
    "#YY  MM DD hh mm  Sep_Freq  Spectrum (m*m/Hz)\n"
    "2026 07 13 12 40  0.100  0.500 (0.060)  9.000 (0.090)  2.000 (0.120)  1.000 (0.160)\n"
)
_SWDIR = (
    "#YY  MM DD hh mm  Direction (deg)\n"
    "2026 07 13 12 40  120.0 (0.060)  118.0 (0.090)  250.0 (0.120)  255.0 (0.160)\n"
)


def test_vector_mean_wraparound_and_weighting():
    assert abs(sp.vector_mean_dir([350.0, 10.0], [1.0, 1.0]) % 360.0) < 1e-6, "350&10 → 0, not 180"
    assert abs(sp.vector_mean_dir([10.0, 20.0], [1.0, 1.0]) - 15.0) < 1e-6
    # energy weighting, not arithmetic: a 10× heavier 90° pulls the mean off 270°
    assert abs(sp.vector_mean_dir([90.0, 270.0], [10.0, 1.0]) - 90.0) < 1e-6
    # fully opposed / no valid data → undefined (None), never a spurious 90°
    assert sp.vector_mean_dir([0.0, 180.0], [1.0, 1.0]) is None
    assert sp.vector_mean_dir([999.0, None], [1.0, 1.0]) is None


def test_parse_realtime2_layout():
    ds = sp.parse_data_spec(_DATA_SPEC)
    sd = sp.parse_swdir(_SWDIR)
    (eh, rec), = ds.items()
    assert abs(rec["sep_freq"] - 0.100) < 1e-9, "Sep_Freq is the lone 6th column"
    assert rec["freqs"] == [0.06, 0.09, 0.12, 0.16], "energy(freq) pairs, sorted by freq"
    assert rec["c11"] == [0.5, 9.0, 2.0, 1.0]
    assert sd[eh] == {0.06: 120.0, 0.09: 118.0, 0.12: 250.0, 0.16: 255.0}


def test_band_split_uses_ndbc_sep_freq_and_boundary_is_windsea():
    # a VALID sep_freq (0.10 ∈ 0.03–0.40): 0.06 & 0.09 swell; a bin AT 0.10 and 0.12 wind-sea.
    spec = {"sep_freq": 0.10, "freqs": [0.06, 0.09, 0.10, 0.12], "c11": [1.0, 1.0, 1.0, 1.0]}
    dirs = {0.06: 100.0, 0.09: 100.0, 0.10: 260.0, 0.12: 260.0}
    m = sp.spectral_metrics(spec, dirs)
    assert m["split_method"] == "ndbc_sep_freq" and abs(m["sep_freq_used"] - 0.10) < 1e-9
    assert abs(m["swell_dir"] - 100.0) < 1e-6, "only f<0.10 in swell"
    assert abs(m["windsea_dir"] - 260.0) < 1e-6, "the f==Sep_Freq bin is wind-sea, not swell"


def test_sep_freq_sentinel_is_rejected_not_coerced():
    # the 44095 trap: Sep_Freq = 9.999 is a MISSING sentinel, never a 9.999 Hz cutoff.
    assert not sp._valid_sep_freq(9.999) and not sp._valid_sep_freq(999.0)
    assert not sp._valid_sep_freq(None) and not sp._valid_sep_freq(0.0)
    assert sp._valid_sep_freq(0.08) and not sp._valid_sep_freq(0.45), "valid band 0.03–0.40 Hz"
    # with 9.999 + wind present, the split must be wave-age, NOT a coerced 9.999 cutoff
    _, method, sep = sp.classify_bands([0.08, 0.15], {}, sep_freq=9.999, wind_speed=8.0, wind_dir=90.0)
    assert method == "wave_age" and sep < 1.0


def test_wave_age_split_captures_short_period_swell_and_is_direction_aware():
    # 0.133 Hz = 7.5 s: the real 44095 swell that a 0.10/0.125 Hz cutoff misclassifies.
    fq = [0.08, 0.133, 0.20, 0.25]
    isw, method, _ = sp.classify_bands(fq, {0.133: 90.0, 0.20: 90.0, 0.25: 90.0},
                                       sep_freq=None, wind_speed=8.0, wind_dir=90.0)
    assert method == "wave_age"
    assert isw[1] is True, "7.5 s swell (c=11.7 m/s) has outrun an 8 m/s wind → swell"
    assert isw[3] is False, "4 s chop is slower than 1.2·U → wind-sea"
    # a FIXED cutoff (no wind) gets the 7.5 s swell wrong — this is why wave-age is primary
    isw_fixed, mfix, _ = sp.classify_bands(fq, {}, sep_freq=None, wind_speed=None)
    assert mfix == "fixed_cutoff" and isw_fixed[1] is False
    # direction-aware: a fast-enough band OPPOSING the wind is swell (wind can't drive it)
    isw_opp, _, _ = sp.classify_bands([0.28], {0.28: 270.0}, wind_speed=12.0, wind_dir=90.0)
    assert isw_opp[0] is True


def test_wave_age_restores_swell_dominated_label():
    # a two-peak 44095-like sea (dominant 7.5 s swell + wind chop) must read SWELL-DOMINATED
    spec = {"sep_freq": 9.999, "freqs": [0.10, 0.133, 0.20, 0.28], "c11": [2.0, 12.0, 1.0, 0.5]}
    dirs = {0.10: 92.0, 0.133: 90.0, 0.20: 88.0, 0.28: 90.0}
    m = sp.spectral_metrics(spec, dirs, wind_speed=8.0, wind_dir=90.0)
    assert m["split_method"] == "wave_age"
    assert m["swell_frac"] is not None and m["swell_frac"] > 0.6, "not wind-sea-dominated"
    assert m["hs_swell"] > m["hs_windsea"]


def test_hs_swell_matches_energy_integral_and_frac():
    ds = sp.parse_data_spec(_DATA_SPEC)
    sd = sp.parse_swdir(_SWDIR)
    (eh, _), = ds.items()
    m = sp.spectral_metrics(ds[eh], sd[eh])
    # swell band (<0.10): freqs 0.06, 0.09. df: [0.03, 0.045, 0.035, 0.04] (central diffs).
    df = sp._bin_widths([0.06, 0.09, 0.12, 0.16])
    e_swell = 0.5 * df[0] + 9.0 * df[1]
    assert abs(m["hs_swell"] - 4.0 * math.sqrt(e_swell)) < 1e-9
    assert m["hs_total"] > m["hs_swell"] and 0.0 < m["swell_frac"] <= 1.0
    # swell direction is energy-weighted toward the dominant 0.09 bin (118°), near 118–120
    assert 117.0 <= m["swell_dir"] <= 121.0
    # total mean direction (the dirpw partner) mixes swell + wind-sea by energy
    assert m["total_mean_dir"] is not None


def test_missing_data_and_mm_handling():
    # an energy value of "MM" (→ None) and a 999.0 direction sentinel are both dropped
    ds = sp.parse_data_spec(
        "#h\n2026 07 13 12 40  0.100  MM (0.060)  4.000 (0.090)  0.000 (0.120)\n")
    sd = sp.parse_swdir("#h\n2026 07 13 12 40  999.0 (0.060)  95.0 (0.090)  999.0 (0.120)\n")
    (eh, _), = ds.items()
    m = sp.spectral_metrics(ds[eh], sd[eh])
    assert m["swell_dir"] is not None and abs(m["swell_dir"] - 95.0) < 1e-6, "only the valid 0.09 bin"
    # an entirely-missing hour is zero/None, never a crash or a fabricated value
    m0 = sp.spectral_metrics({"sep_freq": 0.1, "freqs": [0.06, 0.12], "c11": [None, None]}, {})
    assert m0["hs_total"] == 0 and m0["swell_dir"] is None and m0["swell_frac"] is None


def test_model_wind_fallback_for_wave_only_buoy():
    # 44095 reports MM wind → wave-age must use the MODEL node wind, not fall to fixed_cutoff.
    ds = "#h\n2026 07 14 15 00 9.999 1.0 (0.08) 8.0 (0.133) 1.0 (0.25)\n"
    sd = "#h\n2026 07 14 15 00 90 (0.08) 90 (0.133) 90 (0.25)\n"
    ehk = sp._epoch_hour("2026", "07", "14", "15", "00")
    # no buoy wind (std_text None), model wind supplied → wave_age via model
    r = sp.compute(ds, sd, std_text=None, model_wind={ehk: (8.0, 90.0)})
    assert r[ehk]["split_method"] == "wave_age"
    assert r[ehk]["wind_used"] == (8.0, 90.0, "model")
    assert r[ehk]["swell_frac"] > 0.6, "with model wind, the 7.5 s sea reads swell-dominated"
    # neither buoy nor model wind → fixed_cutoff, source 'none' (honest last resort)
    r0 = sp.compute(ds, sd, std_text=None, model_wind=None)
    assert r0[ehk]["split_method"] == "fixed_cutoff" and r0[ehk]["wind_used"][2] == "none"
    # buoy wind at :50 matches spectral at :00 (hour floor) AND takes priority over model
    std_buoy = "#YY MM DD hh mm WDIR WSPD\n#deg m/s\n2026 07 14 15 50 80 9.0\n"
    rb = sp.compute(ds, sd, std_text=std_buoy, model_wind={ehk: (8.0, 90.0)})
    assert rb[ehk]["wind_used"] == (9.0, 80.0, "buoy"), "buoy wins; :50/:00 minute offset matches"


def test_delta_stats_circular():
    # signed deltas +5 and −5 → circular mean ~0, std ~5 (not an arithmetic 0/large)
    n, mean, std = sp.delta_stats([5.0, 355.0], [0.0, 0.0])
    assert n == 2 and abs(mean) < 1e-6 and abs(std - 5.0) < 0.5
    # wrap-correct: model 5° vs buoy 355° is a +10° delta, not −350°
    n2, mean2, _ = sp.delta_stats([5.0], [355.0])
    assert n2 == 1 and abs(mean2 - 10.0) < 1e-6


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} NDBC-spectral checks passed")


if __name__ == "__main__":
    _run_all()
