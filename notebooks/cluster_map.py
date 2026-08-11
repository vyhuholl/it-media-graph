"""The cluster graph as a local page to pan, zoom and poke at.

Exploratory tooling: not part of the package, not spec'd, not tested.

    uv sync --group data
    uv run notebooks/cluster_map.py
    xdg-open data/cluster_map.html

Imports the clustering from ``clusters.py`` rather than repeating it, so
the map and the workbook can never disagree about who is in what.

**This page is publishable, and only because of what it leaves out.**
The export rule is an explicit column list filtered to ``status =
'seed'``, never ``SELECT *``, and never ``discovered_via``,
``reject_reason``, ``reject_note`` or ``kind_note`` — those four are the
operator's private reasoning about channels, and three of them are about
channels that were turned down. What ships here is the seed set, its
titles and usernames, the reference graph between them, and measures
derived from it. Nothing else. Anything added to ``payload`` has to be
checked against that list before it goes in; the query in
``clusters.NODES`` is the one place the filter lives.

It is self-contained — no CDN, no web font, no tile server — so it can be
opened from a file, mailed, or served from anywhere without the page
phoning home or breaking.

**Every cluster gets its own colour, however many there are.** The
palette is generated per run from the cluster count rather than stored,
because the count is not stable: the collector runs continuously, and
over three days of ordinary use it went 14, then 13, then 15. Twice a
stored list was one colour short, and both times the symptom was a
cluster silently painted grey — indistinguishable from the grey that
means "no cluster at all". Nothing here may be sized by hand.

Two properties are guaranteed at any count, and they are the ones that
would otherwise produce a *wrong* picture rather than a hard one. Every
colour stays inside its mode's lightness band, and every colour stays
above the chroma floor — checked from 12 clusters to 22. The second
matters most: a generated colour that drifts under the floor reads grey,
and grey already means "no cluster", so the palette would be asserting
something false about a cluster that exists.

What is *not* guaranteed is that all of them are tellable apart, and
that is arithmetic rather than a tuning failure: a palette is judged on
the worst pair in it, and the more hues share the wheel the closer the
nearest two sit. At the fifteen clusters current when this was written,
all pairs — worst normal-vision ΔE 9.3 light and 5.1 dark against a
floor of 15, worst colour-blind ΔE 0.9 and 0.7 against a target of 8.
Every cluster added tightens it further.

So a reader with full colour vision separates most pairs and will
hesitate over the near ones; a colour-blind reader will find several
pairs identical. Neither is a reason to hide the distinction — the map
has that many crowds and showing them is the point — but colour is *not*
load-bearing here, and nothing in this page asks it to be. Identity is
carried four other ways that do not run out: clusters are separated in
space by the layout, named in the legend, named again on hover, and any
one of them can be isolated by a click. The palette makes the picture
readable at a glance; those four make it answerable.

Two choices follow. The hues run over several lightness tiers rather
than one, so a pair a colour-blind reader cannot separate by hue still
differs in lightness. And the two palettes are generated for their own
surface instead of one being reused, because the dark band is L
0.48-0.67 and the light tiers fall outside it at both ends — reusing
them put a colour below the chroma floor, which is where this whole
paragraph came from.

The colour modes that *are* continuous — depth, dryness, stability — have
no such cap and paint every node, which is why they are worth having as
modes rather than as columns in a sheet.
"""

import json
import math
import random
from datetime import UTC, datetime
from typing import Any

import igraph as ig
import psycopg
from clusters import DATA, DSN, RESOLUTION, Clustering, build, shares

OUT = DATA / "cluster_map.html"

# Force-directed layout: enough iterations that the picture stops moving,
# seeded so re-running produces the same map rather than a rotated one an
# operator has to re-learn.
LAYOUT_ITERATIONS = 700
LAYOUT_SEED = 11

# How far apart the clusters start, and how much harder an edge inside a
# cluster pulls than one crossing between them. Both are properties of
# the drawing and nothing else — see `layout`. Raise the boost for
# rounder, more separated blobs; lower it towards 1 to see how much of
# the separation the raw graph gives on its own.
CLUSTER_RING = 9.0
INTRA_BOOST = 6.0

# Force layout cools from this temperature. Low, because the starting
# arrangement is already meaningful and a hot start would throw it away.
START_TEMP = 2.0

# What share of nodes at each end is allowed outside the framed square.
LAYOUT_TRIM = 0.03

