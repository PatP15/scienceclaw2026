// Section 1: Live Laboratories - chamber A and B side by side, fed by REST snapshots + SSE.
import { get, post, del, getToken, ApiError } from "./api.js";
import { h, clear, fmt, fmtOhms, timeAgo, hhmmss, statusBadge, sparkline, agentSide, toast, COLORS, num, normProv, tfield, demoMode, deviceBadge, PROV_LABEL } from "./util.js";
import { messageText, messageSender } from "./messages.js";

const CHAMBERS = ["A", "B"];
const STALE_AFTER_S = 60; // mirrors configs/hardware.yaml telemetry.stale_after_s; server flag wins on refresh
const HISTORY_MAX = 360;

const MOTOR_TERMINAL = new Set(["completed", "failed", "cancelled", "rejected", "expired", "expired_unacked", "superseded"]);

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

const SENSOR_FOR = {
  chamber_temperature_c: ["sht31", "bme680"], starter_temperature_c: ["ds18b20_starter"],
  relative_humidity_percent: ["sht31", "bme680"], pressure_pa: ["bme680"], gas_resistance_ohms: ["bme680"],
  ph: ["ph"], starter_height_mm: ["camera"], door_open: ["door", "door_switch", "reed_switch"],
  internal_heatsink_temperature_c: ["ds18b20_heatsink_internal", "internal_heatsink"],
  external_heatsink_temperature_c: ["ds18b20_heatsink_external", "external_heatsink"],
};
const PROV_SHORT = { measured: "measured", simulated_device: "sim device", modeled: "modeled", computed: "computed", replay: "replay", estimated: "est", manual: "manual" };

export class LivePanel {
  constructor(store, stream) {
    this.store = store;
    this.stream = stream;
    this.state = {};       // chamber -> /state payload (kept fresh by SSE)
    this.history = {};     // chamber -> [{t, temp, sp}]
    this.agentMsg = {};    // chamber -> latest message row from that agent
    this.motor = {};       // chamber -> latest motor command {command_id, status, ...}
    this.agentState = {};  // chamber -> GET /agents/{id}/state
    this.busy = {};
    this.cards = Object.fromEntries(CHAMBERS.map((c) => [c, document.getElementById(`chamber-${c}`)]));
    for (const c of CHAMBERS) this.history[c] = [];
    this.wire();
  }

  wire() {
    const s = this.stream;
    s.on("telemetry", (f) => this.onTelemetry(f));
    s.on("image", (f) => {
      const c = f.chamber_id || f.data?.chamber_id;
      if (!this.state[c]) return;
      this.state[c].latest_image = { id: f.data.image_id, url: f.data.url, timestamp_utc: f.data.timestamp_utc, source: f.data.source };
      this.render(c);
    });
    s.on("setpoint", (f) => { if (f.chamber_id) this.refreshState(f.chamber_id); });
    s.on("experiment", () => CHAMBERS.forEach((c) => this.refreshState(c)));
    s.on("command", (f) => this.onCommand(f.data, f.chamber_id));
    s.on("ack", (f) => this.onCommand({ ...f.data, status: f.data.command_status || f.data.status }, f.chamber_id));
    s.on("message", (f) => {
      const side = agentSide(f.data.sender ?? messageSender(f.data.content));
      if (side) { this.agentMsg[side] = f.data; this.render(side); }
      clearTimeout(this._agentTimer);
      this._agentTimer = setTimeout(() => CHAMBERS.forEach((c) => this.refreshAgent(c)), 1500);
    });
    setInterval(() => CHAMBERS.forEach((c) => this.refreshAgent(c)), 30000);
    setInterval(() => CHAMBERS.forEach((c) => this.render(c)), 5000);         // ages / staleness
    setInterval(() => CHAMBERS.forEach((c) => this.refreshState(c)), 20000);  // server truth
  }

  async load() {
    await Promise.all(CHAMBERS.map(async (c) => {
      await this.refreshState(c);
      try {
        const rows = await get(`/api/v1/chambers/${c}/telemetry?limit=${HISTORY_MAX}`);
        this.history[c] = rows.map((r) => this.histPoint(r)).filter(Boolean);
      } catch (e) { /* history optional */ }
      try {
        // "all" + client-side filtering: Finding-shaped messages carry agent_id, which the API's
        // per-agent filter (sender_key from from/sender/agent/author) does not index.
        const msgs = await get(`/api/v1/agents/all/messages?limit=60`);
        const own = msgs.filter((m) => agentSide(m.sender ?? messageSender(m.content)) === c);
        if (own.length) this.agentMsg[c] = own[own.length - 1];
      } catch (e) { /* none */ }
      await this.refreshAgent(c, false);
      this.render(c);
    }));
  }

