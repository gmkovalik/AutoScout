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

function displayValue(value) {
  if (value == null) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(displayValue).filter(Boolean).join(" · ");
  if (typeof value === "object") {
    for (const key of ["label", "name", "title", "team_name", "event_name", "text", "value", "number", "code"]) {
      if (value[key] != null && value[key] !== value) {
        const result = displayValue(value[key]);
        if (result) return result;
      }
    }
    try { return JSON.stringify(value); } catch (_) { return ""; }
  }
  return String(value);
}

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = displayValue(v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (PROPS.has(k)) el[k] = v;
      else el.setAttribute(k, v === true ? "" : displayValue(v));
    }
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : displayValue(kid));
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
    h("div", { class: "lab", text: m.label }), h("div", { class: "val", text: displayValue(m.value) || "-" }))));
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
  if (m.type === "status") {
    S.engineStatus = m.text;
    for (const el of document.querySelectorAll("#splashText, .engine-status-text")) el.textContent = m.text;
    return;
  }
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
  notesTab: "matches", notesTeam: "", teamTab: "pick", teamsNoteFilter: "all", pickSort: "order", pickDesc: null, chatDraft: "", notesQuery: "", teamNoteDraft: null, engineStatus: "Starting…",
  auto: { on: false, minutes: 2, timer: null, initialized: false },
};

const PAGES = [
  ["WORKSPACE", [["overview", "Overview", "⌂", "Competition command center"],
    ["competition", "Schedule Data", "\u2691\uFE0E", "Live schedule, match details and event statistics"],
    ["setup", "Setup", "◈", "Event and session setup"],
    ["teams", "Teams", "▦", "Your event team database"],
    ["notes", "Notes", "✎", "Match notes and team notes"],
    ["teampage", "Team Page", "◎", "Your account's pick list and team chat"],
    ["research", "Team Research", "⌕", "Public FTC team intelligence"],
    ["myteam", "My Team", "\u2605\uFE0E", "Your team's performance"]]],
  ["STRATEGY", [["standings", "Standings", "☷", "Event standings"],
    ["alliances", "Alliances", "◇", "Alliance selection workspace"],
    ["winrate", "Win Rate Curve", "⌁", "Alliance win-rate modeling"],
    ["analysis", "Analysis", "◉", "Prediction quality and insights"]]],
  ["INSIGHTS", [["charts", "Charts", "▥", "Performance visualizations"]]],
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
  return { page: page === "matches" ? "competition" : (PAGE[page] ? page : "overview"), params };
}
function go(page, params) {
  const q = params ? "?" + new URLSearchParams(params).toString() : "";
  const target = "#/" + (page === "matches" ? "competition" : page) + q;
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
  if (S.bootError) return;
  if (!S.boot && page !== "overview") paintPage(page, [engineWaitCard()]);   // the engine is still starting
  let nodes;
  try {
    nodes = await RENDER[page](params);
  } catch (e) {
    nodes = [card("Something went wrong", h("p", { class: "pre", text: e.message }),
      h("p", { class: "muted small", text: "If this keeps happening, reload the page. Your saved sessions are kept." }))];
  }
  if (token !== renderToken || S.bootError) return;   // a newer render started meanwhile, or the engine failed
  const scroll = window.scrollY;
  paintPage(page, nodes);
  window.scrollTo(0, changed ? 0 : scroll);
  for (const fn of afterRender.splice(0)) fn();
}
const afterRender = [];
function paintPage(page, nodes) {
  const main = $("#main");
  main.textContent = "";
  const info = PAGE[page];
  main.append(h("div", { class: "pagehead" }, h("h1", { text: info.title }), h("span", { class: "ctx", text: info.ctx })));
  if (S.loading) main.append(loadingBanner());
  main.append(...[].concat(nodes).filter(Boolean));
}
function engineWaitCard() {
  return h("div", { class: "card engine-wait" },
    h("div", { class: "row" }, h("b", { text: "Starting the AutoScout engine" }),
      h("div", { class: "progress indeterminate" }, h("div")),
      h("span", { class: "muted small engine-status-text", text: S.engineStatus })),
    h("p", { class: "muted small", text: "The first visit downloads the engine (about 7 MB). After that it opens from this device, even offline." }));
}

async function rerender() { await render(); }

// ---------------------------------------------------------------------------
// Tables (the desktop's SortableTree: click a heading to sort)
// ---------------------------------------------------------------------------
function sortKey(v) {
  const s = String(v ?? "").trim().replace(/%/g, "").replace(/\+/g, "");
  if (s === "" || s === "n/a" || s === "-") return [2, 0, ""];
  const n = Number(s.replace("−", "-"));
  if (s !== "" && !Number.isNaN(n)) return [0, n, ""];
  return [1, 0, s.toLowerCase()];
}
function cmpKeys(a, b) { return a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0); }

