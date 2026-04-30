---
title: 'About Stormy Petrel'
description: 'Why a free, open surf forecast site exists in 2026 — and what happened to MagicSeaweed.'
date: '2026-04-30'
author: 'Stormy Petrel'
tag: 'updates'
---

## What is Stormy Petrel?

Stormy Petrel is a free surf forecasting site for ~500 US surf spots. There's no paywall, no ads, no signup required, and the entire pipeline that produces every rating is open source on [GitHub](https://github.com/nunneryl/StormyPetrel).

The forecasts use the same NOAA / NCEP atmospheric and ocean models the professional surf services pay for: NWPS for nearshore wave heights, WAVEWATCH III (gfswave) for spectral swell partitions, HRRR for 3km wind, NDBC for realtime buoy observations, and CO-OPS for tide predictions. Every forecast hour is rated against each spot's specific orientation and swell window.

## Why does it exist?

In 2024, MagicSeaweed shut down. For two decades MSW had been the canonical free surf forecast for serious surfers — clean information design, honest data, no upsells. When it went dark the surf-forecasting space was left with two paid services (Surfline, Magicseaweed-acquirer's combined product) that had quietly raised prices, added paywalls behind chart features that used to be free, and started leaning on AI-generated marketing content.

Stormy Petrel exists to put that information back in the public domain. Every forecast on this site is built on data NOAA publishes for free. The interpretation logic — how to turn an offshore wave height into a rideable face height, how to penalize an off-axis swell, how to combine multiple swell trains — is in [`pipeline/interpret.py`](https://github.com/nunneryl/StormyPetrel/blob/main/pipeline/interpret.py) for anyone to read, critique, and improve.

## What's the petrel?

A petrel is a small ocean bird that spends most of its life at sea, riding storm systems for fish. They're known for showing up before bad weather. The "stormy petrel" is a metaphor for the forecast itself — it sees the swell before it arrives.

## Who runs it?

One person, in their spare time. Forecasts refresh every 6 hours via a GitHub Actions cron that fetches the latest model data, runs the rating engine, and pushes everything to a Postgres database that the site reads. Buoy observations refresh hourly. The whole stack runs on free tiers.

## How can I help?

Right now the most useful thing you can do is tell me when a rating is wrong. Every spot page has a "Report incorrect data" link that opens a GitHub issue with the spot already filled in. The two failure modes I'm chasing are:

1. **Wrong orientation / swell window.** Many spots are auto-classified by an algorithm; some get it backwards (e.g. picking up a south swell where the spot only takes north).
2. **Local microclimate the model doesn't see.** A spot 200 m behind a headland may shadow differently than the 3 km HRRR grid resolves.

If you find one, file an issue with the spot, the time, and what Surfline / your eyes saw. The more reports, the better the model gets.

— SP
