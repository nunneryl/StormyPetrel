---
title: 'How our forecasts work'
description: 'The four-source data pipeline + the rating formula, explained without the jargon.'
date: '2026-04-29'
author: 'Stormy Petrel'
tag: 'methodology'
---

## TL;DR

Every spot gets a star rating from 0 (FLAT) to 5 (EPIC). The rating combines five components multiplied together:

```
stars = size_score × dir_gain × wind_mult × tide_mult × chop_mult × period_quality
```

Each input pulls from the appropriate model:

| Component | Source | What it measures |
|-----------|--------|------------------|
| Wave height | NWPS (NOAA Nearshore Wave Prediction System) | nearshore-shoaled significant wave height |
| Swell direction & period | WAVEWATCH III (gfswave) | 3 spectral swell partitions per grid cell per hour |
| Wind | HRRR (3 km CONUS) | hourly 10 m wind, refined sea-breeze cycles |
| Buoy validation | NDBC | realtime "what's actually breaking right now" |
| Tide | NOAA CO-OPS | hilo + hourly water level predictions |

For non-CONUS spots (Hawaii, Puerto Rico, Alaska), HRRR isn't available — wind falls back to NWPS. Everything else is the same.

## Why three swell partitions?

Real ocean isn't a single sine wave. At any moment a beach might be receiving:

- A long-period North Pacific groundswell (15s, NW)
- A short-period local wind sea (6s, ENE trades)
- A residual South Pacific groundswell (12s, SSW)

If you average those into a single "dominant direction," the answer is meaningless — the actual wave at the spot is the *combination* of all three. WAVEWATCH III publishes each one separately. We combine them in quadrature (energy adds, not heights), weighted by directional gain against the spot's window:

```
combined_hs = sqrt(sum(p_hs² × p_gain) for each partition that's in the window)
```

The dominant partition (highest gain-weighted energy) drives the period and direction we display. This is why a 0.4 ft 15s long-period SSW swell can outrate a 2 ft 6s wind sea on a south-facing spot — the long-period component is in window, the wind sea is texture.

## Why HRRR for wind?

The first version of Stormy Petrel used NWPS's bundled GFS-derived wind. Multiple users reported "you say it's offshore but I'm out here and it's clearly onshore." The reason: GFS-derived wind is on a ~6 km nearshore grid and only updates every 6 hours, which smooths out the diurnal sea-breeze cycle that makes or breaks dawn-patrol surf in California / Florida / etc.

HRRR is a 3 km hourly atmospheric model. It resolves coastal sea breezes, topographic offshores, and onshore inflow at scales that match how a single spot actually feels. We run it through a KDTree lookup against the spot's lat/lng so each spot gets the cell that's actually over its lineup, not the cell over the headland 5 km away.

## Refraction and the "soft window"

Every spot has a "swell window" — a range of bearings the spot can receive swell from given the surrounding headlands and bathymetry. A south-facing point at the bottom of a deep bay only sees swells from a narrow southern arc.

The first version of the rater treated this as a hard zero: any swell direction outside the window scored 0, meaning a refracted NW swell that wraps into a south-facing point (Steamer Lane, Sebastian Inlet) was flagged as zero even though the wave model said the energy was there.

The current version uses a graduated penalty:
- **Inside window** — `cos²(offset_from_optimal)` with a 0.25 floor
- **<45° outside window** — 0.40 (refracted, real but reduced)
- **45–90° outside** — 0.15 (heavily refracted, fringe)
- **>90° outside** — 0 (physically blocked)

This matches the physics: the wave model accounts for refraction at its grid resolution, and "outside the window" doesn't mean "no swell" — it means "swell wraps in via geometry."

## Period quality

Long-period swells refract more cleanly, shoal harder, and break with more push than short-period chop at the same height. A 3 ft 14s on-axis swell is a real session; a 3 ft 6s on-axis wind sea is mush. The rating multiplies by a period_quality factor:

| Period | Multiplier |
|--------|-----------|
| 6s     | 0.50 |
| 8s     | 0.70 |
| 10s    | 0.85 |
| 12s    | 0.95 |
| 14s    | 1.00 |
| 16s+   | 1.05 |

## Chop penalty

When wind sea is comparable to swell height, the lineup is textured even on-axis. We compute the **wind-sea height fraction** — how much of the total wave height is short-period local sea rather than swell:

```
chop_ratio = (total_hs - swell_hs) / total_hs
```

That fraction drives a rating multiplier. The multiplier is a **piecewise-linear curve through these knots, not a step function** — a chop_ratio between two knots is interpolated, so 0.3 gives 0.9250, not 0.85:

| chop_ratio | Multiplier |
|-----------|-----------|
| 0.0 | 1.00 |
| 0.2 | 1.00 |
| 0.4 | 0.85 |
| 0.6 | 0.65 |
| 0.8 | 0.45 |
| 1.0 | 0.30 |

`chop_ratio` is NULL when the inputs can't support a ratio (no total height, no swell height, or a swell height above the total). An unknown scores the neutral 1.00 rather than being treated as zero.

## Surface conditions

The **Clean / Mixed / Choppy / Blown out** label on each spot page is a separate thing from the chop penalty above, and it is derived from **wind**, not from `chop_ratio`.

That is a deliberate correction. "Blown out" is a wind condition — the Encyclopedia of Surfing defines it as an "ocean surface condition created by a moderate-to-strong onshore wind, which, by degrees, produces chopped-up, crumbly, messy surf." `chop_ratio` is a height fraction describing water offshore, which is a different quantity. Measured across 84,774 spot-hours, labelling from `chop_ratio` produced words that were essentially independent of wind: "Blown out" fired 57–64% of the time in *every* wind-direction band, and "Clean" fired more often onshore (5.9%) than offshore (1.9%). 57.3% of offshore-wind hours were labelled "Blown out" — next to a wind readout saying "offshore".

The label now comes from the wind's angle off the spot's directly-offshore bearing (`off_angle`, wrapped to 0–180°) and its speed:

| Condition | Label |
|-----------|-------|
| wind under 2.5 m/s | Clean (glassy — direction stops mattering) |
| off_angle ≤ 60° | Clean (offshore) |
| off_angle ≤ 120° | Mixed (cross-shore) |
| off_angle > 120°, wind under 6 m/s | Choppy (light onshore) |
| off_angle > 120°, wind 6 m/s or more | Blown out |

Speed is checked before direction: a 1 m/s straight-onshore dawn is glassy, not choppy. Across the same 84,774 spot-hours this gives Clean 43.5%, Mixed 23.8%, Choppy 28.3%, Blown out 4.4%, and the average wind multiplier falls monotonically across the four bands (0.93 → 0.83 → 0.66 → 0.58).

`chop_ratio` is still shown on the spot page, as the "swell mix" figure beneath the conditions word — it is a real statistic about what the swell is made of, just not the thing that decides whether the surface is blown out.

## Where we're still wrong

The rating engine is honest about its blind spots:

1. **Local geology.** A reef pass that focuses energy or a sand bar that seasonally shifts isn't in any model. Spot-specific hand-tuning would help; it's on the roadmap.
2. **Crowd factor isn't modeled.** Two spots can have identical model conditions but very different "is it worth driving to" answers.
3. **The 6-hour refresh latency.** Forecasts update every 6 hours; if a swell builds faster than that, the rating lags reality by up to 6h. Buoy observations help here — they refresh hourly and we display the latest reading next to each spot.

For all of this, the source code is on [GitHub](https://github.com/nunneryl/StormyPetrel) — read the rating engine in [`pipeline/interpret.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/interpret.py), file an issue if you spot a bug, send a PR if you want to fix one.
