"""Pipeline constants: endpoints, user agent, rate limits, thresholds."""
from pathlib import Path

USER_AGENT = "StormyPetrel-Pipeline/0.1 (+https://stormypetrel.surf)"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

WIKIPEDIA_API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_ROOT_CATEGORIES = (
    "Category:Surfing_locations_in_the_United_States",
    "Category:Surfing_venues_in_the_United_States",
    "Category:Surfing_in_the_United_States",
)
WIKIPEDIA_MAX_CATEGORY_DEPTH = 4
WIKIPEDIA_PAGES_PER_BATCH = 50
WIKIPEDIA_MIN_INTERVAL_S = 0.2  # ~5 req/s

DEDUPE_DISTANCE_M = 500.0
DEDUPE_NAME_SCORE = 85

SOURCE_PRIORITY = ("wikidata", "wikipedia", "osm", "gapfill")

NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/search"
NOMINATIM_MIN_INTERVAL_S = 1.0  # Nominatim usage policy: max 1 req/s
GAPFILL_DATA_FILE = Path(__file__).resolve().parent / "data" / "llm_spots.json"

PIPELINE_DIR = Path(__file__).resolve().parent
CACHE_DIR = PIPELINE_DIR / "cache"
DEFAULT_OUTPUT = PIPELINE_DIR / "spots_seed.json"

# ---------------------------------------------------------------------------
# Enrichment (Phase 0B)
# ---------------------------------------------------------------------------
GEODATA_DIR = PIPELINE_DIR / "geodata"
GSHHG_L1_SHP = GEODATA_DIR / "GSHHS_f_L1.shp"
CUSP_DIR = GEODATA_DIR  # CUSP shoreline files (optional; falls back to GSHHG)
NDBC_STATIONS_XML = GEODATA_DIR / "ndbc_stations.xml"
NDBC_LATEST_OBS_TXT = GEODATA_DIR / "ndbc_latest_obs.txt"
TIDE_STATIONS_JSON = GEODATA_DIR / "tide_stations.json"

DEFAULT_ENRICHED_OUTPUT = PIPELINE_DIR / "spots_enriched.json"

# Swell window ray-casting
SWELL_RAY_STEP_DEG = 2
SWELL_ARC_SHRINK_DEG = 5  # conservative shrink on each end of merged open arcs
SWELL_LOCAL_COAST_EXCLUSION_KM = 2  # ignore land within this distance of the spot; local coast isn't a swell blocker
SWELL_MIN_FETCH_KM = 3_000  # a bearing is "open" iff the first land hit is beyond this distance (long-period swell fetch)

# --- Blocker geometry (SW-1) -------------------------------------------------
# The original ray-cast treated *any* land polygon crossing a ray as a total
# blocker. That walls off swell behind small islands (Catalina, San Clemente,
# Anacapa, …) that physically diffract / refract around them — collapsing
# open-coast California windows to ~50°. The rules below replace the binary
# total-block with area-, distance-, and angular-width-aware partial blocking.
#
# A bearing is hard-blocked (the swell genuinely can't get there) iff a ray hits
# either (a) a landmass at or above SWELL_BLOCKER_AREA_KM2 *within* the near/mid
# field (SWELL_MAINLAND_SOLID_KM) — continents / big islands the swell can't get
# around — or (b) ANY land within SWELL_LOCAL_LANDMASS_KM, which is the coast /
# headland the spot itself sits on (this must keep blocking even when the local
# landmass is a small island, e.g. Aquidneck Is. for Newport RI). A large landmass
# hit BEYOND SWELL_MAINLAND_SOLID_KM is a distance-graduated partial blocker, not a
# wall — it wraps open by distance through the diffraction machinery below.
SWELL_BLOCKER_AREA_KM2 = 500.0   # land below this is a partial blocker, not a wall
SWELL_LOCAL_LANDMASS_KM = 30.0   # any land nearer than this always hard-blocks (local coast)
# A large (>= SWELL_BLOCKER_AREA_KM2) landmass hard-blocks only within this range;
# beyond it, it is demoted to a partial blocker and fed through the same distance-aware
# diffraction-wrap machinery a sub-threshold island already gets. This fixes the
# "3000 km min-fetch too aggressive" SW-1 backlog item: a coast merely grazed hundreds
# or thousands of km downrange under SWELL_MIN_FETCH_KM no longer walls off an open-ocean
# bearing (it diffracts around), while a genuine obstruction still blocks. 100 km is the
# bucket-B diagnostic boundary from the validate run: coasts within it — the spot's own
# shore, a coast across a bay/strait, Point Conception ~92 km off Rincon — stay solid and
# keep the gate spots bounded; the distant-mainland grazes (Baja/Mexico at 250-2900 km)
# that dominated the too-narrow windows wrap open instead. The wrap machinery graduates
# the demoted set further by distance + subtend, so a broad near-ish coast keeps its core.
SWELL_MAINLAND_SOLID_KM = 100.0
# Sub-threshold islands cast a *partial* shadow. Adjacent island shadows
# separated by a gap narrower than the bridge merge into one "chain" (swell
# can't squeeze a useful amount of energy through a narrow slot between
# islands), and the merged shadow is trimmed inward by a diffraction wrap-in
# on each edge. A lone small island (free water on both edges) wraps away to
# nothing; a long chain keeps its interior blocked. The wrap-in grows with
# obstacle distance (more room for energy to diffract back in behind a far
# island), making the block distance-aware.
SWELL_MIN_SHADOW_DEG = 5.0          # ignore any obstacle subtending less than this
# Extra gap-bridging is OFF by default: true island chains already merge because
# their per-bearing shadows are contiguous, while the open slots between a
# spread chain (e.g. the gaps between the northern Channel Islands as seen from
# Rincon) genuinely pass swell and should stay open. Raise this only if a finer
# land mask (GSHHG full-res, Phase 1) leaves spurious 1-step holes in a wall.
SWELL_ISLAND_GAP_BRIDGE_DEG = 0.0
SWELL_DIFFRACTION_WRAP_DEG = 16.0   # per-edge wrap-in trimmed off a small-island shadow
SWELL_DIFFRACTION_WRAP_PER_100KM = 8.0  # extra per-edge wrap-in per 100 km of obstacle distance