function dataTable(t, opts = {}) {
  const { id = "t", onRow = null, sticky = 1, wrap = [], short = false, flash = null, empty = "Nothing to show yet.", matchStyle = false, mineTeam = null,
    cellClass = null } = opts;
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
      const heading = String(c.label || "").trim().toLowerCase();
      const headerSide = heading === "pred." ? "prediction-neutral-header" :
        heading.includes("blue") ? "alliance-blue-header" :
        heading.includes("red") ? "alliance-red-header" : "";
      return h("th", { class: [align(c), i < sticky ? "sticky" : "", headerSide].join(" "), scope: "col",
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
        const classes = [align(c), i < sticky ? "sticky" : "", wrap.includes(c.key) ? "wrap" : ""];
        if (matchStyle) {
          if (c.key === "label" && /^Q(?:ual)?[- ]?\d+/i.test(String(v))) classes.push("qual-number");
          if (mineTeam != null && ["r1", "r2", "b1", "b2"].includes(c.key) &&
              String(v).trim() === String(mineTeam).trim()) classes.push("selected-team-number");
        }
        if (cellClass) classes.push(cellClass(c.key, v, iid) || "");
        tr.append(h("td", { class: classes.join(" ") }, displayValue(v)));
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
async function loadEvent(args, { rerenderAfter = true } = {}) {
  if (S.loading) return;
  S.loading = true;
  loadUI.msg = "Contacting FTCScout…"; loadUI.frac = 0;
  updateLoadUI();
  try {
    const r = await act("load", args, (msg, frac) => { loadUI.msg = msg; loadUI.frac = frac; updateLoadUI(); });
    toast(r.message, !r.ok);
    loadUI.msg = r.message || "";
    return r;
  } catch (e) {
    toast(e.message, true);
    return { ok: false, message: e.message };
  } finally {
    S.loading = false;
    if (rerenderAfter) rerender();
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

const OVERVIEW_LABELS = ["TEAMS TRACKED", "MATCHES LOADED", "MY TEAM", "SESSION STATUS"];
RENDER.overview = async () => {
  // Before the engine has started, show the page straight away and fill in the numbers when it is ready.
  const d = S.boot ? await view("overview") : { metrics: OVERVIEW_LABELS.map((label) => ({ label, value: "…" })), head: { loaded: false } };
  const metrics = h("div", null, metricsRow(d.metrics));
  if (!S.boot) {
    metrics.append(h("p", { class: "muted small engine-line" }, h("span", { class: "engine-status-text", text: S.engineStatus })));
    afterRender.push(() => {
      const token = renderToken;
      view("overview").then((full) => {
        if (token !== renderToken || !metrics.isConnected) return;
        metrics.replaceChildren(metricsRow(full.metrics));
        const first = document.querySelector(".actions .action");
        if (first) first.classList.toggle("primary", !full.head.loaded);
      }).catch(() => {});
    });
  }
  const action = (n, title, desc, page, primary) => h("a", { class: "action" + (primary ? " primary" : ""), href: "#/" + page },
    h("b", { text: `${n}   ${title}` }), h("span", { text: desc }));
  const steps = [["01", "Set up your event", "Choose a season, event, and your team."],
    ["02", "Build your dataset", "Load event data and add reliable pit or match observations."],
    ["03", "Understand performance", "Use charts, CAP, EPA, and match analysis together."],
    ["04", "Make a strategy call", "Compare alliances and validate predictions against real results."]];
  return [
    hero("COMPETITION COMMAND CENTER", "Scout smarter. Pick stronger alliances.",
      "Your match data, team intelligence, and strategy tools all in one workspace."),
    metrics,
    h("div", { class: "grid-even" },
      card("Quick actions", h("div", { class: "actions" },
        action("01", "Load an event", "Bring in teams, schedule, and match results.", "setup", !d.head.loaded),
        action("02", "Scout a match", "Review matchups and enter your observations.", "matches"),
        action("03", "Research any team", "Explore public season data, records, and performance graphs.", "research"),
        action("04", "Build an alliance", "Compare team strengths and identify promising combinations.", "alliances"))),
      card("Workflow", h("div", { class: "steps" }, steps.map(([n, t, s]) => h("div", { class: "step" },
        h("span", { class: "n", text: n }), h("div", null, h("b", { text: t }), h("span", { text: s }))))),
      h("p", { class: "note", text: isPhone() ? "Tip: the ☰ menu lists every workspace." : "Tip: use Ctrl+K (⌘K on a Mac) to jump to any workspace." }))),
    card("Use it on a phone",
      h("p", { text: "This is the same AutoScout as the desktop app, running in the browser. Add it to your home screen and it opens like an app, even with no signal at the venue once an event is loaded." }),
      h("p", { class: "muted small", text: "iPhone: Safari ▸ Share ▸ Add to Home Screen.   Android: Chrome ▸ ⋮ ▸ Install app.   Data stays on this device; use Setup ▸ Download session file to move it to another device or to the desktop app." })),
  ];
};

RENDER.competition = async (params) => {
  const d = await view("competition", {});
  if (d.head.loaded && !S.auto.initialized) { S.auto.initialized = true; setAuto(true); }
  const table = {
    columns: [...d.matches.columns, { key: "open", label: "MATCH DATA", align: "center" }],
    rows: d.matches.rows.map(([iid, vals, tags]) => [iid, [...vals, "OPEN ↗"], tags]),
  };
  const matches = dataTable(table, {
    id: "schedule-matches", sticky: 1, matchStyle: true, mineTeam: d.head.my_team, short: true,
    wrap: ["predicted", "actual"],
    empty: "Load an event to populate the matches table.",
    onRow: (label) => openMatchDetails(label),
  });
  matches.classList.add("schedule-matches");
  return [
    hero("COMPETITION DAY", "Everything you need between matches.", d.head.loaded ? d.line : "Load an event to populate your matches."),
    metricsRow(d.metrics),
    h("div", { class: "row", style: { marginBottom: "14px" } },
      btn("Refresh meet data", async () => { const r = await runAction("refresh", {}); if (r.ok) rerender(); }, "primary"),
      check("Auto-refresh", S.auto.on, (v) => setAuto(v)),
      h("span", { class: "muted small", text: "every" }),
      h("input", { type: "number", min: 1, max: 60, value: S.auto.minutes, style: { width: "64px" }, onchange: (e) => { S.auto.minutes = Math.max(1, Number(e.target.value) || 2); if (S.auto.on) setAuto(true); } }),
      h("span", { class: "muted small", text: "min" }),
      btn("Alliance selection", () => go("alliances")),
      btn("Team research", () => go("research")), h("span", { class: "spacer" }), btn("Save session", saveSession)),
    card("Matches", matches),
  ];
};

RENDER.setup = async () => {
  const d = await view("setup");
  const season = h("input", { type: "number", value: d.season, min: 2019, max: 2100, inputmode: "numeric" });
  const team = h("input", { type: "number", value: d.team, inputmode: "numeric", placeholder: "e.g. 12928" });
  const extra = textInput(d.extra, { placeholder: "e.g. 11225 19013" });
  const search = h("input", { type: "search", value: S.eventSearch.query, placeholder: "Search by meet name or event code", autocomplete: "off" });
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
        h("span", null, h("b", { text: ev.name || ev.code }), h("small", { text: ev.meta || ev.code })),
        h("span", { class: "event-load-label", text: "Load event →" })));
    }
    results.hidden = !S.eventSearch.results.length;
  }
  async function searchEvents() {
    const query = search.value.trim();
    S.eventSearch.query = query;
    if (!query) {
      S.eventSearch.status = "Enter a meet name or event code to search.";
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
      h("p", { class: "muted", text: "Find your competition by meet name or event code. Choose a result to load it automatically." }),
      h("div", { class: "form" }, field("Season", season), field("My team number", team)),
      h("div", { class: "row", style: { marginTop: "10px" } }, field("Find a meet", search, "wide"), searchBtn),
      searchStatus, results,
      h("details", { class: "adv" }, h("summary", { text: "Advanced options" }), h("div", null,
        field("Extra team numbers (optional)", extra),
        check("Offline (cache only - never touch the network)", offline, (v) => { offline = v; }),
        check("Fetch each team's season history (needed for projected OPR)", history, (v) => { history = v; }))),
      h("div", { class: "row", style: { marginTop: "14px" } },
        h("div", { class: "progress", id: "setupProgress" }, h("div", { style: { width: (loadUI.frac * 100).toFixed(0) + "%" } }))),
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
        "One match per line - the first two team numbers are red, the next two blue.\n    Q-1  11225 19013  19564 23213\n    1    11225 19013 vs 19564 23213   104-66\nA trailing red-blue score marks the match as played." }),
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
  const [d] = await Promise.all([view("teams", { ...S.panel, team }), fetchCloudNotes()]);
  // Mark which teams already have scouting notes (this device's and, when signed in, the shared ones).
  const counts = noteCountsByTeam(d.notes);
  const table = withNotesColumn(d.table, counts);
  const box = h("div");
  const withNotes = table.rows.filter((r) => counts[String(r[0])]).length;
  const filters = [["all", `All (${table.rows.length})`], ["yes", `With notes (${withNotes})`], ["no", `No notes yet (${table.rows.length - withNotes})`]];
  const seg = h("div", { class: "seg", role: "radiogroup", "aria-label": "Show teams" });
  const draw = () => {
    const f = S.teamsNoteFilter || "all";
    for (const b of seg.children) { const on = b.dataset.f === f; b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false"); }
    const rows = table.rows.filter((r) => f === "all" || (f === "yes") === !!counts[String(r[0])]);
    box.replaceChildren(dataTable({ ...table, rows }, { id: "teams", sticky: 2, flash: team || null, onRow: (iid) => go("teams", { team: iid }),
      cellClass: (key, v) => key === "notes" ? (v ? "notes-yes" : "notes-no") : "",
      empty: table.rows.length ? "No teams match this filter." : "No teams loaded. Load a meet on the Setup tab first." }));
  };
  for (const [f, label] of filters) seg.append(h("button", { type: "button", role: "radio", "data-f": f, onclick: () => { S.teamsNoteFilter = f; draw(); } }, label));
  draw();
  const nodes = [
    h("p", { class: "muted", text: "Select a team to view its charts, league standings, and every meet it attended this season." }),
    table.rows.length ? h("div", { class: "row teams-filter" }, seg,
      h("span", { class: "muted small" }, h("span", { class: "notes-yes legend" }, "3"), " = scouting notes on this team",
        teamOn() ? ` (this device + ${accountName()})` : "")) : null,
    box,
  ];
  if (team && d.editor) afterRender.push(() => openTeamSheet(d));
  else if (team) afterRender.push(() => toast(`Team ${team} is not at this event.`, true));
  return nodes;
};

// ---------------------------------------------------------------------------
// Team account and shared notes
//
// Notes are always saved in this browser's session first (exactly as before),
// so scouting works with no account and no signal. When signed in to a team
// account, each new note is also shared to the team's files, and the Notes
// pages show this device's notes and the team's shared notes together.
// A note is identified by its team/match, time and text, the same key the
// earlier sync used, so notes shared before this version are recognised.
// ---------------------------------------------------------------------------
const Team = window.AutoScoutTeamStorage || null;
const teamReady = () => !!(Team && Team.configured());
const teamOn = () => !!(Team && Team.signedIn());
/** Accounts are a team number or a username (stored in lower case). */
const validAccountName = (v) => /^(\d{1,8}|[a-z][a-z0-9_-]{2,29})$/.test(String(v || ""));
function accountName(id = Team && Team.currentTeam()) { return /^\d+$/.test(String(id || "")) ? `Team ${id}` : String(id || ""); }
const CLOUD = { team: null, at: 0, matches: {}, teams: {}, error: "", loading: null };
const CLOUD_FRESH_MS = 60 * 1000;            // reuse shared notes fetched in the last minute
const photoCache = new Map();                // photoRef -> data URL (or a pending promise)

const teamNoteKey = (team, n) => JSON.stringify([String(team), String(n.time || ""), String(n.text || "")]);
const matchNoteKey = (label, n) => JSON.stringify([String(label), String(n.time || ""), String(n.team || "General"),
  String(n.phase || "Overall"), String(n.text || "")]);
const photoId = (raw) => String(raw).replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 75);
function eventId() { const hd = S.head || {}; return hd.code ? `${hd.season}/${hd.code}` : ""; }
function forThisEvent(n) { return !n.event || n.event === eventId(); }   // older shared notes have no event

function resetCloud() {
  Object.assign(CLOUD, { team: teamOn() ? Team.currentTeam() : null, at: 0, matches: {}, teams: {}, error: "", loading: null });
}
function cloudReady() { return teamOn() && CLOUD.team === Team.currentTeam() && CLOUD.at > 0; }

async function fetchCloudNotes(force = false) {
  if (!teamOn()) return CLOUD;
  const team = Team.currentTeam();
  if (CLOUD.team !== team) resetCloud();
  if (!force && CLOUD.at && Date.now() - CLOUD.at < CLOUD_FRESH_MS) return CLOUD;
  if (CLOUD.loading) return CLOUD.loading;
  CLOUD.loading = (async () => {
    try {
      const [m, t] = await Promise.all([
        Team.getFileOr("match-notes.json", { schema: 1, matches: {} }),
        Team.getFileOr("team-notes.json", { schema: 1, teams: {} })]);
      if (CLOUD.team === team) Object.assign(CLOUD, { at: Date.now(), matches: m.data.matches || {}, teams: t.data.teams || {}, error: "" });
    } catch (e) {
      if (CLOUD.team === team) CLOUD.error = e.message;
    } finally {
      CLOUD.loading = null;
    }
    return CLOUD;
  })();
  return CLOUD.loading;
}

async function uploadPhoto(rawId, data) {
  if (!data || !String(data).startsWith("data:image/")) return "";
  const id = photoId(rawId);
  await Team.putFile("photos/" + id + ".json", { data });
  photoCache.set(id, data);
  return id;
}

/** Share notes to the team files. teamNotes: [{team,time,text,photo}], matchNotes: [{match,time,team,phase,text,photo}].
    Notes already shared are skipped, so photos are only uploaded once. Returns how many were added. */
async function shareNotes(teamNotes, matchNotes) {
  await fetchCloudNotes(true);
  if (CLOUD.error) throw new Error(CLOUD.error);
  const ev = eventId();
  const haveTeam = new Set(Object.entries(CLOUD.teams).flatMap(([t, rows]) => rows.map((n) => teamNoteKey(t, n))));
  const haveMatch = new Set(Object.entries(CLOUD.matches).flatMap(([m, rows]) => rows.map((n) => matchNoteKey(m, n))));
  const newTeam = [], newMatch = [];
  for (const n of teamNotes) {
    if (haveTeam.has(teamNoteKey(n.team, n))) continue;
    const photoRef = n.photo ? await uploadPhoto("t_" + n.team + "_" + n.time, n.photo) : (n.photoRef || "");
    newTeam.push({ team: String(n.team), row: { time: n.time, text: n.text, photoRef } });
  }
  for (const [i, n] of matchNotes.entries()) {
    if (haveMatch.has(matchNoteKey(n.match, n))) continue;
    const photoRef = n.photo ? await uploadPhoto("m_" + n.match + "_" + n.time + "_" + i, n.photo) : (n.photoRef || "");
    newMatch.push({ match: String(n.match), row: { time: n.time, team: n.team || "General", phase: n.phase || "Overall", text: n.text, photoRef, ...(ev ? { event: ev } : {}) } });
  }
  if (newTeam.length) {
    const saved = await Team.updateFile("team-notes.json", { schema: 1, teams: {} }, (data) => {
      data.schema = data.schema || 1;
      const teams = data.teams = data.teams || {};
      let added = 0;
      for (const { team, row } of newTeam) {
        const rows = teams[team] = teams[team] || [];
        if (!rows.some((n) => teamNoteKey(team, n) === teamNoteKey(team, row))) { rows.push(row); added++; }
      }
      return added > 0;
    });
    CLOUD.teams = saved.teams || {};
  }
  if (newMatch.length) {
    const saved = await Team.updateFile("match-notes.json", { schema: 1, matches: {} }, (data) => {
      data.schema = data.schema || 1;
      const matches = data.matches = data.matches || {};
      let added = 0;
      for (const { match, row } of newMatch) {
        const rows = matches[match] = matches[match] || [];
        if (!rows.some((n) => matchNoteKey(match, n) === matchNoteKey(match, row))) { rows.push(row); added++; }
        rows.sort((a, b) => String(a.time || "").localeCompare(String(b.time || "")));
      }
      return added > 0;
    });
    CLOUD.matches = saved.matches || {};
  }
  CLOUD.at = Date.now();
  return newTeam.length + newMatch.length;
}

/** Share every note saved on this device that the team does not have yet. */
async function shareAllLocalNotes() {
  const d = await view("notes");
  const teamNotes = (d.team_notes || []).map((n) => ({ team: n.team, time: n.time, text: n.text, photo: n.photo }));
  const matchNotes = (d.matches || []).flatMap((m) => (m.notes || []).map((n) => ({ match: m.label, ...n })));
  return shareNotes(teamNotes, matchNotes);
}

async function unshareNote(kind, group, key, photoRef) {
  const file = kind === "team" ? "team-notes.json" : "match-notes.json";
  const field = kind === "team" ? "teams" : "matches";
  const keyOf = kind === "team" ? teamNoteKey : matchNoteKey;
  const saved = await Team.updateFile(file, { schema: 1, [field]: {} }, (data) => {
    const rows = (data[field] || {})[group] || [];
    const i = rows.findIndex((n) => keyOf(group, n) === key);
    if (i < 0) return false;
    rows.splice(i, 1);
    if (!rows.length) delete data[field][group];
    return true;
  });
  CLOUD[field] = saved[field] || {};
  if (photoRef) {
    const stillUsed = Object.values(CLOUD.teams).concat(Object.values(CLOUD.matches)).flat().some((n) => n.photoRef === photoRef);
    if (!stillUsed) { Team.deleteFile("photos/" + photoRef + ".json").catch(() => {}); photoCache.delete(photoRef); }
  }
}

// ---- Adding and deleting notes (local first, then shared) ------------------
async function addTeamNote(args) {
  const r = await runAction("team_note_add", args, false);
  if (!r.ok) { if (!r.message) toast("Could not save team note.", true); return r; }
  if (teamOn() && r.note) {
    try { await shareNotes([{ ...r.note, photo: args.photo }], []); toast(`Saved and shared with ${accountName()}.`); }
    catch (e) { toast("Saved on this device. Not shared yet: " + e.message, true); }
  } else toast(r.message);
  return r;
}
async function addMatchNote(args) {
  const r = await runAction("note_add", args, false);
  if (!r.ok) { if (!r.message) toast("Could not save note.", true); return r; }
  if (teamOn() && r.note) {
    try { await shareNotes([], [{ match: args.match, ...r.note, photo: args.photo }]); toast(`${r.message} Shared with ${accountName()}.`); }
    catch (e) { toast("Saved on this device. Not shared yet: " + e.message, true); }
  } else toast(r.message);
  return r;
}
/** n: a merged note ({local, localIndex, shared, photoRef}). Shared notes are removed for the whole team. */
function deleteNote(kind, group, n, after) {
  const key = kind === "team" ? teamNoteKey(group, n) : matchNoteKey(group, n);
  const run = async () => {
    try {
      if (n.shared) await unshareNote(kind, group, key, n.photoRef);
      if (n.local) {
        const r = await act(kind === "team" ? "team_note_delete" : "note_delete",
          kind === "team" ? { team: group, index: n.localIndex } : { match: group, index: n.localIndex });
        if (!r.ok) throw new Error(r.message || "Could not delete the note.");
      }
      toast("Note deleted.");
    } catch (e) { toast("Could not delete the note: " + e.message, true); }
    if (after) after();
  };
  if (n.shared) confirmDialog("Delete shared note?", `This removes the note for everyone signed in as ${accountName()}.`, "Delete", run);
  else run();
}

// ---- Merging this device's notes with the team's ---------------------------
function mergedTeamNotes(local) {
  const out = new Map();
  for (const n of local || []) out.set(teamNoteKey(n.team, n), { ...n, local: true, localIndex: n.index, shared: false });
  if (cloudReady()) {
    for (const [team, rows] of Object.entries(CLOUD.teams)) for (const n of rows || []) {
      const k = teamNoteKey(team, n), cur = out.get(k);
      if (cur) { cur.shared = true; cur.photoRef = n.photoRef || ""; }
      else out.set(k, { team: String(team), time: n.time || "", text: n.text || "", photo: "", photoRef: n.photoRef || "", local: false, shared: true });
    }
  }
  return [...out.values()].sort((a, b) => String(b.time).localeCompare(String(a.time)) || Number(a.team) - Number(b.team));
}
function mergedMatchNotes(label, local) {
  const out = new Map();
  (local || []).forEach((n, i) => {
    const note = { ...n, team: n.team || "General", phase: n.phase || "Overall" };
    out.set(matchNoteKey(label, note), { ...note, local: true, localIndex: n.index ?? i, shared: false });
  });
  if (cloudReady()) {
    for (const n of CLOUD.matches[label] || []) {
      if (!forThisEvent(n)) continue;
      const k = matchNoteKey(label, n), cur = out.get(k);
      if (cur) { cur.shared = true; cur.photoRef = n.photoRef || ""; }
      else out.set(k, { time: n.time || "", team: n.team || "General", phase: n.phase || "Overall", text: n.text || "", photo: "", photoRef: n.photoRef || "", local: false, shared: true });
    }
  }
  return [...out.values()].sort((a, b) => String(a.time).localeCompare(String(b.time)));
}

/** Number of scouting notes about each team: team notes plus match notes that name the team,
    from this device and (when signed in) the shared notes, each note counted once. */
function noteCountsByTeam(index) {
  const keys = new Map();
  const add = (team, key) => {
    team = String(team || "");
    if (!/^\d+$/.test(team)) return;
    if (!keys.has(team)) keys.set(team, new Set());
    keys.get(team).add(key);
  };
  for (const n of (index && index.team_notes) || []) add(n.team, "t" + teamNoteKey(n.team, n));
  for (const n of (index && index.match_notes) || []) add(n.team, "m" + matchNoteKey(n.match, n));
  for (const t of Object.keys((index && index.sheet_notes) || {})) add(t, "s");     // the note on the team sheet
  if (cloudReady()) {
    for (const [t, rows] of Object.entries(CLOUD.teams)) for (const n of rows || []) add(t, "t" + teamNoteKey(t, n));
    for (const [m, rows] of Object.entries(CLOUD.matches)) for (const n of rows || []) if (forThisEvent(n)) add(n.team, "m" + matchNoteKey(m, n));
  }
  return Object.fromEntries([...keys].map(([t, set]) => [t, set.size]));
}
/** A copy of the engine's teams table with a "Notes" column after the team name. */
function withNotesColumn(t, counts) {
  if (!t || !t.columns) return { columns: [], rows: [] };
  const at = Math.max(t.columns.findIndex((c) => c.key === "name"), t.columns.findIndex((c) => c.key === "team")) + 1;
  const columns = [...t.columns.slice(0, at), { key: "notes", label: "Notes", align: "center" }, ...t.columns.slice(at)];
  const rows = (t.rows || []).map(([iid, vals, tags]) => {
    const n = counts[String(iid)] || 0;
    return [iid, [...vals.slice(0, at), n ? String(n) : "", ...vals.slice(at)], [...(tags || []), n ? "noted" : "unnoted"]];
  });
  return { columns, rows };
}

// Shared photos load only when they scroll into view, a few at a time.
let photoSlots = 0;
const photoWaiting = [];
function loadSharedPhoto(ref) {
  if (photoCache.has(ref)) return Promise.resolve(photoCache.get(ref));
  const p = new Promise((resolve) => photoWaiting.push({ ref, resolve })).then((data) => { photoCache.set(ref, data); return data; });
  photoCache.set(ref, p);
  pumpPhotos();
  return p;
}
function pumpPhotos() {
  while (photoSlots < 3 && photoWaiting.length) {
    const { ref, resolve } = photoWaiting.shift();
    photoSlots++;
    Team.getFile("photos/" + ref + ".json").then((f) => resolve((f.data && f.data.data) || ""), () => { photoCache.delete(ref); resolve(""); })
      .finally(() => { photoSlots--; pumpPhotos(); });
  }
}
const photoObserver = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { photoObserver.unobserve(e.target); e.target._load(); }
}, { rootMargin: "300px" }) : null;
function notePhoto(n, className = "note-photo") {
  if (n.photo) return photoPreview(n.photo, className);
  if (!n.photoRef || !teamOn()) return null;
  const box = h("div", { class: "note-photo-slot" }, h("span", { class: "muted small", text: "Loading photo…" }));
  box._load = async () => {
    const data = await loadSharedPhoto(n.photoRef);
    box.replaceWith(data ? photoPreview(data, className) : h("span", { class: "muted small", text: "Photo unavailable." }));
  };
  if (photoObserver) photoObserver.observe(box); else box._load();
  return box;
}
/** The note typed on each team's sheet, shown with the team notes (edited on the sheet itself). */
function sheetNoteEntries(sheet) {
  return Object.entries(sheet || {}).map(([team, text]) => ({ team: String(team), time: "", text, photo: "", sheet: true }))
    .sort((a, b) => Number(a.team) - Number(b.team));
}
function noteBadge(n) {
  if (n.sheet) return h("span", { class: "note-badge plain", text: "Team sheet", title: "The note on this team's sheet (Teams list). Saved on this device." });
  if (!teamOn()) return null;
  if (n.shared && !n.local) return h("span", { class: "note-badge shared", text: "Team", title: "Shared by your team" });
  if (n.shared) return h("span", { class: "note-badge shared", text: "Shared", title: "Saved here and shared with your team" });
  return h("span", { class: "note-badge local", text: "Not shared", title: "Only on this device" });
}

