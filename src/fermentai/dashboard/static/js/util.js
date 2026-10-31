// Small DOM / formatting / data helpers shared by every panel. No dependencies.

export const COLORS = {
  A: "#3987e5", B: "#d95926",
  Aband: "rgba(57,135,229,0.18)", Bband: "rgba(217,89,38,0.18)",
  text: "#f3efe6", text2: "#cdc6b8", muted: "#a39d92", grid: "#2b3038", border: "#353a43", surface: "#1e2126",
  acetic: "#199e70", lactic: "#9085e9", accent: "#e8b45a",
};
export const FONT = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';

// Display vocabulary (display mapping only; stored/API provenance values are never changed):
//   MEASURED          physical sensor data (record source "measured", v1.0 alias "real")
//   COMPUTED          NVIDIA ALCHEMI GPU results ("real: ALCHEMI/AIMNet2 on ...") and PreForm print-prep
//                     output (source "preform_local_api")
//   MODELED           model outputs: kinetics forecasts, crumb geometry, in-silico benchmark ("simulated")
//   SIMULATED DEVICE  virtual-ESP32 telemetry (record source "simulated" in a device context)
//   REPLAY            recorded/scripted data
//   estimated         heuristics, policy proposals, assumptions, bounded ALCHEMI-informed nudges
//   manual (human)    human-entered values (field provenance "manual", judge feedback, imported_human)
export const PROVENANCES = ["measured", "computed", "modeled", "simulated_device", "replay", "estimated", "manual"];
export const PROV_LABEL = {
  measured: "measured", computed: "computed", modeled: "modeled", simulated_device: "simulated device",
  replay: "replay", estimated: "estimated", manual: "manual (human)", placeholder: "placeholder", unknown: "unknown",
};

/** Create an element: h("div", {class: "x", onclick}, child, "text", [more]) */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "html") el.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k === "dataset") Object.assign(el.dataset, v);
      else el.setAttribute(k, v === true ? "" : v);
    }
  }
  appendAll(el, children);
  return el;
}
function appendAll(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}
export function clear(el, ...children) { el.replaceChildren(); appendAll(el, children); return el; }
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/**
 * Normalise any provenance-ish value to a display category (see PROVENANCES).
 * ctx === "device": the value is a telemetry/image record source, so "simulated" means a virtual device.
 */
export function normProv(p, ctx) {
  if (p && typeof p === "object") p = p.source || p.provenance || p.label;
  const s = String(p ?? "").toLowerCase().trim();
  if (!s) return "unknown";
  if (s.includes("informed") || s.includes("assumption")) return "estimated";        // bounded nudges / mapping coefficients
  if (s.includes("preform") || s.includes("alchemi") || s.includes("aimnet") || /^real\s*[:(]/.test(s)) return "computed";
  if (s.startsWith("real") || s.startsWith("measured") || s === "physical") return "measured";
  if (s.includes("replay") || s.includes("scripted")) return "replay";
  if (s === "manual" || s.includes("imported") || s.includes("human") || s.includes("judge")) return "manual";
  if (s.includes("simul") || s.includes("virtual")) return ctx === "device" ? "simulated_device" : "modeled";
  if (s.includes("model") || s.includes("in_silico") || s.includes("in-silico")) return "modeled";
  if (s.includes("estimat") || s.includes("heuristic") || s.includes("fallback") || s.includes("deterministic") || s.includes("policy")) return "estimated";
  if (s.includes("placeholder")) return "placeholder";
  return "unknown";
}
/** Provenance badge. `text` overrides the label; `ctx` "device" for telemetry/image record sources. */
export function provBadge(p, text, ctx) {
  const n = normProv(p, ctx);
  const raw = typeof p === "string" ? p : (p && (p.source || p.provenance)) || "";
  const label = text || (n === "unknown" ? (raw || "unknown") : PROV_LABEL[n]);
  return h("span", { class: `badge prov-${n}`, title: raw ? `provenance: ${raw} → ${PROV_LABEL[n] || n}` : "provenance" }, label);
}
/** Badge for a telemetry/image record source ("simulated" -> SIMULATED DEVICE). */
export const deviceBadge = (p, text) => provBadge(p, text, "device");
export function statusBadge(text, kind = "info") { return h("span", { class: `badge status-${kind}` }, text); }

export function num(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}
export function fmt(v, digits = 1, unit = "") {
  const n = num(v);
  if (n === null) return "—";
  return n.toFixed(digits) + (unit ? unit : "");
}
export function fmtOhms(v) {
  const n = num(v);
  if (n === null) return "—";
  if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(2) + " MΩ";
  if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1) + " kΩ";
  return n.toFixed(0) + " Ω";
}
export function parseTime(t) {
  if (!t) return null;
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? null : d;
}
export function timeAgo(t, now = Date.now()) {
  const d = t instanceof Date ? t : parseTime(t);
  if (!d) return "never";
  const s = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
export function hhmmss(t) {
  const d = t instanceof Date ? t : parseTime(t);
  return d ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
}

/** Minimal RFC-4180-ish CSV parser -> array of objects; numeric strings become numbers. */
export function parseCSV(text) {
  const rows = [];
  let row = [], field = "", i = 0, inQ = false;
  while (i < text.length) {
    const c = text[i];
    if (inQ) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false; }
      else field += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = ""; rows.push(row); row = [];
    } else field += c;
    i++;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  const nonEmpty = rows.filter((r) => r.length > 1 || (r[0] ?? "") !== "");
  if (!nonEmpty.length) return [];
  const head = nonEmpty[0].map((x) => x.trim());
  return nonEmpty.slice(1).map((r) => {
    const o = {};
    head.forEach((k, j) => {
      const v = r[j] ?? "";
      const n = v.trim() === "" ? NaN : Number(v);
      o[k] = Number.isFinite(n) ? n : v === "True" ? true : v === "False" ? false : v === "" ? null : v;
    });
    return o;
  });
}