# Buoy regional distance caps (km). Matched against region_hint / lat+lng heuristics.
BUOY_CAP_KM = {
    "California": 150,
    "Oregon": 200,
    "Washington": 200,
    "Hawaii": 150,
    "Puerto Rico": 250,
    "Texas": 350,
    "Louisiana": 350,
    "Mississippi": 350,
    "Alabama": 350,
    "Great Lakes": 100,  # virtual region resolved by state
}
BUOY_CAP_GULF_FLORIDA = 350  # applied when FL spot's lng < -83
BUOY_CAP_DEFAULT_EAST = 150
BUOY_CAP_DEFAULT = 150

TIDE_STATION_MAX_DIST_KM = 50

# ---------------------------------------------------------------------------
# Tide preference classification (Phase 0C)
# ---------------------------------------------------------------------------
TIDE_CLASSIFY_MODEL = "claude-sonnet-4-6"
TIDE_CLASSIFY_BATCH_SIZE = 10
TIDE_CLASSIFY_CACHE_FILE = CACHE_DIR / "tide_classification.json"

# ---------------------------------------------------------------------------
# Forecast fetching (Phase 1)
# ---------------------------------------------------------------------------
NOAA_COOPS_ENDPOINT = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_COOPS_MIN_INTERVAL_S = 1.0  # polite pace for the public CO-OPS API
# Datum cascade: many subordinate and regional stations only publish one of
# these; try them in order and take the first that returns predictions.
NOAA_COOPS_DATUMS = ("MLLW", "STND", "MSL")
NDBC_REALTIME2_BASE = "https://www.ndbc.noaa.gov/data/realtime2"

FORECAST_DATA_DIR = PIPELINE_DIR / "forecast_data"
TIDES_CACHE_DIR = CACHE_DIR / "tides"
BUOYS_CACHE_DIR = CACHE_DIR / "buoys"
TIDES_FORECAST_FILE = FORECAST_DATA_DIR / "tides.json"
BUOYS_FORECAST_FILE = FORECAST_DATA_DIR / "buoys.json"

TIDE_PREDICTION_RANGE_HOURS = 168  # 7 days — the OUTPUT window (what interpret sees; unchanged so
                                   #   tide_norm/ratings are identical). The cache below is longer.

# Tide-stage resilience — a dead CO-OPS backend must never block db_import (tides are a rating
# MODIFIER, not a blocker). See pipeline/forecast/tides.py.
NOAA_COOPS_TIMEOUT_S = 6.0             # per-request socket timeout; SINGLE attempt, no backoff (Fix C).
                                       #   The old path was http.get: 4 attempts x wait_exponential
                                       #   (2+4+8s backoff) x 180s timeout x 3 datums x 2 intervals =
                                       #   ~118 s/station against a dead backend.
