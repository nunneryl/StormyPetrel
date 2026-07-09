# MOP prototype — feasibility spike (read-only)

Can consuming **CDIP MOP** nearshore output give us the two things the GSHHG
raycast failed to produce — a physically-correct swell **window** and a
**refraction-aware optimal direction** — on the 4 CA spots that failed the gate?

**Verdict: yes in principle, and the derivation is proven — but I could not pull
the live numbers from this environment.** `thredds.cdip.ucsd.edu` is egress-blocked
here (`403 host_not_allowed`), exactly like the GSHHG mirrors. Per the same
discipline as the GSHHG gate, I am **not fabricating** the 4-spot MOP numbers.
What I delivered instead: a correct, CDIP-grounded script (`scripts/mop_prototype.py`)
that prints the 4-spot table when run where egress is open, and an **offline-validated
derivation** that demonstrably yields WNW for a Rincon-like refracted spectrum.

Nothing here touches `spots_enriched.json`, the rating pipeline, or any prod path.

---

## Egress status (the gate, stated plainly)

```
$ python scripts/mop_prototype.py
*** THREDDS UNREACHABLE — cannot read CDIP MOP from this environment. ***
    https://thredds.cdip.ucsd.edu/thredds/catalog/cdip/model/MOP_alongshore/catalog.xml
    HTTPError: HTTP Error 403: Forbidden
```
Every CDIP host (`cdip.ucsd.edu`, `thredds.cdip.ucsd.edu`) returns
`403 host_not_allowed`; `cdip.coastwatch.pfeg.noaa.gov` doesn't resolve. PyPI is
reachable, so I installed the netCDF stack (`xarray`, `netCDF4`) **and CDIP's own
`cdippy`** — whose source I used to ground the script's THREDDS URLs and the MOP
variable names below (so it's CDIP's real schema, not my memory). The script must
be run where outbound to `thredds.cdip.ucsd.edu` is allowed (a CI runner with open
egress, like the GSHHG job).

## What IS proven here: the derivation math (`--selftest`, offline)

```
$ python scripts/mop_prototype.py --selftest
  PASS  WNW-peaked energy -> optimal_peak=265 (~265)
  PASS  WNW-peaked energy -> optimal_mean=265 (~265)
  PASS  window arc width=50 brackets 265 (236-286)
  PASS  spectra->derive optimal_peak=265 (~265)
  PASS  windsea band excluded (only swell dirs counted)
  PASS  bimodal weighted-W -> peak=265 on W lobe (~265)
  PASS  haversine ~100 m for 0.0011 deg lon (101 m)
```
The method: accumulate MOP's per-(time,frequency) **energy** into direction bins
over a climatology, restricted to the **swell band (Tp ≥ 8 s)**, to build a
climatological directional-energy distribution `E(θ)` at the point. From it:
**optimal** = peak / energy-weighted circular mean of `E(θ)`; **window** = the
smallest contiguous arc holding 85 % of the energy. The self-test shows that when
the input refracted spectrum peaks WNW, the method reports WNW — i.e. **the
extraction is not the bottleneck; only the live pull is.**

---

## Task 1 — MOP point catalog + nearest-neighbour match

- Catalog: `thredds/catalog/cdip/model/MOP_alongshore/catalog.xml` → ~4,729
  alongshore points on the 10 m contour, county-prefixed, numbered S→N
  (`OC055_…`, `SD…`, `VE…`, etc.). The script walks the catalog XML (same method
  as `cdippy.get_dataset_urls`), reads each candidate point's
  `metaLatitude`/`metaLongitude` (or `geospatial_lat_min/lon_min`), and
  nearest-neighbours each spot by haversine.
