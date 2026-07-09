#!/usr/bin/env python3
"""Build the orientation RELOOK tool — a self-contained HTML reorientation tool
seeded ONLY from scripts/orientation_relook.json (the flagged spots, worst-first),
with PER-SPOT GUIDANCE telling the user which reference arrow to trust.

The relook list mixes two cases that need opposite handling:
  CASE 1  MOP mismatch ("trust MOP" / small folded seed + large MOP Δ): the user's
          orientation already agrees with geometry; the big delta is MOP matching a
          non-representative 10 m point (jetty/harbor/inlet around a corner). Blue is
          probably already right and the spot falls back to it — DON'T reorient.
  CASE 2  Real seed Δ (folded seed disagreement, usually no MOP): the green geometry
          arrow is a genuine reference to reconcile against — these are the real
          relooks. Plus a rare CASE "BOTH" (HIGH-skill CA where both disagree).

Same UX as before — satellite + labels, drag-to-reorient, Enter = confirm +
auto-advance, hover = name, worst-first — plus the guidance banner, the three
numbers, an action hint, MOP-arrow de-emphasis on case 1, and a "real
disagreements only" filter. Export shape UNCHANGED so it flows through
apply_orientation_fixes. Nothing auto-applied.

  python3 scripts/build_orient_relook.py   ->  scripts/orient_relook.html
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RELOOK = os.path.join(HERE, "orientation_relook.json")
OUT_HTML = os.path.join(HERE, "orient_relook.html")

SMALL_SEED = 20.0  # folded seed below this = the user already agrees with geometry


def classify(s, thr):
    """Return per-spot guidance {case, banner, action, mop_suspect, is_case1}."""
    fs = s.get("folded_seed_delta")
    md = s.get("mop_delta")
    zone = s.get("zone")
    fs_big = fs is not None and fs >= thr
    md_big = md is not None and md >= thr
    fs_small = fs is not None and fs < SMALL_SEED
    if zone == "HIGH" and md_big and fs_big:
        return {"case": "BOTH", "is_case1": False, "mop_suspect": False,
                "banner": "BOTH references disagree with you — worth a careful look.",
                "action": "Compare green + magenta, reorient carefully, then Enter"}
    if md_big and fs_small:
        return {"case": "MOP_MISMATCH", "is_case1": True, "mop_suspect": True,
                "banner": "LIKELY ALREADY CORRECT — the large delta is a MOP point mismatch "
                          "(sheltered jetty / harbor / inlet around a corner). Your orientation "
                          "already agrees with the coastline geometry and the spot will fall back "
                          "to it. Confirm as-is unless it looks visibly wrong.",
                "action": "Confirm as-is (Enter) — don't chase the magenta MOP arrow"}
    if fs_big:
        return {"case": "SEED_DISAGREE", "is_case1": False, "mop_suspect": False,
                "banner": f"REAL DISAGREEMENT vs coastline geometry (Δ{fs:.0f}°). Reconcile your "
                          f"blue arrow against the green geometry reference.",
                "action": "Reorient toward green, then Enter"}
    if md_big:
        return {"case": "MOP_ONLY", "is_case1": False, "mop_suspect": False,
                "banner": f"MOP disagrees (Δ{md:.0f}°) and there's no clear geometry seed — "
                          f"judge from the satellite.",
                "action": "Review; reorient if it looks wrong, then Enter"}
    return {"case": "REVIEW", "is_case1": False, "mop_suspect": False,
            "banner": "On the relook list — take a look.", "action": "Review"}


TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>Orientation relook</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html,body{margin:0;height:100%;font:14px/1.4 system-ui,sans-serif;background:#111;color:#eee}
  #wrap{display:flex;height:100%}
  #map{flex:1;height:100%}
  #side{width:360px;padding:14px;box-sizing:border-box;background:#181a1f;overflow:auto}
  h2{margin:.2em 0;font-size:18px} .sub{color:#9aa;font-size:12px}
  .pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;margin-right:4px}
  .HIGH{background:#1d5e2a}.MEDIUM{background:#6b5d12}.HARD{background:#7a2222}.UNKNOWN{background:#444}.null{background:#444}
  #banner{padding:9px 11px;border-radius:7px;margin:8px 0;font-size:13px;font-weight:600;line-height:1.35}
  .b-MOP_MISMATCH{background:#16413f;border:1px solid #2a8f86}
  .b-SEED_DISAGREE{background:#5a4710;border:1px solid #c79a1e}
  .b-BOTH{background:#5e1d1d;border:1px solid #c14a4a}
  .b-MOP_ONLY,.b-REVIEW{background:#1d3a5e;border:1px solid #3a6fc1}
  #action{color:#ffd479;font-weight:700;margin:6px 0}
  table{width:100%;border-collapse:collapse;margin:8px 0}
  td{padding:2px 4px} .num{font-variant-numeric:tabular-nums;text-align:right}
  .cur{color:#7fd1ff;font-weight:700}.seed{color:#9ad29a}.mop{color:#e29ad2}.mop.suspect{color:#7d5e74;text-decoration:line-through}
  #rose{display:block;margin:8px auto;touch-action:none;cursor:grab}
  .btn{background:#2a2f3a;border:1px solid #3a4150;color:#eee;border-radius:6px;padding:6px 10px;cursor:pointer}
  .btn:hover{background:#333a47}
  #prog{margin:6px 0;color:#9aa} kbd{background:#2a2f3a;border:1px solid #3a4150;border-radius:4px;padding:0 5px}
  #done{color:#9ad29a;font-weight:700} label.flt{display:block;margin:8px 0;color:#cdd}
</style></head>
<body><div id="wrap">
  <div id="map"></div>
  <div id="side">
    <div id="prog"></div>
    <h2 id="name"></h2><div class="sub" id="coords"></div>
    <div><span id="zone"></span><span class="sub" id="mopmeta"></span></div>
    <div id="banner"></div>
    <div id="action"></div>
    <svg id="rose" width="280" height="280" viewBox="-150 -150 300 300"></svg>
    <table>
      <tr><td class="cur">your orientation (blue)</td><td class="num cur" id="vcur"></td></tr>
      <tr><td class="seed">geometry seed (green)</td><td class="num seed" id="vseed"></td></tr>
      <tr><td class="mop" id="lmop">MOP shore-normal (magenta)</td><td class="num mop" id="vmop"></td></tr>
    </table>
    <div style="margin:8px 0">
      <button class="btn" onclick="prev()">&larr; prev</button>
      <button class="btn" onclick="confirmAdvance()">confirm + next &crarr;</button>
      <button class="btn" onclick="next()">skip &rarr;</button>
    </div>
    <label class="flt"><input type="checkbox" id="realonly" onchange="toggleFilter()">
      show real disagreements only (hide MOP-mismatch "already correct" spots)</label>
    <div class="sub">drag the blue arrow; <kbd>Enter</kbd> confirm+next,
      <kbd>&larr;</kbd>/<kbd>&rarr;</kbd> nav, <kbd>r</kbd> reset to green seed.</div>
    <div id="prog2" class="sub" style="margin-top:6px"></div>
    <button class="btn" style="margin-top:10px;width:100%" onclick="exportJSON()">export confirmed &darr;</button>
    <div id="done"></div>
  </div>
</div>
<script>
const SPOTS = __RELOOK_DATA__;
const GENERATED = "__GENERATED__";
const CARD = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
function cardinal(d){return CARD[Math.round(((d%360)/22.5))%16];}
let i = 0, realOnly = false;
const result = {};
function curVal(s){ return (result[s.slug] && result[s.slug].orientation_deg!=null)
  ? result[s.slug].orientation_deg : s.orientation_deg; }
function isVisible(j){ return !realOnly || !SPOTS[j].guidance.is_case1; }

const map = L.map('map',{zoomControl:true}).setView([34,-119],13);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:19,attribution:'Esri'}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:19,opacity:0.9}).addTo(map);
let marker=null, sightline=null;

const rose = document.getElementById('rose');
const SVGNS="http://www.w3.org/2000/svg";
function pt(b,r){const a=(b-90)*Math.PI/180; return [r*Math.cos(a), r*Math.sin(a)];}
function arrow(b,r,color,w,dash,op){const [x,y]=pt(b,r); const l=document.createElementNS(SVGNS,'line');
  l.setAttribute('x1',0);l.setAttribute('y1',0);l.setAttribute('x2',x);l.setAttribute('y2',y);
  l.setAttribute('stroke',color);l.setAttribute('stroke-width',w);if(dash)l.setAttribute('stroke-dasharray',dash);
  if(op!=null)l.setAttribute('opacity',op);l.setAttribute('stroke-linecap','round');return l;}
function label(b,txt,color){const [x,y]=pt(b,118);const t=document.createElementNS(SVGNS,'text');
  t.setAttribute('x',x);t.setAttribute('y',y+4);t.setAttribute('fill',color);t.setAttribute('font-size','12');
  t.setAttribute('text-anchor','middle');t.textContent=txt;return t;}
function drawRose(){
  const s=SPOTS[i], g=s.guidance; rose.innerHTML='';
  const c=document.createElementNS(SVGNS,'circle');c.setAttribute('r',130);c.setAttribute('fill','#0d0f13');
  c.setAttribute('stroke','#333');rose.appendChild(c);
  for(const [b,txt] of [[0,'N'],[90,'E'],[180,'S'],[270,'W']]) rose.appendChild(label(b,txt,'#778'));
  const seedDisp=(s.seed_display!=null?s.seed_display:s.seed_normal);
  if(seedDisp!=null) rose.appendChild(arrow(seedDisp,112,'#9ad29a',3,'5 5'));         // geometry seed
  if(s.mop_shore_normal!=null){                                                        // MOP (faint if suspect)
    rose.appendChild(arrow(s.mop_shore_normal,112,'#e29ad2', g.mop_suspect?2:3,'5 5', g.mop_suspect?0.35:1));
    if(g.mop_suspect){const [x,y]=pt(s.mop_shore_normal,96);const t=document.createElementNS(SVGNS,'text');
      t.setAttribute('x',x);t.setAttribute('y',y);t.setAttribute('fill','#7d5e74');t.setAttribute('font-size','10');
      t.setAttribute('text-anchor','middle');t.textContent='MOP suspect';rose.appendChild(t);}
  }
  rose.appendChild(arrow(curVal(s),120,'#7fd1ff',6));                                   // your orientation
  const hub=document.createElementNS(SVGNS,'circle');hub.setAttribute('r',6);hub.setAttribute('fill','#7fd1ff');rose.appendChild(hub);
}
function bearingFromEvent(ev){const r=rose.getBoundingClientRect();const cx=r.left+r.width/2,cy=r.top+r.height/2;
  return ((Math.atan2(ev.clientY-cy,ev.clientX-cx)*180/Math.PI)+90+360)%360;}
let dragging=false;
rose.addEventListener('pointerdown',e=>{dragging=true;rose.setPointerCapture(e.pointerId);setCur(Math.round(bearingFromEvent(e)));});
rose.addEventListener('pointermove',e=>{if(dragging)setCur(Math.round(bearingFromEvent(e)));});
rose.addEventListener('pointerup',()=>{dragging=false;});
function setCur(deg){const s=SPOTS[i];result[s.slug]=result[s.slug]||{name:s.name};
  result[s.slug].orientation_deg=((deg%360)+360)%360;render();}

function render(){
  const s=SPOTS[i], g=s.guidance, cur=curVal(s);
  const vis=SPOTS.filter((_,j)=>isVisible(j)).length;
  document.getElementById('prog').textContent=`spot ${i+1} / ${SPOTS.length}`
    +(realOnly?`  ·  ${vis} real (MOP-mismatch hidden)`:'')+`  ·  worst-first`;
  document.getElementById('name').textContent=s.name;
  document.getElementById('coords').textContent=`${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}  ·  ${s.slug}`;
  document.getElementById('zone').innerHTML=`<span class="pill ${s.zone||'null'}">${s.zone||'—'}</span>`;
  document.getElementById('mopmeta').textContent = s.mop_point
    ? `MOP ${s.mop_point} @ ${s.mop_match_m} m`+(s.zone==='HARD'?' (low-skill zone)':'') : 'no MOP match';
  const ban=document.getElementById('banner'); ban.textContent=g.banner; ban.className='b-'+g.case;
  document.getElementById('action').textContent='→ '+g.action;
  document.getElementById('vcur').textContent=`${cur.toFixed(0)}° ${cardinal(cur)}`
    +(result[s.slug]&&result[s.slug].confirmed?'  ✓':'');
  const sd=(s.seed_display!=null?s.seed_display:s.seed_normal), fsd=(s.folded_seed_delta!=null?s.folded_seed_delta:s.seed_delta);
  document.getElementById('vseed').textContent=sd!=null?`${sd.toFixed(0)}° ${cardinal(sd)}  (folded Δ${fsd?.toFixed(0)}°)`:'—';
  const lmop=document.getElementById('lmop'), vmop=document.getElementById('vmop');
  lmop.className='mop'+(g.mop_suspect?' suspect':'');
  vmop.className='num mop'+(g.mop_suspect?' suspect':'');
  vmop.textContent=s.mop_shore_normal!=null
    ?`${s.mop_shore_normal.toFixed(0)}° ${cardinal(s.mop_shore_normal)}  (MOP Δ${s.mop_delta?.toFixed(0)}°`
       +(g.mop_suspect?', suspect':'')+`)`:'—';
  const nconf=Object.values(result).filter(r=>r.confirmed).length;
  document.getElementById('prog2').textContent=`${nconf} confirmed · ${SPOTS.length-nconf} left`;
  drawRose();
  if(!marker){marker=L.marker([s.lat,s.lon]).addTo(map);} else marker.setLatLng([s.lat,s.lon]);
  marker.bindTooltip(s.name,{permanent:false});
  map.setView([s.lat,s.lon],16);
  if(sightline)map.removeLayer(sightline);
  const dlat=0.006*Math.cos(cur*Math.PI/180), dlon=0.006*Math.sin(cur*Math.PI/180)/Math.cos(s.lat*Math.PI/180);
  sightline=L.polyline([[s.lat,s.lon],[s.lat+dlat,s.lon+dlon]],{color:'#7fd1ff',weight:3}).addTo(map);
}
function stepTo(start,dir){let j=start;for(let k=0;k<SPOTS.length;k++){j=(j+dir+SPOTS.length)%SPOTS.length;if(isVisible(j))return j;}return start;}
function next(){i=stepTo(i,1);render();}
function prev(){i=stepTo(i,-1);render();}
function confirmAdvance(){const s=SPOTS[i];result[s.slug]=result[s.slug]||{name:s.name,orientation_deg:s.orientation_deg};
  result[s.slug].orientation_deg=curVal(s);result[s.slug].confirmed=true;
  let n=i;for(let k=1;k<=SPOTS.length;k++){const j=(i+k)%SPOTS.length;
    if(isVisible(j)&&!(result[SPOTS[j].slug]&&result[SPOTS[j].slug].confirmed)){n=j;break;}}
  i=n;render();
  if(Object.values(result).filter(r=>r.confirmed).length===SPOTS.length)
    document.getElementById('done').textContent='all spots confirmed — export below.';
}
function toggleFilter(){realOnly=document.getElementById('realonly').checked;
  if(!isVisible(i))i=stepTo(i,1);render();}
document.addEventListener('keydown',e=>{
  if(e.key==='Enter')confirmAdvance(); else if(e.key==='ArrowRight')next();
  else if(e.key==='ArrowLeft')prev();
  else if(e.key==='r'){const s=SPOTS[i];const sd=(s.seed_display!=null?s.seed_display:s.seed_normal);if(sd!=null)setCur(Math.round(sd));}
});
function exportJSON(){
  const out={_comment:`orientation relook export ${GENERATED}; apply via apply_orientation_fixes`,
             _schema_version:1, orientations:{}};
  for(const [slug,r] of Object.entries(result)){ if(!r.confirmed)continue;
    out.orientations[slug]={orientation_deg:Math.round(r.orientation_deg),
      cardinal:cardinal(r.orientation_deg), name:r.name, source:"manual_relook"}; }
  const n=Object.keys(out.orientations).length;
  if(!n){alert('nothing confirmed yet');return;}
  const blob=new Blob([JSON.stringify(out,null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download='orientation_relook_export.json';a.click();
}
render();
</script></body></html>
"""


