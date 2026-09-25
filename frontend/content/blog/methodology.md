---
title: 'How our forecasts work'
description: 'Where our data comes from and how the star rating is built, in plain terms.'
date: '2026-04-29'
author: 'Stormy Petrel'
tag: 'methodology'
---

*Last updated: 25 September 2026.*

## TL;DR

Every spot gets a star rating for each forecast hour, from 0 (FLAT) to 5 (EPIC). It takes three steps:

1. **Size.** The wave height, adjusted for swell direction, gives a size score.
2. **Quality.** Four scores adjust the size score: wind, chop, period and tide. They are blended with weights, not simply multiplied together.
3. **Rounding.** The result is rounded to the nearest half star and kept between 1 and 5. A height under half a foot, after the direction adjustment, is FLAT: 0 stars.

```
stars = size_score × wind^0.35 × chop^0.25 × period^0.25 × tide^0.15
```

## How the stars are built

### Size

The size score comes from the wave height after the swell-direction adjustment (see the swell window, below).

| Height | Size score |
|--------|-----------|
| 1 ft | 1.0 |
| 2 ft | 2.0 |
| 3 ft | 2.5 |
| 4 ft | 3.0 |
| 5 ft | 3.5 |
| 6 ft | 4.0 |
| 8 ft | 4.5 |
| 10 ft or more | 5.0 |

Between rows, the score moves in a straight line. Under half a foot, the spot is FLAT whatever else is going on.

### Quality

Four scores adjust the size score. Each is close to 1 when that part of the conditions is good, and lower when it isn't. Wind and period can go a little above 1.

The four are blended with these weights, which add up to 1:

| Quality score | Weight |
|---------------|--------|
| Wind | 0.35 |
| Chop | 0.25 |
| Period | 0.25 |
| Tide | 0.15 |

Technically it's a weighted geometric mean: each score is raised to the power of its weight, and the results are multiplied. In practice:

- If all four scores are 1, you get exactly the size score, before rounding.
- Wind moves the rating most, and tide least.
- A few middling scores don't pile up the way they would if you multiplied them straight.

**Example.** Say a swell comes straight into the spot at 4 ft, which scores 3.0 for size. The wind is offshore (1.20), there's some chop (0.85), the period is 10 s (0.85) and the tide is wrong for the spot (0.70). Weighted, those four come to 0.93. So the rating is 3.0 × 0.93 = 2.8, which rounds to 3 stars. Multiplied straight, the same scores would give 1.8.

## Where the data comes from

| What | Source | Notes |
|------|--------|-------|
| Wave height | NWPS, NOAA's Nearshore Wave Prediction System | Most spots. CDIP MOP at 48 California spots, for the hours around now. WAVEWATCH III fills the rest. |
| Swell direction and period | WAVEWATCH III (gfswave) | Up to three swells plus local wind sea, in 3-hour steps. CDIP MOP at the MOP spots, for its hours. |
| Wind | HRRR, a 3 km model of the continental US | Hourly steps for the first 48 hours of each run, then NWPS wind. NWPS wind throughout in Hawaii and Puerto Rico. |
| Tide | NOAA CO-OPS predictions | Hourly where the station publishes them, otherwise built from the high and low times. |
| Buoys | NDBC | The latest reading is shown on each spot page. Buoys also check NWPS before we use it at a spot, and at some spots and hours they stand in for swell direction and period. |
| Height calibration | CDIP MOP | 130 California spots, measured over two weeks. |

## Where the wave height comes from

The model behind the height depends on the spot and the hour:

- **Most spots: NWPS.** NOAA runs NWPS separately for each coastal forecast office, on a much finer grid than the global models. We read a grid point just offshore of the spot, for each hour the latest run covers.
- **48 California spots: CDIP MOP, for the hours around now.** MOP is the Coastal Data Information Program's model of points along the California coast, just outside the surf zone.
- **Any other hour: WAVEWATCH III.** The WW3 swells are combined as described below. If WW3 has nothing usable for that hour either, we fall back to NWPS's own height.

