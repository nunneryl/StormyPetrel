---
title: 'How our forecasts work'
description: 'Where our data comes from and how the star rating is built, in plain terms.'
date: '2026-04-29'
author: 'Stormy Petrel'
tag: 'methodology'
---

*Last updated: 21 October 2026.*

## TL;DR

Every spot gets a star rating for each forecast hour, from 0 (FLAT) to 5 (EPIC). It takes four steps:

1. **Size.** The wave height, adjusted for swell direction, gives a size score. At sand beach and jetty breaks it falls again once the waves are big enough to close out.
2. **Quality.** Four scores adjust the size score: wind, chop, period and tide. They are blended with weights, not simply multiplied together.
3. **Rounding.** The result is rounded to the nearest half star and kept between 1 and 5. A height under half a foot, after the direction adjustment, is FLAT: 0 stars.
4. **Strong wind.** Strong wind caps the rating, however good the rest is: cross-shore or onshore from 13 kt, offshore from 20 kt.

```
stars = size_score × wind^0.35 × chop^0.25 × period^0.25 × tide^0.15
        rounded, then capped by the wind
```

## How the stars are built

### Size

The size score comes from the wave height after the swell-direction adjustment (see the swell window, below). At the 141 calibrated California spots it reads the calibrated height times 1.59, the median of those spots' ratios between our model height and MOP's (see Calibration, below). That keeps them on the same scale as everywhere else: a spot whose model height runs 1.59 times MOP's rates exactly as it would on the model's height, one that runs higher, like Steamer Lane at 3.11, rates lower, and one that runs lower, like Pacifica Linda Mar at 0.88, rates higher. At every other spot the size score reads the model's height: there's nothing to calibrate it against.

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

### Big beach breaks

A beach or jetty break over sand doesn't keep getting better as the swell grows. Past about 8 ft the sandbars can't hold the waves, and they close out. So at these spots the size score stops rising at 8 ft and falls past 10 ft:

| Height | Size score at a sand beach or jetty break |
|--------|-----------|
| up to 8 ft | as above |
| 8 to 10 ft | 4.5 |
| 12 ft | 3.5 |
| 15 ft | 2.5 |
| 20 ft or more | 1.5 |

These numbers are our judgement, checked against storm days like the one under Wind; no study we know of pins them down.

Closing out depends on how big the waves really are, so at the 141 calibrated California spots we judge it at the calibrated height itself. The size score, read as described under Size, keeps the same share of its value that the table above keeps at the calibrated height. A spot whose model height is 12 ft but whose calibrated height is 6 ft doesn't close out; one whose calibrated height is 12 ft does, whatever the model says.

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
| Wave height | NWPS, NOAA's Nearshore Wave Prediction System | Most spots. WAVEWATCH III fills the rest. CDIP MOP at 48 California spots, for past hours only. |
| Swell direction and period | WAVEWATCH III (gfswave) | Up to three swells plus local wind sea, in 3-hour steps. CDIP MOP at the MOP spots, for its past hours. |
| Wind | HRRR, a 3 km model of the continental US | Hourly steps for the first 48 hours of each run, then NWPS wind. NWPS wind throughout in Hawaii and Puerto Rico. |
| Tide | NOAA CO-OPS predictions | Hourly where the station publishes them, otherwise built from the high and low times. |
| Buoys | NDBC | The latest reading is shown on each spot page. Buoys also check NWPS before we use it at a spot, and at some spots and hours they stand in for swell direction and period. |
| Height calibration | CDIP MOP | 141 California spots, measured over four weeks. |

## Where the wave height comes from

The model behind the height depends on the spot and the hour:

