// Deal Sniper UI, gothic edition. Vanilla ES modules; no build step.
//
// The rose window plots every scored listing as a small ink drawing:
//   distance from the centre = score (the middle is the best deal)
//   petal (direction)        = product family
//   engraving                = risk (open = safe, hatched = medium, cross-hatched red = high)
//   size                     = landed cost
// Clicking a listing circles it in ink and opens its dossier.
//
// The ornament (facade, Tudor arch, hatching, cat) is drawn with the same
// routines as Nelson's board so the two look like one family.

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

// ── theme (follows the system; the button overrides) ────────────────────────
const root = document.documentElement;
const savedTheme = store.get("theme", null);
if (savedTheme) root.dataset.theme = savedTheme;
const currentTheme = () => root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
function syncThemeButton() { $("#theme").textContent = currentTheme() === "dark" ? "Dawn" : "Dusk"; }
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => document.getElementById("theme") && syncThemeButton());

// ── ornament, drawn like the board ──────────────────────────────────────────
let _pid = 0;
function hatchDefs(id, angle = 45, gap = 4, w = 0.8) {
  return `<pattern id="${id}" width="${gap}" height="${gap}" patternUnits="userSpaceOnUse" patternTransform="rotate(${angle})"><line x1="0" y1="0" x2="0" y2="${gap}" stroke="currentColor" stroke-width="${w}"/></pattern>`;
}
function sleepCatSVG() {
  const a = `h${++_pid}`;
  return `<svg class="sleepcat" viewBox="0 0 110 50" aria-hidden="true"><defs>${hatchDefs(a, -35, 3, .7)}</defs>
 <path d="M18 44 C8 40 8 26 22 20 C36 13 66 12 82 18 C96 24 100 36 94 44 Z" fill="url(#${a})" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
 <path d="M94 44 C104 44 106 36 100 30 C96 26 90 30 88 34" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
 <path d="M14 44 C6 44 4 36 8 30 C10 26 14 24 18 24 L17 15 L24 22 C27 21.5 30 21.5 32 22.5 L38 16 L38 26 C42 30 42 38 36 42 C30 46 20 46 14 44 Z" fill="var(--paper)" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
 <path d="M16 33 q3 2 6 0 M26 33 q3 2 6 0" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
 <path d="M23 37 l1.6 1.4 l1.6 -1.4Z" fill="currentColor"/>
 <path d="M4 48 H104" stroke="currentColor" stroke-width="1"/>
 <text x="44" y="10" font-family="IM Fell English, Georgia, serif" font-size="9" font-style="italic" fill="currentColor" opacity=".7">z z</text></svg>`;
}
function washSVG() {
  const f = `w${++_pid}`;
  return `<svg class="wash" viewBox="0 0 640 260" preserveAspectRatio="none" aria-hidden="true"><defs><filter id="${f}" x="-20%" y="-20%" width="140%" height="140%">
 <feTurbulence type="fractalNoise" baseFrequency=".012 .03" numOctaves="4" seed="7"/><feDisplacementMap in="SourceGraphic" scale="70"/><feGaussianBlur stdDeviation="3"/></filter></defs>
 <g filter="url(#${f})" fill="var(--wash)"><ellipse cx="320" cy="170" rx="250" ry="70" opacity=".55"/><ellipse cx="250" cy="200" rx="150" ry="40" opacity=".4"/><ellipse cx="430" cy="150" rx="120" ry="55" opacity=".35"/></g></svg>`;
}
function sketchLines(W, H) {
  let s = ""; let seed = 3;
  const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647; };
  const r = (a, b) => a + rnd() * (b - a);
  for (let i = 0; i < 16; i++) { const x = r(60, W - 60); s += `<line x1="${(x + r(-3, 3)).toFixed(1)}" y1="${r(0, 80).toFixed(1)}" x2="${(x + r(-3, 3)).toFixed(1)}" y2="${(H + r(0, 6)).toFixed(1)}" stroke="currentColor" stroke-width=".4" opacity="${r(.12, .3).toFixed(2)}"/>`; }
  for (let i = 0; i < 5; i++) { const y = r(120, H); s += `<line x1="${r(20, 90).toFixed(1)}" y1="${y.toFixed(1)}" x2="${r(W - 90, W - 20).toFixed(1)}" y2="${(y + r(-2, 2)).toFixed(1)}" stroke="currentColor" stroke-width=".4" opacity=".18"/>`; }
  return s;
}
function facadeSVG() {
  const W = 640, H = 300, cx = 320, G = H - 6;
  const sh = `h${++_pid}`, dk = `h${++_pid}`, xh = `h${++_pid}`, lt = `h${++_pid}`;
  const S = 'stroke="currentColor"';
  let o = `<svg class="facade" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMax meet" aria-hidden="true"><defs>
 ${hatchDefs(sh, 60, 3.4, .7)}${hatchDefs(dk, 60, 2.2, .8)}${hatchDefs(lt, 60, 5, .5)}
 <pattern id="${xh}" width="3" height="3" patternUnits="userSpaceOnUse"><path d="M0 0L3 3M3 0L0 3" ${S} stroke-width=".6"/></pattern></defs>`;
  const lancet = (x, y, w, h, fill = "var(--paper)", sw = 1) => { const r = w / 2; return `<path d="M${x - r} ${y + h} V${y + r * 1.15} C${x - r} ${y + r * .5} ${x - r * .4} ${y + r * .12} ${x} ${y} C${x + r * .4} ${y + r * .12} ${x + r} ${y + r * .5} ${x + r} ${y + r * 1.15} V${y + h} Z" fill="${fill}" ${S} stroke-width="${sw}"/>`; };
  const pinnacle = (x, y, w, h) => {
    let s = `<path d="M${x - w / 2} ${y + h} V${y + h * .45} L${x} ${y} L${x + w / 2} ${y + h * .45} V${y + h}" fill="url(#${lt})" ${S} stroke-width="1"/>`;
    for (let k = 1; k < 4; k++) { const yy = y + k * h * .12, dx = w / 2 * k * .12 / .45; s += `<path d="M${(x - dx).toFixed(1)} ${yy.toFixed(1)} q-3 -1 -2 -4 M${(x + dx).toFixed(1)} ${yy.toFixed(1)} q3 -1 2 -4" fill="none" ${S} stroke-width=".7"/>`; }
    return s + `<circle cx="${x}" cy="${y - 2}" r="1.8" fill="none" ${S} stroke-width=".8"/>`;
  };
  o += `<path d="M200 140 L230 96 L410 96 L440 140 Z" fill="url(#${lt})" ${S} stroke-width=".8" opacity=".7"/>`;
  const LT = [132, 222];
  o += `<rect x="${LT[0]}" y="120" width="90" height="${G - 120}" fill="var(--paper)" ${S} stroke-width="1.5"/>`;
  o += `<rect x="${LT[0] + 66}" y="120" width="24" height="${G - 120}" fill="url(#${sh})" />`;
  o += lancet(177, 232, 30, 52, `url(#${dk})`) + lancet(177, 166, 26, 48, `url(#${dk})`);
  o += `<path d="M${LT[0]} 160 H${LT[1]} M${LT[0]} 222 H${LT[1]}" ${S} stroke-width="1"/>`;
  o += `<path d="M146 120 V78 H208 V120" fill="var(--paper)" ${S} stroke-width="1.4"/><rect x="190" y="78" width="18" height="42" fill="url(#${sh})"/>`;
  o += lancet(165, 86, 12, 30, `url(#${dk})`, .9) + lancet(189, 86, 12, 30, `url(#${dk})`, .9);
  o += pinnacle(140, 92, 12, 30) + pinnacle(214, 92, 12, 30);
  o += `<path d="M150 78 L177 6 L204 78 Z" fill="url(#${lt})" ${S} stroke-width="1.4"/><path d="M177 6 L190 78 L204 78 Z" fill="url(#${sh})"/>`;
  for (let k = 1; k < 8; k++) { const yy = 78 - k * 9, dx = 27 * (1 - k / 8); o += `<path d="M${(177 - dx).toFixed(1)} ${yy} q-4 -1 -3 -5 M${(177 + dx).toFixed(1)} ${yy} q4 -1 3 -5" fill="none" ${S} stroke-width=".8"/>`; }
  o += `<path d="M177 6 V-2 M174 1 H180" ${S} stroke-width="1"/>`;
  for (let k = 0; k < 3; k++) { const yy = 66 - k * 20, dx = 27 * (1 - (78 - yy) / 72) * .45; o += `<path d="M${(177 - dx).toFixed(1)} ${yy + 8} L177 ${yy} L${(177 + dx).toFixed(1)} ${yy + 8}" fill="var(--paper)" ${S} stroke-width=".7"/>`; }
  const RT = [418, 508];
  o += `<rect x="${RT[0]}" y="128" width="90" height="${G - 128}" fill="var(--paper)" ${S} stroke-width="1.5"/>`;
  o += `<rect x="${RT[0] + 66}" y="128" width="24" height="${G - 128}" fill="url(#${sh})"/>`;
  o += lancet(463, 236, 30, 48, `url(#${dk})`) + lancet(463, 172, 24, 44, `url(#${dk})`);
  o += `<path d="M${RT[0]} 166 H${RT[1]} M${RT[0]} 226 H${RT[1]}" ${S} stroke-width="1"/>`;
  o += `<path d="M428 128 L463 16 L498 128 Z" fill="var(--paper)" ${S} stroke-width="1.4"/><path d="M463 16 L480 128 L498 128 Z" fill="url(#${sh})"/>`;
  for (let k = 1; k < 14; k++) { const yy = 128 - k * 8; const dx = 35 * (1 - k * 8 / 112); for (let j = -2; j <= 2; j++) { const xx = 463 + j * dx / 2.6; o += `<path d="M${(xx - 2.2).toFixed(1)} ${yy} q2.2 3 4.4 0" fill="none" ${S} stroke-width=".45" opacity=".55"/>`; } }
  o += `<path d="M455 112 L463 98 L471 112 Z" fill="var(--paper)" ${S} stroke-width=".8"/>${lancet(463, 102, 5, 10, `url(#${dk})`, .6)}`;
  o += pinnacle(424, 104, 10, 26) + pinnacle(502, 104, 10, 26) + `<path d="M463 16 V8 M460 11 H466" ${S} stroke-width="1"/>`;
  o += `<rect x="222" y="132" width="196" height="${G - 132}" fill="var(--paper)" ${S} stroke-width="1.5"/>`;
  o += `<path d="M234 132 L320 70 L406 132" fill="url(#${lt})" ${S} stroke-width="1.3"/>`;
  o += lancet(320, 98, 16, 22, `url(#${dk})`, .9);
  for (let x = 232; x <= 402; x += 17) o += lancet(x + 6, 136, 11, 18, `url(#${dk})`, .7);
  o += `<path d="M222 132 H418 M222 158 H418" ${S} stroke-width="1"/>`;
  const ry = 200, R = 36;
  o += `<circle cx="${cx}" cy="${ry}" r="${R + 5}" fill="var(--paper)" ${S} stroke-width="1.5"/><circle cx="${cx}" cy="${ry}" r="${R}" fill="url(#${xh})" ${S} stroke-width="1"/>`;
  for (let i = 0; i < 16; i++) {
    const t = i * Math.PI / 8;
    o += `<line x1="${cx + Math.cos(t) * 9}" y1="${ry + Math.sin(t) * 9}" x2="${(cx + Math.cos(t) * R).toFixed(1)}" y2="${(ry + Math.sin(t) * R).toFixed(1)}" ${S} stroke-width=".9"/>`;
    const tm = t + Math.PI / 16;
    o += `<circle cx="${(cx + Math.cos(tm) * 27).toFixed(1)}" cy="${(ry + Math.sin(tm) * 27).toFixed(1)}" r="4.6" fill="var(--paper)" ${S} stroke-width=".8"/>`;
    o += `<circle cx="${(cx + Math.cos(tm) * 16).toFixed(1)}" cy="${(ry + Math.sin(tm) * 16).toFixed(1)}" r="2.4" fill="var(--paper)" ${S} stroke-width=".6"/>`;
  }
  o += `<circle cx="${cx}" cy="${ry}" r="9" fill="var(--paper)" ${S} stroke-width="1"/><circle cx="${cx}" cy="${ry}" r="3.5" fill="currentColor"/>`;
  o += lancet(250, 176, 16, 58, `url(#${dk})`, .9) + lancet(390, 176, 16, 58, `url(#${dk})`, .9);
  const portal = (x, w, h) => {
    let s = ""; for (let k = 0; k < 3; k++) { const ww = w - k * 8, hh = h - k * 6; s += lancet(x, G - hh, ww, hh, k === 2 ? `url(#${dk})` : "var(--paper)", k ? 0.8 : 1.3); }
    return s + `<line x1="${x}" y1="${G - h + 18 + 12}" x2="${x}" y2="${G}" ${S} stroke-width=".8"/>`;
  };
  o += `<path d="M222 246 H418" ${S} stroke-width="1"/>`;
  o += portal(320, 52, 62) + portal(262, 34, 44) + portal(378, 34, 44);
  o += pinnacle(222, 104, 10, 30) + pinnacle(418, 108, 10, 26);
  o += `<path d="M60 ${G} V196 L96 160 L132 196 V${G}" fill="url(#${lt})" ${S} stroke-width="1"/>${lancet(96, 214, 18, 44, `url(#${dk})`, .8)}`;
  o += `<path d="M508 ${G} V204 L544 170 L580 204 V${G}" fill="url(#${lt})" ${S} stroke-width="1"/>${lancet(544, 220, 18, 40, `url(#${dk})`, .8)}`;
  o += pinnacle(60, 170, 9, 26) + pinnacle(580, 178, 9, 26);
  o += sketchLines(W, H);
  o += `<path d="M30 ${G} H610 M250 ${G + 3} H390" ${S} stroke-width="1.8"/><path d="M20 ${G + 5} H620" ${S} stroke-width=".6" opacity=".6"/>`;
  return o + `</svg>`;
}

// Four-centred (Tudor) arch with a vine and leaves: the "now" card of the board.
function tudor(x0, x1, sy, top, r) {
  const m = (x0 + x1) / 2; let best = null;
  for (let d = 20; d <= 89; d += .5) {
    const f = d * Math.PI / 180, cx = x0 + r, px = cx - Math.cos(f) * r, py = sy - Math.sin(f) * r, t = py - (m - px) * Math.cos(f) / Math.sin(f);
    if (!best || Math.abs(t - top) < Math.abs(best.t - top)) best = { f, px, py, t };
  }
  const { px, py, t } = best, qx = 2 * m - px;
  return {
    d: `M${x0} ${sy} A${r} ${r} 0 0 1 ${px.toFixed(2)} ${py.toFixed(2)} L${m} ${t.toFixed(2)} L${qx.toFixed(2)} ${py.toFixed(2)} A${r} ${r} 0 0 1 ${x1} ${sy}`,
    pt: (u) => {
      const arcLen = r * best.f, lineLen = Math.hypot(m - px, py - t), s = u * (arcLen + lineLen);
      if (s <= arcLen) { const a = s / r; return [x0 + r - Math.cos(a) * r, sy - Math.sin(a) * r, Math.atan2(-Math.cos(a), Math.sin(a))]; }
      const k = (s - arcLen) / lineLen; return [px + (m - px) * k, py + (t - py) * k, Math.atan2(t - py, m - px)];
    },
  };
}
function nowArch(w) {
  w = Math.max(220, Math.round(w)); const H = Math.round(Math.min(130, Math.max(90, w * .19))), id = `h${++_pid}`, m = w / 2, sy = H;
  const r1 = H * .72;
  const O = tudor(.75, w - .75, sy, .75, r1), I = tudor(10, w - 10, sy, 13, r1 - 9), V = tudor(22, w - 22, sy, 28, r1 - 20);
  const pts = []; for (let i = 0; i <= 30; i++) pts.push(V.pt(i / 30 * .9));
  const vine = "M" + pts.map(([x, y], i) => `${(x + Math.sin(i * 1.4) * 1.8).toFixed(1)} ${(y + Math.cos(i * 1.2) * 1.8).toFixed(1)}`).join(" L");
  const vineR = vine.replace(/(-?\d+\.?\d*) (-?\d+\.?\d*)/g, (s, x, y) => `${(w - x).toFixed(1)} ${y}`);
  const leaf = (x, y, a, s) => `<g transform="translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${a.toFixed(0)}) scale(${s.toFixed(2)})"><path d="M0 0 C2 -3 2 -7 0 -10 C3 -10 6 -9 7 -6 C10 -7 12 -5 11 -2 C13 0 12 3 9 3 C9 6 6 7 4 5 C3 7 0 6 0 3 Z" fill="var(--paper)" stroke="currentColor" stroke-width=".7"/><path d="M0 0 L8 -3 M3 -1 L5 -7 M5 -2 L10 1" stroke="currentColor" stroke-width=".45" fill="none"/></g>`;
  let leaves = "";
  for (let i = 0; i < 8; i++) { const [x, y, a] = V.pt(.04 + i * .105); const tan = a * 180 / Math.PI, side = i % 2 ? 1 : -1, s = 1.15 - i * .05; leaves += leaf(x, y, tan + side * 70, s) + leaf(w - x, y, 180 - tan - side * 70, s); }
  const cy = H * .56, r = Math.min(H * .2, 20);
  const tref = [0, 1, 2].map((k) => { const a = -Math.PI / 2 + k * 2 * Math.PI / 3; return `<circle cx="${(m + Math.cos(a) * r * .42).toFixed(1)}" cy="${(cy + Math.sin(a) * r * .42).toFixed(1)}" r="${(r * .42).toFixed(1)}" fill="var(--paper)" stroke="currentColor" stroke-width=".9"/>`; }).join("");
  return `<svg class="arch" viewBox="0 0 ${w} ${H}" width="100%" height="${H}" aria-hidden="true"><defs>${hatchDefs(id, -45, 4, .7)}</defs>
   <path d="M0 ${H} V0 H${w} V${H} Z ${O.d} Z" fill="url(#${id})" fill-rule="evenodd" opacity=".55"/>
   <path d="${O.d}" fill="none" stroke="currentColor" stroke-width="1.5"/>
   <path d="${I.d}" fill="none" stroke="currentColor" stroke-width=".8" opacity=".6"/>
   <path d="${vine}" fill="none" stroke="currentColor" stroke-width=".9" opacity=".7" stroke-linejoin="round" stroke-linecap="round"/>
   <path d="${vineR}" fill="none" stroke="currentColor" stroke-width=".9" opacity=".7" stroke-linejoin="round" stroke-linecap="round"/>
   ${leaves}
   <circle cx="${m}" cy="${cy}" r="${r + 3}" fill="var(--paper)" stroke="currentColor" stroke-width="1.2"/>
   <circle cx="${m}" cy="${cy}" r="${r}" fill="url(#${id})" stroke="currentColor" stroke-width=".6"/>${tref}</svg>`;
}
let archRO = null;
function mountArch(card) {
  const holder = document.createElement("div"); holder.className = "archholder"; card.prepend(holder);
  const draw = () => { const w = card.getBoundingClientRect().width || 360; if (holder._w === Math.round(w)) return; holder._w = Math.round(w); holder.innerHTML = nowArch(w); };
  draw(); requestAnimationFrame(draw);
  if (archRO) archRO.disconnect();
  if (window.ResizeObserver) { archRO = new ResizeObserver(draw); archRO.observe(card); }
}

// ── geometry ────────────────────────────────────────────────────────────────
const C = 500, RMAX = 438, RMIN = 50;
const radiusOf = (score) => RMIN + (1 - clamp(score, 0, 100) / 100) * (RMAX - RMIN - 24);
const scoreAt = (r) => clamp((1 - (r - RMIN) / (RMAX - RMIN - 24)) * 100, 0, 100);
const rad = (deg) => (deg * Math.PI) / 180;
const norm360 = (d) => ((d % 360) + 360) % 360;
const P = (a, r) => [C + r * Math.cos(rad(a)), C + r * Math.sin(rad(a))];
const pt = (a, r) => P(a, r).map((v) => v.toFixed(1)).join(" ");
function hash(str) {                       // FNV-1a -> [0,1)
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return ((h >>> 0) % 10007) / 10007;
}

// ── state ───────────────────────────────────────────────────────────────────
const S = {
  data: null, deals: [], byId: new Map(), visible: [], sectors: [],
  view: store.get("view", "scope"), selectedId: null, firstPaint: true,
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
  if (mins < 90) return Math.round(mins) + " minutes ago";
  if (mins < 2880) return Math.round(mins / 60) + " hours ago";
  return Math.round(mins / 1440) + " days ago";
}
function renderStatus() {
  const { counts, runs } = S.data, run = runs[0];
  const qual = S.deals.filter((d) => d.qualifies).length;
  let sweep = '<span class="warn">no sweeps recorded</span>';
  if (run) {
    const stale = Date.now() - new Date(run.startedAt).getTime() > 36 * 3600e3;
    const bad = run.status !== "ok" || stale;
    sweep = `Last swept <span class="${bad ? "warn" : "ok"}">${ago(run.startedAt)}${run.status !== "ok" ? " (" + esc(run.status) + ")" : ""}</span>`;
  }
  $("#status").innerHTML = `${sweep}<br><b>${counts.active.toLocaleString()}</b> listed · <b>${S.deals.length}</b> weighed · <b>${qual}</b> clear the bar`;
  $("#n-qualify").textContent = qual;
}
function chip(label, count, pressed, onClick, dot) {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button"; b.setAttribute("aria-pressed", pressed);
  b.innerHTML = `${dot ? "<i></i>" : ""}${esc(label)} <em>${count}</em>`;
  b.addEventListener("click", onClick);
  return b;
}
const toggle = (set, v) => (set.has(v) ? set.delete(v) : set.add(v));
function renderRail() {
  const count = (key, val) => S.deals.filter((d) => d[key] === val).length;
  const sec = $("#sector-chips"); sec.replaceChildren();
  for (const s of S.sectors) sec.append(chip(s, count("family", s), S.f.sectors.has(s), () => { toggle(S.f.sectors, s); refresh(); }));
  const src = $("#source-chips"); src.replaceChildren();
  for (const s of [...new Set(S.deals.map((d) => d.source))].sort()) src.append(chip(s, count("source", s), S.f.sources.has(s), () => { toggle(S.f.sources, s); refresh(); }));
  const mk = $("#mark-chips"); mk.replaceChildren();
  for (const m of ["saved", "contacted", "dismissed"]) {
    mk.append(chip(m, count("mark", m), S.f.mark === m, () => {
      S.f.mark = S.f.mark === m ? null : m;
      if (m === "dismissed" && S.f.mark) { S.f.hideDismissed = false; $("#t-dismissed").checked = false; }
      refresh();
    }));
  }
  $("#v-landed").textContent = S.f.maxLanded === Infinity ? "any" : money(S.f.maxLanded);
  ["#f-landed", "#f-risk", "#f-score"].forEach((s) => syncSlider($(s)));
}
function syncSlider(input) { input.style.setProperty("--fill", ((input.value - input.min) / (input.max - input.min)) * 100 + "%"); }

// ── the rose window ─────────────────────────────────────────────────────────
const scope = $("#scope");
scope.insertAdjacentHTML("afterbegin", `<defs>
  <pattern id="hm" width="3.4" height="3.4" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><line x1="0" y1="0" x2="0" y2="3.4" style="stroke:var(--graphite)" stroke-width=".9"/></pattern>
  <pattern id="hh" width="3.4" height="3.4" patternUnits="userSpaceOnUse"><path d="M0 0L3.4 3.4M3.4 0L0 3.4" style="stroke:var(--graphite)" stroke-width=".7"/></pattern>
  <pattern id="hw" width="3.4" height="3.4" patternUnits="userSpaceOnUse"><path d="M0 0L3.4 3.4M3.4 0L0 3.4" style="stroke:var(--warn)" stroke-width=".8"/></pattern>
  <pattern id="lite" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><line x1="0" y1="0" x2="0" y2="7" style="stroke:var(--graphite)" stroke-opacity=".16" stroke-width=".8"/></pattern>
</defs>`);
const gGrid = sv("g", {}, scope);
const gBlips = sv("g", { id: "blips" }, scope);
const gInk = sv("g", { class: "ink" }, scope);

function sectorGeometry() { const n = S.sectors.length, span = 360 / n; return { n, span, start: (i) => -90 + i * span }; }

function drawGrid() {
  gGrid.replaceChildren();
  const { span, start } = sectorGeometry();
  const R0 = RMAX + 9, R1 = RMAX + 38;
  // petals: faintly hatched wedges, so each product family reads as one light of the window
  S.sectors.forEach((name, i) => {
    const a0 = start(i) + 1, a1 = start(i) + span - 1, ri = RMIN - 2, ro = RMAX - 2;
    sv("path", { class: "petal", "data-petal": name,
      d: `M${pt(a0, ri)} L${pt(a0, ro)} A${ro} ${ro} 0 0 1 ${pt(a1, ro)} L${pt(a1, ri)} A${ri} ${ri} 0 0 0 ${pt(a0, ri)} Z` }, gGrid);
  });
  // rings
  for (const sc of [80, 60, 40, 20]) {
    sv("circle", { cx: C, cy: C, r: radiusOf(sc), class: "ring" + (sc === 60 ? " major" : "") }, gGrid);
    const t = sv("text", { x: C + 6, y: C - radiusOf(sc) - 4, class: "ring-label" }, gGrid); t.textContent = sc;
  }
  // rim with dentils
  sv("circle", { cx: C, cy: C, r: RMAX, class: "rim", "stroke-width": 1.6 }, gGrid);
  sv("circle", { cx: C, cy: C, r: RMAX + 8, class: "rim", "stroke-width": .8 }, gGrid);
  for (let a = 0; a < 360; a += 4) { const [x1, y1] = P(a, RMAX), [x2, y2] = P(a, RMAX + 8); sv("line", { x1, y1, x2, y2, class: "dentil" }, gGrid); }
  // mullions (double lines) and the tracery nodes where they cross the rings
  S.sectors.forEach((_, i) => {
    const a = start(i);
    for (const [da, cls] of [[-.4, "mullion"], [.4, "mullion thin"]]) {
      const [x1, y1] = P(a + da, RMIN - 2), [x2, y2] = P(a + da, RMAX);
      sv("line", { x1, y1, x2, y2, class: cls }, gGrid);
    }
    for (const sc of [80, 60, 40, 20]) { const [x, y] = P(a, radiusOf(sc)); sv("circle", { cx: x, cy: y, r: 3.6, class: "node" }, gGrid); }
  });
  // cusped arches around the rim, one per light, with the family name above
  S.sectors.forEach((name, i) => {
    const a0 = start(i) + 2.2, a1 = start(i) + span - 2.2, mid = start(i) + span / 2;
    const arch = (r0, r1, da) => `M${pt(a0 + da, r0)} Q${pt(a0 + da, r0 + (r1 - r0) * .95)} ${pt(mid, r1)} Q${pt(a1 - da, r0 + (r1 - r0) * .95)} ${pt(a1 - da, r0)} A${r0} ${r0} 0 0 0 ${pt(a0 + da, r0)} Z`;
    sv("path", { d: arch(R0, R1, 0), class: "arch-o" }, gGrid);
    sv("path", { d: arch(R0 + 3, R1 - 7, 1.4), class: "arch-i" }, gGrid);
    const [x, y] = P(mid, R1 + 12), cs = Math.cos(rad(mid)), sn = Math.sin(rad(mid));
    const anchor = Math.abs(cs) < 0.25 ? "middle" : cs > 0 ? "start" : "end";
    const lab = sv("text", { x, y: y + (sn < -0.5 ? -6 : sn > 0.5 ? 20 : 5), "text-anchor": anchor, class: "sector-label", "data-sector": name }, gGrid);
    sv("tspan", { class: "nm" }, lab).textContent = name;
    sv("tspan", { class: "sector-count", dx: 8 }, lab);                // "· 16 in range", filled by renderBlips
  });
  // the hub
  sv("circle", { cx: C, cy: C, r: RMIN - 12, class: "core" }, gGrid);
  sv("circle", { cx: C, cy: C, r: RMIN - 20, class: "core-in" }, gGrid);
  sv("path", { d: `M${C} ${C - 11} V${C + 11} M${C - 7} ${C - 3} H${C + 7}`, stroke: "currentColor", "stroke-width": 1.4, fill: "none" }, gGrid);
}

function blipPos(d) {
  const { span, start } = sectorGeometry();
  const idx = Math.max(0, S.sectors.indexOf(d.family));
  const a = start(idx) + 3.2 + hash(d.id) * (span - 6.4);
  const r = radiusOf(d.score) + (hash(d.id + "r") - 0.5) * 9;
  const [x, y] = P(a, r);
  return { a, r, x, y };
}
const blipRadius = (d) => clamp(3.6 + Math.sqrt(d.landed) * 0.15, 4.4, 15);

function renderBlips() {
  gBlips.replaceChildren(); blipNodes.clear();
  const frag = document.createDocumentFragment();
  let i = 0;
  for (const d of [...S.visible].reverse()) {          // best last, so they sit on top
    const p = blipPos(d), r = blipRadius(d);
    const g = sv("g", { class: "blip" + (S.firstPaint && !reduceMotion ? " enter" : ""), "data-id": d.id });
    if (S.firstPaint && !reduceMotion) g.style.animationDelay = Math.min(900, i * 4) + "ms";
    i++;
    if (d.qualifies) sv("circle", { cx: p.x, cy: p.y, r: r + 6, class: "halo" }, g);
    const high = d.riskLabel === "high";
    sv("circle", { cx: p.x, cy: p.y, r, class: "dot", style: `fill:var(--paper);stroke:${high ? "var(--warn)" : "var(--graphite)"};stroke-width:1.6` }, g);
    if (d.riskLabel !== "low") sv("circle", { cx: p.x, cy: p.y, r: r - .6, fill: high ? "url(#hw)" : "url(#hm)", "pointer-events": "none" }, g);
    if (d.qualifies) sv("circle", { cx: p.x, cy: p.y, r: r + 4, class: "bar" }, g);
    if (d.ageHours < 24 && !reduceMotion) sv("circle", { cx: p.x, cy: p.y, r: r + 2, class: "breathe-ring" }, g);
    if (d.mark === "saved") sv("path", { d: `M${p.x - 3} ${p.y} L${p.x - 1} ${p.y + 3} L${p.x + 4} ${p.y - 3}`, fill: "none", style: "stroke:var(--graphite);stroke-width:2;stroke-linecap:round;stroke-linejoin:round", "pointer-events": "none" }, g);
    if (d.mark === "contacted") sv("circle", { cx: p.x, cy: p.y, r: Math.max(1.6, r * .35), style: "fill:var(--graphite)", "pointer-events": "none" }, g);
    frag.append(g);
    blipNodes.set(d.id, { g, p });
  }
  gBlips.append(frag);
  S.firstPaint = false;
  $("#scope-empty").hidden = S.visible.length > 0;
  for (const s of S.sectors) {
    const n = S.visible.filter((d) => d.family === s).length;
    const cnt = gGrid.querySelector(`[data-sector="${CSS.escape(s)}"] .sector-count`);
    if (cnt) cnt.textContent = n ? String(n) : "";
    gGrid.querySelector(`[data-sector="${CSS.escape(s)}"]`)?.classList.toggle("on", S.f.sectors.has(s));
    gGrid.querySelector(`[data-petal="${CSS.escape(s)}"]`)?.classList.toggle("on", S.f.sectors.has(s));
  }
  paintSelection();
}

function paintSelection() {
  gInk.replaceChildren();
  const sel = S.byId.get(S.selectedId);
  for (const [id, n] of blipNodes) {
    const d = S.byId.get(id);
    n.g.classList.toggle("dim", !!sel && id !== sel.id && d.product !== sel.product);
    n.g.classList.toggle("peer", !!sel && id !== sel.id && d.product === sel.product);
  }
  const node = sel && blipNodes.get(sel.id);
  if (!node) return;
  const { x, y } = node.p, r = blipRadius(sel);
  sv("line", { x1: C, y1: C, x2: x, y2: y, class: "bearing" }, gInk);
  const ringA = r + 10, ringB = r + 15;
  sv("circle", { cx: x, cy: y, r: ringA, class: "ring-a", style: `--len:${(2 * Math.PI * ringA).toFixed(0)}` }, gInk);
  sv("circle", { cx: x, cy: y, r: ringB, class: "ring-b", style: `--len:${(2 * Math.PI * ringB).toFixed(0)}` }, gInk);
  for (const a of [0, 90, 180, 270]) {
    const [x1, y1] = [x + Math.cos(rad(a)) * (ringB + 2), y + Math.sin(rad(a)) * (ringB + 2)];
    const [x2, y2] = [x + Math.cos(rad(a)) * (ringB + 8), y + Math.sin(rad(a)) * (ringB + 8)];
    sv("line", { x1, y1, x2, y2, class: "tick" }, gInk);
  }
}

// pointer interaction -------------------------------------------------------
const tip = $("#tip"), readout = $("#readout"), wrap = $("#scope-wrap");
function toScope(evt) {
  const p = scope.createSVGPoint(); p.x = evt.clientX; p.y = evt.clientY;
  return p.matrixTransform(scope.getScreenCTM().inverse());
}
scope.addEventListener("pointermove", (evt) => {
  const p = toScope(evt), dx = p.x - C, dy = p.y - C, r = Math.hypot(dx, dy);
  if (r < RMAX + 6 && r > RMIN - 10) {
    const { span, n } = sectorGeometry();
    const idx = Math.floor(norm360(Math.atan2(dy, dx) * 180 / Math.PI + 90) / span) % n;
    readout.innerHTML = `toward the <b>${esc(S.sectors[idx] || "")}</b> light, score about <b>${Math.round(scoreAt(r))}</b>`;
  } else readout.innerHTML = "";
  const hit = evt.target.closest?.(".blip");
  if (hit) showTip(S.byId.get(hit.dataset.id), evt); else tip.hidden = true;
});
scope.addEventListener("pointerleave", () => { tip.hidden = true; readout.innerHTML = ""; });
scope.addEventListener("click", (evt) => {
  const lab = evt.target.closest?.(".sector-label");
  if (lab) { toggle(S.f.sectors, lab.dataset.sector); refresh(); return; }
  const hit = evt.target.closest?.(".blip");
  if (hit) select(hit.dataset.id); else select(null);
});
function showTip(d, evt) {
  if (!d) return;
  tip.innerHTML = `<strong>${esc(d.title)}</strong><span><span class="n">${money(d.landed)}</span> landed · ${pct(d.discount)} under · ${esc(d.riskLabel)} risk · score ${d.score}</span>`;
  tip.hidden = false;
  const box = wrap.getBoundingClientRect();
  let x = evt.clientX - box.left + 16, y = evt.clientY - box.top + 16;
  if (x + 290 > box.width) x = evt.clientX - box.left - 296;
  if (y + 90 > box.height) y = evt.clientY - box.top - 84;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function drawLegend() {
  const o = (inner) => `<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="6" fill="var(--paper)" stroke="currentColor" stroke-width="1.5"/>${inner}</svg>`;
  $("#legend").innerHTML = [
    [o(""), "open: low risk"],
    [o('<circle cx="9" cy="9" r="5.4" fill="url(#hm)"/>'), "hatched: medium"],
    [`<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="6" fill="var(--paper)" style="stroke:var(--warn)" stroke-width="1.5"/><circle cx="9" cy="9" r="5.4" fill="url(#hw)"/></svg>`, "cross-hatched, red: high"],
    [o('<circle cx="9" cy="9" r="8.2" fill="none" stroke="currentColor" stroke-width="1"/>'), "double ring: clears the bar"],
    [`<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="6" fill="none" style="stroke:var(--wash)" stroke-width="1.4" stroke-dasharray="2 2"/></svg>`, "rippling: new today"],
    [`<svg viewBox="0 0 18 18"><circle cx="9" cy="9" r="3" fill="none" stroke="currentColor"/><circle cx="9" cy="9" r="7" fill="none" stroke="currentColor" opacity=".5"/></svg>`, "size: landed cost"],
  ].map(([svg, text]) => `<span>${svg}${text}</span>`).join("");
}

// ── ledger ──────────────────────────────────────────────────────────────────
const RISK_GLYPH = { low: "○", medium: "◐", high: "●" };
const COLS = [
  { key: "score", label: "Score", num: true, cell: (d) => `<span class="scorebar"><i style="width:${d.score}%"></i></span>${d.score}` },
  { key: "product", label: "Product", cell: (d) => esc(d.product) },
  { key: "title", label: "Listing", cls: "t", cell: (d) => esc(d.title) },
  { key: "landed", label: "Landed", num: true, cell: (d) => money(d.landed) },
  { key: "discount", label: "vs median", num: true, cell: (d) => `<span class="${d.discount > 0.2 ? "good" : "dim"}">${d.discount > 0 ? "−" : "+"}${pct(Math.abs(d.discount))}</span>` },
  { key: "profit", label: "Profit", num: true, cell: (d) => (d.profit > 0 ? money(d.profit) : '<span class="dim">–</span>') },
  { key: "risk", label: "Risk", num: true, cell: (d) => `<span class="rk ${d.riskLabel}">${RISK_GLYPH[d.riskLabel]}</span>${d.risk}` },
  { key: "fulfillment", label: "Ships", cell: (d) => ({ ships: "yes", unknown: "ask", pickup_only: "pickup" }[d.fulfillment] || "") },
  { key: "source", label: "Source", cell: (d) => esc(d.source) },
  { key: "ageHours", label: "Age", num: true, cell: (d) => (d.ageHours < 48 ? Math.round(d.ageHours) + "h" : Math.round(d.ageHours / 24) + "d") },
];
function renderLedger() {
  const table = $("#ledger"), { key, dir } = S.sort;
  const rows = [...S.visible].sort((a, b) => {
    const x = a[key], y = b[key];
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
  }).slice(0, 800);
  table.innerHTML = `<thead><tr><th></th>${COLS.map((c) => `<th data-k="${c.key}" class="${c.num ? "num " : ""}${c.key === key ? "sorted" : ""}">${c.label}${c.key === key ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((d) => `<tr data-id="${esc(d.id)}" class="${d.id === S.selectedId ? "sel" : ""}"><td class="mk">${{ saved: "✓", contacted: "✉", dismissed: "×" }[d.mark] || ""}</td>${COLS.map((c) => `<td class="${c.num ? "num " : ""}${c.cls || ""}">${c.cell(d)}</td>`).join("")}</tr>`).join("")}</tbody>`;
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
  const recent = S.data.runs.slice(0, 3).map((r) => `<div>${ago(r.startedAt)}: ${esc(r.status)}, ${r.new} new, ${r.alerts} alerts</div>`).join("");
  $("#dossier").innerHTML = `<div class="idle">
    ${sleepCatSVG()}
    <p class="lead">Nothing chosen yet.</p>
    <p>The nearer the centre, the better the deal. Click a listing, or begin with the strongest in range:</p>
    <ul class="top5">${top.map((d) => `<li><button data-id="${esc(d.id)}"><span class="sc">${d.score}</span><span class="tt">${esc(d.product)}<small>${esc(d.title)}</small></span><span class="pr">${money(d.landed)}</span></button></li>`).join("") || "<li><p>Nothing in range.</p></li>"}</ul>
    <div class="runs">${recent}</div></div>`;
  $$(".top5 button").forEach((b) => b.addEventListener("click", () => select(b.dataset.id)));
}

function lensSVG(d, band) {
  const W = 340, H = 100, pad = 16;
  const prices = band?.prices || [];
  if (prices.length < 3) return "<p class='rows'>Not enough comparable listings to draw a band.</p>";
  const q = (p) => prices[Math.min(prices.length - 1, Math.floor(p * (prices.length - 1)))];
  const lo = Math.min(q(0.02), d.landed, d.price) * 0.96;
  let hi = Math.max(q(0.98), d.landed) * 1.04;
  if (hi - lo < 1) hi = lo + 1;
  const x = (v) => pad + ((clamp(v, lo, hi) - lo) / (hi - lo)) * (W - 2 * pad);
  const bins = new Map(), dots = [];
  for (const p of prices) {
    if (p < lo || p > hi) continue;
    const bx = Math.round(x(p) / 5), k = bins.get(bx) || 0; bins.set(bx, k + 1);
    const off = Math.ceil(k / 2) * 4.6 * (k % 2 ? -1 : 1);
    dots.push(`<circle class="comp" cx="${x(p).toFixed(1)}" cy="${clamp(46 + off, 24, 68).toFixed(1)}" r="2.5"/>`);
  }
  const clipped = prices.filter((p) => p > hi).length;
  const lx = x(d.landed);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Where this listing's landed cost sits among ${band.n} comparable asking prices">
    <defs><pattern id="lens-h" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)"><line x1="0" y1="0" x2="0" y2="4" style="stroke:var(--graphite)" stroke-opacity=".35" stroke-width=".8"/></pattern></defs>
    <rect class="band" x="${x(band.p25)}" y="26" width="${Math.max(2, x(band.p75) - x(band.p25))}" height="40"/>
    ${dots.join("")}
    <line class="med" x1="${x(band.median)}" x2="${x(band.median)}" y1="22" y2="72"/>
    <text class="medtxt" x="${x(band.median)}" y="86">median ${money(band.median)}</text>
    <line class="axis" x1="${pad}" x2="${W - pad}" y1="72" y2="72"/>
    <text class="tick" x="${pad}" y="98" text-anchor="start">${money(lo)}</text>
    <text class="tick" x="${W - pad}" y="98" text-anchor="end">${money(hi)}${clipped ? " +" + clipped : ""}</text>
    <g class="me"><line x1="${lx}" x2="${lx}" y1="12" y2="72"/><path class="head" d="M${lx - 5} 12 L${lx} 2 L${lx + 5} 12 Z"/>
      <text x="${clamp(lx, 40, W - 40)}" y="-3">this one ${money(d.landed)}</text></g></svg>`;
}
function sparkSVG(history) {
  if (history.length < 2) return "<p>No price changes recorded yet. Every sweep from now on watches for them.</p>";
  const W = 340, H = 56, pad = 6;
  const prices = history.map((h) => h.price), times = history.map((h) => new Date(h.at).getTime());
  const lo = Math.min(...prices), hi = Math.max(...prices), t0 = times[0], t1 = times[times.length - 1] || t0 + 1;
  const px = (t) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (W - 2 * pad);
  const py = (p) => H - pad - ((p - lo) / Math.max(1, hi - lo)) * (H - 2 * pad);
  const path = history.map((h, i) => `${i ? "L" : "M"}${px(times[i]).toFixed(1)} ${py(h.price).toFixed(1)}`).join(" ");
  const last = history[history.length - 1], first = history[0];
  return `<svg viewBox="0 0 ${W} ${H}"><path class="ln" d="${path}"/>${history.map((h, i) => `<circle class="pt" cx="${px(times[i]).toFixed(1)}" cy="${py(h.price).toFixed(1)}" r="3"/>`).join("")}</svg>
    <p>${money(first.price)} to ${money(last.price)} (${last.price < first.price ? "down" : "up"} ${money(Math.abs(last.price - first.price))})</p>`;
}
function messageFor(d) {
  const postal = S.postal ? S.postal.toUpperCase() : "[your postal code]";
  if (d.source === "reddit") {
    return `Hi! I'm interested in your ${d.product} for ${money(d.price)}. Could you ship it to ${postal}? I'd pay by PayPal Goods & Services. Is it still available, and could you share a timestamp photo? Thanks!`;
  }
  const pay = /paypal_gs/.test(d.payment) ? "PayPal Goods & Services" : "e-transfer (or PayPal G&S if you prefer)";
  return `Hi! Is your "${d.title.slice(0, 90)}" still available? I'm not able to pick up in person. Would you be willing to ship it to ${postal}? I'm happy to pay for tracked, insured shipping and can pay by ${pay}. Could you let me know the shipping cost and send a photo of it with today's date? Thanks!`;
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
  const ship = { ships: ["", "Seller ships"], unknown: ["ask", "Not stated, so ask"], pickup_only: ["no", "Pickup only"] }[d.fulfillment];
  const route = { use: "to keep", flip: "to flip", both: "to keep or flip", none: "near miss" }[d.route];
  const resale = d.profit > 0;
  const rating = d.rating != null ? `${d.rating.toFixed(1)}%${d.reviews != null ? ", " + d.reviews + " ratings" : ""}` : null;
  const protection = /ebay_protection/.test(d.payment) ? "eBay Money Back Guarantee" : /paypal_gs/.test(d.payment) ? "PayPal Goods & Services" : null;

  $("#dossier").innerHTML = `<div class="doc">
    <div class="now"><div class="nowbody">
      <div class="tags">
        <span class="tag ${d.qualifies ? "dark" : "out"}">${d.qualifies ? "clears the bar" : "near miss"}</span>
        <span class="tag ${d.riskLabel === "high" ? "warn" : ""}">${esc(d.riskLabel)} risk</span>
        <span class="tag">${esc(route)}</span><span class="tag">${esc(d.source)}</span>
        ${d.ageHours < 24 ? '<span class="tag">new today</span>' : ""}${d.priceDrop ? '<span class="tag">price dropped</span>' : ""}
      </div>
      <h3>${esc(d.title)}</h3>
      <div class="prod">${esc(d.product)} · ${esc(d.family)}${d.location ? " · " + esc(d.location) : ""}</div>
      <div class="hero"><div class="big"><sup>$</sup><span id="hero-n">0</span></div>
        <div class="delta ${d.discount > 0.15 ? "" : "bad"}">${d.discount >= 0 ? "−" : "+"}${pct(Math.abs(d.discount))} against the median</div>
        <div class="sub">landed cost · score ${d.score} of 100 · ${d.comps} comparable listings</div></div>
    </div></div>
    <div class="card lens"><h4>Where it sits <span>among ${band ? band.n : d.comps} comparable asks</span></h4>${lensSVG(d, band)}</div>
    <div class="card"><h4>The cost</h4><div class="rows">
      <div><span>Asking price</span><span>${money(d.price)}</span></div>
      <div><span>Shipping ${d.shippingStated ? "(stated)" : "(estimate)"}</span><span>${money(d.shipping)}</span></div>
      <div class="total"><span>Landed</span><span>${money(d.landed)}</span></div>
      <div><span>Typical ask (median)</span><span>${money(d.median)}</span></div>
      <div><span>If you resold it</span><span>${resale ? "about " + money(d.profit) + ", " + pct(d.roi) + " return" : "no profit after fees"}</span></div>
      <div class="est"><span>Fees, return shipping and a loss reserve are counted. Asking prices are not sale prices.</span></div></div></div>
    <div class="card"><h4>Risk <span>${d.risk} of 100</span></h4>
      <div class="meter ${d.risk >= 55 ? "hi" : ""}"><b style="width:${d.risk}%"></b></div>
      <div class="meter-scale"><span>safe</span><span>55 is high</span><span>scam-shaped</span></div>
      <ul class="reasons">${(d.riskReasons.length ? d.riskReasons : ["No red flags found"]).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>
    <div class="card spark" id="spark"><h4>Price history</h4><p>Reading the ledger…</p></div>
    <div class="card"><h4>The seller</h4><div class="kv">
      <div><span>Shipping</span><span class="${ship[0]}">${ship[1]}</span></div>
      ${protection ? `<div><span>Protection</span><span>${esc(protection)}</span></div>` : ""}
      ${rating ? `<div><span>Rating</span><span>${esc(rating)}</span></div>` : ""}
      ${d.condition ? `<div><span>Condition</span><span>${esc(d.condition)}</span></div>` : ""}
      ${d.seller ? `<div><span>Seller</span><span>${esc(String(d.seller).slice(0, 18))}</span></div>` : ""}</div>
      ${d.description ? `<p class="desc" id="desc">${esc(d.description)}</p>` : ""}</div>
    <div class="actions">
      <a class="btn primary" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">Open the listing</a>
      <button class="btn" id="b-msg" title="M">Copy the note</button>
      <button class="btn" id="b-saved" aria-pressed="${d.mark === "saved"}" title="S">Save</button>
      <button class="btn" id="b-contacted" aria-pressed="${d.mark === "contacted"}" title="C">Contacted</button>
      <button class="btn danger" id="b-dismissed" aria-pressed="${d.mark === "dismissed"}" title="D">Dismiss</button>
    </div></div>`;
  mountArch($(".now"));
  countUp($("#hero-n"), d.landed);
  $("#desc")?.addEventListener("click", (e) => e.currentTarget.classList.toggle("open"));
  $("#b-msg").addEventListener("click", () => copyMessage(d));
  for (const m of ["saved", "contacted", "dismissed"]) $("#b-" + m).addEventListener("click", () => mark(d.id, d.mark === m ? null : m));
  try {
    const { history } = await (await fetch("/api/history/" + encodeURIComponent(d.id))).json();
    if (S.selectedId === d.id) $("#spark").innerHTML = "<h4>Price history</h4>" + sparkSVG(history);
  } catch { /* offline: leave the placeholder */ }
}

// ── actions ─────────────────────────────────────────────────────────────────
let toastTimer;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.hidden = true), 2400);
}
async function copyMessage(d) {
  const text = messageFor(d);
  try { await navigator.clipboard.writeText(text); toast("Note copied. Paste it to the seller."); }
  catch {
    const ta = document.createElement("textarea"); ta.value = text; document.body.append(ta); ta.select();
    try { document.execCommand("copy"); toast("Note copied."); } catch { toast("Couldn't copy."); }
    ta.remove();
  }
  if (!S.postal) setTimeout(() => toast("Tip: add your postal code on the left to fill it in."), 2600);
}
async function mark(id, value) {
  const d = S.byId.get(id); if (!d) return;
  const prev = d.mark; d.mark = value;
  try {
    const res = await fetch("/api/mark", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, mark: value }) });
    if (!res.ok) throw new Error();
  } catch { d.mark = prev; toast("Couldn't save that."); return; }
  toast(value ? { saved: "Saved.", contacted: "Marked contacted.", dismissed: "Dismissed." }[value] : "Mark cleared.");
  if (value === "dismissed" && S.f.hideDismissed) { const next = neighbour(1) || neighbour(-1); S.selectedId = next ? next.id : null; }
  refresh();
}

