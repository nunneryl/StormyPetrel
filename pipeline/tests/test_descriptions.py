"""Spot descriptions: what the payload may state (pipeline/descriptions.py) and the generator's three
modes (pipeline/generate_descriptions.py).

WHAT IS PINNED HERE:
  1. the payload: break_type and bottom only from reviewed, researched or scraped; no buoy or tide
     station ids; nothing null; roster names mapped as db_import maps them;
  2. signature v2: literal values computed with hashlib directly, the PROMPT_VERSION in the hash, and
     the system prompt's fingerprint, so editing the prompt without bumping the version fails here;
  3. the plan: generate / write-only / keep, with the reason, and orphan pruning;
  4. --dry-run makes no API call and writes nothing; --sample writes its own file and nothing else;
     the full run writes only what it touched, keeps what it could not write, resumes, and
     reconciles rows a concurrent run put back;
  5. the ten sample spots exist in the committed roster and are the cases they stand for.

The model and Supabase are fakes. Every expected value is written by hand; none is obtained by calling
the function under test.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import descriptions as Dn
from pipeline import generate_descriptions as GD

ROOT = Path(__file__).resolve().parents[2]

STEAMER = {"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
           "orientation_deg": 128, "offshore_wind_deg": 308, "optimal_swell_dir": 225,
           "swell_window_arcs": [{"min": 180, "max": 315}], "tide_preference": "all",
           "break_type": "point", "break_type_source": "researched",
           "bottom": "rock", "bottom_source": "researched",
           "nearest_buoy_id": "46269", "nearest_tide_station_id": "9413745"}
STEAMER_PAYLOAD = {"name": "Steamer Lane", "state": "California", "break_type": "point",
                   "bottom": "rock", "faces": "SE", "orientation_deg": 128,
                   "offshore_wind_from": "NW", "offshore_wind_deg": 308, "tide_preference": "all",
                   "swell_window": ["S..NW"], "optimal_swell_from": "SW"}
# 'v2:' + sha1("2\n" + canonical JSON)[:16], computed with hashlib outside the code under test:
STEAMER_V2 = "v2:5d6fec8ee8c395c4"
STEAMER_V2_LEFT_OUT = "v2:f5c39adebca3cd3d"   # the same with break_type and bottom left out


# --------------------------------------------------------------------------- #
# 1 — the payload                                                              #
# --------------------------------------------------------------------------- #
def test_the_payload_of_a_researched_spot():
    assert Dn.description_payload(Dn.spot_fields(STEAMER)) == STEAMER_PAYLOAD


def test_the_payload_keeps_its_key_order():
    assert list(Dn.description_payload(Dn.spot_fields(STEAMER))) == [
        "name", "state", "break_type", "bottom", "faces", "orientation_deg", "offshore_wind_from",
        "offshore_wind_deg", "tide_preference", "swell_window", "optimal_swell_from"]


@pytest.mark.parametrize("source, kept", [
    ("reviewed", True), ("researched", True), ("scraped", True),
    ("model_recall", False), ("unattributed", False), ("algorithm_default", False),
    (None, False), ("", False), ("REVIEWED", False)])
def test_only_a_source_that_can_be_stated_puts_a_value_in(source, kept):
    spot = dict(STEAMER, break_type_source=source, bottom_source=source)
    payload = Dn.description_payload(Dn.spot_fields(spot))
    assert ("break_type" in payload, "bottom" in payload) == (kept, kept)


def test_break_type_and_bottom_are_judged_separately():
    spot = dict(STEAMER, bottom_source="model_recall")
    payload = Dn.description_payload(Dn.spot_fields(spot))
    assert payload["break_type"] == "point" and "bottom" not in payload


def test_an_unknown_bottom_states_nothing():
    spot = dict(STEAMER, bottom="unknown")
    assert "bottom" not in Dn.description_payload(Dn.spot_fields(spot))


def test_no_station_ids_and_no_nulls_reach_the_model():
    bare = {"name": "Agate Beach", "region_hint": "California", "orientation_deg": None,
            "swell_window_arcs": [], "tide_preference": "", "nearest_buoy_id": "46022",
            "nearest_tide_station_id": "9419750"}
    assert Dn.description_payload(Dn.spot_fields(bare)) == {"name": "Agate Beach",
                                                            "state": "California"}


def test_degrees_are_whole_and_cardinals_have_eight_points():
    spot = {"name": "X", "region_hint": "Y", "orientation_deg": 22.4, "offshore_wind_deg": 202.6,
            "optimal_swell_dir": 337.6, "swell_window_arcs": [{"min": 350, "max": 10}, {"min": 90}]}
    assert Dn.description_payload(Dn.spot_fields(spot)) == {
        "name": "X", "state": "Y", "faces": "N", "orientation_deg": 22,
        # 202.6 / 45 = 4.502, just past the S/SW midpoint at 202.5, so SW
        "offshore_wind_from": "SW", "offshore_wind_deg": 203, "swell_window": ["N..N"],
        "optimal_swell_from": "N"}
    assert [Dn.deg_to_cardinal(d) for d in (0, 22.5, 45, 67.5, 180, 359, -45)] == [
        "N", "N", "NE", "E", "S", "N", "NW"]


def test_spot_fields_maps_the_roster_as_db_import_does():
    assert Dn.spot_fields({"name": "A", "region_hint": "Maine", "break_type": "beach",
                           "lat": 1, "tide_preference": None}) == {
        "state": "Maine", "name": "A", "break_type": "beach", "tide_preference": None}
    assert Dn.spot_fields({}) == {"state": None}


# --------------------------------------------------------------------------- #
# 2 — the signature                                                            #
# --------------------------------------------------------------------------- #
def test_the_signature_is_pinned():
    assert Dn.description_signature(STEAMER_PAYLOAD) == STEAMER_V2
    left_out = {k: v for k, v in STEAMER_PAYLOAD.items() if k not in ("break_type", "bottom")}
    assert Dn.description_signature(left_out) == STEAMER_V2_LEFT_OUT


def test_the_signature_ignores_key_order():
    assert Dn.description_signature(dict(reversed(list(STEAMER_PAYLOAD.items())))) == STEAMER_V2


def test_the_prompt_version_is_in_the_signature(monkeypatch):
    monkeypatch.setattr(Dn, "PROMPT_VERSION", "3")
    expected = "v2:" + hashlib.sha1(("3\n" + json.dumps(
        STEAMER_PAYLOAD, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    ).encode()).hexdigest()[:16]
    assert Dn.description_signature(STEAMER_PAYLOAD) == expected != STEAMER_V2


def test_the_prompt_cannot_change_without_a_new_version():
    """Edit SYSTEM_PROMPT and this fails: bump PROMPT_VERSION, then record the new fingerprint."""
    assert (Dn.PROMPT_VERSION, hashlib.sha1(Dn.SYSTEM_PROMPT.encode()).hexdigest()[:12]) == (
        "2", "1ff8340919bc")


def test_the_prompt_says_to_state_only_what_is_given():
    p = Dn.SYSTEM_PROMPT
    assert "Mention only the fields that are given" in p
    assert "Say nothing at all about a field that is not given" in p
    assert "do not mention that it is missing, unknown or unspecified" in p
    assert "If break_type is not given, call it a surf spot" in p


def test_is_v2():
    assert Dn.is_v2("v2:5d6fec8ee8c395c4") and Dn.is_v2("v2:")
    for sig in ("5d6fec8ee8c395c4", None, "", "V2:abc", 2):
        assert not Dn.is_v2(sig), sig


def test_the_generator_and_db_import_share_one_builder():
    from pipeline import db_import
    assert GD.description_payload is Dn.description_payload is db_import.description_payload
    assert GD.description_signature is Dn.description_signature is db_import.description_signature
    assert GD.SYSTEM_PROMPT is Dn.SYSTEM_PROMPT and GD.MODEL == "claude-sonnet-4-6"


# --------------------------------------------------------------------------- #
# 3 — the plan                                                                 #
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_exclusions(monkeypatch):
    monkeypatch.setattr(GD, "_excluded_slugs", lambda: set())


def spot(name, **kw):
    return dict({"name": name, "lat": 1.0, "lng": 2.0, "region_hint": "Hawaii",
                 "orientation_deg": 315}, **kw)


def test_the_roster_is_what_db_import_would_upsert(monkeypatch):
    monkeypatch.setattr(GD, "_excluded_slugs", lambda: {"gone-spot"})
    got = GD.roster_spots([spot("Alpha"), spot("Alpha"), spot("Gone Spot"), {"name": ""},
                           spot("Invalid", is_valid_surf_spot=False), spot("No Lat", lat=None),
                           spot("Beta")])
    assert list(got) == ["alpha", "beta"]


def test_each_spot_gets_its_action_and_reason(no_exclusions):
    roster = GD.roster_spots([spot("New"), spot("Old"), spot("Changed"), spot("Same"),
                              spot("Unwritten")])
    same_sig = "v2:" + hashlib.sha1(
        b'2\n{"faces":"NW","name":"Same","orientation_deg":315,"state":"Hawaii"}').hexdigest()[:16]
    unwritten_sig = "v2:" + hashlib.sha1(
        b'2\n{"faces":"NW","name":"Unwritten","orientation_deg":315,"state":"Hawaii"}'
    ).hexdigest()[:16]
    cache = {"old": {"description": "t", "signature": "0123456789abcdef"},
             "changed": {"description": "t", "signature": "v2:0000000000000000"},
             "same": {"description": "t", "signature": same_sig},
             "unwritten": {"description": "t", "signature": unwritten_sig, "pushed": False}}
    got = [(slug, action, reason) for slug, _s, _p, _g, action, reason in GD.plan(roster, cache)]
    assert got == [("new", "generate", "no description yet"),
                   ("old", "generate", "written before signature v2"),
                   ("changed", "generate", "something it states has changed"),
                   ("same", "keep", "current"),
                   ("unwritten", "push", "generated but not yet written to Supabase")]
    forced = [a for _sl, _s, _p, _g, a, _r in GD.plan(roster, cache, force=True)]
    assert forced == ["generate"] * 5


def test_orphans_are_only_spots_gone_from_the_roster_file():
    spots = [spot("Kept"), spot("Invalid", is_valid_surf_spot=False)]
    cache = {"kept": {}, "invalid": {}, "renamed-long-ago": {}, "a-closed-spot": {}}
    assert GD.orphans(spots, cache) == ["a-closed-spot", "renamed-long-ago"]


def test_left_out_names_each_value_and_why():
    assert GD.left_out(dict(STEAMER, break_type_source="model_recall", bottom="unknown")) == [
        "break_type point (model_recall)", "bottom 'unknown'"]
    assert GD.left_out({"break_type": "beach"}) == ["break_type beach (no source)", "bottom not set"]
    assert GD.left_out(STEAMER) == []


@pytest.mark.parametrize("text, name, said", [
    ("Chuns Reef is a beach break facing northwest.", "Chuns Reef", {"beach"}),
    ("A reef break on Oahu.", "Pipe", {"reef"}),
    ("It breaks over a shallow reef.", "Pipe", {"reef"}),
    ("A right-hand point near Point Loma.", "X", {"point"}),
    ("A surf spot near Point Loma.", "X", set()),
    ("A jetty break.", "X", {"jetty"}), ("A river mouth break.", "X", {"rivermouth"}),
    ("A beachbreak.", "X", {"beach"}), ("", "X", set()),
])
def test_the_break_types_a_text_names(text, name, said):
    assert GD.stated_break_types(text, name) == said


def test_a_contradiction_is_named_with_the_current_value_and_source():
    pipe = {"name": "Banzai Pipeline", "break_type": "reef", "break_type_source": "researched"}
    assert GD.contradiction("Banzai Pipeline is a beach break on the North Shore.", pipe) == (
        "says beach; break_type is now reef (researched)")
    assert GD.contradiction("A reef break.", pipe) is None
    assert GD.contradiction("A surf spot.", pipe) is None
    assert GD.contradiction("A beach break.", {"name": "X"}) is None


def test_the_cost_estimate():
    payload = {"name": "A"}
    tokens_in = math.ceil((len(Dn.SYSTEM_PROMPT) + len('{"name": "A"}')) / 3.5)
    calls, tin, tout, usd = GD.estimate([payload, payload])
    assert (calls, tin, tout) == (2, tokens_in, 90)
    assert usd == pytest.approx((2 * tokens_in * 3.00 + 2 * 90 * 15.00) / 1_000_000)
    assert GD.estimate([]) == (0, 0, 90, 0.0)


# --------------------------------------------------------------------------- #
# 4 — the modes                                                                #
# --------------------------------------------------------------------------- #
class FakeClaude:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        name = json.loads(kw["messages"][0]["content"])["name"]
        if name in self.fail:
            raise RuntimeError("boom")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f'"About {name}."')])


class FakeDB:
    """The spots table: update().eq().execute() and select('*').range().execute()."""

    def __init__(self, rows, fail=()):
        self.rows = {r["slug"]: dict(r) for r in rows}
        self.updates, self.fail = [], set(fail)

    def table(self, name):
        assert name == "spots"
        return FakeQuery(self)


class FakeQuery:
    def __init__(self, db):
        self.db, self.payload, self.slug, self.rng = db, None, None, None

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, col, val):
        assert col == "slug"
        self.slug = val
        return self

    def select(self, cols):
        assert cols == "*"
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        if self.payload is not None:
            if self.slug in self.db.fail:
                raise RuntimeError("network")
            self.db.updates.append((self.slug, dict(self.payload)))
            if self.slug not in self.db.rows:
                return SimpleNamespace(data=[])
            self.db.rows[self.slug].update(self.payload)
            return SimpleNamespace(data=[dict(self.db.rows[self.slug])])
        a, b = self.rng
        return SimpleNamespace(data=[dict(r) for r in list(self.db.rows.values())[a:b + 1]])


def sig_of(name):
    """The v2 signature of spot(name)'s payload, written out and hashed with hashlib."""
    return "v2:" + hashlib.sha1(('2\n{"faces":"NW","name":"%s","orientation_deg":315,'
                                 '"state":"Hawaii"}' % name).encode()).hexdigest()[:16]