/** The strip at the top of the Notes pages: where notes are saved, and sync controls. */
function notesSyncBar(unshared) {
  if (!teamReady()) return null;
  if (!teamOn()) {
    return h("div", { class: "sync-bar" },
      h("span", { class: "sync-dot off", "aria-hidden": "true" }),
      h("span", { class: "sync-text", text: "Notes are saved on this device only." }),
      btn("Sign in to share with your team", () => openSignIn(), "small primary"));
  }
  const when = CLOUD.at ? new Date(CLOUD.at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  return h("div", { class: "sync-bar" + (CLOUD.error ? " err" : "") },
    h("span", { class: "sync-dot" + (CLOUD.error ? " bad" : ""), "aria-hidden": "true" }),
    h("span", { class: "sync-text" },
      h("b", { text: accountName() }),
      CLOUD.error ? ` · Shared notes unavailable: ${CLOUD.error} Showing this device's notes.` :
        ` · shared notes${when ? " updated " + when : ""}`),
    unshared ? btn(`Share ${unshared} note${unshared === 1 ? "" : "s"} from this device`, async (e) => {
      e.target.disabled = true;
      try { const n = await shareAllLocalNotes(); toast(n ? `Shared ${n} note${n === 1 ? "" : "s"} with ${accountName()}.` : "Everything is already shared."); }
      catch (err) { toast("Could not share notes: " + err.message, true); }
      rerender();
    }, "small primary") : null,
    btn("Refresh", async (e) => { e.target.disabled = true; await fetchCloudNotes(true); rerender(); }, "small"));
}

RENDER.notes = async () => {
  const [d] = await Promise.all([view("notes"), fetchCloudNotes()]);
  return renderNotesWorkspace(d);
};

function renderNotesWorkspace(d) {
  const teamNotes = [...sheetNoteEntries(d.sheet_notes), ...mergedTeamNotes(d.team_notes)];
  const matchNotes = new Map((d.matches || []).map((m) => [m.label, mergedMatchNotes(m.label, m.notes)]));
  const unshared = !teamOn() || !cloudReady() ? 0 :
    teamNotes.filter((n) => !n.shared && !n.sheet).length + [...matchNotes.values()].flat().filter((n) => !n.shared).length;
  const tabs = h("div", { class: "notes-main-tabs", role: "tablist" },
    btn("Match Notes", () => { S.notesTab = "matches"; rerender(); }, S.notesTab === "matches" ? "primary" : ""),
    btn(`Team Notes${teamNotes.length ? ` (${teamNotes.length})` : ""}`, () => { S.notesTab = "teams"; rerender(); }, S.notesTab === "teams" ? "primary" : ""));
  return [notesSyncBar(unshared), tabs, ...(S.notesTab === "teams" ? renderTeamNotes(d, teamNotes) : renderNotesTimeline(d, matchNotes))];
}

function renderTeamNotes(d, notes) {
  const draft = S.teamNoteDraft = S.teamNoteDraft || { team: "", text: "", photo: "" };
  const counts = {};
  for (const n of notes) counts[n.team] = (counts[n.team] || 0) + 1;
  const teams = [...new Set([...(d.team_options || []), ...Object.keys(counts)])].sort((a, b) => Number(a) - Number(b));
  if (S.notesTeam && !teams.includes(String(S.notesTeam))) S.notesTeam = "";
  if (!draft.team && S.notesTeam) draft.team = S.notesTeam;

  // ---- add a note
  const listId = "team-note-teams";
  const team = h("input", { type: "text", inputmode: "numeric", pattern: "[0-9]*", value: draft.team, list: listId,
    placeholder: "Team number", autocomplete: "off", oninput: (e) => { draft.team = e.target.value.trim(); } });
  const teamList = h("datalist", { id: listId }, teams.map((t) => h("option", { value: t })));
  const text = h("textarea", { rows: 4, value: draft.text,
    placeholder: "Robot capabilities, strategy, reliability, driver skill, what they asked us for…",
    oninput: (e) => { draft.text = e.target.value; } });
  text.value = draft.text;
  const photoStatus = h("span", { class: "muted small", text: draft.photo ? "Photo attached" : "No photo attached" });
  const save = btn(teamOn() ? "Save and share" : "Save team note", async () => {
    if (!/^\d+$/.test(draft.team)) { toast("Enter the team number first.", true); team.focus(); return; }
    save.disabled = true;
    const r = await addTeamNote({ team: draft.team, text: draft.text, photo: draft.photo });
    save.disabled = false;
    if (r.ok) {
      S.teamNoteDraft = { team: draft.team, text: "", photo: "" };
      if (S.notesTeam) S.notesTeam = draft.team;        // a filtered list follows the team just noted
      rerender();
    }
  }, "primary");
  const where = teamOn() ? `Saved on this device and shared with ${accountName()}.` :
    "Saved with this AutoScout session on this device." + (teamReady() ? " Sign in to share with your team." : "");

  // ---- list, filtered by team and by text
  const listBox = h("div", { class: "team-note-list" });
  const drawList = () => {
    const q = (S.notesQuery || "").trim().toLowerCase();
    const shown = notes.filter((n) => (!S.notesTeam || n.team === String(S.notesTeam)) && (!q || n.text.toLowerCase().includes(q) || n.team.includes(q)));
    listBox.textContent = "";
    if (!shown.length) {
      listBox.append(h("p", { class: "muted small", text: notes.length ? "No notes match this filter." : "No team notes yet. Add an observation or attach a photo above." }));
      return;
    }
    for (const n of shown) {
      listBox.append(h("article", { class: "team-note-entry" },
        h("div", { class: "match-note-meta" },
          S.notesTeam ? h("strong", { text: "Team " + n.team }) :
            h("button", { class: "linkbtn strong", type: "button", title: "Show only this team", onclick: () => { S.notesTeam = n.team; rerender(); } }, "Team " + n.team),
          n.sheet ? null : h("span", { class: "note-time", text: n.time || "Time not recorded" }),
          noteBadge(n),
          n.sheet ? btn("Edit on team sheet", () => go("teams", { team: n.team }), "small") :
            btn("Delete", () => deleteNote("team", n.team, n, rerender), "small danger")),
        n.text ? h("p", { class: "match-note-text", text: n.text }) : null,
        notePhoto(n)));
    }
  };
  drawList();
  const filter = select([["", `All teams (${notes.length})`], ...teams.map((t) => [t, counts[t] ? `${t}  (${counts[t]})` : t])],
    S.notesTeam, (v) => { S.notesTeam = v; if (v) draft.team = v; rerender(); });
  const search = h("input", { type: "search", value: S.notesQuery || "", placeholder: "Search notes", autocomplete: "off",
    oninput: (e) => { S.notesQuery = e.target.value; drawList(); } });

  return [
    card("Add a team note",
      h("p", { class: "muted small", text: "Notes about a team, not one match. " + where }),
      h("div", { class: "form" }, field("Team number", h("span", { class: "combo" }, team, teamList))),
      field("Observation", text),
      h("div", { class: "row team-note-actions" },
        btn(draft.photo ? "Replace photo" : "Add photo", async () => {
          const p = await imageAttachment();
          if (p) { draft.photo = p; photoStatus.textContent = "Photo attached"; }
        }, "small"),
        photoStatus,
        draft.photo ? btn("Remove photo", () => { draft.photo = ""; photoStatus.textContent = "No photo attached"; }, "small") : null,
        h("span", { class: "spacer" }),
        save)),
    card(S.notesTeam ? `Notes for Team ${S.notesTeam}` : "All team notes",
      h("div", { class: "row notes-filter" }, field("Team", filter), field("Search", search, "wide"),
        S.notesTeam ? btn("Show all teams", () => { S.notesTeam = ""; rerender(); }, "small") : null),
      listBox),
  ];
}

function renderNotesTimeline(d, merged) {
  if (!d.loaded) return [card("Match notes", h("p", { class: "muted", text: "Load a meet on Setup to see its schedule and scouting notes here." }))];
  let total = 0;
  const groups = d.matches.map((m) => {
    const status = m.played ? "Played" : "Scheduled";
    const notes = merged.get(m.label) || [];
    total += notes.length;
    return h("section", { class: "match-note-group" },
      h("div", { class: "match-note-head" },
        h("div", null, h("h3", { text: m.label }), h("span", { class: "muted small", text: `Red: ${m.red.join(" · ")}  |  Blue: ${m.blue.join(" · ")}` })),
        h("div", { class: "match-note-actions" },
          h("span", { class: "note-status" + (m.played ? " played" : ""), text: status }),
          btn("Add note", () => openMatchDetails(m.label), "small"))),
      m.score ? h("p", { class: "muted small", text: `Score: ${m.score}` }) : null,
      notes.length ? h("div", { class: "match-note-list" }, notes.map((n) =>
        h("article", { class: "match-note-entry" },
          h("div", { class: "match-note-meta" },
            h("span", { class: "note-time", text: n.time || "Time not recorded" }),
            h("span", { text: n.team || "General" }),
            h("span", { text: n.phase || "Overall" }),
            noteBadge(n),
            btn("Delete", () => deleteNote("match", m.label, n, rerender), "small danger")),
          h("p", { class: "match-note-text", text: n.text || "" }),
          notePhoto(n)))) :
        h("p", { class: "muted small no-match-notes", text: "No notes recorded for this match yet." }));
  });
  return [
    h("div", { class: "notes-overview" },
      h("div", null, h("h2", { text: d.event || "Event schedule" }), h("p", { class: "muted small", text: `${d.matches.length} scheduled matches · ${total} scouting notes · listed in match order` })),
      btn("Open Schedule Data", () => go("competition"), "small")),
    ...groups,
  ];
}

// ---------------------------------------------------------------------------
// Team Page (signed-in accounts): a shared pick list and a team chat
//
// Both live in the account's shared files: picklist.json (one list per event)
// and chat.json (the latest 400 messages). Every change re-reads the file and
// applies the change on top, so two people editing at once don't undo each other.
// ---------------------------------------------------------------------------
const CHAT_KEEP = 400;
const CHAT_POLL_MS = 15000;
const TP = { pick: null, pickAt: 0, pickTeam: null, chat: null, chatTeam: null, chatTimer: null };

function readLocal(key, fallback) { try { const v = localStorage.getItem(key); return v == null ? fallback : JSON.parse(v); } catch (_) { return fallback; } }
function writeLocal(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode */ } }

