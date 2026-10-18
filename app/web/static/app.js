// Deal Sniper UI. Vanilla ES modules; no build step, no dependencies.
//
// The scope plots every scored listing as a blip:
//   distance from centre  = sniper score (centre is the best deal)
//   direction             = product family (sector)
//   colour                = risk;  size = landed cost
// A sweep line passes over them with a phosphor-style fade, a crosshair follows
// the cursor, and clicking "locks on" and opens the target's dossier.

const NS = "http://www.w3.org/2000/svg";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const sv = (tag, attrs = {}, parent) => {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "$" + Math.round(n).toLocaleString("en-CA");
const pct = (x) => Math.round(x * 100) + "%";
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ── geometry ────────────────────────────────────────────────────────────────
const C = 500, RMAX = 438, RMIN = 46;
const radiusOf = (score) => RMIN + (1 - clamp(score, 0, 100) / 100) * (RMAX - RMIN - 22);
const scoreAt = (r) => clamp((1 - (r - RMIN) / (RMAX - RMIN - 22)) * 100, 0, 100);
const rad = (deg) => (deg * Math.PI) / 180;
const norm360 = (d) => ((d % 360) + 360) % 360;
function hash(str) {                       // FNV-1a -> [0,1)
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 10007) / 10007;
}
const RISK_COLOR = { low: "var(--low)", medium: "var(--med)", high: "var(--high)" };
const SOURCE_COLOR = { kijiji: "#8fb8ff", reddit: "#ff8a4d", ebay: "#ffd24b", craigslist: "#b79cff" };

// ── state ───────────────────────────────────────────────────────────────────
const S = {
  data: null, deals: [], byId: new Map(), visible: [], sectors: [],
  view: store.get("view", "scope"), selectedId: null,
  sort: { key: "score", dir: -1 }, landedCap: 0,
  postal: store.get("postal", ""),
  f: { sectors: new Set(), sources: new Set(), maxLanded: Infinity, maxRisk: 100, minScore: 20,
       qualifying: false, shippable: true, hideDismissed: true, suspicious: false, mark: null, q: "" },
};
const blipNodes = new Map();

// ── data ────────────────────────────────────────────────────────────────────
async function load(refresh = false) {
  const res = await fetch("/api/state" + (refresh ? "?refresh=1" : ""));
  if (!res.ok) throw new Error("HTTP " + res.status);
  S.data = await res.json();
  S.deals = S.data.deals;
  S.byId = new Map(S.deals.map((d) => [d.id, d]));
  S.sectors = S.data.sectors;
  const landed = S.deals.map((d) => d.landed).sort((a, b) => a - b);
  S.landedCap = landed.length ? Math.ceil(landed[Math.floor(landed.length * 0.98)] / 100) * 100 : 1000;
}

const isSuspicious = (d) => d.price < 0.35 * d.median;

function applyFilters() {
  const f = S.f, q = f.q.trim().toLowerCase();
  S.visible = S.deals.filter((d) =>
    (!f.sectors.size || f.sectors.has(d.family)) &&
    (!f.sources.size || f.sources.has(d.source)) &&
    d.landed <= f.maxLanded && d.risk <= f.maxRisk && d.score >= f.minScore &&
    (!f.qualifying || d.qualifies) &&
    (!f.shippable || d.fulfillment !== "pickup_only") &&
    (!f.hideDismissed || d.mark !== "dismissed") &&
    (f.suspicious || !isSuspicious(d)) &&
    (!f.mark || d.mark === f.mark) &&
    (!q || (d.title + " " + d.product).toLowerCase().includes(q)));
  S.visible.sort((a, b) => b.score - a.score);
}

// ── status line + rail ──────────────────────────────────────────────────────
function ago(iso) {
  const mins = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 90) return Math.round(mins) + "m ago";
  if (mins < 2880) return Math.round(mins / 60) + "h ago";
  return Math.round(mins / 1440) + "d ago";
}
function renderStatus() {
  const { counts, runs } = S.data, run = runs[0];
  const qual = S.deals.filter((d) => d.qualifies).length;
  let sweep = '<span class="warn">no sweeps recorded</span>';
  if (run) {
    const stale = Date.now() - new Date(run.startedAt).getTime() > 36 * 3600e3;
    const bad = run.status !== "ok" || stale;
    sweep = `last sweep <span class="${bad ? "warn" : "ok"}">${ago(run.startedAt)}${run.status !== "ok" ? " · " + esc(run.status) : ""}</span>`;
  }
  $("#status").innerHTML = `${sweep} · <b>${counts.active.toLocaleString()}</b> active · <b>${S.deals.length}</b> scored · <b>${qual}</b> clear the bar`;
  $("#n-qualify").textContent = qual;
}

