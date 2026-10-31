// Entry point: token capture, tabs, global banner (mode + SSE state), panel wiring.
import { get, captureTokenFromURL, getToken, setToken, EventStream } from "./api.js";
import { $, $$, toast, icon } from "./util.js";
import { LivePanel } from "./live.js";
import { TwinPanel } from "./twin.js";
import { MolecularPanel } from "./molecular.js";
import { TastePanel } from "./taste.js";

const EVENT_TYPES = ["telemetry", "image", "setpoint", "command", "ack", "experiment", "feedback", "feedback_result", "message", "simulation"];
const TAB_KEY = "fermentai.tab";
const store = { serverOffsetMs: 0, health: null, modeLabel: null };

captureTokenFromURL();

// ------------------------------------------------------------------ global banner
// Reasoning mode: "Online Agent" | "Offline Policy" (pipeline reasoning_mode_label); raw mode_label in tooltip.
function setModePill({ reasoning, modeLabel, pipeline, replay } = {}) {
  const pill = $("#mode-pill");
  const raw = String(modeLabel || "");
  let label = reasoning || (/^live:/i.test(raw) ? "Online Agent" : raw ? "Offline Policy" : "");
  let cls = /online/i.test(label) ? "mode-live" : "mode-offline";
  if (pipeline === false || /placeholder/i.test(raw)) { label = "Placeholder (agents pipeline unavailable)"; cls = "mode-placeholder"; }
  if (replay) { label = `${label || "Offline Policy"} · REPLAY (recorded)`; cls = "mode-replay"; }
  if (/live unavailable/i.test(raw)) label += " · online LLM unavailable";
  pill.className = `pill pill-mode ${cls}`;
  pill.textContent = `Reasoning: ${label || "unknown"}`;
  pill.title = raw ? `Server mode_label: ${raw}` : "Reasoning mode of the scientist agents";
}
document.addEventListener("fermentai:mode", (e) => { store.modeLabel = e.detail.modeLabel; setModePill(e.detail); });

// Demonstration mode from the record source of each chamber's latest (non-stale) telemetry.
const sources = {};
document.addEventListener("fermentai:source", (e) => { sources[e.detail.chamber] = e.detail; updateDataMode(); });
function updateDataMode() {
  const el = $("#data-mode");
  const bar = $("#mode-bar");
  const active = new Set();
  const parts = [];
  for (const c of ["A", "B"]) {
    const s = sources[c];
    if (!s) continue;
    const m = s.stale ? null : s.mode;
    if (m) active.add(m);
    const short = { live: "LIVE", replay: "REPLAY", simulation: "SIM" };
    parts.push(`${c}: ${s.stale ? "stale" : (m ? short[m] : (s.source || "no data"))}`);
  }
  el.querySelectorAll(".dm-seg").forEach((seg) => seg.classList.toggle("on", active.has(seg.dataset.mode)));
  el.querySelector(".dm-detail").textContent = parts.length ? parts.join(" · ") : "waiting for data…";
  bar.className = `mode-bar ${active.size === 1 ? [...active][0] : active.size > 1 ? "mixed" : ""}`;
  const word = { live: "LIVE = physical devices (source measured)", replay: "REPLAY = recorded data", simulation: "SIMULATION = virtual devices / generated data" };
  el.title = `Demonstration mode from the telemetry actually arriving. ${[...active].map((m) => word[m]).join("; ") || "No fresh telemetry."}`;
}

function setSsePill({ state, retryInMs }) {
  const pill = $("#sse-pill");
  const txt = pill.querySelector(".txt");
  if (state === "connected") { pill.className = "pill pill-ok"; txt.textContent = "Connected (SSE)"; }
  else if (state === "reconnecting") { pill.className = "pill pill-warn"; txt.textContent = `Reconnecting${retryInMs ? ` in ${Math.ceil(retryInMs / 1000)} s` : "…"}`; }
  else if (state === "closed") { pill.className = "pill pill-bad"; txt.textContent = "Disconnected"; }
  else { pill.className = "pill pill-warn"; txt.textContent = "Connecting…"; }
}

function setTokenPill() {
  const b = $("#token-pill");
  const has = !!getToken();
  b.classList.toggle("has-token", has);
  b.textContent = has ? "✓ Write access" : "Read-only";
  b.title = has ? "API token stored on this device (tap to change)" : "Tap to enter the API token for writes";
}
document.addEventListener("fermentai:token", setTokenPill);
document.addEventListener("fermentai:auth-failed", () => toast("The API rejected the stored token. Tap “Write access” to re-enter it.", "bad"));

