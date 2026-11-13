/* FTC AutoScout web front end.
   The numbers, tables and chart data all come from the desktop AutoScout.py,
   which runs in worker.js. This file only lays them out and sends button
   presses back. Plain JavaScript, no build step. */
"use strict";

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
const $ = (sel, el = document) => el.querySelector(sel);
const PROPS = new Set(["value", "checked", "disabled", "selected", "htmlFor", "type", "min", "max", "step", "placeholder"]);

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (PROPS.has(k)) el[k] = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : String(kid));
  }
  return el;
}

function btn(text, onclick, cls = "") { return h("button", { class: "btn " + cls, type: "button", onclick }, text); }
function card(title, ...kids) { return h("section", { class: "card" }, title ? h("h3", { text: title }) : null, ...kids); }
function plainCard(title, ...kids) { return h("section", { class: "card" }, title ? h("h3", { class: "plain", text: title }) : null, ...kids); }
function field(label, input, cls = "") { return h("label", { class: "field " + cls }, h("span", { text: label }), input); }
function check(label, checked, onchange) {
  const input = h("input", { type: "checkbox", checked: !!checked, onchange: (e) => onchange(e.target.checked) });
  return h("label", { class: "check" }, input, h("span", { text: label }));
}
function select(options, value, onchange) {
  const el = h("select", { onchange: (e) => onchange(e.target.value) });
  for (const o of options) {
    const [v, lab] = Array.isArray(o) ? o : [o, o];
    el.append(h("option", { value: v, selected: v === value }, lab));
  }
  return el;
}
function textInput(value, attrs = {}) { return h("input", { type: "text", value: value ?? "", ...attrs }); }
function metricsRow(list) {
  return h("div", { class: "metrics" }, list.map((m) => h("div", { class: "metric" },
    h("div", { class: "lab", text: m.label }), h("div", { class: "val", text: m.value || "—" }))));
}
function hero(kicker, title, sub) {
  return h("div", { class: "hero" }, h("div", { class: "kicker", text: kicker }), h("h2", { text: title }), sub ? h("p", { text: sub }) : null);
}
function isPhone() { return window.matchMedia("(max-width: 860px)").matches; }

let toastTimer = null;
function toast(msg, err = false) {
  if (!msg) return;
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, err ? 6000 : 3200);
}

function download(name, text, mime) {
  const blob = new Blob([text], { type: mime + ";charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

// ---------------------------------------------------------------------------
// Worker connection
// ---------------------------------------------------------------------------
const worker = new Worker("worker.js");
let seq = 0;
const pending = new Map();
worker.onmessage = (e) => {
  const m = e.data;
  if (m.type === "status") { const s = $("#splashText"); if (s) s.textContent = m.text; return; }
  if (m.type === "progress") { const p = pending.get(m.id); if (p && p.onProgress) p.onProgress(m.msg, m.frac); return; }
  const p = pending.get(m.id);
  if (!p) return;
  pending.delete(m.id);
  if (m.error) { if (m.detail) console.error(m.detail); p.reject(new Error(m.error)); }
  else p.resolve(m.result);
};
worker.onerror = (e) => { bootFailed(e.message || "The AutoScout engine could not start."); };

function call(kind, name, args, onProgress) {
  return new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject, onProgress });
    worker.postMessage({ id, kind, name, args });
  });
}
async function view(page, opts) {
  const d = await call("view", page, opts || {});
  setHead(d.head);
  return d;
}
async function act(name, args, onProgress) {
  const d = await call("action", name, args || {}, onProgress);
  setHead(d.head);
  return d;
}

// ---------------------------------------------------------------------------
// App state, pages and navigation
// ---------------------------------------------------------------------------
const S = {
  boot: null, head: null, page: "overview", params: {}, sort: {}, loading: false,
  comp: {}, panel: {}, all: {}, pairs: {}, chartsPage: "team", live: {}, research: { results: [], profile: null, season: "", query: "" },
  eventSearch: { query: "", results: [], status: "" },
  auto: { on: false, minutes: 2, timer: null },
};

const PAGES = [
  ["WORKSPACE", [["overview", "Overview", "⌂", "Competition command center"],
    ["competition", "Competition Day", "\u2691\uFE0E", "Live event operations and match desk"],
    ["setup", "Setup", "◈", "Event and session setup"],
    ["teams", "Teams", "▦", "Your event team database"],
    ["research", "Team Research", "⌕", "Public FTC team intelligence"],
    ["matches", "Matches", "\u26A1\uFE0E", "Match schedule and predictions"],
    ["myteam", "My Team", "\u2605\uFE0E", "Your team's performance"]]],
  ["STRATEGY", [["standings", "Standings", "☷", "Event standings"],
    ["alliances", "Alliances", "◇", "Alliance selection workspace"],
    ["winrate", "Win Rate Curve", "⌁", "Alliance win-rate modeling"],
    ["analysis", "Analysis", "◉", "Prediction quality and insights"]]],
  ["INSIGHTS", [["charts", "Charts", "▥", "Performance visualizations"],
    ["live", "Live Scouting", "●", "Live match observation workspace"]]],
  ["TOOLS", [["settings", "Settings", "\u2699\uFE0E", "Preferences and data controls"]]],
];
const PAGE = {};
for (const [, items] of PAGES) for (const [id, title, icon, ctx] of items) PAGE[id] = { id, title, icon, ctx };

function buildNav() {
  const nav = $("#nav");
  nav.textContent = "";
  for (const [group, items] of PAGES) {
    nav.append(h("div", { class: "navgroup" + (group === "TOOLS" ? " tools" : "") }, h("h4", { text: group }),
      h("div", { class: "items" }, items.map(([id, title, icon]) =>
        h("a", { class: "navlink", href: "#/" + id, "data-page": id, onclick: closeMenu },
          h("span", { class: "ic", "aria-hidden": "true", text: icon }), title)))));
  }
}
function markNav() {
  for (const a of document.querySelectorAll(".navlink")) a.classList.toggle("active", a.dataset.page === S.page);
}
function openMenu() { $("#nav").classList.add("open"); $("#scrim").hidden = false; $("#menuBtn").setAttribute("aria-expanded", "true"); }
function closeMenu() { $("#nav").classList.remove("open"); $("#scrim").hidden = true; $("#menuBtn").setAttribute("aria-expanded", "false"); }

function setHead(head) {
  if (!head) return;
  S.head = head;
  const pill = $("#eventPill");
  if (head.loaded || head.matches) {
    pill.hidden = false;
    pill.textContent = "";
    pill.append(h("b", { text: head.event || head.code }), `  ·  ${head.season}/${head.code}`,
      head.my_team ? `  ·  team ${head.my_team}` : "", `  ·  ${head.basis}`);
  } else {
    pill.hidden = true;
  }
}

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [page, query] = raw.split("?");
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  return { page: PAGE[page] ? page : "overview", params };
}
function go(page, params) {
  const q = params ? "?" + new URLSearchParams(params).toString() : "";
  const target = "#/" + page + q;
  if (location.hash === target) render();
  else location.hash = target;
}

let renderToken = 0;
async function render() {
  const { page, params } = parseHash();
  const changed = page !== S.page;
  S.page = page;
  S.params = params;
  markNav();
  const token = ++renderToken;
  let nodes;
  try {
    nodes = await RENDER[page](params);
  } catch (e) {
    nodes = [card("Something went wrong", h("p", { class: "pre", text: e.message }),
      h("p", { class: "muted small", text: "If this keeps happening, reload the page. Your saved sessions are kept." }))];
  }
  if (token !== renderToken) return;           // a newer render started meanwhile
  const main = $("#main");
  const scroll = window.scrollY;
  main.textContent = "";
  const info = PAGE[page];
  main.append(h("div", { class: "pagehead" }, h("h1", { text: info.title }), h("span", { class: "ctx", text: info.ctx })));
  if (S.loading) main.append(loadingBanner());
  main.append(...[].concat(nodes).filter(Boolean));
  window.scrollTo(0, changed ? 0 : scroll);
  for (const fn of afterRender.splice(0)) fn();
}
const afterRender = [];

async function rerender() { await render(); }

// ---------------------------------------------------------------------------
// Tables (the desktop's SortableTree: click a heading to sort)
// ---------------------------------------------------------------------------
function sortKey(v) {
  const s = String(v ?? "").trim().replace(/%/g, "").replace(/\+/g, "");
  if (s === "" || s === "n/a" || s === "—") return [2, 0, ""];
  const n = Number(s.replace("−", "-"));
  if (s !== "" && !Number.isNaN(n)) return [0, n, ""];
  return [1, 0, s.toLowerCase()];
}
function cmpKeys(a, b) { return a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0); }