  async refreshAgent(c, rerender = true) {
    try { this.agentState[c] = await get(`/api/v1/agents/${c}/state`); } catch { /* keep last */ }
    if (rerender) this.render(c);
  }

  histPoint(r) {
    const t = Date.parse(r.timestamp_utc || r.received_at_utc); // unsynced device clock -> server receipt time
    if (!Number.isFinite(t)) return null;
    return { t, temp: num(r.chamber_temperature_c), sp: num(r.temperature_setpoint_c), starter: num(r.starter_temperature_c), height: num(r.starter_height_mm) };
  }

  async refreshState(c) {
    try {
      const st = await get(`/api/v1/chambers/${c}/state`);
      this.state[c] = st;
      this.store.serverOffsetMs = Date.parse(st.server_time_utc) - Date.now();
      if (st.last_motor_command) this.motor[c] = { ...(this.motor[c] || {}), ...st.last_motor_command }; // server is truth
      this.render(c);
    } catch (e) {
      if (!this.state[c]) clear(this.cards[c], h("div", { class: "empty" }, `Chamber ${c}: cannot reach the local API (${e.message}).`));
    }
  }

  onTelemetry(f) {
    const c = f.chamber_id || f.data?.chamber_id;
    if (!CHAMBERS.includes(c)) return;
    const st = this.state[c] || (this.state[c] = { chamber_id: c });
    st.latest_telemetry = f.data;
    st.provenance = f.data.source;
    st.telemetry_stale = false;
    st.device_reported_setpoint_c = f.data.temperature_setpoint_c;
    const p = this.histPoint(f.data);
    if (p) { this.history[c].push(p); if (this.history[c].length > HISTORY_MAX) this.history[c].shift(); }
    this.render(c);
    document.dispatchEvent(new CustomEvent("fermentai:telemetry", { detail: { chamber: c, row: f.data } }));
  }

  onCommand(d, chamber) {
    if (!d || (d.command_type && d.command_type !== "motor")) return;
    const c = chamber || d.chamber_id;
    if (!CHAMBERS.includes(c)) return;
    const cur = this.motor[c];
    if (cur && cur.command_id !== d.command_id && d.status !== "issued") return; // ack for an older command
    this.motor[c] = { ...(cur && cur.command_id === d.command_id ? cur : {}), ...d };
    this.render(c);
  }

  serverNow() { return Date.now() + (this.store.serverOffsetMs || 0); }

  isStale(st) {
    const tel = st.latest_telemetry;
    if (!tel) return true;
    const age = (this.serverNow() - Date.parse(tel.received_at_utc || tel.timestamp_utc)) / 1000;
    return !(age <= STALE_AFTER_S);
  }

  async startMotor(c) {
    if (!getToken()) { toast("Write access needed: open the ?token=… URL or tap “Read-only” to enter the token.", "bad"); return; }
    this.busy[c] = true; this.render(c);
    try {
      const cmd = await post(`/api/v1/chambers/${c}/motor`, { duration_s: 5, pattern: "wiggle", reason: "dashboard: start automaton animation", source: "dashboard" });
      this.motor[c] = cmd;
      toast(`Chamber ${c}: automaton animation issued (5 s wiggle).`, "good");
    } catch (e) {
      toast(`Chamber ${c} motor: ${e instanceof ApiError ? e.message : e}`, "bad");
    } finally { this.busy[c] = false; this.render(c); }
  }

  async cancelMotor(c) {
    const m = this.motor[c];
    if (!m) return;
    try {
      const r = await del(`/api/v1/commands/${encodeURIComponent(m.command_id)}`);
      this.motor[c] = { ...m, status: r.cancelled ? "cancelled" : r.status };
      toast(r.cancelled ? `Chamber ${c}: motor command cancelled.` : `Chamber ${c}: ${r.reason || "not cancellable"}`, r.cancelled ? "good" : "");
    } catch (e) { toast(`Cancel failed: ${e.message}`, "bad"); }
    this.render(c);
  }

