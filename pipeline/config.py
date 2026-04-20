"""Pipeline constants: endpoints, user agent, rate limits, thresholds."""
from pathlib import Path

USER_AGENT = "StormyPetrel-Pipeline/0.1 (+https://stormypetrel.surf)"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

WIKIPEDIA_API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_ROOT_CATEGORY = "Category:Surfing_locations_in_the_United_States"
WIKIPEDIA_MAX_CATEGORY_DEPTH = 4
WIKIPEDIA_PAGES_PER_BATCH = 50
WIKIPEDIA_MIN_INTERVAL_S = 0.2  # ~5 req/s

DEDUPE_DISTANCE_M = 500.0
DEDUPE_NAME_SCORE = 85

SOURCE_PRIORITY = ("wikidata", "wikipedia", "osm")

PIPELINE_DIR = Path(__file__).resolve().parent
CACHE_DIR = PIPELINE_DIR / "cache"
DEFAULT_OUTPUT = PIPELINE_DIR / "spots_seed.json"
