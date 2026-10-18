"""The free tide pass (pipeline/tide_free_pass.py): clean prose only, the domain rules, how pages and
spots combine, and the review files it writes instead of importing anything.

THE TWO JUNK CASES. The measurement run on the Mac mapped 15th Street Del Mar to 'low' from a
forecast widget ("Timezone … Forecast updated … Closes") and Carpinteria to 'mid' from
swell-intel.com's navigation links. The page text is on the Mac only, so WIDGET and NAV_LINKS below
are reconstructions of those two shapes, not the pages' own words; each is written so that
map_sentence alone WOULD map it, which is what makes it a failing case for the prose filter.

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import csv
import json
import os
import re

import pytest

from pipeline import config
from pipeline import tide_free_pass as tfp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# 15th Street Del Mar's shape: a forecast widget run together, with a "Low tide" and a "Good".
WIDGET = ("Timezone America/Los_Angeles Forecast updated 2 hours ago Surf 2-3ft Good "
          "Low tide 0.4ft at 5:42am Closes 7:00pm")
# Carpinteria's shape: swell-intel.com's navigation, a list of links, one of them about mid tide.
NAV_LINKS = ("* [Carpinteria Reef](https://www.swell-intel.com/carpinteria-reef) "
             "* [Rincon](https://www.swell-intel.com/rincon) "
             "* [Best spots at mid tide](https://www.swell-intel.com/tides/mid)")


# --- 1. clean prose ---------------------------------------------------------------------------

def test_the_two_junk_shapes_would_map_but_are_not_prose():
    # map_sentence alone takes the bait: these are the failures the filter exists for.
    assert tfp.map_sentence(WIDGET)[0] == "low"
    assert tfp.map_sentence(NAV_LINKS)[0] == "mid"
    assert tfp.is_prose(WIDGET) is False
    assert tfp.is_prose(NAV_LINKS) is False


def test_a_page_of_junk_and_prose_gives_only_the_prose():
    page = "\n".join([
        "# Carpinteria Reef surf guide",
        NAV_LINKS.replace(" * ", "\n* "),
        WIDGET,
        "| Tide | Best |",
        "|---|---|",
        "| Mid | Yes |",
        "Carpinteria Reef is a right point over a rock reef.",
        "It works best at mid tide on a west swell.",
    ])
    assert tfp.prose_sentences(page) == [
        "Carpinteria Reef is a right point over a rock reef.",
        "It works best at mid tide on a west swell."]


@pytest.mark.parametrize("why, text", [
    ("a heading", "## Works best at mid tide"),
    ("a table row", "| Best tide | Low to mid |"),
    ("a bare URL", "See https://example.org/tides for when it works best at low tide."),
    ("two links", "[Rincon](https://a.example/r) works best at [low tide](https://a.example/l)."),
    ("a link that is most of it", "[Works best at low tide on a big swell](https://a.example)."),
    ("a clock time", "The tide is lowest at 5:42 and it works best at low tide."),
    ("widget words", "Forecast updated hourly, and the spot works best at low tide."),
    ("no full stop", "The spot works best at low tide on a west swell"),
    ("too short", "Best at low tide."),
    ("lower-case start, a fragment", "in LA County is an exposed beach break that works at low tide."),
    ("Title Case, a menu", "Surf Spots Tide Charts Best Low Tide Breaks Near You."),
    ("no function word, Title Case", "Hollow Fast Left Peaks Low Tide Only Please."),
    ("no function word", "Hollow fast left peaks, low tide only, offshore wind please."),
    ("too many numbers", "It works best at 1 to 2 ft on a 3 ft low tide, 4 or 5 days a year."),
])
def test_what_is_not_prose(why, text):
    assert tfp.is_prose(text) is False, why


@pytest.mark.parametrize("text", [
    "There are left and right breaking reefs Best around low tide.",
    "Sunset Cliffs in San Diego County is an exposed reef break that has quite consistent surf.",
    "It closes out above eight feet and works best at mid tide.",
    "Wide sandy bottom, consistent 1–3 ft waves, and lifeguard towers every 200 yards make it ultra-safe.",
])
def test_what_is_prose(text):
    assert tfp.is_prose(text) is True


def test_a_heading_is_dropped_even_when_it_reads_as_a_sentence():
    page = ("## The reef works best at mid tide on a west swell.\n"
            "Carpinteria Reef is a right point over a rock reef.")
    assert tfp.is_prose("The reef works best at mid tide on a west swell.") is True
    assert tfp.prose_sentences(page) == ["Carpinteria Reef is a right point over a rock reef."]


def test_one_inline_link_is_read_as_its_text():
    page = ("[Hermosa Beach](https://www.surf-forecast.com/breaks/Hermosa-Beach) in LA County is "
            "an exposed beach break that works best at mid tide.")
    assert tfp.prose_sentences(page) == [
        "Hermosa Beach in LA County is an exposed beach break that works best at mid tide."]


def test_a_list_item_is_read_on_its_own_and_emphasis_is_dropped():
    page = "* Works **best** at low tide on a west swell.\n* Parking is on the street."
    assert tfp.prose_sentences(page) == ["Works best at low tide on a west swell."]


def _summary_tide_quotes():
    with open(os.path.join(ROOT, "pipeline", "data", "spot_break_research_summary.json"),
              encoding="utf-8") as fh:
        spots = json.load(fh)["spots"]
    out = {}
    for spot in spots:
        for field in ("break_type", "bottom"):
            quote = ((spot.get("research") or {}).get(field) or {}).get("quote")
            if quote and re.search(r"\btides?\b", quote, re.I) and spot["name"] not in out:
                out[spot["name"]] = quote
    return out


# A hand reading of the 50 real research quotes that mention the tide (committed summary).
# None: not a preference. Anahola Bay ("…at low to medium tide…", beside "shallow") the mapper
# leaves unknown on purpose, so it is None here too.
HAND = {"Bird Rock": "low", "Brooks Street": None, "Imperial Beach Pier": "mid",
        "Jalama Beach": "all", "Lower Trestles": "mid", "Pleasure Point": "all",
        "Redondo Beach, California": "all", "Rockpile": "all", "San Onofre": "low",
        "The Wedge": "mid", "Toes Over": "low", "Tourmaline": "all", "Upper Trestles": "low_mid",
        "Ventura Point": "all", "Deerfield Beach Pier": "all", "Juno Beach Pier": "mid_high",
        "Mayport Poles": "low_mid", "Patrick Air Force Base Beach": "mid_high",
        "Anahola Bay": None, "Hanalei Bay": "low_mid", "Pakala": None, "Queens Waikiki": "all",
        "Richardson Beach": "mid_high", "Thousand Peaks Maui": None, "Tunnels Beach": "all",
        "Horseneck Beach": "all", "Marconi Beach": "high", "Stone Harbor": "low_mid",
        "Smith Point Beach": "all", "Agate Beach Newport": "low", "Florence South Jetty": None,
        "Nelscott Reef": "low_mid", "Seaside Cove": "all", "Short Sands Beach Oswald West": "mid_high",
        "Crash Boat Beach": None, "Point Judith": "all", "Isla Blanca Park": "all",
        "Avalanches": "low_mid", "Trails": None, "Haggarty's": "all", "Pitas Point": None,
        "Pedro Point": "high", "Vero Pier": "high", "The Cove": "all", "West End Long Beach": "all",
        "Hither Hills": "all", "Deep Hole": "low", "First Beach": "all", "Pirates Cove": None,
        "Crescent Beach": None}


def test_the_real_tide_sentences_are_prose_and_map_as_read_by_hand():
    quotes = _summary_tide_quotes()
    assert set(quotes) == set(HAND)
    for name, quote in quotes.items():
        assert tfp.is_prose(quote), name
        value, _ = tfp.map_sentence(quote)
        assert (None if value == "unknown" else value) == HAND[name], name


# --- 2. the mapping ---------------------------------------------------------------------------

@pytest.mark.parametrize("sentence, value", [
    ("Best on a pushing tide.", "unknown"),
    ("Very tide dependent.", "unknown"),
    ("Works best on an incoming tide from low to mid.", "low_mid"),
    ("Avoid high tide, when it swamps out.", "unknown"),
    ("Needs a minus low tide to break.", "low"),
    ("Works at low and high tide but not mid.", "unknown"),
    ("Works from low to high tide.", "all"),
    ("Good surf at all stages of the tide.", "all"),
])
def test_mapping_rules(sentence, value):
    assert tfp.map_sentence(sentence)[0] == value


# --- 3. domains, pages and spots ---------------------------------------------------------------

@pytest.mark.parametrize("url, out", [
    ("https://stormypetrel.surf/spot/rincon", True),
    ("https://www.stormypetrel.surf/spot/rincon", True),
    ("https://beta.stormypetrel.surf/spot/rincon", True),
    ("https://notstormypetrel.surf/x", False),
    ("https://www.surf-forecast.com/breaks/Rincon", False),
])
def test_our_own_site_is_excluded(url, out):
    assert tfp.excluded(url) is out


@pytest.mark.parametrize("url, out", [
    ("https://www.mondo.surf/surf-spot/x", True),
    ("https://swell-intel.com/x", True),
    ("https://www.swell-intel.com/x", True),
    ("https://www.surfline.com/x", False),
])
def test_never_alone_domains(url, out):
    assert tfp.never_alone(url) is out


@pytest.mark.parametrize("values, out", [
    (["low"], "low"),
    (["low", "low"], "low"),
    (["low", "mid"], "low_mid"),
    (["low", "low_mid"], "low_mid"),
    (["mid", "mid_high", "high"], "mid_high"),
    (["low", "high"], None),
    (["low", "mid_high"], None),
    (["low_mid", "mid_high"], None),
    (["all", "low"], None),
    (["all", "all"], "all"),
])
def test_combine(values, out):
    assert tfp.combine(values) == out


@pytest.mark.parametrize("values, out", [
    (["low"], ("low", False)),
    (["all", "low"], ("all", False)),
    (["low", "high"], (None, True)),
    ([], (None, False)),
])
def test_page_value(values, out):
    assert tfp.page_value(values) == out


def _page(url, value, conflict=False, sentence="Quote."):
    return {"url": url, "domain": tfp.domain(url), "value": value, "conflict": conflict,
            "statements": [(value, sentence)] if value else [("low", "a"), ("high", "b")]}


SF = "https://www.surf-forecast.com/breaks/X"
MONDO = "https://www.mondo.surf/surf-spot/x"
SWELL = "https://www.swell-intel.com/x"
DEEP = "https://deepswell.com/x"


@pytest.mark.parametrize("pages, status, value", [
    ([_page(SF, "low")], "proposed", "low"),
    ([_page(SF, "low"), _page(DEEP, "mid")], "proposed", "low_mid"),
    ([_page(SF, "low"), _page(DEEP, "high")], "conflict", None),
    ([_page(MONDO, "all")], "uncorroborated", "all"),
    ([_page(SWELL, "mid")], "uncorroborated", "mid"),
    ([_page(SWELL, "mid"), _page(MONDO, "low")], "uncorroborated", "low_mid"),
    ([_page(SWELL, "mid"), _page(MONDO, "high")], "uncorroborated", "mid_high"),
    ([_page(SWELL, "low"), _page(MONDO, "high")], "conflict", None),
    ([_page(SF, "low"), _page(SWELL, "mid")], "proposed", "low_mid"),
    ([_page(SF, None, conflict=True)], "conflict", None),
    ([], "none", None),
])
def test_decide(pages, status, value):
    out = tfp.decide(pages)
    assert (out["status"], out["value"]) == (status, value)


def test_a_never_alone_page_that_disagrees_is_noted_not_counted():
    out = tfp.decide([_page(SF, "low"), _page(SWELL, "high")])
    assert (out["status"], out["value"]) == ("proposed", "low")
    assert [p["domain"] for p in out["pages"]] == ["surf-forecast.com"]
    assert [p["domain"] for p in out["ignored"]] == ["swell-intel.com"]


@pytest.mark.parametrize("url, name, out", [
    ("https://www.surf-forecast.com/breaks/San-Onofre", "San Onofre", True),
    ("https://www.mondo.surf/surf-spot/birdrock/guide/3438", "Bird Rock", True),
    ("https://deepswell.com/surf-guide/US/x/Imperial-Beach-Pier/1025", "Imperial Beach Pier", True),
    ("https://www.santacruz.com/guides/santa-cruz-surf-spots", "Toes Over", False),
    ("https://www.mondo.surf/surf-spot/redondo-beach-2/guide/2629", "Redondo Beach, California",
     True),
    ("https://www.mondo.surf/surf-spot/jalama/guide/10918", "Jalama Beach", True),
    ("https://www.surfline.com/surf-report/pacific/1", "Pacific Beach", True),
    ("https://www.surfline.com/surf-report/the/1", "The Beach", False),      # nothing left
    ("https://www.mondo.surf/surf-spot/pedro/guide/1", "Pedro Point", False),  # "point" kept
])
def test_page_is_about(url, name, out):
    assert tfp.page_is_about(url, name) is out


def test_dominant_domains_are_flagged_and_never_alone_ones_are_not():
    def outcome(url, value):
        return {"all_pages": [_page(url, value)]}
    outcomes = ([outcome("https://a.example/x", "mid")] * 8 + [outcome("https://a.example/x", "low")] * 2
                + [outcome("https://b.example/x", "mid")] * 7 + [outcome("https://b.example/x", "low")] * 3
                + [outcome("https://c.example/x", "mid")] * 9
                + [outcome(SWELL, "mid")] * 22 + [outcome(SWELL, "low")] * 2)
    # a: 8 of 10 = 0.8 -> flagged. b: 7 of 10 = 0.7 < 0.75. c: 9 spots < 10. swell-intel is
    # never-alone already.
    assert tfp.dominant_domains(outcomes) == {"a.example": "mostly mid (8 of 10)"}


# --- 4. end to end on a results file -----------------------------------------------------------

def _fetch(url, text, call_id):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_fetch", "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": call_id,
             "content": {"type": "web_fetch_result", "url": url,
                         "content": {"type": "document",
                                     "source": {"type": "text", "data": text}}}}]


def _record(name, state, pages, ok_urls):
    blocks = [b for i, (url, text) in enumerate(pages) for b in _fetch(url, text, f"c{i}")]
    cited = list(ok_urls) + [None, None]
    return {"done": True, "lat": 33.0, "lng": -117.0,
            "identity": {"name": name, "region": state, "lat": 33.0, "lng": -117.0},
            "research_responses": {"first": [{"stop_reason": "end_turn",
                                              "content": blocks + [{"type": "text", "text": "{}"}]}],
                                   "follow_up": None},
            "research": {f: {"source_url": u, "location": {"verdict": "ok"} if u else None}
                         for f, u in zip(("break_type", "bottom"), cited)}}


SO = "https://www.surf-forecast.com/breaks/San-Onofre"
SO_TEXT = ("San Onofre is a reef break in North County.\nThere are left and right breaking reefs "
           "Best around low tide.")
JUNK_URL = "https://www.swell-intel.com/carpinteria-reef"
JUNK_TEXT = WIDGET + "\n" + NAV_LINKS.replace(" * ", "\n* ")


def results():
    spots = {
        # proposed, one domain, low
        "San Onofre|California": _record("San Onofre", "California", [(SO, SO_TEXT)], [SO]),
        # junk only: nothing
        "Carpinteria Reef|California": _record("Carpinteria Reef", "California",
                                               [(JUNK_URL, JUNK_TEXT)], [JUNK_URL]),
        # our own site only: excluded
        "Rincon|California": _record("Rincon", "California", [(
            "https://stormypetrel.surf/spot/rincon",
            "Rincon is a long right point that works best at low tide.")],
            ["https://stormypetrel.surf/spot/rincon"]),
        # mondo alone: uncorroborated
        "Jalama Beach|California": _record("Jalama Beach", "California", [(
            "https://www.mondo.surf/surf-spot/jalama/guide/10918",
            "The bottom is sandy, and the spot works on all tides.")],
            ["https://www.mondo.surf/surf-spot/jalama/guide/10918"]),
        # two domains agree: proposed, mid
        "The Wedge|California": _record("The Wedge", "California", [
            ("https://deepswell.com/surf-guide/US/x/The-Wedge/1", "The Wedge works best at mid tide on a south swell."),
            ("https://www.surfline.com/surf-report/the-wedge/1", "The Wedge is a famous shorebreak that is best around mid tide.")],
            ["https://deepswell.com/surf-guide/US/x/The-Wedge/1", "https://www.surfline.com/surf-report/the-wedge/1"]),
        # two domains disagree: conflict
        "Deep Hole|Rhode Island": _record("Deep Hole", "Rhode Island", [
            ("https://thesurfatlas.com/deep-hole", "Deep Hole will only get rolling at low tide, so time your visit."),
            ("https://www.surfline.com/deep-hole", "Deep Hole is a cobble reef that works best at high tide.")],
            ["https://thesurfatlas.com/deep-hole", "https://www.surfline.com/deep-hole"]),
        # a page whose location was not checked: not counted
        "Pedro Point|California": _record("Pedro Point", "California", [(
            "https://www.surfline.com/pedro-point", "Pedro Point is a right point that works best at high tide.")],
            []),
        # a shared page that does not name the spot in the sentence: not counted
        "Toes Over|California": _record("Toes Over", "California", [(
            "https://www.santacruz.com/guides/santa-cruz-surf-spots",
            "Steamer Lane is a right point that works best at mid tide.")],
            ["https://www.santacruz.com/guides/santa-cruz-surf-spots"]),
    }
    spots["Nothing Saved|Texas"] = {"done": True, "lat": 27.0, "lng": -97.0,
                                    "identity": {"name": "Nothing Saved", "region": "Texas"},
                                    "research_responses": None, "research": None}
    return {"schema": 2, "kind": "full", "spots": spots}


def _roster():
    """The fixture's spots as rated roster entries, so decisions can name them."""
    return [{"name": r["identity"]["name"], "region_hint": r["identity"]["region"],
             "lat": 33.0, "lng": -117.0, "is_valid_surf_spot": True}
            for r in results()["spots"].values()]