function chip(label, count, pressed, onClick, color) {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button"; b.setAttribute("aria-pressed", pressed);
  if (color) b.style.setProperty("--c", color);
  b.innerHTML = `${color ? "<i></i>" : ""}${esc(label)} <em>${count}</em>`;
  b.addEventListener("click", onClick);
  return b;
}
function renderRail() {
  const count = (key, val) => S.deals.filter((d) => d[key] === val).length;
  const sec = $("#sector-chips"); sec.replaceChildren();
  for (const s of S.sectors) {
    sec.append(chip(s, count("family", s), S.f.sectors.has(s), () => { toggle(S.f.sectors, s); refresh(); }));
  }
  const src = $("#source-chips"); src.replaceChildren();
  for (const s of [...new Set(S.deals.map((d) => d.source))].sort()) {
    src.append(chip(s, count("source", s), S.f.sources.has(s), () => { toggle(S.f.sources, s); refresh(); }, SOURCE_COLOR[s]));
  }
  const mk = $("#mark-chips"); mk.replaceChildren();
  for (const m of ["saved", "contacted", "dismissed"]) {
    mk.append(chip(m, count("mark", m), S.f.mark === m, () => { S.f.mark = S.f.mark === m ? null : m; if (m === "dismissed" && S.f.mark) { S.f.hideDismissed = false; $("#t-dismissed").checked = false; } refresh(); }));
  }
  const cap = $("#f-landed");
  $("#v-landed").textContent = S.f.maxLanded === Infinity ? "any" : money(S.f.maxLanded);
  syncSlider(cap); syncSlider($("#f-risk")); syncSlider($("#f-score"));
}
const toggle = (set, v) => (set.has(v) ? set.delete(v) : set.add(v));
function syncSlider(input) {
  input.style.setProperty("--fill", ((input.value - input.min) / (input.max - input.min)) * 100 + "%");
}

// ── scope ───────────────────────────────────────────────────────────────────
const scope = $("#scope");
const gGrid = sv("g", { id: "grid" }, scope);
const gSweep = sv("g", { id: "sweep" }, scope);
const gBlips = sv("g", { id: "blips" }, scope);
const gReticle = sv("g", { class: "reticle" }, scope);
const gCross = sv("g", { class: "cross-g" }, scope);
const crossH = sv("line", { class: "cross", x1: 0, x2: 1000 }, gCross);
const crossV = sv("line", { class: "cross", y1: 0, y2: 1000 }, gCross);
crossH.style.display = crossV.style.display = "none";
let sweepSlices;

function sectorGeometry() {
  const n = S.sectors.length, span = 360 / n;
  return { n, span, start: (i) => -90 + i * span };
}

function drawGrid() {
  gGrid.replaceChildren();
  const { n, span, start } = sectorGeometry();
  for (const sc of [80, 60, 40, 20]) {
    sv("circle", { cx: C, cy: C, r: radiusOf(sc), class: "ring" + (sc === 60 ? " major" : "") }, gGrid);
    const t = sv("text", { x: C + 5, y: C - radiusOf(sc) - 4, class: "ring-label" }, gGrid);
    t.textContent = sc;
  }
  sv("circle", { cx: C, cy: C, r: RMAX, class: "ring major" }, gGrid);
  sv("circle", { cx: C, cy: C, r: RMIN - 8, class: "core" }, gGrid);
  const core = sv("text", { x: C, y: C + 5, class: "core-text" }, gGrid);
  core.textContent = "100";
  S.sectors.forEach((name, i) => {
    const a = start(i);
    sv("line", { x1: C + (RMIN) * Math.cos(rad(a)), y1: C + (RMIN) * Math.sin(rad(a)),
                 x2: C + RMAX * Math.cos(rad(a)), y2: C + RMAX * Math.sin(rad(a)), class: "spoke" }, gGrid);
    const mid = a + span / 2, lr = RMAX + 26;
    const x = C + lr * Math.cos(rad(mid)), y = C + lr * Math.sin(rad(mid));
    const anchor = Math.abs(Math.cos(rad(mid))) < 0.25 ? "middle" : Math.cos(rad(mid)) > 0 ? "start" : "end";
    const lab = sv("text", { x, y, "text-anchor": anchor, class: "sector-label", "data-sector": name }, gGrid);
    lab.textContent = name;
    const cnt = sv("text", { x, y: y + 14, "text-anchor": anchor, class: "sector-count", "data-count": name }, gGrid);
    cnt.textContent = "";
  });
}