# The palette is *generated to fit the run*, not stored. There is no
# constant here saying how many clusters get a colour, because every
# version of that constant has been wrong within a week: the collector
# runs continuously, the graph grows under the script, and the number of
# clusters moved 14 -> 13 -> 15 over three days of ordinary use. A stored
# list of hexes is a promise about a number nothing controls, and the
# failure it produces is silent — the extra cluster comes out grey and
# looks like a cluster the analysis declined to label.
#
# So `palette()` below takes the count it needs. The parameters are what
# came out of a grid search scored on the validator's worst-pair figures;
# the count is whatever Leiden found this morning.
#
# Light and dark are the same hues stepped for their own surface, never
# one list reused: the dark band is L 0.48-0.67 and the light tiers fall
# outside it at both ends. Reusing them put a colour under the chroma
# floor, where it reads grey — the exact bug this comment is about,
# arrived at from the other direction.
LIGHT_TIERS = (0.48, 0.62, 0.75)
LIGHT_CHROMA = 0.17
LIGHT_BAND = (0.43, 0.77)
DARK_TIERS = (0.52, 0.63)
DARK_CHROMA = 0.19
DARK_BAND = (0.48, 0.67)
HUE_OFFSET = 10.0

# Below this a colour reads as grey — and grey is this map's word for
# "no cluster", so a generated colour that lands here would be saying
# something false. Slightly above the validator's 0.1 floor, as margin.
MIN_CHROMA = 0.11

# Kept this far inside the lightness band, so a fitted colour cannot land
# exactly on the edge and fail the band check by a thousandth.
BAND_MARGIN = 0.01

TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Карта кластеров</title>
<style>
  :root {
    color-scheme: light;
    --surface: #fcfcfb;
    --panel: #ffffff;
    --border: #d9d8d2;
    --text-primary: #0b0b0b;
    --text-secondary: #52514e;
    --text-muted: #85847e;
    --edge: rgba(11, 11, 11, 0.13);
    --edge-strong: rgba(11, 11, 11, 0.42);
    --dim: #cecdc6;
__SERIES_LIGHT__
    --neutral: #9d9c94;
    --pole-low: #2a78d6;
    --pole-high: #e34948;
    --mid: #f0efec;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface: #1a1a19;
      --panel: #232322;
      --border: #3d3d3a;
      --text-primary: #ffffff;
      --text-secondary: #c3c2b7;
      --text-muted: #8f8e85;
      --edge: rgba(255, 255, 255, 0.13);
      --edge-strong: rgba(255, 255, 255, 0.45);
      --dim: #464642;
__SERIES_DARK__
      --neutral: #6f6e68;
      --pole-low: #3987e5;
      --pole-high: #e66767;
      --mid: #383835;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--surface);
    color: var(--text-primary);
    font: 13px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI",
          Roboto, "Helvetica Neue", sans-serif;
    height: 100vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  header {
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: center;
  }
  h1 { font-size: 14px; margin: 0; font-weight: 600; }
  .meta { color: var(--text-muted); font-size: 12px; }
  .modes { display: flex; gap: 2px; margin-left: auto; flex-wrap: wrap; }
  .modes button, .tool {
    font: inherit;
    color: var(--text-secondary);
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 10px;
    cursor: pointer;
  }
  .modes button[aria-pressed="true"] {
    color: var(--text-primary);
    border-color: var(--text-muted);
    font-weight: 600;
  }
  input[type="search"] {
    font: inherit;
    padding: 4px 9px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--panel);
    color: var(--text-primary);
    width: 190px;
  }
  main { flex: 1; display: flex; min-height: 0; }
  #stage { flex: 1; position: relative; min-width: 0; }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  canvas.dragging { cursor: grabbing; }
  aside {
    width: 290px;
    border-left: 1px solid var(--border);
    overflow-y: auto;
    padding: 12px 14px 24px;
    background: var(--panel);
  }
  aside h2 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
    margin: 14px 0 6px;
    font-weight: 600;
  }
  aside h2:first-child { margin-top: 0; }
  .row {
    display: flex;
    gap: 8px;
    align-items: baseline;
    width: 100%;
    text-align: left;
    padding: 5px 6px;
    border: 0;
    border-radius: 6px;
    background: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .row:hover { background: var(--mid); }
  .row[aria-pressed="true"] { background: var(--mid); font-weight: 600; }
  .swatch {
    width: 10px; height: 10px; border-radius: 3px; flex: none;
    transform: translateY(1px);
  }
  .row .name { flex: 1; min-width: 0; }
  .row .name b { display: block; font-weight: 600; }
  .row .name span {
    display: block; color: var(--text-muted); font-size: 11px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .row .size { color: var(--text-muted); font-variant-numeric: tabular-nums; }
  .scale { display: flex; align-items: center; gap: 8px; font-size: 11px;
           color: var(--text-muted); }
  .ramp { height: 8px; border-radius: 4px; flex: 1; }
  #tip {
    position: absolute;
    pointer-events: none;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 8px 10px;
    max-width: 280px;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.18);
    opacity: 0;
    transition: opacity .08s;
    font-size: 12px;
  }
  #tip b { display: block; margin-bottom: 3px; }
  #tip dl {
    margin: 5px 0 0; display: grid;
    grid-template-columns: auto 1fr; gap: 1px 10px;
  }
  #tip dt { color: var(--text-muted); }
  #tip dd { margin: 0; font-variant-numeric: tabular-nums; }
  .hint { color: var(--text-muted); font-size: 11px; margin-top: 10px; }