- **Most spots: NWPS.** NOAA runs NWPS separately for each coastal forecast office, on a much finer grid than the global models. We read a grid point just offshore of the spot, for each hour the latest run covers.
- **48 California spots: CDIP MOP, for past hours only.** MOP is the Coastal Data Information Program's model of points along the California coast, just outside the surf zone. We read its nowcast, and the nowcast ends before the current hour, even right after a forecast run. So at the 48 MOP spots, the hours you can see, the current one included, are handled like any other hour, below.
- **Any other hour: WAVEWATCH III.** The WW3 swells are combined as described below. If WW3 has nothing usable for that hour either, we fall back to NWPS's own height.

**Calibration at 141 California spots.** For these spots we compared our height with MOP's over four weeks, 8 September to 6 October 2026, and now divide the height we show by each spot's typical ratio. The star rating reads the calibrated height too, times 1.59, so these spots are rated on the same scale as everywhere else (see Size).

**What the number is.** The height we publish is labelled nearshore swell height: the significant height of the waves just outside the surf zone, roughly the average of the bigger waves. It is not the face of a breaking wave, which is often bigger. The label holds at the 141 calibrated California spots, which are scaled so their typical height matches MOP's. Everywhere else, the model height gets a boost for longer-period swell and isn't corrected, so it can read higher than nearshore swell height. That includes all the hours you can see at the 48 MOP spots: MOP's own height, which carries no boost, never reaches the page. On the site, each spot's current height is marked with which it is: "Calibrated to CDIP's nearshore model" or "Model estimate".

**The range we show.** On the Swell height tile, the spot lists, the map and the cams page, the height appears as a reading range — the standard step the height falls in. The steps are under 1 ft, 1–2, 2–3, 3–4, 4–5, 5–6, 6–8, 8–10, 10–12, 12–15, 15–20, 20–25 and 25–30 ft, then 5 ft at a time. A height exactly on a boundary goes to the higher step, so 6.0 ft reads 6–8 ft and 5.99 ft reads 5–6 ft. The steps are the same at every spot, and they're built from the height described above: the calibrated height where we have one, the model's elsewhere. The range is a way of reading that one number, not a measured spread. Individual waves vary more than it does: some will be smaller, and the bigger sets can be well above the top of the step. The chart on each spot page plots the single height itself.

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

**Where the height comes from NWPS, the window chooses the swell but doesn't cut the height.** NWPS models the water near the coast, so the height we read from it already carries the sheltering by islands and headlands that the window stands for. Multiplying that height by the outside-the-window gains above cut the same energy a second time. So for NWPS heights the window now does one job: it weighs the WW3 swells, as described below, and picks the one whose direction we score. The height is then scaled only by how well that direction lines up with the spot: cos²(offset ÷ 2) from the best swell direction, never below 0.25, inside the window or out. Heights from WW3 and MOP still take the window's gain in full.

When we replayed the forecast published on 19 October 2026 this way, 7,162 of 78,817 NWPS spot-hours (9.1%), at 230 of our spots, changed: 6,875 came out higher and 287 lower, each one scored on the swell the window had chosen. The lower ones are swells more than about 100° from the spot's best direction, which now score below the 0.40 the window gave them. We also tried leaving the window out of the choice. Then a swell from outside the window could be picked instead, and 2,053 spot-hours came out lower on its direction.

## Wind

The first version of Stormy Petrel used the wind that comes with the NWPS wave forecast. Users told us it said offshore when it was clearly onshore. HRRR is a 3 km model with hourly steps, and it picks up sea breezes and terrain-driven offshore winds much better.

We use HRRR for the first 48 hours of each run, and NWPS wind after that. HRRR only covers the continental US, so Hawaii and Puerto Rico use NWPS wind throughout. Each spot takes its nearest HRRR grid point, or, where HRRR counts that point as land, the nearest point over water on the spot's seaward side: wind over land is slowed by the land's roughness. HRRR gives its wind along its own map grid, which at our spots is turned by up to about 17° from true north (about 14° in New Jersey, 15° the other way in California), so we turn the wind to true north before comparing it with the spot's offshore direction.

The wind score depends on the wind's angle to the spot's offshore direction: offshore scores best, onshore worst. Light wind matters less, strong offshore wind loses its bonus, and very strong wind costs extra.