  sensorState(tel, field) {
    const health = tfield(tel, "diagnostics")?.sensor_health || {};
    for (const k of [field, ...(SENSOR_FOR[field] || [])]) if (health[k]) return String(health[k]);
    return null;
  }
  provMini(tel, field) {
    const fp = (tel && (tel.field_provenance || tel.payload?.field_provenance)) || {};
    const raw = fp[field] || (tel ? tel.source : null);
    if (!raw) return null;
    const n = normProv(raw, "device");
    return h("span", { class: `badge mini prov-${n}`, title: `provenance of this value: ${raw} → ${PROV_LABEL[n] || n}${fp[field] ? " (field_provenance)" : " (record source)"}` }, PROV_SHORT[n] || raw);
  }
  /** A value cell: real number, or an explicit "unavailable" state - never an invented value. */
  val(tel, field, format) {
    const x = tfield(tel, field);
    if (x === null || x === undefined || (typeof x === "number" && !Number.isFinite(x))) {
      const sh = this.sensorState(tel, field);
      return h("div", { class: "val na" }, "unavailable", sh && sh !== "ok" ? h("span", { class: "tiny" }, ` (sensor ${sh})`) : "");
    }
    return h("div", { class: "val" }, format(x));
  }

  render(c) {
    const card = this.cards[c];
    const st = this.state[c];
    if (!card || !st) return;
    const tel = st.latest_telemetry || null;
    const stale = st.telemetry_stale === true && !tel ? true : this.isStale(st);
    const prov = tel ? tel.source : null;
    const mode = demoMode(prov);
    document.dispatchEvent(new CustomEvent("fermentai:source", { detail: { chamber: c, source: prov, mode, stale } }));
    const diag = tfield(tel, "diagnostics") || {};
    const unsynced = tel && (tel.timestamp_utc === null || tel.timestamp_utc === undefined || tfield(tel, "timestamp_quality") === "unsynced");

    const modeBadge = mode ? h("span", { class: `badge prov-${mode === "live" ? "measured" : mode === "replay" ? "replay" : "simulated_device"}`, title: "demonstration mode from this chamber's record source" },
      mode === "live" ? "LIVE" : mode === "replay" ? "REPLAY" : "SIMULATION") : null;
    const head = h("div", { class: "ch-head" },
      h("div", null,
        h("div", { class: "ch-title" }, "Chamber ", h("span", { class: "letter" }, c)),
        h("div", { class: "ch-sub" }, (st.label || "").replace(/^Chamber [AB]\s*[-–]\s*/, ""))),
      h("div", { class: "ch-badges" },
        modeBadge, prov ? deviceBadge(prov) : statusBadge("no data", "info"),
        stale ? statusBadge("stale", "bad") : statusBadge("fresh", "good"),
        st.experiment_id ? h("span", { class: "badge status-info", title: "current experiment" }, st.experiment_id) : null));

    const devId = tfield(tel, "device_id") || st.device_id;
    const boot = tfield(tel, "boot_id"), seq = tfield(tel, "sequence_number"), up = tfield(tel, "device_uptime_ms");
    const devLine = h("div", { class: "dev-line tiny" },
      h("span", null, "📟 ", h("b", null, devId || "no device")),
      boot ? h("span", { title: `boot_id ${boot}` }, `boot ${String(boot).slice(0, 8)}`) : null,
      seq !== null ? h("span", null, `seq #${seq}`) : null,
      up !== null ? h("span", null, `up ${fmtUptime(up)}`) : null,
      diag.firmware_version || tfield(tel, "firmware_version") ? h("span", null, `fw ${diag.firmware_version || tfield(tel, "firmware_version")}`) : null,
      unsynced ? statusBadge("device clock unsynced", "warn") : tel ? statusBadge("device clock synced", "good") : null);

    const staleBanner = stale
      ? h("div", { class: "stale-banner" }, tel
        ? `⚠ Telemetry stale — last reading received ${timeAgo(tel.received_at_utc, this.serverNow())}. Values below are the last known, not live.`
        : "⚠ No telemetry received from this chamber yet. Start the ESP32 (or scripts/start_demo.sh --with-simulated-devices).")
      : null;

    const hist = this.history[c];
    const spark = sparkline([
      { values: hist.map((p) => ({ t: p.t, v: p.sp })), color: COLORS.muted, dash: true },
      { values: hist.map((p) => ({ t: p.t, v: p.temp })), color: c === "A" ? COLORS.A : COLORS.B },
    ]);
    const span = hist.length > 1 ? Math.round((hist[hist.length - 1].t - hist[0].t) / 60000) : 0;
    const tempNow = tfield(tel, "chamber_temperature_c");
    const hero = h("div", { class: "hero-temp" },
      h("div", { class: "lbl small muted" }, "Chamber air temperature ", this.provMini(tel, "chamber_temperature_c")),
      num(tempNow) === null ? h("div", { class: "big na" }, "unavailable") : h("div", { class: "big" }, fmt(tempNow, 1), h("span", { class: "unit" }, " °C")),
      h("div", { class: "sp" }, "Setpoint ", h("b", null, fmt(st.desired_setpoint_c, 1, " °C")),
        num(st.device_reported_setpoint_c) !== null ? ` · device using ${fmt(st.device_reported_setpoint_c, 1, " °C")}` : ""),
      h("div", { class: "sp small" }, "Starter ", num(tfield(tel, "starter_temperature_c")) === null ? h("b", { class: "faint" }, "unavailable") : h("b", null, fmt(tfield(tel, "starter_temperature_c"), 1, " °C")), " ", this.provMini(tel, "starter_temperature_c")),
      spark,
      h("div", { class: "spark-legend" },
        h("span", null, h("i", { style: { background: c === "A" ? COLORS.A : COLORS.B } }), "air temperature"),
        h("span", null, h("i", { style: { background: COLORS.muted } }), "setpoint (device)"),
        h("span", null, span ? `last ${span} min${unsynced ? " (server receipt time)" : ""}` : "")));

    const img = st.latest_image;
    let snap = card.querySelector(".snap");
    const imgKey = img ? img.url : "none";
    if (!snap || snap.dataset.key !== imgKey) {
      snap = h("div", { class: "snap", dataset: { key: imgKey } },
        img ? h("img", { src: img.url, alt: `Chamber ${c} camera snapshot`, loading: "lazy" }) : h("div", { class: "snap-empty" }, "No camera snapshot yet"),
        img ? h("div", { class: "snap-cap" }, h("span", null, `📷 ${hhmmss(img.timestamp_utc)}`), deviceBadge(img.source)) : null);
    } else {
      const cap = snap.querySelector(".snap-cap span");
      if (cap && img) cap.textContent = `📷 ${hhmmss(img.timestamp_utc)}`;
    }

    // Starter rise estimate: camera-derived height vs the first height in the loaded history.
    const heights = hist.filter((p) => p.height !== null && p.height !== undefined);
    const hNow = num(tfield(tel, "starter_height_mm"));
    const rise = heights.length && hNow !== null && heights[0].height > 0 ? hNow / heights[0].height : null;

    const metric = (lbl, field, val, cls = "") => h("div", { class: `metric ${cls}` }, h("div", { class: "lbl" }, lbl, " ", field ? this.provMini(tel, field) : null), val);
    const peltier = tfield(tel, "peltier_mode");
    const frac = num(tfield(tel, "peltier_output_fraction"));
    const doorOpen = tfield(tel, "door_open");
    const motorRunning = tfield(tel, "motor_running");
    const metrics = h("div", { class: "metrics" },
      metric("Peltier state (device)", "peltier_mode", h("div", { class: peltier ? "val" : "val na" }, peltier || "unavailable",
        h("span", { class: "tiny faint" }, frac !== null ? ` ${Math.round(frac * 100)} % output` : "", st.peltier_mode_hint ? ` · hint ${st.peltier_mode_hint}` : ""))),
      metric("Starter height (camera)", "starter_height_mm", this.val(tel, "starter_height_mm", (x) => [fmt(x, 1, " mm"), rise !== null ? h("span", { class: "tiny faint" }, ` rise ×${rise.toFixed(2)} est.`) : ""])),
      metric("Humidity", "relative_humidity_percent", this.val(tel, "relative_humidity_percent", (x) => fmt(x, 0, " %RH"))),
      metric("BME680 gas resistance (Ω) — not CO₂", "gas_resistance_ohms", this.val(tel, "gas_resistance_ohms", fmtOhms)),
      metric("pH", "ph", num(tfield(tel, "ph")) === null
        ? h("div", { class: "val na" }, "no pH observation", this.sensorState(tel, "ph") && this.sensorState(tel, "ph") !== "ok" ? h("span", { class: "tiny" }, ` (probe ${this.sensorState(tel, "ph")})`) : "")
        : h("div", { class: "val" }, fmt(tfield(tel, "ph"), 2), h("span", { class: "tiny faint" }, tfield(tel, "ph_method") ? ` ${tfield(tel, "ph_method")}` : ""))),
      metric("Pressure", "pressure_pa", this.val(tel, "pressure_pa", (x) => fmt(x / 100, 1, " hPa"))),
      metric("Door", "door_open", doorOpen === true ? h("div", { class: "val", style: { color: "#ffb0b0" } }, "OPEN") : doorOpen === false ? h("div", { class: "val" }, "closed") : this.val(tel, "door_open", String)),
      metric("Chef-hat motor", "motor_running", h("div", { class: motorRunning === null ? "val na" : "val" }, motorRunning === true ? "running" : motorRunning === false ? "idle" : "unavailable",
        h("span", { class: "tiny faint" }, st.motor_ran ? " · last cmd ran" : ""))),
      metric("Last valid update", null, h("div", { class: tel ? "val small-val" : "val na" }, tel ? timeAgo(tel.received_at_utc, this.serverNow()) : "never",
        h("span", { class: "tiny faint" }, tel ? (unsynced ? " (server receipt)" : ` (device ${hhmmss(tel.timestamp_utc)})`) : ""))));

    // Equipment safety (heatsinks): shown separately - never fermentation evidence.
    const safety = h("div", { class: "safety-strip" },
      h("div", { class: "tiny faint" }, "Equipment safety — not fermentation evidence"),
      h("div", { class: "safety-vals" },
        h("span", null, "Heatsink internal ", this.provMini(tel, "internal_heatsink_temperature_c"), " ", this.inline(tel, "internal_heatsink_temperature_c", (x) => fmt(x, 1, " °C"))),
        h("span", null, "external ", this.provMini(tel, "external_heatsink_temperature_c"), " ", this.inline(tel, "external_heatsink_temperature_c", (x) => fmt(x, 1, " °C"))),
        tfield(tel, "fan_running") !== null ? h("span", null, `fan ${tfield(tel, "fan_running") ? "on" : "off"}`) : null));

    // Diagnostics
    const health = diag.sensor_health || {};
    const hb = (k, v) => h("span", { class: `badge ${v === "ok" ? "status-good" : /missing|absent|not_installed/.test(v) ? "status-info" : "status-bad"}`, title: `sensor_health.${k} = ${v}` }, `${k.replace(/_/g, " ")}: ${v}`);
    const rssi = num(diag.wifi_rssi_dbm ?? tfield(tel, "wifi_rssi_dbm"));
    const flags = (tel?.quality_flags || []).filter((f) => f);
    const diagEl = h("details", { class: "diag" },
      h("summary", null, "Diagnostics\u00a0\u00a0", h("span", { class: "tiny faint" },
        rssi !== null ? `RSSI ${rssi.toFixed(0)} dBm · ` : "", num(diag.free_heap_bytes) !== null ? `heap ${(diag.free_heap_bytes / 1024).toFixed(0)} kB · ` : "",
        `${Object.values(health).filter((v) => v === "ok").length}/${Object.keys(health).length} sensors ok`)),
      h("div", { class: "health-badges" }, Object.entries(health).map(([k, v]) => hb(k, String(v)))),
      flags.length ? h("div", { class: "tiny faint" }, "Quality flags: ", flags.join(", ")) : null,
      tfield(tel, "time_basis") ? h("div", { class: "tiny faint" }, `time basis: ${tfield(tel, "time_basis")}`) : null);

    const agent = this.agentBox(c, st);

    // Pending device commands (v1.1 commands[] not yet acked)
    const pend = Array.isArray(st.pending_commands) ? st.pending_commands : [];
    const pendEl = pend.length ? h("div", { class: "tiny faint" }, `Pending device commands: ${pend.map((p) => p.action || p.command_type).join(", ")}`) : null;

    const m = this.motor[c];
    const active = m && !MOTOR_TERMINAL.has(m.status);
    const motor = h("div", { class: "motor-row" },
      h("button", { class: "btn btn-motor", type: "button", disabled: this.busy[c] || !getToken(), onclick: () => this.startMotor(c),
        title: getToken() ? "Run the decorative chef-hat automaton for 5 s (separate from the Peltier controller)" : "Needs the API token" },
        this.busy[c] ? "Sending…" : "▶ Start automaton animation"),
      m ? motorTrack(m) : h("span", { class: "tiny faint" }, "no motor command yet"),
      active ? h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => this.cancelMotor(c) }, "Cancel") : null);

    const mainRow = h("div", { class: "ch-main" }, hero, snap);
    clear(card, head, devLine, staleBanner, mainRow, metrics, safety, diagEl, agent, pendEl, motor);
  }

  inline(tel, field, format) {
    const x = num(tfield(tel, field));
    if (x === null) { const sh = this.sensorState(tel, field); return h("b", { class: "faint" }, `unavailable${sh && sh !== "ok" ? ` (${sh})` : ""}`); }
    return h("b", null, format(x));
  }

  agentBox(c, st) {
    const as = this.agentState[c];
    const am = this.agentMsg[c];
    const changed = st.setpoint_updated_at_utc ? timeAgo(st.setpoint_updated_at_utc, this.serverNow()) : "";
    const setpointLine = h("div", { class: "tiny faint", style: { marginTop: "4px" } },
      `Chamber setpoint ${fmt(st.desired_setpoint_c, 1, " °C")} set by ${st.setpoint_source || "—"}${changed ? ` (${changed})` : ""}`,
      st.setpoint_rationale ? ` — ${String(st.setpoint_rationale).slice(0, 120)}` : "");
    if (!as || as.available === false || as.stub) {
      return h("div", { class: "agent-box" },
        h("div", { class: "agent-head" }, h("span", null, `Scientist ${c}`), h("span", { class: "tiny faint" }, am ? `last message ${timeAgo(am.created_at_utc, this.serverNow())}` : "idle — waiting for taste feedback")),
        as?.objective ? h("div", { class: "small" }, h("b", null, "Objective: "), as.objective) : null,
        h("div", { class: "agent-text" }, am ? [am.kind ? h("b", null, `${am.kind}: `) : "", stripArrow(messageText(am.content ?? am))] : "No findings yet — the agent publishes its hypothesis and decisions after taste feedback."),
        setpointLine);
    }
    const hyp = as.current_hypothesis;
    const dec = as.last_decision;
    const obs = as.last_observation;
    const anoms = Array.isArray(as.open_anomalies) ? as.open_anomalies : [];
    const reasoning = as.mode?.reasoning_mode_label;
    let phText = "pH: no observation summary yet";
    if (obs) phText = obs.ph_valid ? "pH: valid observation" : `no valid pH${obs.ph_reason ? ` — ${obs.ph_reason}` : ""}`;
    return h("div", { class: "agent-box" },
      h("div", { class: "agent-head" },
        h("span", null, `Scientist ${c} `, reasoning ? statusBadge(reasoning, /online/i.test(reasoning) ? "good" : "info") : null),
        h("span", { class: "tiny faint" }, as.last_activity_utc ? `active ${timeAgo(as.last_activity_utc, this.serverNow())}` : "no activity yet")),
      hyp ? h("div", { class: "agent-text" }, h("b", null, "Hypothesis: "), hyp.statement || "—") : h("div", { class: "agent-text faint" }, "No hypothesis yet — waiting for taste feedback."),
      dec ? h("div", { class: "small", style: { marginTop: "3px" } }, h("b", null, "Last decision: "), dec.name || dec.schedule_id || "—",
        Array.isArray(dec.points) ? h("span", { class: "faint" }, ` (${dec.points.map((p) => `${fmt(p[1], 0)}°C@${fmt(p[0], 0)}h`).join("→")})`) : "",
        dec.revised_after_critique ? statusBadge("revised after critique", "warn") : null) : null,
      h("div", { class: "tiny", style: { marginTop: "3px" } },
        h("b", null, "Observations: "),
        obs ? `${obs.n_records ?? "?"} records${obs.latest_stale ? " (latest stale)" : ""} · ${(obs.usable_fields || []).length} usable fields${obs.proofing_status ? ` · proofing ${obs.proofing_status}` : ""} · ` : "no quality summary yet · ",
        h("span", { class: obs && obs.ph_valid ? "" : "warn-text" }, phText)),
      anoms.length ? h("div", { class: "tiny warn-text" }, `⚠ ${anoms.length} open anomal${anoms.length === 1 ? "y" : "ies"}: `, anoms.map((a) => a.content || a.type).join("; ").slice(0, 200)) : h("div", { class: "tiny faint" }, "No open anomalies."),
      setpointLine);
  }
}

function stripArrow(t) { return String(t || "").replace(/^\s*\[[^\]]{1,60}(→|->)[^\]]{1,60}\]\s*/, ""); }
function fmtUptime(ms) {
  const s = Math.floor(Number(ms) / 1000);
  if (!Number.isFinite(s)) return "?";
  if (s < 120) return `${s}s`;
  if (s < 7200) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