@pytest.fixture
def files(tmp_path):
    path = tmp_path / "full.json"
    path.write_text(json.dumps(results()), encoding="utf-8")
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps(_roster()), encoding="utf-8")
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"spots": []}), encoding="utf-8")
    return {"results": path, "review": tmp_path / "review.csv",
            "conflicts": tmp_path / "conflicts.csv", "roster": roster, "decisions": decisions}


def _run(files, capsys):
    assert tfp.main(["--results", str(files["results"]), "--review", str(files["review"]),
                     "--conflicts", str(files["conflicts"]), "--roster", str(files["roster"]),
                     "--decisions", str(files["decisions"])]) == 0
    return capsys.readouterr().out.splitlines()


def _csv(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_the_review_file(files, capsys):
    _run(files, capsys)
    rows = _csv(files["review"])
    assert [(r["name"], r["status"], r["value"], r["domains"]) for r in rows] == [
        # proposed: single-source first, then two-source
        ("San Onofre", "proposed", "low", "surf-forecast.com"),
        ("The Wedge", "proposed", "mid", "deepswell.com surfline.com"),
        # then the uncorroborated
        ("Jalama Beach", "uncorroborated", "all", "mondo.surf"),
    ]
    assert rows[0]["quote"] == "There are left and right breaking reefs Best around low tide."
    assert (rows[0]["url"], rows[0]["domain"], rows[0]["decision"]) == (SO, "surf-forecast.com", "")
    # No decisions: the proposed rows may be imported once reviewed, the uncorroborated not.
    assert [r["import"] for r in rows] == ["yes", "yes", "no"]
    assert rows[1]["other_quotes"] == (
        "surfline.com: The Wedge is a famous shorebreak that is best around mid tide.")
    assert rows[2]["flags"] == "mondo.surf: never counts alone"
    assert list(rows[0]) == list(tfp.REVIEW_COLUMNS)


def test_the_conflicts_file(files, capsys):
    _run(files, capsys)
    assert [(r["name"], r["domain"], r["value"], r["quote"]) for r in _csv(files["conflicts"])] == [
        ("Deep Hole", "thesurfatlas.com", "low",
         "Deep Hole will only get rolling at low tide, so time your visit."),
        ("Deep Hole", "surfline.com", "high", "Deep Hole is a cobble reef that works best at high tide."),
    ]


def test_the_report(files, capsys):
    out = _run(files, capsys)
    assert out[:5] == [
        "spots with saved pages: 8",
        "by outcome: proposed 2, uncorroborated 1, conflict 1, none 4",
        "proposed values: low 1, low_mid 0, mid 1, mid_high 0, high 0, all 0",
        "proposed from a single domain: 1",
        "flagged domains (mostly one value): none"]
    assert "pages on an excluded domain: 1" in out
    assert "spots with no saved pages: 1" in out
    assert "statements about another break on a shared page: 1" in out
    assert out[-1] == "Dry run: nothing imported. Review the rows before anything is written."


def test_it_writes_only_the_two_review_files(files, capsys, tmp_path):
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    _run(files, capsys)
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert set(after) - set(before) == {"review.csv", "conflicts.csv"}
    for name in ("full.json", "roster.json", "decisions.json"):
        assert after[name] == before[name]


@pytest.mark.parametrize("where", ["roster", "results", "json"])
def test_it_refuses_to_write_anywhere_but_a_csv(files, where):
    target = {"roster": str(config.DEFAULT_ENRICHED_OUTPUT), "results": str(files["results"]),
              "json": str(files["review"].with_suffix(".json"))}[where]
    with pytest.raises(SystemExit, match="refusing to write"):
        tfp.main(["--results", str(files["results"]), "--review", target,
                  "--conflicts", str(files["conflicts"]), "--roster", str(files["roster"]),
                  "--decisions", str(files["decisions"])])


def test_it_imports_nothing_that_writes_the_roster_or_the_database():
    with open(os.path.join(ROOT, "pipeline", "tide_free_pass.py"), encoding="utf-8") as fh:
        src = fh.read()
    for name in ("db_import", "supabase", "psycopg2", "roster_text", "tide_preference_fields"):
        assert name not in src, name


def test_the_measurement_script_uses_the_same_rules():
    with open(os.path.join(ROOT, "scripts", "tide_freepass_measure.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "from pipeline.tide_free_pass import excluded, map_sentence, prose_sentences" in src
    assert "def map_sentence" not in src and "POSITIVE =" not in src


def test_the_review_order_single_source_and_unusual_first():
    """Each sort key decides something here, against the alphabet:
    flagged-domain rows first among single-source ones, then rarer values, then the rest of the
    single-source rows by name, then multi-source rows, then uncorroborated ones."""
    def outcome(name, status, value, *urls):
        pages = [_page(u, value, sentence=f"{name} works best at {value} tide.") for u in urls]
        return {"name": name, "state": "California", "status": status, "value": value,
                "pages": pages, "ignored": [], "all_pages": pages}
    outcomes = [
        outcome("Alpha", "proposed", "mid", SF, DEEP),            # two domains
        outcome("Bravo", "proposed", "mid", SF),
        outcome("Charlie", "proposed", "mid", DEEP),
        outcome("Delta", "proposed", "high", SF),                 # high: 1 of 5, mid: 4 of 5
        outcome("Echo", "uncorroborated", "all", MONDO),
        outcome("Foxtrot", "proposed", "mid", "https://flagged.example/x"),
    ]
    rows = tfp.review_rows(outcomes, {"flagged.example": "mostly mid (9 of 10)"})
    assert [r["name"] for r in rows] == ["Foxtrot", "Delta", "Bravo", "Charlie", "Alpha", "Echo"]
    assert rows[0]["flags"] == "flagged.example: mostly mid (9 of 10)"


# --- 5. the review's mapper fixes (2026-10-17) -------------------------------------------------
#
# From the first review of the 187 rows. The pages are on the Mac, so each case is a sentence
# built around the words the review quoted, given here in quotation marks.

@pytest.mark.parametrize("spot, sentence, value", [
    # "low, mid, and high tide": all three listed, was 'high' from the last one
    ("Seal Beach Jetty", "Seal Beach Jetty works at low, mid, and high tide on a south swell.", "all"),
    ("listed with or", "It can be surfed at low, medium or high tides.", "all"),
    ("listed with slashes", "It works best at low/mid/high tide.", "all"),
    ("three words, two stages", "It works best at low, mid, or medium tide.", "low_mid"),
    ("three words, low and high", "It works best at low, high, or low tide.", "unknown"),
    # "low-to-mid": a hyphenated range, was 'mid' from "mid tide"
    ("San Clemente Beach", "San Clemente Beach is best on a low-to-mid tide.", "low_mid"),
    ("an en dash", "San Clemente Beach is best on a low–mid tide.", "low_mid"),
    ("a slash", "It works best at low/mid tide.", "low_mid"),
    ("mid-high", "It works best at mid-high tide.", "mid_high"),
    # "medium high to low": two stage words run together, cannot be read
    ("Pismo Beach Pier", "Pismo Beach Pier works best at medium high to low tide.", "unknown"),
    # "a low to rising mid tide": a direction word inside the range
    ("San Elijo State Beach", "San Elijo works best on a low to rising mid tide.", "low_mid"),
    ("an incoming low", "It is best on an incoming low to mid tide.", "low_mid"),
    # unchanged
    ("low and high", "Works at low and high tide but not mid.", "unknown"),
    ("low/high", "Works best at low/high tide.", "unknown"),
    ("low to high", "Works from low to high tide.", "all"),
])
def test_the_review_fixes(spot, sentence, value):
    assert tfp.map_sentence(sentence)[0] == value, spot


def test_a_full_stop_with_no_space_ends_the_sentence():
    """Crash Boat Beach: "…more forgiving at higher tide.Best at 3–8 ft." was one sentence, and
    'Best' made the comparison a preference ('high'). Split, the comparison stays unknown."""
    page = ("The wave is a right peeling off the pier over sand, fast and hollow on a solid swell, "
            "mellower and more forgiving at higher tide.Best at 3–8 ft on a north swell.")
    sentences = tfp.prose_sentences(page)
    assert sentences == [
        "The wave is a right peeling off the pier over sand, fast and hollow on a solid swell, "
        "mellower and more forgiving at higher tide.",
        "Best at 3–8 ft on a north swell."]
    assert [tfp.map_sentence(x)[0] for x in sentences] == ["unknown", None]
    assert tfp.map_sentence(page)[0] == "high"     # what the run-on sentence gave


@pytest.mark.parametrize("text", [
    "The bar is 0.4 ft deep at the lowest tide and works best at mid tide.",
    "It is a reef in the U.S.Virgin Islands that works best at low tide on a north swell.",
])
def test_the_run_on_split_needs_a_lower_case_letter_before_the_stop(text):
    """The new split is only "<lower-case>.<Capital>": a decimal, or an acronym run into the
    next word, stays one sentence."""
    assert tfp.prose_sentences(text) == [text]


# --- 6. a person's decisions -------------------------------------------------------------------

def test_the_committed_decisions():
    with open(config.DEFAULT_ENRICHED_OUTPUT, encoding="utf-8") as fh:
        roster = json.load(fh)
    decisions = tfp.load_decisions(tfp.DECISIONS_FILE, roster)
    assert {k: (e["decision"], e.get("value")) for k, e in decisions.items()} == {
        "Shi Shi Beach|Washington": ("reject", None),
        "Ponce Inlet|Florida": ("accept", "all")}
    assert "rock arches" in decisions["Shi Shi Beach|Washington"]["note"]
    assert tfp.DECISIONS_FILE == config.PIPELINE_DIR / "data" / "tide_review_decisions.json"


def test_a_missing_decisions_file_is_no_decisions(tmp_path):
    assert tfp.load_decisions(tmp_path / "none.json", _roster()) == {}


def test_bad_decisions_stop_the_run_naming_each(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"spots": [
        {"name": "San Onofre", "state": "California", "decision": "reject", "note": "Fine."},
        {"name": "San Onofre", "state": "California", "decision": "reject", "note": "Twice."},
        {"name": "Nowhere", "state": "California", "decision": "reject", "note": "No such spot."},
        {"name": "The Wedge", "state": "California", "decision": "reject", "notes": "x"},
        {"name": "Jalama Beach", "state": "California", "decision": "maybe", "note": "x"},
        {"name": "Deep Hole", "state": "Rhode Island", "decision": "accept", "note": "No value."},
        {"name": "Rincon", "state": "California", "decision": "accept", "value": "sometimes",
         "note": "Bad value."},
        {"name": "Pedro Point", "state": "California", "decision": "reject", "value": "low",
         "note": "A reject with a value."},
        {"name": "Toes Over", "state": "California", "decision": "reject", "note": " "},
    ]}), encoding="utf-8")
    with pytest.raises(SystemExit) as err:
        tfp.load_decisions(path, _roster())
    lines = str(err.value).splitlines()
    assert lines[0].endswith(": 8 of its 9 entries cannot be used:")
    values = "('low', 'low_mid', 'mid', 'mid_high', 'high', 'all')"
    assert lines[1:] == [
        "  San Onofre (California): listed twice",
        "  Nowhere (California): not a rated spot in spots_enriched.json (the name and state "
        "must match exactly)",
        "  The Wedge (California): unknown key 'notes'",
        "  Jalama Beach (California): decision 'maybe' is not one of ('accept', 'reject')",
        f"  Deep Hole (Rhode Island): an accept names the value checked, one of {values}",
        f"  Rincon (California): an accept names the value checked, one of {values}",
        "  Pedro Point (California): a reject takes no value",
        "  Toes Over (California): needs a note of 1-300 characters"]


ACCEPT_ALL = {"decision": "accept", "value": "all", "note": "x"}
REJECT = {"decision": "reject", "note": "x"}


@pytest.mark.parametrize("status, value, entry, out", [
    ("proposed", "low", None, (True, "proposed")),
    ("uncorroborated", "all", None, (False, "uncorroborated: needs an accept")),
    ("conflict", None, None, (False, "conflict")),
    ("none", None, None, (False, "no statement")),
    ("proposed", "all", REJECT, (False, "rejected on review")),
    ("uncorroborated", "all", REJECT, (False, "rejected on review")),
    ("proposed", "all", ACCEPT_ALL, (True, "accepted on review as all")),
    ("uncorroborated", "all", ACCEPT_ALL, (True, "accepted on review as all")),
    ("proposed", "mid", ACCEPT_ALL,
     (False, "accepted on review as all, but the pass now gives mid (proposed)")),
    ("conflict", None, ACCEPT_ALL,
     (False, "accepted on review as all, but the pass now gives no value (conflict)")),
])
def test_import_decision(status, value, entry, out):
    # Each outcome here has a page that states its value, as a single-page outcome does.
    pages = [{"value": value}] if value else []
    assert tfp.import_decision({"status": status, "value": value, "pages": pages}, entry) == out


@pytest.mark.parametrize("entry", [None, {"decision": "accept", "value": "low_mid", "note": "x"}])
def test_a_value_no_one_page_states_is_never_imported(entry):
    """low here, mid there: the pass proposes low_mid, but 'researched' means a cited page and a
    quote that say the value, and no page says low_mid. Held back, even with an accept."""
    outcome = {"status": "proposed", "value": "low_mid",
               "pages": [{"value": "low"}, {"value": "mid"}]}
    assert tfp.import_decision(outcome, entry) == (False, "combined: no one page says low_mid")
    assert tfp.stating_page(outcome) is None
    assert tfp.stating_page(dict(outcome, pages=[{"value": "low"}, {"value": "low_mid"}])) == {
        "value": "low_mid"}


def test_a_combined_row_shows_why_in_the_review_file():
    pages = [_page(SF, "low", sentence="X works best at low tide."),
             _page(DEEP, "mid", sentence="X works best at mid tide.")]
    outcome = {"name": "X", "state": "California", "status": "proposed", "value": "low_mid",
               "pages": pages, "ignored": [], "all_pages": pages}
    row, = tfp.review_rows([outcome], {})
    assert (row["import"], row["flags"]) == ("no", "combined: no one page says low_mid")
    assert row["quote"] == "X works best at low tide."


def test_the_review_file_shows_the_decisions(files, capsys):
    files["decisions"].write_text(json.dumps({"spots": [
        {"name": "San Onofre", "state": "California", "decision": "reject", "note": "Not surf."},
        {"name": "The Wedge", "state": "California", "decision": "accept", "value": "low",
         "note": "Checked low."},
        {"name": "Jalama Beach", "state": "California", "decision": "accept", "value": "all",
         "note": "Checked all."},
        {"name": "Deep Hole", "state": "Rhode Island", "decision": "accept", "value": "low",
         "note": "Low, as the atlas says."},
    ]}), encoding="utf-8")
    out = _run(files, capsys)
    rows = {r["name"]: r for r in _csv(files["review"])}
    assert [(n, rows[n]["import"], rows[n]["decision"], rows[n]["decision_note"])
            for n in ("San Onofre", "The Wedge", "Jalama Beach")] == [
        ("San Onofre", "no", "reject", "Not surf."),
        ("The Wedge", "no", "accept low", "Checked low."),
        ("Jalama Beach", "yes", "accept all", "Checked all.")]
    assert rows["The Wedge"]["flags"] == (
        "accepted on review as low, but the pass now gives mid (proposed)")
    assert "decisions: 4 (accept 3, reject 1); rows the import would write: 1" in out
    # Deep Hole is a conflict, so it has no review row.
    assert "decisions with no row in the review file: Deep Hole|Rhode Island" in out
    assert ("accept no longer matches: The Wedge (California): accepted on review as low, but "
            "the pass now gives mid (proposed)") in out


def test_a_rerun_reports_what_changed_and_keeps_the_old_file(files, capsys):
    old = [
        {"name": "San Onofre", "state": "California", "status": "proposed", "value": "high",
         "quote": "q", "url": SO, "decision": ""},
        {"name": "The Wedge", "state": "California", "status": "proposed", "value": "mid",
         "quote": "an older quote", "url": "https://example.org/old", "decision": ""},
        {"name": "Gone Spot", "state": "Texas", "status": "proposed", "value": "low",
         "quote": "q", "url": "u", "decision": ""},
        {"name": "Typed Spot", "state": "Texas", "status": "proposed", "value": "low",
         "quote": "q", "url": "u", "decision": "reject: about kayaks"},
    ]
    with open(files["review"], "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(old[0]))
        writer.writeheader()
        writer.writerows(old)
    previous = files["review"].read_bytes()
    out = _run(files, capsys)
    assert files["review"].with_name("review.previous.csv").read_bytes() == previous
    start = out.index("rows changed since the last review file: 5")
    assert out[start + 1:start + 6] == [
        "  San Onofre (California): proposed high -> proposed low",
        "  The Wedge (California): proposed mid, same value from a different quote",
        "  Jalama Beach (California): new row, uncorroborated all",
        "  Gone Spot (Texas): row gone (was proposed low)",
        "  Typed Spot (Texas): row gone (was proposed low)"]
    assert ("typed into the old review file's decision column but not in the decisions file "
            "(add them there, or they are lost): Typed Spot (Texas): 'reject: about kayaks'") in out
    assert out[-1] == "Dry run: nothing imported. Review the rows before anything is written."


def test_the_first_run_says_there_is_no_earlier_file(files, capsys):
    out = _run(files, capsys)
    assert "rows changed since the last review file: no earlier review file" in out
    assert not files["review"].with_name("review.previous.csv").exists()
