"""Fixture checks for scripts/archive_partitions.py — the P3-2 partition archive.

The archiver's fetch path needs NOMADS + eccodes + NDBC, so the parts that decide WHAT gets
stored are pure functions and are tested here against a synthetic cycle and a synthetic
spectral read. Two properties matter most and are both covered:

  * the ROW SHAPE — every row carries the same key set whichever source it came from, the
    raw tracked-system list survives unmodified, and NaN never reaches a row (it is not
    valid JSON and PostgREST rejects it);
  * IDEMPOTENCE against the unique key (valid_hour, wfo, buoy_id, source, lead_hours) — a
    repeated write must leave one row per key, not two, while a DIFFERENT lead for the same
    valid hour must add a row rather than overwrite one. Verified against a fake client that
    enforces the constraint, rather than assumed from the presence of on_conflict.

Run: python3 -m pipeline.tests.test_archive_partitions   (or pytest)
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import math
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "archive_partitions",
    Path(__file__).resolve().parent.parent.parent / "scripts" / "archive_partitions.py")
ap = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ap)

# A synthetic hour and a synthetic CG0_Trkng system list, shaped like the real reader's
# output (nwps_trkng.trkng_systems_at): system index, hs, tp, dir.
HOUR = 486_000                      # epoch-hour bucket
SYSTEMS = [
    {"system": 1, "hs": 0.44, "tp": 4.3, "dir": 200.0},
    {"system": 2, "hs": 0.13, "tp": 18.2, "dir": 273.0},
    {"system": 3, "hs": 0.05, "tp": 11.1, "dir": 190.0},
]
# A synthetic ndbc_spectral.by_hour entry and the two NDBC products the buoy row also carries.
SPECTRAL = {"hs_total": 0.62, "hs_swell": 0.31, "hs_windsea": 0.54, "swell_dir": 271.0,
            "windsea_dir": 198.0, "total_mean_dir": 214.0, "swell_frac": 0.50,
            "split_method": "fixed_cutoff", "sep_freq_used": 0.125, "n_bands": 47}
SPEC = {"swh": 0.30, "swp": 16.7, "swd": 270.0}
STD = {"hs": 0.61, "mwd": 212.0, "swell_dir": 270.0, "swell_hs": 0.30}


class FakeTable:
    """Minimal stand-in for supabase-py's table handle that ENFORCES the unique key, so a
    repeated write is a real test of idempotence rather than a call-count assertion."""

    def __init__(self, store, key_cols):
        self.store, self.key_cols = store, key_cols
        self._pending = None

    def upsert(self, rows, on_conflict=None):
        assert on_conflict is not None, "upsert must target the unique key explicitly"
        cols = tuple(c.strip() for c in on_conflict.split(","))
        assert cols == self.key_cols, f"upsert targeted {cols}, not the table's key {self.key_cols}"
        self._pending = rows
        return self

    def select(self, *_a, **_k):
        self._pending = None
        return self

    def limit(self, _n):
        return self

    def execute(self):
        for r in (self._pending or []):
            self.store[tuple(r[c] for c in self.key_cols)] = r   # replace, never duplicate
        self._pending = None
        return self


class FakeClient:
    def __init__(self):
        self.store = {}

    def table(self, name):
        assert name == ap.TABLE, name
        return FakeTable(self.store, ("valid_hour", "wfo", "buoy_id", "source", "lead_hours"))


def test_row_builders_produce_the_expected_shape():
    m = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                           lead_hours=6, swh=0.62, perpw=4.4, dirpw=201.0, shts=0.31,
                           wind_speed=3.0, wind_dir=200.0, systems=SYSTEMS)
    b = ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL, spec=SPEC, std=STD)

    # ONE key set, whichever source — so the table shape cannot drift between the two writers
    assert set(m) == set(b) == set(ap.ALL_FIELDS)
    assert tuple(ap.KEY_FIELDS) == ("valid_hour", "wfo", "buoy_id", "source", "lead_hours")

    # the key, and the ISO/timestamptz rendering of the epoch-hour bucket
    for r, src in ((m, "model"), (b, "buoy")):
        assert (r["wfo"], r["buoy_id"], r["source"]) == ("sgx", "46254", src)
        assert r["valid_hour"] == ap.valid_hour_iso(HOUR)
    dt = datetime.datetime.fromisoformat(m["valid_hour"])
    assert dt.tzinfo is not None and (dt.minute, dt.second) == (0, 0), "hour-truncated UTC"
    assert int(dt.timestamp()) // 3600 == HOUR, "round-trips back to the epoch-hour bucket"

    # model row: the CG1 fields, the cycle identity, and the lead
    assert (m["cycle_date"], m["cycle_hour"], m["lead_hours"]) == ("20260810", "12", 6)
    assert m["trkng_status"] == ap.TRKNG_OK, "a read Trkng file is recorded as such"
    assert (m["swh"], m["perpw"], m["dirpw"], m["shts"]) == (0.62, 4.4, 201.0, 0.31)
    assert (m["wind_speed"], m["wind_dir"]) == (3.0, 200.0)
    assert all(m[f] is None for f in ap.BUOY_FIELDS), "model row must not carry buoy columns"

    # THE RAW SYSTEM LIST, UNMODIFIED — not a selected system. This is the whole point of the
    # archive: the analysis compares selection RULES, so storing a selection would destroy it.
    assert m["systems"] == SYSTEMS and len(m["systems"]) == 3
    assert [s["system"] for s in m["systems"]] == [1, 2, 3], "order preserved"
    assert all({"hs", "tp", "dir"} <= set(s) for s in m["systems"])
    assert json.loads(json.dumps(m["systems"])) == SYSTEMS, "jsonb-serializable"

    # buoy row: the eight spectral fields plus the five independent NDBC reference fields
    assert (b["hs_total"], b["hs_swell"], b["hs_windsea"]) == (0.62, 0.31, 0.54)
    assert (b["swell_dir"], b["windsea_dir"], b["total_mean_dir"]) == (271.0, 198.0, 214.0)
    assert (b["swell_frac"], b["split_method"]) == (0.50, "fixed_cutoff")
    assert (b["spec_swh"], b["spec_swp"], b["spec_swd"]) == (0.30, 16.7, 270.0)
    assert (b["spec_wvht"], b["spec_mwd"]) == (0.61, 212.0), ".txt WVHT and MWD"
    assert all(b[f] is None for f in ap.MODEL_FIELDS), "buoy row must not carry model columns"

    # a partial hour is still recorded, with Nones — never dropped
    thin = ap.build_buoy_row("sgx", "46254", HOUR, spectral=None, spec=None, std=None)
    assert set(thin) == set(ap.ALL_FIELDS) and thin["source"] == "buoy"
    assert all(thin[f] is None for f in ap.BUOY_FIELDS)

    # NaN/inf must never reach a row — not valid JSON, and PostgREST rejects it
    nanny = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                               lead_hours=0, swh=float("nan"), dirpw=float("inf"),
                               systems=[{"system": 1, "hs": float("nan"), "tp": 9.0, "dir": 10.0}])
    assert nanny["swh"] is None and nanny["dirpw"] is None
    assert nanny["systems"][0]["hs"] is None and nanny["systems"][0]["tp"] == 9.0
    assert not any(isinstance(v, float) and math.isnan(v) for v in nanny.values())
    json.dumps(nanny)                                    # would raise on a stray NaN

    # only pending zones WITH a buoy are archived
    doc = {"buoy_reference": {
        "pending": [{"zone": "sgx/46254", "wfo": "sgx", "buoy": "46254"},
                    {"zone": "x/none", "wfo": "x"},
                    {"zone": "y/1", "buoy": "1"}],
        "unverifiable": [{"zone": "tae/florida-panhandle-no-reference", "wfo": "tae"}]}}
    assert ap.pending_zones(doc) == [("sgx", "46254", "sgx/46254")]


def test_repeated_write_is_idempotent_against_the_unique_key():
    """A re-run must leave ONE row per (valid_hour, wfo, buoy_id, source, lead_hours), while a
    DIFFERENT lead for the same valid hour must ADD a row — that is what keeping lead_hours in
    the key buys, and losing it is unrecoverable past NOMADS' five-day retention. The fake
    client enforces the constraint, so this fails if the upsert key is wrong or a plain insert
    is used — it is not a call-count assertion."""
    client = FakeClient()
    rows = [
        ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                           lead_hours=6, swh=0.62, systems=SYSTEMS),
        ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL, spec=SPEC, std=STD),
    ]
    assert ap.upsert_rows(client, rows) == 2
    assert len(client.store) == 2, "one model row and one buoy row"

    # the same run again — still two rows, and the values are unchanged
    assert ap.upsert_rows(client, rows) == 2, "the call still reports what it wrote"
    assert len(client.store) == 2, "a repeat must REPLACE, not duplicate"
    snapshot = json.dumps(sorted(map(str, client.store)), sort_keys=True)
    ap.upsert_rows(client, rows)
    assert json.dumps(sorted(map(str, client.store)), sort_keys=True) == snapshot

    # THE POINT OF THE KEY CHANGE: a later cycle forecasting the SAME valid hour at a
    # DIFFERENT lead is a different row and must be KEPT, not overwrite the longer lead.
    fresher = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="18",
                                 lead_hours=0, swh=0.71, systems=SYSTEMS)
    ap.upsert_rows(client, [fresher])
    assert len(client.store) == 3, "a different lead is a different key — kept, not replaced"
    by_lead = {k[4]: v for k, v in client.store.items() if k[3] == "model"}
    assert set(by_lead) == {0, 6} and by_lead[0]["swh"] == 0.71 and by_lead[6]["swh"] == 0.62

    # re-writing that SAME lead still replaces in place, so re-runs stay idempotent
    ap.upsert_rows(client, [fresher])
    assert len(client.store) == 3, "same full key → replaced, not added"

    # a DIFFERENT hour, wfo, buoy or source is likewise a different key and must add a row
    for extra in (ap.build_model_row("sgx", "46254", HOUR + 1, cycle_date="20260810",
                                     cycle_hour="18", lead_hours=1),
                  ap.build_model_row("pqr", "46254", HOUR, cycle_date="20260810",
                                     cycle_hour="18", lead_hours=0),
                  ap.build_model_row("sgx", "46243", HOUR, cycle_date="20260810",
                                     cycle_hour="18", lead_hours=0)):
        before = len(client.store)
        ap.upsert_rows(client, [extra])
        assert len(client.store) == before + 1, extra["valid_hour"]

    # THE SENTINEL: buoy rows carry lead 0, never NULL — a NULL in a Postgres unique key does
    # not compare equal to another NULL, so two buoy rows for one hour would BOTH insert.
    b = ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL)
    assert b["lead_hours"] == ap.BUOY_LEAD_SENTINEL == 0 and b["lead_hours"] is not None
    # and a model row may not carry a NULL lead at all
    for bad in (None,):
        try:
            ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                               lead_hours=bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, "a NULL lead would silently defeat deduplication"

    # --dry-run writes NOTHING, while still REPORTING what it would write (returning 0 here
    # is what made the run summary read "would write 0 row(s)" — see the summary test)
    fresh = FakeClient()
    assert ap.upsert_rows(fresh, rows, dry_run=True) == len(rows)
    assert not fresh.store, "dry run must write nothing"

    # dedupe collapses EXACT-key duplicates only (a repeated chunk would otherwise make
    # PostgREST reject the whole batch), and never across leads
    a12 = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="06",
                             lead_hours=12, swh=0.5)
    a0 = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="18",
                            lead_hours=0, swh=0.71)
    for order in ([a12, a0], [a0, a12]):
        keep = ap.dedupe_by_key(order)
        assert len(keep) == 2, "different leads must BOTH survive"
        assert {r["lead_hours"] for r in keep} == {0, 12}
        # shortest-lead-wins now lives in the DATA: this is the reader's rule, not the writer's
        assert min(keep, key=lambda r: r["lead_hours"])["swh"] == 0.71
    assert len(ap.dedupe_by_key([a0, a0, a0])) == 1, "exact-key repeats collapse"
    # model and buoy rows for the same hour are DIFFERENT keys and both survive
    both = ap.dedupe_by_key([a0, ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL)])
    assert len(both) == 2 and {r["source"] for r in both} == {"model", "buoy"}


def test_trkng_status_distinguishes_absent_from_genuinely_empty():
    """An empty `systems` array is ambiguous on its own — it looks identical whether the model
    tracked no systems that hour or the cycle never published CG0_Trkng. trkng_status carries
    that distinction in the data, so the analysis does not have to guess."""
    def row(status, systems):
        return ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                                  lead_hours=0, trkng_status=status, systems=systems)

    read_and_empty = row(ap.TRKNG_OK, [])
    never_published = row(ap.TRKNG_ABSENT, [])
    unreadable = row(ap.TRKNG_ERROR, [])
    # identical systems arrays, three different meanings — only the status separates them
    assert read_and_empty["systems"] == never_published["systems"] == unreadable["systems"] == []
    assert {read_and_empty["trkng_status"], never_published["trkng_status"],
            unreadable["trkng_status"]} == {"ok", "absent", "error"}
    assert row(ap.TRKNG_OK, SYSTEMS)["systems"] == SYSTEMS

    # the default is 'ok', and an unknown status is rejected rather than written through
    assert ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                              lead_hours=0)["trkng_status"] == ap.TRKNG_OK
    for bad in ("OK", "missing", "", None, True):
        try:
            row(bad, [])
            raised = False
        except ValueError:
            raised = True
        assert raised, f"trkng_status {bad!r} must be rejected"

    # buoy rows have no Trkng at all, so the column is null there
    assert ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL)["trkng_status"] is None
    assert "trkng_status" in ap.MODEL_FIELDS and "trkng_status" not in ap.BUOY_FIELDS


def test_missing_table_fails_loudly_rather_than_silently_skipping():
    """The table is created by hand before the first run, so its absence is a setup error.
    It must stop the run with an actionable message, not degrade into 26 vague SKIP lines."""
    class Dead:
        def table(self, _n):
            raise RuntimeError('relation "partition_archive" does not exist')

    try:
        ap.ensure_table(Dead())
        raised = None
    except RuntimeError as e:
        raised = str(e)
    assert raised is not None, "a missing table must raise"
    assert ap.TABLE in raised and "--print-ddl" in raised, "the message must be actionable"
    assert ap.UPSERT_KEY in raised, "and must name the constraint the upsert needs"

    # a missing UNIQUE constraint is likewise fatal and named, not a raw driver error
    class NoConstraint:
        def table(self, _n):
            return self

        def upsert(self, _rows, on_conflict=None):
            return self

        def execute(self):
            raise RuntimeError("42P10: there is no unique or exclusion constraint matching "
                               "the ON CONFLICT specification")

    try:
        ap.upsert_rows(NoConstraint(), [ap.build_buoy_row("sgx", "46254", HOUR)])
        msg = None
    except RuntimeError as e:
        msg = str(e)
    assert msg is not None and "UNIQUE" in msg and "--print-ddl" in msg
    assert "duplicate" in msg.lower(), "the message must say what goes wrong without it"

    # and the DDL the script prints actually declares that constraint, over all five columns
    assert "unique (valid_hour, wfo, buoy_id, source, lead_hours)" in ap.DDL
    assert ", ".join(ap.KEY_FIELDS) in ap.DDL, "the DDL constraint must match UPSERT_KEY"
    # EVERY column the script writes must appear in the DDL the user applies, or the first
    # production run fails on an unknown column after the capture window has already moved on
    for col in ap.ALL_FIELDS:
        assert col in ap.DDL, f"{col} is written but absent from the DDL"
    # lead_hours is in the key, so it must be NOT NULL — a NULL there silently defeats dedupe
    assert "lead_hours     integer     not null" in ap.DDL
    assert "trkng_status   text" in ap.DDL


def test_run_summary_accumulates_rows_across_zones():
    """THE BUG THIS COVERS: the totals were accumulated from upsert_rows' return value, which
    was 0 under --dry-run, so a run whose per-zone lines each reported hundreds of rows ended
    with "would write 0 row(s) across 26 zone(s)". A scheduled run is judged on that final
    line and a real capture reporting zero reads as a silent no-op — so the summary must never
    under-report a run that captured anything."""
    total, n_zones, line = ap.render_summary([316, 316, 208], 2, dry_run=True)
    assert total == 840 and n_zones == 3, "totals must ACCUMULATE, not reset"
    assert "would write 840 row(s) across 3 zone(s); 2 skipped." in line
    # a nonzero capture can never render as zero — the exact shape of the reported bug
    assert " 0 row(s)" not in line
    # and the real-run wording differs only in tense
    _, _, wrote = ap.render_summary([316, 316, 208], 2, dry_run=False)
    assert "wrote 840 row(s) across 3 zone(s); 2 skipped." in wrote

    # an all-skipped run genuinely IS zero, and must still say so honestly
    z_total, z_n, z_line = ap.render_summary([], 26, dry_run=True)
    assert (z_total, z_n) == (0, 0) and "0 row(s) across 0 zone(s); 26 skipped." in z_line

    # upsert_rows reports the count in BOTH modes — returning 0 for a dry run is what made the
    # totals stick at zero — while still writing nothing
    client = FakeClient()
    rows = [ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                               lead_hours=6),
            ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL)]
    assert ap.upsert_rows(client, rows, dry_run=True) == 2, "dry run must report what it WOULD write"
    assert not client.store, "…while writing nothing"
    assert ap.upsert_rows(client, rows) == 2 and len(client.store) == 2
    assert ap.upsert_rows(client, [], dry_run=True) == 0


def test_lead_cap_drops_only_leads_past_the_cap():
    """Model rows past MAX_LEAD_HOURS are not captured. The boundary is INCLUSIVE ("72 or
    less"), and the cap is a named constant because raising it later cannot backfill —
    NOMADS retains ~5 days, so a lead dropped today is gone."""
    assert ap.MAX_LEAD_HOURS == 72
    for keep in (0, 1, 24, 71, 72):
        assert ap.within_lead_cap(keep) is True, keep
    for drop in (73, 96, 120, 144):
        assert ap.within_lead_cap(drop) is False, drop
    assert ap.within_lead_cap(None) is False, "a missing lead is not capturable"
    # an explicit cap overrides, so the constant is genuinely the single edit point
    assert ap.within_lead_cap(100, 144) is True and ap.within_lead_cap(100, 48) is False

    # the volume figure is derived from the cap, so the printed estimate cannot drift from it
    m72, b72, t72 = ap.expected_rows_per_zone_per_day()
    assert (m72, b72, t72) == (292, 24, 316), (m72, b72, t72)
    m144, _, t144 = ap.expected_rows_per_zone_per_day(144)
    assert m144 == 580 and abs(m72 / m144 - 0.5) < 0.02, "the cap keeps ~half the horizon"
    assert ap.expected_rows_per_zone_per_day(48)[0] == 196

    # THE FILTER IS ALSO EXERCISED WHERE IT RUNS — rows_for_zone, not just the predicate.
    # The synthetic cycle carries steps [0, 1, 96]; the 96 must be dropped AND counted.
    from pipeline.forecast import nwps_nearshore as nn
    from pipeline.forecast import nwps_trkng as trk
    cg1, trkc = _synthetic_cycle()
    bundles = [("20260810", "12", cg1, trkc, ap.TRKNG_OK, None)]
    rows, _wind, hours, n_over, _why = ap.rows_for_zone(
        "okx", "44025", 40.0, -73.0, bundles, ap.MAX_LEAD_HOURS, nn=nn, trk=trk)
    assert sorted(r["lead_hours"] for r in rows) == [0, 1], "the 96 h step must not be captured"
    assert n_over == 1, "and it must be COUNTED, not silently dropped"
    assert len(hours) == 2
    # raising the cap admits it, so the filter really is the cap and not a step-list quirk
    rows_hi, _w, _h, n_hi, _y = ap.rows_for_zone(
        "okx", "44025", 40.0, -73.0, bundles, 144, nn=nn, trk=trk)
    assert sorted(r["lead_hours"] for r in rows_hi) == [0, 1, 96] and n_hi == 0

    # the lifespan and the cap are both recorded where a future reader will look
    for text in (ap.DDL, ap.__doc__):
        assert "TWELVE TO EIGHTEEN MONTHS" in text.upper()
        assert "90%" in text and "shortest-lead-per-valid-hour" in text
    assert str(ap.MAX_LEAD_HOURS) in ap.DDL


def test_roster_staleness_is_warned_once_per_run_not_once_per_zone():
    """The warning fired 26 times in a 26-zone run, with the age drifting 2.3 → 2.4 days as
    the clock moved, because nwps_nearshore._buoy_latlng calls _warn_if_roster_stale() on
    every invocation whose station lists are not injected. load_station_roster warns once and
    returns the lists, and collect_zone injects them — which takes that path out entirely."""
    from pipeline.forecast import nwps_nearshore as nn
    calls = []
    orig_warn = nn._warn_if_roster_stale
    orig_latlng = nn._buoy_latlng
    try:
        nn._warn_if_roster_stale = lambda *a, **k: calls.append("warn")
        ap.load_station_roster()
        assert calls.count("warn") == 1, "the roster warning must fire exactly once per run"

        # and an INJECTED roster suppresses the per-call warning inside _buoy_latlng, which is
        # the mechanism — a bare call still warns, an injected one does not
        station = [{"id": "46254", "lat": 32.9, "lng": -117.4}]
        calls.clear()
        orig_latlng("46254", _active=station, _reporting=[{"id": "46254"}])
        assert calls == [], "injected lists must bypass the per-call warner"
    finally:
        nn._warn_if_roster_stale = orig_warn
        nn._buoy_latlng = orig_latlng

    # an unavailable roster degrades to (None, None) so every zone still SKIPs individually
    import pipeline.enrichment.geodata as geo
    orig_active = geo.load_ndbc_active_stations
    try:
        geo.load_ndbc_active_stations = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        assert ap.load_station_roster() == (None, None)
    finally:
        geo.load_ndbc_active_stations = orig_active


def _synthetic_cycle():
    """A real CG1-shaped cycle dict and a real parsed CG0_Trkng cycle, so the node-sampling
    code under test is the ACTUAL nn._nearest_cell / nn._node_value / trk.trkng_node path —
    only the two FETCHES are stubbed, which is what the counter measures."""
    import numpy as np
    from pipeline.forecast import nwps_trkng as trk
    lats = np.array([[40.0, 40.0], [39.98, 39.98]])
    lons = np.array([[-73.0, -72.98], [-73.0, -72.98]])
    cdt = datetime.datetime(2026, 8, 10, 12, tzinfo=datetime.timezone.utc)
    steps = [0, 1, 96]        # 96 is past MAX_LEAD_HOURS — exercises the sampler filter
    fields = {}
    for short, val in (("swh", 1.2), ("perpw", 9.0), ("dirpw", 200.0), ("shts", 0.8),
                       ("ws", 4.0), ("wdir", 210.0)):
        for fh in steps:
            fields[(short, fh)] = np.full((2, 2), val)
    cg1 = {"lats": lats, "lons": lons, "mask": np.zeros((2, 2), bool), "cycle_dt": cdt,
           "steps": steps, "fields": fields}
    S = trk.TRKNG_SENTINEL

    def grid(v):
        a = np.full((2, 2), S, dtype="float64")
        a[0, 0] = a[0, 1] = a[1, 0] = a[1, 1] = v
        return a
    records = [("swdir", 1, fh, grid(273.0), S) for fh in steps] \
        + [("shts", 1, fh, grid(0.13), S) for fh in steps] \
        + [("mpts", 1, fh, grid(18.2), S) for fh in steps]
    return cg1, trk.parse_trkng(lats, lons, cdt, records)


def test_zones_sharing_a_wfo_fetch_each_cycle_file_once():
    """THE OPTIMISATION: zones on one WFO share the CG1 and CG0_Trkng grids exactly — only
    their node coordinates differ — so the GRIBs must be fetched and parsed ONCE per (wfo,
    cycle), not once per zone. Measured before this change: a --backfill 0 run took 24 min
    wall clock for 26 zones against a 50 min workflow cap, about 2x headroom for a job that
    runs twice daily indefinitely. 26 zones across ~15 WFOs is ~1.7x the necessary work."""
    from pipeline.forecast import nwps_nearshore as nn
    from pipeline.forecast import nwps_trkng as trk
    from pipeline.forecast import ndbc_spectral as ndbc_spec
    cg1, trkc = _synthetic_cycle()
    calls = {"cg1": 0, "trk": 0}

    def fake_cg1(wfo, cycle):
        calls["cg1"] += 1
        return cg1

    def fake_trk(wfo, cycle):
        calls["trk"] += 1
        return trkc

    saved = (nn.find_latest_cycle, nn._buoy_latlng, nn._buoy_hourly,
             trk._spec_by_hour, ndbc_spec.by_hour)
    try:
        nn.find_latest_cycle = lambda *a, **k: ("20260810", "12", "u")
        nn._buoy_latlng = lambda b, **k: (40.0, -73.0)
        nn._buoy_hourly = lambda b: {}
        trk._spec_by_hour = lambda b: {}
        ndbc_spec.by_hour = lambda b, **k: {}

        # TWO zones on ONE wfo — one fetch of each file, not two
        members = [("44025", "okx/44025"), ("44065", "okx/44065")]
        res = ap.collect_wfo("okx", members, load_cycle=fake_cg1, load_trkng=fake_trk)
        assert calls == {"cg1": 1, "trk": 1}, f"refetched per zone: {calls}"
        assert set(res) == {"44025", "44065"}, "every zone on the WFO still gets a result"
        for buoy, (rows, note) in res.items():
            assert rows, f"{buoy} produced no rows"
            assert {r["buoy_id"] for r in rows} == {buoy}, "rows must not leak between zones"
            assert all(r["wfo"] == "okx" for r in rows)
            # the shared parse still yields per-zone trkng_status and real sampled values
            model = [r for r in rows if r["source"] == "model"]
            assert model and all(r["trkng_status"] == ap.TRKNG_OK for r in model)
            assert all(r["swh"] == 1.2 and r["dirpw"] == 200.0 for r in model)
            assert all(r["systems"] and r["systems"][0]["tp"] == 18.2 for r in model)
            assert "1 cycle(s)" in note

        # the un-grouped equivalent costs one fetch PER zone — this is the saving, measured
        calls.update(cg1=0, trk=0)
        for m in members:
            ap.collect_wfo("okx", [m], load_cycle=fake_cg1, load_trkng=fake_trk)
        assert calls == {"cg1": 2, "trk": 2}, "two separate calls must cost two fetches"

        # four zones on one WFO (the mhx/sgx shape) still cost exactly one fetch of each
        calls.update(cg1=0, trk=0)
        four = [(b, f"mhx/{b}") for b in ("44056", "44086", "44095", "41120")]
        res4 = ap.collect_wfo("mhx", four, load_cycle=fake_cg1, load_trkng=fake_trk)
        assert calls == {"cg1": 1, "trk": 1} and len(res4) == 4
    finally:
        (nn.find_latest_cycle, nn._buoy_latlng, nn._buoy_hourly,
         trk._spec_by_hour, ndbc_spec.by_hour) = saved

    # grouping itself: order preserved, zones kept together, one entry per WFO
    zones = [("mhx", "1", "mhx/1"), ("sgx", "2", "sgx/2"), ("mhx", "3", "mhx/3"),
             ("sgx", "4", "sgx/4"), ("phi", "5", "phi/5")]
    groups = ap.group_zones_by_wfo(zones)
    assert [w for w, _ in groups] == ["mhx", "sgx", "phi"], "first-seen WFO order preserved"
    assert dict(groups)["mhx"] == [("1", "mhx/1"), ("3", "mhx/3")]
    assert sum(len(m) for _w, m in groups) == len(zones), "no zone lost in grouping"
    assert ap.group_zones_by_wfo([]) == []


def test_one_wfos_fetch_failure_is_scoped_and_does_not_stop_the_others():
    """Error discipline, preserved but re-scoped. A fetch failure now affects EVERY zone on
    that WFO rather than one, so it must be reported as a shared cause instead of looking like
    the same unexplained error N times — and one WFO's failure must never abort the others."""
    from pipeline.forecast import nwps_nearshore as nn
    from pipeline.forecast import nwps_trkng as trk
    from pipeline.forecast import ndbc_spectral as ndbc_spec
    cg1, trkc = _synthetic_cycle()

    # a CG1 that will not load drops that CYCLE, not the whole WFO run
    bundles = ap.load_wfo_cycles("okx", [("20260810", "12", "u")],
                                 load_cycle=lambda *a: (_ for _ in ()).throw(OSError("gone")),
                                 load_trkng=lambda *a: trkc)
    assert len(bundles) == 1 and bundles[0][2] is None, "CG1 failure recorded, not raised"
    assert "CG1 unreadable" in bundles[0][5]

    # CG0_Trkng stays OPTIONAL: its absence is a status on every row, not a lost cycle
    for exc, want in ((OSError("not GRIB (Trkng file missing?)"), ap.TRKNG_ABSENT),
                      (RuntimeError("eccodes exploded"), ap.TRKNG_ERROR)):
        b = ap.load_wfo_cycles("okx", [("20260810", "12", "u")],
                               load_cycle=lambda *a: cg1,
                               load_trkng=lambda *a, _e=exc: (_ for _ in ()).throw(_e))
        assert b[0][2] is cg1 and b[0][3] is None and b[0][4] == want, b

    # a zone whose BUOY will not resolve is still a per-zone failure, leaving its sibling intact
    saved = (nn.find_latest_cycle, nn._buoy_latlng, nn._buoy_hourly,
             trk._spec_by_hour, ndbc_spec.by_hour)
    try:
        nn.find_latest_cycle = lambda *a, **k: ("20260810", "12", "u")
        nn._buoy_hourly = lambda b: {}
        trk._spec_by_hour = lambda b: {}
        ndbc_spec.by_hour = lambda b, **k: {}

        def picky(b, **k):
            if b == "bad":
                raise KeyError(f"buoy {b!r} not in the NDBC active-station list")
            return (40.0, -73.0)
        nn._buoy_latlng = picky
        res = ap.collect_wfo("okx", [("bad", "okx/bad"), ("44025", "okx/44025")],
                             load_cycle=lambda *a: cg1, load_trkng=lambda *a: trkc)
        assert res["bad"][0] == [] and "KeyError" in res["bad"][1], "per-zone failure isolated"
        assert res["44025"][0], "its sibling on the same WFO still captured"
    finally:
        (nn.find_latest_cycle, nn._buoy_latlng, nn._buoy_hourly,
         trk._spec_by_hour, ndbc_spec.by_hour) = saved

    # no cycles in range is a per-WFO answer for every member, not an exception
    try:
        nn_saved = nn.find_latest_cycle
        nn.find_latest_cycle = lambda *a, **k: None
        res = ap.collect_wfo("okx", [("1", "okx/1"), ("2", "okx/2")],
                             load_cycle=lambda *a: cg1, load_trkng=lambda *a: trkc)
        assert list(res) == ["1", "2"] and all(r == [] for r, _n in res.values())
        assert all("no NOMADS cycles in range" in n for _r, n in res.values())
    finally:
        nn.find_latest_cycle = nn_saved


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} partition-archive checks passed")


if __name__ == "__main__":
    _run_all()
