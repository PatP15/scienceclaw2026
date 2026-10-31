// Entry point: token capture, tabs, global banner (mode + SSE state), panel wiring.
import { get, captureTokenFromURL, getToken, setToken, EventStream } from "./api.js";
import { $, $$, toast } from "./util.js";
import { LivePanel } from "./live.js";
import { TwinPanel } from "./twin.js";
import { MolecularPanel } from "./molecular.js";
import { TastePanel } from "./taste.js";

const EVENT_TYPES = ["telemetry", "image", "setpoint", "command", "ack", "experiment", "feedback", "feedback_result", "message", "simulation"];
const TAB_KEY = "fermentai.tab";
const store = { serverOffsetMs: 0, health: null, modeLabel: null };

captureTokenFromURL();

// ------------------------------------------------------------------ global banner
function setModePill(label, pipelineAvailable) {
  const pill = $("#mode-pill");
  const text = String(label || "");
  let cls = "mode-offline";
  if (/placeholder/i.test(text) || pipelineAvailable === false) cls = "mode-placeholder";
  else if (/(live|llm|claude|anthropic|cloud)/i.test(text) && !/(fallback|deterministic|^offline)/i.test(text)) cls = "mode-live";
  pill.className = `pill pill-mode ${cls}`;
  // Keep the pill short; the full server label (including any fallback reason) is in the tooltip.
  const main = text.replace(/\s*\(.*\)\s*$/, "");
  const pretty = main.replace(/^offline:/i, "Offline · ").replace(/^live:/i, "Live LLM · ").replace(/_/g, " ")
    + (/live unavailable/i.test(text) ? " · live LLM unavailable" : "");
  pill.textContent = text ? `Mode: ${pretty}` : "Mode: unknown";
  pill.title = `Decision engine mode reported by the server: ${text}`;
}
document.addEventListener("fermentai:mode", (e) => { store.modeLabel = e.detail.modeLabel; setModePill(e.detail.modeLabel, e.detail.pipeline); });

function setSsePill({ state, retryInMs }) {
  const pill = $("#sse-pill");
  const txt = pill.querySelector(".txt");
  if (state === "connected") { pill.className = "pill pill-ok"; txt.textContent = "Live (SSE connected)"; }
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

// ------------------------------------------------------------------ boot
async function boot() {
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
    setModePill(llm === "offline" ? "Offline deterministic fallback (server default)" : `Live LLM configured (${llm})`, store.health.pipeline_available);
    if (!store.health.pipeline_available) setModePill("placeholder — agents pipeline not installed", false);
  } catch (e) {
    setModePill("API unreachable", false);
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