// ── selection + navigation ──────────────────────────────────────────────────
function select(id) {
  S.selectedId = id && S.byId.has(id) ? id : null;
  paintSelection(); renderDossier();
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
  $$(".seg button").forEach((b) => { const on = b.dataset.view === view; b.setAttribute("aria-selected", on); b.setAttribute("aria-checked", on); });
  $("#scope-wrap").hidden = view !== "scope";
  $("#ledger-wrap").hidden = view !== "ledger";
  if (view === "ledger" && S.data) renderLedger();
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
  $("#theme").addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    root.dataset.theme = next; store.set("theme", next); syncThemeButton();
  });
  $("#refresh").addEventListener("click", async () => {
    const btn = $("#refresh"); btn.classList.add("busy");
    try { await load(true); renderStatus(); refresh(); toast("Re-weighed with the latest data."); }
    catch { toast("Couldn't refresh."); } finally { btn.classList.remove("busy"); }
  });
  $("#search").addEventListener("input", (e) => { S.f.q = e.target.value; refresh(); });
  const landed = $("#f-landed"), risk = $("#f-risk"), score = $("#f-score");
  landed.addEventListener("input", () => {
    const v = +landed.value; S.f.maxLanded = v >= 100 ? Infinity : Math.max(50, Math.round((v / 100) * S.landedCap / 50) * 50);
    refresh();
  });
  risk.addEventListener("input", () => { S.f.maxRisk = +risk.value; $("#v-risk").textContent = risk.value >= 100 ? "any" : risk.value; refresh(); });
  score.addEventListener("input", () => { S.f.minScore = +score.value; $("#v-score").textContent = score.value; refresh(); });
  $("#v-risk").textContent = "any"; $("#v-score").textContent = S.f.minScore;
  $("#t-qualify").addEventListener("change", (e) => { S.f.qualifying = e.target.checked; refresh(); });
  $("#t-ship").addEventListener("change", (e) => { S.f.shippable = e.target.checked; refresh(); });
  $("#t-dismissed").addEventListener("change", (e) => { S.f.hideDismissed = e.target.checked; refresh(); });
  $("#t-suspicious").addEventListener("change", (e) => { S.f.suspicious = e.target.checked; refresh(); });
  $("#reset-filters").addEventListener("click", () => {
    Object.assign(S.f, { sectors: new Set(), sources: new Set(), maxLanded: Infinity, maxRisk: 100, minScore: 0, qualifying: false, shippable: false, hideDismissed: false, suspicious: true, mark: null, q: "" });
    $("#search").value = ""; landed.value = 100; risk.value = 100; score.value = 0;
    $("#t-qualify").checked = false; $("#t-ship").checked = false; $("#t-dismissed").checked = false; $("#t-suspicious").checked = true;
    $("#v-risk").textContent = "any"; $("#v-score").textContent = "0"; refresh();
  });
  const postal = $("#postal"); postal.value = S.postal;
  postal.addEventListener("input", () => { S.postal = postal.value.trim(); store.set("postal", S.postal); });
}

async function boot() {
  syncThemeButton();
  $("#facade").innerHTML = washSVG() + facadeSVG();
  $("#splash-art").innerHTML = facadeSVG();
  $("#empty-cat").innerHTML = sleepCatSVG();
  bindControls(); setView(S.view);
  try { await load(); }
  catch {
    $("#splash-text").textContent = "Couldn't reach the server. Is `python -m app.web` running?";
    return;
  }
  drawGrid(); drawLegend(); renderStatus(); refresh();
  $("#splash").classList.add("done");
  setInterval(async () => {                       // pick up new sweeps without a reload
    try {
      await load(); renderStatus();
      const keep = S.selectedId; applyFilters(); renderRail(); renderBlips();
      if (keep && S.byId.has(keep)) { S.selectedId = keep; paintSelection(); }
      if (S.view === "ledger") renderLedger();
    } catch { /* try again later */ }
  }, 5 * 60 * 1000);
}
boot();