function drawSweep() {
  gSweep.replaceChildren();
  sweepSlices = sv("g", {}, gSweep);
  const slices = 22, w = 1.7;
  for (let i = 0; i < slices; i++) {
    const a0 = rad(-(i + 1) * w), a1 = rad(-i * w), r = RMAX + 4;
    sv("path", {
      d: `M${C} ${C} L${C + r * Math.cos(a0)} ${C + r * Math.sin(a0)} A${r} ${r} 0 0 1 ${C + r * Math.cos(a1)} ${C + r * Math.sin(a1)} Z`,
      fill: "#d6ff3f", "fill-opacity": (0.2 * Math.pow(1 - i / slices, 2)).toFixed(3), class: "sweep-slice",
    }, sweepSlices);
  }
  sv("line", { x1: C, y1: C, x2: C + RMAX + 4, y2: C, stroke: "#d6ff3f", "stroke-opacity": ".55", "stroke-width": 1.2, class: "sweep-slice" }, sweepSlices);
}

function blipPos(d) {
  const { span, start } = sectorGeometry();
  const idx = Math.max(0, S.sectors.indexOf(d.family));
  const a = start(idx) + 3 + hash(d.id) * (span - 6);
  const r = radiusOf(d.score) + (hash(d.id + "r") - 0.5) * 9;
  return { a, r, x: C + r * Math.cos(rad(a)), y: C + r * Math.sin(rad(a)) };
}
const blipRadius = (d) => clamp(2.6 + Math.sqrt(d.landed) * 0.115, 3.2, 12);

function renderBlips() {
  gBlips.replaceChildren(); blipNodes.clear();
  const frag = document.createDocumentFragment();
  // Draw the best last so they sit on top.
  for (const d of [...S.visible].reverse()) {
    const p = blipPos(d), r = blipRadius(d), color = RISK_COLOR[d.riskLabel] || RISK_COLOR.medium;
    const g = sv("g", { class: "blip", "data-id": d.id });
    sv("circle", { cx: p.x, cy: p.y, r: r + 4, fill: color, "fill-opacity": ".15", class: "halo" }, g);
    const dot = sv("circle", { cx: p.x, cy: p.y, r, fill: color, class: "dot" }, g);
    if (d.qualifies) sv("circle", { cx: p.x, cy: p.y, r: r + 4.5, class: "bar" }, g);
    if (d.ageHours < 24 && !reduceMotion) sv("circle", { cx: p.x, cy: p.y, r: r + 2, class: "pulse-ring" }, g);
    if (d.mark === "saved") sv("circle", { cx: p.x, cy: p.y, r: Math.max(1.6, r * 0.38), fill: "#0a0d0c" }, g);
    if (d.mark === "contacted") sv("circle", { cx: p.x, cy: p.y, r: Math.max(1.8, r * 0.45), fill: "none", stroke: "#0a0d0c", "stroke-width": 1.3 }, g);
    frag.append(g);
    blipNodes.set(d.id, { g, dot, a: norm360(p.a), p, strong: d.qualifies });
  }
  gBlips.append(frag);
  $("#scope-empty").hidden = S.visible.length > 0;
  // sector tallies on the rim
  for (const s of S.sectors) {
    const n = S.visible.filter((d) => d.family === s).length;
    const node = gGrid.querySelector(`[data-count="${CSS.escape(s)}"]`);
    if (node) node.textContent = n ? n + " in range" : "";
    gGrid.querySelector(`[data-sector="${CSS.escape(s)}"]`)?.classList.toggle("on", S.f.sectors.has(s));
  }
  paintSelection();
}

function paintSelection() {
  gReticle.replaceChildren();
  const sel = S.byId.get(S.selectedId);
  for (const [id, n] of blipNodes) {
    const d = S.byId.get(id);
    n.g.classList.toggle("dim", !!sel && id !== sel.id && d.product !== sel.product);
    n.g.classList.toggle("peer", !!sel && id !== sel.id && d.product === sel.product);
    n.strong = d.qualifies || (sel && (id === sel.id || d.product === sel.product));
  }
  const node = sel && blipNodes.get(sel.id);
  if (!node) return;
  const { x, y } = node.p, s = blipRadius(sel) + 15, k = 8;
  sv("line", { x1: C, y1: C, x2: x, y2: y, class: "bearing" }, gReticle);
  const shell = sv("g", { class: "shell" }, gReticle);
  for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    sv("path", { class: "brk", d: `M${x + sx * s} ${y + sy * (s - k)} V${y + sy * s} H${x + sx * (s - k)}` }, shell);
  }
}

// phosphor sweep ------------------------------------------------------------
const SWEEP_MS = 16000;
let lastPaint = 0;
function tick(t) {
  requestAnimationFrame(tick);
  if (S.view !== "scope" || reduceMotion || t - lastPaint < 33) return;
  lastPaint = t;
  const sw = ((t % SWEEP_MS) / SWEEP_MS) * 360;
  sweepSlices?.setAttribute("transform", `rotate(${sw} ${C} ${C})`);
  const heavy = blipNodes.size > 1400;
  for (const n of blipNodes.values()) {
    if (heavy) break;
    const delta = norm360(sw - n.a);
    const floor = n.strong ? 0.92 : 0.4;
    n.g.style.opacity = (floor + (1 - floor) * Math.exp(-delta / 120)).toFixed(2);
  }
}