RENDER.teampage = async () => {
  const tabs = h("div", { class: "notes-main-tabs", role: "tablist" },
    btn("Pick List", () => { S.teamTab = "pick"; rerender(); }, S.teamTab !== "chat" ? "primary" : ""),
    btn("Team Chat", () => { S.teamTab = "chat"; rerender(); }, S.teamTab === "chat" ? "primary" : ""));
  if (!teamReady()) return [card("Team Page", h("p", { class: "muted", text: "Team accounts are not set up for this site." }))];
  if (!teamOn()) {
    return [card("Team Page",
      h("p", { text: "Sign in to your team's account to use the shared pick list and the team chat." }),
      h("p", { class: "muted small", text: "Everyone signed in to the same account sees the same list and messages, on any device." }),
      h("div", { class: "row" }, btn("Sign in", () => openSignIn(), "primary"), btn("Create an account", () => openSignIn("create"))))];
  }
  return [tabs, ...(S.teamTab === "chat" ? await renderChat() : await renderPickList())];
};

// ---- Pick list -------------------------------------------------------------
function pickKey() { return eventId() || "general"; }
async function fetchPickList(force = false) {
  const acct = Team.currentTeam();
  if (TP.pickTeam !== acct) { TP.pick = null; TP.pickAt = 0; TP.pickTeam = acct; }
  if (!force && TP.pick && Date.now() - TP.pickAt < 30000) return TP.pick;
  const f = await Team.getFileOr("picklist.json", { schema: 1, lists: {} });
  TP.pick = f.data && f.data.lists ? f.data : { schema: 1, lists: {} };
  TP.pickAt = Date.now();
  return TP.pick;
}
async function editPickList(change) {
  const key = pickKey();
  const saved = await Team.updateFile("picklist.json", { schema: 1, lists: {} }, (data) => {
    data.schema = 1;
    data.lists = data.lists || {};
    const list = data.lists[key] = data.lists[key] || { teams: [] };
    list.teams = list.teams || [];
    if (change(list) === false) return false;
    list.updated = new Date().toISOString();
  });
  TP.pick = saved; TP.pickAt = Date.now(); TP.pickTeam = Team.currentTeam();
}

/** The things the list can be ordered by: every number column of the engine's teams
    table, the predicted alliance with us, and what we have noted. */
function pickSortOptions(d) {
  const opts = [["order", "Our order"]];
  const pcols = (d.partners && d.partners.columns) || [];
  const plabel = (k, fb) => (pcols.find((c) => c.key === k) || {}).label || fb;
  if (d.my_team) {
    opts.push(["p:total", `Predicted score with ${d.my_team} (${plabel("total", "Total")})`],
      ["p:cow", `Chance of winning with ${d.my_team}`], ["p:rate", "Win rate together"],
      ["p:strength", "Alliance strength together"], ["p:margin", "Margin together"]);
  }
  for (const c of ((d.teams && d.teams.columns) || [])) {
    if (["team", "name"].includes(c.key)) continue;
    opts.push(["t:" + c.key, c.key === "rank" ? "Rank (#)" : c.label]);
  }
  opts.push(["notes", "Scouting notes (most first)"], ["issue", "Technical issue (none first)"], ["team", "Team number"]);
  return opts;
}
function num(v) {
  const k = sortKey(v);
  return k[0] === 0 ? k[1] : null;
}