- **Stored coords used** (from `spots_enriched.json`; the prompt's were approximate):

  | spot | stored lat,lng | note |
  |---|---|---|
  | Blacks Beach | 32.879677, -117.252982 | San Diego Blacks Beach |
  | Rincon | 34.371814, -119.478507 | |
  | Malibu Surfrider | 34.03143, -118.688865 | the "nearly passed / raycast 90" one (not Malibu Point, ~1 km E) |
  | Huntington Beach | 33.640633, -117.986298 | "Huntington Beach, California" (the Pier point is ~1.5 km NW) |

- Match distances: **PENDING egress.** Expectation for these open-coast points:
  `< ~100–200 m` (10 m-contour spacing is ~100 m in SoCal). The script prints the
  exact `dist_m` per spot; a large distance is the signal a spot has no clean MOP
  point (see the seam, Task 4).

## Task 2 — what MOP exposes (variable schema, from `cdippy` + CDIP docs)

**2a. Is there a precomputed "open direction range" / window field? No.** MOP
(O'Reilly et al. 2016) publishes the **refracted nearshore spectrum** at each
point, not a stored valid-direction arc. The window is *derived* from the spectrum
(2b). The script still dumps the live variable list for one matched point so this
is verified, not assumed; the expected list (CDIP standard wave schema):

```
waveTime            (waveTime)            UTC time
waveFrequency       (waveFrequency)       spectral bands (~64), Hz
waveEnergyDensity   (waveTime,waveFrequency)  energy density, m^2/Hz   <- the spectrum
waveA1, waveB1      (waveTime,waveFrequency)  1st directional Fourier moments
waveA2, waveB2      (waveTime,waveFrequency)  2nd directional Fourier moments
waveMeanDirection   (waveTime,waveFrequency)  per-freq mean direction, deg  <- refracted theta
waveDp              (waveTime)            peak-frequency direction, deg
waveTp, waveTa      (waveTime)            peak / average period, s
waveHs              (waveTime)            significant wave height, m
metaLatitude, metaLongitude              10 m-contour point location
metaStationName / metaSiteLabel          point id
(global attrs: geospatial_lat_min/lon_min, …)
```
`waveMeanDirection` (or `atan2(waveB1, waveA1)` if absent) is the **refracted**
local direction at the 10 m contour — that is precisely the refraction-aware
quantity the geometric raycast cannot compute. MOP flavours per point:
`*_hindcast.nc` (history), `*_nowcast.nc` (hourly), `*_forecast.nc` (6-hourly,
ECMWF-driven). For a climatology the script reads the last 365 days of
nowcast/hindcast.

## Task 3 — derived window + optimal vs targets

| spot | raycast | target window | **MOP window** | **MOP optimal** | the check |
|---|---|---|---|---|---|
| Blacks Beach | 74 (ceiling 84) | 140–170 | *pending egress* | *pending* | does it break the 84 ceiling? |
| **Rincon** | 92, **wrong lobe (196)** | 80–100 | *pending egress* | *pending* | **WNW ~260–270?** |
| Malibu Surfrider | 90 | 100–140 | *pending egress* | *pending* | |
| Huntington Beach | 100 | 150–180 | *pending egress* | *pending* | separates from Rincon? |

I am leaving the MOP columns **empty rather than faked** — I did not pull them.

**The Rincon-WNW verdict (the single most important check).** Structurally, the
raycast *cannot* produce Rincon's WNW optimum: it is pure line-of-sight geometry,
so it opened Rincon's southern exposure (NE-coarse coastline) and its circular
mean landed on the **S lobe (196°)** — the documented failure. MOP is a **spectral
refraction model**: it computes the energy that actually reaches Rincon's 10 m
point, which is dominated by **W/NW swell wrapping around the point and down the
Santa Barbara Channel**. So MOP's `E(θ)` for Rincon should peak **WNW (~260–270°)**,
and the validated derivation will report that peak (the `--selftest` proves the
extraction step on exactly this shape). **Strong physical expectation + a proven
extractor ⇒ I expect the script to show Rincon WNW — but I have not measured it
here and will not assert a number I didn't pull.** This is the one result worth
running the script for first.

## Task 4 — feasibility for the ~170 CA spots

**Access cadence / latency / size.** Forecast files refresh every 6 h (ECMWF);
nowcast/hindcast hourly. Per-point OPeNDAP lets you subset server-side to the last
year + swell band, so a climatology transfer is ~a few MB/point (full multi-year
hindcast files are tens of MB, but you never need the whole thing). A **one-time**
pull for ~170 CA spots is a few hundred MB of OPeNDAP — comfortably a single CI
job (same shape as the GSHHG job). Walking the full 4,729-point catalog to build
the coordinate index is the heavy step; cache it once.

**Integration — my read: bake it into enrichment (one-time, like orientation),
not a live per-cycle read.**
- The **window + baseline optimal are stable geometry** (a refraction climatology;
  it doesn't change cycle-to-cycle). Compute once per spot, store next to
  `orientation_deg`, recompute maybe yearly. This directly replaces the raycast's
  two failing outputs with MOP-derived ones at **zero per-cycle cost and no new
  runtime dependency on THREDDS** — the right Phase-1 integration.
- A **live per-cycle read** of the current refracted Tp/Dp/Hs at each spot's MOP
  point (like buoy/tide) is a real upgrade — it makes the *forecast itself*
  refraction-aware, not just the static window — but it couples the rating
  pipeline to a 170-read THREDDS dependency every 6 h. **Defer it to a Phase 2**
  once the baked window/optimal proves out; don't take the live dependency first.

**The seam (coverage).** MOP covers the **open coast on the 10 m contour**.
Open-coast spots — Blacks, Rincon, Malibu, Huntington, and the large majority of
the ~170 CA spots — should match cleanly (<~200 m). No clean match for:
**deeply sheltered / bay / harbor / estuary breaks** (e.g. inside Mission Bay,
Newport Harbor backside, SF Bay, Morro Bay back) where the nearest 10 m-contour
MOP point sits on the open coast outside the shelter and isn't representative; the
NN distance blows past ~0.5–2 km there. Those keep the existing
`orientation_derived` fallback. And **non-CA spots have no MOP at all** (MOP is a
CA-coast product) — RI, the Gulf, Great Lakes, etc. stay on the fallback. So MOP
is a **CA-open-coast solution**: it fixes the spots the raycast was supposed to fix
and is silent elsewhere. The script prints `dist_m` per spot precisely so a
threshold (e.g. >500 m ⇒ no clean MOP match ⇒ fallback) can be set from data.

---

## How to run it (where egress is open)

```
python scripts/mop_prototype.py            # prints the 4-spot table (Task 1 + 3) + variable dump (Task 2)
python scripts/mop_prototype.py --selftest # offline proof of the derivation math
```
First run there answers the open question: **does Rincon come out WNW?** If it
does (as the physics says), MOP is worth a one-time enrichment bake for the CA
open coast.

## Honest limits of this spike
- **Not measured:** the 4-spot match distances and MOP window/optimal — egress
  blocked. The math is proven; the live pull is not.
- The MOP variable list above is CDIP's documented schema (via `cdippy`); the
  script dumps the *actual* list at run time to confirm.
- Spot↔MOP coord choices for Malibu/Huntington noted in Task 1 (alternatives ~1 km
  away); trivially swappable in `SPOTS`.
