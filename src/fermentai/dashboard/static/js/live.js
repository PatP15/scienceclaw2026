// Section 1: Live Laboratories - a grid of lab cards (#live) and a lab detail view (#lab/<id>).
// Terse by design: only sensors that report a value from the CURRENT device are shown, each as a big number
// plus a short history line; one provenance badge per card; explanations live in tooltips / disclosures.
import { get, post, del, getToken, ApiError } from "./api.js";
import { h, clear, fmt, timeAgo, hhmmss, statusBadge, sparkline, agentSide, toast, COLORS, num, normProv, tfield, demoMode,
  deviceBadge, chefLabel, brigadeText, icon, emptyCard, provBadge, normSchedule, plotLayout, PLOT_CONFIG, whenPlotly } from "./util.js";
import { messageText, messageSender } from "./messages.js";
import { ChatPanel } from "./chat.js";
import { TwinPanel } from "./twin.js";

const SHOW_CAMERA = false;       // camera frames are hidden for now (API endpoints untouched); flip to bring them back
const DOOR_FLASH_MS = 1800;     // door icon/tile pulse after an SSE `door` open event

const STALE_AFTER_S = 60;       // configs/hardware.yaml telemetry.stale_after_s; the server flag wins on refresh
const HISTORY_MAX = 720;        // rows kept per chamber (~2 h at 10 s)
const WINDOW_MIN = 60;          // history window shown on cards and charts
const MOTOR_TERMINAL = new Set(["completed", "failed", "cancelled", "rejected", "expired", "expired_unacked", "superseded"]);
const colorOf = (c) => COLORS[c] || COLORS.accent;
const isDemo = (st) => st?.kind === "demo";
const hasName = (st) => !!st?.label && !/^Chamber\s+[A-Za-z0-9]\b/.test(st.label);

export function motorTrack(cmd) {
  // issued -> accepted (device ack) -> completed (device ack). The motor counts as having RUN only
  // after the device's "completed" ack (contract v1.1 `ran`); queued commands are never shown as run.
  const s = (cmd && cmd.status) || "none";
  const step = (label, cls) => h("span", { class: `stp ${cls}` }, label);
  const arrow = () => h("span", { class: "arrow" }, "→");
  const bad = MOTOR_TERMINAL.has(s) && s !== "completed";
  const accepted = cmd && (["accepted", "completed"].includes(s) || (Array.isArray(cmd.acks) && cmd.acks.some((a) => a.status === "accepted")));
  const ran = cmd && (cmd.ran === true || s === "completed");
  const issued = s === "issued" ? "now" : s === "none" ? "" : "done";
  const acc = s === "accepted" ? "now" : accepted ? "done" : bad ? "bad" : "";
  const last = bad ? step(s.replace(/_/g, " "), "bad") : step(ran ? "completed · ran" : "completed", ran ? "done" : "");
  return h("span", { class: "cmd-track", title: cmd?.command_id ? `command ${cmd.command_id}${ran ? " — device acked completed" : " — not yet run"}` : "" },
    step("issued", issued), arrow(), step("accepted", acc), arrow(), last);
}

const ens = (r) => tfield(r, "diagnostics")?.ens160 || null;
/** Sensor catalogue. A sensor is shown only when the latest telemetry from the current device has a value. */
const SENSORS = [
  { key: "air", label: "Air", unit: "°C", dp: 1, get: (r) => num(tfield(r, "chamber_temperature_c")) },
  { key: "starter", label: "Starter", unit: "°C", dp: 1, get: (r) => num(tfield(r, "starter_temperature_c")) },
  { key: "humidity", label: "Humidity", unit: "%", dp: 0, get: (r) => num(tfield(r, "relative_humidity_percent")) },
  { key: "rise", label: "Rise", unit: "mm", dp: 1, get: (r) => num(tfield(r, "starter_height_mm")) },
  { key: "ph", label: "pH", unit: "", dp: 2, get: (r) => num(tfield(r, "ph")) },
  { key: "aqi", label: "Air quality", unit: "AQI", dp: 0, note: "index · not CO₂", tip: "ENS160 air-quality indices (AQI, TVOC, eCO₂ equivalent) — not a CO₂ measurement",
    get: (r) => { const e = ens(r); return e && e.valid !== 0 ? num(e.aqi) : null; },
    sub: (r) => { const e = ens(r); return e ? [num(e.tvoc_ppb) !== null ? `TVOC ${fmt(e.tvoc_ppb, 0)}` : null, num(e.eco2_ppm) !== null ? `eCO₂ idx ${fmt(e.eco2_ppm, 0)}` : null].filter(Boolean).join(" · ") : ""; } },
  { key: "gas", label: "Gas", unit: "kΩ", dp: 1, note: "BME680 · not CO₂", get: (r) => { const g = num(tfield(r, "gas_resistance_ohms")); return g === null ? null : g / 1000; } },
  { key: "pressure", label: "Pressure", unit: "hPa", dp: 1, get: (r) => { const p = num(tfield(r, "pressure_pa")); return p === null ? null : p / 100; } },
  { key: "peltier", label: "Peltier", unit: "%", dp: 0, text: (r) => tfield(r, "peltier_mode"),
    get: (r) => { const f = num(tfield(r, "peltier_output_fraction")); return f === null ? (tfield(r, "peltier_mode") ? 0 : null) : f * 100; } },
];
const NOT_FITTED = [["ph", "pH"], ["pressure_pa", "pressure"], ["gas_resistance_ohms", "gas"], ...(SHOW_CAMERA ? [["starter_height_mm", "rise camera"]] : []),
  ["relative_humidity_percent", "humidity"], ["internal_heatsink_temperature_c", "heatsinks"]];

