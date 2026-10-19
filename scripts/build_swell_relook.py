#!/usr/bin/env python3
"""Build the swell-window ANSWER KEY page: scripts/swell_relook.html, a self-contained page for
marking, spot by spot, which swell directions should work and which are blocked.

    python3 scripts/build_swell_relook.py        ->  scripts/swell_relook.html

Open the page in a browser (double-click it, or `open scripts/swell_relook.html` on a Mac). The
satellite map needs the internet (Leaflet from unpkg, Esri imagery); without it the page still
works from the compass rose and the table.

WHAT IT SHOWS, for each flagged spot (the five RUN_19 spots first, then HEADLANDS, REGRESSIONS
and every spot facing outside every arc):
  - the window as the rater reads it today (each arc's padded sector, cyan) and the geometric
    window the candidate uses (padded + the ray-cast's 5° shrink put back, orange);
  - the facing (blue) and the optimal swell direction (green);
  - 36 wedges, one per 10° bin centred on 0, 10, ... 350, whose length is the HEIGHT factor at
    the bin's centre for the function picked: the window's directional_gain as it stands (the
    gain WW3 and MOP heights take, and the weight that chooses an NWPS hour's swell); the
    alignment alone, max(0.25, cos²(Δ/2)), which is all an NWPS height takes since run 19; or
    the candidate soft window (pipeline/swell_window_soft.py) at 8, 12 or 18 s.

WHAT YOU MARK: click a wedge (on the map, the rose or the table) to cycle it
works -> blocked -> unsure -> unmarked. Marks are kept in the browser between visits.

THE EXPORT: "export labels" downloads swell_direction_labels.json to the browser's download
folder: {"schema", "built_from", "exported_at", "bin_deg", "spots": {slug: {"name", "state",
"labels": {"<bin centre>": "works" | "blocked" | "unsure"}}}}, marked bins only. Move it to
pipeline/data/swell_direction_labels.json and commit it; "import" loads one back to carry on.

The page is built from the committed roster; built_from fingerprints the fields it draws, and a
test fails when the committed page no longer matches the roster, so rebuild after any change to
the swell windows. Labels key on the compass bin, not the window, so they survive a rebuild.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.db_import import _slugify                                    # noqa: E402
from pipeline.interpret import directional_gain, in_any_arc                # noqa: E402
from pipeline.swell_window_soft import (                                   # noqa: E402
    PERIODS_S, candidate_height_factor, geometric_sectors, padded_sectors,
    today_height_factor, union_edges,
)

ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
OUT_HTML = os.path.join(HERE, "swell_relook.html")
BIN_DEG = 10

# The spots run 19 moved most when the NWPS height stopped taking the window's rung: Doran,
# Carmel and Dillon by more than 1.5 stars (variant B), Honolua and Flemings down to the 0.25
# floor on swells far from their best direction. First on the page, to check by eye.
RUN_19 = ("doran-beach", "carmel-beach", "dillon-beach", "honolua-bay", "flemings-beach")
# The headlands named for the answer key.
HEADLANDS = ("lovers-point", "table-tops-aguadilla", "dogmans-rincon", "hanalei-bay",
             "waimea-bay", "tunnels-beach")
# The Hawaii and Puerto Rico spots whose window within 90° of the facing narrowed in the
# own-land recompute, with the degrees lost: docs/swell_window_recompute_report.md, in its order.
REGRESSIONS = (
    ("hanalei-bay", 78.5), ("canoes-waikiki", 44), ("table-tops-aguadilla", 40),
    ("tunnels-beach", 28.5), ("waimea-bay", 28.5), ("dogmans-rincon", 28), ("honolua-bay", 25.5),
    ("hapuna-beach", 19), ("kanaha-beach", 18.5), ("queens-waikiki", 16.5),
    ("yokohama-bay", 16.5), ("makapuu-beach-park", 16), ("puaena-point", 14.5),
    ("haleiwa-ali-i-beach", 13.5), ("kiahuna-beach", 12.5), ("polihale-beach", 10.5),
    ("laniakea", 8.5), ("pakala", 8.5), ("makaha", 8), ("suicide-s", 8),
    ("flemings-beach", 7.5), ("lymans", 5.5), ("turtle-bay", 4.5), ("backdoor-pipeline", 4),
    ("banzai-pipeline", 4), ("ho-okipa", 4), ("kolekole-beach-park", 4), ("log-cabins", 4),
    ("off-the-wall", 4), ("p-p-kea-hawaii", 4), ("rocky-point", 4),
    ("sunset-beach-hawaii", 4), ("banyans", 3.5), ("chuns-reef", 3.5),
    ("electric-beach", 2.5), ("kaisers", 1.5),
)
GROUP_ORDER = ("run_19", "headland", "hi_pr_regression", "facing_outside")
# The roster fields the page draws: the page is stale when any of them changes.
DRAWN_FIELDS = ("name", "lat", "lng", "region_hint", "is_valid_surf_spot", "orientation_deg",
                "optimal_swell_dir", "swell_window_arcs")


def rated(roster):
    return [s for s in roster if s.get("is_valid_surf_spot") is not False and s.get("name")]


def facing_outside(spot):
    """The swell_window_report rule: no facing, or a facing outside every arc (no arcs counts)."""
    o = spot.get("orientation_deg")
    return o is None or not in_any_arc(o, spot.get("swell_window_arcs") or [])


def flagged(roster):
    """[(spot, [groups])] in GROUP_ORDER, each spot once: run 19's spots in RUN_19's order, the
    headlands in HEADLANDS' order, the regressions in the report's order, then the rest facing
    outside by state and name."""
    by_slug = {_slugify(s["name"]): s for s in rated(roster)}
    groups = {}
    for slug in RUN_19:
        groups.setdefault(slug, []).append("run_19")
    for slug in HEADLANDS:
        groups.setdefault(slug, []).append("headland")
    for slug, _ in REGRESSIONS:
        groups.setdefault(slug, []).append("hi_pr_regression")
    outside = sorted((s for s in rated(roster) if facing_outside(s)),
                     key=lambda s: (s.get("region_hint") or "", s["name"]))
    for s in outside:
        groups.setdefault(_slugify(s["name"]), []).append("facing_outside")
    missing = [slug for slug in groups if slug not in by_slug]
    if missing:
        raise SystemExit(f"flagged spots not in the roster: {missing}")
    return [(by_slug[slug], g) for slug, g in groups.items()]


def built_from(roster):
    """sha1 of the drawn fields of every rated spot, so a stale page can be told apart."""
    rows = [{k: s.get(k) for k in DRAWN_FIELDS} for s in rated(roster)]
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return "roster-windows-" + hashlib.sha1(blob).hexdigest()[:16]


def spot_record(spot, groups, lost=None):
    arcs = spot.get("swell_window_arcs") or []
    opt, ori = spot.get("optimal_swell_dir"), spot.get("orientation_deg")
    bins = []
    for c in range(0, 360, BIN_DEG):
        d = float(c)
        bins.append([c, round(today_height_factor(d, arcs, opt, ori), 3),
                     round(directional_gain(d, [], opt if opt is not None else ori, ori), 3)]
                    + [round(candidate_height_factor(d, t, arcs, opt, ori), 3) for t in PERIODS_S])
    padded, geometric = padded_sectors(arcs), geometric_sectors(arcs)
    return {
        "slug": _slugify(spot["name"]), "name": spot["name"], "state": spot.get("region_hint"),
        "lat": spot["lat"], "lng": spot["lng"], "groups": groups, "lost_deg": lost,
        "orientation": ori, "optimal": opt, "arcs": arcs,
        "padded": [[round(a, 3), round(b, 3)] for a, b in padded],
        "geometric": [[round(a, 3), round(b, 3)] for a, b in geometric],
        "padded_edges": [round(e, 3) for e in union_edges(padded)],
        "geometric_edges": [round(e, 3) for e in union_edges(geometric)],
        "bins": bins,
    }


def build_data(roster):
    lost = dict(REGRESSIONS)
    return [spot_record(s, g, lost.get(_slugify(s["name"]))) for s, g in flagged(roster)]


def render(data, stamp):
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")
    return (TEMPLATE.replace("__RELOOK_DATA__", payload)
            .replace("__BUILT_FROM__", stamp)
            .replace("__PERIODS__", json.dumps([int(t) for t in PERIODS_S])))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default=ROSTER)
    p.add_argument("--out", default=OUT_HTML)
    args = p.parse_args(argv)
    with open(args.roster, encoding="utf-8") as f:
        roster = json.load(f)
    data = build_data(roster)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(data, built_from(roster)))
    counts = {g: sum(g in r["groups"] for r in data) for g in GROUP_ORDER}
    print(f"wrote {args.out}: {len(data)} spots {counts}")
    return 0


TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Swell window answer key</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%;font:14px/1.4 system-ui,sans-serif;background:#111;color:#eee}
  #wrap{display:flex;height:100%}
  #map{flex:1;height:100%;background:#0d0f13}
  #side{width:400px;padding:12px;box-sizing:border-box;background:#181a1f;overflow:auto}
  h2{margin:.2em 0;font-size:18px} .sub{color:#9aa;font-size:12px}
  .pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;margin:0 4px 0 0;background:#2a2f3a}
  .btn{background:#2a2f3a;border:1px solid #3a4150;color:#eee;border-radius:6px;padding:5px 9px;cursor:pointer}
  .btn:hover{background:#333a47} .btn.on{background:#3b5b8a;border-color:#6d95d0}
  #rose{display:block;margin:6px auto;cursor:pointer}
  table{width:100%;border-collapse:collapse;margin:6px 0;font-size:12px}
  td,th{padding:1px 4px;text-align:right;font-variant-numeric:tabular-nums}
  th{color:#9aa;font-weight:500} td.lab{text-align:left;cursor:pointer;width:64px}
  tr.sel td{background:#26303d}
  .works{color:#5fd38a}.blocked{color:#f06c6c}.unsure{color:#d9c35a}
  kbd{background:#2a2f3a;border:1px solid #3a4150;border-radius:4px;padding:0 5px}
  .legend span{display:inline-block;margin-right:10px}
  textarea{width:100%;height:80px;background:#0d0f13;color:#ccc;border:1px solid #333}
</style></head>
<body><div id="wrap">
  <div id="map"></div>
  <div id="side">
    <div id="prog" class="sub"></div>
    <h2 id="name"></h2>
    <div class="sub" id="meta"></div>
    <div id="groups"></div>
    <div style="margin:6px 0" id="fns"></div>
    <svg id="rose" width="300" height="300" viewBox="-172 -172 344 344"></svg>
    <div class="legend sub">
      <span style="color:#58c7e8">&#9644; today's window (padded)</span>
      <span style="color:#f0a040">&#9644; geometric window</span>
      <span style="color:#7fb2ff">&#8594; facing</span><span style="color:#8fdc8f">&#8594; optimal</span>
    </div>
    <div class="legend sub">click a wedge: <span class="works">works</span> &#8594;
      <span class="blocked">blocked</span> &#8594; <span class="unsure">unsure</span> &#8594; unmarked</div>
    <div style="margin:6px 0">
      <button class="btn" onclick="go(-1)">&larr; prev</button>
      <button class="btn" onclick="go(1)">next &rarr;</button>
      <select id="filter" class="btn" onchange="setFilter(this.value)">
        <option value="all">all flagged</option>
        <option value="run_19">run 19: Doran, Carmel, Dillon, Honolua, Flemings</option>
        <option value="headland">headlands</option>
        <option value="hi_pr_regression">Hawaii / Puerto Rico regressions</option>
        <option value="facing_outside">facing outside every arc</option>
        <option value="unmarked">not marked yet</option>
      </select>
    </div>
    <div class="sub"><kbd>&larr;</kbd>/<kbd>&rarr;</kbd> spot, <kbd>1</kbd>-<kbd>5</kbd> function</div>
    <table id="tbl"></table>
    <button class="btn" style="width:100%;margin-top:6px" onclick="doExport()">export labels &darr;</button>
    <label class="btn" style="display:block;text-align:center;margin-top:6px">import labels
      <input type="file" accept=".json,application/json" style="display:none" onchange="doImport(this)"></label>
    <div class="sub" id="status" style="margin-top:6px"></div>
    <textarea id="fallback" style="display:none" readonly></textarea>
  </div>
</div>
<script>
const SPOTS = __RELOOK_DATA__;
const BUILT_FROM = "__BUILT_FROM__";
const PERIODS = __PERIODS__;
const STORE_KEY = "swell_relook_labels_v1";

// PURE-BEGIN — no DOM; tests run this block in node.
const LABELS = ["works", "blocked", "unsure"];
function nextLabel(cur) {
  const i = LABELS.indexOf(cur);
  return i === LABELS.length - 1 ? null : LABELS[i + 1];
}
function setLabel(labels, slug, center, value) {
  const out = Object.assign({}, labels);
  const spot = Object.assign({}, out[slug] || {});
  if (value) spot[String(center)] = value; else delete spot[String(center)];
  if (Object.keys(spot).length) out[slug] = spot; else delete out[slug];
  return out;
}
function exportPayload(labels, spots, builtFrom, exportedAt) {
  const bySlug = {};
  for (const s of spots) bySlug[s.slug] = s;
  const out = {};
  for (const slug of Object.keys(labels).sort()) {
    const marks = labels[slug];
    if (!bySlug[slug] || !Object.keys(marks).length) continue;
    const sorted = {};
    for (const k of Object.keys(marks).sort((a, b) => Number(a) - Number(b))) sorted[k] = marks[k];
    out[slug] = {name: bySlug[slug].name, state: bySlug[slug].state, labels: sorted};
  }
  return {schema: "swell_direction_labels/1", built_from: builtFrom, exported_at: exportedAt,
          bin_deg: 10, spots: out};
}
function mergeImport(labels, payload) {
  if (!payload || payload.schema !== "swell_direction_labels/1") throw new Error("not a labels export");
  let out = Object.assign({}, labels);
  for (const [slug, rec] of Object.entries(payload.spots || {}))
    for (const [center, value] of Object.entries(rec.labels || {}))
      if (LABELS.includes(value)) out = setLabel(out, slug, center, value);
  return out;
}
// PURE-END

let labels = {};
try { labels = JSON.parse(localStorage.getItem(STORE_KEY) || "{}") || {}; } catch (e) { labels = {}; }
function save() { try { localStorage.setItem(STORE_KEY, JSON.stringify(labels)); } catch (e) {} }

let fn = 0;                        // 0 the window today, 1 NWPS alignment, 2..4 the candidate
let filter = "all", order = SPOTS.map((_, j) => j), pos = 0;
const FN_NAMES = ["window today", "NWPS alignment"].concat(PERIODS.map(t => "candidate " + t + " s"));
const COLOR = {works: "#5fd38a", blocked: "#f06c6c", unsure: "#d9c35a"};
const SVGNS = "http://www.w3.org/2000/svg";
const R0 = 22, R1 = 128;           // rose: gain 0 at R0, gain 1 at R1

function spot() { return SPOTS[order[pos]]; }
function mark(s, c) { return (labels[s.slug] || {})[String(c)] || null; }
function cycle(c) { const s = spot(); labels = setLabel(labels, s.slug, c, nextLabel(mark(s, c))); save(); render(); }
function setFilter(v) {
  filter = v;
  order = SPOTS.map((_, j) => j).filter(j => v === "all" ? true
    : v === "unmarked" ? !labels[SPOTS[j].slug] : SPOTS[j].groups.includes(v));
  if (!order.length) order = SPOTS.map((_, j) => j);
  pos = 0; render();
}
function go(d) { pos = (pos + d + order.length) % order.length; render(); }

function polar(b, r) { const a = (b - 90) * Math.PI / 180; return [r * Math.cos(a), r * Math.sin(a)]; }
function wedgePath(c, r0, r1) {
  const [a, b] = [c - 5, c + 5];
  const p = [polar(a, r0), polar(a, r1), polar(b, r1), polar(b, r0)];
  return `M${p[0]}L${p[1]}A${r1},${r1} 0 0 1 ${p[2]}L${p[3]}A${r0},${r0} 0 0 0 ${p[0]}Z`;
}
function bandPath(start, len, r0, r1) {
  if (len >= 359.999) len = 359.999;
  const e = start + len, big = len > 180 ? 1 : 0;
  const p = [polar(start, r0), polar(start, r1), polar(e, r1), polar(e, r0)];
  return `M${p[0]}L${p[1]}A${r1},${r1} 0 ${big} 1 ${p[2]}L${p[3]}A${r0},${r0} 0 ${big} 0 ${p[0]}Z`;
}
function el(tag, attrs) { const n = document.createElementNS(SVGNS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]); return n; }
function ray(rose, b, r, color, w, dash) { const [x, y] = polar(b, r);
  rose.appendChild(el("line", {x1: 0, y1: 0, x2: x, y2: y, stroke: color, "stroke-width": w,
    "stroke-dasharray": dash || "", "stroke-linecap": "round"})); }

function drawRose() {
  const s = spot(), rose = document.getElementById("rose"); rose.innerHTML = "";
  rose.appendChild(el("circle", {r: R1, fill: "#0d0f13", stroke: "#333"}));
  for (const [st, len] of s.geometric) rose.appendChild(el("path", {d: bandPath(st, len, R1 + 12, R1 + 18), fill: "#f0a040"}));
  for (const [st, len] of s.padded) rose.appendChild(el("path", {d: bandPath(st, len, R1 + 4, R1 + 10), fill: "#58c7e8"}));
  for (const b of s.bins) {
    const c = b[0], m = mark(s, c), g = b[1 + fn];
    const bg = el("path", {d: wedgePath(c, 0, R1), fill: m ? COLOR[m] : "#000", "fill-opacity": m ? 0.28 : 0.01,
      stroke: "#2a2f3a"});
    bg.addEventListener("click", () => cycle(c)); rose.appendChild(bg);
    rose.appendChild(el("path", {d: wedgePath(c, 0, R0 + (R1 - R0) * g), fill: m ? COLOR[m] : "#cfd6e4",
      "fill-opacity": 0.85, "pointer-events": "none"}));
  }
  for (const e of s.geometric_edges) ray(rose, e, R1 + 18, "#f0a040", 1.5, "4 3");
  for (const e of s.padded_edges) ray(rose, e, R1 + 10, "#58c7e8", 1.5, "2 3");
  if (s.orientation != null) ray(rose, s.orientation, R1 - 10, "#7fb2ff", 4);
  if (s.optimal != null) ray(rose, s.optimal, R1 - 25, "#8fdc8f", 3, "6 4");
  for (const [b, t] of [[0, "N"], [90, "E"], [180, "S"], [270, "W"]]) {
    const [x, y] = polar(b, R1 + 30);
    const n = el("text", {x: x, y: y + 4, fill: "#778", "font-size": 12, "text-anchor": "middle"});
    n.textContent = t; rose.appendChild(n);
  }
}

function drawTable() {
  const s = spot();
  let h = "<tr><th>bin</th><th>mark</th><th>window</th><th>NWPS</th>" + PERIODS.map(t => `<th>${t} s</th>`).join("") + "</tr>";
  for (const b of s.bins) {
    const m = mark(s, b[0]);
    h += `<tr><td>${b[0]}&deg;</td><td class="lab ${m || ""}" onclick="cycle(${b[0]})">${m || "&middot;"}</td>`
      + b.slice(1).map((g, k) => `<td style="${k === fn ? "color:#fff;font-weight:600" : "color:#9aa"}">${g.toFixed(2)}</td>`).join("")
      + "</tr>";
  }
  document.getElementById("tbl").innerHTML = h;
}

let map = null, layer = null;
function drawMap() {
  if (typeof L === "undefined") return;
  const s = spot();
  if (!map) {
    map = L.map("map", {zoomControl: true});
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {maxZoom: 19, attribution: "Esri"}).addTo(map);
    L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      {maxZoom: 19, opacity: 0.8}).addTo(map);
  }
  if (layer) layer.remove();
  layer = L.layerGroup().addTo(map);
  const R = 1500, k = Math.cos(s.lat * Math.PI / 180);
  const at = (b, r) => [s.lat + r * Math.cos(b * Math.PI / 180) / 111320,
                        s.lng + r * Math.sin(b * Math.PI / 180) / (111320 * k)];
  const fan = (c, r) => { const pts = [[s.lat, s.lng]]; for (let b = c - 5; b <= c + 5; b += 2.5) pts.push(at(b, r)); return pts; };
  for (const b of s.bins) {
    const c = b[0], m = mark(s, c), g = b[1 + fn];
    L.polygon(fan(c, R), {color: "#2a2f3a", weight: 1, fillColor: m ? COLOR[m] : "#000", fillOpacity: m ? 0.3 : 0.05})
      .on("click", () => cycle(c)).addTo(layer);
    if (g > 0) L.polygon(fan(c, R * (0.15 + 0.85 * g)), {stroke: false, fillColor: m ? COLOR[m] : "#ffffff",
      fillOpacity: 0.55, interactive: false}).addTo(layer);
  }
  for (const e of s.geometric_edges) L.polyline([[s.lat, s.lng], at(e, R * 1.25)], {color: "#f0a040", weight: 2, dashArray: "6 4", interactive: false}).addTo(layer);
  for (const e of s.padded_edges) L.polyline([[s.lat, s.lng], at(e, R * 1.15)], {color: "#58c7e8", weight: 2, dashArray: "2 4", interactive: false}).addTo(layer);
  if (s.orientation != null) L.polyline([[s.lat, s.lng], at(s.orientation, R * 1.1)], {color: "#7fb2ff", weight: 4, interactive: false}).addTo(layer);
  if (s.optimal != null) L.polyline([[s.lat, s.lng], at(s.optimal, R * 1.0)], {color: "#8fdc8f", weight: 3, dashArray: "8 5", interactive: false}).addTo(layer);
  map.setView([s.lat, s.lng], 14);
}

function render() {
  const s = spot();
  const marked = SPOTS.filter(x => labels[x.slug]).length;
  document.getElementById("prog").textContent =
    `spot ${pos + 1} / ${order.length}${filter !== "all" ? " (" + filter + ")" : ""} · ${marked} of ${SPOTS.length} spots marked`;
  document.getElementById("name").textContent = s.name;
  document.getElementById("meta").textContent = `${s.state || ""} · ${s.slug} · facing ${s.orientation}° · optimal ${s.optimal}°`
    + ` · arcs ${s.arcs.map(a => a.min + "–" + a.max).join(", ") || "none"}`;
  document.getElementById("groups").innerHTML = s.groups.map(g => `<span class="pill">${g.replace(/_/g, " ")}</span>`).join("")
    + (s.lost_deg != null ? `<span class="sub">lost ${s.lost_deg}° in the recompute</span>` : "");
  document.getElementById("fns").innerHTML = FN_NAMES.map((n, k) =>
    `<button class="btn ${k === fn ? "on" : ""}" onclick="fn=${k};render()">${n}</button>`).join(" ");
  drawRose(); drawTable(); drawMap();
}

function doExport() {
  const payload = exportPayload(labels, SPOTS, BUILT_FROM, new Date().toISOString());
  const text = JSON.stringify(payload, null, 2) + "\n";
  try {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], {type: "application/json"}));
    a.download = "swell_direction_labels.json"; document.body.appendChild(a); a.click(); a.remove();
    document.getElementById("status").textContent = `exported ${Object.keys(payload.spots).length} spots to swell_direction_labels.json (your downloads folder)`;
  } catch (e) {
    const t = document.getElementById("fallback"); t.style.display = "block"; t.value = text;
    document.getElementById("status").textContent = "download blocked: copy the JSON below";
  }
}
function doImport(input) {
  const f = input.files[0]; if (!f) return;
  f.text().then(t => { labels = mergeImport(labels, JSON.parse(t)); save(); render();
    document.getElementById("status").textContent = "imported " + f.name; })
    .catch(e => { document.getElementById("status").textContent = "import failed: " + e.message; });
  input.value = "";
}
document.addEventListener("keydown", e => {
  if (e.key === "ArrowRight") go(1); else if (e.key === "ArrowLeft") go(-1);
  else if ("12345".includes(e.key) && e.key.length === 1) { fn = Number(e.key) - 1; render(); }
});
render();
</script>
</body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