/** Chamber/agent id ("scientist_A", "agent-b", "Chamber A", "A") -> "A" | "B" | null. */
export function agentSide(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  if (/^[AB]$/i.test(s)) return s.toUpperCase();
  const m = s.match(/(?:^|[_\-\s.:])([AB])$/i) || s.match(/(?:scientist|agent|chamber|loaf)[_\-\s]*([AB])\b/i);
  return m ? m[1].toUpperCase() : null;
}

/**
 * Normalise a schedule of unknown-but-plausible shape to [[t_h, temp_c], ...] plus a shape hint.
 * Accepts: [{t_h, setpoint_c}], [[t, c]], {points|knots|breakpoints|steps|schedule: ...}, {mode}.
 */
export function normSchedule(s) {
  let mode = "linear", meta = {};
  if (s && !Array.isArray(s) && typeof s === "object") {
    meta = s;
    if (String(s.mode || "").includes("constant")) mode = "hv";
    s = s.points || s.knots || s.breakpoints || s.steps || s.schedule || s.setpoints || null;
    if (s && !Array.isArray(s) && typeof s === "object") return normSchedule(s);
  }
  if (!Array.isArray(s)) return { points: [], mode, meta };
  const pts = [];
  for (const p of s) {
    if (Array.isArray(p) && p.length >= 2) pts.push([num(p[0]), num(p[1])]);
    else if (p && typeof p === "object") {
      const t = num(p.t_h ?? p.time_h ?? p.t ?? p.hour);
      const c = num(p.setpoint_c ?? p.temperature_setpoint_c ?? p.temp_c ?? p.temperature_c ?? p.target_c ?? p.setpoint ?? p.temperature);
      pts.push([t, c]);
    }
  }
  return { points: pts.filter(([t, c]) => t !== null && c !== null), mode, meta };
}

export function plotLayout(extra = {}) {
  return {
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: FONT, color: COLORS.text2, size: 11 },
    margin: { l: 44, r: 10, t: 8, b: 34 },
    xaxis: { gridcolor: COLORS.grid, zerolinecolor: COLORS.grid, linecolor: COLORS.border, tickcolor: COLORS.border, ...(extra.xaxis || {}) },
    yaxis: { gridcolor: COLORS.grid, zerolinecolor: COLORS.grid, linecolor: COLORS.border, tickcolor: COLORS.border, ...(extra.yaxis || {}) },
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { size: 10, color: COLORS.text2 }, bgcolor: "rgba(0,0,0,0)" },
    hovermode: "x unified",
    hoverlabel: { bgcolor: "#2d323a", bordercolor: COLORS.border, font: { family: FONT, color: COLORS.text, size: 12 } },
    ...Object.fromEntries(Object.entries(extra).filter(([k]) => k !== "xaxis" && k !== "yaxis")),
  };
}
// Never link out to the Plotly cloud; no mode bar (clean, no hover-only controls).
export const PLOT_CONFIG = { displaylogo: false, displayModeBar: false, responsive: true, showLink: false, showEditInChartStudio: false, showSendToCloud: false, showTips: false, scrollZoom: false };