function dataTable(t, opts = {}) {
  const { id = "t", onRow = null, sticky = 1, wrap = [], short = false, flash = null, empty = "Nothing to show yet." } = opts;
  const box = h("div", { class: "tablewrap" + (short ? " short" : "") });
  if (!t || !t.rows || !t.rows.length) {
    box.append(h("div", { class: "empty", text: empty }));
    return box;
  }
  const cols = t.columns;
  const align = (c) => (c.align === "e" ? "num" : c.align === "center" ? "mid" : "");
  function draw() {
    const st = S.sort[id];
    let rows = t.rows.slice();
    if (st && st.col < cols.length) {
      rows.sort((a, b) => cmpKeys(sortKey(a[1][st.col]), sortKey(b[1][st.col])) * (st.desc ? -1 : 1));
    }
    const thead = h("thead", null, h("tr", null, cols.map((c, i) => {
      const arrow = st && st.col === i ? (st.desc ? " ▼" : " ▲") : "";
      return h("th", { class: [align(c), i < sticky ? "sticky" : ""].join(" "), scope: "col",
        onclick: () => { const cur = S.sort[id]; S.sort[id] = { col: i, desc: cur && cur.col === i ? !cur.desc : false }; draw(); } },
      c.label + arrow);
    })));
    const tbody = h("tbody");
    for (const [iid, vals, tags] of rows) {
      const tr = h("tr", { class: (tags || []).map((x) => "t-" + x).join(" ") + (onRow ? " clickable" : "") });
      if (flash != null && String(iid) === String(flash)) tr.classList.add("flash");
      if (onRow) tr.addEventListener("click", () => onRow(iid, vals));
      vals.forEach((v, i) => {
        const c = cols[i] || {};
        tr.append(h("td", { class: [align(c), i < sticky ? "sticky" : "", wrap.includes(c.key) ? "wrap" : ""].join(" ") }, v == null ? "" : String(v)));
      });
      tbody.append(tr);
    }
    const table = h("table", { class: "data" }, thead, tbody);
    box.textContent = "";
    box.append(table);
    requestAnimationFrame(() => fixSticky(table, sticky));
    if (flash != null) requestAnimationFrame(() => {
      const row = table.querySelector("tr.flash");
      if (row) row.scrollIntoView({ block: "center" });
    });
  }
  draw();
  return box;
}
function fixSticky(table, n) {
  if (n < 2) return;
  const first = table.rows[0];
  if (!first) return;
  let left = 0;
  const lefts = [];
  for (let i = 0; i < n; i++) { lefts.push(left); left += first.cells[i].getBoundingClientRect().width; }
  for (const row of table.rows) for (let i = 0; i < n && i < row.cells.length; i++) row.cells[i].style.left = lefts[i] + "px";
}

// ---------------------------------------------------------------------------
// Charts (a port of the desktop ChartCanvas: same axes, ticks, colours and rules)
// ---------------------------------------------------------------------------
const C = { padL: 62, padR: 18, padT: 34, padB: 48, grid: "#e6e9f0", axis: "#8a93a6", text: "#182236", muted: "#6b7280", brand: "#5d081f" };
const measureCtx = document.createElement("canvas").getContext("2d");
function textW(s, px = 11, weight = "400") { measureCtx.font = `${weight} ${px}px Roboto, Arial, sans-serif`; return measureCtx.measureText(s).width; }
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function niceTicks(lo, hi, n = 5) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (hi <= lo) hi = lo + (Math.abs(lo) * 0.1 || 1);
  const span = hi - lo, raw = span / Math.max(n, 1);
  const mag = raw > 0 ? 10 ** Math.floor(Math.log10(raw)) : 1;
  let step = mag;
  for (const m of [1, 2, 2.5, 5, 10]) { step = m * mag; if (span / step <= n + 0.5) break; }
  const ticks = [];
  for (let t = Math.floor(lo / step) * step; t <= hi + step * 0.5; t += step) ticks.push(Math.round(t * 1e10) / 1e10);
  return ticks;
}
function fmtTick(v) {
  if (Math.abs(v) >= 100 || v === Math.trunc(v)) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(1);
  return v.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}
function lerpColor(a, b, t) {
  t = Math.min(Math.max(t, 0), 1);
  const p = (c, i) => parseInt(c.slice(1 + 2 * i, 3 + 2 * i), 16);
  const ch = (i) => Math.round(p(a, i) + (p(b, i) - p(a, i)) * t).toString(16).padStart(2, "0");
  return "#" + ch(0) + ch(1) + ch(2);
}
function wrapText(s, maxW, px, weight) {
  const words = String(s).split(" ");
  const lines = [];
  let cur = "";
  for (const w of words) {
    const t = cur ? cur + " " + w : w;
    if (textW(t, px, weight) > maxW && cur) { lines.push(cur); cur = w; } else cur = t;
  }
  if (cur) lines.push(cur);
  return lines;
}

function buildChart(c, w, H) {
  const out = [];
  const screen = [];
  const series = (c && c.series) || [];
  const fin = (v) => typeof v === "number" && Number.isFinite(v);
  const pts = [];
  for (const s of series) for (const p of s.points) if (fin(p.x) && fin(p.y)) pts.push([p, s]);
  const zs = pts.map(([p]) => p.z).filter(fin);
  const zLo = zs.length ? Math.min(...zs) : 0, zHi = zs.length ? Math.max(...zs) : 0;
  const titleLines = c && c.title ? wrapText(c.title, w - 24, 12.5, "700") : [];
  const entries = series.filter((s) => s.name && s.kind !== "vbar").map((s) => [s.color, s.name]);
  if (zs.length) entries.push([null, `size/colour = ${(c.z_label || "z").slice(0, 48)} (${fmtTick(zLo)} … ${fmtTick(zHi)})`]);
  const legendRows = [];
  if (entries.length > 1 || zs.length) {
    let row = [], used = 0;
    for (const [color, name] of entries) {
      const need = textW(name, 11) + 34;
      if (row.length && used + need > w - C.padL - C.padR) { legendRows.push(row); row = []; used = 0; }
      row.push([color, name, need]); used += need;
    }
    if (row.length) legendRows.push(row);
  }
  let rot = false, padB = C.padB;
  const xt0 = c && c.x_ticks;
  if (xt0 && xt0.length) {
    const spacing = (w - C.padL - C.padR) / Math.max(1, xt0.length);
    const maxlen = Math.max(...xt0.map(([, lab]) => String(lab).length));
    rot = spacing < maxlen * 7 + 10;
    if (rot) padB = C.padB + Math.round(Math.min(maxlen, 14) * 5.5);
  }
  const titleH = Math.max(0, titleLines.length - 1) * 16;
  const x0 = C.padL, y0 = C.padT + titleH + 14 * legendRows.length, x1 = w - C.padR, y1 = H - padB;
  out.push(`<rect width="${w}" height="${H}" fill="#fff"/>`);
  titleLines.forEach((line, i) => out.push(`<text x="${w / 2}" y="${18 + i * 16}" text-anchor="middle" font-size="12.5" font-weight="700" fill="${C.brand}">${esc(line)}</text>`));
  let ly = 28 + titleH;
  for (const row of legendRows) {
    let lx = x0;
    for (const [color, name, need] of row) {
      if (color) out.push(`<rect x="${lx}" y="${ly}" width="12" height="10" fill="${color}"/>`);
      out.push(`<text x="${lx + (color ? 16 : 0)}" y="${ly + 9}" font-size="11" fill="${color ? C.text : C.muted}">${esc(name)}</text>`);
      lx += need;
    }
    ly += 14;
  }
  if (!pts.length) {
    out.push(`<text x="${w / 2}" y="${H / 2}" text-anchor="middle" font-size="13" fill="${C.muted}">${esc((c && c.note) || "No data to chart yet.")}</text>`);
    return { svg: out.join(""), screen };
  }
  const xs = pts.map(([p]) => p.x), ys = pts.map(([p]) => p.y);
  let loX = Math.min(...xs), hiX = Math.max(...xs), loY = Math.min(...ys), hiY = Math.max(...ys);
  let xt;
  if (xt0 && xt0.length) {
    loX = Math.min(loX, xt0[0][0]) - 0.6; hiX = Math.max(hiX, xt0[xt0.length - 1][0]) + 0.6;
    xt = xt0;
  } else {
    const padx = (hiX - loX) * 0.05 || (Math.abs(hiX) * 0.05 || 1);
    loX -= padx; hiX += padx;
    xt = niceTicks(loX, hiX, w < 500 ? 4 : 6).map((v) => [v, fmtTick(v)]);
    loX = Math.min(loX, xt[0][0]); hiX = Math.max(hiX, xt[xt.length - 1][0]);
  }
  const pady = (hiY - loY) * 0.08 || (Math.abs(hiY) * 0.08 || 1);
  loY -= pady; hiY += pady;
  const yt = niceTicks(loY, hiY, 5);
  loY = Math.min(loY, yt[0]); hiY = Math.max(hiY, yt[yt.length - 1]);
  const sx = (v) => x0 + (v - loX) / (hiX - loX) * (x1 - x0);
  const sy = (v) => y1 - (v - loY) / (hiY - loY) * (y1 - y0);
  for (const v of yt) {
    const y = sy(v);
    out.push(`<line x1="${x0}" y1="${y}" x2="${x1}" y2="${y}" stroke="${C.grid}"/>`);
    out.push(`<text x="${x0 - 6}" y="${y + 3.5}" text-anchor="end" font-size="10.5" fill="${C.muted}">${fmtTick(v)}</text>`);
  }
  // label every category when they fit; thin them out on narrow screens
  const every = xt0 && xt0.length ? Math.max(1, Math.ceil(xt.length / Math.max(1, Math.floor((x1 - x0) / 13)))) : 1;
  xt.forEach(([v, lab], i) => {
    const x = sx(v);
    out.push(`<line x1="${x}" y1="${y0}" x2="${x}" y2="${y1}" stroke="${C.grid}"/>`);
    if (i % every) return;
    if (rot) out.push(`<text transform="translate(${x + 3},${y1 + 8}) rotate(-60)" text-anchor="end" font-size="10.5" fill="${C.muted}">${esc(String(lab).slice(0, 14))}</text>`);
    else out.push(`<text x="${x}" y="${y1 + 16}" text-anchor="middle" font-size="10.5" fill="${C.muted}">${esc(String(lab).slice(0, 12))}</text>`);
  });
  out.push(`<line x1="${x0}" y1="${y1}" x2="${x1}" y2="${y1}" stroke="${C.axis}"/><line x1="${x0}" y1="${y0}" x2="${x0}" y2="${y1}" stroke="${C.axis}"/>`);
  if (c.x_label) out.push(`<text x="${(x0 + x1) / 2}" y="${H - 8}" text-anchor="middle" font-size="11.5" fill="${C.text}">${esc(c.x_label)}</text>`);
  if (c.y_label) out.push(`<text transform="translate(14,${(y0 + y1) / 2}) rotate(-90)" text-anchor="middle" font-size="11.5" fill="${C.text}">${esc(c.y_label)}</text>`);
  const zNorm = (z) => (!fin(z) || !zs.length) ? null : (zHi === zLo ? 0.5 : (z - zLo) / (zHi - zLo));
  for (const s of series) {
    const sp = s.points.filter((p) => fin(p.x) && fin(p.y));
    if (s.kind === "vbar") {
      const groups = new Map();
      for (const p of sp) { if (!groups.has(p.x)) groups.set(p.x, []); groups.get(p.x).push(p.y); }
      for (const [gx, gys] of groups) out.push(`<line x1="${sx(gx)}" y1="${sy(Math.min(...gys))}" x2="${sx(gx)}" y2="${sy(Math.max(...gys))}" stroke="${s.color}" stroke-width="4"/>`);
      continue;
    }
    if (s.kind === "line" && sp.length >= 2) {
      const d = sp.slice().sort((a, b) => a.x - b.x).map((p, i) => (i ? "L" : "M") + sx(p.x).toFixed(1) + " " + sy(p.y).toFixed(1)).join("");
      out.push(`<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2"${s.dashed ? ' stroke-dasharray="6 4"' : ""}/>`);
    }
    if (!s.markers) continue;
    for (const p of sp) {
      const cx = sx(p.x), cy = sy(p.y), zn = zNorm(p.z);
      const r = zn == null ? 4.5 : 4 + 9 * zn;
      const color = p.color || (zn == null ? s.color : lerpColor("#dbdce0", "#5d081f", zn));
      out.push(`<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${r.toFixed(1)}" fill="${color}" stroke="#fff" stroke-width="1"/>`);
      if (s.labels && p.label) out.push(`<text x="${cx + r + 2}" y="${cy - r - 1}" font-size="10" fill="${C.text}">${esc(p.label)}</text>`);
      screen.push([cx, cy, p]);
    }
  }
  if (c.note) out.push(`<text x="${x0 + 4}" y="${y0 + 12}" font-size="10.5" fill="${C.muted}">${esc(c.note)}</text>`);
  return { svg: out.join(""), screen };
}