def write(tmp_path, spots, cache=None):
    roster = tmp_path / "spots.json"
    roster.write_text(json.dumps(spots))
    out = tmp_path / "cache.json"
    if cache is not None:
        out.write_text(json.dumps(cache))
    return roster, out


def forbid(monkeypatch, *names):
    for n in names:
        monkeypatch.setattr(GD, n, lambda *a, _n=n, **k: pytest.fail(f"{_n} was called"))


def test_the_dry_run_calls_nothing_and_writes_nothing(tmp_path, monkeypatch, capsys, no_exclusions):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    forbid(monkeypatch, "anthropic_client", "get_client", "save_json")
    spots = [spot("Banzai Pipeline", break_type="reef", break_type_source="researched",
                  bottom="rock", bottom_source="model_recall"),
             spot("Same"), spot("Gone", is_valid_surf_spot=False)]
    cache = {"banzai-pipeline": {"description": "Banzai Pipeline is a beach break.",
                                 "signature": "0123456789abcdef"},
             "same": {"description": "Same is a surf spot.", "signature": sig_of("Same")},
             "renamed": {"description": "x", "signature": "v2:x"}}
    roster, out = write(tmp_path, spots, cache)
    before = out.read_bytes()
    assert GD.main(["--dry-run", "--input", str(roster), "--output", str(out)]) == 0
    assert out.read_bytes() == before and not (tmp_path / "cache.json.tmp").exists()
    lines = capsys.readouterr().out.splitlines()
    assert lines[:6] == [
        "[generate] banzai-pipeline — written before signature v2",
        "    states:   break_type reef (researched)",
        "    left out: bottom rock (model_recall)",
        "    current text contradicts: says beach; break_type is now reef (researched)",
        '    payload:  {"name": "Banzai Pipeline", "state": "Hawaii", "break_type": "reef", '
        '"faces": "NW", "orientation_deg": 315}',
        "[keep] same — current",
    ]
    tokens_in = math.ceil((len(Dn.SYSTEM_PROMPT) + len(
        '{"name": "Banzai Pipeline", "state": "Hawaii", "break_type": "reef", "faces": "NW", '
        '"orientation_deg": 315}')) / 3.5)
    usd = (tokens_in * 3.00 + 90 * 15.00) / 1_000_000
    assert lines[6:] == [
        "",
        "TOTALS",
        "  spots in the roster:              2",
        "  to generate:                      1",
        "      written before signature v2: 1",
        "  to write to Supabase only:        0",
        "  unchanged:                        1",
        "  orphan cache entries to prune:    1 (renamed)",
        "  current texts contradicting the break type: 1",
        "  break_type left out: not set 1",
        "  bottom left out:     model_recall 1, not set 1",
        f"  estimate: 1 call(s) x ~{tokens_in} input + ~90 output tokens = about ${usd:.2f} on "
        "claude-sonnet-4-6 at $3.00 / $15.00 per million tokens (from character counts, not a "
        "token count)",
    ]