// pointer interaction -------------------------------------------------------
const tip = $("#tip"), readout = $("#readout"), wrap = $("#scope-wrap");
function toScope(evt) {
  const pt = scope.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
  return pt.matrixTransform(scope.getScreenCTM().inverse());
}
scope.addEventListener("pointermove", (evt) => {
  const p = toScope(evt), dx = p.x - C, dy = p.y - C, r = Math.hypot(dx, dy);
  const inside = r < RMAX + 14;
  crossH.style.display = crossV.style.display = inside ? "" : "none";
  crossH.setAttribute("y1", p.y); crossH.setAttribute("y2", p.y);
  crossV.setAttribute("x1", p.x); crossV.setAttribute("x2", p.x);
  if (inside && r > RMIN - 8) {
    const { span, n } = sectorGeometry();
    const idx = Math.floor(norm360(Math.atan2(dy, dx) * 180 / Math.PI + 90) / span) % n;
    readout.innerHTML = `bearing <b>${esc(S.sectors[idx] || "—")}</b><br>score ≥ <b>${Math.round(scoreAt(r))}</b>`;
  } else readout.innerHTML = "";
  const hit = evt.target.closest?.(".blip");
  if (hit) showTip(S.byId.get(hit.dataset.id), evt); else tip.hidden = true;
});
scope.addEventListener("pointerleave", () => {
  crossH.style.display = crossV.style.display = "none"; tip.hidden = true; readout.innerHTML = "";
});
scope.addEventListener("click", (evt) => {
  const hit = evt.target.closest?.(".blip");
  if (hit) select(hit.dataset.id);
  else if (!evt.target.closest?.(".sector-label")) select(null);
});
gGrid.addEventListener("click", (evt) => {
  const lab = evt.target.closest?.(".sector-label");
  if (lab) { toggle(S.f.sectors, lab.dataset.sector); refresh(); }
});
function showTip(d, evt) {
  if (!d) return;
  tip.innerHTML = `<strong>${esc(d.title)}</strong><span><span class="n">${money(d.landed)}</span> landed · ${pct(d.discount)} under · ${esc(d.riskLabel)} risk · score ${d.score}</span>`;
  tip.hidden = false;
  const box = wrap.getBoundingClientRect();
  let x = evt.clientX - box.left + 16, y = evt.clientY - box.top + 16;
  if (x + 280 > box.width) x = evt.clientX - box.left - 286;
  if (y + 90 > box.height) y = evt.clientY - box.top - 80;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}

