"""Standalone HTML dashboard export.

One self-contained file: the payload is embedded as JSON, all CSS and JS are
inline. Plotly is loaded from CDN for the chart and map geometry (country
topology). Pass ``vendor_plotly=True`` with a local copy to make the export
fully offline.

Design notes (the chart decisions are deliberate, not defaults):

* The categorical order is the validated one from ``config/styles.yaml``.
  Colour follows the entity, so hiding a series never repaints the survivors.
* Every map layer is a *diverging* or *sequential* encoding chosen by the job
  the data does -- polarity gets blue/red with a neutral gray midpoint,
  magnitude gets one hue light-to-dark. No rainbows, no hue at a midpoint.
* One y-axis, always. Where two measures of different scale need comparing
  they get two charts, never two scales.
* Light mode has three categorical slots below 3:1 contrast, so the relief
  rule applies: every analogue line carries a direct end label and every
  chart has a table-view twin.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"


def _vendored_plotly() -> str | None:
    """Locate the plotly.js bundle shipped with the plotly Python package."""
    try:
        import plotly
    except ImportError:
        return None
    candidate = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
    return candidate.read_text(encoding="utf-8") if candidate.exists() else None


def export_dashboard(
    payload: dict[str, Any],
    target: Path,
    *,
    plotly_src: str = PLOTLY_CDN,
    vendor: bool = False,
    topojson_url: str | None = None,
) -> Path:
    """Write the single-file dashboard.

    ``vendor=True`` inlines plotly.js so the file opens with no network at all.
    Note the one remaining dependency: plotly.js fetches country topology for
    the choropleth from its CDN at render time. For a fully air-gapped export,
    also pass ``topojson_url`` pointing at a local directory containing the
    plotly-geo topojson bundles -- everything else in the file is self-contained.
    """
    target.parent.mkdir(parents=True, exist_ok=True)

    if vendor:
        bundle = _vendored_plotly()
        if bundle is None:
            raise RuntimeError(
                "vendor=True requires the plotly Python package "
                "(pip install plotly) to source plotly.min.js"
            )
        script_tag = f"<script>{bundle}</script>"
    else:
        script_tag = f'<script src="{plotly_src}" charset="utf-8"></script>'

    config = (
        f'<script>window.PLOTLY_TOPOJSON_URL={json.dumps(topojson_url)};</script>'
        if topojson_url else ""
    )

    html = (
        TEMPLATE
        .replace('<script src="__PLOTLY_SRC__" charset="utf-8"></script>',
                 script_tag + config)
        .replace("__PAYLOAD__", json.dumps(payload, default=str))
    )
    target.write_text(html, encoding="utf-8")
    return target


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>El Nino 2026-27 Tracker</title>
<script src="__PLOTLY_SRC__" charset="utf-8"></script>
<style>
:root{
  color-scheme: light;
  --bg:#f9f9f7; --surface:#fcfcfb; --surface-alt:#f0efec;
  --border:rgba(11,11,11,0.10); --grid:#e1e0d9; --axis:#c3c2b7;
  --ink:#0b0b0b; --ink-2:#52514e; --ink-muted:#898781;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --rule:#898781;
  --shadow:0 1px 2px rgba(11,11,11,.05), 0 8px 24px rgba(11,11,11,.04);
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --bg:#0d0d0d; --surface:#1a1a19; --surface-alt:#232322;
  --border:rgba(255,255,255,0.10); --grid:#2c2c2a; --axis:#383835;
  --ink:#ffffff; --ink-2:#c3c2b7; --ink-muted:#898781;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
  --shadow:none;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg); color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  font-size:15px; line-height:1.5;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--s1)}
.wrap{max-width:1320px;margin:0 auto;padding:20px 20px 80px}

/* ---------- header ---------- */
header.top{
  display:flex;align-items:flex-start;justify-content:space-between;
  gap:20px;flex-wrap:wrap;padding:12px 0 18px;
}
h1{font-size:1.45rem;margin:0 0 4px;letter-spacing:-.015em;font-weight:650}
.sub{color:var(--ink-2);font-size:.84rem;margin:0}
.controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button,select,input[type=search]{
  font:inherit;font-size:.82rem;color:var(--ink);background:var(--surface);
  border:1px solid var(--border);border-radius:8px;padding:7px 11px;cursor:pointer;
}
button:hover,select:hover{background:var(--surface-alt)}
button:focus-visible,select:focus-visible,input:focus-visible,
[tabindex]:focus-visible{outline:2px solid var(--s1);outline-offset:2px}
button[aria-pressed="true"]{background:var(--ink);color:var(--surface);border-color:var(--ink)}

/* ---------- alert strip ----------
   Three tiers, because an operator who sees the same two reds every morning
   stops reading the strip. Each tier carries an icon AND a text label, so the
   severity never depends on colour alone. */
.warnbox{margin:0 0 16px;border:1px solid var(--border);border-radius:10px;
  background:var(--surface);overflow:hidden}
.warnbox>summary{
  list-style:none;cursor:pointer;padding:9px 13px;display:flex;
  align-items:center;gap:10px;flex-wrap:wrap;font-size:.83rem;
}
.warnbox>summary::-webkit-details-marker{display:none}
.warnbox>summary:hover{background:var(--surface-alt)}
.warnbox>summary:focus-visible{outline:2px solid var(--s1);outline-offset:-2px}
.warnbox>summary .caret{
  color:var(--ink-muted);font-size:.7rem;flex:none;
  transition:transform .15s ease;display:inline-block;
}
.warnbox[open]>summary .caret{transform:rotate(90deg)}
.warnbox>summary .hd{font-weight:640;flex:none}
.warnbox>summary .lead{color:var(--ink-2);min-width:0;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;flex:1}
.warnbox>summary .counts{display:flex;gap:6px;flex:none;flex-wrap:wrap}
/* Severity is carried by the left rule of the bar itself, so the worst tier
   present is visible without expanding. */
.warnbox.worst-incident{border-left:3px solid var(--critical)}
.warnbox.worst-standing{border-left:3px solid var(--warning)}
.warnbox.worst-configuration{border-left:3px solid var(--ink-muted)}
.warnbox.worst-none{border-left:3px solid var(--good)}
.alerts{display:flex;flex-direction:column;gap:6px;padding:0 13px 13px}
@media (max-width:640px){
  .warnbox>summary .lead{display:none}
}
.alert{
  display:flex;gap:10px;align-items:flex-start;font-size:.83rem;
  background:var(--surface);border:1px solid var(--border);
  border-left:3px solid var(--ink-muted);border-radius:8px;padding:9px 12px;
}
.alert.sev-incident{border-left-color:var(--critical)}
.alert.sev-standing{border-left-color:var(--warning)}
.alert.sev-configuration{border-left-color:var(--ink-muted)}
.alert .body{flex:1;min-width:0}
.alert .title{font-weight:620}
.alert .detail{color:var(--ink-2);margin-top:2px}
.chip{
  display:inline-flex;align-items:center;gap:5px;flex:none;
  font-size:.68rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  border:1px solid var(--border);border-radius:999px;padding:2px 8px;
  color:var(--ink-2);background:var(--surface-alt);white-space:nowrap;
}
.chip .ico{font-size:.8rem;line-height:1}
.chip.sev-incident{color:var(--critical);border-color:var(--critical)}
.chip.sev-standing{color:#8a5d00;border-color:var(--warning)}
:root[data-theme="dark"] .chip.sev-standing{color:var(--warning)}
.alert details summary{font-size:.8rem;padding:2px 0}
.alert details ul{margin:6px 0 0;padding-left:18px;color:var(--ink-2)}
.alert details li{margin-bottom:3px}
.allclear{
  font-size:.82rem;color:var(--ink-2);background:var(--surface);
  border:1px solid var(--border);border-left:3px solid var(--good);
  border-radius:8px;padding:9px 12px;
}

/* ---------- cards & grid ---------- */
.grid{display:grid;gap:14px}
.card{
  background:var(--surface);border:1px solid var(--border);
  border-radius:12px;padding:16px 18px;box-shadow:var(--shadow);
}
.card h2{font-size:.94rem;margin:0 0 2px;font-weight:640;letter-spacing:-.005em}
.card .cap{font-size:.76rem;color:var(--ink-2);margin:0 0 12px}

/* ---------- stat tiles ---------- */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(158px,1fr));gap:14px;margin-bottom:14px}
.tile .val{font-size:2.05rem;font-weight:660;letter-spacing:-.03em;line-height:1.05;margin:2px 0 1px}
.tile .lab{font-size:.74rem;color:var(--ink-2);text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.tile .note{font-size:.74rem;color:var(--ink-muted);margin-top:3px}
.tile .val.hero{font-size:2.7rem}

/* ---------- gauge ---------- */
.gauge{margin:8px 0 2px}
.gauge-track{display:flex;height:9px;border-radius:5px;overflow:hidden;gap:2px}
.gauge-track span{flex:1;border-radius:2px}
.gauge-marks{position:relative;height:34px;margin-top:5px}
.gauge-mark{position:absolute;transform:translateX(-50%);text-align:center;font-size:.7rem;color:var(--ink-2)}
.gauge-mark b{display:block;font-size:.78rem;color:var(--ink)}
.gauge-mark::before{content:"";display:block;width:2px;height:8px;background:var(--ink);margin:0 auto 2px;border-radius:1px}
.gauge-mark.ghost::before{background:var(--ink-muted)}
.gauge-scale{display:flex;justify-content:space-between;font-size:.68rem;color:var(--ink-muted);margin-top:2px}

/* ---------- tabs ---------- */
nav.tabs{display:flex;gap:4px;border-bottom:1px solid var(--border);margin:22px 0 16px;flex-wrap:wrap}
nav.tabs button{
  border:none;background:none;border-radius:0;padding:9px 13px;
  border-bottom:2px solid transparent;color:var(--ink-2);font-weight:560;font-size:.86rem;
}
nav.tabs button[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--s2)}
nav.tabs button:hover{background:var(--surface-alt)}
.panel{display:none}
.panel.on{display:block}

/* ---------- filter row (one row, above everything it scopes) ---------- */
.filters{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.filters label{font-size:.76rem;color:var(--ink-2);font-weight:600}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:.77rem;color:var(--ink-2);margin-top:8px}
.legend i{display:inline-block;width:22px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.legend .dashed{height:0;border-top:2px dashed var(--rule)}

/* ---------- tables ---------- */
.tablewrap{overflow-x:auto;margin-top:6px}
.tablewrap.wrap td,.tablewrap.wrap th{white-space:normal}
.tablewrap.wrap td:last-child{max-width:46ch;line-height:1.4}
table{border-collapse:collapse;width:100%;font-size:.81rem;font-variant-numeric:tabular-nums}
th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}
th{
  position:sticky;top:0;background:var(--surface);cursor:pointer;
  font-weight:640;color:var(--ink-2);font-size:.74rem;
  text-transform:uppercase;letter-spacing:.04em;
}
th:hover{color:var(--ink)}
th[aria-sort="ascending"]::after{content:" \2191"}
th[aria-sort="descending"]::after{content:" \2193"}
tbody tr:hover{background:var(--surface-alt)}
tbody tr.pinned td:first-child{font-weight:650}
.pill{
  display:inline-block;padding:1px 7px;border-radius:999px;font-size:.68rem;
  border:1px solid var(--border);color:var(--ink-2);font-weight:600;
}
.pill.bad{border-color:var(--critical);color:var(--critical)}
.pill.ok{border-color:var(--good);color:var(--good)}
.num-neg{color:var(--critical)}
.num-pos{color:var(--good)}

/* ---------- region cards ---------- */
.regiongrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:14px}
.hazards{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.metricrow{display:flex;justify-content:space-between;font-size:.8rem;padding:4px 0;border-bottom:1px solid var(--border)}
.metricrow:last-child{border-bottom:none}
.metricrow span:first-child{color:var(--ink-2)}

/* ---------- provenance ---------- */
abbr[title]{
  text-decoration:underline dotted var(--ink-muted);
  text-underline-offset:2px;cursor:help;
}
abbr[title]:hover,abbr[title]:focus{color:var(--ink)}
/* Column notes: a table should be readable, or exportable and sendable,
   without the reader needing the Method tab. */
.colnote{
  margin-top:14px;padding-top:12px;border-top:1px solid var(--border);
  font-size:.78rem;color:var(--ink-2);
}
.colnote h4{
  font-size:.76rem;margin:0 0 6px;color:var(--ink);font-weight:640;
  text-transform:uppercase;letter-spacing:.04em;
}
.colnote .method{margin:0 0 10px;max-width:96ch;line-height:1.5}
.colnote .caveat{
  margin:10px 0 0;padding:8px 11px;background:var(--surface-alt);
  border-radius:7px;max-width:96ch;line-height:1.5;
}
.colnote dl{
  display:grid;grid-template-columns:minmax(110px,auto) 1fr;
  gap:3px 14px;margin:0;align-items:baseline;
}
.colnote dt{font-weight:640;color:var(--ink);white-space:nowrap}
.colnote dd{margin:0;line-height:1.45}
.colnote dd .more{display:block;color:var(--ink-muted);margin-top:3px}
.colnote .missing{color:var(--critical)}
@media (max-width:640px){
  .colnote dl{grid-template-columns:1fr;gap:1px}
  .colnote dt{margin-top:7px}
}
.gloss{columns:2;column-gap:26px;margin-top:8px}
.gloss dl{break-inside:avoid;margin:0 0 14px}
.gloss dt{font-weight:640;font-size:.82rem}
.gloss dd{margin:1px 0 0;font-size:.79rem;color:var(--ink-2)}
.gloss dd.n{color:var(--ink-muted);margin-top:3px}
.gloss h4{font-size:.76rem;text-transform:uppercase;letter-spacing:.05em;
  color:var(--ink-2);margin:0 0 6px;break-after:avoid}
@media (max-width:760px){.gloss{columns:1}}
.prov{font-size:.72rem;color:var(--ink-muted);margin-top:10px;line-height:1.45}
.prov code{background:var(--surface-alt);padding:1px 4px;border-radius:4px;font-size:.95em}
details{margin-top:10px;font-size:.82rem}
summary{cursor:pointer;font-weight:600;color:var(--ink-2);padding:4px 0}
summary:hover{color:var(--ink)}
.methods p{color:var(--ink-2);font-size:.85rem;max-width:76ch}
.methods h3{font-size:.86rem;margin:18px 0 6px}
.srcgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-top:10px}
.srcitem{font-size:.78rem;border:1px solid var(--border);border-radius:8px;padding:9px 11px}
.srcitem b{display:block;font-size:.8rem;margin-bottom:2px}

@media (max-width:640px){
  .wrap{padding:12px 12px 60px}
  .tile .val{font-size:1.65rem}
  .tile .val.hero{font-size:2.1rem}
  h1{font-size:1.2rem}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media print{.controls,nav.tabs{display:none}.panel{display:block!important}}
</style>
</head>
<body>
<div class="wrap">

<header class="top">
  <div>
    <h1>El Ni&ntilde;o 2026&ndash;27 Tracker</h1>
    <p class="sub" id="subtitle"></p>
  </div>
  <div class="controls">
    <button id="themeBtn" type="button" aria-label="Toggle colour theme">Dark mode</button>
    <button id="shareBtn" type="button">Copy snapshot link</button>
    <button id="updateBtn" type="button" title="Re-runs Steps A-F on the server">Update now</button>
  </div>
</header>

<details class="warnbox" id="warnbox">
  <summary id="warnsummary"></summary>
  <div class="alerts" id="alerts"></div>
</details>

<div class="tiles" id="tiles"></div>

<div class="card">
  <h2>Intensity gauge &mdash; Ni&ntilde;o 3.4 sea-surface temperature anomaly</h2>
  <p class="cap" id="gaugeCap"></p>
  <div class="gauge">
    <div class="gauge-track" id="gaugeTrack"></div>
    <div class="gauge-marks" id="gaugeMarks"></div>
    <div class="gauge-scale"><span>0.0</span><span>1.0</span><span>2.0</span><span>3.0</span><span>4.5&nbsp;&deg;C</span></div>
  </div>
</div>

<nav class="tabs" id="tabs" role="tablist"></nav>

<!-- ============ OVERVIEW ============ -->
<section class="panel on" id="panel-overview" role="tabpanel">
  <div class="card">
    <h2>Forecast ensemble against the three strongest events on record</h2>
    <p class="cap" id="plumeCap"></p>
    <div class="filters">
      <label for="analogSel">Compare with</label>
      <select id="analogSel" multiple size="3" style="min-width:170px">
        <option value="1982-83" selected>1982&ndash;83</option>
        <option value="1997-98" selected>1997&ndash;98</option>
        <option value="2015-16" selected>2015&ndash;16</option>
      </select>
      <button id="plumeTableBtn" type="button" aria-pressed="false">Table view</button>
    </div>
    <div id="plume" style="height:430px"></div>
    <div class="legend" id="plumeLegend"></div>
    <div id="plumeTable" hidden></div>
    <p class="prov" id="plumeProv"></p>
  </div>
</section>

<!-- ============ MAP ============ -->
<section class="panel" id="panel-map" role="tabpanel">
  <div class="filters">
    <label for="layerSel">Layer</label>
    <select id="layerSel"></select>
    <label for="scenSel">Scenario</label>
    <select id="scenSel"></select>
    <button id="mapTableBtn" type="button" aria-pressed="false">Table view</button>
  </div>
  <div class="card">
    <h2 id="mapTitle"></h2>
    <p class="cap" id="mapCap"></p>
    <div id="map" style="height:520px"></div>
    <div id="mapTable" hidden></div>
    <p class="prov" id="mapProv"></p>
  </div>
  <div class="card" id="pinCard" style="margin-top:14px"></div>
</section>

<!-- ============ REGIONS ============ -->
<section class="panel" id="panel-regions" role="tabpanel">
  <div class="filters">
    <label for="regionSearch">Filter</label>
    <input type="search" id="regionSearch" placeholder="Country or hazard&hellip;" style="min-width:210px">
    <label for="groupSel">Group</label>
    <select id="groupSel"></select>
  </div>
  <div class="regiongrid" id="regions"></div>
</section>

<!-- ============ COMMODITIES ============ -->
<section class="panel" id="panel-commodities" role="tabpanel">
  <div class="filters">
    <label for="commScenSel">Scenario</label>
    <select id="commScenSel"></select>
    <button data-export="commodities" data-fmt="csv" type="button">Export CSV</button>
    <button data-export="commodities" data-fmt="json" type="button">Export JSON</button>
  </div>
  <div class="card">
    <h2>Modelled price response by commodity</h2>
    <p class="cap">Global-production-weighted shock &rarr; price, one axis. Bars right of zero are upward price pressure; bars left are downward. Scenario output, not a forecast.</p>
    <div id="commChart" style="height:420px"></div>
    <p class="prov" id="commProv"></p>
  </div>
  <div class="card" style="margin-top:14px">
    <h2>Commodity table</h2>
    <p class="cap">Sortable. <em>Modelled</em> is the share of world production this system has an actual response for; <em>Listed</em> is the share it can name. The gap is supply it knows exists but cannot model &mdash; for fishmeal that is mostly Chinese rendering of imported raw material, which does not answer to a Peruvian El Ni&ntilde;o. An earlier build counted the gap as coverage, which made a 32%-modelled estimate look like 57%.</p>
    <div class="tablewrap" id="commTable"></div>
  </div>
</section>

<!-- ============ COUNTRY TABLE ============ -->
<section class="panel" id="panel-table" role="tabpanel">
  <div class="filters">
    <input type="search" id="tableSearch" placeholder="Filter countries&hellip;" style="min-width:210px">
    <button data-export="countries" data-fmt="csv" type="button">Export CSV</button>
    <button data-export="countries" data-fmt="json" type="button">Export JSON</button>
  </div>
  <div class="card">
    <h2>All modelled regions</h2>
    <p class="cap">Every value here is the composite response scaled to forecast intensity. Confidence already carries the extrapolation penalty.</p>
    <div class="tablewrap" id="countryTable"></div>
  </div>
</section>

<!-- ============ METHOD ============ -->
<section class="panel methods" id="panel-method" role="tabpanel">
  <div class="card" id="methodCard"></div>
</section>

</div>

<script>
const PAYLOAD = __PAYLOAD__;
const $ = (s) => document.querySelector(s);
const fmt = (v, d=1) => (v===null||v===undefined||Number.isNaN(v)) ? "n/a" : Number(v).toFixed(d);
const sgn = (v, d=1) => (v===null||v===undefined||Number.isNaN(v)) ? "n/a" : (v>0?"+":"") + Number(v).toFixed(d);
/* Acronym expansion. Applied to text that has ALREADY been escaped, so there
   are no tags to corrupt. Each term is wrapped at most once per string --
   expanding every occurrence turns prose into a thicket. */
let GLOSS_PATTERN = null;
function glossPattern(){
  if (GLOSS_PATTERN !== null) return GLOSS_PATTERN;
  const terms = Object.keys((PAYLOAD.glossary || {}).terms || {});
  if (!terms.length) return (GLOSS_PATTERN = false);
  const alts = terms
    .sort((a, b) => b.length - a.length)          // longest first: "Nino 3.4" before "Nino"
    .map(s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  return (GLOSS_PATTERN = new RegExp(`\\b(${alts})\\b`, "g"));
}

/* DOM-level pass. Walks text nodes after render, so captions and headings
   built from template literals containing markup are covered too -- a
   string-level replace cannot touch those without risking the tags. Skips
   anything already inside an <abbr>, code, or the glossary table itself. */
function glossifyDom(root){
  const re = glossPattern();
  if (!re) return;
  const terms = PAYLOAD.glossary.terms;
  const seen = new Set();
  const SKIP = new Set(["ABBR", "CODE", "SCRIPT", "STYLE", "OPTION", "TEXTAREA", "SVG"]);

  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node){
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      for (let el = node.parentElement; el && el !== root; el = el.parentElement){
        if (SKIP.has(el.tagName) || el.classList.contains("gloss")) {
          return NodeFilter.FILTER_REJECT;
        }
      }
      return NodeFilter.FILTER_ACCEPT;
    }
  });

  const targets = [];
  while (walker.nextNode()) targets.push(walker.currentNode);

  targets.forEach(node => {
    re.lastIndex = 0;
    if (!re.test(node.nodeValue)) return;
    re.lastIndex = 0;
    const frag = document.createDocumentFragment();
    let last = 0, m;
    while ((m = re.exec(node.nodeValue)) !== null){
      const entry = terms[m[1]];
      if (!entry || seen.has(m[1])) continue;   // once per page keeps prose clean
      seen.add(m[1]);
      frag.appendChild(document.createTextNode(node.nodeValue.slice(last, m.index)));
      const el = document.createElement("abbr");
      el.title = entry.note ? `${entry.full} \u2014 ${entry.note.trim()}` : entry.full;
      el.textContent = m[1];
      frag.appendChild(el);
      last = m.index + m[1].length;
    }
    if (!last) return;
    frag.appendChild(document.createTextNode(node.nodeValue.slice(last)));
    node.parentNode.replaceChild(frag, node);
  });
}

function glossify(escaped){
  const re = glossPattern();
  if (!re || !escaped) return escaped;
  const terms = PAYLOAD.glossary.terms;
  const used = new Set();
  re.lastIndex = 0;
  return String(escaped).replace(re, (match) => {
    if (used.has(match)) return match;
    const entry = terms[match];
    if (!entry) return match;
    used.add(match);
    const title = entry.note ? `${entry.full} \u2014 ${entry.note.trim()}` : entry.full;
    return `<abbr title="${String(title).replace(/"/g, "&quot;")}">${match}</abbr>`;
  });
}

const esc = (s) => String(s??"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const titleCase = (s) => String(s).replace(/_/g," ").replace(/\b\w/g, c=>c.toUpperCase());

const S = PAYLOAD.state, META = PAYLOAD.meta, ENS = PAYLOAD.ensemble;
const STYLES = PAYLOAD.styles || {};
let THEME = "light";
let SCENARIO = "base";

/* ---------------- theme ---------------- */
function palette(){
  const cat = (STYLES.categorical||{})[THEME] || ["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4"];
  const css = getComputedStyle(document.documentElement);
  return {
    cat,
    ink: css.getPropertyValue("--ink").trim(),
    ink2: css.getPropertyValue("--ink-2").trim(),
    muted: css.getPropertyValue("--ink-muted").trim(),
    grid: css.getPropertyValue("--grid").trim(),
    axis: css.getPropertyValue("--axis").trim(),
    surface: css.getPropertyValue("--surface").trim(),
    div: (STYLES.diverging||{})[THEME] || {},
    seq: STYLES.sequential || {}
  };
}
function setTheme(mode){
  THEME = mode;
  document.documentElement.setAttribute("data-theme", mode);
  $("#themeBtn").textContent = mode === "dark" ? "Light mode" : "Dark mode";
  renderAll();
}

/* ---------------- header ---------------- */
function renderHeader(){
  $("#subtitle").innerHTML =
    `Peak forecast <b>${fmt(S.peak_median)}&nbsp;&deg;C</b> `
    + `(80% interval ${fmt(S.peak_p10)}&ndash;${fmt(S.peak_p90)}) &middot; `
    + `${ENS.n_models} models, ${ENS.n_members} members, ${esc(ENS.initialization)} initialisation &middot; `
    + `run <code>${esc(META.run_id)}</code>, generated ${esc(String(META.generated_utc).slice(0,16))}Z`;

  renderAlerts();

  const record = S.record_to_beat_monthly;
  const tiles = [
    {lab:"Forecast peak", val: fmt(S.peak_median)+"&nbsp;&deg;C", note:`80% ${fmt(S.peak_p10)}&ndash;${fmt(S.peak_p90)}`, hero:true},
    {lab:"Observed ONI", labFull:"Oceanic Ni\u00f1o Index", val: sgn(S.current_oni), note:`${esc(S.current_oni_season)} &middot; ${titleCase(S.category)}`},
    {lab:"Days to peak", val: String(S.days_to_peak), note:`centred ${esc(S.peak_centre)}`},
    {lab:"P(beats "+record+"&nbsp;&deg;C)", val: Math.round(S.prob_exceed_record*100)+"%", note:"vs 2015&ndash;16 record"},
    {lab:"P(super event)", val: Math.round(S.prob_super*100)+"%", note:"ONI &ge; 2.5&nbsp;&deg;C"},
    {lab:"Sources live", val: `${META.sources_live}`, note:`${META.sources_seeded} seeded &middot; ${META.sources_dormant} dormant`}
  ];
  $("#tiles").innerHTML = tiles.map(t =>
    `<div class="card tile"><div class="lab"${t.labFull?` title="${esc(t.labFull)}"`:""}>${t.lab}</div>`
    + `<div class="val${t.hero?" hero":""}">${t.val}</div>`
    + `<div class="note">${t.note}</div></div>`).join("");
}

const SEV_ICON = {incident:"!", standing:"\u25b2", configuration:"\u25cb"};

function renderAlerts(){
  const box = $("#alerts");
  const wrap = $("#warnbox");
  const summary = $("#warnsummary");
  const alerts = PAYLOAD.alerts || [];
  const order = PAYLOAD.severity_order || ["incident","standing","configuration"];
  const meta = PAYLOAD.severity_meta || {};
  box.innerHTML = "";

  /* The collapsed bar has to survive being the only thing on screen, so it
     carries the counts AND the most urgent title. Hiding an incident entirely
     behind a click would undo the point of having severity tiers at all. */
  const counts = order.map(sev => [sev, alerts.filter(a => a.severity === sev).length])
                      .filter(([, n]) => n > 0);
  const worst = counts.length ? counts[0][0] : "none";
  wrap.className = "warnbox worst-" + worst;

  const chips = counts.map(([sev, n]) =>
    `<span class="chip sev-${sev}"><span class="ico">${SEV_ICON[sev]||"-"}</span>`
    + `${n}&nbsp;${esc(((meta[sev]||{}).label || sev).toLowerCase())}</span>`).join("");

  const lead = alerts.length ? alerts[0].title : "";
  summary.innerHTML = `<span class="caret">\u25b6</span>`
    + `<span class="hd">Warnings</span>`
    + `<span class="counts">${chips || `<span class="chip">none</span>`}</span>`
    + `<span class="lead">${esc(lead)}</span>`;

  if (!alerts.length){
    box.innerHTML = `<div class="allclear"><b>\u2713 All clear</b> \u2014 no alerts this run.</div>`;
    return;
  }

  const card = (sev, title, detail, extra) => {
    const label = (meta[sev]||{}).label || sev;
    return `<div class="alert sev-${sev}">`
      + `<span class="chip sev-${sev}"><span class="ico">${SEV_ICON[sev]||"-"}</span>${esc(label)}</span>`
      + `<span class="body"><span class="title">${glossify(esc(title))}</span>`
      + `<div class="detail">${glossify(esc(detail))}</div>${extra||""}</span></div>`;
  };

  order.forEach(sev => {
    const group = alerts.filter(a => a.severity === sev);
    if (!group.length) return;

    /* Incidents and standing conditions each get their own row -- they are
       few and they matter. Configuration entries collapse into one row with
       a disclosure, because a fresh deployment can have half a dozen and
       they are all the same message: "this is switched off". */
    if (sev === "configuration" && group.length > 1){
      const items = group.map(a =>
        `<li><b>${glossify(esc(a.title))}</b> \u2014 ${glossify(esc(a.detail))}</li>`).join("");
      box.insertAdjacentHTML("beforeend", card(
        sev,
        `${group.length} capabilities not enabled`,
        (meta[sev]||{}).description || "",
        `<details><summary>Show what is switched off</summary><ul>${items}</ul></details>`
      ));
    } else {
      group.forEach(a => box.insertAdjacentHTML("beforeend",
        card(sev, a.title, a.detail)));
    }
  });
}

function renderGauge(){
  const bands = (STYLES.gauge||{}).bands || [];
  const max = 4.5;
  $("#gaugeTrack").innerHTML = bands.map((b,i) => {
    const lo = i === 0 ? 0 : bands[i-1].max;
    return `<span style="flex:${(b.max-lo)/max};background:${b.colour||b.color}" title="${esc(b.label)}"></span>`;
  }).join("");

  const marks = [
    {v:S.current_oni, label:"Observed", sub:sgn(S.current_oni)+"&deg;C", ghost:false},
    {v:S.record_to_beat_monthly, label:"Record", sub:S.record_to_beat_monthly+"&deg;C", ghost:true},
    {v:S.peak_median, label:"Forecast", sub:fmt(S.peak_median)+"&deg;C", ghost:false}
  ];
  $("#gaugeMarks").innerHTML = marks.map(m =>
    `<div class="gauge-mark${m.ghost?" ghost":""}" style="left:${Math.min(98,Math.max(2,(m.v/max)*100))}%">`
    + `<b>${m.sub}</b>${m.label}</div>`).join("");

  $("#gaugeCap").innerHTML =
    `Current status from the <abbr title="NOAA Climate Prediction Center">CPC</abbr> (NOAA Climate Prediction Center): <b>${esc(S.alert_status)}</b>`
    + (((S.provenance||{}).advisory === "carried_forward")
        ? ` <span style="color:var(--ink-muted)">(last issued advisory, carried forward &mdash; the <abbr title="NOAA Climate Prediction Center">CPC</abbr> issues it monthly in the ENSO Diagnostic Discussion, separately from the weekly sea-surface temperature file)</span>` : "")
    + `. Band labels are printed, so colour is redundant encoding rather than the information channel. `
    + `The record marker is the ${S.record_to_beat_monthly}&nbsp;&deg;C monthly ERSSTv5 peak from 2015&ndash;16; the seasonal ONI peak of that event was ${S.record_to_beat_oni}&nbsp;&deg;C.`;
}

/* ---------------- plume ---------------- */
const ANALOG_SLOT = {"1982-83":2, "1997-98":3, "2015-16":4};

function monthAxis(){
  /* Union of every month any series occupies, sorted. Analogues, the observed
     ONI path and the forecast all live on the same calendar axis -- which is
     the whole point: El Nino is seasonally phase-locked, so a comparison that
     is not calendar-aligned compares nothing. */
  const set = new Set();
  PAYLOAD.plume.forEach(r => set.add(r.month));
  (PAYLOAD.oni_calendar||[]).forEach(r => set.add(r.month));
  const sel = selectedAnalogs();
  PAYLOAD.analogs.filter(r => sel.includes(r.event)).forEach(r => r.month && set.add(r.month));
  return Array.from(set).sort();
}

function selectedAnalogs(){
  return Array.from($("#analogSel").selectedOptions).map(o => o.value);
}

function plumeTraces(){
  const p = palette();
  const plume = PAYLOAD.plume;
  const x = plume.map(r => r.month);
  const traces = [];

  traces.push({
    x: x.concat(x.slice().reverse()),
    y: plume.map(r=>r.p90).concat(plume.map(r=>r.p10).reverse()),
    fill:"toself", fillcolor: hexA(p.cat[1], THEME==="dark"?0.16:0.13),
    line:{width:0}, hoverinfo:"skip", showlegend:false, name:"80% interval"
  });

  /* Observed = the verified CPC ONI path, placed on the month each season is
     centred on. Not the reconstructed plume point that used to sit here. */
  const obs = (PAYLOAD.oni_calendar||[]).filter(r => r.month >= "2026-01");
  if (obs.length) traces.push({
    x: obs.map(r=>r.month), y: obs.map(r=>r.oni), mode:"lines+markers",
    name:"Observed ONI 2026", line:{color:p.cat[0], width:2},
    marker:{size:8, line:{width:2, color:p.surface}},
    text: obs.map(r=>r.season),
    hovertemplate:"Observed %{text}<br>%{y:.2f} \u00b0C<extra></extra>"
  });

  traces.push({
    x, y: plume.map(r=>r.median), mode:"lines", name:"Forecast median 2026\u201327",
    line:{color:p.cat[1], width:2},
    hovertemplate:"Forecast %{x}<br>median %{y:.2f} \u00b0C<extra></extra>"
  });

  const analogs = PAYLOAD.analogs;
  selectedAnalogs().forEach(event => {
    const rows = analogs.filter(r => r.event === event && r.month)
                        .sort((a,b) => a.step - b.step);
    if (!rows.length) return;
    traces.push({
      x: rows.map(r=>r.month), y: rows.map(r=>r.oni), mode:"lines", name:event,
      line:{color:p.cat[ANALOG_SLOT[event]], width:2, dash:"dot"},
      hovertemplate:event+" %{x}<br>%{y:.2f} \u00b0C<extra></extra>"
    });
  });
  return traces;
}

/* Direct end labels are the light-mode relief rule for the sub-3:1 categorical
   slots. Layout annotations rather than text traces so they can be pixel-
   staggered when two analogues share a value at the final step. */
function analogAnnotations(){
  const p = palette();
  /* Order the stagger by each series' final value so labels never cross the
     lines they name, and space them enough that two converging analogues
     cannot overprint each other. */
  const ends = selectedAnalogs().map(event => {
    const rows = PAYLOAD.analogs.filter(r => r.event === event && r.month)
                                .sort((a,b) => a.step - b.step);
    return rows.length ? {event, last: rows[rows.length - 1]} : null;
  }).filter(Boolean).sort((a,b) => b.last.oni - a.last.oni);

  return ends.map((e, i) => ({
    x: e.last.month, y: e.last.oni,
    text: e.event, showarrow:false, xanchor:"left", xshift:8,
    yshift: (i - (ends.length - 1) / 2) * -18,
    font:{color:p.ink2, size:11}
  }));
}

function drawPlume(){
  const p = palette();
  Plotly.newPlot("plume", plumeTraces(), {
    paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)",
    margin:{l:52,r:78,t:10,b:44},
    font:{family:'system-ui,-apple-system,"Segoe UI",sans-serif', color:p.ink2, size:12},
    xaxis:{showgrid:false, linecolor:p.axis, tickfont:{size:11}, ticks:"outside", tickcolor:p.axis},
    yaxis:{title:{text:"Ni\u00f1o 3.4 anomaly (\u00b0C)", font:{size:11}},
           gridcolor:p.grid, zerolinecolor:p.axis, linecolor:p.axis, tickfont:{size:11}},
    hovermode:"x unified",
    hoverlabel:{bgcolor:p.surface, bordercolor:p.grid, font:{color:p.ink}},
    showlegend:true,
    legend:{orientation:"h", y:-0.16, font:{size:11}},
    shapes:[{
      type:"line", xref:"paper", x0:0, x1:1,
      y0:S.record_to_beat_monthly, y1:S.record_to_beat_monthly,
      line:{color:p.muted, width:1, dash:"dash"}
    }],
    annotations:[...analogAnnotations(), {
      xref:"paper", x:0.012, y:S.record_to_beat_monthly, yanchor:"bottom",
      text:"2015\u201316 record "+S.record_to_beat_monthly+" \u00b0C",
      showarrow:false, font:{size:10.5, color:p.muted}, align:"left"
    }]
  }, {responsive:true, displayModeBar:false});

  $("#plumeCap").innerHTML =
    `Observed CPC ONI, the ${ENS.n_members}-member forecast median with its 80% interval, `
    + `and the three strongest events in the record \u2014 all on one <b>calendar</b> axis. `
    + `Historical events are placed on the month each ONI season is centred on `
    + `(DJF\u2009\u2192\u2009Jan \u2026 NDJ\u2009\u2192\u2009Dec) and anchored to `
    + `${PAYLOAD.anchor_year}, because El Ni\u00f1o is seasonally phase-locked: every one of `
    + `these events peaks in the Nov\u2013Jan window. What differs is the <b>ramp rate</b>, `
    + `not the timing.`;
  $("#plumeLegend").innerHTML =
    `<span><i class="dashed"></i>Dashed rule &mdash; the record to beat, not a series</span>`
    + `<span><i style="background:${hexA(palette().cat[1],0.3)}"></i>Shaded &mdash; 80% ensemble interval</span>`;
  $("#plumeProv").innerHTML = provLine("plume");
}

function plumeTableHTML(){
  const rows = PAYLOAD.plume.map(r =>
    `<tr><td>${esc(r.month)}</td><td>${fmt(r.median,2)}</td><td>${fmt(r.p10,2)}</td>`
    + `<td>${fmt(r.p90,2)}</td><td>${r.observed==null?"&mdash;":fmt(r.observed,2)}</td></tr>`).join("");
  return `<div class="tablewrap"><table><thead><tr><th>Month</th><th>Median</th>`
    + `<th>10th pct</th><th>90th pct</th><th>Observed</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/* ---------------- map ---------------- */
function layerScale(layerKey){
  const p = palette();
  const div = p.div || {};
  const diverging = ["precip_djf","precip_mam","temp_djf","yield_index","fisheries"].includes(layerKey);
  if (diverging){
    const neg = div.negative || [], pos = div.positive || [], mid = div.mid || "#eeeeee";
    const invert = layerKey === "temp_djf";
    const lowArm = invert ? pos.slice().reverse() : neg.slice().reverse();
    const highArm = invert ? neg : pos;
    const stops = [];
    lowArm.forEach((c,i) => stops.push([i/(2*lowArm.length), c]));
    stops.push([0.5, mid]);
    highArm.forEach((c,i) => stops.push([0.5 + (i+1)/(2*highArm.length), c]));
    return {colorscale: stops, diverging:true};
  }
  const ramp = (p.seq.orange || []);
  return {colorscale: ramp.map((c,i) => [i/(ramp.length-1), c]), diverging:false};
}

function drawMap(){
  const p = palette();
  const layerKey = $("#layerSel").value;
  const layer = PAYLOAD.layers.find(l => l.key === layerKey) || PAYLOAD.layers[0];
  const rows = PAYLOAD.countries.filter(r => r[layerKey] !== null && r[layerKey] !== undefined);
  const values = rows.map(r => r[layerKey]);
  const {colorscale, diverging} = layerScale(layerKey);

  const bound = Math.max(...values.map(Math.abs));
  const range = diverging ? {zmin:-bound, zmax:bound} : {zmin:0, zmax:Math.max(...values)};

  const pinned = rows.filter(r => r.pinned).map(r => r.iso3);

  Plotly.newPlot("map", [{
    type:"choropleth", locationmode:"ISO-3",
    locations: rows.map(r=>r.iso3), z: values,
    text: rows.map(r => r.name),
    customdata: rows.map(r => [r.confidence, r.group_label, (r.hazards||[]).slice(0,3).join(", ").replace(/_/g," ")]),
    colorscale, ...range,
    marker:{line:{color: pinned.length ? p.surface : p.surface, width:0.6}},
    colorbar:{
      title:{text: layer.unit, side:"top", font:{size:11}},
      thickness:11, len:0.72, outlinewidth:0, tickfont:{size:10.5, color:p.ink2}
    },
    hovertemplate:"<b>%{text}</b><br>"+layer.label+": %{z} "+layer.unit
      +"<br>Confidence %{customdata[0]}<br>%{customdata[1]}<br>Hazards: %{customdata[2]}<extra></extra>"
  }], {
    paper_bgcolor:"rgba(0,0,0,0)", margin:{l:0,r:0,t:0,b:0},
    font:{family:'system-ui,-apple-system,"Segoe UI",sans-serif', color:p.ink2},
    geo:{
      ...(window.PLOTLY_TOPOJSON_URL ? {} : {}),
      projection:{type:"natural earth"}, showframe:false, showcoastlines:false,
      bgcolor:"rgba(0,0,0,0)", showland:true,
      landcolor: THEME==="dark" ? "#232322" : "#f0efec",
      showcountries:true, countrycolor: p.grid, countrywidth:0.4
    },
    hoverlabel:{bgcolor:p.surface, bordercolor:p.grid, font:{color:p.ink}}
  }, {responsive:true, displayModeBar:false,
      ...(window.PLOTLY_TOPOJSON_URL ? {topojsonURL: window.PLOTLY_TOPOJSON_URL} : {})});

  $("#mapTitle").textContent = layer.label;
  $("#mapCap").innerHTML = diverging
    ? `Diverging scale with a neutral midpoint: the two poles read as opposite and zero reads as nothing. Symmetric about zero at &plusmn;${fmt(bound)} ${esc(layer.unit)}.`
    : `Sequential scale, one hue light to dark, because this layer encodes magnitude rather than polarity.`;
  $("#mapProv").innerHTML = provLine("map");

  document.getElementById("map").on("plotly_click", ev => {
    const iso = ev.points[0].location;
    showPin(PAYLOAD.countries.find(c => c.iso3 === iso));
  });

  const pins = PAYLOAD.countries.filter(c => c.pinned);
  if (!$("#pinCard").dataset.touched) renderPinned(pins);
}

function renderPinned(list){
  $("#pinCard").innerHTML = `<h2>Pinned regions</h2>`
    + `<p class="cap">Pinned by configuration (<code>pinned_by_default</code>). Click any country on the map to pin it here.</p>`
    + `<div class="regiongrid">${list.map(countryCardHTML).join("")}</div>`;
}
function showPin(c){
  if (!c) return;
  $("#pinCard").dataset.touched = "1";
  $("#pinCard").innerHTML = `<h2>${esc(c.name)}</h2>`
    + `<p class="cap">${esc(c.group_label)} &middot; click another country to change, or `
    + `<button type="button" id="resetPin" style="padding:2px 8px">reset to defaults</button></p>`
    + `<div class="regiongrid">${countryCardHTML(c)}</div>`;
  $("#resetPin").onclick = () => {
    $("#pinCard").dataset.touched = "";
    renderPinned(PAYLOAD.countries.filter(x => x.pinned));
  };
}

function countryCardHTML(c){
  const rows = [
    ["Precipitation, DJF 2026–27", sgn(c.precip_djf)+" %"],
    ["Precipitation, MAM 2027", sgn(c.precip_mam)+" %"],
    ["Temperature, DJF", sgn(c.temp_djf,2)+" °C"],
    ["Yield impact index", sgn(c.yield_index)+" %"],
    ["Fire / drought risk", fmt(c.fire_drought,0)+" / 100"],
    ["Price pressure", fmt(c.price_pressure,0)+" / 100"],
  ];
  if (c.fisheries !== null && c.fisheries !== undefined){
    const sp = c.fisheries_species ? ` (${String(c.fisheries_species).replace(/_/g," ")})` : "";
    rows.push([`Fisheries biomass${sp}`, sgn(c.fisheries)+" %"]);
    if (c.fisheries_landings !== null && c.fisheries_landings !== undefined){
      /* Identical to biomass when quota transmission is 1:1, which is what
         1997-98 showed (biomass -79%, catch -78%). Label it so the equality
         reads as a documented finding rather than a duplicated cell. */
      const same = Math.abs(c.fisheries_landings - c.fisheries) < 0.05;
      rows.push([`\u2192 landings after quota${same ? " (1:1 pass-through)" : ""}`,
                 sgn(c.fisheries_landings)+" %"]);
    }
  }
  rows.push(["Confidence", fmt(c.confidence,2)]);

  return `<div class="card"><h2>${esc(c.name)}</h2>`
    + `<p class="cap">${esc(c.group_label)} &middot; <span class="pill">${esc(c.provenance)}</span></p>`
    + rows.map(([k,v]) => `<div class="metricrow"><span>${k}</span><span>${v}</span></div>`).join("")
    + `<div class="hazards">${(c.hazards||[]).map(h=>`<span class="pill">${esc(titleCase(h))}</span>`).join("")}</div>`
    + (c.notes ? `<details><summary>Why</summary><p style="color:var(--ink-2);margin:6px 0 0">${glossify(esc(c.notes))}</p></details>` : "")
    + `</div>`;
}

/* ---------------- commodities ---------------- */
function commodityRows(){ return PAYLOAD.commodities[SCENARIO] || PAYLOAD.commodities.base || []; }

function drawCommodities(){
  const p = palette();
  const rows = commodityRows().slice().sort((a,b) => a.price_response_pct - b.price_response_pct);
  /* One series, one colour. The sign is carried by bar direction and by the
     zero rule -- not by a value ramp, which would double-encode length. */
  Plotly.newPlot("commChart", [{
    type:"bar", orientation:"h",
    x: rows.map(r=>r.price_response_pct), y: rows.map(r=>r.label),
    marker:{color:p.cat[1]},
    text: rows.map(r => sgn(r.price_response_pct)+"%"),
    textposition:"outside", textfont:{size:11, color:p.ink2}, cliponaxis:false,
    customdata: rows.map(r=>[r.production_shock_pct, r.coverage, r.elasticity, r.lag_months]),
    hovertemplate:"<b>%{y}</b><br>Price response %{x:.1f}%"
      +"<br>Global production shock %{customdata[0]:.2f}%"
      +"<br>Coverage %{customdata[1]:.0%} of world output"
      +"<br>Elasticity %{customdata[2]} &middot; lag %{customdata[3]} mo<extra></extra>"
  }], {
    paper_bgcolor:"rgba(0,0,0,0)", plot_bgcolor:"rgba(0,0,0,0)",
    margin:{l:132,r:56,t:8,b:38},
    font:{family:'system-ui,-apple-system,"Segoe UI",sans-serif', color:p.ink2, size:12},
    xaxis:{title:{text:"Modelled price response (%)", font:{size:11}},
           gridcolor:p.grid, zerolinecolor:p.axis, zerolinewidth:1, linecolor:p.axis, tickfont:{size:11}},
    yaxis:{linecolor:p.axis, tickfont:{size:11.5}, automargin:true},
    bargap:0.34, showlegend:false,
    hoverlabel:{bgcolor:p.surface, bordercolor:p.grid, font:{color:p.ink}}
  }, {responsive:true, displayModeBar:false});

  $("#commProv").innerHTML = provLine("commodities");
  renderTable("#commTable", commodityRows(), [
    {k:"label", h:"Commodity", t:"text"},
    {k:"exchange", h:"Venue", t:"text"},
    {k:"production_shock_pct", h:"Prod. shock %", t:"signed"},
    {k:"price_response_pct", h:"Price response %", t:"signed"},
    {k:"elasticity", h:"Elasticity", t:"num", d:2},
    {k:"stock_to_use", h:"Stock/use", t:"pct"},
    {k:"stock_modifier", h:"Stock mod.", t:"num", d:2},
    {k:"coverage", h:"Modelled", t:"pct"},
    {k:"listed_share", h:"Listed", t:"pct"},
    {k:"lag_months", h:"Lag (mo)", t:"num", d:0},
    {k:"top_contributors", h:"Top 3", t:"list"}
  ], "price_response_pct", "commodities");
}

/* ---------------- tables ---------------- */
/* Renders the definition block under a table. Any column present in the table
   but absent from config/columns.yaml is called out in red rather than quietly
   skipped -- so the note cannot drift behind the model without someone seeing. */
function columnNote(tableKey, cols){
  const spec = ((PAYLOAD.column_notes || {}).tables || {})[tableKey];
  if (!spec) return "";
  const defined = new Map((spec.columns || []).map(c => [c.key, c]));
  const shown = cols.map(c => c.k);
  const missing = shown.filter(k => !defined.has(k));

  const items = shown.filter(k => defined.has(k)).map(k => {
    const c = defined.get(k);
    return `<dt>${esc(c.label || k)}</dt><dd>${esc((c.definition||"").trim())}`
      + (c.detail ? `<span class="more">${esc(c.detail.trim())}</span>` : "")
      + `</dd>`;
  }).join("");

  return `<div class="colnote">`
    + `<h4>${esc(spec.title || "How to read this table")}</h4>`
    + (spec.method ? `<p class="method">${spec.method.trim()}</p>` : "")
    + `<dl>${items}</dl>`
    + (missing.length
        ? `<p class="missing">Undocumented column(s): ${esc(missing.join(", "))}</p>`
        : "")
    + (spec.caveat ? `<p class="caveat">${spec.caveat.trim()}</p>` : "")
    + `</div>`;
}

function renderTable(sel, rows, cols, sortKey, noteKey){
  const host = document.querySelector(sel);
  let dir = -1, key = sortKey || cols[0].k;

  function cell(r, c){
    const v = r[c.k];
    if (c.t === "signed"){
      const cls = v < 0 ? "num-neg" : (v > 0 ? "num-pos" : "");
      return `<td class="${cls}">${sgn(v)}</td>`;
    }
    if (c.t === "pct") return `<td>${v==null?"&mdash;":(v*100).toFixed(0)+"%"}</td>`;
    if (c.t === "num") return `<td>${v==null?"&mdash;":fmt(v, c.d ?? (Number.isInteger(v)?0:2))}</td>`;
    if (c.t === "list") return `<td>${esc((v||[]).join(", "))}</td>`;
    if (c.t === "pills") return `<td>${(v||[]).slice(0,3).map(h=>`<span class="pill">${esc(titleCase(h))}</span>`).join(" ")}</td>`;
    return `<td>${esc(v)}</td>`;
  }

  function draw(){
    const sorted = rows.slice().sort((a,b) => {
      const x = a[key], y = b[key];
      if (typeof x === "string") return dir * x.localeCompare(y);
      return dir * ((x ?? -1e12) - (y ?? -1e12));
    });
    host.innerHTML = `<table><thead><tr>`
      + cols.map(c => `<th data-k="${c.k}" ${c.k===key?`aria-sort="${dir<0?"descending":"ascending"}"`:""}>${c.h}</th>`).join("")
      + `</tr></thead><tbody>`
      + sorted.map(r => `<tr class="${r.pinned?"pinned":""}">` + cols.map(c => cell(r,c)).join("") + `</tr>`).join("")
      + `</tbody></table>`
      + (noteKey ? columnNote(noteKey, cols) : "");
    host.querySelectorAll("th").forEach(th => th.onclick = () => {
      const k = th.dataset.k;
      dir = (k === key) ? -dir : -1; key = k; draw();
    });
  }
  draw();
  return { filter(pred){ rows = rows.filter(pred); draw(); } };
}

function renderCountryTable(filterText){
  const q = (filterText||"").toLowerCase();
  const rows = PAYLOAD.countries.filter(c =>
    !q || c.name.toLowerCase().includes(q) || c.group_label.toLowerCase().includes(q)
    || (c.hazards||[]).join(" ").toLowerCase().includes(q));
  renderTable("#countryTable", rows, [
    {k:"name", h:"Region", t:"text"},
    {k:"group_label", h:"Group", t:"text"},
    {k:"precip_djf", h:"Precip DJF %", t:"signed"},
    {k:"precip_mam", h:"Precip MAM %", t:"signed"},
    {k:"temp_djf", h:"Temp DJF °C", t:"signed"},
    {k:"yield_index", h:"Yield index %", t:"signed"},
    {k:"fire_drought", h:"Fire/drought", t:"num", d:0},
    {k:"price_pressure", h:"Price pressure", t:"num", d:0},
    {k:"fisheries", h:"Fisheries %", t:"signed"},
    {k:"confidence", h:"Confidence", t:"num", d:2},
    {k:"hazards", h:"Hazards", t:"pills"}
  ], "yield_index", "countries");
}

function renderRegions(){
  const q = ($("#regionSearch").value||"").toLowerCase();
  const grp = $("#groupSel").value;
  const rows = PAYLOAD.countries.filter(c =>
    (grp === "__all__" || c.group === grp)
    && (!q || c.name.toLowerCase().includes(q) || (c.hazards||[]).join(" ").toLowerCase().includes(q)));
  $("#regions").innerHTML = rows.length
    ? rows.map(countryCardHTML).join("")
    : `<p class="cap">No regions match that filter.</p>`;
}

/* ---------------- method ---------------- */
function ageLabel(hours){
  if (hours === null || hours === undefined) return "unknown";
  const d = hours / 24;
  if (d < 1) return `${Math.round(hours)} h`;
  if (d < 60) return `${Math.round(d)} d`;
  return `${(d / 30.44).toFixed(1)} mo`;
}

function provLine(which){
  /* Two clocks, and they mean different things. Retrieval age says whether the
     pipeline ran; observation age says whether the data is current. An earlier
     build showed only the first, so a three-month-old value read as "0 h". */
  const obs = META.observation_age_h || {};
  const aged = Object.entries(obs).filter(([, v]) => v !== null && v !== undefined);
  const oldest = aged.sort((a, b) => b[1] - a[1])[0];

  let base = `Run <code>${esc(META.run_id)}</code> &middot; generated `
    + `${esc(String(META.generated_utc).slice(0, 19))}Z`;
  if (oldest){
    base += ` &middot; oldest <b>observation</b> <code>${esc(oldest[0])}</code>, `
      + `${ageLabel(oldest[1])} old`;
  }

  if (which === "plume")
    return base + ` &middot; observed ONI from ${esc(ENS.source || "CPC")}; plume reconstructed `
      + `from published ensemble summary statistics &mdash; see Method.`;
  if (which === "map")
    return base + ` &middot; layer values are <code>composite</code>: published teleconnection `
      + `composites scaled to forecast intensity, not dynamically downscaled output.`;
  if (which === "commodities")
    return base + ` &middot; scenario output. Cross-checked against the one attributable `
      + `El Ni&ntilde;o price response found (palm oil, +20&ndash;40% at 6-month lag).`;
  return base;
}

function fmtAnchor(v){
  if (Array.isArray(v)) return v.join(" \u2013 ");
  if (typeof v === "number") return v.toLocaleString();
  return String(v ?? "");
}

function renderMethod(){
  const u = PAYLOAD.unverified_assertions || {};
  const anchors = PAYLOAD.literature_anchors || {};
  const rows = Object.entries(u).filter(([k]) => !k.startsWith("_"));

  $("#methodCard").innerHTML = `
    <h2>Method, provenance and what this does not know</h2>
    <p class="cap">Everything on this dashboard traces to one of three tiers. The tier is printed next to every number.</p>

    <h3>Provenance tiers</h3>
    <p><b>verified</b> &mdash; read from the cited primary source on the retrieval date.
    <b>cached</b> &mdash; a primary-source value held from an earlier read, refreshed verbatim on the next live run.
    <b>composite / modelled</b> &mdash; derived by this system from published composites and elasticities. Indicative, never an observation.</p>

    <h3>ONI convention</h3>
    <p>Three-month running mean of the Ni&ntilde;o 3.4 anomaly against CPC's era-specific 30-year climatology &mdash; the same convention as the source forecast, so peak intensities compare directly with 1982&ndash;83 (${S.record_to_beat_oni === 2.8 ? "2.2" : "2.2"}&nbsp;&deg;C), 1997&ndash;98 (2.4&nbsp;&deg;C) and 2015&ndash;16 (${S.record_to_beat_oni}&nbsp;&deg;C). Impact layers use a fixed 1991&ndash;2020 base so they stay comparable across regions.</p>

    <h3>The extrapolation problem</h3>
    <p>No El Ni&ntilde;o of the forecast amplitude has ever been observed. Teleconnection composites are calibrated on events near 2.4&nbsp;&deg;C, and this system scales them by <code>(intensity / 2.4) ** 0.85</code> &mdash; sub-linear, because responses saturate. Above ${fmt(2.8)}&nbsp;&deg;C every impact layer carries a ${Math.round((1-S.confidence_multiplier)*100)}% confidence penalty. That penalty is already reflected in every confidence figure shown. The honest summary: the <em>sign</em> of these responses is well established, the <em>magnitude</em> at this amplitude is not.</p>

    <h3>Attributable anchors</h3>
    <div class="srcgrid">${Object.entries(anchors).filter(([k])=>!k.startsWith("_")).map(([k,v]) =>
      `<div class="srcitem"><b>${esc(titleCase(k))}</b>${esc(fmtAnchor(v.value ?? v.value_pct))}
       ${v.url?`<br><a href="${esc(v.url)}" target="_blank" rel="noopener">source</a>`:""}</div>`).join("")}</div>

    <h3>Figures this system could not verify</h3>
    <p>Supplied in the commissioning brief but not traceable to a primary source in the research pass. They are excluded from every headline number and appear only inside labelled scenario bands. Attribution is re-attempted on each run.</p>
    <div class="tablewrap wrap"><table><thead><tr><th>Assertion</th><th>Value</th><th>Status</th></tr></thead><tbody>
    ${rows.map(([k,v]) => `<tr><td>${esc(k)}</td><td>${esc(fmtAnchor(v.value))}</td>
      <td>${esc(v.status)}${v.conflict?`<br><span style="color:var(--critical)">${esc(v.conflict)}</span>`:""}
      ${v.note?`<br><span style="color:var(--ink-muted)">${esc(v.note)}</span>`:""}</td></tr>`).join("")}
    </tbody></table></div>

    <h3>Colour</h3>
    <p>The categorical order was machine-validated in both light and dark modes for colourblind separation, chroma and contrast &mdash; not chosen by eye. Three light-mode slots sit below 3:1 contrast, so every series is also directly end-labelled and every chart has a table view. Diverging layers use two opposite-temperature poles with a neutral gray midpoint; magnitude layers use a single hue, light to dark.</p>

    <h3>Glossary</h3>
    <p>Every acronym used anywhere in this dashboard. Terms in captions and
    alert text carry a dotted underline &mdash; hover or focus them for the
    expansion without coming here.</p>
    <div class="gloss">${(() => {
      const g = PAYLOAD.glossary || {};
      const terms = g.terms || {};
      const labels = g.group_labels || {};
      const byGroup = {};
      Object.entries(terms).forEach(([k, v]) => {
        const grp = v.group || "other";
        (byGroup[grp] = byGroup[grp] || []).push([k, v]);
      });
      return Object.keys(labels).concat(
        Object.keys(byGroup).filter(k => !(k in labels))
      ).filter(g2 => byGroup[g2]).map(grp =>
        `<dl><h4>${esc(labels[grp] || titleCase(grp))}</h4>`
        + byGroup[grp].map(([k, v]) =>
            `<dt>${esc(k)}</dt><dd>${esc(v.full)}</dd>`
            + (v.note ? `<dd class="n">${esc(v.note.trim())}</dd>` : "")
          ).join("")
        + `</dl>`
      ).join("");
    })()}</div>

    <h3>Data age</h3>
    <p>Two clocks. <b>Retrieved</b> is how long since the pipeline last spoke to
    the source &mdash; it detects a dead scheduler. <b>Observed</b> is how old the
    underlying measurement is &mdash; it detects a source that answers on time
    with months-old values. Only the second one tells you whether what you are
    reading is current.</p>
    <div class="tablewrap"><table><thead><tr><th>Dataset</th>
      <th>Observation age</th><th>Retrieval age</th></tr></thead><tbody>
    ${Object.keys(META.observation_age_h || {}).map(k => {
      const obs = (META.observation_age_h||{})[k];
      const ret = (META.data_latency_h||{})[k];
      return `<tr><td>${esc(k)}</td>`
        + `<td>${obs===null||obs===undefined ? "reference \u2014 does not age" : esc(ageLabel(obs))}</td>`
        + `<td>${ret===undefined ? "&mdash;" : esc(ageLabel(ret))}</td></tr>`;
    }).join("")}
    </tbody></table></div>

    <h3>Run log</h3>
    <p>${(PAYLOAD.notes||[]).length} pipeline notes, ${(PAYLOAD.qc_flags||[]).length} QC flags this run.</p>
    <details><summary>Show run notes</summary><ul style="color:var(--ink-2);font-size:.8rem">
      ${(PAYLOAD.qc_flags||[]).map(f=>`<li>QC: ${esc(f)}</li>`).join("")}
      ${(PAYLOAD.notes||[]).map(n=>`<li>${esc(n)}</li>`).join("")}
    </ul></details>`;
}

/* ---------------- export ---------------- */
function toCSV(rows){
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  const cell = v => Array.isArray(v) ? `"${v.join("; ")}"`
    : (typeof v === "string" && /[",\n]/.test(v)) ? `"${v.replace(/"/g,'""')}"` : (v ?? "");
  return [cols.join(","), ...rows.map(r => cols.map(c => cell(r[c])).join(","))].join("\n");
}
function download(name, text, type){
  const url = URL.createObjectURL(new Blob([text], {type}));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}

/* ---------------- wiring ---------------- */
const TABS = [
  ["overview","Overview"], ["map","Global map"], ["regions","Regional deep-dive"],
  ["commodities","Commodities"], ["table","Data table"], ["method","Method &amp; provenance"]
];

function hexA(hex, a){
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!m) return hex;
  return `rgba(${parseInt(m[1],16)},${parseInt(m[2],16)},${parseInt(m[3],16)},${a})`;
}

function selectTab(id){
  document.querySelectorAll(".panel").forEach(p => p.classList.toggle("on", p.id === "panel-"+id));
  document.querySelectorAll("#tabs button").forEach(b =>
    b.setAttribute("aria-selected", String(b.dataset.tab === id)));
  const hash = new URLSearchParams(location.hash.slice(1));
  hash.set("tab", id); hash.set("as_of", S.as_of);
  history.replaceState(null, "", "#" + hash.toString());
  if (id === "map") Plotly.Plots.resize("map");
  if (id === "overview") Plotly.Plots.resize("plume");
  if (id === "commodities") Plotly.Plots.resize("commChart");
}

function renderAll(){
  renderHeader(); renderAlerts(); renderGauge(); drawPlume(); drawMap();
  drawCommodities(); renderRegions(); renderCountryTable($("#tableSearch").value); renderMethod();
  glossifyDom(document.querySelector(".wrap"));
}

function init(){
  $("#tabs").innerHTML = TABS.map(([id,label],i) =>
    `<button role="tab" data-tab="${id}" aria-selected="${i===0}">${label}</button>`).join("");
  $("#tabs").onclick = e => { if (e.target.dataset.tab) selectTab(e.target.dataset.tab); };

  $("#layerSel").innerHTML = PAYLOAD.layers.map(l =>
    `<option value="${l.key}">${esc(l.label)}</option>`).join("");
  const scenarioOpts = Object.keys(PAYLOAD.commodities).map(k =>
    `<option value="${k}"${k==="base"?" selected":""}>${esc(titleCase(k))}</option>`).join("");
  $("#scenSel").innerHTML = scenarioOpts;
  $("#commScenSel").innerHTML = scenarioOpts;
  $("#groupSel").innerHTML = `<option value="__all__">All groups</option>`
    + Object.entries(PAYLOAD.groups).map(([k,v]) => `<option value="${k}">${esc(v.label)}</option>`).join("");

  $("#themeBtn").onclick = () => setTheme(THEME === "dark" ? "light" : "dark");
  $("#shareBtn").onclick = async () => {
    const url = location.href.split("#")[0] + "#tab=" + (document.querySelector('[aria-selected="true"]')?.dataset.tab||"overview")
      + "&as_of=" + S.as_of + "&run=" + META.run_id;
    try { await navigator.clipboard.writeText(url); $("#shareBtn").textContent = "Copied"; }
    catch { prompt("Snapshot link", url); }
    setTimeout(() => $("#shareBtn").textContent = "Copy snapshot link", 1800);
  };
  $("#updateBtn").onclick = () => alert(
    "This is the static export. Re-run Steps A-F with:\n\n  enso-tracker all\n\n"
    + "or POST to /api/rerun on the served app. The exported file is a snapshot of run "
    + META.run_id + ".");

  $("#analogSel").onchange = drawPlume;
  $("#layerSel").onchange = drawMap;
  $("#scenSel").onchange = e => { SCENARIO = e.target.value; $("#commScenSel").value = SCENARIO; drawCommodities(); };
  $("#commScenSel").onchange = e => { SCENARIO = e.target.value; $("#scenSel").value = SCENARIO; drawCommodities(); };
  const reglossPanel = (fn, sel) => (...args) => {
    fn(...args);
    glossifyDom(document.querySelector(sel));
  };
  $("#regionSearch").oninput = reglossPanel(renderRegions, "#panel-regions");
  $("#groupSel").onchange = reglossPanel(renderRegions, "#panel-regions");
  $("#tableSearch").oninput = reglossPanel(
    e => renderCountryTable(e.target.value), "#panel-table");

  $("#plumeTableBtn").onclick = e => {
    const on = e.target.getAttribute("aria-pressed") === "true";
    e.target.setAttribute("aria-pressed", String(!on));
    $("#plumeTable").hidden = on;
    $("#plumeTable").innerHTML = on ? "" : plumeTableHTML();
  };
  $("#mapTableBtn").onclick = e => {
    const on = e.target.getAttribute("aria-pressed") === "true";
    e.target.setAttribute("aria-pressed", String(!on));
    $("#mapTable").hidden = on;
    if (!on) renderTable("#mapTable", PAYLOAD.countries, [
      {k:"name",h:"Region",t:"text"},
      {k:$("#layerSel").value, h:$("#layerSel").selectedOptions[0].text, t:"signed"},
      {k:"confidence",h:"Confidence",t:"num",d:2}
    ], $("#layerSel").value);
  };

  document.querySelectorAll("[data-export]").forEach(btn => btn.onclick = () => {
    const which = btn.dataset.export;
    const rows = which === "commodities" ? commodityRows() : PAYLOAD.countries;
    const stamp = S.as_of;
    if (btn.dataset.fmt === "csv") download(`elnino_${which}_${stamp}.csv`, toCSV(rows), "text/csv");
    else download(`elnino_${which}_${stamp}.json`, JSON.stringify(rows, null, 2), "application/json");
  });

  const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  setTheme(prefersDark ? "dark" : "light");

  const hash = new URLSearchParams(location.hash.slice(1));
  selectTab(hash.get("tab") || "overview");
  window.addEventListener("resize", () => {
    ["plume","map","commChart"].forEach(id => { try { Plotly.Plots.resize(id); } catch(e){} });
  });
}
init();
</script>
</body>
</html>
"""