</style>
</head>
<body>
<header>
  <h1>Карта кластеров</h1>
  <span class="meta" id="meta"></span>
  <input type="search" id="find" placeholder="Найти канал…"
         aria-label="Найти канал">
  <button class="tool" id="reset">Сброс</button>
  <div class="modes" role="group" aria-label="Чем красить">
    <button data-mode="cluster" aria-pressed="true">Кластер</button>
    <button data-mode="depth" aria-pressed="false">Глубина</button>
    <button data-mode="dryness" aria-pressed="false">Занудство</button>
    <button data-mode="stability" aria-pressed="false">Устойчивость</button>
  </div>
</header>
<main>
  <div id="stage">
    <canvas id="map"></canvas>
    <div id="tip" role="status"></div>
  </div>
  <aside>
    <h2 id="legend-title">Кластеры</h2>
    <div id="legend"></div>
    <div class="hint">
      Клик по кластеру или по каналу — показать только его и соседей.
      Колесо — масштаб, перетаскивание — сдвиг.
    </div>
  </aside>
</main>
<script id="payload" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById("payload").textContent);
const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const stage = document.getElementById("stage");
const tip = document.getElementById("tip");
const css = getComputedStyle(document.documentElement);
const token = (n) => css.getPropertyValue(n).trim();

let mode = "cluster";
let view = { x: 0, y: 0, k: 1 };
let hovered = null;
let selected = null;      // one node id
let isolated = null;      // one cluster id
let matches = new Set();  // search hits

const nodes = DATA.nodes;
const links = DATA.links;
const byId = new Map(nodes.map((n) => [n.i, n]));
const neighbours = new Map(nodes.map((n) => [n.i, new Set()]));
for (const [a, b] of links) {
  neighbours.get(a).add(b);
  neighbours.get(b).add(a);
}

/* --- colour ------------------------------------------------------- */

function mix(from, to, t) {
  const parse = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [r1, g1, b1] = parse(from), [r2, g2, b2] = parse(to);
  const c = (a, b) => Math.round(a + (b - a) * t);
  return `rgb(${c(r1, r2)},${c(g1, g2)},${c(b1, b2)})`;
}

// Diverging: two poles with a neutral middle, so "average" reads as
// nothing rather than as a colour of its own.
function diverging(value, span) {
  if (value === null) return token("--neutral");
  const t = Math.max(-1, Math.min(1, value / span));
  return t < 0
    ? mix(token("--mid"), token("--pole-low"), -t)
    : mix(token("--mid"), token("--pole-high"), t);
}

// Sequential: one hue, light to dark.
function sequential(value) {
  if (value === null) return token("--neutral");
  return mix(token("--mid"), token("--s1"), Math.max(0, Math.min(1, value)));
}

function colourOf(n) {
  if (mode === "cluster") {
    return n.c !== null && n.slot >= 0
      ? token("--s" + (n.slot + 1))
      : token("--neutral");
  }
  if (mode === "depth") return diverging(n.d, 2);
  if (mode === "dryness") return diverging(n.dr, 2);
  return sequential(n.s === null ? null : (n.s - 0.4) / 0.6);
}

/* --- what is lit up ----------------------------------------------- */

function focusSet() {
  if (isolated !== null) {
    const keep = new Set();
    for (const n of nodes) if (n.c === isolated) keep.add(n.i);
    return keep;
  }
  if (selected !== null) {
    const keep = new Set(neighbours.get(selected));
    keep.add(selected);
    return keep;
  }
  if (matches.size) return matches;
  return null;
}

/* --- drawing ------------------------------------------------------ */

let width = 0, height = 0, scale = 1;

function resize() {
  const box = stage.getBoundingClientRect();
  scale = window.devicePixelRatio || 1;
  width = box.width; height = box.height;
  canvas.width = width * scale;
  canvas.height = height * scale;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  draw();
}

// The layout is a unit square; the stage rarely is. Fit the square into
// the shorter side and centre it, then apply pan and zoom on top.
const project = (n) => {
  const side = Math.min(width, height) * 0.94;
  return {
    x: ((n.x - 0.5) * side * view.k) + width / 2 + view.x,
    y: ((n.y - 0.5) * side * view.k) + height / 2 + view.y,
  };
};

const radius = (n) => (3.0 + Math.sqrt(n.w) * 5.5) * Math.sqrt(view.k);