### Strong wind caps the rating

The weighted blend shrinks even a bad wind score. Cross-shore wind scores 0.80, and the 0.35 weight turns that into a 7.5% cut. On 20 October 2026 that left Jennettes Pier, Avalon Pier, Avon Pier and Kill Devil Hills on the Outer Banks at 4.5 stars in a 24–26 kt north wind on 11–12 ft storm surf, which Surfline rated "Very Poor".

So strong wind now sets a ceiling on the rating, whatever the other scores are. It follows the wind speed ladder in [our wind article](/learn/wind): junky above about 13 kt, blown out from 15 kt, and generally unsurfable above 25 kt, even offshore.

| Wind | Onshore | Cross-shore | Offshore |
|------|---------|-------------|----------|
| over 13 kt | 3 stars | 3 stars | no cap |
| over 15 kt | 2 | 2.5 | no cap |
| over 20 kt | 1.5 | 1.5 | 3 |
| over 25 kt | 1 | 1 | 2 |

Offshore means within 60° of the spot's straight-offshore direction, cross-shore 60° to 120°, and onshore more than 120°.

For the cap, cross-offshore wind, 60° to 90° off, counts as offshore: it blows partly offshore, so it gets the offshore column, not the cross-shore one.

Long-period swell stands up to wind better, so up to 20 kt the wind is read a knot lighter for every second of period above 8 s: a 12 s swell in an 18 kt wind is read at 14 kt. Above 20 kt there's no allowance.

When we replayed the forecast published on 20 October 2026 with the cap and the beach-break limit together, 8,844 of 83,446 spot-hours (10.6%), at 257 of our spots, came out lower, and none came out higher. 6,697 moved for the wind alone, 736 for the beach-break limit alone, and 1,411 for both. Most were windy East Coast hours, above all the 25–30 kt onshore days in Florida's forecast; California moved by 72 hours. Judging closing out at the calibrated height kept 12 California hours, at Caspar, Jug Handle and Carmel Beach, from dropping half a star.

On 21 October 2026 we tightened two rungs. Jennettes Pier read a 21.8 kt cross-shore wind at 11.9 s as 17.9 kt and kept 2.5 stars on a day Surfline rated "Very Poor"; now the allowance stops at 20 kt. Ocean City NJ rated 4.5 stars in a 22 kt offshore wind that Surfline rated "Fair", and Atlantic City 4.5 in 21–24 kt; offshore that strong holds the lip up so long that drops go late and vertical, so it's now capped at 3 stars. Replaying the forecast published that day, 2,612 of 95,107 spot-hours (2.7%), at 259 of our spots, came out lower, and none came out higher: 2,256 because the allowance stops at 20 kt, and 356 for the offshore cap. Ocean City NJ and Atlantic City went from 4.5 to 3 stars.

On 22 October 2026 we found that HRRR's wind direction had been read off its map grid rather than true north, about 14° out in New Jersey. Turned to true north, Ocean City NJ's 20-plus-knot wind at 16:00 UTC on 21 October moved from 58° to 72° off its offshore direction, from offshore to cross-shore, and the cross-shore column cut it from 3 stars to 1.5 for a wind still blowing partly offshore. So cross-offshore wind now reads the offshore column. Replaying 27,813 published HRRR hours with both changes, 2,605 moved: 726 up and 1,879 down.

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

The tide score compares the tide at that hour with the tide the spot prefers: low, mid, high, or a range in between. It's full marks when they match and lower when they don't.

**A preferred tide only counts once it's verified.** We use it only when a person has reviewed it, or when it comes from a page about that spot that we cite: a source we researched, or the spot's own surf-forecast.com page. Any other preference scores neutral, the same as a spot that works at all tides. So does a spot with no preference, and an hour with no tide data.

