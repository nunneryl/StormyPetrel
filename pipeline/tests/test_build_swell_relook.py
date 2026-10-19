"""scripts/build_swell_relook.py: the swell-window answer-key page.

The flagged counts come from docs/swell_window_recompute_report.md (68 facing outside every arc,
the 36 Hawaii / Puerto Rico regressions in its table) and the six headlands named for the key;
three spots are in both of the first two lists (dogmans-rincon, honolua-bay and
table-tops-aguadilla) and every headland is already in one of them, so 68 + 36 - 3 = 101.
The bin values for Lovers Point are the hand arithmetic in test_swell_window_soft.py.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("build_swell_relook",
                                               ROOT / "scripts" / "build_swell_relook.py")
B = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(B)

PAGE = ROOT / "scripts" / "swell_relook.html"


def _roster():
    return json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8"))


def test_the_regressions_are_the_reports_table():
    doc = (ROOT / "docs" / "swell_window_recompute_report.md").read_text(encoding="utf-8")
    table = doc.split("| spot | state | degrees lost |", 1)[1].split("\n\n", 1)[0]
    rows = re.findall(r"^\| ([a-z0-9-]+) \| (?:Hawaii|Puerto Rico) \| ([\d.]+) \|$", table, re.M)
    assert [(s, float(d)) for s, d in rows] == [(s, float(d)) for s, d in B.REGRESSIONS]
    assert len(B.REGRESSIONS) == 36


def test_the_flagged_spots():
    flagged = B.flagged(_roster())
    slugs = [B._slugify(s["name"]) for s, _ in flagged]
    assert len(slugs) == len(set(slugs)) == 101
    count = {g: sum(g in groups for _, groups in flagged) for g in B.GROUP_ORDER}
    assert count == {"headland": 6, "hi_pr_regression": 36, "facing_outside": 68}
    assert slugs[:6] == list(B.HEADLANDS)
    both = sorted(B._slugify(s["name"]) for s, g in flagged
                  if "hi_pr_regression" in g and "facing_outside" in g)
    assert both == ["dogmans-rincon", "honolua-bay", "table-tops-aguadilla"]
    groups = dict((B._slugify(s["name"]), g) for s, g in flagged)
    assert groups["lovers-point"] == ["headland", "facing_outside"]
    assert groups["monterey-beach"] == ["facing_outside"]       # no arcs at all


def test_a_flagged_slug_missing_from_the_roster_stops_the_build():
    roster = [s for s in _roster() if s.get("name") != "Lovers Point"]
    with pytest.raises(SystemExit, match="lovers-point"):
        B.flagged(roster)


def test_lovers_points_record():
    s, = [x for x in _roster() if x.get("name") == "Lovers Point"]
    rec = B.spot_record(s, ["headland", "facing_outside"])
    assert (rec["padded"], rec["geometric"]) == ([[315.0, 6.0]], [[310.0, 16.0]])
    assert (rec["padded_edges"], rec["geometric_edges"]) == ([315.0, 321.0], [310.0, 326.0])
    assert len(rec["bins"]) == 36 and [b[0] for b in rec["bins"]] == list(range(0, 360, 10))
    bins = {b[0]: b[1:] for b in rec["bins"]}
    # [today, 8 s, 12 s, 18 s], rounded to 3. At 320 (δ = -6): 8 s v = 1.047597, K = 0.195317,
    # E = 0.804683 x 0.998097 = 0.803151; 18 s v = 0.465598, K = 0.317279, x -> 0.681422.
    assert bins[320] == [0.998, 0.803, 0.742, 0.681]
    assert bins[330] == [0.4, 0.253, 0.312, 0.362]
    assert bins[0] == [0.4, 0.032, 0.165, 0.213]
    assert bins[200] == [0.0, 0.0, 0.0, 0.0]


def test_built_from_follows_the_window_fields_only():
    roster = _roster()
    stamp = B.built_from(roster)
    other = copy.deepcopy(roster)
    for s in other:
        s["tide_preference"] = "all"                 # not drawn: same stamp
    assert B.built_from(other) == stamp
    moved = copy.deepcopy(roster)
    s = next(x for x in moved if x.get("name") == "Lovers Point")
    s["swell_window_arcs"] = [{"min": 317, "max": 323, "span": 10}]
    assert B.built_from(moved) != stamp


def test_the_committed_page_is_current():
    """Rebuild with `python3 scripts/build_swell_relook.py` when this fails: the roster's swell
    windows (or a flagged spot) changed since the page was built."""
    html = PAGE.read_text(encoding="utf-8")
    roster = _roster()
    assert re.search(r'const BUILT_FROM = "([^"]+)";', html).group(1) == B.built_from(roster)
    assert html == B.render(B.build_data(roster), B.built_from(roster))


def test_render_cannot_close_the_script_early():
    html = B.render([{"slug": "x", "name": "</script><b>"}], "stamp")
    assert "</script><b>" not in html and "<\\/script><b>" in html


def test_the_page_reaches_only_leaflet_and_esri():
    html = PAGE.read_text(encoding="utf-8")
    hosts = set(re.findall(r"https://([a-z0-9.-]+)/", html))
    assert hosts == {"unpkg.com", "server.arcgisonline.com"}
    assert 'a.download = "swell_direction_labels.json"' in html
    assert "swell_relook_labels_v1" in html


def _pure_js():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("// PURE-BEGIN", 1)[1].split("// PURE-END", 1)[0]
    return block.split("\n", 1)[1]          # past the rest of the marker's comment line


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_pages_label_logic_in_node():
    script = _pure_js() + r"""
const assert = require("assert");
assert.deepStrictEqual([null, "works", "blocked", "unsure"].map(nextLabel), ["works", "blocked", "unsure", null]);
let l = setLabel({}, "a", 330, "works");
l = setLabel(l, "a", 0, "blocked");
assert.deepStrictEqual(l, {a: {"330": "works", "0": "blocked"}});
assert.deepStrictEqual(setLabel(setLabel(l, "a", 330, null), "a", 0, null), {});
const spots = [{slug: "a", name: "A", state: "California"}, {slug: "b", name: "B", state: "Hawaii"}];
const p = exportPayload(Object.assign({}, l, {gone: {"10": "works"}, b: {}}), spots, "stamp", "T");
assert.deepStrictEqual(p, {schema: "swell_direction_labels/1", built_from: "stamp", exported_at: "T",
  bin_deg: 10, spots: {a: {name: "A", state: "California", labels: {"0": "blocked", "330": "works"}}}});
const back = mergeImport({b: {"90": "unsure"}}, p);
assert.deepStrictEqual(back, {b: {"90": "unsure"}, a: {"0": "blocked", "330": "works"}});
assert.deepStrictEqual(mergeImport({}, {schema: "swell_direction_labels/1", spots: {a: {labels: {"10": "maybe"}}}}), {});
assert.throws(() => mergeImport({}, {schema: "other"}));
console.log("ok");
"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_pages_script_parses(tmp_path):
    html = PAGE.read_text(encoding="utf-8")
    js = html.split("<script>\n", 1)[1].rsplit("</script>", 1)[0]
    path = tmp_path / "page.js"
    path.write_text(js, encoding="utf-8")
    out = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
