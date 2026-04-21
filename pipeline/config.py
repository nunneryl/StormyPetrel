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