Today 126 of our spots have a preferred tide that counts, each one stated on a page about that spot that we cite: 57 work at all tides, 12 at low, 23 at low to mid, 15 at mid, 13 at mid to high and 6 at high. Every other spot stays neutral on tide until we've researched it.

Why: until October 2026, every preferred tide we held, 444 of them, was an estimate written by a language model that had been told to give an answer even when it didn't know one, and 365 of the 444 said "mid". The tide score can only lower a rating, never raise it. When we replayed the forecast published on 15 October 2026 with those estimates set aside, 6,574 of 83,359 spot-hours (7.9%), across 426 of our spots, came out half a star higher, and none came out lower. A guess shouldn't cost a spot half a star, so none of those estimates affects a rating. 356 of them are still unverified; each will count only once it's verified.

When we replayed the forecast published on 18 October 2026 with the 126 verified preferences applied, 1,011 of 85,913 spot-hours (1.2%), at 67 spots, came out half a star lower, and none came out higher. A spot that works at all tides never loses anything to the tide, so only the other 69 could.

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

1. **Local geology.** A reef pass that focuses energy, or a sandbar that moves with the seasons, isn't in any model. The calibration at 141 California spots corrects each one's typical height; other spots have no local height correction.
2. **Crowds aren't modelled.** Two spots with the same conditions can be very different sessions.
3. **Tide preference.** Only 126 of our spots have a verified preferred tide so far (see Tide); at every other spot the tide doesn't change the rating. We're researching the rest spot by spot, each from a source we cite.
4. **Height calibration drifts.** Each spot's factor comes from a single four-week window, and it moves with the swell mix: between two of the earlier two-week windows, about a third of spots shifted by more than 10%.
5. **Uncalibrated heights can run high.** Outside the 141 calibrated California spots, heights aren't corrected, and that includes all the hours you can see at the 48 MOP spots. They include the period boost and can read higher than nearshore swell height, which is why they're marked as model estimates. At the California spots where we measured it, the uncorrected height was typically about 1.6 times MOP's. Whether heights elsewhere run high by the same amount hasn't been measured, so the rating leaves them as the model makes them.
6. **Rating the calibrated spots.** We used to work out stars at the calibrated spots from the calibrated height. The calibration in use then covered 130 California spots, measured from 18 August to 1 September 2026, and at all but two of them that height was smaller than the uncalibrated one, so in the same swell they rated lower than everywhere else. In the forecast published on 4 October 2026, 0.4% of those spots' daylight hours rated 3 stars or more, against 9.1% at uncalibrated spots; rated on the model's height instead, they came to 10.3%. From then until 21 October 2026 the rating used the model's height at every spot, and that overrated the calibrated spots whose model height runs well above MOP's: six of the ten spots in "Best conditions right now" were calibrated California spots shown at 1 to 4 ft and rated 4 to 4.5 stars. Now the rating reads their calibrated height times 1.59 (see Size). When we replayed the forecast published on 20 October 2026 that way, 8,673 of 85,470 spot-hours (10.1%), all in California, changed: 4,585 came out higher and 4,088 lower.
7. **Break types.** The beach-break limit applies at 404 of our spots, every beach or jetty break over sand. At 223 of them the break type or the bottom is still a language model's recollection rather than a source we cite. We apply it anyway, because every one it changed when we checked was a genuine sand beach or jetty, and we're citing the rest spot by spot.
8. **Refresh delay.** Forecasts are {{FORECAST_UPDATES}}, not continuously. If a swell builds faster than that, the rating lags until the next update. Buoy readings arrive {{BUOY_UPDATES}}, and we show the latest one next to each spot.

The code is on [GitHub](https://github.com/nunneryl/StormyPetrel). The rating is in [`pipeline/interpret.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/interpret.py), and the NWPS, MOP and calibration steps are in [`nwps_nearshore.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/forecast/nwps_nearshore.py), [`mop.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/forecast/mop.py) and [`face_correction.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/forecast/face_correction.py). File an issue if you spot a bug, or send a PR if you want to fix one.