const rowTime = (r) => Date.parse(r.timestamp_utc || r.received_at_utc);
function stripArrow(t) { return String(t || "").replace(/^\s*\[[^\]]{1,60}(→|->)[^\]]{1,60}\]\s*/, ""); }
function fmtUptime(ms) {
  const s = Math.floor(Number(ms) / 1000);
  if (!Number.isFinite(s)) return "?";
  if (s < 120) return `${s}s`;
  if (s < 7200) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export class LivePanel {
  constructor(store, stream) {
    this.store = store;
    this.stream = stream;
    this.ids = ["A", "B"];
    this.state = {};
    this.rows = {};          // chamber -> recent telemetry rows (oldest first)
    this.agentMsg = {};
    this.agentState = {};
    this.motor = {};
    this.allMsgs = [];
    this.experiments = [];
    this.busy = {};
    this.thinking = {};      // chamber -> {pending: n, until: ms}
    this.chats = {};         // chamber -> ChatPanel (detail view)
    this.detailId = null;
    this.gridEl = document.getElementById("lab-grid");
    this.gridView = document.getElementById("live-grid-view");
    this.detailView = document.getElementById("lab-detail");
    this.passThreadEl = document.getElementById("pass-thread");
    this.sp = {};            // chamber -> {el, result, cmd, busy, polledAt} (manual setpoint control)
    this.twin = new TwinPanel(store);
    document.getElementById("lab-twin-toggle").addEventListener("click", () => this.toggleTwin());
    this.wire();
  }

  toggleTwin(open) {
    const body = document.getElementById("lab-twin-body"), btn = document.getElementById("lab-twin-toggle");
    const want = open === undefined ? body.hidden : open;
    body.hidden = !want;
    btn.textContent = want ? "Close digital twin" : "Open digital twin";
    btn.setAttribute("aria-expanded", String(want));
    this.twin.setOpen(want);
  }
  resize() { this.twin.resize(); }

  // ------------------------------------------------------------------ wiring
  wire() {
    const s = this.stream;
    s.on("telemetry", (f) => this.onTelemetry(f));
    s.on("door", (f) => this.onDoor(f.data || {}, f.chamber_id));
    if (SHOW_CAMERA) s.on("image", (f) => {
      const c = f.chamber_id || f.data?.chamber_id;
      if (!this.state[c]) return;
      this.state[c].latest_image = { id: f.data.image_id, url: f.data.url, timestamp_utc: f.data.timestamp_utc, source: f.data.source,
        device_id: f.data.device_id, stale_image_from_other_device: f.data.stale_image_from_other_device };
      this.render(c);
    });
    s.on("setpoint", (f) => { if (f.chamber_id) this.refreshState(f.chamber_id); });
    s.on("experiment", () => { this.ids.forEach((c) => this.refreshState(c)); this.loadExperiments(); });
    s.on("command", (f) => this.onCommand(f.data, f.chamber_id));
    s.on("ack", (f) => this.onCommand({ ...f.data, status: f.data.command_status || f.data.status }, f.chamber_id));
    s.on("message", (f) => {
      const side = agentSide(f.data.sender ?? messageSender(f.data.content));
      if (side) this.agentMsg[side] = f.data;
      this.allMsgs.push(f.data); this.allMsgs = this.allMsgs.slice(-60);
      this.renderThreads();
      if (side) this.render(side);
      clearTimeout(this._agentTimer);
      this._agentTimer = setTimeout(() => this.ids.forEach((c) => this.refreshAgent(c)), 1500);
    });
    s.on("chat", (f) => this.onChatEvent(f.data || {}));
    document.addEventListener("fermentai:thinking", (e) => this.setThinking(e.detail.station, e.detail.on));
    setInterval(() => this.ids.forEach((c) => this.render(c)), 5000);
    setInterval(() => this.ids.forEach((c) => this.refreshState(c)), 20000);
    setInterval(() => this.ids.forEach((c) => this.refreshAgent(c)), 30000);
    setInterval(() => this.renderHats(), 1000);
  }

  // ------------------------------------------------------------------ door flash
  onDoor(d, chamber) {
    const c = d.chamber_id || chamber;
    if (!c || !this.state[c]) return;
    if (this.state[c].latest_telemetry) this.state[c].latest_telemetry.door_open = !!d.open;
    if (d.open !== true) { this.render(c); return; }
    (this.doorFlash ||= {})[c] = Date.now() + DOOR_FLASH_MS;
    this.render(c);
    setTimeout(() => this.render(c), DOOR_FLASH_MS + 50);
  }
  doorFlashing(c) { return Date.now() < ((this.doorFlash || {})[c] || 0); }

  // ------------------------------------------------------------------ thinking (hat wiggle)
  setThinking(station, on) {
    for (const c of station ? [station] : this.ids) {
      const t = (this.thinking[c] ||= { pending: 0, until: 0 });
      t.pending = Math.max(0, t.pending + (on ? 1 : -1));
    }
    this.renderHats();
  }
  onChatEvent(d) {
    const role = String(d.role || "");
    if (!/sous|head/.test(role) && d.stage !== "handoff") return;
    const fromSession = Object.entries(this.chats).find(([, cp]) => cp.sessionId && cp.sessionId === d.session_id)?.[0];
    const stations = /head/.test(role) || d.stage === "handoff" ? this.ids : fromSession ? [fromSession] : [];
    for (const c of stations) { const t = (this.thinking[c] ||= { pending: 0, until: 0 }); t.until = Date.now() + 6000; }
    this.renderHats();
  }
  isThinking(c) {
    const t = this.thinking[c] || {};
    const m = this.motor[c];
    return t.pending > 0 || Date.now() < (t.until || 0) || (m && (m.status === "issued" || m.status === "accepted"));
  }
  hat(c, size = 26) {
    const st = this.state[c] || {};
    const running = tfield(st.latest_telemetry, "motor_running") === true;
    const m = this.motor[c];
    const title = running ? "hat motor running" : this.isThinking(c) ? "thinking — hat bobbing" : `hat idle${m?.status ? ` · last command ${m.status.replace(/_/g, " ")}` : ""}`;
    return h("span", { class: `hat${this.isThinking(c) ? " thinking" : ""}${running ? " running" : ""}`, "data-hat": c, title }, icon("toque", { size }));
  }
  renderHats() {
    document.querySelectorAll("[data-hat]").forEach((el) => {
      const c = el.dataset.hat;
      el.classList.toggle("thinking", this.isThinking(c));
      el.classList.toggle("running", tfield(this.state[c]?.latest_telemetry, "motor_running") === true);
    });
  }

  // ------------------------------------------------------------------ USB serial bridge
  startBridgePoll() {
    const paths = this.store.apiPaths;
    if (paths && !paths.has("/api/v1/bridge/status")) { this.bridge = { unsupported: true }; return; }
    const poll = async () => {
      try { this.bridge = await get("/api/v1/bridge/status"); }
      catch (e) { if (e.status === 404) { this.bridge = { unsupported: true }; clearInterval(this.bridgeTimer); } }
      this.ids.forEach((c) => this.render(c));
    };
    poll();
    this.bridgeTimer = setInterval(poll, 5000);
  }
  usbLink(c) {
    const b = this.bridge;
    if (!b || b.unsupported) return null;
    const ch = (b.chambers || {})[c] || null;
    const link = String(ch?.link || (b.bridge_running ? "disconnected" : "bridge_offline")).toLowerCase();
    let text, cls;
    if (!b.bridge_running || link === "bridge_offline") { text = "USB · bridge off"; cls = "off"; }
    else if (link === "connected") { text = ch?.simulated ? "USB · simulated" : "USB · connected"; cls = "ok"; }
    else if (/log/.test(link)) { text = "USB · logs only"; cls = "warn"; }
    else { text = "USB · disconnected"; cls = "bad"; }
    const title = [ch?.label, b.transport || "usb-serial", ch?.port && `port ${ch.port}`, ch?.device_id && `device ${ch.device_id}`,
      ch?.firmware_version && `fw ${ch.firmware_version}`, ch?.last_seen && `last seen ${ch.last_seen}`, !b.bridge_running && b.reason].filter(Boolean).join(" · ");
    return { cls, text, title, realLinked: cls === "ok" && ch?.simulated === false, deviceId: ch?.device_id || null };
  }

  // ------------------------------------------------------------------ data
  async load() {
    try { const all = await get("/api/v1/chambers"); this.ids = Object.keys(all); Object.assign(this.state, all); } catch { /* keep A/B */ }
    this.startBridgePoll();
    this.loadExperiments();
    await Promise.all(this.ids.map(async (c) => {
      if (!this.state[c]) await this.refreshState(c, false);
      try { this.rows[c] = await get(`/api/v1/chambers/${c}/telemetry?limit=${HISTORY_MAX}`); } catch { this.rows[c] = []; }
      if (this.state[c]?.last_motor_command) this.motor[c] = this.state[c].last_motor_command;
      await this.refreshAgent(c, false);
    }));
    try {
      const msgs = await get("/api/v1/agents/all/messages?limit=60");
      this.allMsgs = msgs.slice(-60);
      for (const m of msgs) { const s = agentSide(m.sender ?? messageSender(m.content)); if (s) this.agentMsg[s] = m; }
    } catch { /* none */ }
    this.buildGrid();
    this.ids.forEach((c) => this.render(c));
    this.renderThreads();
  }
  async refreshAgent(c, rerender = true) {
    try { this.agentState[c] = await get(`/api/v1/agents/${c}/state`); } catch { /* keep last */ }
    if (rerender) this.render(c);
  }
  async refreshState(c, rerender = true) {
    try {
      const st = await get(`/api/v1/chambers/${c}/state`);
      this.state[c] = st;
      this.store.serverOffsetMs = Date.parse(st.server_time_utc) - Date.now();
      if (st.last_motor_command) this.motor[c] = { ...(this.motor[c] || {}), ...st.last_motor_command };
    } catch { /* offline API: keep last */ }
    if (rerender) this.render(c);
  }
  async loadExperiments() {
    try { this.experiments = await get("/api/v1/experiments?limit=6"); } catch { /* keep */ }
    this.renderThreads();
  }
  onTelemetry(f) {
    const c = f.chamber_id || f.data?.chamber_id;
    if (!c) return;
    if (!this.ids.includes(c)) { this.ids.push(c); this.buildGrid(); }
    const st = this.state[c] || (this.state[c] = { chamber_id: c });
    st.latest_telemetry = f.data;
    st.provenance = f.data.source;
    st.telemetry_stale = false;
    st.device_reported_setpoint_c = f.data.temperature_setpoint_c;
    (this.rows[c] ||= []).push(f.data);
    if (this.rows[c].length > HISTORY_MAX) this.rows[c].splice(0, this.rows[c].length - HISTORY_MAX);
    this.render(c);
  }
  onCommand(d, chamber) {
    if (!d || (d.command_type && d.command_type !== "motor")) return;
    const c = chamber || d.chamber_id;
    if (!c) return;
    const cur = this.motor[c];
    if (cur && cur.command_id !== d.command_id && d.status !== "issued") return;
    this.motor[c] = { ...(cur && cur.command_id === d.command_id ? cur : {}), ...d };
    this.render(c);
    this.renderHats();
  }
  serverNow() { return Date.now() + (this.store.serverOffsetMs || 0); }
  isStale(st) {
    const tel = st?.latest_telemetry;
    if (!tel) return true;
    return !((this.serverNow() - Date.parse(tel.received_at_utc || tel.timestamp_utc)) / 1000 <= STALE_AFTER_S);
  }
  windowRows(c) {
    const cut = this.serverNow() - WINDOW_MIN * 60000;
    return (this.rows[c] || []).filter((r) => rowTime(r) >= cut);
  }

  // ------------------------------------------------------------------ derived per-station facts
  facts(c) {
    const st = this.state[c] || {};
    const tel = st.latest_telemetry || null;
    const stale = this.isStale(st);
    const usb = this.usbLink(c);
    // A real USB-linked device makes the station LIVE/MEASURED only when the latest telemetry is from that
    // device AND not itself labelled simulated (virtual devices may reuse the real device id).
    const fromLinked = usb?.realLinked && tel && (!usb.deviceId || tfield(tel, "device_id") === usb.deviceId) && normProv(tel.source, "device") !== "simulated_device";
    const prov = fromLinked ? "measured" : (tel ? tel.source : null);
    const kind = normProv(prov, "device");
    let link;
    if (usb && usb.cls !== "off") link = usb;
    else if (!tel || stale) link = { cls: "off", text: usb ? usb.text : "offline", title: usb?.title || "no fresh telemetry" };
    else if (kind === "simulated_device") link = { cls: "sim", text: "simulated", title: "virtual device over HTTP" };
    else link = { cls: "ok", text: "network", title: "device posting over the LAN (HTTP)" };
    const sensors = tel ? SENSORS.filter((s) => s.get(tel) !== null) : [];
    const unsynced = tel && (tel.timestamp_utc === null || tel.timestamp_utc === undefined || tfield(tel, "timestamp_quality") === "unsynced");
    document.dispatchEvent(new CustomEvent("fermentai:source", { detail: { chamber: c, source: prov, mode: demoMode(prov), stale } }));
    return { st, tel, stale, usb, prov, link, sensors, unsynced, online: !!tel && !stale };
  }

  // ------------------------------------------------------------------ grid
  buildGrid() {
    if (!this.gridEl) return;
    this.cardEls = {};
    clear(this.gridEl, this.ids.map((c) => {
      const el = h("article", { class: "lab-card card", dataset: { chamber: c }, tabindex: "0", role: "link", "aria-label": `Open ${this.state[c]?.label || "Station " + c}`,
        onclick: (ev) => { if (!ev.target.closest("button, a, summary")) location.hash = `#lab/${c}`; },
        onkeydown: (ev) => { if (ev.key === "Enter") location.hash = `#lab/${c}`; } });
      this.cardEls[c] = el;
      return el;
    }));
  }

  render(c) {
    if (this.cardEls?.[c]) this.renderCard(c, this.cardEls[c]);
    if (this.detailId === c) this.renderDetail(c);
  }

  headBits(c, f, { big = false } = {}) {
    return [
      h("div", { class: "lab-id" },
        h("span", { class: "lab-dot", style: { background: colorOf(c) } }),
        h("div", null, h("div", { class: big ? "lab-title big" : "lab-title" }, hasName(f.st) ? f.st.label : `Station ${c}`,
          hasName(f.st) ? h("span", { class: "lab-tag" }, c) : null,
          isDemo(f.st) ? h("span", { class: "lab-tag demo", title: "fictional demo lab, simulated device" }, "DEMO") : null),
          hasName(f.st)
            ? (f.st.location ? h("div", { class: "lab-pin" }, `📍 ${f.st.location}`) : null)
            : h("div", { class: "lab-sub" }, (f.st.label || "").replace(/^Chamber [A-Za-z0-9]\s*[-–]\s*/, "").replace(/ hypothesis$/, "")))),
      h("div", { class: "lab-badges" },
        f.tel ? deviceBadge(f.prov) : null,
        h("span", { class: `usb-chip usb-${f.link.cls}`, title: f.link.title }, f.link.text),
        f.tel ? h("span", { class: `fresh-dot ${f.stale ? "stale" : "fresh"}`, title: f.stale ? `stale — last reading ${timeAgo(f.tel.received_at_utc, this.serverNow())}` : "fresh" }, f.stale ? "stale" : "live") : null,
        f.usb?.realLinked && f.tel && !(f.prov === "measured") ? h("span", { class: "usb-chip usb-warn", title: "A real USB device is linked, but the latest telemetry is labelled simulated — shown as recorded." }, "data ≠ USB device") : null),
      h("div", { class: "lab-icons" }, this.doorIcon(f.tel, c), this.hat(c, big ? 30 : 26)),
    ];
  }

  doorIcon(tel, c) {
    const d = tfield(tel, "door_open");
    if (d === null) return null;
    return h("span", { class: `door-ico ${d ? "open" : "closed"}${c && this.doorFlashing(c) ? " door-flash" : ""}`, title: d ? "door open" : "door closed" }, icon(d ? "door_open" : "door_closed", { size: 22 }));
  }

  /** Big air number + air/setpoint line; tiles for the other reporting sensors. */
  readings(c, f, { tileLines = true } = {}) {
    const rows = this.windowRows(c);
    const air = SENSORS[0];
    const airNow = air.get(f.tel);
    const line = (s) => sparkline([{ values: rows.map((r) => ({ t: rowTime(r), v: s.get(r) })), color: colorOf(c) }], { height: 30 });
    const hero = airNow === null ? null : h("div", { class: "lab-hero" },
      h("div", { class: "lab-hero-num" }, h("span", { class: "lbl" }, "Air"), h("span", { class: "big" }, fmt(airNow, 1), h("span", { class: "unit" }, "°C")),
        h("span", { class: "setp", title: "server setpoint (device-reported in tooltip)" }, `→ ${fmt(f.st.desired_setpoint_c, 1)}`)),
      sparkline([
        { values: rows.map((r) => ({ t: rowTime(r), v: num(tfield(r, "temperature_setpoint_c")) ?? f.st.desired_setpoint_c })), color: COLORS.muted, dash: true },
        { values: rows.map((r) => ({ t: rowTime(r), v: air.get(r) })), color: colorOf(c) },
      ], { height: 44 }));
    const tiles = f.sensors.filter((s) => s.key !== "air").map((s) => {
      const v = s.get(f.tel);
      return h("div", { class: "tile", title: s.tip || s.note || "" },
        h("div", { class: "tile-lbl" }, s.label, s.note ? h("span", { class: "tile-note" }, s.note) : null),
        h("div", { class: "tile-val" }, s.text ? [h("span", { class: "tile-txt" }, s.text(f.tel)), " ", h("span", { class: "unit" }, `${fmt(v, 0)} %`)] : [fmt(v, s.dp), h("span", { class: "unit" }, s.unit ? ` ${s.unit}` : "")]),
        s.sub ? h("div", { class: "tile-sub" }, s.sub(f.tel)) : null,
        tileLines ? line(s) : null);
    });
    const door = this.doorTimeline(c, rows);
    return h("div", { class: "lab-readings" }, hero, tiles.length || door ? h("div", { class: "tiles" }, tiles, door) : null);
  }

  doorTimeline(c, rows) {
    if (!rows.length || rows.every((r) => tfield(r, "door_open") === null)) return null;
    const t1 = this.serverNow(), t0 = t1 - WINDOW_MIN * 60000;
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 300 14"); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("class", "door-line");
    const bg = document.createElementNS(NS, "rect"); bg.setAttribute("x", 0); bg.setAttribute("y", 4); bg.setAttribute("width", 300); bg.setAttribute("height", 6); bg.setAttribute("rx", 3); bg.setAttribute("fill", "#2d323a"); svg.append(bg);
    let opens = 0, prev = false;
    rows.forEach((r, i) => {
      const open = tfield(r, "door_open") === true;
      if (open && !prev) opens++;
      prev = open;
      if (!open) return;
      const a = rowTime(r), b = i + 1 < rows.length ? rowTime(rows[i + 1]) : t1;
      const x = ((a - t0) / (t1 - t0)) * 300, w = Math.max(2, ((b - a) / (t1 - t0)) * 300);
      const seg = document.createElementNS(NS, "rect"); seg.setAttribute("x", x); seg.setAttribute("y", 2); seg.setAttribute("width", w); seg.setAttribute("height", 10); seg.setAttribute("rx", 2); seg.setAttribute("fill", "#fab219"); svg.append(seg);
    });
    const nowOpen = tfield(this.state[c]?.latest_telemetry, "door_open") === true;
    return h("div", { class: `tile door-tile${this.doorFlashing(c) ? " door-flash" : ""}`, title: `door state over the last ${WINDOW_MIN} min (amber = open)` },
      h("div", { class: "tile-lbl" }, "Door"),
      h("div", { class: "tile-val" }, nowOpen ? "open" : "closed", h("span", { class: "unit" }, ` · ${opens} open${opens === 1 ? "" : "s"}`)),
      svg);
  }

  chefLine(c) {
    const m = this.agentMsg[c];
    const text = m ? brigadeText(stripArrow(messageText(m.content ?? m))) : "";
    return h("div", { class: "lab-chef" },
      h("span", { class: "chef-av", style: { color: c === "B" ? "var(--chamber-B-text)" : "var(--chamber-A-text)" } }, icon("toque", { size: 16 })),
      h("span", { class: "chef-name" }, chefLabel(`scientist_${c}`)),
      h("span", { class: "chef-msg" }, text || "—"));
  }

  renderCard(c, el) {
    const f = this.facts(c);
    el.classList.toggle("offline", !f.online);
    if (!f.tel) {
      clear(el, h("div", { class: "lab-head" }, this.headBits(c, f)),
        emptyCard({ icon: "plug", title: "No device", detail: f.usb ? f.usb.text : "USB bridge pending", compact: true }),
        h("div", { class: "lab-foot" }, h("span", { class: "setp" }, `set ${fmt(f.st.desired_setpoint_c, 1)} °C`)));
      return;
    }
    clear(el, h("div", { class: "lab-head" }, this.headBits(c, f)), this.readings(c, f), this.chefLine(c), this.danceButton(c));
  }

  // one-click hat dance on the grid card; stopPropagation keeps the card from opening the detail view
  danceButton(c) {
    const m = this.motor[c];
    const running = m && !MOTOR_TERMINAL.has(m.status);
    return h("div", { class: "lab-dance" },
      h("button", { class: "btn btn-motor btn-sm", type: "button", disabled: this.busy[c] || running,
        title: getToken() ? "Make the chef hat dance for 5 s" : "Needs the API token",
        onclick: (ev) => { ev.stopPropagation(); this.startMotor(c); } },
        this.busy[c] ? "Sending…" : running ? "💃 dancing…" : "💃 Dance"));
  }

  // ------------------------------------------------------------------ detail view (#lab/<id>)
  openDetail(c) {
    if (!this.ids.includes(c)) c = this.ids[0];
    this.detailId = c;
    this.gridView.hidden = true;
    this.detailView.hidden = false;
    this.detailView.dataset.chamber = c;
    const chatRoot = document.getElementById("lab-chat");
    const demo = isDemo(this.state[c]);
    const modeSeg = this.detailView.querySelector(".detail-mode");
    if (modeSeg) modeSeg.hidden = demo;
    if (demo) { clear(chatRoot, h("section", { class: "card chat-card demo-chat-note" }, "Demo lab — chat available at Stations A and B")); }
    else if (!this.chats[c]) {
      this.chats[c] = new ChatPanel(h("section", { class: "card chat-card" }), this.taste, { station: c });
      this.chats[c].init(this.stream, this.store.apiPaths);
    }
    if (!demo) clear(chatRoot, this.chats[c].root);
    this._chartKey = null;
    this.twin.setChamber(c);
    this.toggleTwin(false);                // closed by default for each station visit
    this.buildSetpoint(c);
    this.renderDetail(c, true);
    window.scrollTo(0, 0);
  }
  closeDetail() {
    this.detailId = null;
    this.twin.setOpen(false);
    this.detailView.hidden = true;
    this.gridView.hidden = false;
  }
  show() { if (this.detailId) this.renderDetail(this.detailId, true); }
  hide() {}

  renderDetail(c, force = false) {
    const f = this.facts(c);
    clear(document.getElementById("lab-detail-head"),
      h("a", { class: "btn btn-ghost btn-sm back-link", href: "#live" }, "← Labs"),
      h("div", { class: "lab-head" }, this.headBits(c, f, { big: true })),
      this.motorRow(c, this.motor[c]));
    const now = Date.now();
    if (force || !this._detailAt || now - this._detailAt > 2000) {   // throttle chart redraws
      this._detailAt = now;
      this.renderDetailCharts(c, f);
    }
    this.renderDetailSide(c, f);
    this.updateSetpoint(c);
    this.renderThreads();
  }

  async renderDetailCharts(c, f) {
    const box = document.getElementById("lab-detail-charts");
    if (!f.tel) { clear(box, emptyCard({ icon: "plug", title: "No device", detail: f.usb ? f.usb.text : "USB bridge pending" })); return; }
    const key = f.sensors.map((s) => s.key).join(",");
    if (this._chartKey !== key || !box.querySelector(".dchart")) {
      this._chartKey = key;
      clear(box, f.sensors.map((s) => h("div", { class: `dchart-card${s.key === "air" ? " wide" : ""}` },
        h("div", { class: "dchart-head" }, h("span", { class: "tile-lbl" }, s.label, s.note ? h("span", { class: "tile-note" }, s.note) : null), h("span", { class: "dchart-val", "data-val": s.key })),
        h("div", { class: "dchart", id: `dchart-${s.key}` }))), this.doorTimeline(c, this.windowRows(c)));
    } else {
      const old = box.querySelector(".door-tile"); const nd = this.doorTimeline(c, this.windowRows(c));
      if (old && nd) old.replaceWith(nd);
    }
    let Plotly;
    try { Plotly = await whenPlotly(); } catch { return; }
    const rows = this.windowRows(c);
    const xs = rows.map((r) => new Date(rowTime(r)));
    for (const s of f.sensors) {
      const valEl = box.querySelector(`[data-val="${s.key}"]`);
      if (valEl) valEl.textContent = s.text ? `${s.text(f.tel)} · ${fmt(s.get(f.tel), 0)} %` : `${fmt(s.get(f.tel), s.dp)}${s.unit ? ` ${s.unit}` : ""}`;
      const el = document.getElementById(`dchart-${s.key}`);
      if (!el) continue;
      const traces = [{ x: xs, y: rows.map((r) => s.get(r)), type: "scatter", mode: "lines", name: s.label, line: { color: colorOf(c), width: 2 },
        hovertemplate: `${s.label} %{y:.${s.dp}f}${s.unit ? ` ${s.unit}` : ""}<extra></extra>` }];
      if (s.key === "air") traces.unshift({ x: xs, y: rows.map((r) => num(tfield(r, "temperature_setpoint_c")) ?? f.st.desired_setpoint_c), type: "scatter", mode: "lines", name: "setpoint",
        line: { color: COLORS.muted, width: 1.5, dash: "dash", shape: "hv" }, hovertemplate: "setpoint %{y:.1f} °C<extra></extra>" });
      await Plotly.react(el, traces, plotLayout({ showlegend: false, margin: { l: 38, r: 8, t: 4, b: 24 }, xaxis: { type: "date", tickformat: "%H:%M" } }), PLOT_CONFIG);
    }
  }

  /** Camera frame (only when SHOW_CAMERA), device drawer, Sous Chef status with a "why" disclosure. */
  renderDetailSide(c, f) {
    const box = document.getElementById("lab-detail-side");
    if (!box) return;
    let snap = null;
    if (SHOW_CAMERA) {
      // Image rule: never show a frame from another device than the latest telemetry's.
      const img = f.st.latest_image;
      const telKind = normProv(f.tel?.source, "device");
      const otherDevice = img && (img.stale_image_from_other_device === true
        || (img.device_id && f.tel && tfield(f.tel, "device_id") && img.device_id !== tfield(f.tel, "device_id"))
        || (f.tel && img.source && normProv(img.source, "device") !== telKind));
      snap = box.querySelector(".snap");
      const key = img && !otherDevice ? img.url : "none";
      if (!snap || snap.dataset.key !== key) {
        snap = h("div", { class: "snap", dataset: { key } },
          img && !otherDevice ? h("img", { src: img.url, alt: `Station ${c} camera frame`, loading: "lazy" }) : emptyCard({ icon: "camera", title: "No frame from this device yet", compact: true }),
          img && !otherDevice ? h("div", { class: "snap-cap" }, h("span", null, hhmmss(img.timestamp_utc)), deviceBadge(img.source)) : null);
      }
    }
    clear(box, ...(snap ? [snap] : []), this.deviceDrawer(c, f), this.agentBox(c, f.st));
  }

  deviceDrawer(c, f) {
    const tel = f.tel;
    if (!tel) return null;
    const diag = tfield(tel, "diagnostics") || {};
    const health = diag.sensor_health || {};
    const nOk = Object.values(health).filter((v) => v === "ok").length, nH = Object.keys(health).length;
    const fw = diag.firmware_version || tfield(tel, "firmware_version");
    const notFitted = NOT_FITTED.filter(([k]) => tfield(tel, k) === null).map(([, n]) => n);
    const hs = [num(tfield(tel, "internal_heatsink_temperature_c")), num(tfield(tel, "external_heatsink_temperature_c"))];
    const flags = (tel.quality_flags || []).filter(Boolean);
    return h("details", { class: "device-drawer" },
      h("summary", null, icon("device", { size: 16 }), h("b", null, tfield(tel, "device_id") || "device"),
        f.unsynced ? h("span", { class: "chip-mini warn" }, "device clock unsynced") : h("span", { class: "chip-mini ok" }, "device clock synced"),
        nH ? h("span", { class: `chip-mini ${nOk === nH ? "ok" : "warn"}` }, `${nOk}/${nH} ok`) : null,
        h("span", { class: "drawer-hint" }, "device")),
      h("div", { class: "drawer-body" },
        h("div", { class: "dev-line tiny" },
          tfield(tel, "boot_id") ? h("span", null, `boot ${String(tfield(tel, "boot_id")).slice(0, 8)}`) : null,
          tfield(tel, "sequence_number") !== null ? h("span", null, `seq #${tfield(tel, "sequence_number")}`) : null,
          tfield(tel, "device_uptime_ms") !== null ? h("span", null, `up ${fmtUptime(tfield(tel, "device_uptime_ms"))}`) : null,
          fw ? h("span", null, `fw ${fw}`) : null,
          num(diag.wifi_rssi_dbm) !== null ? h("span", null, `RSSI ${fmt(diag.wifi_rssi_dbm, 0)} dBm`) : null,
          num(diag.free_heap_bytes) !== null ? h("span", null, `heap ${(diag.free_heap_bytes / 1024).toFixed(0)} kB`) : null),
        hs.some((x) => x !== null) ? h("div", { class: "safety-line", title: "Heatsinks protect the Peltier module." }, icon("shield", { size: 14 }),
          h("span", { class: "safety-lbl" }, "Equipment safety · not fermentation evidence"),
          hs[0] !== null ? h("span", null, `in ${fmt(hs[0], 1)} °C`) : null, hs[1] !== null ? h("span", null, `out ${fmt(hs[1], 1)} °C`) : null) : null,
        nH ? h("div", { class: "health-badges" }, Object.entries(health).map(([k, v]) => h("span", { class: `badge ${v === "ok" ? "status-good" : "status-bad"}` }, `${k.replace(/_/g, " ")}: ${v}`))) : null,
        notFitted.length ? h("div", { class: "tiny faint" }, `not fitted: ${notFitted.join(" · ")}`) : null,
        flags.length ? h("div", { class: "tiny faint" }, `flags: ${flags.join(", ")}`) : null));
  }

  agentBox(c, st) {
    const as = this.agentState[c];
    const hyp = as?.current_hypothesis, dec = as?.last_decision, obs = as?.last_observation;
    const anoms = Array.isArray(as?.open_anomalies) ? as.open_anomalies : [];
    const reasoning = as?.mode?.reasoning_mode_label;
    return h("div", { class: "agent-box", style: { "--c": colorOf(c) } },
      h("div", { class: "agent-head" }, h("span", { class: "who" }, icon("toque", { size: 18, cls: "toque" }), chefLabel(`scientist_${c}`)),
        reasoning ? statusBadge(reasoning, /online/i.test(reasoning) ? "good" : "info") : null),
      dec ? h("div", { class: "agent-dec" }, h("b", null, dec.name || dec.schedule_id || "—"), dec.revised_after_critique ? statusBadge("revised", "warn") : null) : h("div", { class: "faint" }, "no plan yet"),
      obs && !obs.ph_valid ? h("div", { class: "tiny warn-text" }, "no valid pH") : null,
      anoms.length ? h("div", { class: "tiny warn-text" }, `⚠ ${anoms.length} anomal${anoms.length === 1 ? "y" : "ies"}`) : null,
      hyp || anoms.length ? h("details", { class: "why" }, h("summary", null, "why"),
        hyp ? h("p", { class: "small" }, brigadeText(hyp.statement || "")) : null,
        obs ? h("p", { class: "tiny faint" }, `${obs.n_records ?? "?"} records · ${(obs.usable_fields || []).length} usable fields${obs.proofing_status ? ` · proofing ${obs.proofing_status}` : ""}${obs.ph_valid ? "" : ` · no valid pH${obs.ph_reason ? ` (${obs.ph_reason})` : ""}`}`) : null,
        anoms.length ? h("p", { class: "tiny warn-text" }, brigadeText(anoms.map((a) => a.content || a.type).join("; ").slice(0, 300))) : null,
        h("p", { class: "tiny faint" }, `setpoint ${fmt(st.desired_setpoint_c, 1)} °C · ${st.setpoint_source || "—"}`)) : null);
  }

  motorRow(c, m) {
    const active = m && !MOTOR_TERMINAL.has(m.status);
    return h("div", { class: "motor-row" },
      h("button", { class: "btn btn-motor btn-sm", type: "button", disabled: this.busy[c] || !getToken(), onclick: () => this.startMotor(c),
        title: getToken() ? "Run the decorative chef-hat automaton for 5 s (separate from the Peltier controller) — Hat bobs · new assignment" : "Needs the API token" },
        this.busy[c] ? "Sending…" : h("span", { class: "btn-2line" }, h("span", null, "▶ Start automaton animation"), h("span", { class: "btn-sub" }, "Hat bobs · new assignment"))),
      m ? motorTrack(m) : null,
      active ? h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => this.cancelMotor(c) }, "Cancel") : null);
  }

  async startMotor(c) {
    if (!getToken()) { toast("Write access needed: click “Read-only” to enter the API token.", "bad"); return; }
    this.busy[c] = true; this.render(c);
    try {
      this.motor[c] = await post(`/api/v1/chambers/${c}/motor`, { duration_s: 5, pattern: "wiggle", reason: "dashboard: start automaton animation", source: "dashboard" });
    } catch (e) {
      toast(`Station ${c} motor: ${e instanceof ApiError ? e.message : e}`, "bad");
    } finally { this.busy[c] = false; this.render(c); this.renderHats(); }
  }
  async cancelMotor(c) {
    const m = this.motor[c];
    if (!m) return;
    try {
      const r = await del(`/api/v1/commands/${encodeURIComponent(m.command_id)}`);
      this.motor[c] = { ...m, status: r.cancelled ? "cancelled" : r.status };
    } catch (e) { toast(`Cancel failed: ${e.message}`, "bad"); }
    this.render(c); this.renderHats();
  }

  // ------------------------------------------------------------------ manual target temperature (detail view)
  buildSetpoint(c) {
    const box = document.getElementById("lab-setpoint");
    const st = (this.sp[c] ||= { result: null, cmd: null, busy: false, polledAt: 0 });
    const cur = this.state[c]?.desired_setpoint_c;
    const input = h("input", { class: "sp-input", type: "number", min: "4", max: "35", step: "0.5", inputmode: "decimal", "aria-label": "Target temperature in °C",
      value: Number.isFinite(cur) ? String(cur) : "" });
    const btn = h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => this.sendSetpoint(c) }, "Set");
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") this.sendSetpoint(c); });
    st.input = input; st.btn = btn; st.out = h("div", { class: "sp-status tiny" });
    clear(box, h("div", { class: "sp-row" }, h("span", { class: "tile-lbl" }, "Target temperature"), input, h("span", { class: "tiny faint" }, "°C (4–35)"), btn), st.out);
    this.loadSpCmd(c);
  }
  async loadSpCmd(c) {
    const st = this.sp[c]; if (!st) return;
    st.polledAt = Date.now();
    try {
      const rows = await get(`/api/v1/commands?chamber_id=${encodeURIComponent(c)}&action=set_setpoint&limit=5`);
      st.cmd = (Array.isArray(rows) ? rows : []).find((r) => r.action === "set_setpoint" || r.payload?.temperature_setpoint_c !== undefined) || null;
    } catch { /* keep last */ }
    this.updateSetpoint(c);
  }
  async sendSetpoint(c) {
    const st = this.sp[c]; if (!st || st.busy) return;
    const v = Number(st.input.value);
    if (st.input.value === "" || !Number.isFinite(v) || v < 4 || v > 35) { toast("Enter a target between 4 and 35 °C.", "bad"); return; }
    if (!getToken()) { toast("Write access needed: click “Read-only” to enter the API token.", "bad"); return; }
    st.busy = true; this.updateSetpoint(c);
    try {
      st.result = await post(`/api/v1/chambers/${c}/setpoint`, { setpoint_c: v, source: "operator", rationale: "dashboard: manual target temperature" });
      if (st.result.applied_c !== undefined) this.state[c] = { ...(this.state[c] || {}), desired_setpoint_c: st.result.applied_c };
    } catch (e) {
      st.result = { error: e instanceof ApiError ? e.message : String(e), requested_c: v };
    } finally { st.busy = false; }
    this.updateSetpoint(c);
    setTimeout(() => this.loadSpCmd(c), 1500);
  }
  updateSetpoint(c) {
    const st = this.sp[c]; if (!st || !st.out) return;
    if (this.detailId === c && Date.now() - st.polledAt > 5000) this.loadSpCmd(c);   // refresh device ack (throttled)
    st.btn.disabled = st.busy || !getToken();
    st.btn.textContent = st.busy ? "Setting…" : "Set";
    st.btn.title = getToken() ? "" : "Needs the API token";
    const r = st.result, tel = this.state[c]?.latest_telemetry;
    const devSp = num(tel?.temperature_setpoint_c);
    const ack = st.cmd?.acks?.length ? st.cmd.acks[st.cmd.acks.length - 1] : null;
    const ackDetail = ack?.payload?.detail || ack?.payload?.message || null;
    const forced = num(ack?.payload?.result?.applied_setpoint_c ?? ack?.payload?.applied_setpoint_c);
    const bits = [];
    if (r) bits.push(h("span", null, `requested ${fmt(r.requested_c, 1)} °C`, r.applied_c !== undefined && r.applied_c !== r.requested_c ? ` → server ${fmt(r.applied_c, 1)} °C` : ""));
    bits.push(h("span", null, `server ${fmt(this.state[c]?.desired_setpoint_c, 1)} °C`));
    bits.push(h("span", { title: "temperature_setpoint_c from the latest device telemetry" }, `device in force ${devSp !== null ? fmt(devSp, 1) : "—"} °C`));
    if (st.cmd) bits.push(statusBadge(`device ack: ${ack ? ack.status : st.cmd.status}`, (ack?.status || st.cmd.status) === "rejected" ? "warn" : (ack?.status || st.cmd.status) === "accepted" || (ack?.status || st.cmd.status) === "completed" ? "good" : "info"));
    const why = [];
    if (r?.error) why.push(r.error);
    else if (r) {
      if (r.rejected || r.clamped || r.step_limited) why.push(r.reason);
    }
    if (ack?.status === "rejected" && ackDetail) {
      why.push(/disarmed/i.test(ackDetail) ? `Peltier disarmed on the device — arm it on the board to heat/cool${forced !== null ? ` (setpoint ${fmt(forced, 1)} °C stored locally)` : ""}` : ackDetail);
    } else if (!ack && tel && String(tel.peltier_mode || "").toLowerCase() === "disarmed") why.push("Peltier disarmed on the device — arm it on the board to heat/cool");
    clear(st.out, h("div", { class: "sp-line" }, bits), why.length ? h("div", { class: "warn-text" }, why.join(" · ")) : null);
  }

  // ------------------------------------------------------------------ threads (kitchen pass + per-lab)
  thread(filter) {
    const msgs = this.allMsgs.filter((m) => !filter || filter(m)).slice(-6);
    if (!msgs.length) return emptyCard({ icon: "toque", title: "No exchange yet", compact: true });
    return h("div", { class: "pass-convo" }, msgs.map((m) => {
      const sender = m.sender ?? messageSender(m.content);
      const side = agentSide(sender);
      return h("div", { class: `pmsg ${side ? `pmsg-${side}` : "pmsg-head"}` },
        h("div", { class: "pwho" }, icon(side ? "toque" : "headchef", { size: 14 }), chefLabel(sender ?? "system"),
          m.kind ? h("span", { class: "pkind" }, ` · ${m.kind}`) : null, m.created_at_utc ? h("span", { class: "pts" }, ` · ${timeAgo(m.created_at_utc, this.serverNow())}`) : null),
        h("div", { class: "ptext" }, brigadeText(stripArrow(messageText(m.content ?? m)))));
    }));
  }
  renderThreads() {
    if (this.passThreadEl) clear(this.passThreadEl, h("h3", null, icon("toque", { size: 18 }), " Latest A ↔ B"), this.thread(null));
    const lab = document.getElementById("lab-thread");
    if (lab && this.detailId) {
      const c = this.detailId;
      clear(lab, h("h3", null, icon("toque", { size: 18 }), ` Sous Chef ${c} thread`),
        this.thread((m) => { const s = agentSide(m.sender ?? messageSender(m.content)); const r = agentSide(m.recipient ?? m.content?.to); return s === c || r === c || !s; }));
    }
    const plan = document.getElementById("pass-plan-history");
    if (plan) {
      clear(plan, this.experiments.length ? this.experiments.map((e, i) => h("div", { class: `ticket-card${i === 0 ? " latest" : ""}` },
        h("div", { class: "ticket-top" }, h("b", null, e.title || e.experiment_id), provBadge(e.provenance || "unknown")),
        h("div", { class: "ticket-st" }, Object.entries(e.schedules || {}).map(([cid, sc]) => h("span", { class: `st-chip st-${cid}` },
          `${cid} ${normSchedule(sc).points.map(([, t]) => `${fmt(t, 0)}°`).join("→") || "—"}`))),
        h("div", { class: "tiny faint" }, `${e.status || ""} · ${timeAgo(e.created_at_utc, this.serverNow())}`)))
        : emptyCard({ icon: "headchef", title: "No plan yet", compact: true }));
    }
  }
}
