"""The buoy rule's regional cap (enrichment/buoys._regional_cap_km): New York has two coasts.

Until 2026-11-02 every New York spot north of 40 N and west of 70 W took the Great Lakes cap,
100 km, so the whole Atlantic coast of the state did: the city beaches, Long Island, Montauk.
Mecox Beach's nearest buoy, 44097 at 100.89 km, fell just outside it (sw1-raycast run 64). Only
New York's lake shore, at or north of 42.0 N, takes the lake cap now; the rest take the Atlantic
default, 150 km. Every other state is unchanged.

The latitudes, by hand: New York's Lake Erie shore starts at Barcelona, 42.3 N; Lake Ontario's
runs 43.2-43.5 N; the Atlantic coast runs from 40.5 N (Breezy Point) to 41.3 N (Fishers Island).
"""
from __future__ import annotations

import json

import pytest

from pipeline import config
from pipeline.enrichment.buoys import _regional_cap_km


def cap(region, lat, lng):
    return _regional_cap_km({"region_hint": region, "lat": lat, "lng": lng})


@pytest.mark.parametrize("region, lat, lng, want", [
    ("New York", 40.893, -72.320, 150),      # Mecox Beach
    ("New York", 41.071, -71.855, 150),      # Montauk Point
    ("New York", 40.542, -73.938, 150),      # Breezy Point
    ("New York", 41.99, -73.0, 150),         # just south of the line
    ("New York", 42.0, -79.5, 100),          # on the line: the lake cap
    ("New York", 42.886, -78.878, 100),      # Buffalo, Lake Erie
    ("New York", 43.46, -76.51, 100),        # Oswego, Lake Ontario
    ("New York", 40.0, -73.0, 150),          # not north of 40: never the lakes (unchanged)
    ("New York", 41.0, -69.9, 150),          # east of 70 W: never the lakes (unchanged)
    ("Pennsylvania", 42.13, -80.09, 100),    # Erie: unchanged
    ("Ohio", 41.5, -81.7, 100),              # Cleveland, south of 42 N: still the lakes
    ("Michigan", 41.8, -86.6, 100),          # unchanged
    ("New Jersey", 40.4, -74.0, 150),        # the Atlantic default, unchanged
    ("Florida", 30.39, -86.5, 350),          # Destin: Florida west of 83 W, unchanged
    ("Florida", 27.3, -82.6, 150),           # Sarasota, east of 83 W: the default, unchanged
    ("Puerto Rico", 18.4, -67.2, 250),       # unchanged
])
def test_the_cap(region, lat, lng, want):
    assert cap(region, lat, lng) == want


def test_every_new_york_spot_on_the_roster_takes_the_atlantic_cap():
    roster = json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())
    ny = [s for s in roster if s.get("region_hint") == "New York"]
    assert len(ny) == 30
    assert {_regional_cap_km(s) for s in ny} == {150}