async function renderPickList() {
  const [d] = await Promise.all([view("pick"), fetchPickList().catch((e) => { TP.pickError = e.message; }), fetchCloudNotes()]);
  const counts = noteCountsByTeam(d.notes);
  const list = ((TP.pick && TP.pick.lists && TP.pick.lists[pickKey()]) || { teams: [] }).teams || [];
  const tcols = (d.teams && d.teams.columns) || [];
  const stats = new Map((d.teams && d.teams.rows || []).map(([iid, vals]) => [String(iid), Object.fromEntries(tcols.map((c, i) => [c.key, vals[i]]))]));
  const pcols = (d.partners && d.partners.columns) || [];
  const partner = new Map((d.partners && d.partners.rows || []).map(([iid, vals]) => [String(iid), Object.fromEntries(pcols.map((c, i) => [c.key, vals[i]]))]));
  const plabel = (k, fb) => (pcols.find((c) => c.key === k) || {}).label || fb;
  const basis = d.basis || "CAP";

  // ---- order the rows
  const sortBy = S.pickSort || "order";
  const desc = S.pickDesc != null ? S.pickDesc : !["team", "issue", "t:rank"].includes(sortBy);
  const value = (t) => {
    if (sortBy === "notes") return counts[t] || 0;
    if (sortBy === "team") return Number(t);
    if (sortBy === "issue") { const x = String((stats.get(t) || {}).tdc || "").trim(); return x && x !== "-" ? 1 : 0; }
    if (sortBy.startsWith("p:")) return num((partner.get(t) || {})[sortBy.slice(2)]);
    if (sortBy.startsWith("t:")) return num((stats.get(t) || {})[sortBy.slice(2)]);
    return null;
  };
  let rows = list.map((item, i) => ({ ...item, team: String(item.team), pos: i }));
  if (sortBy !== "order") {
    const ascending = !desc;
    rows = rows.map((r) => ({ ...r, v: value(r.team) })).sort((a, b) =>
      (a.v == null) - (b.v == null) || (a.v == null ? 0 : (ascending ? a.v - b.v : b.v - a.v)) || a.pos - b.pos);
  }
  // Outline the pick with the highest predicted score, when one stands out.
  const totals = rows.map((r) => num((partner.get(r.team) || {}).total)).filter((v) => v != null);
  const top = totals.length ? Math.max(...totals) : null;
  const best = top != null && totals.filter((v) => v === top).length === 1 && totals.length > 1 ? top : null;

  // ---- controls
  const options = pickSortOptions(d);
  // Each order starts the natural way round: most first, except rank, team number and issues.
  const sortSel = select(options, sortBy, (v) => { S.pickSort = v; S.pickDesc = !["team", "issue", "t:rank"].includes(v); rerender(); });
  const dirBtn = sortBy === "order" ? null : btn(desc ? "High → low" : "Low → high", () => { S.pickDesc = !desc; rerender(); }, "small");
  const saveOrder = sortBy === "order" ? null : btn("Save as our order", async (e) => {
    e.currentTarget.disabled = true;
    const order = rows.map((r) => r.team);
    try {
      await editPickList((l) => { const by = new Map(l.teams.map((x) => [String(x.team), x])); l.teams = [...order.filter((t) => by.has(t)).map((t) => by.get(t)), ...l.teams.filter((x) => !order.includes(String(x.team)))]; });
      S.pickSort = "order"; toast("Saved this order for everyone.");
    } catch (err) { toast("Could not save the order: " + err.message, true); }
    rerender();
  }, "small primary");

  const listed = new Set(list.map((x) => String(x.team)));
  const eventTeams = (d.teams && d.teams.rows || []).map(([iid, vals]) => ({ team: String(iid), name: (stats.get(String(iid)) || {}).name || "" }));
  const addInput = h("input", { type: "text", inputmode: "numeric", list: "pick-add-teams", placeholder: "Team number", autocomplete: "off" });
  const addList = h("datalist", { id: "pick-add-teams" }, eventTeams.filter((t) => !listed.has(t.team)).map((t) => h("option", { value: t.team }, t.name)));
  const add = async (teams) => {
    teams = teams.map(String).filter((t) => /^\d+$/.test(t) && !listed.has(t));
    if (!teams.length) { toast("Those teams are already on the list.", true); return; }
    try {
      await editPickList((l) => {
        const have = new Set(l.teams.map((x) => String(x.team)));
        const fresh = teams.filter((t) => !have.has(t));
        if (!fresh.length) return false;
        for (const t of fresh) l.teams.push({ team: t, note: "", by: chatName() || "", at: new Date().toISOString() });
      });
    } catch (err) { toast("Could not add: " + err.message, true); }
    rerender();
  };
  const addBtn = btn("Add to list", () => add([addInput.value.trim()]), "primary");
  addInput.addEventListener("keydown", (e) => { if (e.key === "Enter") add([addInput.value.trim()]); });
  const topBtn = d.my_team && partner.size ? btn("Add the top 8 partners", () => add([...partner.keys()].filter((t) => !listed.has(t)).slice(0, 8)), "small") : null;

  // ---- one card per team
  const move = async (team, delta) => {
    try {
      await editPickList((l) => {
        const i = l.teams.findIndex((x) => String(x.team) === team), j = i + delta;
        if (i < 0 || j < 0 || j >= l.teams.length) return false;
        [l.teams[i], l.teams[j]] = [l.teams[j], l.teams[i]];
      });
    } catch (err) { toast("Could not move: " + err.message, true); }
    rerender();
  };
  const remove = (team) => confirmDialog(`Remove ${team} from the list?`, `This removes it for everyone signed in as ${accountName()}. Its list note is removed too.`, "Remove", async () => {
    try { await editPickList((l) => { const n = l.teams.length; l.teams = l.teams.filter((x) => String(x.team) !== team); return l.teams.length !== n; }); }
    catch (err) { toast("Could not remove: " + err.message, true); }
    rerender();
  });
  const cards = rows.map((r, i) => {
    const st = stats.get(r.team) || {};
    const p = partner.get(r.team) || {};
    const total = num(p.total);
    const isMe = String(d.my_team) === r.team;
    const note = h("textarea", { rows: 2, class: "pick-note", value: r.note || "", placeholder: "Why we'd pick them, what to check, who to ask…" });
    note.value = r.note || "";
    const status = h("span", { class: "muted small" });
    const saveNote = async () => {
      const text = note.value.trim();
      if (text === (r.note || "").trim()) return;
      status.textContent = "Saving…";
      try { await editPickList((l) => { const x = l.teams.find((y) => String(y.team) === r.team); if (!x) return false; x.note = text; x.noteBy = chatName() || ""; }); r.note = text; status.textContent = "Saved"; }
      catch (err) { status.textContent = ""; toast("Could not save the note: " + err.message, true); }
    };
    note.addEventListener("blur", saveNote);
    const issue = String(st.tdc || "").trim();
    const statBits = [["Rank", st.rank], ["CAP", st.cap], ["OPR", st.opr], ["EPA", st.epa], ["Auto", st.auto], ["Teleop", st.teleop], ["Endgame", st.endgame]]
      .filter(([, v]) => v !== undefined && v !== "");
    return h("article", { class: "pick-card" + (best != null && total === best ? " best" : "") },
      h("div", { class: "pick-rank", text: String(i + 1) }),
      h("div", { class: "pick-main" },
        h("div", { class: "pick-head" },
          h("button", { class: "linkbtn strong", type: "button", title: "Open the team sheet", onclick: () => go("teams", { team: r.team }) }, r.team),
          h("span", { class: "pick-name", text: st.name || (stats.has(r.team) || !d.loaded ? "" : "Not at this event") }),
          counts[r.team] ? h("button", { class: "note-badge shared linkish", type: "button", title: "Open this team's notes",
            onclick: () => { S.notesTab = "teams"; S.notesTeam = r.team; go("notes"); } }, `${counts[r.team]} note${counts[r.team] === 1 ? "" : "s"}`) :
            h("span", { class: "note-badge local", text: "No notes yet" }),
          issue && issue !== "-" ? h("span", { class: "note-badge issue", text: issue }) : null,
          st.focus && st.focus !== "-" ? h("span", { class: "note-badge plain", text: "Focus: " + st.focus }) : null),
        isMe ? h("p", { class: "muted small", text: "This is your team." }) :
          total != null ? h("div", { class: "pick-predict" },
            h("div", { class: "big" }, h("b", { text: p.total }), h("span", { text: ` predicted with ${d.my_team}` })),
            h("span", { class: "muted small", text: [p.cow ? `Chance of winning ${p.cow}` : "", p.rate ? `wins ${p.rate}` : "", p.beats ? `beats ${p.beats}` : "", p.verdict || ""].filter(Boolean).join(" · ") })) :
            h("p", { class: "muted small", text: d.my_team ? "No prediction: this team is not at the loaded event." : "Set your team number on Setup to see the predicted score with each pick." }),
        h("div", { class: "pick-stats" }, statBits.map(([k, v]) => h("span", null, h("small", { text: k }), h("b", { text: displayValue(v) || "—" })))),
        h("label", { class: "field" }, h("span", { text: "List note" }), note), status),
      h("div", { class: "pick-actions" },
        sortBy === "order" ? btn("↑", () => move(r.team, -1), "small") : null,
        sortBy === "order" ? btn("↓", () => move(r.team, 1), "small") : null,
        btn("Remove", () => remove(r.team), "small danger")));
  });
  cards.forEach((c, i) => { const up = c.querySelector(".pick-actions .btn"); if (up && i === 0 && sortBy === "order") up.disabled = true; });
  if (cards.length && sortBy === "order") { const last = cards[cards.length - 1].querySelectorAll(".pick-actions .btn")[1]; if (last) last.disabled = true; }

  const updated = TP.pick && TP.pick.lists && TP.pick.lists[pickKey()] && TP.pick.lists[pickKey()].updated;
  return [
    h("div", { class: "sync-bar" + (TP.pickError ? " err" : "") }, h("span", { class: "sync-dot" + (TP.pickError ? " bad" : ""), "aria-hidden": "true" }),
      h("span", { class: "sync-text" }, h("b", { text: accountName() }),
        TP.pickError ? ` · Couldn't load the shared list: ${TP.pickError}` :
          ` · pick list for ${d.event || "no event loaded"}${updated ? " · changed " + new Date(updated).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : ""}`),
      btn("Refresh", async (e) => { e.currentTarget.disabled = true; TP.pickError = ""; await fetchPickList(true).catch((err) => { TP.pickError = err.message; }); rerender(); }, "small")),
    card("Add teams",
      d.loaded ? null : h("p", { class: "muted small", text: "Load your event on Setup to see stats and predicted scores. You can still build the list." }),
      h("div", { class: "row pick-add" }, field("Team", h("span", { class: "combo" }, addInput, addList)), addBtn, topBtn),
      d.bar ? h("p", { class: "note", text: d.bar }) : null),
    card(`Pick list (${rows.length})`,
      h("div", { class: "row pick-sort" }, field("Order by", sortSel, "wide"), dirBtn, saveOrder),
      sortBy !== "order" ? h("p", { class: "note", text: "Sorted view. \"Save as our order\" makes it the list everyone sees in \"Our order\"." }) : null,
      rows.length ? h("div", { class: "pick-list" }, cards) :
        h("p", { class: "muted", text: "No teams on the list yet. Add teams above." }),
      d.my_team ? h("p", { class: "note", text: `Predicted score = the two teams' ${basis} added together, the same prediction AutoScout uses for matches. Chance of winning is a share of predicted points, not a probability.` }) : null),
  ];
}