def sample_roster():
    return [spot(n) for n in GD.SAMPLE_SPOTS] + [spot("Other")]


def test_the_sample_writes_its_own_file_and_nothing_else(tmp_path, monkeypatch, capsys,
                                                          no_exclusions):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    claude = FakeClaude()
    monkeypatch.setattr(GD, "anthropic_client", lambda: claude)
    forbid(monkeypatch, "get_client", "push", "reconcile")
    roster, out = write(tmp_path, sample_roster(), {"other": {"description": "x", "signature": "y"}})
    before = out.read_bytes()
    sample = tmp_path / "sample.json"
    assert GD.main(["--sample", "--input", str(roster), "--output", str(out),
                    "--sample-output", str(sample)]) == 0
    assert out.read_bytes() == before
    got = json.loads(sample.read_text())
    assert sorted(got) == sorted(["banzai-pipeline", "waimea-bay", "steamer-lane",
                                  "malibu-surfrider-beach", "matanzas-inlet", "asbury-park",
                                  "pensacola-beach", "agate-beach", "aviones", "seaside-reef"])
    assert got["aviones"] == {"name": "Aviones", "payload": {"name": "Aviones", "state": "Hawaii",
                                                             "faces": "NW", "orientation_deg": 315},
                              "signature": sig_of("Aviones"), "description": "About Aviones."}
    assert len(claude.calls) == 10
    assert claude.calls[0] == {"model": "claude-sonnet-4-6", "max_tokens": 200,
                               "system": Dn.SYSTEM_PROMPT,
                               "messages": [{"role": "user", "content":
                                             '{"name": "Banzai Pipeline", "state": "Hawaii", '
                                             '"faces": "NW", "orientation_deg": 315}'}]}
    printed = capsys.readouterr().out
    assert "== Banzai Pipeline (banzai-pipeline)\n   left out: break_type not set, bottom not set\n" \
           '   payload:  {"name": "Banzai Pipeline", "state": "Hawaii", "faces": "NW", ' \
           '"orientation_deg": 315}\n   About Banzai Pipeline.\n' in printed
    assert printed.endswith(f"10 sample description(s) written to {sample}; nothing else was "
                            "written.\n")


