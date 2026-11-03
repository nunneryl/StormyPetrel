"""Check NDBC's station list and latest observations, as downloaded for a full sw1-raycast run,
before they replace the cached copies.

    python -m pipeline.ndbc_fresh --xml activestations.xml --obs latest_obs.txt

WHY. sw1-raycast full mode (then_enrich) downloads both files and enrich picks every spot's buoy
from them. curl -f catches a failed download, but not a 200 that carries the wrong thing: an
empty body, a maintenance page, a cut-off file. A broken activestations.xml crashes the next
step with a bare traceback; a broken latest_obs.txt is read as "no station reports a wave
height", and enrich would carry on and push a review branch. Exits 1, with one ::error:: line
per problem, when either file does not look like NDBC's.

The floors sit well under what NDBC serves: on 2026-11-03 activestations.xml listed 1352
stations, all with coordinates, and latest_obs.txt had 784 station rows. Only the structure is
checked, not how many stations report a wave height: on 2026-11-02 NDBC's own buoys gave none
for hours, and the 7-day list (pipeline/ndbc_recent_wave.py) is there for that.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET

MIN_STATIONS = 500      # stations with coordinates in activestations.xml
MIN_OBS_ROWS = 200      # station rows in latest_obs.txt


def check(xml_path, obs_path, min_stations=MIN_STATIONS, min_rows=MIN_OBS_ROWS):
    """A list of problems, empty when both files look like NDBC's."""
    problems = []
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as e:
        problems.append(f"activestations.xml is not readable XML ({type(e).__name__}: {e})")
    else:
        n = 0
        for el in root.iter("station"):
            try:
                float(el.get("lat")), float(el.get("lon"))
            except (TypeError, ValueError):
                continue
            n += 1
        if n < min_stations:
            problems.append(f"activestations.xml lists {n} stations with coordinates "
                            f"(at least {min_stations} expected)")
    try:
        with open(obs_path, encoding="utf-8", errors="replace") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError as e:
        problems.append(f"latest_obs.txt is not readable ({type(e).__name__}: {e})")
        return problems
    header = lines[0].lstrip("#").split() if lines and lines[0].startswith("#") else []
    if "STN" not in header or "WVHT" not in header:
        problems.append("latest_obs.txt has no header row naming STN and WVHT")
        return problems
    rows = sum(1 for ln in lines if not ln.startswith("#") and len(ln.split()) >= len(header))
    if rows < min_rows:
        problems.append(f"latest_obs.txt has {rows} station rows (at least {min_rows} expected)")
    return problems


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--xml", required=True)
    p.add_argument("--obs", required=True)
    a = p.parse_args(argv)
    problems = check(a.xml, a.obs)
    for msg in problems:
        print(f"::error::NDBC {msg}.")
    if problems:
        return 1
    print("ndbc_fresh: both NDBC files look right")
    return 0


if __name__ == "__main__":
    sys.exit(main())