// ---- Team chat -------------------------------------------------------------
function chatName() { return readLocal("autoscout.chatName", ""); }
function myMessageIds() { return new Set(readLocal("autoscout.chatMine", [])); }
async function fetchChat() {
  const acct = Team.currentTeam();
  const f = await Team.getFileOr("chat.json", { schema: 1, messages: [] });
  if (Team.currentTeam() !== acct) return TP.chat;
  TP.chatTeam = acct;
  TP.chat = (f.data && Array.isArray(f.data.messages)) ? f.data.messages : [];
  TP.chatError = "";
  return TP.chat;
}
function stopChatPolling() { if (TP.chatTimer) { clearInterval(TP.chatTimer); TP.chatTimer = null; } }

async function renderChat() {
  stopChatPolling();
  if (TP.chatTeam !== Team.currentTeam()) TP.chat = null;
  if (!TP.chat) await fetchChat().catch((e) => { TP.chatError = e.message; TP.chat = []; });
  const box = h("div", { class: "chat-log", role: "log", "aria-live": "polite" });
  const drawLog = () => {
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const mine = myMessageIds();
    box.textContent = "";
    if (!TP.chat.length) box.append(h("p", { class: "muted small chat-empty", text: "No messages yet. Say hello to your team." }));
    let lastDay = "";
    for (const m of TP.chat) {
      const when = new Date(m.at);
      const day = when.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
      if (day !== lastDay) { box.append(h("div", { class: "chat-day", text: day })); lastDay = day; }
      const own = mine.has(m.id);
      box.append(h("div", { class: "chat-msg" + (own ? " own" : "") },
        h("div", { class: "chat-meta" }, h("b", { text: m.name || "Someone" }),
          h("span", { text: when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) }),
          own ? h("button", { class: "linkbtn small-link", type: "button", onclick: () => deleteMessage(m.id) }, "Delete") : null),
        h("div", { class: "chat-text", text: m.text })));
    }
    if (atBottom || box.dataset.first !== "no") { box.scrollTop = box.scrollHeight; box.dataset.first = "no"; }
  };
  const status = h("span", { class: "muted small chat-status", text: TP.chatError ? "Couldn't load messages: " + TP.chatError : "" });
  const name = h("input", { type: "text", value: chatName(), placeholder: "Your name", maxlength: 40, autocomplete: "nickname",
    onchange: (e) => writeLocal("autoscout.chatName", e.target.value.trim()) });
  const text = h("textarea", { rows: 2, placeholder: "Message your team…", maxlength: 2000 });
  text.value = S.chatDraft || "";
  text.addEventListener("input", () => { S.chatDraft = text.value; });
  const send = btn("Send", async () => {
    const who = name.value.trim();
    const body = text.value.trim();
    if (!who) { toast("Enter your name first, so your team knows who's talking.", true); name.focus(); return; }
    if (!body) return;
    writeLocal("autoscout.chatName", who);
    const msg = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8), at: new Date().toISOString(), name: who, text: body };
    send.disabled = true;
    try {
      const saved = await Team.updateFile("chat.json", { schema: 1, messages: [] }, (data) => {
        data.schema = 1;
        data.messages = (Array.isArray(data.messages) ? data.messages : []).concat(msg).slice(-CHAT_KEEP);
      });
      writeLocal("autoscout.chatMine", [...myMessageIds(), msg.id].slice(-CHAT_KEEP));
      TP.chat = saved.messages;
      text.value = ""; S.chatDraft = "";
      box.dataset.first = "yes";
      drawLog();
      status.textContent = "";
    } catch (e) { toast("Message not sent: " + e.message, true); }
    send.disabled = false;
    text.focus();
  }, "primary");
  text.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !isPhone()) { e.preventDefault(); send.click(); } });
  async function deleteMessage(id) {
    try {
      const saved = await Team.updateFile("chat.json", { schema: 1, messages: [] }, (data) => {
        const n = (data.messages || []).length;
        data.messages = (data.messages || []).filter((m) => m.id !== id);
        return data.messages.length !== n;
      });
      TP.chat = saved.messages || [];
      drawLog();
    } catch (e) { toast("Could not delete: " + e.message, true); }
  }
  // Check for new messages while the chat is on screen.
  TP.chatTimer = setInterval(async () => {
    if (S.page !== "teampage" || S.teamTab !== "chat" || !teamOn() || !box.isConnected) { stopChatPolling(); return; }
    if (document.hidden) return;
    const before = JSON.stringify(TP.chat.map((m) => m.id));
    try { await fetchChat(); status.textContent = ""; } catch (e) { status.textContent = "Offline? " + e.message; return; }
    if (JSON.stringify(TP.chat.map((m) => m.id)) !== before) drawLog();
  }, CHAT_POLL_MS);
  afterRender.push(drawLog);
  return [
    card(`${accountName()} chat`,
      h("p", { class: "muted small", text: `Everyone signed in as ${accountName()} sees these messages. New messages appear every few seconds. Stored in the public data repository: don't share anything private.` }),
      box,
      h("div", { class: "chat-compose" }, field("Name", name, "chat-name"), h("label", { class: "field chat-input" }, h("span", { text: "Message" }), text), send),
      status),
  ];
}

// ---- Signing in ------------------------------------------------------------
function openSignIn(mode = "signin") {
  if (!teamReady()) { toast("Team storage is not set up for this site.", true); return; }
  modal((close) => {
    let creating = mode === "create";
    let needCode = false;          // only if the storage service is set to require a setup code
    const title = h("h2");
    const intro = h("p", { class: "muted small" });
    const team = h("input", { type: "text", name: "username", autocomplete: "username", autocapitalize: "none", spellcheck: "false",
      placeholder: "e.g. 12928 or lightsaders", value: (S.head && S.head.my_team) || "" });
    const pw = h("input", { type: "password", name: "password", placeholder: "Password" });
    const code = h("input", { type: "password", name: "setup-code", autocomplete: "off", placeholder: "From the AutoScout administrator" });
    const codeField = field("Setup code", code);
    const hint = h("span", { class: "muted small field-hint" });
    const msg = h("p", { class: "status", role: "alert" });
    const submit = h("button", { class: "btn primary", type: "submit" });
    const toggle = h("button", { class: "linkbtn", type: "button", onclick: () => { creating = !creating; msg.textContent = ""; draw(); } });
    const show = check("Show password", false, (v) => { pw.type = v ? "text" : "password"; });
    function draw() {
      title.textContent = creating ? "Create an account" : "Sign in";
      intro.textContent = creating
        ? "Use your FTC team number to share with your whole team, or pick a username. Everyone who signs in with the same name and password sees the same notes."
        : "Share scouting notes, photos and sessions between devices. You stay signed in on this device for 12 hours.";
      hint.textContent = creating ? "A team number, or a username of 3-30 letters, numbers, - or _ starting with a letter." : "";
      hint.hidden = !creating;
      codeField.hidden = !(creating && needCode);
      pw.autocomplete = creating ? "new-password" : "current-password";
      pw.placeholder = creating ? "At least 12 characters" : "Password";
      submit.textContent = creating ? "Create account" : "Sign in";
      toggle.textContent = creating ? "Already have an account? Sign in" : "New here? Create an account";
    }
    draw();
    const form = h("form", { class: "signin-form", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      const t = team.value.trim().toLowerCase();
      msg.className = "status err";
      if (!validAccountName(t)) {
        msg.textContent = creating ? "Enter a team number, or a username of 3-30 letters, numbers, - or _ that starts with a letter."
          : "Enter your team number or username.";
        team.focus(); return;
      }
      if (!pw.value) { msg.textContent = "Enter the password."; pw.focus(); return; }
      if (creating && pw.value.length < 12) { msg.textContent = "Use a password with at least 12 characters."; pw.focus(); return; }
      if (creating && needCode && !code.value.trim()) { msg.textContent = "Enter the setup code."; code.focus(); return; }
      submit.disabled = true;
      msg.className = "status";
      msg.textContent = creating ? "Creating the account…" : "Signing in…";
      try {
        if (creating) await Team.register(t, pw.value, code.value.trim());
        else await Team.login(t, pw.value);
        close();
        toast(creating ? `Account ${accountName(t)} created. You're signed in.` : `Signed in as ${accountName(t)}.`);
      } catch (err) {
        msg.className = "status err";
        msg.textContent = err.message;
        submit.disabled = false;
        if (creating && /setup code/i.test(err.message) && !needCode) { needCode = true; draw(); }
      }
    } },
      h("label", { class: "field" }, h("span", { text: "Team number or username" }), team, hint),
      field("Password", pw), show, codeField, msg,
      h("div", { class: "foot" }, toggle, h("span", { class: "spacer" }), btn("Cancel", close), submit));
    return [h("button", { class: "btn small close", type: "button", onclick: close, "aria-label": "Close" }, "✕"),
      title, intro, form,
      h("p", { class: "note", text: "Shared data is stored in a public GitHub repository: anyone can read it. Don't put private or personal information in notes, and don't reuse a password from another site." })];
  });
}

// Shared sessions: sessions/<season_code>.json, plus sessions/index.json listing them
// (the storage service lists only the team's top folder, so the index is how they are found).
async function saveSessionToTeam() {
  const exported = await act("export", { kind: "session" });
  if (!exported.ok) throw new Error(exported.message || "Could not export this session.");
  const hd = S.head || {};
  const slug = (String(hd.season || "season") + "_" + String(hd.code || "session")).replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 70);
  const sessionData = JSON.parse(exported.text);
  sessionData.live_notes = {};          // notes are shared separately, with their own sync
  sessionData.team_notes = {};
  await Team.putFile("sessions/" + slug + ".json", sessionData);
  await Team.updateFile("sessions/index.json", { schema: 1, sessions: {} }, (data) => {
    data.schema = 1;
    data.sessions = data.sessions || {};
    data.sessions[slug] = { event: hd.event || hd.code || slug, season: hd.season || "", code: hd.code || "", saved: new Date().toISOString() };
  });
  return slug;
}
async function listTeamSessions() {
  const out = new Map();
  const index = await Team.getFileOr("sessions/index.json", { sessions: {} });
  for (const [slug, info] of Object.entries((index.data && index.data.sessions) || {})) out.set(slug, { slug, ...info });
  try {      // a storage service that can list the sessions folder also finds sessions saved before the index existed
    for (const f of await Team.listFiles("sessions")) {
      const m = /(?:^|\/)sessions\/([A-Za-z0-9_-]+)\.json$/.exec(f.path || "");
      if (m && m[1] !== "index" && !out.has(m[1])) out.set(m[1], { slug: m[1], event: m[1].replace(/_/g, " ") });
    }
  } catch (_) { /* the index is enough */ }
  return [...out.values()].sort((a, b) => String(b.saved || "").localeCompare(String(a.saved || "")));
}
async function openTeamSessions() {
  const list = await listTeamSessions();
  modal((close) => [
    h("button", { class: "btn small close", type: "button", onclick: close, "aria-label": "Close" }, "✕"),
    h("h2", { text: `${accountName()} sessions` }),
    list.length ? h("div", { class: "sessions" }, list.map((s) => h("div", { class: "sess" },
      h("div", { class: "info" }, h("b", { text: s.event || s.slug }),
        h("span", { class: "muted small", text: [s.season && s.code ? `${s.season}/${s.code}` : "", s.saved ? "saved " + new Date(s.saved).toLocaleString() : ""].filter(Boolean).join(" · ") })),
      btn("Open", async (e) => {
        e.currentTarget.disabled = true;
        try {
          const result = await Team.getFile("sessions/" + s.slug + ".json");
          const imported = await act("import", { text: JSON.stringify(result.data) });
          if (!imported.ok) throw new Error(imported.message || "Could not open the shared session.");
          close();
          toast("Opened shared session.");
          rerender();
        } catch (err) { toast(err.message, true); e.target.disabled = false; }
      }, "small primary")))) :
      h("p", { class: "muted", text: "No shared sessions yet. Use \"Save session to team\" to share the open one." }),
    h("p", { class: "note", text: "Opening a shared session replaces the open one in this browser. Your notes are kept: they are saved separately." }),
    h("div", { class: "foot" }, btn("Close", close)),
  ]);
}

