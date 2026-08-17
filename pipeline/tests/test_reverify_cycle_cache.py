"""Reverify performance + coverage: the per-process cycle cache, and the zone rotation.

TWO DEFECTS, both in what reverify_tagged costs and covers rather than in any verdict.

  CACHE — trust_check loads n_cycles CG1 cycles per ZONE, and zones share a WFO (mhx 4,
      sgx 4, lox 3), so the same (wfo, date, cycle) was fetched AND REWRITTEN once per zone.
      The rewrite is the expensive half: it left cfgrib's sidecar .idx older than the GRIB, so
      every zone paid a full re-scan — the hundreds of "Ignoring index file ... older than GRIB
      file" lines. load_cycle now caches per process on (wfo, date, cycle).

  ROTATION — 57 zones at ~6.5 min each in CI against a 40-minute cap means the run is
      cancelled around zone 15 EVERY time. With a fixed alphabetical order that is always the
      same 15, so mfl onward had never banked an event. rotate_zones moves only the START
      index; the order stays sorted and iteration wraps.

Neither test touches the network, cfgrib or the DB: the cache test stubs _http_get and
_parse_cycle_file, which is why load_cycle's parse was split out.

Run: python -m pipeline.tests.test_reverify_cycle_cache   (or pytest)
"""
from __future__ import annotations

import os
import tempfile

from pipeline.forecast import nwps_nearshore as nn


# --------------------------------------------------------------------------- #
# Change 1 — the per-process cycle cache                                       #
# --------------------------------------------------------------------------- #
class _Counter:
    """Stand-in for _http_get / _parse_cycle_file that records every call."""

    def __init__(self, ret):
        self.calls, self._ret = [], ret

    def __call__(self, *a, **kw):
        self.calls.append(a)
        return self._ret


def _with_stubs(fn):
    """Run fn with _http_get and _parse_cycle_file stubbed and the cache reset, always
    restoring the module globals. /tmp writes go to a scratch dir via a patched os.path.join
    target — load_cycle's path format is unchanged, we just point it at a temp file."""
    saved_get, saved_parse = nn._http_get, nn._parse_cycle_file
    nn.reset_cycle_cache()
    try:
        return fn()
    finally:
        nn._http_get, nn._parse_cycle_file = saved_get, saved_parse
        nn.reset_cycle_cache()


def test_cycle_cache_serves_a_repeat_request_without_a_second_download():
    """THE FIX. The same (wfo, date, cycle) requested twice downloads ONCE."""
    def body():
        parsed = {"lats": None, "lons": None, "mask": None,
                  "cycle_dt": None, "steps": [], "fields": {}}
        get = _Counter(b"GRIB" + b"\x00" * 32)
        parse = _Counter(parsed)
        nn._http_get, nn._parse_cycle_file = get, parse
        cyc = ("20260816", "12", "https://example.invalid/mhx.grib2")

        first = nn.load_cycle("mhx", cyc)
        second = nn.load_cycle("mhx", cyc)
        third = nn.load_cycle("mhx", cyc)

        assert len(get.calls) == 1, f"downloaded {len(get.calls)} times; the cache must serve repeats"
        assert len(parse.calls) == 1, f"parsed {len(parse.calls)} times; a parsed hit must not re-parse"
        assert first is second is third, "a cached hit returns the same parsed dict"
        s = nn.cycle_cache_stats()
        assert s["requested"] == 3 and s["downloaded"] == 1, s
        assert s["saved"] == 2 and s["parse_reused"] == 2, s

        # a DIFFERENT cycle of the same wfo is a different key and does download
        nn.load_cycle("mhx", ("20260816", "18", "https://example.invalid/mhx18.grib2"))
        assert len(get.calls) == 2, "a different cycle must not be served from another key"
        # a different WFO likewise
        nn.load_cycle("sgx", ("20260816", "12", "https://example.invalid/sgx.grib2"))
        assert len(get.calls) == 3, "the key includes the wfo"
        assert nn.cycle_cache_stats()["downloaded"] == 3
    _with_stubs(body)


def test_file_reuse_skips_the_rewrite_that_invalidated_the_index():
    """When the parsed entry has been evicted but this process already wrote the GRIB, the
    file is reused: no download AND no rewrite, which is what keeps the sidecar index valid."""
    def body():
        get = _Counter(b"GRIB" + b"\x00" * 32)
        nn._http_get = get
        with tempfile.TemporaryDirectory() as tmp:
            written = []

            def parse(path, url, date, cc):
                written.append(os.path.getmtime(path))
                return {"fields": {}, "steps": []}
            nn._parse_cycle_file = parse
            saved_max = nn._CYCLE_CACHE_MAX
            nn._CYCLE_CACHE_MAX = 1          # force eviction after the next load
            try:
                a = ("20260816", "12", "https://example.invalid/a.grib2")
                b = ("20260816", "18", "https://example.invalid/b.grib2")
                nn.load_cycle("mhx", a)      # downloads + parses, cached
                nn.load_cycle("mhx", b)      # evicts a's parsed entry
                nn.load_cycle("mhx", a)      # parsed entry gone, but the FILE is ours
            finally:
                nn._CYCLE_CACHE_MAX = saved_max
            assert len(get.calls) == 2, \
                f"a re-parse must not re-download: {len(get.calls)} downloads for 2 distinct cycles"
            s = nn.cycle_cache_stats()
            assert s["file_reused"] == 1, f"expected one file-reuse, got {s}"
            assert s["downloaded"] == 2 and s["requested"] == 3, s
            assert written[0] == written[2], \
                "the GRIB was REWRITTEN on the reuse — that is what invalidates cfgrib's index"
            del tmp
    _with_stubs(body)


