#!/usr/bin/env bash
# Download the geodata files used by pipeline.enrichment.
#
# Idempotent: any file already present is skipped. Used by:
#   - The GH Actions full-pipeline cron (after restoring the geodata
#     cache, this fills in any missing files)
#   - Local enrichment runs (one-off bootstrap on a fresh checkout)
#
# Note: the *forecast* cron (fetch_all + interpret + db_import) does NOT
# need these files — they're consumed only by the enrichment step that
# produces spots_enriched.json. Keep them around so a future enrichment
# re-run doesn't redownload.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
GEODATA_DIR="${GEODATA_DIR:-$SCRIPT_DIR/geodata}"
mkdir -p "$GEODATA_DIR"

# --refresh-roster (a.k.a. --force-roster): dl_if_missing SKIPS a file that already exists, so a
# stale ndbc_stations.xml / latest_obs.txt never updates on a plain re-run (the roster ages out and
# the trust gate warns it's N days old). This flag force-refetches JUST the two NDBC metadata files;
# every other download keeps its idempotent skip-if-present behavior.
REFRESH_ROSTER=0
for _arg in "$@"; do
    case "$_arg" in
        --refresh-roster|--force-roster) REFRESH_ROSTER=1 ;;
    esac
done

dl_if_missing() {
    local url="$1"
    local dest="$2"
    if [[ -s "$dest" ]]; then
        echo "[skip] $dest (already present)"
        return 0
    fi
    echo "[get ] $url"
    echo "       -> $dest"
    curl -fsSL --retry 4 --retry-delay 5 -o "$dest" "$url"
}

dl_force() {   # unconditional fetch — used only by --refresh-roster for the NDBC roster metadata
    local url="$1"
    local dest="$2"
    echo "[get!] $url (force refresh)"
    echo "       -> $dest"
    curl -fsSL --retry 4 --retry-delay 5 -o "$dest" "$url"
}

# --- NDBC station metadata + latest observations ---------------------------
# With --refresh-roster, force-refetch these two first; the dl_if_missing calls below then skip
# them (now present & fresh). Without the flag, behavior is unchanged (fetch only if missing).
if [[ "$REFRESH_ROSTER" == "1" ]]; then
    dl_force "https://www.ndbc.noaa.gov/activestations.xml"             "$GEODATA_DIR/ndbc_stations.xml"
    dl_force "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt" "$GEODATA_DIR/ndbc_latest_obs.txt"
fi

dl_if_missing \
    "https://www.ndbc.noaa.gov/activestations.xml" \
    "$GEODATA_DIR/ndbc_stations.xml"

dl_if_missing \
    "https://www.ndbc.noaa.gov/data/latest_obs/latest_obs.txt" \
    "$GEODATA_DIR/ndbc_latest_obs.txt"

# --- NOAA CO-OPS tide-prediction station list -----------------------------
dl_if_missing \
    "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions" \
    "$GEODATA_DIR/tide_stations.json"

# --- GSHHG L1 (full-resolution global shorelines) -------------------------
# ~150 MB zipped; we only keep the L1 (continental) shapefile.
#
# *Best-effort*: the FORECAST pipeline (fetch_all + interpret + db_import)
# doesn't read GSHHG at all — it's only consumed by pipeline.enrich. If
# every mirror is down we log a warning and continue so the forecast cron
# isn't blocked by a third-party CDN outage.
GSHHG_VER="${GSHHG_VER:-2.3.7}"
GSHHG_ZIP="$GEODATA_DIR/gshhg-shp-${GSHHG_VER}.zip"
GSHHG_SHP="$GEODATA_DIR/GSHHS_f_L1.shp"

# Mirror order: SOEST (the original publisher, very stable) first, then
# the older NGDC oldversions/ path, then NGDC's "latest" symlink. The
# `latest/` URL was the original choice but NOAA stopped 301-redirecting
# it to 2.3.7 sometime in 2025 — every cron had been 404'ing on the
# zip download.
GSHHG_URLS=(
    "https://www.soest.hawaii.edu/pwessel/gshhg/gshhg-shp-${GSHHG_VER}.zip"
    "https://www.ngdc.noaa.gov/mgg/shorelines/data/gshhg/oldversions/version${GSHHG_VER}/gshhg-shp-${GSHHG_VER}.zip"
    "https://www.ngdc.noaa.gov/mgg/shorelines/data/gshhg/latest/gshhg-shp-${GSHHG_VER}.zip"
)

if [[ ! -s "$GSHHG_SHP" ]]; then
    echo "[get ] GSHHG L1 shapefile v${GSHHG_VER} (~150 MB)"
    GSHHG_OK=0
    for url in "${GSHHG_URLS[@]}"; do
        echo "       trying $url"
        if curl -fsSL --retry 4 --retry-delay 5 -o "$GSHHG_ZIP" "$url"; then
            GSHHG_OK=1
            break
        fi
        echo "       (failed, trying next mirror)"
    done

    if [[ "$GSHHG_OK" == "1" ]]; then
        # Different mirrors use different zip layouts: NGDC nests under
        # "gshhg-shp-<ver>/GSHHS_shp/f/", SOEST uses "GSHHS_shp/f/" with
        # no version prefix. Extract to a temp dir and use `find` to
        # locate the L1 files regardless of structure.
        EXTRACT_DIR="$(mktemp -d)"
        if unzip -q -o "$GSHHG_ZIP" -d "$EXTRACT_DIR"; then
            FOUND=0
            while IFS= read -r -d '' f; do
                cp -f "$f" "$GEODATA_DIR/"
                FOUND=1
            done < <(find "$EXTRACT_DIR" -type f -name 'GSHHS_f_L1.*' -print0)
            if [[ "$FOUND" == "1" ]]; then
                echo "[ok  ] extracted GSHHS_f_L1.* to $GEODATA_DIR"
            else
                echo "WARNING: zip extracted but GSHHS_f_L1.* not found inside —"
                echo "         layout may have changed again. Continuing without it."
            fi
        else
            echo "WARNING: unzip failed on $GSHHG_ZIP. Continuing without GSHHG."
        fi
        rm -rf "$EXTRACT_DIR" "$GSHHG_ZIP"
    else
        echo "WARNING: every GSHHG mirror failed. Skipping — the forecast"
        echo "         pipeline doesn't consume this file, only enrichment"
        echo "         does. Re-run pipeline/download_geodata.sh once a"
        echo "         mirror comes back online if you need to re-enrich."
    fi
fi

echo
echo "geodata ready in $GEODATA_DIR"
ls -lh "$GEODATA_DIR"
