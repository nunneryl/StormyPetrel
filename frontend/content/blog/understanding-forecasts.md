---
title: 'Understanding surf forecasts'
description: 'A short primer on swell period, direction, wind, and tide for surfers who haven''t spent years staring at MagicSeaweed.'
date: '2026-04-28'
author: 'Stormy Petrel'
tag: 'forecasts'
---

If you're newer to surfing and the forecast page looks like alphabet soup, this is for you. Five things to know.

## 1. Wave height isn't what it looks like

The number you usually see on a forecast — say, **3 ft** — is *significant wave height (Hs)*: roughly the average height of the largest third of waves in a given window. The wave you actually ride at the beach is the *face* — measured from trough to crest as it breaks. Face is taller than Hs, by roughly 1.3–1.6× depending on bottom contour and period.

So when this site says **3 ft face**, that's an actual breaking-wave-face estimate. Surfline calls the same thing "knee to thigh" or "shoulder to head" depending on your height. We chose feet-of-face because it's less ambiguous.

## 2. Period is more important than height

A swell with 3 ft Hs and 14 second period will produce dramatically better surf than a swell with 3 ft Hs and 6 second period. Period is the time between successive wave crests passing a fixed point, and it correlates with how much energy the swell is carrying. Long period = long-traveled groundswell from a distant storm = clean, organized, hits hard. Short period = locally generated wind chop = shorter waves with less push.

Rough rule of thumb: under 8s is wind chop, 8–11s is mixed, 11–13s is groundswell, 13s+ is long-period (best for surf), 16s+ is rare and dangerous (huge North Pacific energy reaches the shore as solid head-and-up sets even at modest Hs).

## 3. Direction matters as much as height

Every surf spot has a **swell window** — the range of compass bearings it can receive swell from. A north-facing spot like Pipeline only "sees" swells coming from a roughly 270° (W) through 30° (NNE) arc. A south-facing point like Trestles only sees swells in the southern hemisphere arc.

A 6 ft 15s long-period swell from the south is invisible at Pipeline — the swell wraps around Oahu and dies before reaching the North Shore. The same swell at Trestles is a session of a lifetime. This is why a forecast site can't just look at "the swell" globally; each spot's rating depends on whether the swell arrives from a direction that spot's geometry can receive.

This site uses spectral partition data from WAVEWATCH III to identify each individual swell train and check it against each spot's specific window. When you see "P1 / P2 / P3" in the swell components card, that's three different swells on the ocean at the same time, each evaluated separately for that spot.

## 4. Wind makes or breaks everything

Even a perfect swell with the right size and period can be junked by 15 mph onshore wind. Wind direction relative to the spot determines wave quality:

- **Offshore** (wind blowing from land to ocean): grooms the wave face, holds the lip up, makes faces glassy. Best.
- **Cross-offshore**: still clean, slight texture.
- **Cross-shore**: textured but rideable.
- **Cross-onshore / onshore**: blown out, the wave loses shape and breaks earlier than it should.

The forecast pages here color-code wind quality directly: green for offshore, yellow for cross, red for onshore. A 4 ft 12s NW swell at Mavericks with offshore wind is a 4★ day; the same swell with 20 mph onshore is maybe 1★.

Local sea breezes follow daily cycles in most coastal regions:
- **Dawn-patrol glass-off** (low wind from overnight cooling)
- **Mid-morning sea breeze building** (onshore as land heats up)
- **Afternoon onshore peak**
- **Evening glass-off** (wind dies as land cools)

The HRRR model used for our wind forecasts resolves these cycles at 3 km × hourly resolution, so the daily wind pattern in your forecast should match what you actually see.

## 5. Tide affects each spot differently

Some spots only break on a low tide (the swell needs the bottom to feel it), some only on a high tide (otherwise it's too shallow and closes out), some at all tides. We track each spot's tide preference and reduce the rating outside its window. The forecast page shows you the current tide level + whether it's rising or falling, plus a chart of the next 7 days with H/L markers.

Rule of thumb when reading a forecast:
- **Reef breaks** often need a specific tide window
- **Beach breaks** are usually less picky but lows often reveal banks
- **Points** can hold a wider tide range

## Putting it together

Here's how I read a forecast page in 5 seconds:

1. Hero rating badge — top right of the spot page. POOR / FAIR / GOOD / EPIC. If POOR, no need to read further unless you're really curious.
2. Wave height + period — the 5 ft 14s entry is a session, the 5 ft 7s entry is closeouts.
3. Wind direction — green or red label tells you everything. Red = blown out regardless of size.
4. Tide level — does it match what the spot wants?
5. Swell components — if "P1" is in window with long period, that's the real swell. Wind sea in red text is always present and rarely matters for rating.

Once you've read a few of these, the pattern becomes second nature. You'll start judging "is it worth driving to" off the forecast in seconds, not minutes.
