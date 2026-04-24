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

TIDE_PREDICTION_RANGE_HOURS = 168  # 7 days

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

# ---------------------------------------------------------------------------
# surf-forecast.com scrape (Phase 2C) — direct HTML extraction
# ---------------------------------------------------------------------------
SURF_FORECAST_BASE = "https://www.surf-forecast.com"
# surf-forecast.com doesn't publish an explicit crawl rate, so pace politely
# at 1 request / 2 s. Mirrors the NOAA CO-OPS pacing pattern.
SURF_FORECAST_MIN_INTERVAL_S = 2.0
SURF_FORECAST_CACHE_FILE = PIPELINE_DIR / "data" / "surf_forecast_scrape.json"