def test_a_sample_spot_missing_from_the_roster_stops_before_any_call(tmp_path, monkeypatch,
                                                                      no_exclusions):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    claude = FakeClaude()
    monkeypatch.setattr(GD, "anthropic_client", lambda: claude)
    roster, out = write(tmp_path, sample_roster()[1:])
    sample = tmp_path / "sample.json"
    assert GD.main(["--sample", "--input", str(roster), "--output", str(out),
                    "--sample-output", str(sample)]) == 2
    assert claude.calls == [] and not sample.exists()


def full(tmp_path, monkeypatch, spots, cache, db, claude=None, extra=()):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    claude = claude or FakeClaude()
    monkeypatch.setattr(GD, "anthropic_client", lambda: claude)
    monkeypatch.setattr(GD, "get_client", lambda: db)
    roster, out = write(tmp_path, spots, cache)
    rc = GD.main(["--input", str(roster), "--output", str(out), *extra])
    return rc, json.loads(out.read_text()), claude


def test_the_full_run_writes_what_it_generates_prunes_orphans_and_keeps_the_rest(
        tmp_path, monkeypatch, no_exclusions):
    spots = [spot("New"), spot("Old"), spot("Same")]
    cache = {"old": {"description": "Old is a beach break.", "signature": "0123456789abcdef"},
             "same": {"description": "Same text.", "signature": sig_of("Same")},
             "renamed": {"description": "x", "signature": "v2:x"}}
    db = FakeDB([{"slug": "new"}, {"slug": "old", "description": "Old is a beach break."},
                 {"slug": "same", "description": "Same text.",
                  "description_signature": sig_of("Same")}])
    rc, got, claude = full(tmp_path, monkeypatch, spots, cache, db)
    assert rc == 0
    assert got == {"new": {"description": "About New.", "signature": sig_of("New")},
                   "old": {"description": "About Old.", "signature": sig_of("Old")},
                   "same": {"description": "Same text.", "signature": sig_of("Same")}}
    assert db.updates == [
        ("new", {"description": "About New.", "description_signature": sig_of("New")}),
        ("old", {"description": "About Old.", "description_signature": sig_of("Old")})]
    assert [json.loads(c["messages"][0]["content"])["name"] for c in claude.calls] == ["New", "Old"]