/** Account status and actions: used by the top-bar account dialog and Settings. */
function accountPanel(onDone) {
  if (!teamReady()) return h("p", { class: "muted", text: "Team sharing is not set up for this site. Everything is saved on this device." });
  if (!teamOn()) {
    return h("div", { class: "account-panel" },
      h("p", { text: "You're using AutoScout on this device only. Notes and sessions are saved in this browser and work offline." }),
      h("p", { class: "muted small", text: "Sign in with your team number or a username to share scouting notes and photos between devices." }),
      h("div", { class: "row" }, btn("Sign in", () => { if (onDone) onDone(); openSignIn(); }, "primary"),
        btn("Create an account", () => { if (onDone) onDone(); openSignIn("create"); })));
  }
  const until = new Date(Team.expiresAt()).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  const busy = (fn) => async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try { await fn(); } catch (err) { toast(err.message, true); }
    b.disabled = false;
  };
  return h("div", { class: "account-panel" },
    h("div", { class: "account-status" }, h("span", { class: "sync-dot", "aria-hidden": "true" }),
      h("div", null, h("b", { text: `Signed in as ${accountName()}` }),
        h("span", { class: "muted small", text: `Stays signed in on this device until ${until}.` }))),
    h("div", { class: "account-actions" },
      btn("Team Page", () => { if (onDone) onDone(); go("teampage"); }, "small"),
      btn("Team notes", () => { if (onDone) onDone(); S.notesTab = "teams"; go("notes"); }, "small"),
      btn("Share this device's notes", busy(async () => {
        const n = await shareAllLocalNotes();
        toast(n ? `Shared ${n} note${n === 1 ? "" : "s"} with ${accountName()}.` : "Everything is already shared.");
        if (S.page === "notes") rerender();
      }), "small"),
      btn("Save session to team", busy(async () => { await saveSessionToTeam(); toast(`Session saved to ${accountName()}.`); }), "small"),
      btn("Open a team session", busy(async () => { if (onDone) onDone(); await openTeamSessions(); }), "small"),
      btn("Sign out", () => { if (onDone) onDone(); Team.signOut(); toast("Signed out. Notes stay saved on this device."); }, "small danger")));
}
function openAccount() {
  if (!teamOn()) { openSignIn(); return; }
  modal((close) => [h("button", { class: "btn small close", type: "button", onclick: close, "aria-label": "Close" }, "✕"),
    h("h2", { text: "Team account" }), accountPanel(close)]);
}
function updateAccountButton() {
  const b = $("#accountBtn");
  if (!b) return;
  b.hidden = !teamReady();
  const on = teamOn();
  b.classList.toggle("on", on);
  b.querySelector(".account-label").textContent = on ? accountName() : "Sign in";
  b.title = on ? `Signed in as ${accountName()}` : "Sign in to share notes with your team";
  b.setAttribute("aria-label", b.title);
}
if (Team) Team.onChange(() => {
  resetCloud();
  updateAccountButton();
  TP.pick = null; TP.chat = null; stopChatPolling();
  if (S.boot && ["notes", "settings", "teampage", "teams"].includes(S.page)) rerender();
});