// ── ledger ──────────────────────────────────────────────────────────────────
const COLS = [
  { key: "score", label: "Score", num: true, cell: (d) => `<span class="scorebar"><i style="width:${d.score}%"></i></span>${d.score}` },
  { key: "product", label: "Product", cell: (d) => esc(d.product) },
  { key: "title", label: "Title", cls: "t", cell: (d) => esc(d.title) },
  { key: "landed", label: "Landed", num: true, cell: (d) => money(d.landed) },
  { key: "discount", label: "vs median", num: true, cell: (d) => `<span class="${d.discount > 0.2 ? "good" : "dim"}">${d.discount > 0 ? "−" : "+"}${pct(Math.abs(d.discount))}</span>` },
  { key: "profit", label: "Profit", num: true, cell: (d) => (d.profit > 0 ? money(d.profit) : '<span class="dim">—</span>') },
  { key: "risk", label: "Risk", num: true, cell: (d) => `<i class="rk" style="background:${RISK_COLOR[d.riskLabel]}"></i>${d.risk}` },
  { key: "fulfillment", label: "Ships", cell: (d) => ({ ships: "yes", unknown: "ask", pickup_only: "pickup" }[d.fulfillment] || "") },
  { key: "source", label: "Src", cell: (d) => esc(d.source) },
  { key: "ageHours", label: "Age", num: true, cell: (d) => (d.ageHours < 48 ? Math.round(d.ageHours) + "h" : Math.round(d.ageHours / 24) + "d") },
];
function renderLedger() {
  const table = $("#ledger"), { key, dir } = S.sort;
  const rows = [...S.visible].sort((a, b) => {
    const x = a[key], y = b[key];
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
  }).slice(0, 800);
  table.innerHTML = `<thead><tr><th></th>${COLS.map((c) => `<th data-k="${c.key}" class="${c.num ? "num " : ""}${c.key === key ? "sorted" : ""}">${c.label}${c.key === key ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((d) => `<tr data-id="${esc(d.id)}" class="${d.id === S.selectedId ? "sel" : ""}"><td class="mk">${{ saved: "★", contacted: "✉", dismissed: "✕" }[d.mark] || ""}</td>${COLS.map((c) => `<td class="${c.num ? "num " : ""}${c.cls || ""}">${c.cell(d)}</td>`).join("")}</tr>`).join("")}</tbody>`;
  $$("th[data-k]", table).forEach((th) => th.addEventListener("click", () => {
    const k = th.dataset.k;
    S.sort = { key: k, dir: S.sort.key === k ? -S.sort.dir : (["product", "title", "source", "fulfillment"].includes(k) ? 1 : -1) };
    renderLedger();
  }));
  $$("tbody tr", table).forEach((tr) => tr.addEventListener("click", () => select(tr.dataset.id)));
}

// ── dossier ─────────────────────────────────────────────────────────────────
function renderIdle() {
  const top = S.visible.slice(0, 5);
  const recent = S.data.runs.slice(0, 3).map((r) => `<div>${ago(r.startedAt)} · ${esc(r.status)} · ${r.new} new · ${r.alerts} alerts</div>`).join("");
  $("#dossier").innerHTML = `<div class="idle">
    <p class="lead">No target <span>locked.</span></p>
    <p>Closer to the centre means a better deal. Click a blip, or take one of the strongest in range:</p>
    <ul class="top5">${top.map((d) => `<li><button data-id="${esc(d.id)}"><span class="sc">${d.score}</span><span class="tt">${esc(d.product)}<small>${esc(d.title)}</small></span><span class="pr">${money(d.landed)}</span></button></li>`).join("") || "<li><p>Nothing in range.</p></li>"}</ul>
    <div class="runs">${recent}</div></div>`;
  $$(".top5 button").forEach((b) => b.addEventListener("click", () => select(b.dataset.id)));
}

function lensSVG(d, band) {
  const W = 340, H = 96, pad = 16;
  const prices = band?.prices || [];
  if (prices.length < 3) return "<p class='rows'>Not enough comparable listings to draw a band.</p>";
  const q = (p) => prices[Math.min(prices.length - 1, Math.floor(p * (prices.length - 1)))];
  let lo = Math.min(q(0.02), d.landed, d.price) * 0.96, hi = Math.max(q(0.98), d.landed) * 1.04;
  if (hi - lo < 1) hi = lo + 1;
  const x = (v) => pad + ((clamp(v, lo, hi) - lo) / (hi - lo)) * (W - 2 * pad);
  const bins = new Map(), dots = [];
  for (const p of prices) {
    if (p < lo || p > hi) continue;
    const bx = Math.round(x(p) / 5), k = bins.get(bx) || 0; bins.set(bx, k + 1);
    const off = Math.ceil(k / 2) * 4.4 * (k % 2 ? -1 : 1);
    dots.push(`<circle class="comp" cx="${x(p).toFixed(1)}" cy="${clamp(44 + off, 22, 66).toFixed(1)}" r="2.4"/>`);
  }
  const clipped = prices.filter((p) => p > hi).length;
  const lx = x(d.landed);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Where this listing's landed cost sits among ${band.n} comparable asking prices">
    <rect class="band" x="${x(band.p25)}" y="24" width="${Math.max(2, x(band.p75) - x(band.p25))}" height="40" rx="4"/>
    ${dots.join("")}
    <line class="med" x1="${x(band.median)}" x2="${x(band.median)}" y1="20" y2="70"/>
    <text class="medtxt" x="${x(band.median)}" y="84">median ${money(band.median)}</text>
    <line class="axis" x1="${pad}" x2="${W - pad}" y1="70" y2="70"/>
    <text class="tick" x="${pad}" y="94" text-anchor="start">${money(lo)}</text>
    <text class="tick" x="${W - pad}" y="94" text-anchor="end">${money(hi)}${clipped ? " +" + clipped : ""}</text>
    <g class="me"><line x1="${lx}" x2="${lx}" y1="10" y2="70"/><circle cx="${lx}" cy="10" r="4"/>
      <text x="${clamp(lx, 36, W - 36)}" y="6">you ${money(d.landed)}</text></g></svg>`;
}

function sparkSVG(history) {
  if (history.length < 2) return "<p>No price changes recorded yet. Every sweep from now on tracks them.</p>";
  const W = 340, H = 54, pad = 6;
  const prices = history.map((h) => h.price), times = history.map((h) => new Date(h.at).getTime());
  const lo = Math.min(...prices), hi = Math.max(...prices), t0 = times[0], t1 = times[times.length - 1] || t0 + 1;
  const px = (t) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * pad);
  const py = (p) => H - pad - ((p - lo) / Math.max(1, hi - lo)) * (H - 2 * pad);
  const pts = history.map((h, i) => `${i ? "L" : "M"}${px(times[i]).toFixed(1)} ${py(h.price).toFixed(1)}`).join(" ");
  const last = history[history.length - 1], first = history[0];
  return `<svg viewBox="0 0 ${W} ${H}"><path class="ln" d="${pts}"/>${history.map((h, i) => `<circle class="pt" cx="${px(times[i]).toFixed(1)}" cy="${py(h.price).toFixed(1)}" r="3"/>`).join("")}</svg>
    <p>${money(first.price)} → ${money(last.price)} (${last.price < first.price ? "−" : "+"}${money(Math.abs(last.price - first.price))})</p>`;
}