def main():
    if not os.path.exists(RELOOK):
        raise SystemExit(f"missing {RELOOK} — run scripts/orientation_relook.py first")
    payload = json.load(open(RELOOK))
    spots = payload.get("spots", [])
    thr = payload.get("_meta", {}).get("threshold_deg", 30.0)

    case1 = case_seed = case_both = case_moponly = 0
    for s in spots:
        g = classify(s, thr)
        s["guidance"] = g
        if g["case"] == "MOP_MISMATCH":
            case1 += 1
        elif g["case"] == "SEED_DISAGREE":
            case_seed += 1
        elif g["case"] == "BOTH":
            case_both += 1
        elif g["case"] == "MOP_ONLY":
            case_moponly += 1
    case2 = case_seed + case_both + case_moponly

    html = (TEMPLATE.replace("__RELOOK_DATA__", json.dumps(spots))
            .replace("__GENERATED__", str(payload.get("_meta", {}).get("scoring", ""))))
    with open(OUT_HTML, "w") as f:
        f.write(html)

    print(f"wrote {OUT_HTML}  ({len(spots)} flagged spots, worst-first)")
    print(f"  CASE 1  MOP-mismatch (confirm as-is / skip):  {case1}")
    print(f"  CASE 2  real relook:                          {case2}"
          f"   [seed-Δ {case_seed} · both {case_both} · MOP-only {case_moponly}]")
    print(f"  → true relook count is {case2} of {len(spots)}; the other {case1} just confirm as-is.")
    # show a couple of classified examples for confirmation
    ex = []
    for want in ("MOP_MISMATCH", "SEED_DISAGREE", "BOTH", "MOP_ONLY"):
        for s in spots:
            if s["guidance"]["case"] == want:
                ex.append(s); break
    if ex:
        print("  examples:")
        for s in ex[:3]:
            g = s["guidance"]
            print(f"    [{g['case']:13}] {s['name'][:26]:26} fSeed={s.get('folded_seed_delta')} "
                  f"MOPΔ={s.get('mop_delta')} zone={s.get('zone')}  → {g['action'][:40]}")


if __name__ == "__main__":
    main()