function leagueInfoCard(info, teamNumber, onLoadEvent) {
  if (!info) return card("League standings & meet history", h("p", { class: "muted", text: "League and event information could not be loaded." }));
  const leagues = info.leagues || [];
  const events = info.events || [];
  const leagueSections = leagues.map((league) => {
    const rows = league.rankings || [];
    const mine = league.team_rank;
    const head = h("div", { class: "league-standing-summary" },
      h("div", null,
        h("b", { text: league.name || league.code || "League" }),
        h("span", { class: "muted small", text: [league.region, league.code].filter(Boolean).join(" · ") })),
      mine ? h("strong", { class: "league-my-rank", text: `#${mine.rank} overall` }) :
        h("span", { class: "muted small", text: "No ranking row for this team yet" }));
    const table = rows.length ? h("div", { class: "league-ranking-wrap" },
      h("table", { class: "league-ranking-table" },
        h("thead", null, h("tr", null, ["Rank", "Team", "W", "L", "T", "Qual avg."].map((label) => h("th", { text: label })))),
        h("tbody", null, rows.map((row) => h("tr", { class: Number(row.number) === Number(teamNumber) ? "my-league-row" : "" },
          h("td", { text: row.rank ?? "—" }),
          h("td", null, h("b", { text: row.number || "" }), row.name ? h("span", { class: "muted small", text: " " + row.name }) : null),
          h("td", { text: row.wins ?? "—" }), h("td", { text: row.losses ?? "—" }),
          h("td", { text: row.ties ?? "—" }), h("td", { text: row.qual_average == null ? "—" : Number(row.qual_average).toFixed(1) })))))) :
      h("p", { class: "muted small", text: league.rankings_error || "League rankings are not available yet." });
    return h("section", { class: "league-standing" }, head, table);
  });
  const eventCards = events.map((event) => h("article", { class: "league-event-link" },
    h("span", { class: "league-event-name", text: event.name }),
    h("span", { class: "muted small", text: [event.date, event.date_end && event.date_end !== event.date ? event.date_end : "", event.league, event.division ? `Division ${event.division}` : "", event.code, [event.city, event.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ") }),
    h("div", { class: "row league-event-actions" },
      btn("Load meet in AutoScout", async () => {
        if (onLoadEvent) return onLoadEvent(event);
        const loaded = await loadEvent({ season: event.season || info.season, event: event.code, team: String(teamNumber || ""), extra: "",
          offline: false, history: true, schedule: "" });
        if (loaded && loaded.ok) go("competition");
      }, "small primary"),
      h("a", { href: event.url, target: "_blank", rel: "noopener", class: "muted small" }, "Official event page"))));
  return card("League standings & meet history",
    h("p", { class: "muted small", text: `Season ${info.season}: league rankings and every event participation returned for this team.` }),
    leagueSections.length ? h("div", { class: "league-standing-list" }, leagueSections) :
      h("p", { class: "muted small", text: "No league memberships were returned for this season." }),
    h("h4", { class: "league-subheading", text: `Meets attended (${events.length})` }),
    eventCards.length ? h("div", { class: "league-event-list" }, eventCards) :
      h("p", { class: "muted small", text: "No meet participations were returned for this season." }),
    h("p", { class: "note", text: "League standings are cumulative League Meet rankings. League Tournament rankings are separate." }));
}

function teamPhaseChartCard(charts) {
  const data = charts || {};
  let scope = "event";
  const body = h("div");
  const draw = () => {
    body.textContent = "";
    const c = data[scope];
    body.append(chartView(c, { height: 320, name: `team_phases_${scope}` }));
    if (c && c.note) body.append(h("p", { class: "note", text: c.note }));
  };
  const controls = h("div", { class: "row phase-chart-controls" },
    btn("This event", () => { scope = "event"; controls.querySelectorAll("button").forEach((b, i) => b.classList.toggle("primary", i === 0)); draw(); }, "primary small"),
    btn("This season", () => { scope = "season"; controls.querySelectorAll("button").forEach((b, i) => b.classList.toggle("primary", i === 1)); draw(); }, "small"));
  draw();
  return card("Auto / Teleop / Endgame by match", controls, body);
}

function openTeamSheet(d) {
  const e = d.editor;
  let closeIt = null;
  const back = () => { if (closeIt) closeIt(); };
  closeIt = modal((close) => {
    const tdc = select(S.boot.tdc_issues.map((x) => [x, x || "- none -"]), e.tdc, () => apply());
    const delta = h("input", { type: "number", value: e.delta, step: "any", inputmode: "decimal" });
    const focus = select(S.boot.focus_modes, e.focus, () => apply());
    const note = textInput(e.note, { placeholder: "Short pit note, robot issues…" });
    delta.addEventListener("change", () => apply());
    note.addEventListener("change", () => apply());          // save the sheet note when you leave the box
    async function apply() {
      await runAction("team_edit", { team: e.team, tdc: tdc.value, delta: delta.value, focus: focus.value, note: note.value }, false);
      const nd = await view("teams", { ...S.panel, team: e.team });
      refreshPanel(nd.panel);
      const notesBox = document.querySelector(".sheet-notes");
      if (notesBox && notesBox._draw) notesBox._draw();
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
      h("section", { class: "card" }, h("h3", { text: "Adjust the selected team (Technical Issues + Team Focus)" }),
        h("div", { class: "form" }, field("Technical issue", tdc), field("Delta", delta), field("Focus", focus), field("Sheet note", note, "wide")),
        h("div", { class: "row" }, btn("Apply", apply, "primary"))),
      sheetNotesCard(e.team),
      leagueInfoCard(d.league_info, e.team, async (event) => {
        const loaded = await loadEvent({ season: event.season || d.league_info.season, event: event.code,
          team: String(e.team), extra: "", offline: false, history: true, schedule: "" }, { rerenderAfter: false });
        if (loaded && loaded.ok) { close(); go("competition"); }
      }),
      panelBox,
      teamPhaseChartCard(d.phase_charts),
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
        card("Match log", dataTable(P.matches, { id: "r-matches", short: true, empty: "No match records.", onRow: (_iid, vals) => infoDialog("Team match record", vals.join("\\n")) })))),
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

// Open one consistent match detail view from every match list.
async function openMatchDetails(label) {
  try {
    const [d, live, matchData] = await Promise.all([view("match_detail", { match: String(label) }),
      view("live", { match: String(label) }), view("matches"), fetchCloudNotes()]);
    S.live.match = String(label);
    S.live.team = (live.match_teams && live.match_teams[0] && live.match_teams[0][0]) ? String(live.match_teams[0][0]) : "";
    const teams = (d.teams || []).map((side) => h("section", { class: "match-team-side " + side.color },
      h("h3", { text: side.label }),
      ...(side.teams || []).map((t) => h("div", { class: "match-team" },
        h("b", { text: String(t.number) }), h("span", { text: t.name || "Team name unavailable" }),
        h("span", { class: "muted small", text: t.stats || "" })))));
    const noteBox = h("div");
    const drawNotes = (nd) => {
      noteBox.textContent = "";
      noteBox.append(notesCard(nd, async (patch) => {
        Object.assign(S.live, patch);
        drawNotes(await view("live", { match: S.live.match }));
      }));
    };
    drawNotes(live);
    const matchColumns = matchData.table.columns.map((c) => c.key);
    const matchRow = matchData.table.rows.find((row) => String(row[0]) === String(label));
    modal((close) => [
      h("button", { class: "btn small close", onclick: close, "aria-label": "Close" }, "✕"),
      h("h2", { text: d.title || String(label) }),
      h("div", { class: "match-team-grid" }, ...teams),
      card("Match statistics", h("div", { class: "detail pre", text: d.text || "No additional match statistics are available yet." })),
      noteBox,
      h("div", { class: "foot" },
        matchRow ? btn("Edit score", () => { close(); resultDialog(String(label), matchRow[1], matchColumns); }, "small") : null,
        null),
    ], { wide: true });
  } catch (e) { toast("Could not load match details: " + e.message, true); }
}

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
    dataTable(d.table, { id: "matches", sticky: 1, flash: params.match || null, empty: "No matches loaded.", matchStyle: true,
      onRow: (iid, vals) => openMatchDetails(iid) }),
    h("p", { class: "note", text: "Open any match to review both alliances, match statistics and scouting notes. Scores can be edited from the match details." }),
  ];
};
function setAuto(on) {
  S.auto.on = on;
  clearInterval(S.auto.timer);
  if (on) S.auto.timer = setInterval(async () => {
    if (S.loading) return;
    const r = await runAction("refresh", {}, false);
    if (r.ok) rerender();
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
    return [h("h2", { text: `Result - ${label}` }),
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
    card("My matches", dataTable(d.table, { id: "mymatches", empty: "No matches for this team.", onRow: (iid) => openMatchDetails(iid) })),
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
    card("Wrong predictions", dataTable(d.wrong, { id: "wrong", wrap: ["suggest"], onRow: (iid) => openMatchDetails(iid), empty: "No wrong predictions (or no results yet)." })),
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

// Standalone Live Scouting page and video search removed. Match notes remain in Notes and match details.

async function imageAttachment(label = "Photo") {
  return new Promise((resolve) => {
    const picker = h("input", { type: "file", accept: "image/*", style: { display: "none" } });
    picker.addEventListener("change", () => {
      const file = picker.files && picker.files[0];
      if (!file) return resolve("");
      if (!file.type.startsWith("image/")) { toast("Choose an image file.", true); return resolve(""); }
      if (file.size > 4 * 1024 * 1024) { toast("Images must be 4 MB or smaller.", true); return resolve(""); }
      const reader = new FileReader();
      reader.onload = () => {
        const img = new Image();
        img.onload = () => {
          const scale = Math.min(1, 1200 / Math.max(img.width, img.height));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(img.width * scale));
          canvas.height = Math.max(1, Math.round(img.height * scale));
          const ctx = canvas.getContext("2d");
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          let quality = 0.68;
          let data = canvas.toDataURL("image/jpeg", quality);
          while (data.length > 650000 && quality > 0.35) {
            quality -= 0.08;
            data = canvas.toDataURL("image/jpeg", quality);
          }
          if (data.length > 680000) { toast("That photo is too large to sync. Try a smaller image.", true); resolve(""); }
          else resolve(data);
        };
        img.onerror = () => { toast("Could not process that image.", true); resolve(""); };
        img.src = String(reader.result || "");
      };
      reader.onerror = () => { toast("Could not read that image.", true); resolve(""); };
      reader.readAsDataURL(file);
    }, { once: true });
    document.body.append(picker);
    picker.click();
    picker.addEventListener("change", () => picker.remove(), { once: true });
    picker.addEventListener("cancel", () => { picker.remove(); resolve(""); }, { once: true });
  });
}
function photoPreview(data, className = "note-photo") {
  return data ? h("img", { class: className, src: data, alt: "Attached scouting photo", loading: "lazy" }) : null;
}

function notesCard(d, update) {
  const shared = mergedMatchNotes(d.match, d.notes);
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
  let photo = "";
  const photoStatus = h("span", { class: "muted small", text: "No photo attached" });
  const list = h("div", { class: "notes" });
  if (!shared.length) list.append(h("p", { class: "muted small", text: "No notes for this match yet." }));
  shared.forEach((n) => list.append(h("div", { class: "notecard" },
    h("span", { class: "meta" }, `${n.time || ""} · ${n.team || "General"} · ${n.phase || "Overall"} `, noteBadge(n)),
    h("button", { class: "btn small danger", type: "button", onclick: () => deleteNote("match", d.match, n, () => update({})) }, "Delete"),
    h("span", { class: "txt", text: n.text || "" }), notePhoto(n))));
  return card("Match notes",
    h("div", { class: "form" }, field("Match", match), field("Team", team), field("Phase", phase)),
    h("label", { class: "field", style: { marginTop: "10px" } }, h("span", { text: "Observation" }), text),
    h("div", { class: "row" },
      btn("Add photo", async () => { photo = await imageAttachment(); photoStatus.textContent = photo ? "Photo attached" : "No photo attached"; }, "small"),
      photoStatus,
      btn("Add note", async () => {
        const r = await addMatchNote({ match: d.match, team: team.value, phase: phase.value, text: text.value, photo });
        if (r.ok) { text.value = ""; photo = ""; photoStatus.textContent = "No photo attached"; update({}); }
      }, "primary"),
      h("span", { class: "spacer" }), btn("Save session", saveSession)),
    list);
}

/** Team sheet: this team's scouting notes (the same notes as Notes > Team Notes), with an add box. */
function sheetNotesCard(team) {
  team = String(team);
  const box = h("section", { class: "card sheet-notes" }, h("h3", { text: `Scouting notes for Team ${team}` }), h("p", { class: "muted small", text: "Loading notes…" }));
  const draw = async () => {
    const [d] = await Promise.all([view("team_notes", { team }), fetchCloudNotes()]);
    const notes = [...sheetNoteEntries(d.sheet_note ? { [team]: d.sheet_note } : {}),
      ...mergedTeamNotes(d.notes).filter((n) => n.team === team)];
    const text = h("textarea", { rows: 3, placeholder: "What you saw: robot capabilities, strategy, reliability, driver skill…" });
    let photo = "";
    const photoStatus = h("span", { class: "muted small", text: "No photo attached" });
    const save = btn(teamOn() ? "Save and share" : "Save note", async () => {
      save.disabled = true;
      const r = await addTeamNote({ team, text: text.value, photo });
      save.disabled = false;
      if (r.ok) draw();
    }, "primary");
    box.replaceChildren(
      h("h3", { text: `Scouting notes for Team ${team}` }),
      h("p", { class: "muted small", text: "The same notes as Notes ▸ Team Notes" + (teamOn() ? `, shared with ${accountName()}.` : ". Sign in to share them with your team.") }),
      field("New note", text),
      h("div", { class: "row team-note-actions" },
        btn("Add photo", async () => { const p = await imageAttachment(); if (p) { photo = p; photoStatus.textContent = "Photo attached"; } }, "small"),
        photoStatus, h("span", { class: "spacer" }), save),
      notes.length ? h("div", { class: "team-note-list" }, notes.map((n) => h("article", { class: "team-note-entry" },
        h("div", { class: "match-note-meta" },
          n.sheet ? null : h("span", { class: "note-time", text: n.time || "Time not recorded" }),
          noteBadge(n),
          n.sheet ? null : btn("Delete", () => deleteNote("team", team, n, draw), "small danger")),
        n.text ? h("p", { class: "match-note-text", text: n.text }) : null,
        notePhoto(n)))) :
        h("p", { class: "muted small", text: "No notes on this team yet." }));
  };
  box._draw = () => draw().catch(() => {});
  draw().catch((err) => box.replaceChildren(h("h3", { text: `Scouting notes for Team ${team}` }), h("p", { class: "status err", text: err.message })));
  return box;
}

// ---- Settings --------------------------------------------------------------
function teamStorageSettingsCard() {
  return card("Team account", accountPanel(),
    h("p", { class: "note", text: "Without an account everything is saved in this browser only. A team account shares scouting notes, photos and sessions between your team's devices. Team data is stored in a public GitHub repository: anyone can read it." }));
}

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
      btn("What do CAP, OPR and EPA mean?", () => infoDialog("CAP, OPR and EPA", S.boot.cap_help), "small")),
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
          check("Offline (cache only - never touch the network)", d.offline, async (v) => { await runAction("offline", { offline: v }, false); toast(v ? "Offline: only cached data is used." : "Online."); })),
        h("p", { class: "note", text: "Sessions and the cache live in this browser's storage. On iPhone, add the site to your home screen so Safari keeps them, and download session files as a backup." })),
      card("About",
        h("b", { text: d.about.title }), h("p", { class: "muted", text: d.about.blurb, style: { marginTop: "6px" } }),
        h("p", { class: "muted small" }, d.about.org, "  ·  ", h("a", { href: d.about.website, target: "_blank", rel: "noopener" }, "AutoScout website")),
        h("p", { class: "muted small", text: `Web version: the desktop AutoScout ${S.boot.version} engine running in your browser (Pyodide). Data from the public FTCScout API; no account or key needed.` }),
        h("div", { class: "row" }, btn("What does CAP mean?", () => infoDialog("CAP, OPR and EPA", S.boot.cap_help), "small")))),
    teamStorageSettingsCard(),
  ];
};

// ---------------------------------------------------------------------------
// Start up
// ---------------------------------------------------------------------------
function bootFailed(msg) {
  S.bootError = msg;
  let sp = $("#splash");
  if (!sp) {         // the page was already drawn: put the start-up error back in its place
    sp = h("div", { class: "splash", id: "splash" }, h("img", { src: "assets/logo-56.png", alt: "", width: 56, height: 56 }),
      h("h1", { text: "FTC AutoScout" }), h("p", { id: "splashText" }));
    $("#main").replaceChildren(sp);
  }
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
  $("#accountBtn").addEventListener("click", openAccount);
  updateAccountButton();
  if (!location.hash) history.replaceState(null, "", "#/overview");
  window.addEventListener("hashchange", render);
  // Draw the page now; pages that need the engine wait for it (the worker queues their requests).
  const boot = call("boot", "boot", {});
  render().then(() => { if (!S.bootError) document.body.classList.add("app-ready"); });
  try {
    S.boot = await boot;
  } catch (e) {
    bootFailed(e.message);
    return;
  }
  setHead(S.boot.head);
  if (S.boot.restored) toast(`Reopened ${S.boot.restored}.`);
  if ("serviceWorker" in navigator && (location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname))) navigator.serviceWorker.register("sw.js").catch(() => {});
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
}
start();