def test_nothing_is_written_when_nothing_changed(tmp_path, monkeypatch, no_exclusions):
    cache = {"same": {"description": "Same text.", "signature": sig_of("Same")}}
    db = FakeDB([{"slug": "same", "description": "Same text.",
                  "description_signature": sig_of("Same")}])
    rc, got, claude = full(tmp_path, monkeypatch, [spot("Same")], cache, db)
    assert (rc, got, claude.calls, db.updates) == (0, cache, [], [])


def test_an_unwritten_description_is_kept_and_written_next_run_without_regenerating(
        tmp_path, monkeypatch, no_exclusions):
    db = FakeDB([{"slug": "a"}, {"slug": "b"}], fail={"b"})
    rc, got, _ = full(tmp_path, monkeypatch, [spot("A"), spot("B")], {}, db)
    assert rc == 1
    assert got["b"] == {"description": "About B.", "signature": sig_of("B"), "pushed": False}
    assert got["a"] == {"description": "About A.", "signature": sig_of("A")}

    db.fail.clear()
    rc, got, claude = full(tmp_path, monkeypatch, [spot("A"), spot("B")], got, db)
    assert rc == 0 and claude.calls == []
    assert got["b"] == {"description": "About B.", "signature": sig_of("B")}
    assert db.updates[-1] == ("b", {"description": "About B.", "description_signature": sig_of("B")})