function messageFor(d) {
  const postal = S.postal ? S.postal.toUpperCase() : "[your postal code]";
  if (d.source === "reddit") {
    return `Hi! I'm interested in your ${d.product} for ${money(d.price)}. Could you ship it to ${postal}? I'd pay by PayPal Goods & Services. Is it still available, and could you share a timestamp photo? Thanks!`;
  }
  const pay = /paypal_gs/.test(d.payment) ? "PayPal Goods & Services" : "e-transfer (or PayPal G&S if you prefer)";
  return `Hi! Is your "${d.title.slice(0, 90)}" still available? I'm not able to pick up in person — would you be willing to ship it to ${postal}? I'm happy to pay for tracked, insured shipping and can pay by ${pay}. Could you let me know the shipping cost and send a photo of it with today's date? Thanks!`;
}

function countUp(node, to) {
  if (reduceMotion) { node.textContent = Math.round(to).toLocaleString("en-CA"); return; }
  const from = to * 0.55, t0 = performance.now();
  const step = (t) => {
    const k = clamp((t - t0) / 520, 0, 1), e = 1 - Math.pow(1 - k, 3);
    node.textContent = Math.round(from + (to - from) * e).toLocaleString("en-CA");
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

async function renderDossier() {
  const d = S.byId.get(S.selectedId);
  if (!d) return renderIdle();
  const band = S.data.bands[d.product];
  const ship = { ships: ["yes", "Seller ships"], unknown: ["ask", "Not stated — ask the seller"], pickup_only: ["no", "Pickup only"] }[d.fulfillment];
  const route = { use: "Keep", flip: "Flip", both: "Keep / flip", none: "Near miss" }[d.route];
  const resale = d.profit > 0;
  const rating = d.rating != null ? `${d.rating.toFixed(1)}%${d.reviews != null ? " · " + d.reviews + " ratings" : ""}` : null;
  const protection = /ebay_protection/.test(d.payment) ? "eBay Money Back Guarantee" : /paypal_gs/.test(d.payment) ? "PayPal Goods & Services" : null;

  $("#dossier").innerHTML = `<div class="doc">
    <div class="tags">
      <span class="tag ${d.qualifies ? "lime" : ""}">${d.qualifies ? "Clears the bar" : "Near miss"}</span>
      <span class="tag ${d.riskLabel === "low" ? "low" : d.riskLabel === "medium" ? "med" : "high"}">${esc(d.riskLabel)} risk</span>
      <span class="tag">${esc(route)}</span><span class="tag">${esc(d.source)}</span>
      ${d.ageHours < 24 ? '<span class="tag lime">new</span>' : ""}${d.priceDrop ? '<span class="tag">price drop</span>' : ""}
    </div>
    <div><h3>${esc(d.title)}</h3><div class="prod">${esc(d.product)} · ${esc(d.family)}${d.location ? " · " + esc(d.location) : ""}</div></div>
    <div class="hero"><div class="big"><sup>$</sup><span id="hero-n">0</span></div>
      <div class="delta ${d.discount > 0.15 ? "good" : "bad"}">${d.discount >= 0 ? "−" : "+"}${pct(Math.abs(d.discount))} vs median</div>
      <div class="sub">landed cost · score ${d.score}/100 · ${d.comps} comparable listings</div></div>
    <div class="card lens"><h4>Price lens <span>vs ${band ? band.n : d.comps} comparable asks</span></h4>${lensSVG(d, band)}</div>
    <div class="card"><h4>Cost</h4><div class="rows">
      <div><span>Asking price</span><span>${money(d.price)}</span></div>
      <div><span>Shipping ${d.shippingStated ? "(stated)" : "(estimate)"}</span><span>${money(d.shipping)}</span></div>
      <div class="total"><span>Landed</span><span>${money(d.landed)}</span></div>
      <div><span>Typical ask (median)</span><span>${money(d.median)}</span></div>
      <div><span>Resale profit if flipped</span><span>${resale ? "≈ " + money(d.profit) + " · " + pct(d.roi) + " ROI" : "none after fees"}</span></div>
      <div class="est"><span>Fees, return shipping and a loss reserve are included. Asks are not sale prices.</span></div></div></div>
    <div class="card"><h4>Risk <span>${d.risk}/100</span></h4>
      <div class="meter"><i style="left:${d.risk}%"></i></div><div class="meter-scale"><span>safe</span><span>55 = high</span><span>scam-shaped</span></div>
      <ul class="reasons">${(d.riskReasons.length ? d.riskReasons : ["No red flags found"]).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
    <div class="card spark" id="spark"><h4>Price history</h4><p>Loading…</p></div>
    <div class="card"><h4>The deal</h4><div class="kv">
      <div><span>Shipping</span><span class="${ship[0]}">${ship[1]}</span></div>
      ${protection ? `<div><span>Protection</span><span class="yes">${esc(protection)}</span></div>` : ""}
      ${rating ? `<div><span>Seller</span><span>${esc(rating)}</span></div>` : ""}
      ${d.condition ? `<div><span>Condition</span><span>${esc(d.condition)}</span></div>` : ""}
      ${d.seller ? `<div><span>Seller id</span><span>${esc(String(d.seller).slice(0, 18))}</span></div>` : ""}</div>
      ${d.description ? `<p class="desc" id="desc" style="margin-top:12px">${esc(d.description)}</p>` : ""}</div>
    <div class="actions">
      <a class="btn primary" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">Open listing ↗</a>
      <button class="btn" id="b-msg" title="M">Copy “ship it?” note</button>
      <button class="btn" id="b-saved" aria-pressed="${d.mark === "saved"}" title="S">★ Save</button>
      <button class="btn" id="b-contacted" aria-pressed="${d.mark === "contacted"}" title="C">✉ Contacted</button>
      <button class="btn danger" id="b-dismissed" aria-pressed="${d.mark === "dismissed"}" title="D">✕ Dismiss</button>
    </div></div>`;
  countUp($("#hero-n"), d.landed);
  $("#desc")?.addEventListener("click", (e) => e.currentTarget.classList.toggle("open"));
  $("#b-msg").addEventListener("click", () => copyMessage(d));
  for (const m of ["saved", "contacted", "dismissed"]) $("#b-" + m).addEventListener("click", () => mark(d.id, d.mark === m ? null : m));
  // price history loads after the dossier paints
  try {
    const { history } = await (await fetch("/api/history/" + encodeURIComponent(d.id))).json();
    if (S.selectedId === d.id) $("#spark").innerHTML = "<h4>Price history</h4>" + sparkSVG(history);
  } catch { /* offline: leave the placeholder */ }
}

// ── actions ─────────────────────────────────────────────────────────────────
let toastTimer;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 2200);
}
async function copyMessage(d) {
  const text = messageFor(d);
  try { await navigator.clipboard.writeText(text); toast("Message copied — paste it to the seller"); }
  catch {
    const ta = document.createElement("textarea"); ta.value = text; document.body.append(ta); ta.select();
    try { document.execCommand("copy"); toast("Message copied"); } catch { toast("Couldn't copy"); }
    ta.remove();
  }
  if (!S.postal) toast("Tip: add your postal code on the left to fill it in");
}
async function mark(id, value) {
  const d = S.byId.get(id); if (!d) return;
  const prev = d.mark; d.mark = value;
  try {
    const res = await fetch("/api/mark", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, mark: value }) });
    if (!res.ok) throw new Error();
  } catch { d.mark = prev; toast("Couldn't save that"); return; }
  toast(value ? { saved: "Saved ★", contacted: "Marked contacted", dismissed: "Dismissed" }[value] : "Cleared");
  if (value === "dismissed" && S.f.hideDismissed) { const next = neighbour(1) || neighbour(-1); S.selectedId = next ? next.id : null; }
  refresh();
}