function wireTokenDialog() {
  const dlg = $("#token-dialog"), input = $("#token-input");
  $("#token-pill").addEventListener("click", () => { input.value = getToken(); dlg.hidden = false; input.focus(); });
  $("#token-cancel").addEventListener("click", () => { dlg.hidden = true; });
  $("#token-clear").addEventListener("click", () => { setToken(""); dlg.hidden = true; toast("Token removed from this device."); });
  $("#token-save").addEventListener("click", () => { setToken(input.value); dlg.hidden = true; toast(getToken() ? "Token saved on this device." : "Token cleared."); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") $("#token-save").click(); });
}

function startClock() {
  const el = $("#clock");
  const tick = () => { el.textContent = new Date(Date.now() + (store.serverOffsetMs || 0)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  tick(); setInterval(tick, 1000);
}

// ------------------------------------------------------------------ tabs
function wireTabs(panels) {
  const show = (name) => {
    if (!panels[name]) name = "live";
    for (const btn of $$(".tab")) btn.setAttribute("aria-selected", String(btn.dataset.tab === name));
    for (const [k, p] of Object.entries(panels)) {
      const sec = document.getElementById(`tab-${k}`);
      sec.hidden = k !== name;
      if (k === name) p?.show?.(); else p?.hide?.();
    }
    try { localStorage.setItem(TAB_KEY, name); } catch { /* ignore */ }
    if (location.hash !== `#${name}`) history.replaceState(null, "", `${location.pathname}${location.search}#${name}`);
  };
  for (const btn of $$(".tab")) btn.addEventListener("click", () => show(btn.dataset.tab));
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  let initial = location.hash.slice(1);
  if (!panels[initial]) { try { initial = localStorage.getItem(TAB_KEY) || "live"; } catch { initial = "live"; } }
  show(initial);
  window.addEventListener("resize", () => Object.values(panels).forEach((p) => p?.resize?.()));
}

// ------------------------------------------------------------------ icons for tabs / section headers
function decorate() {
  const tabIcons = { live: "station", twin: "chart", molecular: "atom", taste: "headchef" };
  for (const b of $$(".tab")) if (!b.querySelector(".ico")) b.prepend(icon(tabIcons[b.dataset.tab] || "toque", { size: 18 }));
  const secIcons = { "tab-twin": "chart", "tab-molecular": "atom" };
  for (const [id, ic] of Object.entries(secIcons)) {
    const h2 = document.querySelector(`#${id} .section-head h2`);
    if (h2 && !h2.querySelector(".ico")) h2.prepend(icon(ic, { size: 20 }));
  }
}

// ------------------------------------------------------------------ boot
async function boot() {
  decorate();
  setTokenPill();
  wireTokenDialog();
  startClock();

  // Connect SSE first (only new events), then take REST snapshots, so nothing is missed in between.
  const stream = new EventStream(EVENT_TYPES);
  stream.on("_state", setSsePill);
  stream.connect();
  window.addEventListener("online", () => { if (stream.state !== "connected") stream.connect(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && stream.state !== "connected") stream.connect(); });

  try {
    store.health = await get("/api/v1/health");
    store.serverOffsetMs = Date.parse(store.health.server_time_utc) - Date.now();
    const llm = String(store.health.llm_mode || "offline");
    setModePill({ reasoning: llm === "offline" ? "Offline Policy" : "Online Agent (configured)", modeLabel: `server llm_mode=${llm}`, pipeline: store.health.pipeline_available });
  } catch (e) {
    setModePill({ reasoning: "unknown (API unreachable)" });
    toast(`Cannot reach the local API: ${e.message}`, "bad");
  }

  const live = new LivePanel(store, stream);
  const twin = new TwinPanel(store);
  const molecular = new MolecularPanel();
  const taste = new TastePanel(store, stream, live);
  if (store.health && store.health.llm_mode && store.health.llm_mode !== "offline") taste.setMode("live");
  wireTabs({ live, twin, molecular, taste });
  await live.load();
  await taste.init();
  window.fermentai = { store, stream, live, twin, molecular, taste }; // handy for debugging on the iPad
}

boot();