def test_a_spot_with_no_row_yet_is_kept_unwritten(tmp_path, monkeypatch, no_exclusions):
    rc, got, _ = full(tmp_path, monkeypatch, [spot("A")], {}, FakeDB([]))
    assert rc == 1 and got["a"]["pushed"] is False


def test_the_run_resumes_where_it_stopped(tmp_path, monkeypatch, no_exclusions):
    spots = [spot("A"), spot("B"), spot("C")]
    db = FakeDB([{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    rc, got, claude = full(tmp_path, monkeypatch, spots, {}, db, extra=("--limit", "2"))
    assert rc == 0 and sorted(got) == ["a", "b"] and len(claude.calls) == 2
    rc, got, claude = full(tmp_path, monkeypatch, spots, got, db)
    assert rc == 0 and sorted(got) == ["a", "b", "c"]
    assert [json.loads(c["messages"][0]["content"])["name"] for c in claude.calls] == ["C"]


def test_a_failed_generation_fails_the_run_and_is_retried_next_time(tmp_path, monkeypatch,
                                                                     no_exclusions):
    db = FakeDB([{"slug": "a"}, {"slug": "b"}])
    rc, got, _ = full(tmp_path, monkeypatch, [spot("A"), spot("B")], {}, db,
                      claude=FakeClaude(fail={"A"}))
    assert rc == 1 and sorted(got) == ["b"]
    rc, got, claude = full(tmp_path, monkeypatch, [spot("A"), spot("B")], got, db)
    assert rc == 0 and len(claude.calls) == 1 and sorted(got) == ["a", "b"]


def test_the_reconcile_puts_back_a_row_a_concurrent_run_reverted(tmp_path, monkeypatch,
                                                                  no_exclusions):
    """A forecast-pipeline run that read the row before the generator wrote it upserts the old text
    and the version-1 signature back. The cache is current, so the spot is not regenerated, but the
    reconcile sees the row differ and writes it again."""
    cache = {"same": {"description": "Same text.", "signature": sig_of("Same")}}
    db = FakeDB([{"slug": "same", "description": "Same is a beach break.",
                  "description_signature": "0123456789abcdef"}])
    rc, _got, claude = full(tmp_path, monkeypatch, [spot("Same")], cache, db)
    assert rc == 0 and claude.calls == []
    assert db.updates == [("same", {"description": "Same text.",
                                    "description_signature": sig_of("Same")})]


def test_the_reconcile_also_compares_the_text(tmp_path, monkeypatch, no_exclusions):
    """The signature matches but the text in the table is not the cached one (edited by hand, or a
    write that landed half): the cache's text is written back."""
    cache = {"same": {"description": "Same text.", "signature": sig_of("Same")}}
    db = FakeDB([{"slug": "same", "description": "Edited in the table.",
                  "description_signature": sig_of("Same")}])
    rc, _got, claude = full(tmp_path, monkeypatch, [spot("Same")], cache, db)
    assert rc == 0 and claude.calls == []
    assert db.updates == [("same", {"description": "Same text.",
                                    "description_signature": sig_of("Same")})]


def test_no_db_keeps_everything_unwritten_and_never_opens_supabase(tmp_path, monkeypatch,
                                                                    no_exclusions):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(GD, "anthropic_client", lambda: FakeClaude())
    forbid(monkeypatch, "get_client")
    roster, out = write(tmp_path, [spot("A")], {})
    assert GD.main(["--no-db", "--input", str(roster), "--output", str(out)]) == 0
    assert json.loads(out.read_text()) == {
        "a": {"description": "About A.", "signature": sig_of("A"), "pushed": False}}


def test_without_a_key_only_the_dry_run_works(tmp_path, monkeypatch, no_exclusions):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    forbid(monkeypatch, "anthropic_client", "get_client")
    roster, out = write(tmp_path, [spot("A")], {})
    assert GD.main(["--input", str(roster), "--output", str(out)]) == 1
    assert GD.main(["--sample", "--input", str(roster), "--output", str(out)]) == 1


# --------------------------------------------------------------------------- #
# 5 — the sample spots, in the committed roster                                #
# --------------------------------------------------------------------------- #
def test_the_sample_spots_are_the_cases_they_stand_for():
    roster = {s["name"]: s for s in json.loads(
        (ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8"))}
    assert all(n in roster for n in GD.SAMPLE_SPOTS)
    pick = {n: roster[n] for n in GD.SAMPLE_SPOTS}
    assert (pick["Banzai Pipeline"]["break_type"], pick["Banzai Pipeline"]["break_type_source"]) == (
        "reef", "researched")
    assert (pick["Matanzas Inlet"]["break_type"], pick["Matanzas Inlet"]["break_type_source"]) == (
        "jetty", "reviewed")
    assert pick["Asbury Park"]["region_hint"] == "New Jersey"
    assert (pick["Asbury Park"]["break_type"], pick["Asbury Park"]["bottom_source"]) == (
        "beach", "model_recall")
    assert pick["Pensacola Beach"]["break_type_source"] == "model_recall"
    assert not pick["Agate Beach"].get("break_type")
    assert (pick["Aviones"]["region_hint"], pick["Aviones"]["break_type"]) == ("Puerto Rico", "reef")
    assert "tide_preference" not in pick["Seaside Reef"]
    assert {pick[n]["region_hint"] for n in ("Banzai Pipeline", "Waimea Bay")} == {"Hawaii"}
    assert {pick[n]["region_hint"] for n in ("Steamer Lane", "Malibu Surfrider Beach")} == {
        "California"}