**Calibration at 130 California spots.** For these spots we compared our height with MOP's over two weeks, 18 August to 1 September 2026, and now divide our height by each spot's typical ratio. The star rating is then recomputed from the calibrated height.

**What the number is.** The height we publish is labelled nearshore swell height: the significant height of the waves just outside the surf zone, roughly the average of the bigger waves. It is not the face of a breaking wave, which is often bigger. The label holds at the 48 MOP spots for MOP's hours, and the 130 calibrated California spots are scaled so their typical height matches MOP's. Everywhere else, the model height gets a boost for longer-period swell and isn't corrected, so it can read higher than nearshore swell height.

## Swell direction and period

WAVEWATCH III splits the sea at each grid point into up to three swells, each with its own height, period and direction, plus a separate local wind sea. We read it in 3-hour steps, and each forecast hour uses the nearest step.

When the height comes from WW3, the swells are combined as energy rather than added up:

```
combined height = √(sum over swells of height² × direction gain)
```

The local wind sea is left out, and a swell 90° or more outside the spot's window adds nothing.

The period and direction we show come from one swell, the dominant one. We pick it by height² × direction gain × period score², so long-period swell counts extra. From the same direction, a 2 ft 14 s swell beats a 3 ft 7 s swell: 2² × 1.02² ≈ 4.1 against 3² × 0.60² ≈ 3.2.

Other sources step in when WW3 can't:

- At the MOP spots, for MOP's hours, direction and period come from MOP.
- If WW3 has no usable swell for an hour, we use NWPS's own direction and period.
- At some spots and hours, the nearest buoy's latest reading stands in when the models give no swell direction. It's a snapshot: the same reading is used for each hour of the forecast.

## The swell window

Every spot has a swell window: the range of directions it can get swell from, given the headlands and seabed around it. The first version of the rater scored any swell outside the window as zero. A refracted NW swell wrapping into a south-facing point like Steamer Lane counted for nothing, even when the wave model said the energy was there.

Now each swell gets a direction gain:

| Swell direction | Direction gain |
|-----------------|----------------|
| Inside the window | cos²(offset ÷ 2), never below 0.25 |
| Less than 45° outside | 0.40 |
| 45° to 90° outside | 0.15 |
| 90° or more outside | 0 |

Inside the window, the offset is the angle between the swell and the spot's best swell direction. A swell straight down that line gets 1.00, and one 60° off it gets 0.75. Outside the window, the angle is measured from the window's edge. Swell from outside the window isn't impossible: it wraps in at reduced strength.

## Wind

The first version of Stormy Petrel used the wind that comes with the NWPS wave forecast. Users told us it said offshore when it was clearly onshore. HRRR is a 3 km model with hourly steps, and it picks up sea breezes and terrain-driven offshore winds much better.

We use HRRR for the first 48 hours of each run, and NWPS wind after that. HRRR only covers the continental US, so Hawaii and Puerto Rico use NWPS wind throughout. Each spot takes its nearest HRRR grid point.

The wind score depends on the wind's angle to the spot's offshore direction: offshore scores best, onshore worst. Light wind matters less, strong offshore wind loses its bonus, and very strong wind costs extra.

## Period

Long-period swell usually makes better surf than short-period wind swell of the same height. The period score:

| Period | Score |
|--------|-------|
| 6 s or less | 0.50 |
| 7 s | 0.60 |
| 8 s | 0.70 |
| 9 s | 0.80 |
| 10 s | 0.85 |
| 11 s | 0.90 |
| 12 s | 0.95 |
| 13 s | 1.00 |
| 16 s or more | 1.05 |

Between rows it's a straight line, so 14 s gives about 1.02.

## Chop

When local wind sea is a big share of the wave height, the lineup is bumpy even if the swell lines up well. We measure that share:

