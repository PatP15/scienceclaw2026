// Small DOM / formatting / data helpers shared by every panel. No dependencies.

export const COLORS = {
  A: "#3987e5", B: "#d95926",
  Aband: "rgba(57,135,229,0.18)", Bband: "rgba(217,89,38,0.18)",
  text: "#f0efec", text2: "#c3c2b7", muted: "#8f8e86", grid: "#2e2e2b", border: "#383835",
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
    hoverlabel: { bgcolor: "#2c2c29", bordercolor: COLORS.border, font: { family: FONT, color: COLORS.text, size: 12 } },
    ...Object.fromEntries(Object.entries(extra).filter(([k]) => k !== "xaxis" && k !== "yaxis")),
  };
}
// Never link out to the Plotly cloud; no mode bar (touch-first, avoids hover-only UI).
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
