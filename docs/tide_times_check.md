# Tide times: ours against NOAA's published high and low tides

`scripts/tide_times_check.py`, run 2026-11-25 against NOAA CO-OPS. For each station and date: NOAA's published high and low tide times (its tide table, in local time), and the times our stored hourly tide gives them, found as the site finds them (the parabola through three hourly values, `frontend/lib/tideTiming.ts`), with this change (CO-OPS in UTC, read at the UTC hour) and before it (CO-OPS in local time, read at the UTC hour plus `round(lng / 15)` hours). Times are local to the station; the minutes are ours minus NOAA's.

- **With this change**, every high and low is within 7 minutes of NOAA's, most within 3: the residue is reading a turn off hourly values (Santa Cruz is a high/low-only station, so its hours are themselves interpolated between NOAA's highs and lows).
- **Before it**, every July tide was about an hour late at all three stations (daylight saving), and every Honolulu tide was an hour late in December too (`round(-157.9 / 15)` is -11; Hawaii is UTC-10). Only the mainland in December matched.

| Station | Date | Tide | NOAA | Ours, with this change | Ours, before |
|---|---|---|---|---|---|
| Wrightsville Beach, NC (8658163) | 2026-07-15 | Low | 02:50 EDT | 02:49 (+0 min) | 03:49 (+60 min) |
| Wrightsville Beach, NC (8658163) | 2026-07-15 | High | 08:33 EDT | 08:33 (+0 min) | 09:33 (+60 min) |
| Wrightsville Beach, NC (8658163) | 2026-07-15 | Low | 14:46 EDT | 14:44 (-1 min) | 15:44 (+59 min) |
| Wrightsville Beach, NC (8658163) | 2026-07-15 | High | 21:00 EDT | 21:00 (+1 min) | 22:00 (+61 min) |
| Wrightsville Beach, NC (8658163) | 2026-12-15 | Low | 04:38 EST | 04:37 (+0 min) | 04:37 (+0 min) |
| Wrightsville Beach, NC (8658163) | 2026-12-15 | High | 11:16 EST | 11:14 (-2 min) | 11:14 (-2 min) |
| Wrightsville Beach, NC (8658163) | 2026-12-15 | Low | 17:29 EST | 17:28 (+0 min) | 17:28 (+0 min) |
| Wrightsville Beach, NC (8658163) | 2026-12-15 | High | 23:40 EST | 23:39 (+0 min) | 23:39 (+0 min) |
| Santa Cruz, CA (9413745) | 2026-07-15 | Low | 06:03 PDT | 06:06 (+3 min) | 07:06 (+63 min) |
| Santa Cruz, CA (9413745) | 2026-07-15 | High | 12:53 PDT | 12:59 (+6 min) | 13:59 (+66 min) |
| Santa Cruz, CA (9413745) | 2026-07-15 | Low | 17:25 PDT | 17:22 (-3 min) | 18:22 (+57 min) |
| Santa Cruz, CA (9413745) | 2026-07-15 | High | 23:31 PDT | 23:28 (-3 min) | 00:28 (+57 min) |
| Santa Cruz, CA (9413745) | 2026-12-15 | High | 03:35 PST | 03:41 (+6 min) | 03:41 (+6 min) |
| Santa Cruz, CA (9413745) | 2026-12-15 | Low | 08:27 PST | 08:28 (+1 min) | 08:28 (+1 min) |
| Santa Cruz, CA (9413745) | 2026-12-15 | High | 13:28 PST | 13:21 (-7 min) | 13:21 (-7 min) |
| Santa Cruz, CA (9413745) | 2026-12-15 | Low | 20:42 PST | 20:41 (+0 min) | 20:41 (+0 min) |
| Honolulu, HI (1612340) | 2026-07-15 | High | 04:44 HST | 04:44 (+0 min) | 05:44 (+60 min) |
| Honolulu, HI (1612340) | 2026-07-15 | Low | 09:59 HST | 09:56 (-3 min) | 10:56 (+57 min) |
| Honolulu, HI (1612340) | 2026-07-15 | High | 17:19 HST | 17:18 (+0 min) | 18:18 (+60 min) |
| Honolulu, HI (1612340) | 2026-12-15 | Low | 00:32 HST | 00:30 (-1 min) | 01:30 (+59 min) |
| Honolulu, HI (1612340) | 2026-12-15 | High | 08:03 HST | 08:02 (-1 min) | 09:02 (+59 min) |
| Honolulu, HI (1612340) | 2026-12-15 | Low | 15:41 HST | 15:42 (+2 min) | 16:42 (+62 min) |
| Honolulu, HI (1612340) | 2026-12-15 | High | 22:11 HST | 22:13 (+2 min) | 23:13 (+62 min) |

Stations: Wrightsville Beach (8658163, hourly predictions), Santa Cruz (9413745, high/low only, used by Steamer Lane and its neighbours), Honolulu (1612340, hourly).