```
chop_ratio = (total_hs - swell_hs) / total_hs
```

The chop score is a straight line between these points, not a step, so a chop_ratio of 0.3 gives 0.9250, not 0.85:

| chop_ratio | Score |
|-----------|-------|
| 0.0 | 1.00 |
| 0.2 | 1.00 |
| 0.4 | 0.85 |
| 0.6 | 0.65 |
| 0.8 | 0.45 |
| 1.0 | 0.30 |

`chop_ratio` is left empty when the inputs can't support a ratio: no total height, no swell height, or a swell height above the total. An unknown scores the neutral 1.00 rather than being treated as zero.

Heavy chop also takes away the offshore-wind bonus. When more than 40% of the height is wind sea, a wind score that would be above neutral is held to 0.80.

## Tide

The tide score compares the tide at that hour with the tide the spot prefers: low, mid, high, or a range in between. It's full marks when they match and lower when they don't. Spots with no known preference, or no tide data, score neutral. For most spots the preference is still an estimate; see below.

## Surface conditions

The **Clean / Mixed / Choppy / Blown out** word on each spot page is separate from the chop score above. It comes from the **wind**, not from `chop_ratio`.

That's deliberate. "Blown out" is a wind condition: the Encyclopedia of Surfing defines it as an "ocean surface condition created by a moderate-to-strong onshore wind, which, by degrees, produces chopped-up, crumbly, messy surf." `chop_ratio` describes the water offshore, which is a different thing. When we measured it in August 2026, across 84,774 spot-hours, a label based on `chop_ratio` barely tracked the wind: 57.3% of offshore-wind hours were labelled "Blown out".

The label now comes from the wind's angle off the spot's straight-offshore direction (`off_angle`, 0–180°) and its speed:

| Condition | Label |
|-----------|-------|
| wind under 2.5 m/s | Clean (glassy — direction stops mattering) |
| off_angle ≤ 60° | Clean (offshore) |
| off_angle ≤ 120° | Mixed (cross-shore) |
| off_angle > 120°, wind under 6 m/s | Choppy (light onshore) |
| off_angle > 120°, wind 6 m/s or more | Blown out |

Speed is checked first: a 1 m/s straight-onshore hour is glassy, not choppy. On the same August 2026 hours, this gives Clean 43.5%, Mixed 23.8%, Choppy 28.3% and Blown out 4.4%.

`chop_ratio` still appears on the spot page, as the "swell mix" figure under the word.

## What we're still working on

1. **Local geology.** A reef pass that focuses energy, or a sandbar that moves with the seasons, isn't in any model. The calibration at 130 California spots corrects each one's typical height; other spots have no local height correction.
2. **Crowds aren't modelled.** Two spots with the same conditions can be very different sessions.
3. **Tide preference.** For most spots, the preferred tide is an unverified estimate. We're replacing these with researched values.
4. **Height calibration drifts.** Each spot's factor comes from a single two-week window, and it moves with the swell mix: between two windows, about a third of spots shifted by more than 10%.
5. **Uncalibrated heights can run high.** Outside the 130 calibrated California spots, and apart from the hours that come straight from MOP, heights aren't corrected. They include the period boost and can read higher than the nearshore swell height they're labelled as. At the California spots where we measured it, the uncorrected height was typically about 1.5 times MOP's.
6. **Refresh delay.** Forecasts are {{FORECAST_UPDATES}}, not continuously. If a swell builds faster than that, the rating lags until the next update. Buoy readings arrive {{BUOY_UPDATES}}, and we show the latest one next to each spot.

The code is on [GitHub](https://github.com/nunneryl/StormyPetrel). The rating is in [`pipeline/interpret.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/interpret.py), and the NWPS, MOP and calibration steps are in [`nwps_nearshore.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/forecast/nwps_nearshore.py), [`mop.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/forecast/mop.py) and [`face_correction.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/forecast/face_correction.py). File an issue if you spot a bug, or send a PR if you want to fix one.