/** Plotly loads via a deferred classic script; modules may run first. */
export function whenPlotly(timeoutMs = 15000) {
  return new Promise((resolve, reject) => {
    if (window.Plotly) return resolve(window.Plotly);
    const t0 = Date.now();
    const iv = setInterval(() => {
      if (window.Plotly) { clearInterval(iv); resolve(window.Plotly); }
      else if (Date.now() - t0 > timeoutMs) { clearInterval(iv); reject(new Error("vendor/plotly.min.js failed to load")); }
    }, 50);
  });
}

export function toast(msg, kind = "") {
  const box = document.getElementById("toasts");
  if (!box) return;
  const t = h("div", { class: `toast ${kind}` }, msg);
  box.append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 7000 : 4000);
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Inline SVG sparkline. series: [{values:[{t,v}], color, dash}] sharing one time axis. */
export function sparkline(series, { width = 300, height = 46, pad = 3 } = {}) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "spark");
  const all = series.flatMap((s) => s.values).filter((p) => p.v !== null && p.t !== null);
  if (all.length < 2) {
    const tx = document.createElementNS(NS, "text");
    tx.setAttribute("x", width / 2); tx.setAttribute("y", height / 2 + 4);
    tx.setAttribute("text-anchor", "middle"); tx.setAttribute("fill", COLORS.muted); tx.setAttribute("font-size", "11");
    tx.textContent = "waiting for telemetry history…";
    svg.append(tx);
    return svg;
  }
  const t0 = Math.min(...all.map((p) => p.t)), t1 = Math.max(...all.map((p) => p.t));
  let v0 = Math.min(...all.map((p) => p.v)), v1 = Math.max(...all.map((p) => p.v));
  if (v1 - v0 < 1) { const m = (v0 + v1) / 2; v0 = m - 0.5; v1 = m + 0.5; }
  const X = (t) => pad + ((t - t0) / Math.max(1, t1 - t0)) * (width - 2 * pad);
  const Y = (v) => height - pad - ((v - v0) / (v1 - v0)) * (height - 2 * pad);
  for (const s of series) {
    const pts = s.values.filter((p) => p.v !== null && p.t !== null);
    if (pts.length < 2) continue;
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", pts.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(""));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", s.color);
    path.setAttribute("stroke-width", "2");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    path.setAttribute("stroke-linejoin", "round");
    if (s.dash) path.setAttribute("stroke-dasharray", "4 3");
    svg.append(path);
  }
  return svg;
}

/** Render an arbitrary JSON value as readable HTML (used for pipeline fields of unknown shape). */
export function renderValue(v, depth = 0) {
  if (v === null || v === undefined) return h("span", { class: "faint" }, "—");
  if (typeof v === "string") return h("span", null, v);
  if (typeof v === "number") return h("span", { class: "mono" }, Number.isInteger(v) ? String(v) : v.toFixed(3));
  if (typeof v === "boolean") return h("span", null, v ? "yes" : "no");
  if (Array.isArray(v)) {
    if (!v.length) return h("span", { class: "faint" }, "(none)");
    if (v.every((x) => typeof x !== "object" || x === null)) return h("span", null, v.join(", "));
    return h("ul", { class: "desired-list" }, v.slice(0, 12).map((x) => h("li", null, renderValue(x, depth + 1))));
  }
  if (depth > 3) return h("code", null, JSON.stringify(v).slice(0, 300));
  const dl = h("dl", { class: "kv" });
  for (const [k, x] of Object.entries(v)) dl.append(h("dt", null, k.replace(/_/g, " ")), h("dd", null, renderValue(x, depth + 1)));
  return dl;
}

/** Telemetry value that may live on the row or (v1.1 extras) only inside row.payload. */
export function tfield(tel, k) {
  if (!tel) return null;
  const v = tel[k];
  if (v !== undefined && v !== null) return v;
  const p = tel.payload && tel.payload[k];
  return p === undefined ? null : p;
}

/** Map a telemetry record source to a demonstration mode. */
export function demoMode(source) {
  const n = normProv(source, "device");
  return n === "measured" ? "live" : n === "replay" ? "replay" : n === "simulated_device" ? "simulation" : null;
}

// ---------------------------------------------------------------- kitchen-brigade display names
// Display only: agent ids in payloads stay scientist_A / scientist_B; the raw id is kept in tooltips.
//   Head Chef  = the taste-feedback orchestrator (interpretation, goal, selection, dispatch)
//   Sous Chef A/B = scientist agents scientist_A / scientist_B
//   Line cooks = candidate schedule simulations (candidate_ranking)
//   Guest      = the judge;  Station A/B = chambers A/B
export function chefName(id) {
  const side = agentSide(id);
  return side ? `Sous Chef ${side}` : String(id ?? "system").replace(/^system$/i, "Head Chef");
}
/** Name element with the raw agent id as tooltip. */
export function chefLabel(id, cls = "") {
  const side = agentSide(id);
  return h("span", { class: cls, title: `agent id: ${side ? `scientist_${side}` : (id ?? "system")}` }, chefName(id));
}
/** Replace agent ids inside free text (e.g. "scientist_B → scientist_A") with brigade names. */
export function brigadeText(t) {
  return String(t ?? "").replace(/\bscientist[_\s-]?([AB])\b/gi, (_, s) => `Sous Chef ${s.toUpperCase()}`);
}