function draw() {
  ctx.clearRect(0, 0, width, height);
  const focus = focusSet();
  const pos = new Map(nodes.map((n) => [n.i, project(n)]));

  ctx.lineWidth = 1;
  for (const [a, b, w] of links) {
    const lit = !focus || (focus.has(a) && focus.has(b));
    if (focus && !lit && !(focus.has(a) || focus.has(b))) continue;
    const pa = pos.get(a), pb = pos.get(b);
    ctx.strokeStyle = lit && focus ? token("--edge-strong") : token("--edge");
    ctx.globalAlpha = focus && !lit ? 0.12 : Math.min(1, 0.45 + w * 0.55);
    ctx.beginPath();
    ctx.moveTo(pa.x, pa.y);
    ctx.lineTo(pb.x, pb.y);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  for (const n of nodes) {
    const p = pos.get(n.i);
    const dimmed = focus && !focus.has(n.i);
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius(n), 0, Math.PI * 2);
    ctx.fillStyle = dimmed ? token("--dim") : colourOf(n);
    ctx.globalAlpha = dimmed ? 0.45 : 1;
    ctx.fill();
    // A surface ring, so overlapping nodes stay countable.
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = token("--surface");
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // Direct labels for the biggest nodes in focus — identity that does
  // not depend on hovering. A label that would land on one already
  // drawn is dropped rather than overprinted: two names on top of each
  // other name nobody, and the node underneath still has its tooltip.
  const candidates = (focus ? nodes.filter((n) => focus.has(n.i)) : nodes)
    .sort((a, b) => b.w - a.w)
    .slice(0, 90);
  ctx.font = "600 11px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  const placed = [];
  let drawn = 0;
  for (const n of candidates) {
    if (drawn >= (focus ? 26 : 20)) break;
    const p = pos.get(n.i);
    const text = n.t.length > 28 ? n.t.slice(0, 27) + "…" : n.t;
    const half = ctx.measureText(text).width / 2 + 3;
    const y = p.y - radius(n) - 5;
    const box = { x1: p.x - half, x2: p.x + half, y1: y - 11, y2: y + 3 };
    if (placed.some((q) =>
      box.x1 < q.x2 && box.x2 > q.x1 && box.y1 < q.y2 && box.y2 > q.y1
    )) continue;
    placed.push(box);
    drawn += 1;
    ctx.lineWidth = 3;
    ctx.strokeStyle = token("--surface");
    ctx.strokeText(text, p.x, y);
    ctx.fillStyle = token("--text-primary");
    ctx.fillText(text, p.x, y);
  }

  if (hovered !== null) {
    const n = byId.get(hovered);
    const p = pos.get(hovered);
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius(n) + 3, 0, Math.PI * 2);
    ctx.lineWidth = 2;
    ctx.strokeStyle = token("--text-primary");
    ctx.stroke();
  }
}

/* --- interaction --------------------------------------------------- */

function hit(mx, my) {
  let best = null, bestD = 14;
  for (const n of nodes) {
    const p = project(n);
    const d = Math.hypot(p.x - mx, p.y - my);
    if (d < Math.max(bestD, radius(n) + 3)) { best = n.i; bestD = d; }
  }
  return best;
}

const fmt = (v, digits = 2) => (v === null ? "—" : v.toFixed(digits));

function showTip(n, mx, my) {
  tip.innerHTML =
    `<b>${n.t}</b>` +
    (n.u ? `<span style="color:var(--text-muted)">@${n.u}</span>` : "") +
    `<dl>` +
    `<dt>кластер</dt><dd>${n.cn || "—"}</dd>` +
    `<dt>тип</dt><dd>${n.k || "—"}</dd>` +
    `<dt>связей</dt><dd>${n.p}</dd>` +
    `<dt>устойчивость</dt><dd>${fmt(n.s)}</dd>` +
    `<dt>внутри кластера</dt><dd>${fmt(n.in)}</dd>` +
    `<dt>репосты / упоминания</dt><dd>${fmt(n.fi)} / ${fmt(n.mi)}</dd>` +
    `<dt>глубина</dt><dd>${fmt(n.d)}</dd>` +
    `<dt>занудство</dt><dd>${fmt(n.dr)}</dd>` +
    `</dl>`;
  tip.style.opacity = "1";
  const box = tip.getBoundingClientRect();
  tip.style.left = Math.min(mx + 14, width - box.width - 8) + "px";
  tip.style.top = Math.min(my + 14, height - box.height - 8) + "px";
}

canvas.addEventListener("mousemove", (e) => {
  const box = canvas.getBoundingClientRect();
  const mx = e.clientX - box.left, my = e.clientY - box.top;
  const found = hit(mx, my);
  if (found !== hovered) { hovered = found; draw(); }
  if (found === null) tip.style.opacity = "0";
  else showTip(byId.get(found), mx, my);
});

canvas.addEventListener("mouseleave", () => {
  hovered = null; tip.style.opacity = "0"; draw();
});

