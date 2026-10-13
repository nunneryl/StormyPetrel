"""Spot descriptions: what the payload may state and how the template writes it
(pipeline/descriptions.py), and the generator's three modes (pipeline/generate_descriptions.py).

WHAT IS PINNED HERE:
  1. the payload: break_type and bottom only from reviewed, researched or scraped; no buoy or tide
     station ids; nothing null; roster names mapped as db_import maps them;
  2. signature v2: literal values computed with hashlib directly, the TEMPLATE_VERSION in the hash,
     and the template's fingerprint, so editing its wording without bumping the version fails here;
  3. the plan: generate / write-only / keep, with the reason, and orphan pruning;
  4. --dry-run writes nothing and prints every final text; --sample writes its own file only;
     the full run writes only what it touched, keeps what it could not write, resumes, and
     reconciles rows a concurrent run put back;
  5. the ten sample spots exist in the committed roster and are the cases they stand for;
  6. the output check, on the reply a model once gave for Pensacola Beach and rule by rule;
  7. the template: the exact text of each pattern, with and without break type, bottom and the
     directions, and every spot in the committed roster passing the output check.

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

STEAMER = {"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
           "orientation_deg": 128, "offshore_wind_deg": 308, "optimal_swell_dir": 225,
           "swell_window_arcs": [{"min": 180, "max": 315}], "tide_preference": "all",
           "tide_preference_source": "researched", "break_type": "point", "break_type_source": "researched",
           "bottom": "rock", "bottom_source": "researched",
           "nearest_buoy_id": "46269", "nearest_tide_station_id": "9413745"}
STEAMER_PAYLOAD = {"name": "Steamer Lane", "state": "California", "break_type": "point",
                   "bottom": "rock", "faces": "SE", "orientation_deg": 128,
                   "offshore_wind_from": "NW", "offshore_wind_deg": 308, "tide_preference": "all",
                   "optimal_swell_from": "SW", "optimal_swell_deg": 225}
# 'v2:' + sha1("t2\n" + canonical JSON)[:16], computed with hashlib outside the code under test:
STEAMER_V2 = "v2:fb3e7d4f849945e3"
STEAMER_V2_LEFT_OUT = "v2:380c5caa202c9bf2"   # the same with break_type and bottom left out


# --------------------------------------------------------------------------- #
# 1 — the payload                                                              #
# --------------------------------------------------------------------------- #
def test_the_payload_of_a_researched_spot():
    assert Dn.description_payload(Dn.spot_fields(STEAMER)) == STEAMER_PAYLOAD


def test_the_payload_keeps_its_key_order():
    assert list(Dn.description_payload(Dn.spot_fields(STEAMER))) == [
        "name", "state", "break_type", "bottom", "faces", "orientation_deg", "offshore_wind_from",
        "offshore_wind_deg", "tide_preference", "optimal_swell_from", "optimal_swell_deg"]


@pytest.mark.parametrize("source, kept", [
    ("reviewed", True), ("researched", True), ("scraped", True),
    ("model_recall", False), ("unattributed", False), ("algorithm_default", False),
    (None, False), ("", False), ("REVIEWED", False)])
def test_only_a_source_that_can_be_stated_puts_a_value_in(source, kept):
    spot = dict(STEAMER, break_type_source=source, bottom_source=source,
                tide_preference_source=source)
    payload = Dn.description_payload(Dn.spot_fields(spot))
    assert ("break_type" in payload, "bottom" in payload, "tide_preference" in payload) == (
        kept, kept, kept)


@pytest.mark.parametrize("source", ["derived_from_break_type", "unattributed", "unknown", None])
def test_a_tide_without_a_stated_source_is_left_out(source):
    spot = dict(STEAMER, tide_preference_source=source)
    assert "tide_preference" not in Dn.description_payload(Dn.spot_fields(spot))


def test_a_tide_with_no_source_key_at_all_is_left_out():
    """The committed roster today: 444 tide values, not one with a source."""
    spot = {k: v for k, v in STEAMER.items() if k != "tide_preference_source"}
    assert "tide_preference" not in Dn.description_payload(Dn.spot_fields(spot))


def test_an_unknown_tide_states_nothing():
    spot = dict(STEAMER, tide_preference="unknown")
    assert "tide_preference" not in Dn.description_payload(Dn.spot_fields(spot))


def test_the_swell_window_is_never_sent():
    payload = Dn.description_payload(Dn.spot_fields(STEAMER))
    assert "swell_window" not in payload and "swell_window_arcs" not in payload
    assert "swell_window_arcs" not in Dn.spot_fields(STEAMER)


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
    # an empty string is dropped like a null, not sent as ""
    assert Dn.description_payload(Dn.spot_fields(dict(bare, region_hint=""))) == {
        "name": "Agate Beach"}


def test_degrees_are_whole_and_cardinals_have_eight_points():
    spot = {"name": "X", "region_hint": "Y", "orientation_deg": 22.4, "offshore_wind_deg": 202.6,
            "optimal_swell_dir": 337.6, "swell_window_arcs": [{"min": 350, "max": 10}, {"min": 90}]}
    assert Dn.description_payload(Dn.spot_fields(spot)) == {
        "name": "X", "state": "Y", "faces": "N", "orientation_deg": 22,
        # 202.6 / 45 = 4.502, just past the S/SW midpoint at 202.5, so SW
        "offshore_wind_from": "SW", "offshore_wind_deg": 203, "optimal_swell_from": "N",
        "optimal_swell_deg": 338}
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


def test_the_template_version_is_in_the_signature(monkeypatch):
    monkeypatch.setattr(Dn, "TEMPLATE_VERSION", "t3")
    expected = "v2:" + hashlib.sha1(("t3\n" + json.dumps(
        STEAMER_PAYLOAD, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    ).encode()).hexdigest()[:16]
    assert Dn.description_signature(STEAMER_PAYLOAD) == expected != STEAMER_V2


def template_source():
    parts = (Dn._an, Dn._sentence, Dn._degrees_apart, Dn._swell_behind, Dn._name_holds, Dn._facts,
             *Dn.PATTERNS)
    return ("".join(inspect.getsource(f) for f in parts) + repr(Dn.DIRECTION_WORDS)
            + repr(Dn.BREAK_WORDS) + Dn.NO_BREAK_TYPE + repr(Dn.SWELL_MAX_OFF_FACING_DEG))


def test_the_template_cannot_change_without_a_new_version():
    """Edit the template's wording and this fails: bump TEMPLATE_VERSION, then record the new
    fingerprint. Every description written by the old wording is then rewritten."""
    assert (Dn.TEMPLATE_VERSION, hashlib.sha1(template_source().encode()).hexdigest()[:12]) == (
        "t2", "28b70373f076")


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
    same_sig = "v2:" + hashlib.sha1(
        b't2\n{"faces":"NW","name":"Same","orientation_deg":315,"state":"Hawaii"}').hexdigest()[:16]
    unwritten_sig = "v2:" + hashlib.sha1(
        b't2\n{"faces":"NW","name":"Unwritten","orientation_deg":315,"state":"Hawaii"}'
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
    return "v2:" + hashlib.sha1(('t2\n{"faces":"NW","name":"%s","orientation_deg":315,'
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


def text_of(name, pattern):
    """What the template writes for spot(name): Hawaii, facing northwest, nothing else known.
    Written out by hand for each of the three patterns."""
    return {0: f"{name} is a surf spot in Hawaii. It faces northwest.",
            1: f"{name} is a Hawaii surf spot. It faces northwest.",
            2: f"Facing northwest, {name} is a surf spot in Hawaii."}[pattern]


# Which pattern each slug used below gets: sha1(slug) mod 3, computed with hashlib directly.
#   a 1, b 0, c 2, new 2, old 1, same 1, unwritten 0, banzai-pipeline 1, pensacola-beach 2,
#   aviones 0, waimea-bay 2, steamer-lane 1


def test_the_pattern_picks_are_pinned():
    picks = {"a": 1, "b": 0, "c": 2, "new": 2, "old": 1, "same": 1, "unwritten": 0,
             "banzai-pipeline": 1, "pensacola-beach": 2, "aviones": 0, "waimea-bay": 2,
             "steamer-lane": 1}
    assert {slug: Dn.pattern_for(slug) for slug in picks} == picks


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
        '    payload:  {"name": "Banzai Pipeline", "state": "Hawaii", "break_type": "reef", '
        '"faces": "NW", "orientation_deg": 315}',
        "    pattern:  1",
        "    check:    pass",
        "    final:    Banzai Pipeline is a Hawaii reef break. It faces northwest.",
        "[keep] same — current",
        "    final:    Same is a Hawaii surf spot. It faces northwest.",
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
        "  patterns among the texts to write: 1 x1",
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
        "    pattern:  1",
        "    check:    FAIL — self-talk or meta text: 'wait'; does not include the spot's name",
        "    final:    (unchanged) A was here.",
    ]
    assert "    final:    B is a surf spot in Hawaii. It faces northwest." in lines
    assert "  new texts failing the output check (would be rejected): 1" in lines
    assert "  patterns among the texts to write: 0 x1, 1 x1" in lines


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
    assert got["aviones"] == {"name": "Aviones", "payload": {"name": "Aviones", "state": "Hawaii",
                                                             "faces": "NW", "orientation_deg": 315},
                              "pattern": 0, "check": "pass", "signature": sig_of("Aviones"),
                              "description": text_of("Aviones", 0)}
    printed = capsys.readouterr().out
    assert ("== Banzai Pipeline (banzai-pipeline)\n"
            "   left out: break_type not set, bottom not set, tide_preference not set\n"
            '   payload:  {"name": "Banzai Pipeline", "state": "Hawaii", "faces": "NW", '
            '"orientation_deg": 315}\n'
            "   pattern 1, check: pass\n"
            "   Banzai Pipeline is a Hawaii surf spot. It faces northwest.\n\n") in printed
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
    assert (f"   pattern 2, check: {fail}\n   Wait.\n"
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
    assert "tide_preference" not in pick["Seaside Reef"]
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
        "88 words (at most 80)",
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


def test_eighty_words_pass_and_eighty_one_fail():
    payload = {"name": "Pipe"}
    assert GD.check_description("Pipe" + " swell" * 79, payload) == []            # 1 + 79 = 80
    assert GD.check_description("Pipe" + " swell" * 80, payload) == ["81 words (at most 80)"]


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
FULL = {"name": "Banzai Pipeline", "state": "Hawaii", "break_type": "reef", "bottom": "rock",
        "faces": "NW", "orientation_deg": 315, "offshore_wind_from": "SE",
        "offshore_wind_deg": 135, "optimal_swell_from": "N", "optimal_swell_deg": 0}
NO_BOTTOM = {k: v for k, v in FULL.items() if k != "bottom"}
NO_TYPE = {k: v for k, v in FULL.items() if k != "break_type"}
NEITHER = {k: v for k, v in FULL.items() if k not in ("break_type", "bottom")}


def in_pattern(payload, n):
    return " ".join(x for x in Dn.PATTERNS[n](Dn._facts(payload)) if x)


@pytest.mark.parametrize("payload, n, text", [
    (FULL, 0, "Banzai Pipeline is a reef break in Hawaii over a rock bottom. It faces northwest, and "
              "its optimal swell direction is north. Offshore winds blow from the southeast."),
    (FULL, 1, "Banzai Pipeline is a Hawaii reef break with a rock bottom. It faces northwest, and it "
              "is offshore in southeast winds. The optimal swell comes from the north."),
    (FULL, 2, "Facing northwest, Banzai Pipeline is a reef break in Hawaii over a rock bottom. The "
              "optimal swell comes from the north, and the offshore wind is from the southeast."),
    (NO_BOTTOM, 0, "Banzai Pipeline is a reef break in Hawaii. It faces northwest, and its optimal "
                   "swell direction is north. Offshore winds blow from the southeast."),
    (NO_BOTTOM, 1, "Banzai Pipeline is a Hawaii reef break. It faces northwest, and it is offshore "
                   "in southeast winds. The optimal swell comes from the north."),
    (NO_BOTTOM, 2, "Facing northwest, Banzai Pipeline is a reef break in Hawaii. The optimal swell "
                   "comes from the north, and the offshore wind is from the southeast."),
    (NO_TYPE, 0, "Banzai Pipeline is a surf spot in Hawaii over a rock bottom. It faces northwest, "
                 "and its optimal swell direction is north. Offshore winds blow from the southeast."),
    (NO_TYPE, 1, "Banzai Pipeline is a Hawaii surf spot with a rock bottom. It faces northwest, and "
                 "it is offshore in southeast winds. The optimal swell comes from the north."),
    (NO_TYPE, 2, "Facing northwest, Banzai Pipeline is a surf spot in Hawaii over a rock bottom. The "
                 "optimal swell comes from the north, and the offshore wind is from the southeast."),
    (NEITHER, 0, "Banzai Pipeline is a surf spot in Hawaii. It faces northwest, and its optimal "
                 "swell direction is north. Offshore winds blow from the southeast."),
    (NEITHER, 1, "Banzai Pipeline is a Hawaii surf spot. It faces northwest, and it is offshore in "
                 "southeast winds. The optimal swell comes from the north."),
    (NEITHER, 2, "Facing northwest, Banzai Pipeline is a surf spot in Hawaii. The optimal swell "
                 "comes from the north, and the offshore wind is from the southeast."),
])
def test_each_pattern_word_for_word(payload, n, text):
    assert in_pattern(payload, n) == text
    assert GD.check_description(text, payload) == []


@pytest.mark.parametrize("drop, texts", [
    ("faces", ("Banzai Pipeline is a reef break in Hawaii over a rock bottom. Its optimal swell "
               "direction is north. Offshore winds blow from the southeast.",
               "Banzai Pipeline is a Hawaii reef break with a rock bottom. It is offshore in "
               "southeast winds. The optimal swell comes from the north.",
               "Banzai Pipeline is a reef break in Hawaii over a rock bottom. The optimal swell "
               "comes from the north, and the offshore wind is from the southeast.")),
    ("optimal_swell_from", ("Banzai Pipeline is a reef break in Hawaii over a rock bottom. It faces "
                            "northwest. Offshore winds blow from the southeast.",
                            "Banzai Pipeline is a Hawaii reef break with a rock bottom. It faces "
                            "northwest, and it is offshore in southeast winds.",
                            "Facing northwest, Banzai Pipeline is a reef break in Hawaii over a "
                            "rock bottom. The offshore wind is from the southeast.")),
    ("offshore_wind_from", ("Banzai Pipeline is a reef break in Hawaii over a rock bottom. It faces "
                            "northwest, and its optimal swell direction is north.",
                            "Banzai Pipeline is a Hawaii reef break with a rock bottom. It faces "
                            "northwest. The optimal swell comes from the north.",
                            "Facing northwest, Banzai Pipeline is a reef break in Hawaii over a "
                            "rock bottom. The optimal swell comes from the north.")),
    ("state", ("Banzai Pipeline is a reef break over a rock bottom. It faces northwest, and its "
               "optimal swell direction is north. Offshore winds blow from the southeast.",
               "Banzai Pipeline is a reef break with a rock bottom. It faces northwest, and it is "
               "offshore in southeast winds. The optimal swell comes from the north.",
               "Facing northwest, Banzai Pipeline is a reef break over a rock bottom. The optimal "
               "swell comes from the north, and the offshore wind is from the southeast.")),
])
def test_an_absent_field_is_simply_left_out(drop, texts):
    payload = {k: v for k, v in FULL.items() if k != drop}
    assert tuple(in_pattern(payload, n) for n in range(3)) == texts


def test_name_and_state_alone():
    bare = {"name": "Agate Beach", "state": "California"}
    assert [in_pattern(bare, n) for n in range(3)] == [
        "Agate Beach is a surf spot in California.", "Agate Beach is a California surf spot.",
        "Agate Beach is a surf spot in California."]
    assert [in_pattern({"name": "X"}, n) for n in range(3)] == ["X is a surf spot."] * 3


def test_a_and_an():
    oregon = {"name": "Nye Beach", "state": "Oregon", "break_type": "beach"}
    assert in_pattern(oregon, 1) == "Nye Beach is an Oregon beach break."
    assert in_pattern(oregon, 0) == "Nye Beach is a beach break in Oregon."
    assert [Dn._an(w) for w in ("Oregon", "Indiana", "Hawaii", "reef break", "")] == [
        "an", "an", "a", "a", "a"]


@pytest.mark.parametrize("break_type, bottom, words", [
    ("beach", "sand", "a beach break in Florida over a sand bottom"),
    ("point", "cobble", "a point break in Florida over a cobble bottom"),
    ("jetty", "sand", "a jetty break in Florida over a sand bottom"),
    ("rivermouth", "mixed", "a rivermouth break in Florida over a mixed bottom"),
    ("reef", "coral", "a reef break in Florida over a coral bottom"),
])
def test_every_break_type_and_bottom_reads_plainly(break_type, bottom, words):
    payload = {"name": "Spot", "state": "Florida", "break_type": break_type, "bottom": bottom}
    assert in_pattern(payload, 0) == f"Spot is {words}."
    assert GD.check_description(in_pattern(payload, 0), payload) == []


def test_every_direction_has_its_word():
    for code, word in [("N", "north"), ("NE", "northeast"), ("E", "east"), ("SE", "southeast"),
                       ("S", "south"), ("SW", "southwest"), ("W", "west"), ("NW", "northwest")]:
        assert in_pattern({"name": "X", "faces": code}, 0) == f"X is a surf spot. It faces {word}."


def test_render_uses_the_slugs_pattern():
    assert Dn.render(FULL, "banzai-pipeline") == in_pattern(FULL, 1)
    assert Dn.render(FULL, "aviones") == in_pattern(FULL, 0)
    assert Dn.render(FULL, "pensacola-beach") == in_pattern(FULL, 2)


def test_the_tide_is_never_stated():
    with_tide = dict(FULL, tide_preference="low")
    assert [in_pattern(with_tide, n) for n in range(3)] == [in_pattern(FULL, n) for n in range(3)]


def test_every_spot_in_the_committed_roster_passes_the_check():
    """The template against the real roster: no text the full run would reject, none mentions
    tide or the swell window, and each names exactly the break type its payload gives."""
    roster = GD.roster_spots(json.loads(
        (ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8")))
    assert len(roster) >= 600
    for slug, s in roster.items():
        payload = Dn.description_payload(Dn.spot_fields(s))
        text = Dn.render(payload, slug)
        assert GD.check_description(text, payload) == [], (slug, text)
        assert "tide" not in text.lower() and "window" not in text.lower(), (slug, text)



# --------------------------------------------------------------------------- #
# 8 — a swell from behind the spot, and a state already in the name            #
# --------------------------------------------------------------------------- #
# Spoils as the roster gives it: faces east (95°) with an optimal swell from the west (292°), 163°
# apart. The swell clause is left out of every pattern; the value stays in the payload.
SPOILS = {"name": "Spoils", "state": "Florida", "break_type": "jetty", "bottom": "rock",
          "faces": "E", "orientation_deg": 95, "offshore_wind_from": "W", "offshore_wind_deg": 275,
          "optimal_swell_from": "W", "optimal_swell_deg": 292}


def test_a_swell_from_behind_is_left_out_of_every_pattern():
    assert [in_pattern(SPOILS, n) for n in range(3)] == [
        "Spoils is a jetty break in Florida over a rock bottom. It faces east. Offshore winds blow "
        "from the west.",
        "Spoils is a Florida jetty break with a rock bottom. It faces east, and it is offshore in "
        "west winds.",
        "Facing east, Spoils is a jetty break in Florida over a rock bottom. The offshore wind is "
        "from the west.",
    ]


@pytest.mark.parametrize("facing, swell, stated", [
    (95, 185, True),     # 90 apart: stated
    (95, 186, False),    # 91 apart: left out
    (350, 80, True),     # 90 apart across north
    (350, 81, False),    # 91 apart across north
    (10, 280, True),     # 90 apart the other way across north
    (10, 279, False),
    (180, 0, False),     # straight behind
])
def test_more_than_ninety_degrees_is_behind(facing, swell, stated):
    payload = dict(SPOILS, orientation_deg=facing, optimal_swell_deg=swell)
    assert ("optimal swell direction is west" in in_pattern(payload, 0)) is stated


def test_the_swell_is_stated_when_the_facing_or_its_degrees_are_unknown():
    for drop in ("orientation_deg", "optimal_swell_deg"):
        payload = {k: v for k, v in SPOILS.items() if k != drop}
        assert "its optimal swell direction is west" in in_pattern(payload, 0), drop


def test_the_held_back_swell_stays_in_the_payload_and_signature():
    """A corrected optimal swell (here 100°, in front of the spot) changes the signature, so the
    text is rewritten, now with the swell in it."""
    spot_ = {"name": "Spoils", "region_hint": "Florida", "orientation_deg": 95,
             "offshore_wind_deg": 275, "optimal_swell_dir": 292}
    behind = Dn.description_payload(Dn.spot_fields(spot_))
    assert (behind["optimal_swell_from"], behind["optimal_swell_deg"]) == ("W", 292)
    fixed = Dn.description_payload(Dn.spot_fields(dict(spot_, optimal_swell_dir=100)))
    assert (fixed["optimal_swell_from"], fixed["optimal_swell_deg"]) == ("E", 100)
    assert Dn.description_signature(behind) != Dn.description_signature(fixed)
    assert "optimal swell" not in Dn.render(behind, "spoils")
    assert "optimal swell" in Dn.render(fixed, "spoils")


DANA = {"name": "Dana Point, California", "state": "California", "break_type": "beach",
        "faces": "W", "orientation_deg": 265, "offshore_wind_from": "E", "offshore_wind_deg": 85,
        "optimal_swell_from": "SW", "optimal_swell_deg": 225}


def test_a_state_already_in_the_name_is_not_stated_again():
    assert [in_pattern(DANA, n) for n in range(3)] == [
        "Dana Point, California is a beach break. It faces west, and its optimal swell direction "
        "is southwest. Offshore winds blow from the east.",
        "Dana Point, California is a beach break. It faces west, and it is offshore in east winds. "
        "The optimal swell comes from the southwest.",
        "Facing west, Dana Point, California is a beach break. The optimal swell comes from the "
        "southwest, and the offshore wind is from the east.",
    ]
    for n in range(3):
        assert GD.check_description(in_pattern(DANA, n), DANA) == []


@pytest.mark.parametrize("name, state, text", [
    ("North End Virginia Beach", "Virginia",
     "North End Virginia Beach is a beach break over a sand bottom."),
    ("Sunset Beach Hawaii", "Hawaii", "Sunset Beach Hawaii is a beach break over a sand bottom."),
    ("Pūpūkea, Hawaii", "Hawaii", "Pūpūkea, Hawaii is a beach break over a sand bottom."),
    ("sunset beach HAWAII", "Hawaii", "sunset beach HAWAII is a beach break over a sand bottom."),
    # not as a whole word: the state is stated
    ("Hawaiian Reef", "Hawaii", "Hawaiian Reef is a beach break in Hawaii over a sand bottom."),
    ("Westvirginia Point", "Virginia",
     "Westvirginia Point is a beach break in Virginia over a sand bottom."),
])
def test_which_names_hold_their_state(name, state, text):
    payload = {"name": name, "state": state, "break_type": "beach", "bottom": "sand"}
    assert in_pattern(payload, 0) == text


def test_the_two_cases_on_the_committed_roster():
    """Spot by spot, against the lists drawn up when the rules were set (2026-10-13)."""
    roster = GD.roster_spots(json.loads(
        (ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8")))
    behind, named = set(), set()
    for slug, s in roster.items():
        payload = Dn.description_payload(Dn.spot_fields(s))
        text = Dn.render(payload, slug)
        if "optimal_swell_from" in payload and "optimal swell" not in text:
            behind.add(slug)
        # the state stated outside the name, or not at all
        if payload.get("state") and payload["state"] not in text.replace(s["name"], ""):
            named.add(slug)
    assert behind == {
        "38th-avenue", "bolinas", "boneyard", "campus-point-ucsb",
        "cowell-s-beach-children-s-surfing-area", "crash-boat-beach", "doran-beach", "first-peak",
        "fort-point", "gas-chambers-aguadilla", "haggarty-s", "hapuna-beach", "honolua-bay",
        "leadbetter-beach", "lovers-point", "mackerricher", "marina-state-beach", "monterey-beach",
        "polihale-beach", "rat-beach", "richardson-beach", "sandspit", "second-peak", "spoils",
        "steamer-lane", "suicide-s", "table-tops-aguadilla", "the-hook", "thousand-peaks-maui",
        "topanga-beach", "wilderness"}
    assert named == {
        "dana-point-california", "hermosa-beach-california", "huntington-beach-california",
        "imperial-beach-california", "mavericks-california", "newport-beach-california",
        "redondo-beach-california", "surfside-california", "pine-trees-hawaii", "p-p-kea-hawaii",
        "sunset-beach-hawaii", "virginia-beach-oceanfront", "north-end-virginia-beach"}