// ---------------------------------------------------------------- brigade icons (inline SVG, currentColor)
const ICON_PATHS = {
  // sous chef: classic toque
  toque: '<path d="M7 18h10v3H7z"/><path d="M6.5 16.5c-2.2-.4-3.5-2.2-3.5-4.2 0-2.4 2-4.3 4.4-4.2C8 5.7 9.8 4 12 4s4 1.7 4.6 4.1c2.4-.1 4.4 1.8 4.4 4.2 0 2-1.3 3.8-3.5 4.2V17h-11z"/>',
  // head chef: tall toque with a band
  headchef: '<path d="M6.5 18.5h11V21h-11z"/><path d="M7 15.6C4.7 15 3.4 13 3.4 10.8c0-2.6 2.1-4.6 4.6-4.5C8.6 3.6 10.1 2 12 2s3.4 1.6 4 4.3c2.5-.1 4.6 1.9 4.6 4.5 0 2.2-1.3 4.2-3.6 4.8V17H7z"/><path d="M8 13.2h8v1.4H8z" fill="#15171a"/>',
  // line cook: pan
  pan: '<path d="M3 11h13a0 0 0 0 1 0 0v1a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5v-1z"/><path d="M16 11.4h5.2a.8.8 0 0 1 0 1.6H16z"/><path d="M7 6.5c0-1 1-1.2 1-2.2M10 6.5c0-1 1-1.2 1-2.2" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round"/>',
  // guest: fork & knife
  guest: '<path d="M7 3v6a2 2 0 0 0 1.2 1.8V21h1.6V10.8A2 2 0 0 0 11 9V3h-1v5H9.4V3H8.6v5H8V3z"/><path d="M15.5 3c-1.8 1.3-2.5 3.6-2.5 6.5V13h2.2v8h1.6V3z"/>',
  // station: proofing chamber
  station: '<rect x="4" y="3" width="16" height="18" rx="3"/><rect x="7" y="6" width="10" height="8" rx="1.5" fill="#15171a"/><circle cx="9" cy="17.5" r="1.1" fill="#15171a"/><circle cx="15" cy="17.5" r="1.1" fill="#15171a"/>',
  device: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 3v3M12 3v3M15 3v3M9 18v3M12 18v3M15 18v3M3 9h3M3 12h3M3 15h3M18 9h3M18 12h3M18 15h3" stroke="currentColor" stroke-width="1.4"/>',
  shield: '<path d="M12 3l7 3v5c0 4.6-3 8.4-7 10-4-1.6-7-5.4-7-10V6z"/>',
  chart: '<path d="M4 19h16v1.6H4z"/><path d="M5 16l4.5-5 3.5 3 6-7" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
  atom: '<circle cx="12" cy="12" r="2"/><ellipse cx="12" cy="12" rx="9" ry="3.6" fill="none" stroke="currentColor" stroke-width="1.5"/><ellipse cx="12" cy="12" rx="9" ry="3.6" fill="none" stroke="currentColor" stroke-width="1.5" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="9" ry="3.6" fill="none" stroke="currentColor" stroke-width="1.5" transform="rotate(-60 12 12)"/>',
  plug: '<path d="M9 2v5M15 2v5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M6 7h12v3a6 6 0 0 1-5 5.9V22h-2v-6.1A6 6 0 0 1 6 10z"/>',
  camera: '<path d="M4 7h3.5L9 5h6l1.5 2H20a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.2" fill="#15171a"/>',
};
/** Inline SVG icon (no external assets). */
export function icon(name, { size = 18, cls = "", title = null } = {}) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "currentColor");
  svg.setAttribute("class", `ico ${cls}`.trim());
  if (title) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", title); } else svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICON_PATHS[name] || ICON_PATHS.toque;
  return svg;
}

/** Intentional empty state: icon, short title, one-line hint. */
export function emptyCard({ icon: ic = "plug", title, detail = "", compact = false } = {}) {
  return h("div", { class: `empty-card${compact ? " compact" : ""}` },
    h("span", { class: "empty-ico" }, icon(ic, { size: compact ? 20 : 28 })),
    h("div", null, h("div", { class: "empty-title" }, title), detail ? h("div", { class: "empty-detail" }, detail) : null));
}