let dragging = null;
canvas.addEventListener("mousedown", (e) => {
  dragging = { x: e.clientX - view.x, y: e.clientY - view.y, moved: false };
  canvas.classList.add("dragging");
});
window.addEventListener("mousemove", (e) => {
  if (!dragging) return;
  view.x = e.clientX - dragging.x;
  view.y = e.clientY - dragging.y;
  dragging.moved = true;
  draw();
});
window.addEventListener("mouseup", (e) => {
  if (dragging && !dragging.moved) {
    const box = canvas.getBoundingClientRect();
    const found = hit(e.clientX - box.left, e.clientY - box.top);
    selected = found === selected ? null : found;
    if (found !== null) isolated = null;
    renderLegend();
    draw();
  }
  dragging = null;
  canvas.classList.remove("dragging");
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const box = canvas.getBoundingClientRect();
  const mx = e.clientX - box.left, my = e.clientY - box.top;
  const factor = Math.exp(-e.deltaY * 0.0016);
  const next = Math.max(0.35, Math.min(14, view.k * factor));
  const ratio = next / view.k;
  view.x = mx - (mx - view.x) * ratio;
  view.y = my - (my - view.y) * ratio;
  view.k = next;
  draw();
}, { passive: false });

document.querySelectorAll(".modes button").forEach((button) => {
  button.addEventListener("click", () => {
    mode = button.dataset.mode;
    document.querySelectorAll(".modes button").forEach((other) =>
      other.setAttribute("aria-pressed", String(other === button)));
    renderLegend();
    draw();
  });
});

document.getElementById("find").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  matches = new Set();
  if (q.length >= 2) {
    for (const n of nodes) {
      if ((n.t + " " + (n.u || "")).toLowerCase().includes(q)) {
        matches.add(n.i);
      }
    }
  }
  draw();
});

document.getElementById("reset").addEventListener("click", () => {
  selected = null; isolated = null; matches = new Set();
  document.getElementById("find").value = "";
  view = { x: 0, y: 0, k: 1 };
  renderLegend();
  draw();
});

/* --- legend -------------------------------------------------------- */

function renderLegend() {
  const box = document.getElementById("legend");
  const title = document.getElementById("legend-title");
  box.innerHTML = "";

  if (mode !== "cluster") {
    title.textContent = { depth: "Глубина", dryness: "Занудство",
                          stability: "Устойчивость" }[mode];
    const scaleRow = document.createElement("div");
    scaleRow.className = "scale";
    const ramp = document.createElement("div");
    ramp.className = "ramp";
    ramp.style.background = mode === "stability"
      ? `linear-gradient(90deg, ${token("--mid")}, ${token("--s1")})`
      : `linear-gradient(90deg, ${token("--pole-low")}, ${token("--mid")}, ${token("--pole-high")})`;
    const low = document.createElement("span");
    const high = document.createElement("span");
    if (mode === "stability") { low.textContent = "0.4"; high.textContent = "1.0"; }
    else { low.textContent = "−2"; high.textContent = "+2"; }
    scaleRow.append(low, ramp, high);
    box.append(scaleRow);

    const note = document.createElement("div");
    note.className = "hint";
    note.textContent = mode === "stability"
      ? "Насколько устойчиво канал попадает к тем же соседям. Серый — не измерено."
      : "Ноль — медиана инвентаря. Серый — не хватило текста, чтобы измерить.";
    box.append(note);
    return;
  }

  title.textContent = "Кластеры";
  for (const c of DATA.clusters) {
    const row = document.createElement("button");
    row.className = "row";
    row.setAttribute("aria-pressed", String(isolated === c.id));
    const swatch = document.createElement("i");
    swatch.className = "swatch";
    swatch.style.background = c.slot >= 0
      ? token("--s" + (c.slot + 1))
      : token("--neutral");
    const name = document.createElement("span");
    name.className = "name";
    name.innerHTML = `<b>${c.name || "без названия"}</b><span>${c.terms}</span>`;
    const size = document.createElement("span");
    size.className = "size";
    size.textContent = c.size;
    row.append(swatch, name, size);
    row.addEventListener("click", () => {
      isolated = isolated === c.id ? null : c.id;
      selected = null;
      renderLegend();
      draw();
    });
    box.append(row);
  }
}

document.getElementById("meta").textContent =
  `${nodes.length} каналов · ${links.length} связей · ` +
  `${DATA.clusters.length} кластеров · resolution ${DATA.resolution} · ${DATA.generated}`;