def test_a_failed_parse_is_not_cached():
    """Nothing is remembered until the parse succeeds, so a truncated body is retried."""
    def body():
        get = _Counter(b"GRIB" + b"\x00" * 32)
        nn._http_get = get
        calls = {"n": 0}

        def parse(path, url, date, cc):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("cfgrib produced no datasets")
            return {"fields": {}}
        nn._parse_cycle_file = parse
        cyc = ("20260816", "12", "https://example.invalid/x.grib2")
        try:
            nn.load_cycle("mhx", cyc)
            raise AssertionError("the first load should have raised")
        except OSError:
            pass
        nn.load_cycle("mhx", cyc)            # retried, not served from a poisoned cache
        assert len(get.calls) == 2, "a failed parse must not mark the key as cached"
    _with_stubs(body)


def test_non_grib_body_still_raises_and_caches_nothing():
    def body():
        nn._http_get = _Counter(b"<html>404</html>")
        nn._parse_cycle_file = _Counter({"fields": {}})
        try:
            nn.load_cycle("mhx", ("20260816", "12", "https://example.invalid/x.grib2"))
            raise AssertionError("a non-GRIB body must raise")
        except OSError as e:
            assert "not GRIB" in str(e)
        assert nn.cycle_cache_stats()["downloaded"] == 1, \
            "a fetched-but-invalid body still cost the network; count it or the "\
            "savings line overstates the cache"
        assert not nn._CYCLE_FILES, "a non-GRIB body must not be recorded as written"
    _with_stubs(body)


# --------------------------------------------------------------------------- #
# Change 2 — the zone rotation                                                 #
# --------------------------------------------------------------------------- #
def _zones(n):
    return [(f"w{i:02d}", f"b{i:02d}", i) for i in range(n)]


def test_rotation_visits_every_zone_exactly_once_and_starts_at_the_offset():
    """THE FIX. A full pass is a permutation — every zone once, none twice — and it begins
    where the offset says. Fixed order + moving start is what makes a capped run fair."""
    zones = _zones(57)                       # the live roster size
    for off in (0, 1, 14, 15, 28, 56):
        out = nn.rotate_zones(zones, off)
        assert len(out) == len(zones), f"offset {off}: length changed {len(out)} != {len(zones)}"
        assert set(out) == set(zones), f"offset {off}: not the same zone set"
        assert len(set(out)) == len(out), f"offset {off}: a zone appears twice"
        assert out[0] == zones[off], f"offset {off}: started at {out[0]} not {zones[off]}"
        assert out[-1] == zones[off - 1], f"offset {off}: did not wrap to the element before the start"
    # order is preserved apart from the rotation — consecutive runs cover different windows
    assert nn.rotate_zones(zones, 0) == zones, "offset 0 is the identity (Mac runs unchanged)"
    assert nn.rotate_zones(zones, 15)[:3] == zones[15:18]


def test_rotation_wraps_past_the_end_and_survives_odd_offsets():
    zones = _zones(57)
    assert nn.rotate_zones(zones, 57) == zones, "a full turn is the identity"
    assert nn.rotate_zones(zones, 58) == nn.rotate_zones(zones, 1), "offsets wrap modulo len"
    assert nn.rotate_zones(zones, 1000)[0] == zones[1000 % 57], "a large run number still lands in range"
    assert nn.rotate_zones(zones, -1) == nn.rotate_zones(zones, 56), "negative offsets wrap too"
    assert nn.rotate_zones(zones, None) == zones, "None behaves as 0"
    assert nn.rotate_zones([], 5) == [], "an empty roster does not divide by zero"


def test_consecutive_run_numbers_cover_the_roster_over_several_runs():
    """The coverage property the workflow relies on: with a window of W zones per capped run,
    consecutive run numbers reach every zone. This is what mfl-onward never got."""
    zones = _zones(57)
    window = 15                              # what the 40-minute cap reached before the cache
    seen = set()
    for run_number in range(1, 1 + (len(zones) // window) + 1):
        seen.update(nn.rotate_zones(zones, run_number * window)[:window])
    assert len(seen) == len(zones), f"only {len(seen)}/{len(zones)} zones covered across the runs"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} reverify cache/rotation checks passed")


if __name__ == "__main__":
    _run_all()