TIDE_FETCH_MAX_CONSECUTIVE_FAILURES = 8   # circuit breaker: after this many station failures IN A ROW,
                                       #   stop contacting NOAA for the rest of the run (Fix D).
TIDE_KNOWN_BAD_TTL_DAYS = 30           # a station CONFIRMED to have no predictions is skipped for this
                                       #   long, then re-verified — so one that comes back online (or was
                                       #   wrongly classified) recovers on its own, no CLI needed. The
                                       #   permanent verdict must not rest solely on our classification.
TIDE_STAGE_DEADLINE_S = 1200.0         # whole-stage wall-clock budget (20 min); on expiry, bail with
                                       #   what we have and mark the rest stale (Fix E). Only binds on a
                                       #   slow-but-ALIVE backend — a true outage trips the breaker in
                                       #   ~48s (8 failures x 6s). A cold start (all ~230 stations, ~2
                                       #   requests each) legitimately needs > 10 min; 20 min still sits
                                       #   well inside the 35-min fetch step + 60-min job timeouts.
# Long cache (Fix B) — harmonic predictions are DETERMINISTIC, so cache a wide horizon and reuse it:
TIDE_CACHE_HORIZON_HOURS = 720         # MAX fetch/cache horizon per station (30 days), persisted
TIDE_CACHE_HORIZON_MIN_HOURS = 600     # MIN horizon (25 days). Each station's ACTUAL horizon is a
                                       #   deterministic hash of its id in [MIN, MAX] (see
                                       #   tides._station_horizon_hours). Without it, all stations
                                       #   cold-started together expire on the SAME day (~day 23) and
                                       #   refetch in one thundering-herd run; the jitter spreads the
                                       #   refetch day across a ~5-6 day window (~40 stations/day).
TIDE_CACHE_REFETCH_WITHIN_HOURS = 168  # only refetch a station when < 7 days of its cache remain

# NWPS — Nearshore Wave Prediction System forecasts
NWPS_NOMADS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod"
NWPS_GRIB_FILTER_BASE = "https://nomads.ncep.noaa.gov/cgi-bin"
NWPS_CACHE_DIR = CACHE_DIR / "nwps"
NWPS_FORECAST_FILE = FORECAST_DATA_DIR / "nwps.json"
NWPS_CYCLE_LOOKBACK = 4  # number of (day, cycle) candidates to try, newest first
# Variables we pull through the NOMADS grib_filter for subsetting. Keeping the
# list tight drops per-WFO download from ~100–300 MB to ~30–50 MB.
NWPS_GRIB_VARS = ("HTSGW", "PERPW", "DIRPW", "SWELL", "SWPER", "SWDIR", "WIND", "WDIR")
# NWPS runs are published under a per-NWS-region tree (er/sr/wr/pr/ar), not a
# flat nwps.YYYYMMDD directory — every WFO belongs to exactly one region.
WFO_TO_REGION = {
    # Eastern Region
    "box": "er", "okx": "er", "phi": "er", "akq": "er",
    "mhx": "er", "ilm": "er", "chs": "er", "car": "er", "gyx": "er",
    # Southern Region (incl. Gulf + Puerto Rico + tropical CONUS)
    "bro": "sr", "crp": "sr", "hgx": "sr", "jax": "sr", "mlb": "sr",
    "mfl": "sr", "tbw": "sr", "key": "sr", "sju": "sr",
    "mob": "sr", "tae": "sr", "lch": "sr", "lix": "sr",
    # Western Region
    "sgx": "wr", "lox": "wr", "mtr": "wr", "eka": "wr",
    "mfr": "wr", "pqr": "wr", "sew": "wr",
    # Pacific Region
    "hfo": "pr", "gum": "pr",
    # Alaska Region
    "afc": "ar", "ajk": "ar", "alu": "ar",
}

# ---------------------------------------------------------------------------
# WAVEWATCH III (NCEP gfswave) — global wave model with full directional
# spectra. Unlike NWPS (which only publishes total + 1 swell magnitude),
# gfswave publishes 3 swell partitions (height + period + direction) plus
# wind sea — exactly what surfers see when Surfline / MagicSeaweed list
# multiple swell components. We use it as the source of truth for swell
# direction / period; NWPS still drives nearshore Hs because its coastal
# refraction is finer-grained than gfswave's 0.25° global grid.
# ---------------------------------------------------------------------------