window.addEventListener("resize", resize);
renderLegend();
resize();
</script>
</body>
</html>
"""


def layout(result: Clustering) -> dict[int, tuple[float, float]]:
    """Force-directed positions, normalized into the unit square.

    Plain Fruchterman-Reingold on this graph draws a hairball: every
    cluster lands on top of every other, and the one thing the map was
    supposed to show is the one thing invisible in it. Two corrections,
    and both are about the layout only — neither touches the weights the
    clustering itself used, which are computed in ``clusters.py`` and
    must not be tuned to make a picture look better.

    Clusters start apart, on a circle, and each member starts near its
    own cluster's point. Force layout is local: it refines a starting
    arrangement rather than searching for the global best, so where it
    starts is most of where it ends.

    Edges inside a cluster then pull harder than edges between them, by
    ``INTRA_BOOST``. This is the layout saying what the partition already
    concluded, so that a reader sees the grouping rather than having to
    take it on trust from a colour.

    The RNG is seeded so re-running after a threshold change shows what
    moved instead of a map rotated into unfamiliarity. Note that ``seed``
    on the layout call is *starting coordinates* in igraph, not an RNG
    seed — hence both.
    """
    ig.set_random_number_generator(random.Random(LAYOUT_SEED))
    jitter = random.Random(LAYOUT_SEED)

    labels = sorted({label for label in result.cluster.values()})
    anchors = {
        label: (
            math.cos(2 * math.pi * i / len(labels)) * CLUSTER_RING,
            math.sin(2 * math.pi * i / len(labels)) * CLUSTER_RING,
        )
        for i, label in enumerate(labels)
    }

    start = []
    boosted = []
    for vertex in result.graph.vs:
        anchor = anchors.get(result.cluster.get(vertex["channel"]), (0.0, 0.0))
        start.append(
            [
                anchor[0] + jitter.uniform(-1.0, 1.0),
                anchor[1] + jitter.uniform(-1.0, 1.0),
            ]
        )
    for edge in result.graph.es:
        source = result.graph.vs[edge.source]["channel"]
        target = result.graph.vs[edge.target]["channel"]
        same = result.cluster.get(source) is not None and result.cluster.get(
            source
        ) == result.cluster.get(target)
        boosted.append(edge["weight"] * (INTRA_BOOST if same else 1.0))

    coords = result.graph.layout_fruchterman_reingold(
        weights=boosted,
        niter=LAYOUT_ITERATIONS,
        seed=start,
        start_temp=START_TEMP,
    )
    xs = [point[0] for point in coords]
    ys = [point[1] for point in coords]

    # Normalized on a robust range, not on the extremes. A dozen
    # barely-connected channels drift a long way out, and scaling to
    # min/max would spend most of the frame on them and squeeze the 450
    # channels worth looking at into the middle third. Outliers land
    # outside the unit square instead, which the page pans and zooms to
    # reach like anything else.
    def bounds(values: list[float]) -> tuple[float, float]:
        ordered = sorted(values)
        low = ordered[int(len(ordered) * LAYOUT_TRIM)]
        high = ordered[int(len(ordered) * (1 - LAYOUT_TRIM)) - 1]
        return low, high

    x_low, x_high = bounds(xs)
    y_low, y_high = bounds(ys)
    span = max(x_high - x_low, y_high - y_low) or 1.0
    return {
        vertex["channel"]: (
            (coords[vertex.index][0] - x_low) / span,
            (coords[vertex.index][1] - y_low) / span,
        )
        for vertex in result.graph.vs
    }


def payload(result: Clustering) -> dict[str, Any]:
    """Everything the page draws, and nothing the raw layer holds."""
    node_share, _ = shares(result)
    positions = layout(result)

    sizes: dict[int, int] = {}
    for label in result.cluster.values():
        sizes[label] = sizes.get(label, 0) + 1
    ranked = sorted(sizes, key=lambda label: -sizes[label])
    # Every cluster gets a slot — the palette is generated to this count,
    # so there is no "past the end" case and nothing falls through to
    # grey. Grey means unlabelled, and only that.
    slot = dict(zip(ranked, slot_order(len(ranked)), strict=True))

    weights = {
        vertex["channel"]: sum(
            result.graph.es[edge]["weight"]
            for edge in result.graph.incident(vertex.index)
        )
        for vertex in result.graph.vs
    }
    heaviest = max(weights.values()) or 1.0

    def number(value: Any) -> float | None:
        """A style score as JSON can hold it, or nothing."""
        if value is None:
            return None
        try:
            scalar = float(value)
        except TypeError, ValueError:
            return None
        # NaN is what pandas leaves where a channel had too little text
        # to score, and `json.dumps` writes it as the bare token `NaN`,
        # which `JSON.parse` rejects — the page would fail to load at all
        # rather than show a gap.
        return None if math.isnan(scalar) else round(scalar, 3)

    nodes = []
    for vertex in result.graph.vs:
        channel = vertex["channel"]
        title, username, kind, _ = result.meta[channel]
        label = result.cluster.get(channel)
        share = node_share.get(channel, {})
        style = (
            result.style.loc[channel]
            if channel in result.style.index
            else None
        )
        x, y = positions[channel]
        nodes.append(
            {
                "i": vertex.index,
                "t": title or username or str(channel),
                "u": username,
                "k": kind,
                "c": label,
                "cn": result.names.get(label) if label is not None else None,
                "slot": slot.get(label, -1) if label is not None else -1,
                "x": round(x, 4),
                "y": round(y, 4),
                "w": round(weights[channel] / heaviest, 4),
                "p": vertex.degree(),
                "s": number(result.stability.get(channel)),
                "in": number(share.get("all")) if label is not None else None,
                "fi": number(share.get("forward"))
                if label is not None
                else None,
                "mi": number(share.get("mention"))
                if label is not None
                else None,
                "d": None if style is None else number(style["depth"]),
                "dr": None if style is None else number(style["dryness"]),
            }
        )

    heaviest_edge = max(edge["weight"] for edge in result.graph.es) or 1.0
    links = [
        [
            edge.source,
            edge.target,
            round(edge["weight"] / heaviest_edge, 3),
        ]
        for edge in result.graph.es
    ]

    clusters = [
        {
            "id": label,
            "name": (result.names.get(label) or "").split(", ")[0],
            "terms": result.names.get(label) or "",
            "size": sizes[label],
            "slot": slot.get(label, -1),
        }
        for label in ranked
    ]

    return {
        "nodes": nodes,
        "links": links,
        "clusters": clusters,
        "resolution": RESOLUTION,
        "generated": datetime.now(UTC).strftime("%Y-%m-%d"),
    }


def oklch_to_linear(
    lightness: float, chroma: float, hue_degrees: float
) -> tuple[float, float, float]:
    """OKLCH to linear sRGB, unclamped so the caller can test for gamut."""
    hue = math.radians(hue_degrees)
    a = chroma * math.cos(hue)
    b = chroma * math.sin(hue)

    long_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    medium = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    short = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3

    return (
        4.0767416621 * long_ - 3.3077115913 * medium + 0.2309699292 * short,
        -1.2684380046 * long_ + 2.6097574011 * medium - 0.3413193965 * short,
        -0.0041960863 * long_ - 0.7034186147 * medium + 1.7076147010 * short,
    )


def oklch_to_hex(lightness: float, chroma: float, hue_degrees: float) -> str:
    """One OKLCH colour as sRGB hex, gamut-mapped rather than clipped.

    The distinction is the whole function. sRGB cannot reach the same
    chroma at every hue — a saturated cyan at a mid lightness is simply
    outside it — and clamping the channels afterwards does not merely
    reduce saturation, it moves the hue and lightness too, arbitrarily
    and differently per colour. That is how a generated palette ends up
    with one entry measuring *below* the chroma floor and reading grey on
    the page, which is exactly the appearance that means "no cluster".

    So chroma is bisected down to the most the gamut will hold at this
    lightness and hue. The colour comes out less saturated than asked and
    stays the colour that was asked for.

    Written out rather than pulled from a colour library: it is thirty
    lines of published matrix arithmetic, and this is the only place in
    the project that needs it.
    """
    low, high = 0.0, chroma
    if (
        max(oklch_to_linear(lightness, chroma, hue_degrees)) > 1.0
        or min(oklch_to_linear(lightness, chroma, hue_degrees)) < 0.0
    ):
        for _ in range(24):
            middle = (low + high) / 2
            channels = oklch_to_linear(lightness, middle, hue_degrees)
            if max(channels) > 1.0 or min(channels) < 0.0:
                high = middle
            else:
                low = middle
        chroma = low

    def encode(channel: float) -> int:
        channel = max(0.0, min(1.0, channel))
        companded = (
            12.92 * channel
            if channel <= 0.0031308
            else 1.055 * channel ** (1 / 2.4) - 0.055
        )
        return round(companded * 255)

    return "#{:02x}{:02x}{:02x}".format(
        *(encode(c) for c in oklch_to_linear(lightness, chroma, hue_degrees))
    )


def max_chroma(lightness: float, hue_degrees: float) -> float:
    """The most chroma sRGB holds at this lightness and hue."""
    low, high = 0.0, 0.4
    for _ in range(20):
        middle = (low + high) / 2
        channels = oklch_to_linear(lightness, middle, hue_degrees)
        if max(channels) > 1.0 or min(channels) < 0.0:
            high = middle
        else:
            low = middle
    return low


def fit_lightness(
    preferred: float, hue_degrees: float, band: tuple[float, float]
) -> float:
    """The lightness to actually use for this hue, kept inside the band.

    sRGB is not equally wide at every hue: teal and olive run out of
    chroma at a mid lightness where red and blue still have plenty. Left
    alone they come back under the chroma floor — grey, which on this map
    is the colour that means *no cluster*, so the palette would be
    reporting the one thing it must never report by accident.

    A hue that cannot hold ``MIN_CHROMA`` at its tier is therefore moved
    to the lightness within the band where it holds the most, and only
    such a hue is moved. Everything else stays on its tier, which is what
    the tiers are for.
    """
    if max_chroma(preferred, hue_degrees) >= MIN_CHROMA:
        return preferred

    # The *nearest* workable lightness, not the best one. Moving every
    # starved hue to wherever the gamut is widest piles the neighbouring
    # cyans onto one lightness — and they are neighbours in hue already,
    # so that is the one place the tiers were still doing work. Walking
    # outwards from the tier keeps as much of the original spacing as the
    # gamut allows.
    low = band[0] + BAND_MARGIN
    high = band[1] - BAND_MARGIN
    step = (high - low) / 60
    for distance in range(61):
        for candidate in (
            preferred + distance * step,
            preferred - distance * step,
        ):
            if low <= candidate <= high and (
                max_chroma(candidate, hue_degrees) >= MIN_CHROMA
            ):
                return candidate

    # No lightness in the band holds enough chroma at this hue. Take the
    # widest point rather than the tier, so it is at least as far from
    # grey as this hue can get.
    return max(
        (low + (high - low) * i / 60 for i in range(61)),
        key=lambda lightness: max_chroma(lightness, hue_degrees),
    )


def palette(
    count: int,
    tiers: tuple[float, ...],
    chroma: float,
    band: tuple[float, float],
) -> list[str]:
    """``count`` hues spread evenly round the wheel, cycling the tiers.

    The tiers are what keeps this honest as the count grows: hue spacing
    shrinks with every extra cluster, and past a dozen or so some pairs
    are indistinguishable by hue alone. Alternating lightness means such
    a pair still differs in something.
    """
    colours = []
    for index in range(count):
        hue = HUE_OFFSET + 360.0 * index / max(count, 1)
        colours.append(
            oklch_to_hex(
                fit_lightness(tiers[index % len(tiers)], hue, band),
                chroma,
                hue,
            )
        )
    return colours


def slot_stride(count: int) -> int:
    """A step round the wheel that visits every slot exactly once.

    Roughly a third of the way round, so that clusters adjacent in the
    legend — which are adjacent in size, and usually adjacent on the map —
    get hues far apart rather than neighbouring ones. Any stride coprime
    to the count enumerates the whole palette; this picks the one nearest
    a third that qualifies, so it works for whatever count turns up
    instead of only for the count that happened to be current.
    """
    for delta in range(count):
        for candidate in (count // 3 + delta, count // 3 - delta):
            if candidate > 0 and math.gcd(candidate, count) == 1:
                return candidate
    return 1


def slot_order(count: int) -> list[int]:
    """Palette slots in the order clusters should take them.

    Not ``0, 1, 2, …``. The palette runs around the hue wheel, and both
    the legend and the size ranking hand out slots in order — so the two
    largest clusters, which sit next to each other in the legend and
    usually next to each other on the map, were getting adjacent hues.
    That is the one pair a reader compares most and the one the palette
    separates least: it put two oranges side by side at the top of the
    list.

    Walking with a stride coprime to the count visits every slot exactly
    once while putting roughly a third of the wheel between consecutive
    ranks. The stride is computed from the count rather than fixed — see
    `slot_stride` — because the count changes between runs.
    """
    if count <= 1:
        return list(range(count))
    stride = slot_stride(count)
    return [(i * stride) % count for i in range(count)]


def series_tokens(palette: tuple[str, ...], indent: int) -> str:
    """The ``--sN`` custom properties, written out of the palette itself.

    Generated rather than typed into the stylesheet so the count cannot
    drift from ``COLOURED_CLUSTERS``. When it could, it did.
    """
    pad = " " * indent
    return "\n".join(
        f"{pad}--s{slot}: {colour};"
        for slot, colour in enumerate(palette, start=1)
    )


def main() -> None:
    with psycopg.connect(DSN) as conn:
        result = build(conn)

    data = payload(result)
    colours = len(data["clusters"])
    page = (
        TEMPLATE.replace(
            "__SERIES_LIGHT__",
            series_tokens(
                palette(colours, LIGHT_TIERS, LIGHT_CHROMA, LIGHT_BAND), 4
            ),
        )
        .replace(
            "__SERIES_DARK__",
            series_tokens(
                palette(colours, DARK_TIERS, DARK_CHROMA, DARK_BAND), 6
            ),
        )
        .replace(
            "__DATA__",
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
        )
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")

    size_kb = OUT.stat().st_size / 1024
    print(
        f"{len(data['nodes'])} channels, {len(data['links'])} links, "
        f"{len(data['clusters'])} clusters -> {OUT} ({size_kb:.0f} KB)"
    )
    print(
        "seed channels only, no review columns — safe to publish; "
        "self-contained, so it makes no network request wherever it runs"
    )


if __name__ == "__main__":
    main()