function chartView(c, opts = {}) {
  const { height = 360, onPoint = null, name = "chart" } = opts;
  const box = h("div", { class: "chart" });
  const tip = h("div", { class: "tip", hidden: true });
  let screen = [], size = [0, 0], pointerType = "mouse", tipped = null;
  function draw() {
    const w = Math.max(Math.round(box.clientWidth), 260);
    const H = w < 600 ? Math.min(height, Math.max(280, Math.round(w * 0.95))) : height;
    if (w === size[0] && H === size[1]) return;
    size = [w, H];
    const b = buildChart(c, w, H);
    screen = b.screen;
    box.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${H}" width="${w}" height="${H}" font-family="Roboto, Arial, sans-serif" role="img" aria-label="${esc((c && c.title) || "chart")}">${b.svg}<circle class="ring" r="8" fill="none" stroke="#5d081f" stroke-width="2" visibility="hidden"/></svg>`;
    box.append(tip);
  }
  function nearest(ev) {
    const r = box.getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    let best = null, bd = (pointerType === "touch" ? 22 : 14) ** 2;
    for (const s of screen) { const d = (s[0] - x) ** 2 + (s[1] - y) ** 2; if (d <= bd) { bd = d; best = s; } }
    return best;
  }
  function showTip(s) {
    const ring = box.querySelector(".ring");
    if (!s || (!s[2].text && s[2].key == null)) { tip.hidden = true; if (ring) ring.setAttribute("visibility", "hidden"); tipped = null; return; }
    tipped = s;
    ring.setAttribute("cx", s[0]); ring.setAttribute("cy", s[1]); ring.setAttribute("visibility", "visible");
    tip.textContent = s[2].text || String(s[2].key);
    if (onPoint && s[2].key != null && pointerType === "touch") tip.append(h("span", { class: "hint", text: "Tap again to open" }));
    tip.hidden = false;
    const bw = box.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = s[0] + 12, top = s[1] - th - 10;
    if (left + tw > bw - 4) left = Math.max(4, s[0] - tw - 12);
    if (top < 4) top = s[1] + 12;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  box.addEventListener("pointerdown", (e) => { pointerType = e.pointerType || "mouse"; });
  box.addEventListener("pointermove", (e) => { if (e.pointerType === "mouse") { pointerType = "mouse"; showTip(nearest(e)); } });
  box.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") showTip(null); });
  box.addEventListener("click", (e) => {
    const s = nearest(e);
    if (!s) { showTip(null); return; }
    if (pointerType === "touch" && tipped !== s) { showTip(s); return; }
    if (onPoint && s[2].key != null) onPoint(s[2].key);
    else showTip(s);
  });
  const ro = new ResizeObserver(() => draw());
  ro.observe(box);
  afterRender.push(draw);
  box.saveImage = () => saveChartPng(c, name);
  return box;
}

function saveChartPng(c, name) {
  const w = 1200, H = 700;
  const b = buildChart(c, w, H);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${H}" viewBox="0 0 ${w} ${H}" font-family="Roboto, Arial, sans-serif">${b.svg}</svg>`;
  const img = new Image();
  img.onload = () => {
    const cv = document.createElement("canvas");
    cv.width = w * 2; cv.height = H * 2;
    const ctx = cv.getContext("2d");
    ctx.scale(2, 2);
    ctx.drawImage(img, 0, 0);
    cv.toBlob((blob) => {
      const url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: (name || "chart") + ".png" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    }, "image/png");
  };
  img.onerror = () => download((name || "chart") + ".svg", svg, "image/svg+xml");
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

function chartBlock(c, opts = {}) {
  const view = chartView(c, opts);
  return h("div", null, view, h("div", { class: "row", style: { marginTop: "8px" } },
    h("span", { class: "spacer" }), btn("Save image", () => view.saveImage(), "small")));
}

function onChartPoint(key) {
  if (typeof key === "number") go("teams", { team: key });
  else if (Array.isArray(key) && key.length) go("teams", { team: key[0] });
  else if (typeof key === "string") go("matches", { match: key });
}

// ---------------------------------------------------------------------------
// Dialogs
// ---------------------------------------------------------------------------
function modal(build, { wide = false, onClose = null } = {}) {
  const wrap = h("div", { class: "modal", role: "dialog", "aria-modal": "true" });
  const dlg = h("div", { class: "dialog" + (wide ? " wide" : "") });
  const close = () => { wrap.remove(); document.removeEventListener("keydown", esc); if (onClose) onClose(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  document.addEventListener("keydown", esc);
  dlg.append(...[].concat(build(close)).filter(Boolean));
  wrap.append(dlg);
  document.body.append(wrap);
  const first = dlg.querySelector("input, select, textarea");
  if (first && !isPhone()) first.focus();
  return close;
}
function infoDialog(title, text) {
  modal((close) => [h("button", { class: "btn small close", onclick: close, "aria-label": "Close" }, "✕"),
    h("h2", { text: title }), h("pre", { text }), h("div", { class: "foot" }, btn("Close", close, "primary"))], { wide: true });
}
function confirmDialog(title, text, okText, onOk) {
  modal((close) => [h("h2", { text: title }), h("p", { text }),
    h("div", { class: "foot" }, btn("Cancel", close), btn(okText, () => { close(); onOk(); }, "primary"))]);
}
function quickSwitch() {
  const all = Object.values(PAGE);
  let idx = 0;
  modal((close) => {
    const list = h("div", { class: "switchlist" });
    const input = h("input", { type: "search", placeholder: "Jump to a workspace…", style: { width: "100%" } });
    const draw = () => {
      const q = input.value.trim().toLowerCase();
      const items = all.filter((p) => !q || p.title.toLowerCase().includes(q) || p.ctx.toLowerCase().includes(q));
      idx = Math.min(idx, Math.max(0, items.length - 1));
      list.textContent = "";
      items.forEach((p, i) => list.append(h("button", { class: i === idx ? "on" : "", type: "button",
        onclick: () => { close(); go(p.id); } }, `${p.icon}  ${p.title}`, h("small", { text: p.ctx }))));
      list.items = items;
    };
    input.addEventListener("input", () => { idx = 0; draw(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { idx++; draw(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { idx = Math.max(0, idx - 1); draw(); e.preventDefault(); }
      else if (e.key === "Enter" && list.items[idx]) { close(); go(list.items[idx].id); }
    });
    draw();
    return [h("h2", { text: "Quick switch" }), input, list];
  });
}

// ---------------------------------------------------------------------------
// Loading an event (shared by Setup and the banner on every page)
// ---------------------------------------------------------------------------
const loadUI = { msg: "", frac: 0 };
function loadingBanner() {
  return h("div", { class: "card", id: "loadBanner" }, h("div", { class: "row" },
    h("b", { text: "Loading event data" }), h("div", { class: "progress" }, h("div", { style: { width: (loadUI.frac * 100).toFixed(0) + "%" } })),
    h("span", { class: "muted small", text: loadUI.msg })));
}
function updateLoadUI() {
  for (const bar of document.querySelectorAll("#loadBanner .progress > div, #setupProgress > div")) bar.style.width = (loadUI.frac * 100).toFixed(0) + "%";
  for (const t of document.querySelectorAll("#loadBanner .muted, #setupStatus")) t.textContent = loadUI.msg;
}
async function loadEvent(args) {
  if (S.loading) return;
  S.loading = true;
  loadUI.msg = "Contacting FTCScout…"; loadUI.frac = 0;
  updateLoadUI();
  try {
    const r = await act("load", args, (msg, frac) => { loadUI.msg = msg; loadUI.frac = frac; updateLoadUI(); });
    toast(r.message, !r.ok);
    loadUI.msg = r.message || "";
  } catch (e) {
    toast(e.message, true);
  } finally {
    S.loading = false;
    rerender();
  }
}

async function runAction(name, args, okToast = true) {
  try {
    const r = await act(name, args);
    if (r.message && (okToast || !r.ok)) toast(r.message, !r.ok);
    return r;
  } catch (e) {
    toast(e.message, true);
    return { ok: false, message: e.message };
  }
}

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------
const RENDER = {};

RENDER.overview = async () => {
  const d = await view("overview");
  const action = (n, title, desc, page, primary) => h("a", { class: "action" + (primary ? " primary" : ""), href: "#/" + page },
    h("b", { text: `${n}   ${title}` }), h("span", { text: desc }));
  const steps = [["01", "Set up your event", "Choose a season, event, and your team."],
    ["02", "Build your dataset", "Load event data and add reliable pit or match observations."],
    ["03", "Understand performance", "Use charts, CAP, EPA, and match analysis together."],
    ["04", "Make a strategy call", "Compare alliances and validate predictions against real results."]];
  return [
    hero("COMPETITION COMMAND CENTER", "Scout smarter. Pick stronger alliances.",
      "Your match data, team intelligence, and strategy tools — together in one workspace."),
    metricsRow(d.metrics),
    h("div", { class: "grid-even" },
      card("Quick actions", h("div", { class: "actions" },
        action("01", "Load an event", "Bring in teams, schedule, and match results.", "setup", !d.head.loaded),
        action("02", "Scout a match", "Review matchups and enter your observations.", "matches"),
        action("03", "Research any team", "Explore public season data, records, and performance graphs.", "research"),
        action("04", "Build an alliance", "Compare team strengths and identify promising combinations.", "alliances"),
        action("05", "Open live scouting", "Keep match notes organized while following a stream.", "live"))),
      card("Workflow", h("div", { class: "steps" }, steps.map(([n, t, s]) => h("div", { class: "step" },
        h("span", { class: "n", text: n }), h("div", null, h("b", { text: t }), h("span", { text: s }))))),
      h("p", { class: "note", text: isPhone() ? "Tip: the ☰ menu lists every workspace." : "Tip: use Ctrl+K (⌘K on a Mac) to jump to any workspace." }))),
    card("Use it on a phone",
      h("p", { text: "This is the same AutoScout as the desktop app, running in the browser. Add it to your home screen and it opens like an app, even with no signal at the venue once an event is loaded." }),
      h("p", { class: "muted small", text: "iPhone: Safari ▸ Share ▸ Add to Home Screen.   Android: Chrome ▸ ⋮ ▸ Install app.   Data stays on this device; use Setup ▸ Download session file to move it to another device or to the desktop app." })),
  ];
};

RENDER.competition = async (params) => {
  if (params.match) S.comp.match = params.match;
  const d = await view("competition", { match: S.comp.match });
  const my = d.head.my_team ? String(d.head.my_team) : "";
  const list = h("div", { class: "matchlist" });
  const sel = d.detail ? d.detail.label : null;
  for (const [, [label, red, blue, forecast, status]] of d.matches.rows) {
    const mine = my && red.split(" · ").concat(blue.split(" · ")).includes(my);
    const chip = label === d.upcoming ? h("span", { class: "chip next", text: "Up next" })
      : h("span", { class: "chip " + (status === "Played" ? "played" : ""), text: status });
    const fc = String(forecast).match(/R (\d+)% \/ B (\d+)%/);
    list.append(h("button", { class: "matchrow" + (label === sel ? " on" : "") + (mine ? " mine" : ""), type: "button",
      onclick: () => { S.comp.match = label; rerender(); } },
    h("span", { class: "lab", text: label }),
    h("span", { class: "teams" }, h("span", { class: "side r", text: red }), h("span", { class: "side b", text: blue })),
    h("span", { class: "fc" }, fc ? h("b", { text: `R ${fc[1]}%  B ${fc[2]}%` }) : h("b", { text: "—" }), chip)));
  }
  if (!d.matches.rows.length) list.append(h("div", { class: "empty", text: "Load an event to populate your match desk." }));
  afterRender.push(() => {          // scroll the list itself, not the page
    const on = list.querySelector(".matchrow.on");
    if (on && (on.offsetTop < list.scrollTop || on.offsetTop + on.offsetHeight > list.scrollTop + list.clientHeight)) list.scrollTop = on.offsetTop - list.offsetTop - 8;
  });
  const detail = d.detail
    ? [h("h3", { class: "plain", text: d.detail.title, style: { fontSize: "20px", marginBottom: "8px" } }),
      h("div", { class: "detail", text: d.detail.text }),
      h("div", { class: "row", style: { marginTop: "10px", justifyContent: "flex-end" } },
        btn("Scout this match", () => { S.live.match = d.detail.label; go("live"); }, "primary"))]
    : [h("p", { class: "muted", text: "Select a match" })];
  return [
    hero("COMPETITION DAY", "Everything you need between matches.", d.head.loaded ? d.line : "Load an event to populate your match desk."),
    metricsRow(d.metrics),
    h("div", { class: "row", style: { marginBottom: "14px" } },
      btn("Refresh results", async () => { const r = await runAction("refresh", {}); if (r.ok) rerender(); }, "primary"),
      btn("Open live scouting", () => go("live")), btn("Alliance selection", () => go("alliances")),
      btn("Team research", () => go("research")), h("span", { class: "spacer" }), btn("Save session", saveSession)),
    h("div", { class: "grid2" }, card("Match desk", list), card("Selected match", ...detail)),
  ];
};

RENDER.setup = async () => {
  const d = await view("setup");
  const season = h("input", { type: "number", value: d.season, min: 2019, max: 2100, inputmode: "numeric" });
  const team = h("input", { type: "number", value: d.team, inputmode: "numeric", placeholder: "e.g. 12928" });
  const extra = textInput(d.extra, { placeholder: "e.g. 11225 19013" });
  const search = h("input", { type: "search", value: S.eventSearch.query, placeholder: "Search by meet name, city, or event code", autocomplete: "off" });
  const searchStatus = h("div", { class: "status", text: S.eventSearch.status || "Search FTCScout events for the selected season." });
  const results = h("div", { class: "switchlist event-results" });
  let offline = d.offline, history = true;
  const schedule = h("textarea", { rows: 7, spellcheck: "false", placeholder: "Q-1  11225 19013  19564 23213" });
  schedule.value = d.schedule || "";
  team.addEventListener("change", async () => { await runAction("my_team", { team: team.value }, false); });
  season.addEventListener("change", () => {
    S.eventSearch.results = [];
    S.eventSearch.status = "Season changed. Search again to find events in this season.";
    drawResults();
    searchStatus.textContent = S.eventSearch.status;
  });
  search.addEventListener("input", () => {
    S.eventSearch.query = search.value;
    S.eventSearch.results = [];
    S.eventSearch.status = "";
    drawResults();
    searchStatus.textContent = "Press Search meets to search again.";
  });
  const args = (code) => ({ season: season.value, event: code, team: team.value, extra: extra.value,
    offline, history, schedule: schedule.value });
  async function chooseEvent(ev) {
    S.eventSearch.status = "Loading " + (ev.name || ev.code) + "…";
    await loadEvent({ ...args(ev.code), season: ev.season || season.value });
  }
  function drawResults() {
    results.textContent = "";
    for (const ev of S.eventSearch.results) {
      results.append(h("button", { type: "button", class: "event-result", onclick: () => chooseEvent(ev) },
        h("span", null, h("b", { text: ev.name || ev.code }), h("small", { text: ev.code })),
        h("span", { class: "event-load-label", text: "Load event →" })));
    }
    results.hidden = !S.eventSearch.results.length;
  }
  async function searchEvents() {
    const query = search.value.trim();
    S.eventSearch.query = query;
    if (!query) {
      S.eventSearch.status = "Enter a meet name, city, or event code to search.";
      searchStatus.textContent = S.eventSearch.status;
      return;
    }
    if (offline) {
      S.eventSearch.status = "Event search needs an internet connection. Turn off Offline in Advanced options.";
      searchStatus.textContent = S.eventSearch.status;
      return;
    }
    searchStatus.textContent = "Searching FTCScout…";
    const r = await runAction("events_search", { season: season.value, query: query }, false);
    if (!r.ok) {
      S.eventSearch.results = [];
      S.eventSearch.status = r.message || "Could not search events.";
    } else {
      S.eventSearch.results = r.results || [];
      S.eventSearch.status = r.message || (S.eventSearch.results.length ? "Select a meet to load it." : "No matching meets found.");
    }
    await rerender();
  }
  const searchBtn = btn("Search meets", searchEvents, "primary");
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") searchEvents(); });
  drawResults();
  searchStatus.textContent = S.eventSearch.status || "Search FTCScout events for the selected season.";
  const fileIn = h("input", { type: "file", accept: ".json,application/json", hidden: true, onchange: async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const r = await runAction("import", { text: await f.text() });
    if (r.ok) rerender();
  } });
  const sessions = h("div", { class: "sessions" });
  if (!d.sessions.length) sessions.append(h("p", { class: "muted", text: "No sessions saved in this browser yet. Loading an event saves one automatically." }));
  for (const s of d.sessions) {
    const current = d.head.path === s.file;
    sessions.append(h("div", { class: "sess" + (current ? " current" : "") },
      h("div", { class: "info" }, h("b", { text: s.event || s.code }),
        h("span", { class: "muted small", text: s.season + "/" + s.code + " · " + s.teams + " teams" + (s.my_team ? " · team " + s.my_team : "") + " · saved " + s.saved + (current ? " · open now" : "") })),
      current ? null : btn("Open", async () => { const r = await runAction("open_saved", { file: s.file }); if (r.ok) rerender(); }, "small"),
      btn("Delete", () => confirmDialog("Delete saved session?", (s.event || s.code) + " will be removed from this browser. Download it first if you want a copy.", "Delete",
        async () => { await runAction("delete_saved", { file: s.file }); rerender(); }), "small danger")));
  }
  return [
    card("Meet",
      h("p", { class: "muted", text: "Find your competition by name, location, or event code. Choose a result to load it automatically." }),
      h("div", { class: "form" }, field("Season", season), field("My team number", team)),
      h("div", { class: "row", style: { marginTop: "10px" } }, field("Find a meet", search, "wide"), searchBtn),
      searchStatus, results,
      h("details", { class: "adv" }, h("summary", { text: "Advanced options" }), h("div", null,
        field("Extra team numbers (optional)", extra),
        check("Offline (cache only — never touch the network)", offline, (v) => { offline = v; }),
        check("Fetch each team's season history (needed for projected OPR)", history, (v) => { history = v; }))),
      h("div", { class: "status", id: "setupStatus", text: S.loading ? loadUI.msg : "A big event takes a few minutes the first time: every team's season history is downloaded." })),
    card("Sessions saved in this browser", sessions,
      h("div", { class: "row", style: { marginTop: "12px" } }, fileIn,
        btn("Open session file…", () => fileIn.click()),
        btn("Download session file", () => exportFile("session"), ""),
        btn("New session", () => confirmDialog("Start a new session?", "The current session stays saved in this browser.", "New session",
          async () => { await runAction("new", {}, false); rerender(); }))),
      h("p", { class: "note", text: "Session files are the same .json the desktop app opens and saves, so you can move a session between the desktop app, this site and other phones." })),
    card("Paste schedule (used when FTCScout has no schedule)",
      h("pre", { class: "muted small", style: { margin: "0 0 8px", whiteSpace: "pre-wrap" }, text:
        "One match per line — the first two team numbers are red, the next two blue.\n    Q-1  11225 19013  19564 23213\n    1    11225 19013 vs 19564 23213   104-66\nA trailing red-blue score marks the match as played." }),
      schedule,
      h("div", { class: "row" }, btn("Use pasted schedule", async () => { const r = await runAction("schedule", { season: season.value, team: team.value, extra: extra.value, schedule: schedule.value }); if (r.ok) rerender(); }))),
    card("Loaded event", h("div", { class: "pre", text: d.info })),
  ];
};

function exportFile(kind) {
  runAction("export", { kind }, false).then((r) => { if (r.ok) download(r.name, r.text, r.mime); });
}
async function saveSession() {
  const r = await runAction("save", {});
  if (r.ok && S.page === "setup") rerender();
}

// ---- Teams (+ the team sheet) ---------------------------------------------
RENDER.teams = async (params) => {
  const team = params.team ? Number(params.team) : 0;
  const d = await view("teams", { ...S.panel, team });
  const nodes = [
    h("p", { class: "muted", text: "Tap a team to chart its statistics and edit its inputs." }),
    dataTable(d.table, { id: "teams", sticky: 2, flash: team || null, onRow: (iid) => go("teams", { team: iid }),
      empty: "No teams loaded. Load a meet on the Setup tab first." }),
  ];
  if (team && d.editor) afterRender.push(() => openTeamSheet(d));
  else if (team) afterRender.push(() => toast(`Team ${team} is not at this event.`, true));
  return nodes;
};

function openTeamSheet(d) {
  const e = d.editor;
  let closeIt = null;
  const back = () => { if (closeIt) closeIt(); };
  closeIt = modal((close) => {
    const tdc = select(S.boot.tdc_issues.map((x) => [x, x || "— none —"]), e.tdc, () => apply());
    const delta = h("input", { type: "number", value: e.delta, step: "any", inputmode: "decimal" });
    const focus = select(S.boot.focus_modes, e.focus, () => apply());
    const note = textInput(e.note, { placeholder: "Pit notes, robot issues…" });
    delta.addEventListener("change", () => apply());
    async function apply() {
      await runAction("team_edit", { team: e.team, tdc: tdc.value, delta: delta.value, focus: focus.value, note: note.value }, false);
      const nd = await view("teams", { ...S.panel, team: e.team });
      refreshPanel(nd.panel);
      toast("Saved. CAP and every prediction updated.");
    }
    const panelBox = h("div");
    const refreshPanel = (panel) => { panelBox.textContent = ""; panelBox.append(teamPanel(panel, { inSheet: true, onChange: async () => {
      const nd = await view("teams", { ...S.panel, team: e.team }); refreshPanel(nd.panel);
    } })); for (const fn of afterRender.splice(0)) fn(); };
    refreshPanel(d.panel);
    return [
      h("button", { class: "btn small close", onclick: close, "aria-label": "Close" }, "✕"),
      h("h2", { text: e.title }),
      h("section", { class: "card" }, h("h3", { text: "Adjust the selected team (T.D.C. + Team Focus)" }),
        h("div", { class: "form" }, field("T.D.C. issue", tdc), field("Delta", delta), field("Focus", focus), field("Note", note, "wide")),
        h("div", { class: "row" }, btn("Apply", apply, "primary"))),
      panelBox,
    ];
  }, { wide: true, onClose: () => { if (parseHash().page === "teams" && parseHash().params.team) { history.replaceState(null, "", "#/teams"); rerender(); } } });
  return back;
}

// The one-team chart panel (Teams sheet and Charts ▸ One team)
function teamPanel(p, { onChange, inSheet = false } = {}) {
  const set = (patch) => { Object.assign(S.panel, patch); onChange(); };
  const ySel = (i) => select(i === 0 ? p.y_options : [p.none, ...p.y_options], p.y[i] || p.none, (v) => {
    const y = p.y.slice(); y[i] = v; set({ y, domain: p.domain, x: p.x });
  });
  const chart = chartView(p.chart, { height: inSheet ? 320 : 380, onPoint: inSheet ? null : onChartPoint, name: `team_${p.team || "chart"}` });
  const ovInputs = {};
  const status = h("div", { class: "status" });
  const overrides = p.team ? h("section", { class: "card" },
    h("h3", { text: "Edit this team's data inputs (blank = value from FTCScout)" }),
    h("div", { class: "form" }, p.overrides.map((o) => {
      const inp = h("input", { type: "number", step: "any", inputmode: "decimal", value: o.value, placeholder: o.current });
      ovInputs[o.key] = inp;
      return field(`${o.label} ${o.current}`, inp);
    })),
    h("div", { class: "row" },
      btn("Apply", async () => {
        const values = Object.fromEntries(Object.entries(ovInputs).map(([k, el]) => [k, el.value]));
        const r = await runAction("overrides", { team: p.team, values }, false);
        status.textContent = r.message || ""; onChange();
      }, "primary"),
      btn("Reset to FTCScout", async () => { const r = await runAction("overrides", { team: p.team, reset: true }, false); status.textContent = r.message || ""; onChange(); })),
    status,
    h("p", { class: "note", text: "Overrides feed CAP, the predictions and the charts for this meet (earlier events keep their recorded values); saved with the session." })) : null;
  return h("div", null,
    h("section", { class: "card" },
      h("h3", { class: "plain", text: p.head }),
      h("div", { class: "form roomy" },
        field("Chart", select(p.domains, p.domain, (v) => set({ domain: v, x: null, y: null }))),
        field("X axis", select(p.x_options, p.x, (v) => set({ x: v, domain: p.domain, y: p.y }))),
        field("Y axis", ySel(0)), field("Y2 (optional)", ySel(1)), field("Y3 (optional)", ySel(2))),
      h("div", { class: "row" },
        btn("OPR vs CAP vs EPA", () => set({ domain: p.domains[0], x: null, y: ["OPR (non-penalty)", "CAP (as of that event)", "EPA (after that event)"] }), "small"),
        btn("Auto / Teleop / Endgame", () => set({ domain: p.domains[0], x: null, y: ["Auto", "Teleop", "Endgame"] }), "small"),
        h("span", { class: "spacer" }), btn("Save image", () => chart.saveImage(), "small")),
      h("div", { style: { marginTop: "10px" } }, chart),
      h("div", { class: "row" }, h("span", { class: "muted small", style: { flex: "1 1 300px" }, text: p.explain }),
        btn("Explain this chart", () => infoDialog("What this chart shows", p.explain_long), "small"))),
    overrides);
}

// ---- Team Research ---------------------------------------------------------
RENDER.research = async () => {
  const d = await view("research");
  const R = S.research;
  if (!R.season) R.season = d.season;
  const query = h("input", { type: "search", value: R.query, placeholder: "e.g. 12928 or LightSaders", enterkeyhint: "search" });
  const season = h("input", { type: "number", value: R.season, inputmode: "numeric", style: { maxWidth: "120px" } });
  const status = h("div", { class: "status", text: R.status || d.hint });
  const search = async () => {
    R.query = query.value.trim(); R.season = season.value;
    status.textContent = "Searching public team records…";
    const r = await runAction("research_search", { query: R.query }, false);
    if (!r.ok) { status.textContent = r.message; return; }
    R.results = r.results; R.status = r.message || "";
    if (r.direct) await loadTeam(r.direct);
    else rerender();
  };
  query.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  async function loadTeam(n) {
    R.season = season.value;
    status.textContent = `Loading team ${n} · ${R.season} season…`;
    const r = await runAction("research_load", { team: n, season: R.season }, false);
    if (!r.ok) { status.textContent = r.message; R.status = r.message; return; }
    R.profile = r; R.status = r.status;
    rerender();
  }
  const results = R.results.length ? h("div", { class: "switchlist", style: { maxHeight: "260px" } },
    R.results.map((t) => h("button", { type: "button", class: R.profile && R.profile.title.startsWith(String(t.number) + " ") ? "on" : "",
      onclick: () => loadTeam(t.number) }, t.text))) : null;
  const P = R.profile;
  const profile = P ? [
    plainCard(null, h("h2", { text: P.title, style: { fontSize: "22px" } }), h("p", { class: "muted", text: P.subtitle, style: { marginTop: "4px" } }),
      metricsRow(P.metrics), chartBlock(P.chart, { height: 300, name: "team_research" })),
    h("div", { class: "grid2" },
      card("Season event history", dataTable(P.events, { id: "r-events", short: true, empty: "No events this season." })),
      h("div", null, card("Awards", dataTable(P.awards, { id: "r-awards", short: true, empty: "No awards listed." })),
        card("Match log", dataTable(P.matches, { id: "r-matches", short: true, empty: "No match records." })))),
    h("p", { class: "note pre", text: P.footer }),
  ] : [];
  return [
    h("p", { class: "muted", text: "Research any FTC team using FTCScout's public statistics. This workspace is separate from your loaded scouting session." }),
    card("Find a team",
      h("div", { class: "row" }, field("Team number or name", query), field("Season (start year)", season),
        h("div", { style: { alignSelf: "flex-end" } }, btn("Search teams", search, "primary"))),
      status, results),
    ...profile,
  ];
};

// ---- Matches ---------------------------------------------------------------
RENDER.matches = async (params) => {
  const d = await view("matches");
  const cols = d.table.columns.map((c) => c.key);
  const minutes = h("input", { type: "number", min: 1, max: 60, value: S.auto.minutes, style: { width: "70px" }, onchange: (e) => {
    S.auto.minutes = Math.max(1, Number(e.target.value) || 2); setAuto(S.auto.on);
  } });
  return [
    h("div", { class: "headline", text: d.summary }),
    h("div", { class: "row", style: { marginBottom: "10px" } },
      btn("Refresh results", async () => { const r = await runAction("refresh", {}); if (r.ok) rerender(); }, "primary"),
      check("Auto-refresh every", S.auto.on, (v) => setAuto(v)), minutes, h("span", { class: "muted", text: "min" })),
    dataTable(d.table, { id: "matches", sticky: 1, flash: params.match || null, empty: "No matches loaded.",
      onRow: (iid, vals) => resultDialog(iid, vals, cols) }),
    h("p", { class: "note", text: "Tap a match to enter a score by hand." }),
  ];
};
function setAuto(on) {
  S.auto.on = on;
  clearInterval(S.auto.timer);
  if (on) S.auto.timer = setInterval(async () => {
    if (S.loading) return;
    const r = await runAction("refresh", {}, false);
    if (r.ok && ["matches", "competition", "standings", "myteam", "analysis", "overview"].includes(S.page)) rerender();
  }, S.auto.minutes * 60000);
}
function resultDialog(label, vals, cols) {
  const get = (k) => vals[cols.indexOf(k)] ?? "";
  const score = String(get("score")).split("–").map((x) => x.trim());
  const red = h("input", { type: "number", inputmode: "numeric", value: score.length === 2 ? score[0] : "" });
  const blue = h("input", { type: "number", inputmode: "numeric", value: score.length === 2 ? score[1] : "" });
  modal((close) => {
    const ok = async () => { close(); const r = await runAction("result", { label, red: red.value, blue: blue.value }); if (r.ok) rerender(); };
    [red, blue].forEach((el) => el.addEventListener("keydown", (e) => { if (e.key === "Enter") ok(); }));
    return [h("h2", { text: `Result — ${label}` }),
      h("div", { class: "form" }, field(`Red ${[get("r1"), get("r2")].filter(Boolean).join(" + ")}`, red),
        field(`Blue ${[get("b1"), get("b2")].filter(Boolean).join(" + ")}`, blue)),
      h("div", { class: "foot" },
        btn("Clear", async () => { close(); const r = await runAction("result", { label, clear: true }); if (r.ok) rerender(); }),
        btn("Cancel", close), btn("OK", ok, "primary"))];
  });
}

// ---- My Team ---------------------------------------------------------------
RENDER.myteam = async () => {
  const d = await view("myteam");
  const team = h("input", { type: "number", value: d.team, inputmode: "numeric", style: { width: "110px" } });
  const show = async () => { await runAction("my_team", { team: team.value }, false); rerender(); };
  team.addEventListener("keydown", (e) => { if (e.key === "Enter") show(); });
  return [
    h("div", { class: "row", style: { marginBottom: "12px" } }, h("h2", { text: d.name, style: { fontSize: "22px", marginRight: "12px" } }),
      h("span", { text: "Team" }), team, btn("Show", show)),
    card("Season stats", metricsRow(d.stats), h("div", { class: "headline", text: d.change }), h("div", { class: "muted", text: d.proj })),
    card("My matches", dataTable(d.table, { id: "mymatches", empty: "No matches for this team." })),
  ];
};

RENDER.standings = async () => {
  const d = await view("standings");
  return [h("div", { class: "headline", text: d.head }),
    dataTable(d.table, { id: "standings", sticky: 2, onRow: (iid) => go("teams", { team: iid }), empty: "No standings yet." })];
};

RENDER.alliances = async () => {
  S.alliOnly = S.alliOnly || false;
  const d = await view("alliances", { only: S.alliOnly });
  const team = h("input", { type: "number", value: d.team, inputmode: "numeric", style: { width: "110px" } });
  const show = async () => { await runAction("my_team", { team: team.value }, false); rerender(); };
  team.addEventListener("keydown", (e) => { if (e.key === "Enter") show(); });
  return [
    h("div", { class: "headline", text: d.head }),
    h("div", { class: "row" }, h("span", { text: "Team" }), team, btn("Show", show),
      check("Unbeatable only", S.alliOnly, (v) => { S.alliOnly = v; rerender(); })),
    h("p", { class: "note", text: d.bar }),
    card("Alliance captains (what the draft leaves each seat)", dataTable(d.seats, { id: "seats", sticky: 2, empty: "No teams loaded." })),
    card("Best alliances", dataTable(d.alliances, { id: "alli", sticky: 2, wrap: ["names"], empty: "No alliances to show." })),
    card(d.partners_title, dataTable(d.partners, { id: "partners", sticky: 1, empty: "Set a team number above to see the partners it could win with." }),
      h("p", { class: "note", text: d.status })),
  ];
};

RENDER.winrate = async () => {
  const d = await view("winrate");
  return [plainCard(d.heading, h("p", { class: "muted", text: d.summary }), chartBlock(d.chart, { height: 480, name: "alliance_win_rate" }),
    h("p", { style: { color: "var(--brand)", marginTop: "10px", overflowWrap: "anywhere" }, text: d.equation }))];
};

RENDER.analysis = async () => {
  const d = await view("analysis");
  const openReport = async () => {
    const r = await runAction("export", { kind: "html" }, false);
    if (!r.ok) return;
    const url = URL.createObjectURL(new Blob([r.text], { type: "text/html" }));
    window.open(url, "_blank", "noopener");
  };
  return [
    h("div", { class: "row", style: { marginBottom: "12px" } },
      btn("Export CSV", () => exportFile("csv")), btn("Export printable HTML", () => exportFile("html")),
      btn("Open printable report", openReport),
      btn("Copy printable text", async () => {
        const r = await runAction("export", { kind: "text" }, false);
        if (!r.ok) return;
        try { await navigator.clipboard.writeText(r.text); toast("Printable text copied."); }
        catch { download(r.name, r.text, r.mime); }
      }),
      h("span", { class: "spacer" }), btn("Re-analyse", rerender)),
    card("Prediction report", h("div", { class: "detail", text: d.summary })),
    card("Wrong predictions", dataTable(d.wrong, { id: "wrong", wrap: ["suggest"], onRow: (iid) => go("matches", { match: iid }), empty: "No wrong predictions (or no results yet)." })),
    card("Team flags", dataTable(d.flags, { id: "flags", onRow: (iid) => go("teams", { team: iid }), empty: "No team flags." })),
  ];
};

// ---- Charts ----------------------------------------------------------------
RENDER.charts = async (params) => {
  if (params.page) S.chartsPage = params.page;
  const page = S.chartsPage;
  const tabs = h("div", { class: "subtabs" }, [["team", "One team"], ["all", "All teams"], ["pairs", "Matches & alliances"]].map(([k, lab]) =>
    h("button", { type: "button", class: k === page ? "on" : "", onclick: () => { S.chartsPage = k; rerender(); } }, lab)),
  h("span", { class: "spacer" }), btn("How to use the charts", () => infoDialog("How to use the Charts tab", S.boot.charts_guide), "small"));
  if (page === "team") {
    if (params.team) S.panel.team = Number(params.team);
    const d = await view("charts", { page: "team", ...S.panel });
    const team = h("input", { type: "number", value: d.panel.team || "", inputmode: "numeric", style: { width: "110px" } });
    const show = () => { S.panel.team = Number(team.value) || 0; rerender(); };
    team.addEventListener("keydown", (e) => { if (e.key === "Enter") show(); });
    return [tabs, h("div", { class: "row", style: { marginBottom: "12px" } }, h("span", { text: "Team" }), team, btn("Show", show)),
      teamPanel(d.panel, { onChange: rerender })];
  }
  if (page === "all") {
    const d = await view("charts", { page: "all", ...S.all });
    if (!d.views) return [tabs, chartBlock(d.chart)];
    const set = (patch) => { Object.assign(S.all, { view: d.view, x: d.x, y: d.y, z: d.z, labels: d.labels, spread: d.spread, ratings: d.ratings.filter((r) => r.on).map((r) => r.key) }, patch); rerender(); };
    const compare = d.view.startsWith("Compare");
    const controls = h("div", { class: "form" },
      field("View", select(d.views, d.view, (v) => set({ view: v }))),
      field(compare ? "Order teams by" : "X axis", select(d.options, d.x, (v) => set({ x: v }))),
      compare ? null : field("Y axis", select(d.options, d.y, (v) => set({ y: v }))),
      compare ? null : field("Z (size + colour)", select([d.none, ...d.options], d.z, (v) => set({ z: v }))));
    const extras = compare
      ? h("div", { class: "row" }, d.ratings.map((r) => check(r.label, r.on, (v) => {
        const keys = d.ratings.filter((x) => (x.key === r.key ? v : x.on)).map((x) => x.key); set({ ratings: keys });
      })), check("show disagreement bars", d.spread, (v) => set({ spread: v })))
      : h("div", { class: "row" }, check("Label points", d.labels, (v) => set({ labels: v })));
    return [tabs, card("What to chart", controls, extras),
      chartBlock(d.chart, { height: 420, onPoint: onChartPoint, name: "all_teams" }),
      h("p", { class: "note", text: d.status }),
      h("div", { class: "row" }, h("span", { class: "muted small", style: { flex: "1 1 300px" }, text: d.explain }),
        btn("Explain this chart", () => infoDialog("What this chart shows", d.explain_long), "small"))];
  }
  const d = await view("charts", { page: "pairs", ...S.pairs });
  if (!d.units) return [tabs, chartBlock(d.chart)];
  const set = (patch) => { Object.assign(S.pairs, { unit: d.unit, x: d.x, y: d.y, z: d.z, labels: d.labels, top: d.top }, patch); rerender(); };
  const isMatches = d.unit === d.units[0];
  return [tabs,
    card("What to chart",
      h("div", { class: "form" },
        field("Chart", select(d.units, d.unit, (v) => { S.pairs = { unit: v, labels: d.labels, top: d.top }; rerender(); })),
        field("X axis", select(d.options, d.x, (v) => set({ x: v }))),
        field("Y axis", select(d.options, d.y, (v) => set({ y: v }))),
        field("Z (size + colour)", select([d.none, ...d.options], d.z, (v) => set({ z: v }))),
        isMatches ? null : field("Show top", select(d.tops, d.top, (v) => set({ top: v })))),
      h("div", { class: "row" }, check("Label points", d.labels, (v) => set({ labels: v })))),
    chartBlock(d.chart, { height: 420, onPoint: onChartPoint, name: isMatches ? "matches" : "alliances" }),
    h("p", { class: "note", text: d.status }),
    h("div", { class: "row" }, h("span", { class: "muted small", style: { flex: "1 1 300px" }, text: d.explain }),
      btn("Explain this chart", () => infoDialog("What this chart shows", d.explain_long), "small"))];
};

// ---- Live Scouting ---------------------------------------------------------
function youtubeEmbed(url) {
  try {
    const u = new URL(url);
    let id = "";
    if (u.hostname === "youtu.be") id = u.pathname.slice(1);
    else if (u.searchParams.get("v")) id = u.searchParams.get("v");
    else { const m = u.pathname.match(/^\/(?:live|embed|shorts)\/([\w-]{6,})/); if (m) id = m[1]; }
    id = (id || "").split("/")[0];
    return /^[\w-]{6,}$/.test(id) ? `https://www.youtube-nocookie.com/embed/${id}?playsinline=1&rel=0` : "";
  } catch { return ""; }
}

RENDER.live = async () => {
  const d = await view("live", { match: S.live.match });
  S.live.match = d.match;
  const query = h("input", { type: "search", value: S.live.query || d.query });
  const url = h("input", { type: "url", value: d.stream, placeholder: "https://www.youtube.com/watch?v=…", autocapitalize: "off" });
  const embed = d.stream ? youtubeEmbed(d.stream) : "";
  const player = embed ? h("div", { class: "video" }, h("iframe", { src: embed, title: "Meet video", allow: "autoplay; encrypted-media; picture-in-picture; fullscreen", allowfullscreen: true }))
    : h("p", { class: "muted", text: d.stream ? "This link can't play inside the page (channel or search links can't). Use Open video." : "No stream selected" });
  const yt = async (live) => {
    S.live.query = query.value;
    const r = await runAction("youtube", { live, query: query.value }, false);
    if (r.open) window.open(r.open, "_blank", "noopener");
  };
  const notesBox = h("div");
  const drawNotes = (nd) => {
    notesBox.textContent = "";
    notesBox.append(notesCard(nd, async (patch) => { Object.assign(S.live, patch); drawNotes(await view("live", { match: S.live.match })); }));
  };
  drawNotes(d);
  return [
    h("p", { class: "muted", text: "Find a meet stream and record observations against the exact match. Notes are saved in the session." }),
    h("div", { class: "grid2" },
      h("div", null, card("Meet video", player,
        h("div", { class: "row" }, field("Paste a YouTube stream or video URL", url, "wide")),
        h("div", { class: "row" },
          btn("Use this stream", async () => { const r = await runAction("stream", { url: url.value }); if (r.ok) rerender(); }, "primary"),
          btn("Open video", () => { const u = url.value || d.stream; if (u) window.open(u, "_blank", "noopener"); else toast("Search for a live meet or paste a YouTube URL first."); }),
          d.stream ? btn("Clear", async () => { await runAction("stream", { url: "" }, false); rerender(); }, "small") : null),
        h("div", { class: "row" }, field("Search YouTube for this meet", query, "wide")),
        h("div", { class: "row" }, btn("Search live streams", () => yt(true)), btn("Search all videos", () => yt(false))),
        h("p", { class: "note", text: "Pick the event stream on YouTube, copy its link, paste it above, and it plays here beside your notes." }))),
      notesBox),
  ];
};

function notesCard(d, update) {
  if (!d.matches.length) return card("Match notes", h("p", { class: "muted", text: "Load or paste a match schedule before adding match-specific notes." }));
  const counts = d.note_counts || {};
  const match = select(d.matches.map((m) => [m, counts[m] ? `${m}  (${counts[m]})` : m]), d.match, (v) => update({ match: v }));
  const mt = d.match_teams;
  const teamOpts = [d.teams[0]];
  if (mt) { for (const n of mt[0]) teamOpts.push([String(n), `${n} (red)`]); for (const n of mt[1]) teamOpts.push([String(n), `${n} (blue)`]); }
  for (const t of d.teams.slice(1)) if (!mt || !mt.flat().map(String).includes(t)) teamOpts.push(t);
  const team = select(teamOpts, S.live.team || d.teams[0], (v) => { S.live.team = v; });
  const phase = select(d.phases, S.live.phase || "Overall", (v) => { S.live.phase = v; });
  const text = h("textarea", { rows: 4, placeholder: "What the robot/team did, outcome, reliability, issue, etc." });
  const list = h("div", { class: "notes" });
  if (!d.notes.length) list.append(h("p", { class: "muted small", text: "No notes for this match yet." }));
  d.notes.forEach((n, i) => list.append(h("div", { class: "notecard" },
    h("span", { class: "meta", text: `${n.time || ""} · ${n.team || "General"} · ${n.phase || "Overall"}` }),
    h("button", { class: "btn small danger", type: "button", onclick: async () => { await runAction("note_delete", { match: d.match, index: i }, false); update({}); } }, "Delete"),
    h("span", { class: "txt", text: n.text || "" }))));
  return card("Match notes",
    h("div", { class: "form" }, field("Match", match), field("Team", team), field("Phase", phase)),
    h("label", { class: "field", style: { marginTop: "10px" } }, h("span", { text: "Observation" }), text),
    h("div", { class: "row" },
      btn("Add note", async () => {
        const r = await runAction("note_add", { match: d.match, team: team.value, phase: phase.value, text: text.value });
        if (r.ok) update({});
      }, "primary"),
      h("span", { class: "spacer" }), btn("Save session", saveSession)),
    list);
}

// ---- Settings --------------------------------------------------------------
RENDER.settings = async () => {
  const d = await view("settings");
  const st = { rating: d.rating, faithful: d.faithful, epa_live: d.epa_live, epa_future: d.epa_future, k0: d.k0, kmin: d.kmin,
    quirks: Object.fromEntries(d.quirks.map((q) => [q.key, q.value])), mpt: d.mpt, margin: d.margin, poa: d.poa };
  const apply = async (patch) => { Object.assign(st, patch); await runAction("settings", st, false); rerender(); };
  const num = (v, key, w = "90px") => h("input", { type: "number", step: "any", value: v, style: { width: w }, onchange: (e) => { st[key] = e.target.value; } });
  const k0 = num(d.k0, "k0"), kmin = num(d.kmin, "kmin"), mpt = num(d.mpt, "mpt"), margin = num(d.margin, "margin");
  for (const el of [k0, kmin]) el.addEventListener("change", () => apply({}));
  return [
    card("Predictions are based on",
      h("div", { class: "row" }, h("div", { class: "seg", role: "radiogroup" }, [["CAP", "CAP (AutoScout's own rating)"], ["OPR", "OPR (non-penalty)"], ["EPA", "EPA (learns match by match)"]].map(([k, lab]) =>
        h("button", { type: "button", class: d.rating === k ? "on" : "", role: "radio", "aria-checked": d.rating === k ? "true" : "false", onclick: () => apply({ rating: k }) }, lab))),
      btn("What are CAP, OPR and EPA?", () => infoDialog("CAP, OPR and EPA", S.boot.cap_help), "small")),
      h("p", { class: "note", text: "The per-team number that is added up for each alliance: it drives the match predictions, the accuracy count, projected standings and every alliance table." }),
      h("div", { class: "row" }, check("EPA keeps learning from this event's played matches", d.epa_live, (v) => apply({ epa_live: v })),
        h("span", { text: "k max ×mean" }), k0, h("span", { text: "k min ×mean" }), kmin),
      h("div", { class: "row" }, check("EPA also learns from events these teams played after this one (season-long EPA, like StatCube; reload the meet with history to fetch them)", d.epa_future, (v) => apply({ epa_future: v }))),
      h("p", { class: "note", text: d.epa_info })),
    h("div", { class: "grid-even", style: { marginBottom: "14px" } },
      card("Fidelity",
        check("Sheet-faithful mode (reproduce the original spreadsheets' quirks)", d.faithful, (v) => apply({ faithful: v })),
        h("p", { class: "note", text: "Off (the default) uses the corrected behaviour: the latest event's stats, real auto/endgame OPR components, and the OPR history in date order." }),
        h("div", { class: "form", style: { marginTop: "8px" } }, d.quirks.map((q) => field(q.label, select(d.tristate, q.value, (v) => apply({ quirks: { ...st.quirks, [q.key]: v } })))))),
      card("Numbers",
        h("div", { class: "form" }, field("Matches per team (blank = count from the schedule)", mpt), field("Margin points (Sheet18: trust the call only above this)", margin)),
        h("div", { class: "row" }, check('Show the "potential of accuracy" column on Matches (experimental)', d.poa, (v) => { st.poa = v; })),
        h("div", { class: "row" }, btn("Apply", () => apply({}), "primary")))),
    h("div", { class: "grid-even", style: { marginBottom: "14px" } },
      card("Cache",
        h("p", { class: "muted", text: d.cache_count == null ? "FTCScout responses are cached in this browser." : `${d.cache_count} FTCScout responses cached in this browser.` }),
        h("div", { class: "row" }, btn("Clear cache", async () => { await runAction("clear_cache", {}); rerender(); }),
          check("Offline (cache only — never touch the network)", d.offline, async (v) => { await runAction("offline", { offline: v }, false); toast(v ? "Offline: only cached data is used." : "Online."); })),
        h("p", { class: "note", text: "Sessions and the cache live in this browser's storage. On iPhone, add the site to your home screen so Safari keeps them, and download session files as a backup." })),
      card("About",
        h("b", { text: d.about.title }), h("p", { class: "muted", text: d.about.blurb, style: { marginTop: "6px" } }),
        h("p", { class: "muted small" }, d.about.org, "  ·  ", h("a", { href: d.about.website, target: "_blank", rel: "noopener" }, "AutoScout website")),
        h("p", { class: "muted small", text: `Web version: the desktop AutoScout ${S.boot.version} engine running in your browser (Pyodide). Data from the public FTCScout API; no account or key needed.` }),
        h("div", { class: "row" }, btn("What is CAP?", () => infoDialog("CAP, OPR and EPA", S.boot.cap_help), "small")))),
  ];
};

// ---------------------------------------------------------------------------
// Start up
// ---------------------------------------------------------------------------
function bootFailed(msg) {
  const sp = $("#splash");
  if (!sp) { toast(msg, true); return; }
  sp.classList.add("err");
  $("#splashText").textContent = "AutoScout could not start.\n" + msg + "\n\nCheck your connection and reload. The first visit needs internet to download the engine.";
  const bar = sp.querySelector(".bar");
  if (bar) bar.remove();
}

async function start() {
  buildNav();
  $("#menuBtn").addEventListener("click", () => ($("#nav").classList.contains("open") ? closeMenu() : openMenu()));
  $("#scrim").addEventListener("click", closeMenu);
  $("#switchBtn").addEventListener("click", quickSwitch);
  $("#saveBtn").addEventListener("click", saveSession);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); quickSwitch(); }
  });
  try {
    S.boot = await call("boot", "boot", {});
  } catch (e) {
    bootFailed(e.message);
    return;
  }
  setHead(S.boot.head);
  window.addEventListener("hashchange", render);
  if (!location.hash) history.replaceState(null, "", "#/overview");
  await render();
  if (S.boot.restored) toast(`Reopened ${S.boot.restored}.`);
  if ("serviceWorker" in navigator && (location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname))) navigator.serviceWorker.register("sw.js").catch(() => {});
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
}
start();