# NCEP nests gfswave inside the GFS cycle tree as of 2022:
#   /pub/data/nccf/com/gfs/prod/gfs.YYYYMMDD/HH/wave/gridded/gfswave.t{HH}z....grib2
# So the date directories we scan are gfs.YYYYMMDD/ (NOT gfswave.YYYYMMDD/),
# but the filenames inside still start with gfswave.
WW3_NOMADS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
WW3_GRIB_FILTER_BASE = "https://nomads.ncep.noaa.gov/cgi-bin"
WW3_CACHE_DIR = CACHE_DIR / "ww3"
WW3_FORECAST_FILE = FORECAST_DATA_DIR / "ww3.json"
WW3_CYCLE_LOOKBACK = 4
# Forecast horizon to bother extracting — gfswave publishes out to 384h
# but we never display past 168h, so the extra 200h is wasted IO.
WW3_MAX_FORECAST_HOURS = 168
# gfswave per-step file naming. Steps are every 1h for f000–f120 then every
# 3h thereafter. We sample every 3h (matches our display granularity and
# keeps the per-cycle download under ~50 MB after variable subset).
WW3_STEP_HOURS = tuple(range(0, WW3_MAX_FORECAST_HOURS + 1, 3))
WW3_GRID = "global.0p25"  # global 0.25° — single grid covers HI + PR + CONUS
WW3_DATE_PREFIX = "gfs"          # date directories are gfs.YYYYMMDD/
WW3_FILE_PREFIX = "gfswave"      # filenames still gfswave.tHHz....
WW3_CYCLE_SUBPATH = "wave/gridded"  # under {date}/{HH}/...
# Each gfswave file ships every wave variable; the grib_filter subsets to a
# small set so a 30 MB file becomes ~100 KB. Names use the GRIB shortName /
# gfswave naming (cfgrib's shortName comes from these).
WW3_GRIB_VARS = (
    "HTSGW", "PERPW", "DIRPW",
    "WVHGT", "WVPER", "WVDIR",
    "SWELL_1", "SWPER_1", "SWDIR_1",
    "SWELL_2", "SWPER_2", "SWDIR_2",
    "SWELL_3", "SWPER_3", "SWDIR_3",
)
# US-spanning bbox so the filter clips a tiny window of each global file.
# (lat_min, lat_max, lon_min, lon_max). Hawaii pushes lon_min west;
# Caribbean / PR pushes lon_max east. Latitudes cover South Texas to
# Aleutians.
WW3_BBOX = (15.0, 60.0, -170.0, -60.0)

# ---------------------------------------------------------------------------
# HRRR — High-Resolution Rapid Refresh (NCEP). 3 km Lambert-conformal grid
# over CONUS, run every hour. Replaces NWPS / GFS-derived wind for the
# rating because (a) 3 km resolves coastal sea breezes and topographic
# effects properly, (b) hourly cycles vs every-6 h, (c) NWPS wind
# disagreements with reality were the most reported "this rating is
# wrong" failure mode (backlog FV-1).
#
# HRRR is CONUS only — no Hawaii / Puerto Rico / Alaska. Those regions
# fall back to NWPS wind in interpret.py.
# ---------------------------------------------------------------------------

HRRR_NOMADS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod"
HRRR_GRIB_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_hrrr_2d.pl"
HRRR_CACHE_DIR = CACHE_DIR / "hrrr"
HRRR_FORECAST_FILE = FORECAST_DATA_DIR / "hrrr.json"
HRRR_CYCLE_LOOKBACK = 4
# HRRR cycles 00 / 06 / 12 / 18 Z run out to 48 forecast hours; off-cycle
# hours only go to 18 h. We always pick from the long-horizon set so the
# fetched window covers the next two days end-to-end.
HRRR_LONG_CYCLES = ("00", "06", "12", "18")
HRRR_MAX_FORECAST_HOURS = 48
HRRR_STEP_HOURS = tuple(range(0, HRRR_MAX_FORECAST_HOURS + 1))
# Variables: 10 m above-ground U/V wind components. We post-process to
# speed + meteorological direction; the rater never sees the raw U/V.
HRRR_GRIB_VARS = ("UGRD", "VGRD")
HRRR_GRIB_LEVEL = "lev_10_m_above_ground"
# CONUS bbox used to skip non-CONUS spots at extract time. The HRRR
# Lambert grid extends further (roughly 21–53 N, –134 to –60 W) but we
# clip to the conservative interior so spots near the edge don't pick
# spurious cells.
HRRR_CONUS_BBOX = (22.0, 50.0, -130.0, -65.0)

