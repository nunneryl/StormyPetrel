"""Fixture checks for scripts/archive_partitions.py — the P3-2 partition archive.

The archiver's fetch path needs NOMADS + eccodes + NDBC, so the parts that decide WHAT gets
stored are pure functions and are tested here against a synthetic cycle and a synthetic
spectral read. Two properties matter most and are both covered:

  * the ROW SHAPE — every row carries the same key set whichever source it came from, the
    raw tracked-system list survives unmodified, and NaN never reaches a row (it is not
    valid JSON and PostgREST rejects it);
  * IDEMPOTENCE against the unique key (valid_hour, wfo, buoy_id, source) — a repeated write
    must leave one row per key, not two. Verified against a fake client that enforces the
    constraint, rather than assumed from the presence of on_conflict.

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
        return FakeTable(self.store, ("valid_hour", "wfo", "buoy_id", "source"))


def test_row_builders_produce_the_expected_shape():
    m = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="12",
                           lead_hours=6, swh=0.62, perpw=4.4, dirpw=201.0, shts=0.31,
                           wind_speed=3.0, wind_dir=200.0, systems=SYSTEMS)
    b = ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL, spec=SPEC, std=STD)

    # ONE key set, whichever source — so the table shape cannot drift between the two writers
    assert set(m) == set(b) == set(ap.ALL_FIELDS)
    assert tuple(ap.KEY_FIELDS) == ("valid_hour", "wfo", "buoy_id", "source")

    # the key, and the ISO/timestamptz rendering of the epoch-hour bucket
    for r, src in ((m, "model"), (b, "buoy")):
        assert (r["wfo"], r["buoy_id"], r["source"]) == ("sgx", "46254", src)
        assert r["valid_hour"] == ap.valid_hour_iso(HOUR)
    dt = datetime.datetime.fromisoformat(m["valid_hour"])
    assert dt.tzinfo is not None and (dt.minute, dt.second) == (0, 0), "hour-truncated UTC"
    assert int(dt.timestamp()) // 3600 == HOUR, "round-trips back to the epoch-hour bucket"

    # model row: the CG1 fields, the cycle identity, and the lead
    assert (m["cycle_date"], m["cycle_hour"], m["lead_hours"]) == ("20260810", "12", 6)
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
    """A re-run must leave ONE row per (valid_hour, wfo, buoy_id, source). The fake client
    enforces the constraint, so this fails if the upsert key is wrong or a plain insert is
    used — it is not a call-count assertion."""
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

    # a later cycle re-forecasting the SAME valid hour replaces that hour's row in place
    fresher = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="18",
                                 lead_hours=0, swh=0.71, systems=SYSTEMS)
    ap.upsert_rows(client, [fresher])
    assert len(client.store) == 2, "same key → replaced, not added"
    stored = client.store[(ap.valid_hour_iso(HOUR), "sgx", "46254", "model")]
    assert stored["swh"] == 0.71 and stored["lead_hours"] == 0

    # a DIFFERENT hour, wfo, buoy or source is a different key and must add a row
    for extra in (ap.build_model_row("sgx", "46254", HOUR + 1, cycle_date="20260810",
                                     cycle_hour="18", lead_hours=1),
                  ap.build_model_row("pqr", "46254", HOUR, cycle_date="20260810",
                                     cycle_hour="18", lead_hours=0),
                  ap.build_model_row("sgx", "46243", HOUR, cycle_date="20260810",
                                     cycle_hour="18", lead_hours=0)):
        before = len(client.store)
        ap.upsert_rows(client, [extra])
        assert len(client.store) == before + 1, extra["valid_hour"]

    # --dry-run writes NOTHING
    fresh = FakeClient()
    assert ap.upsert_rows(fresh, rows, dry_run=True) == 0 and not fresh.store

    # shortest lead wins per key, deterministically and independent of input order — that is
    # what makes a re-run stable, since lead_hours is NOT part of the unique key
    a6 = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="06",
                            lead_hours=12, swh=0.5)
    a0 = ap.build_model_row("sgx", "46254", HOUR, cycle_date="20260810", cycle_hour="18",
                            lead_hours=0, swh=0.71)
    for order in ([a6, a0], [a0, a6]):
        keep = ap.dedupe_shortest_lead(order)
        assert len(keep) == 1 and keep[0]["lead_hours"] == 0 and keep[0]["swh"] == 0.71
    # model and buoy rows for the same hour are DIFFERENT keys and both survive the collapse
    both = ap.dedupe_shortest_lead([a0, ap.build_buoy_row("sgx", "46254", HOUR, spectral=SPECTRAL)])
    assert len(both) == 2 and {r["source"] for r in both} == {"model", "buoy"}


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

    # and the DDL the script prints actually declares that constraint
    assert f"unique (valid_hour, wfo, buoy_id, source)" in ap.DDL
    for col in ap.ALL_FIELDS:
        assert col in ap.DDL, f"{col} is written but absent from the DDL"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} partition-archive checks passed")


if __name__ == "__main__":
    _run_all()