// ── selection + navigation ──────────────────────────────────────────────────
function select(id) {
  S.selectedId = id && S.byId.has(id) ? id : null;
  paintSelection(); renderDossier();
  // On narrow screens the dossier sits below the scope: bring it into view.
  if (S.selectedId && matchMedia("(max-width: 1040px)").matches) {
    $("#dossier").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
  }
  $$("#ledger tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === S.selectedId));
  if (S.view === "ledger" && S.selectedId) $(`#ledger tr[data-id="${CSS.escape(S.selectedId)}"]`)?.scrollIntoView({ block: "nearest" });
}
function neighbour(dir) {
  const list = S.view === "ledger" ? $$("#ledger tbody tr").map((tr) => S.byId.get(tr.dataset.id)) : S.visible;
  if (!list.length) return null;
  const i = list.findIndex((d) => d.id === S.selectedId);
  return list[i < 0 ? (dir > 0 ? 0 : list.length - 1) : clamp(i + dir, 0, list.length - 1)];
}

addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName);
  if (e.key === "Escape") { if (typing) document.activeElement.blur(); else select(null); return; }
  if (e.key === "/" && !typing) { e.preventDefault(); $("#search").focus(); return; }
  if (typing) return;
  const d = S.byId.get(S.selectedId);
  switch (e.key.toLowerCase()) {
    case "j": case "arrowright": case "arrowdown": e.preventDefault(); select(neighbour(1)?.id); break;
    case "k": case "arrowleft": case "arrowup": e.preventDefault(); select(neighbour(-1)?.id); break;
    case "enter": if (d) window.open(d.url, "_blank", "noopener"); break;
    case "s": if (d) mark(d.id, d.mark === "saved" ? null : "saved"); break;
    case "c": if (d) mark(d.id, d.mark === "contacted" ? null : "contacted"); break;
    case "d": if (d) mark(d.id, d.mark === "dismissed" ? null : "dismissed"); break;
    case "m": if (d) copyMessage(d); break;
    case "1": setView("scope"); break;
    case "2": setView("ledger"); break;
    case "?": $("#help-dialog").showModal(); break;
  }
});

