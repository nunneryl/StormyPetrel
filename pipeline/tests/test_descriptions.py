"""Spot descriptions: what the payload may state and how the template writes it
(pipeline/descriptions.py), and the generator's three modes (pipeline/generate_descriptions.py).

WHAT IS PINNED HERE:
  1. the payload (template t4): break_type, bottom and tide only from reviewed, researched or
     scraped; what research says of the wave, re-said; nothing null; roster names mapped as db_import
     maps them. The facts from outside the spot's row (best months, buoy and station, cam, nearby
     spots) are tested in test_descriptions_v2.py; here the Context is empty;
  2. signature v2: literal values computed with hashlib directly, the TEMPLATE_VERSION in the hash,
     and the template's fingerprint, so editing its wording without bumping the version fails here;
  3. the plan: generate / write-only / keep, with the reason, and orphan pruning;
  4. --dry-run writes nothing and prints every final text; --sample writes its own file only;
     the full run writes only what it touched, keeps what it could not write, resumes, and
     reconciles rows a concurrent run put back;
  5. the ten sample spots exist in the committed roster and are the cases they stand for;
  6. the output check, on the reply a model once gave for Pensacola Beach and rule by rule;
  7. the template's first sentence and tide sentence word for word, and every spot in the committed
     roster passing the output check.

Supabase is a fake. Every expected value is written by hand; none is obtained by calling
the function under test.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import descriptions as Dn
from pipeline import generate_descriptions as GD

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_outside_facts(monkeypatch):
    """The spot's own row only: no other spots, best months, cams or station names, so a spot named
    "Banzai Pipeline" here is not given the real one's cam or neighbours. The committed files'
    Context is tested in test_descriptions_v2.py."""
    monkeypatch.setattr(Dn, "default_context", lambda: Dn.Context([]))
    monkeypatch.setattr(Dn, "context_for", lambda roster: Dn.Context([]))

STEAMER = {"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
           "orientation_deg": 128, "offshore_wind_deg": 308, "optimal_swell_dir": 225,
           "swell_window_arcs": [{"min": 180, "max": 315}], "tide_preference": "all",
           "tide_preference_source": "researched", "break_type": "point", "break_type_source": "researched",
           "bottom": "rock", "bottom_source": "researched",
           "nearest_buoy_id": "46269", "nearest_tide_station_id": "9413745"}
STEAMER_PAYLOAD = {"name": "Steamer Lane", "state": "California", "break_type": "point",
                   "bottom": "rock", "tide_preference": "all"}
# 'v2:' + sha1("t4\n" + canonical JSON)[:16], computed with hashlib outside the code under test:
STEAMER_V2 = "v2:e3cfaeb7005c1559"
STEAMER_V2_LEFT_OUT = "v2:ad5156ac76fe0e9f"   # the same with break_type and bottom left out


# --------------------------------------------------------------------------- #
# 1 — the payload                                                              #
# --------------------------------------------------------------------------- #
def test_the_payload_of_a_researched_spot():
    assert Dn.description_payload(Dn.spot_fields(STEAMER)) == STEAMER_PAYLOAD


def test_the_payload_keeps_its_key_order():
    full = dict(STEAMER, break_type_evidence="An exposed right-hand point.", nearest_buoy_dist_km=21.6,
                nearest_tide_station_dist_km=1.4, tide_height_suppressed=False)
    assert list(Dn.description_payload(Dn.spot_fields(full))) == [
        "name", "state", "break_type", "hand", "exposure", "bottom", "tide_preference", "buoy",
        "tide_station"]


@pytest.mark.parametrize("source, kept", [
    ("reviewed", True), ("researched", True), ("scraped", True), ("model_recall", False),
    ("unattributed", False), ("algorithm_default", False), ("derived_from_break_type", False),
    (None, False)])
def test_only_a_source_that_can_be_stated_puts_a_value_in(source, kept):
    s = dict(STEAMER, break_type_source=source, bottom_source=source, tide_preference_source=source)
    p = Dn.description_payload(Dn.spot_fields(s))
    assert (("break_type" in p) and ("bottom" in p) and ("tide_preference" in p)) is kept
    assert (("break_type" in p) or ("bottom" in p) or ("tide_preference" in p)) is kept


def test_unknown_values_state_nothing():
    s = dict(STEAMER, bottom="unknown", tide_preference="unknown")
    p = Dn.description_payload(Dn.spot_fields(s))
    assert "bottom" not in p and "tide_preference" not in p


def test_the_directions_and_the_swell_window_are_left_to_the_card():
    p = Dn.description_payload(Dn.spot_fields(STEAMER))
    for k in ("faces", "orientation_deg", "offshore_wind_from", "optimal_swell_from",
              "swell_window_arcs"):
        assert k not in p, k


def test_spot_fields_maps_the_roster_as_db_import_does():
    assert Dn.spot_fields(dict(STEAMER, break_type_evidence="e", tide_height_suppressed=True)) == {
        "state": "California", "name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266,
        "break_type": "point", "break_type_source": "researched", "break_type_evidence": "e",
        "bottom": "rock", "bottom_source": "researched", "tide_preference": "all",
        "tide_preference_source": "researched", "nearest_buoy_id": "46269",
        "nearest_tide_station_id": "9413745", "tide_height_suppressed": True}


# --------------------------------------------------------------------------- #
# 2 — the signature                                                            #
# --------------------------------------------------------------------------- #
def test_the_signature_is_pinned():
    assert Dn.description_signature(STEAMER_PAYLOAD) == STEAMER_V2
    left_out = {k: v for k, v in STEAMER_PAYLOAD.items() if k not in ("break_type", "bottom")}
    assert Dn.description_signature(left_out) == STEAMER_V2_LEFT_OUT


def test_the_signature_ignores_key_order():
    assert Dn.description_signature(dict(reversed(list(STEAMER_PAYLOAD.items())))) == STEAMER_V2


def test_the_template_version_is_in_the_signature(monkeypatch):
    monkeypatch.setattr(Dn, "TEMPLATE_VERSION", "t5")
    expected = "v2:" + hashlib.sha1(("t5\n" + json.dumps(
        STEAMER_PAYLOAD, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    ).encode()).hexdigest()[:16]
    assert Dn.description_signature(STEAMER_PAYLOAD) == expected != STEAMER_V2


def template_source():
    parts = (Dn._an, Dn._name_holds, Dn._list, Dn._month, Dn._span, Dn._identity, Dn._best,
             Dn._sources, Dn._cam, Dn._nearby, Dn.render, Dn.evidence_traits, Dn.display_name)
    return ("".join(inspect.getsource(f) for f in parts) + repr(Dn.BREAK_WORDS) + Dn.NO_BREAK_TYPE
            + repr(Dn.HAND_WORDS) + repr(Dn.TIDE_SENTENCES) + repr(Dn.MONTHS)
            + repr((Dn.BUOY_MAX_KM, Dn.NEARBY_MAX_KM, Dn.NEARBY_N))
            + Dn._LEFT.pattern + Dn._RIGHT.pattern + Dn._BOTH.pattern)


def test_the_template_cannot_change_without_a_new_version():
    """Edit the template's wording and this fails: bump TEMPLATE_VERSION, then record the new
    fingerprint. Every description written by the old wording is then rewritten."""
    assert (Dn.TEMPLATE_VERSION, hashlib.sha1(template_source().encode()).hexdigest()[:12]) == (
        "t4", "64a597830229")


def test_is_v2():
    assert Dn.is_v2("v2:fb3e7d4f849945e3") and Dn.is_v2("v2:")
    for sig in ("fb3e7d4f849945e3", None, "", "V2:abc", 2):
        assert not Dn.is_v2(sig), sig


def test_the_generator_and_db_import_share_one_builder():
    from pipeline import db_import
    assert GD.description_payload is Dn.description_payload is db_import.description_payload
    assert GD.description_signature is Dn.description_signature is db_import.description_signature
    assert GD.render is Dn.render and GD.pattern_for is Dn.pattern_for
    assert not hasattr(GD, "MODEL") and not hasattr(Dn, "SYSTEM_PROMPT")


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
    same_sig = "v2:" + hashlib.sha1(b't4\n{"name":"Same","state":"Hawaii"}').hexdigest()[:16]
    unwritten_sig = "v2:" + hashlib.sha1(b't4\n{"name":"Unwritten","state":"Hawaii"}').hexdigest()[:16]
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
    assert GD.left_out(dict(STEAMER, break_type_source="model_recall", bottom="unknown",
                            tide_preference_source=None)) == [
        "break_type point (model_recall)", "bottom 'unknown'", "tide_preference all (no source)"]
    assert GD.left_out({"break_type": "beach"}) == [
        "break_type beach (no source)", "bottom not set", "tide_preference not set"]
    assert GD.left_out(dict(STEAMER, tide_preference="unknown")) == ["tide_preference 'unknown'"]
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
    return "v2:" + hashlib.sha1(('t4\n{"name":"%s","state":"Hawaii"}' % name).encode()).hexdigest()[:16]


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


def text_of(name, pattern=0):
    """What the template writes for spot(name): Hawaii, nothing else known (t4 has one pattern and
    leaves the facing to the Optimal conditions card)."""
    return f"{name} is a surf spot in Hawaii."


def test_one_pattern_since_t4():
    assert Dn.PATTERN_COUNT == 1
    assert {Dn.pattern_for(slug) for slug in ("a", "b", "banzai-pipeline", "steamer-lane")} == {0}


def test_the_dry_run_writes_nothing_and_prints_every_final_text(tmp_path, monkeypatch, capsys,
                                                                no_exclusions):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    forbid(monkeypatch, "get_client", "save_json", "push")
    spots = [spot("Banzai Pipeline", break_type="reef", break_type_source="researched",
                  bottom="rock", bottom_source="model_recall", tide_preference="low"),
             spot("Same"), spot("Gone", is_valid_surf_spot=False)]
    cache = {"banzai-pipeline": {"description": "Banzai Pipeline is a beach break.",
                                 "signature": "0123456789abcdef"},
             "same": {"description": text_of("Same", 1), "signature": sig_of("Same")},
             "renamed": {"description": "x", "signature": "v2:x"}}
    roster, out = write(tmp_path, spots, cache)
    before = out.read_bytes()
    assert GD.main(["--dry-run", "--input", str(roster), "--output", str(out)]) == 0
    assert out.read_bytes() == before and not (tmp_path / "cache.json.tmp").exists()
    assert capsys.readouterr().out.splitlines() == [
        "[generate] banzai-pipeline — written before signature v2",
        "    states:   break_type reef (researched)",
        "    left out: bottom rock (model_recall), tide_preference low (no source)",
        "    current text contradicts: says beach; break_type is now reef (researched)",
        '    payload:  {"name": "Banzai Pipeline", "state": "Hawaii", "break_type": "reef"}',
        "    pattern:  0",
        "    check:    pass",
        "    final:    Banzai Pipeline is a reef break in Hawaii.",
        "[keep] same — current",
        "    final:    Same is a surf spot in Hawaii.",
        "",
        "TOTALS",
        "  spots in the roster:              2",
        "  to write:                         1",
        "      written before signature v2: 1",
        "  to write to Supabase only:        0",
        "  unchanged:                        1",
        "  orphan cache entries to prune:    1 (renamed)",
        "  current texts contradicting the break type: 1",
        "  new texts failing the output check (would be rejected): 0",
        "  patterns among the texts to write: 0 x1",
        "  break_type left out:      not set 1",
        "  bottom left out:          model_recall 1, not set 1",
        "  tide_preference left out: no source 1, not set 1",
    ]


def bad_render(bad_slug):
    """The template, except that *bad_slug* gets a text the output check rejects."""
    real = Dn.render
    return lambda payload, slug: "Wait." if slug == bad_slug else real(payload, slug)


def test_the_dry_run_shows_a_text_the_check_would_reject(tmp_path, monkeypatch, capsys,
                                                         no_exclusions):
    monkeypatch.setattr(GD, "render", bad_render("a"))
    cache = {"a": {"description": "A was here.", "signature": "0123456789abcdef"}}
    roster, out = write(tmp_path, [spot("A"), spot("B")], cache)
    assert GD.main(["--dry-run", "--input", str(roster), "--output", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[4:7] == [
        "    pattern:  0",
        "    check:    FAIL — self-talk or meta text: 'wait'; does not include the spot's name",
        "    final:    (unchanged) A was here.",
    ]
    assert "    final:    B is a surf spot in Hawaii." in lines
    assert "  new texts failing the output check (would be rejected): 1" in lines
    assert "  patterns among the texts to write: 0 x2" in lines


def sample_roster():
    return [spot(n) for n in GD.SAMPLE_SPOTS] + [spot("Other")]


def test_the_sample_writes_its_own_file_and_nothing_else(tmp_path, monkeypatch, capsys,
                                                          no_exclusions):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
    assert got["aviones"] == {"name": "Aviones", "payload": {"name": "Aviones", "state": "Hawaii"},
                              "pattern": 0, "check": "pass", "signature": sig_of("Aviones"),
                              "description": text_of("Aviones", 0)}
    printed = capsys.readouterr().out
    assert ("== Banzai Pipeline (banzai-pipeline)\n"
            "   left out: break_type not set, bottom not set, tide_preference not set\n"
            '   payload:  {"name": "Banzai Pipeline", "state": "Hawaii"}\n'
            "   pattern 0, check: pass\n"
            "   Banzai Pipeline is a surf spot in Hawaii.\n\n") in printed
    assert printed.endswith(f"10 sample description(s) written to {sample}; nothing else was "
                            "written.\n")


def test_a_sample_spot_missing_from_the_roster_stops_before_writing(tmp_path, no_exclusions):
    roster, out = write(tmp_path, sample_roster()[1:])
    sample = tmp_path / "sample.json"
    assert GD.main(["--sample", "--input", str(roster), "--output", str(out),
                    "--sample-output", str(sample)]) == 2
    assert not sample.exists()


def test_the_sample_reports_a_rejected_text(tmp_path, monkeypatch, capsys, no_exclusions):
    monkeypatch.setattr(GD, "render", bad_render("pensacola-beach"))
    roster, out = write(tmp_path, sample_roster())
    sample = tmp_path / "sample.json"
    assert GD.main(["--sample", "--input", str(roster), "--output", str(out),
                    "--sample-output", str(sample)]) == 1
    got = json.loads(sample.read_text())["pensacola-beach"]
    fail = "FAIL — self-talk or meta text: 'wait'; does not include the spot's name"
    assert (got["check"], got["signature"], got["description"]) == (fail, None, None)
    printed = capsys.readouterr().out
    assert (f"   pattern 0, check: {fail}\n   Wait.\n"
            "   REJECTED: a full run would keep the existing description\n") in printed
    assert printed.endswith("rejected by the output check: pensacola-beach\n")
    assert not out.exists()


def full(tmp_path, monkeypatch, spots, cache, db, extra=()):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(GD, "get_client", lambda: db)
    roster, out = write(tmp_path, spots, cache)
    rc = GD.main(["--input", str(roster), "--output", str(out), *extra])
    return rc, json.loads(out.read_text())


def test_the_full_run_writes_what_changed_prunes_orphans_and_keeps_the_rest(
        tmp_path, monkeypatch, no_exclusions):
    spots = [spot("New"), spot("Old"), spot("Same")]
    cache = {"old": {"description": "Old is a beach break.", "signature": "0123456789abcdef"},
             "same": {"description": text_of("Same", 1), "signature": sig_of("Same")},
             "renamed": {"description": "x", "signature": "v2:x"}}
    db = FakeDB([{"slug": "new"}, {"slug": "old", "description": "Old is a beach break."},
                 {"slug": "same", "description": text_of("Same", 1),
                  "description_signature": sig_of("Same")}])
    rc, got = full(tmp_path, monkeypatch, spots, cache, db)
    assert rc == 0
    assert got == {"new": {"description": text_of("New", 2), "signature": sig_of("New")},
                   "old": {"description": text_of("Old", 1), "signature": sig_of("Old")},
                   "same": {"description": text_of("Same", 1), "signature": sig_of("Same")}}
    assert db.updates == [
        ("new", {"description": text_of("New", 2), "description_signature": sig_of("New")}),
        ("old", {"description": text_of("Old", 1), "description_signature": sig_of("Old")})]


def test_nothing_is_written_when_nothing_changed(tmp_path, monkeypatch, no_exclusions):
    cache = {"same": {"description": text_of("Same", 1), "signature": sig_of("Same")}}
    db = FakeDB([{"slug": "same", "description": text_of("Same", 1),
                  "description_signature": sig_of("Same")}])
    rc, got = full(tmp_path, monkeypatch, [spot("Same")], cache, db)
    assert (rc, got, db.updates) == (0, cache, [])


def test_an_unwritten_description_is_kept_and_written_next_run(tmp_path, monkeypatch,
                                                                no_exclusions):
    db = FakeDB([{"slug": "a"}, {"slug": "b"}], fail={"b"})
    rc, got = full(tmp_path, monkeypatch, [spot("A"), spot("B")], {}, db)
    assert rc == 1
    assert got["b"] == {"description": text_of("B", 0), "signature": sig_of("B"), "pushed": False}
    assert got["a"] == {"description": text_of("A", 1), "signature": sig_of("A")}

    db.fail.clear()
    rc, got = full(tmp_path, monkeypatch, [spot("A"), spot("B")], got, db)
    assert rc == 0
    assert got["b"] == {"description": text_of("B", 0), "signature": sig_of("B")}
    assert db.updates[-1] == ("b", {"description": text_of("B", 0),
                                    "description_signature": sig_of("B")})


def test_a_spot_with_no_row_yet_is_kept_unwritten(tmp_path, monkeypatch, no_exclusions):
    rc, got = full(tmp_path, monkeypatch, [spot("A")], {}, FakeDB([]))
    assert rc == 1 and got["a"]["pushed"] is False


def test_the_run_resumes_where_it_stopped(tmp_path, monkeypatch, no_exclusions):
    spots = [spot("A"), spot("B"), spot("C")]
    db = FakeDB([{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    rc, got = full(tmp_path, monkeypatch, spots, {}, db, extra=("--limit", "2"))
    assert rc == 0 and sorted(got) == ["a", "b"]
    db.updates.clear()
    rc, got = full(tmp_path, monkeypatch, spots, got, db)
    assert rc == 0 and sorted(got) == ["a", "b", "c"]
    assert db.updates == [("c", {"description": text_of("C", 2),
                                 "description_signature": sig_of("C")})]


def test_the_reconcile_puts_back_a_row_a_concurrent_run_reverted(tmp_path, monkeypatch,
                                                                  no_exclusions):
    """A forecast-pipeline run that read the row before the generator wrote it upserts the old text
    and the version-1 signature back. The cache is current, so the spot is not rewritten, but the
    reconcile sees the row differ and writes it again."""
    cache = {"same": {"description": text_of("Same", 1), "signature": sig_of("Same")}}
    db = FakeDB([{"slug": "same", "description": "Same is a beach break.",
                  "description_signature": "0123456789abcdef"}])
    rc, _got = full(tmp_path, monkeypatch, [spot("Same")], cache, db)
    assert rc == 0
    assert db.updates == [("same", {"description": text_of("Same", 1),
                                    "description_signature": sig_of("Same")})]


def test_the_reconcile_also_compares_the_text(tmp_path, monkeypatch, no_exclusions):
    cache = {"same": {"description": text_of("Same", 1), "signature": sig_of("Same")}}
    db = FakeDB([{"slug": "same", "description": "Edited in the table.",
                  "description_signature": sig_of("Same")}])
    rc, _got = full(tmp_path, monkeypatch, [spot("Same")], cache, db)
    assert rc == 0
    assert db.updates == [("same", {"description": text_of("Same", 1),
                                    "description_signature": sig_of("Same")})]


def test_no_db_needs_no_key_keeps_everything_unwritten_and_never_opens_supabase(
        tmp_path, monkeypatch, no_exclusions):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    forbid(monkeypatch, "get_client")
    roster, out = write(tmp_path, [spot("A")], {})
    assert GD.main(["--no-db", "--input", str(roster), "--output", str(out)]) == 0
    assert json.loads(out.read_text()) == {
        "a": {"description": text_of("A", 1), "signature": sig_of("A"), "pushed": False}}


def test_a_rejected_text_keeps_the_description_and_gets_no_signature(tmp_path, monkeypatch,
                                                                     capsys, no_exclusions):
    """The cache entry and the table row are left exactly as they were, the run fails, and the
    end-of-run report names the spot. The other spot is written as usual."""
    monkeypatch.setattr(GD, "render", bad_render("pensacola-beach"))
    spots = [spot("Pensacola Beach"), spot("A")]
    old = {"description": "Pensacola Beach is a beach break.", "signature": "0123456789abcdef"}
    db = FakeDB([{"slug": "pensacola-beach", "description": old["description"],
                  "description_signature": old["signature"]}, {"slug": "a"}])
    rc, got = full(tmp_path, monkeypatch, spots, {"pensacola-beach": dict(old)}, db)
    assert rc == 1
    assert got["pensacola-beach"] == old
    assert db.rows["pensacola-beach"] == {"slug": "pensacola-beach",
                                          "description": old["description"],
                                          "description_signature": old["signature"]}
    assert [slug for slug, _p in db.updates] == ["a"]
    assert capsys.readouterr().out.splitlines() == [
        "Output check: 1 spot(s) rejected — existing description kept, no signature written:",
        "  pensacola-beach — self-talk or meta text: 'wait'; does not include the spot's name",
        "    text: Wait.",
    ]


def test_a_clean_run_reports_no_rejection(tmp_path, monkeypatch, capsys, no_exclusions):
    rc, _got = full(tmp_path, monkeypatch, [spot("A")], {}, FakeDB([{"slug": "a"}]))
    assert rc == 0 and capsys.readouterr().out == "Output check: no spot rejected.\n"


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
    assert (pick["Seaside Reef"]["tide_preference"], pick["Seaside Reef"]["tide_preference_source"]) == (
        "low_mid", "researched")
    assert "tide_preference" not in pick["Agate Beach"]
    assert {pick[n]["region_hint"] for n in ("Banzai Pipeline", "Waimea Bay")} == {"Hawaii"}
    assert {pick[n]["region_hint"] for n in ("Steamer Lane", "Malibu Surfrider Beach")} == {
        "California"}


# --------------------------------------------------------------------------- #
# 6 — the output check                                                         #
# --------------------------------------------------------------------------- #
# The reply the second sample run (2026-10-13) saved as Pensacola Beach's description, copied
# from its terminal printout: line breaks as printed, the dash after "revise" an em dash.
PENSACOLA_REPLY = (
    "Pensacola Beach is a surf spot in Florida that faces south and works best with swells from "
    "the south. Offshore winds blow from the north, helping to clean up the surf. No bottom type "
    "or tide preference is specified for this location.\n\n---\n\nWait, I need to revise — I was "
    "instructed not to mention missing fields. Here is the corrected version:\n\nPensacola Beach "
    "is a surf spot in Florida that faces south and is best suited for swells from the south. "
    "Offshore winds blow from the north.")
# Its break type is model_recall, so the payload has none.
PENSACOLA_PAYLOAD = {"name": "Pensacola Beach", "state": "Florida", "faces": "S",
                     "orientation_deg": 180, "offshore_wind_from": "N", "offshore_wind_deg": 0,
                     "optimal_swell_from": "S"}


def test_the_pensacola_reply_fails_on_every_count_it_should():
    # 88 words, counted by hand: 19 + 12 + 11 in the first paragraph, 1 for "---", 14 + 5 in the
    # self-talk, 20 + 6 in the second description.
    assert GD.check_description(PENSACOLA_REPLY, PENSACOLA_PAYLOAD) == [
        "more than one paragraph (line break)",
        "contains '---'",
        "self-talk or meta text: 'I', 'corrected', 'here is', 'instructed', 'revise', 'wait'",
        "mentions an absent field: 'No bottom type or tide preference is specified', 'missing'",
    ]


def test_its_corrected_second_paragraph_alone_passes():
    assert GD.check_description(
        "Pensacola Beach is a surf spot in Florida that faces south and is best suited for swells "
        "from the south. Offshore winds blow from the north.", PENSACOLA_PAYLOAD) == []


PIPE = {"name": "Banzai Pipeline", "state": "Hawaii", "break_type": "reef", "bottom": "rock",
        "faces": "NW"}
PIPE_TEXT = ("Banzai Pipeline is a reef break in Hawaii over a rock bottom. It faces northwest and "
             "is offshore in southeast winds.")


def test_a_good_description_passes():
    assert GD.check_description(PIPE_TEXT, PIPE) == []
    assert GD.check_line([]) == "pass"
    assert GD.check_line(["a", "b"]) == "FAIL — a; b"


@pytest.mark.parametrize("text, problems", [
    (PIPE_TEXT.replace(". It", ".\nIt"), ["more than one paragraph (line break)"]),
    (PIPE_TEXT.replace(". It", ".\r It"), ["more than one paragraph (line break)"]),
    (PIPE_TEXT + " ---", ["contains '---'"]),
    ("Here's the description: " + PIPE_TEXT, ["self-talk or meta text: \"here's\""]),
    (PIPE_TEXT + " I hope this helps.", ["self-talk or meta text: 'I'"]),
    (PIPE_TEXT + " Wait for a north swell.", ["self-talk or meta text: 'wait'"]),
    (PIPE_TEXT + " Revised per the metadata.", ["self-talk or meta text: 'metadata', 'revised'"]),
    (PIPE_TEXT + " The tide preference is not specified.",
     ["mentions an absent field: 'not specified'"]),
    (PIPE_TEXT + " No tide preference is given.",
     ["mentions an absent field: 'No tide preference is given'"]),
    (PIPE_TEXT + " Its tide is unknown.", ["mentions an absent field: 'unknown'"]),
    (PIPE_TEXT.replace("Banzai Pipeline", "Pipeline"), ["does not include the spot's name"]),
    (PIPE_TEXT.replace("a reef break", "a beach break"),
     ["does not state the break type 'reef'", "names another break type: beach"]),
    (PIPE_TEXT.replace("a reef break", "a surf spot"), ["does not state the break type 'reef'"]),
    (PIPE_TEXT + " It sits beside a jetty.", ["names another break type: jetty"]),
])
def test_each_rule_on_its_own(text, problems):
    assert GD.check_description(text, PIPE) == problems


def test_a_hundred_and_twenty_words_pass_and_one_more_fails():
    payload = {"name": "Pipe"}
    assert GD.check_description("Pipe" + " swell" * 119, payload) == []           # 1 + 119 = 120
    assert GD.check_description("Pipe" + " swell" * 120, payload) == ["121 words (at most 120)"]


def test_nearby_and_station_names_are_not_read_as_its_words():
    payload = {"name": "Avalon", "break_type": "beach", "nearby": [["Avalon Jetty", 1]],
               "tide_station": ["8536110", "Cape May, Fishing Creek", 9, False]}
    text = ("Avalon is a beach break in New Jersey. Its tides come from NOAA's Cape May, Fishing Creek "
            "station (8536110), 9 km away. Nearby spots: Avalon Jetty (1 km).")
    assert GD.check_description(text, payload) == []


@pytest.mark.parametrize("text", [
    "Pensacola Beach is a beach break.", "Pensacola Beach is a reef break.",
    "Pensacola Beach is a point break.", "Pensacola Beach breaks beside a jetty.",
    "Pensacola Beach is a river mouth.", "Pensacola Beach is a rivermouth."])
def test_no_break_type_may_be_named_when_none_is_given(text):
    named = {"beach": "beach", "reef": "reef", "point": "point", "jetty": "jetty",
             "river": "rivermouth"}[next(w for w in ("beach break", "reef", "point", "jetty",
                                                    "river") if w in text).split()[0]]
    assert GD.check_description(text, PENSACOLA_PAYLOAD) == [
        f"names a break type it was not given: {named}"]


def test_a_reef_or_point_given_may_be_stated_loosely():
    reef = dict(PIPE, name="Pipe")
    assert GD.check_description("Pipe breaks over a shallow reef.", reef) == []
    point = {"name": "Rincon", "break_type": "point"}
    assert GD.check_description("Rincon is a right-hand point.", point) == []
    assert GD.check_description("Rincon is a point break.", point) == []
    beach = {"name": "Asbury Park", "break_type": "beach"}
    assert GD.check_description("Asbury Park is a beach break near the boardwalk.", beach) == []
    assert GD.check_description("Asbury Park is a sandy beach.", beach) == [
        "does not state the break type 'beach'"]


def test_the_name_is_found_as_whole_words_only():
    assert GD.check_description("Wait.", {"name": "A"}) == [
        "self-talk or meta text: 'wait'", "does not include the spot's name"]
    assert GD.check_description("Lido Beach is a surf spot.", {"name": "Lido"}) == []


def test_the_name_is_removed_as_whole_words_only():
    assert GD.check_description("A. Wait for it.", {"name": "A"}) == ["self-talk or meta text: 'wait'"]
    assert GD.check_description("Pools is a surf spot near whirlpools.", {"name": "Pools"}) == []


def test_the_name_is_read_out_before_the_rules_and_matched_loosely():
    # a name holding a break type or "I" is not read as one
    assert GD.check_description("Avalon Jetty is a surf spot.", {"name": "Avalon Jetty"}) == []
    assert GD.check_description("Chuns Reef is a surf spot.",
                                {"name": "Chuns Reef", "break_type": "reef"}) == [
        "does not state the break type 'reef'"]
    assert GD.check_description("Haleiwa Ali'i Beach is a beach break.",
                                {"name": "Haleiwa Ali'i Beach", "break_type": "beach"}) == []
    # okina, accents and punctuation do not matter
    assert GD.check_description("Ho'okipa is a surf spot.", {"name": "Hoʻokipa"}) == []
    assert GD.check_description("Surf City, NJ is a surf spot.", {"name": "Surf City NJ"}) == []
    assert GD.check_description("St. Simons Island is a surf spot.",
                                {"name": "St Simons Island"}) == []
    # an apostrophe may be dropped, not just changed
    assert GD.check_description("Marias is a surf spot.", {"name": "Maria's"}) == []
    # the part before a comma is enough
    assert GD.check_description("Atlantic Beach is a surf spot.",
                                {"name": "Atlantic Beach, NC"}) == []


# --------------------------------------------------------------------------- #
# 7 — the template                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload, text", [
    ({"name": "Banzai Pipeline", "state": "Hawaii", "break_type": "reef", "bottom": "rock"},
     "Banzai Pipeline is a reef break in Hawaii over a rock bottom."),
    ({"name": "Rincon", "state": "California", "break_type": "point", "hand": "right",
      "exposure": "exposed", "bottom": "cobble"},
     "Rincon is an exposed right-hand point break in California over a cobble bottom."),
    ({"name": "Cardiff Reef", "state": "California", "break_type": "reef", "hand": "both"},
     "Cardiff Reef is a reef break in California with both lefts and rights."),
    ({"name": "Doran", "state": "California", "break_type": "beach", "exposure": "sheltered"},
     "Doran is a sheltered beach break in California."),
    ({"name": "Agate Beach", "state": "California"}, "Agate Beach is a surf spot in California."),
    ({"name": "Agate Beach", "state": "California", "bottom": "sand"},
     "Agate Beach is a surf spot in California over a sand bottom."),
    ({"name": "Matanzas Inlet", "state": "Florida", "break_type": "jetty"},
     "Matanzas Inlet is a jetty break in Florida."),
    ({"name": "Sunset Beach Hawaii", "state": "Hawaii", "break_type": "reef"},
     "Sunset Beach Hawaii is a reef break."),
    ({"name": "Spot"}, "Spot is a surf spot."),
])
def test_the_first_sentence(payload, text):
    assert Dn.render(payload, "x") == text


@pytest.mark.parametrize("value, sentence", list({
    "low": "It works best around low tide.", "low_mid": "It works best from low to mid tide.",
    "mid": "It works best around mid tide.", "mid_high": "It works best from mid to high tide.",
    "high": "It works best around high tide.", "all": "It works at all stages of the tide."}.items()))
def test_a_stated_tide_adds_one_plain_sentence(value, sentence):
    assert Dn.render({"name": "Spot", "state": "Oregon", "tide_preference": value}) == (
        "Spot is a surf spot in Oregon. " + sentence)


def test_every_spot_in_the_committed_roster_passes_the_check(monkeypatch):
    """The template against the real roster and the committed files: no text the full run would
    reject, none mentions the swell window, and each names exactly the break type its payload
    gives."""
    monkeypatch.undo()                               # the committed files' Context, not the empty one
    Dn.default_context.cache_clear()
    roster = GD.roster_spots(json.loads(
        (ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8")))
    assert len(roster) >= 600
    for slug, s in roster.items():
        payload = Dn.description_payload(Dn.spot_fields(s))
        text = Dn.render(payload, slug)
        assert GD.check_description(text, payload) == [], (slug, text)
        assert "window" not in text.lower(), (slug, text)