# ---------------------------------------------------------------------------
# Interpretation (Phase 2) — surf rating composite
# ---------------------------------------------------------------------------
RATINGS_FILE = FORECAST_DATA_DIR / "ratings.json"

# ---------------------------------------------------------------------------
# Spot verification (Phase 2B) — LLM cross-check of metadata
# ---------------------------------------------------------------------------
SPOT_VERIFY_MODEL = "claude-sonnet-4-6"
# With web_search enabled each request carries search-result context, which
# is charged as input tokens and inflates requests past the 30K input-token
# per-minute rate limit at batch size 5. Batch of 2 keeps each request
# smaller and lets the inter-batch sleep keep us under the TPM ceiling.
SPOT_VERIFY_BATCH_SIZE = 2
# Seconds to sleep between batches. 15s × 30 RPM-equivalent = 2 req/m →
# well under the input-TPM cap even with large search-result payloads.
# Raise to 30s if you still see 429s.
SPOT_VERIFY_INTER_BATCH_SECONDS = 15.0
# On a 429 (rate-limit) response, wait this long and retry the same batch
# rather than skipping it. Retries capped at SPOT_VERIFY_MAX_RETRIES.
SPOT_VERIFY_RETRY_BACKOFF_SECONDS = 60.0
SPOT_VERIFY_MAX_RETRIES = 3
SPOT_VERIFICATION_FILE = PIPELINE_DIR / "data" / "spot_verification.json"

# Manually-curated data files that carry cleanup decisions across runs. The
# exclusion list is consulted at seed time so removed spots don't come back
# on subsequent crawls.
EXCLUDED_SPOTS_FILE = PIPELINE_DIR / "data" / "excluded_spots.json"
SPOT_COORD_FIXES_FILE = PIPELINE_DIR / "data" / "spot_coord_fixes.json"
# Hand-curated orientations for spots where the geometric algorithm fails
# (Great Lakes — GSHHG L1 treats lakes as land; complex harbors; jetties;
# barrier islands that geocoded to the bay side). Applied by enrich.py and
# treated as authoritative — overrides algorithm and LLM verification.
MANUAL_ORIENTATIONS_FILE = PIPELINE_DIR / "data" / "manual_orientations.json"
# Slug-keyed orientation overrides — the comprehensive human review pass.
# Applied by enrich.py AFTER MANUAL_ORIENTATIONS_FILE, so a slug match here
# is authoritative over both the geometric algorithm and the name-keyed
# legacy file. Same role for orientation that SPOT_COORD_FIXES_FILE plays
# for lat/lng: a committed override that survives re-enrich.
SPOT_ORIENTATIONS_FILE = PIPELINE_DIR / "data" / "spot_orientations.json"
# Persistent review queue — list of spots whose orientation/scrape/verification
# state suggests they should get a manual eyeball at some point. Survives
# regeneration: spots marked `reviewed: true` keep that flag.
REVIEW_QUEUE_FILE = PIPELINE_DIR / "data" / "review_queue.json"

# ---------------------------------------------------------------------------
# surf-forecast.com scrape (Phase 2C) — direct HTML extraction
# ---------------------------------------------------------------------------
SURF_FORECAST_BASE = "https://www.surf-forecast.com"
# surf-forecast.com doesn't publish an explicit crawl rate, so pace politely
# at 1 request / 2 s. Mirrors the NOAA CO-OPS pacing pattern.
SURF_FORECAST_MIN_INTERVAL_S = 2.0
SURF_FORECAST_CACHE_FILE = PIPELINE_DIR / "data" / "surf_forecast_scrape.json"
# Crawled directory of every /breaks/<slug> link surf-forecast.com publishes.
# Built once via `scrape_surf_forecast --build-directory` and reused across
# every subsequent scrape run to catch spots whose naive slug candidates
# would 404 (e.g. "Ocean Beach San Diego" → /breaks/Ocean-Beach-San-Diego).
SURF_FORECAST_DIRECTORY_FILE = CACHE_DIR / "surf_forecast_directory.json"
# rapidfuzz token_set_ratio threshold for directory lookups. 85 is strict
# enough to reject "Rincon" ↔ "Rincon Point" differences (they'd score
# around 80) while still catching "Pipeline" ↔ "Banzai Pipeline".
SURF_FORECAST_FUZZY_THRESHOLD = 85