// ── wiring ──────────────────────────────────────────────────────────────────
function setView(view) {
  S.view = view; store.set("view", view);
  $$(".seg button").forEach((b) => b.setAttribute("aria-selected", b.dataset.view === view));
  $("#scope-wrap").hidden = view !== "scope";
  $("#ledger-wrap").hidden = view !== "ledger";
  if (view === "ledger") renderLedger();
}
function refresh() {
  applyFilters(); renderRail(); renderBlips();
  if (S.view === "ledger") renderLedger();
  if (S.selectedId && !S.byId.get(S.selectedId)) S.selectedId = null;
  renderDossier();
}
function bindControls() {
  $$(".seg button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("#help").addEventListener("click", () => $("#help-dialog").showModal());
  $("#refresh").addEventListener("click", async () => {
    const btn = $("#refresh"); btn.classList.add("spin");
    try { await load(true); renderStatus(); refresh(); toast("Re-scored with the latest data"); }
    catch { toast("Couldn't refresh"); } finally { btn.classList.remove("spin"); }
  });
  $("#search").addEventListener("input", (e) => { S.f.q = e.target.value; refresh(); });
  const landed = $("#f-landed"), risk = $("#f-risk"), score = $("#f-score");
  landed.addEventListener("input", () => {
    const v = +landed.value; S.f.maxLanded = v >= 100 ? Infinity : Math.max(50, Math.round((v / 100) * S.landedCap / 50) * 50);
    refresh();
  });
  risk.addEventListener("input", () => { S.f.maxRisk = +risk.value; $("#v-risk").textContent = risk.value >= 100 ? "any" : "≤ " + risk.value; refresh(); });
  score.addEventListener("input", () => { S.f.minScore = +score.value; $("#v-score").textContent = "≥ " + score.value; refresh(); });
  $("#v-risk").textContent = "any"; $("#v-score").textContent = "≥ " + S.f.minScore;
  $("#t-qualify").addEventListener("change", (e) => { S.f.qualifying = e.target.checked; refresh(); });
  $("#t-ship").addEventListener("change", (e) => { S.f.shippable = e.target.checked; refresh(); });
  $("#t-dismissed").addEventListener("change", (e) => { S.f.hideDismissed = e.target.checked; refresh(); });
  $("#t-suspicious").addEventListener("change", (e) => { S.f.suspicious = e.target.checked; refresh(); });
  $("#reset-filters").addEventListener("click", () => {
    Object.assign(S.f, { sectors: new Set(), sources: new Set(), maxLanded: Infinity, maxRisk: 100, minScore: 0, qualifying: false, shippable: false, hideDismissed: false, suspicious: true, mark: null, q: "" });
    $("#search").value = ""; landed.value = 100; risk.value = 100; score.value = 0;
    $("#t-qualify").checked = false; $("#t-ship").checked = false; $("#t-dismissed").checked = false; $("#t-suspicious").checked = true;
    $("#v-risk").textContent = "any"; $("#v-score").textContent = "≥ 0"; refresh();
  });
  const postal = $("#postal"); postal.value = S.postal;
  postal.addEventListener("input", () => { S.postal = postal.value.trim(); store.set("postal", S.postal); });
}

async function boot() {
  bindControls(); setView(S.view);
  try { await load(); }
  catch (err) {
    $("#splash-text").textContent = "Couldn't reach the server — is `python -m app.web` running?";
    return;
  }
  drawGrid(); drawSweep(); renderStatus(); refresh();
  $("#splash").classList.add("done");
  requestAnimationFrame(tick);
  setInterval(async () => {                       // pick up new sweeps without a reload
    try { await load(); renderStatus(); const keep = S.selectedId; applyFilters(); renderRail(); renderBlips(); if (keep && S.byId.has(keep)) S.selectedId = keep; if (S.view === "ledger") renderLedger(); } catch { /* try again later */ }
  }, 5 * 60 * 1000);
}
boot();
