// Section 1: Live Laboratories - chamber A and B side by side, fed by REST snapshots + SSE.
import { get, post, del, getToken, ApiError } from "./api.js";
import { h, clear, fmt, fmtOhms, timeAgo, hhmmss, provBadge, statusBadge, sparkline, agentSide, toast, COLORS, num } from "./util.js";
import { messageText, messageSender } from "./messages.js";

const CHAMBERS = ["A", "B"];
const STALE_AFTER_S = 60; // mirrors configs/hardware.yaml telemetry.stale_after_s; server flag wins on refresh
const HISTORY_MAX = 360;

const MOTOR_TERMINAL = new Set(["completed", "failed", "cancelled", "rejected", "expired", "expired_unacked", "superseded"]);

export function motorTrack(cmd) {
  // issued -> acked (device "accepted") -> completed; terminal failures shown in red.
  const s = (cmd && cmd.status) || "none";
  const step = (label, cls) => h("span", { class: `stp ${cls}` }, label);
  const arrow = () => h("span", { class: "arrow" }, "→");
  const bad = MOTOR_TERMINAL.has(s) && s !== "completed";
  const wasAcked = cmd && (!!cmd.acked || ["accepted", "completed", "failed"].includes(s));
  const issued = s === "issued" ? "now" : s === "none" ? "" : "done";
  const acked = s === "accepted" ? "now" : wasAcked ? "done" : bad ? "bad" : "";
  const last = bad ? step(s.replace(/_/g, " "), "bad") : step("completed", s === "completed" ? "done" : "");
  return h("span", { class: "cmd-track", title: cmd?.command_id ? `command ${cmd.command_id}` : "" }, step("issued", issued), arrow(), step("acked", acked), arrow(), last);
}

export class LivePanel {
  constructor(store, stream) {
    this.store = store;
    this.stream = stream;
    this.state = {};       // chamber -> /state payload (kept fresh by SSE)
    this.history = {};     // chamber -> [{t, temp, sp}]
    this.agentMsg = {};    // chamber -> latest message row from that agent
    this.motor = {};       // chamber -> latest motor command {command_id, status, ...}
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
    });
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
      this.render(c);
    }));
  }

  histPoint(r) {
    const t = Date.parse(r.timestamp_utc || r.received_at_utc);
    if (!Number.isFinite(t)) return null;
    return { t, temp: num(r.chamber_temperature_c), sp: num(r.temperature_setpoint_c), starter: num(r.starter_temperature_c) };
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

  render(c) {
    const card = this.cards[c];
    const st = this.state[c];
    if (!card || !st) return;
    const tel = st.latest_telemetry || null;
    const stale = st.telemetry_stale === true && !tel ? true : this.isStale(st);
    const prov = tel ? tel.source : null;

    const head = h("div", { class: "ch-head" },
      h("div", null,
        h("div", { class: "ch-title" }, "Chamber ", h("span", { class: "letter" }, c)),
        h("div", { class: "ch-sub" }, (st.label || "").replace(/^Chamber [AB]\s*[-–]\s*/, ""), st.device_id ? ` · ${st.device_id}` : "")),
      h("div", { class: "ch-badges" },
        prov ? provBadge(prov) : statusBadge("no data", "info"),
        stale ? statusBadge("stale", "bad") : statusBadge("live", "good"),
        st.experiment_id ? h("span", { class: "badge status-info", title: "current experiment" }, st.experiment_id) : null));

    const staleBanner = stale
      ? h("div", { class: "stale-banner" }, tel
        ? `⚠ Telemetry stale — last reading ${timeAgo(tel.received_at_utc, this.serverNow())}. Values below are the last known, not live.`
        : "⚠ No telemetry received from this chamber yet. Start the ESP32 (or scripts/simulate_device.py).")
      : null;

    const hist = this.history[c];
    const spark = sparkline([
      { values: hist.map((p) => ({ t: p.t, v: p.sp })), color: COLORS.muted, dash: true },
      { values: hist.map((p) => ({ t: p.t, v: p.temp })), color: c === "A" ? COLORS.A : COLORS.B },
    ]);
    const span = hist.length > 1 ? Math.round((hist[hist.length - 1].t - hist[0].t) / 60000) : 0;
    const hero = h("div", { class: "hero-temp" },
      h("div", { class: "lbl small muted" }, "Chamber temperature"),
      h("div", { class: "big" }, fmt(tel?.chamber_temperature_c, 1), h("span", { class: "unit" }, " °C")),
      h("div", { class: "sp" }, "Setpoint ", h("b", null, fmt(st.desired_setpoint_c, 1, " °C")),
        st.device_reported_setpoint_c !== null && st.device_reported_setpoint_c !== undefined ? ` · device using ${fmt(st.device_reported_setpoint_c, 1, " °C")}` : ""),
      h("div", { class: "sp small" }, "Starter ", h("b", null, fmt(tel?.starter_temperature_c, 1, " °C"))),
      spark,
      h("div", { class: "spark-legend" },
        h("span", null, h("i", { style: { background: c === "A" ? COLORS.A : COLORS.B } }), "temperature"),
        h("span", null, h("i", { style: { background: COLORS.muted } }), "setpoint (device)"),
        h("span", null, span ? `last ${span} min` : "")));

    const img = st.latest_image;
    let snap = card.querySelector(".snap");
    const imgKey = img ? img.url : "none";
    if (!snap || snap.dataset.key !== imgKey) {
      snap = h("div", { class: "snap", dataset: { key: imgKey } },
        img ? h("img", { src: img.url, alt: `Chamber ${c} camera snapshot`, loading: "lazy" }) : h("div", { class: "snap-empty" }, "No camera snapshot yet"),
        img ? h("div", { class: "snap-cap" }, h("span", null, `📷 ${hhmmss(img.timestamp_utc)}`), provBadge(img.source)) : null);
    } else {
      const cap = snap.querySelector(".snap-cap span");
      if (cap && img) cap.textContent = `📷 ${hhmmss(img.timestamp_utc)}`;
    }

    const metric = (lbl, val, cls = "") => h("div", { class: `metric ${cls}` }, h("div", { class: "lbl" }, lbl), val);
    const v = (x, digits, unit) => (num(x) === null ? h("div", { class: "val na" }, "—") : h("div", { class: "val" }, fmt(x, digits, unit)));
    const peltier = tel?.peltier_mode ?? null;
    const metrics = h("div", { class: "metrics" },
      metric("Peltier mode (device)", h("div", { class: peltier ? "val" : "val na" }, peltier || "—", h("span", { class: "tiny faint" }, st.peltier_mode_hint ? ` hint: ${st.peltier_mode_hint}` : ""))),
      metric("Starter height", v(tel?.starter_height_mm, 1, " mm")),
      metric("Humidity", v(tel?.relative_humidity_percent, 0, " %RH")),
      metric("BME680 gas resistance (Ω) — not CO₂", num(tel?.gas_resistance_ohms) === null ? h("div", { class: "val na" }, "—") : h("div", { class: "val" }, fmtOhms(tel.gas_resistance_ohms))),
      metric("pH", num(tel?.ph) === null ? h("div", { class: "val na" }, "no pH observation") : h("div", { class: "val" }, fmt(tel.ph, 2), h("span", { class: "tiny faint" }, tel.ph_method ? ` ${tel.ph_method}` : ""))),
      metric("Door", tel?.door_open === true ? h("div", { class: "val", style: { color: "#ffb0b0" } }, "OPEN") : tel?.door_open === false ? h("div", { class: "val" }, "closed") : h("div", { class: "val na" }, "unknown")));

    const flags = (tel?.quality_flags || []).filter((f) => f);
    const flagsEl = flags.length ? h("div", { class: "tiny faint" }, "Quality flags: ", flags.join(", ")) : null;

    // Agent status + latest decision
    const am = this.agentMsg[c];
    const changed = st.setpoint_updated_at_utc ? timeAgo(st.setpoint_updated_at_utc, this.serverNow()) : "";
    const agent = h("div", { class: "agent-box" },
      h("div", { class: "agent-head" },
        h("span", null, `Scientist ${c}`),
        h("span", { class: "tiny faint" }, am ? `last message ${timeAgo(am.created_at_utc, this.serverNow())}` : "idle — waiting for taste feedback")),
      h("div", { class: "agent-text" }, am ? [am.kind ? h("b", null, `${am.kind}: `) : "", messageText(am.content ?? am).replace(/^\s*\[[^\]]{1,60}(→|->)[^\]]{1,60}\]\s*/, "")] : "No agent messages yet."),
      h("div", { class: "tiny faint", style: { marginTop: "4px" } },
        `Latest decision: setpoint ${fmt(st.desired_setpoint_c, 1, " °C")} by ${st.setpoint_source || "—"}${changed ? ` (${changed})` : ""}`,
        st.setpoint_rationale ? ` — ${String(st.setpoint_rationale).slice(0, 160)}` : ""));

    // Motor
    const m = this.motor[c];
    const active = m && !MOTOR_TERMINAL.has(m.status);
    const motor = h("div", { class: "motor-row" },
      h("button", { class: "btn btn-motor", type: "button", disabled: this.busy[c] || !getToken(), onclick: () => this.startMotor(c),
        title: getToken() ? "Run the decorative chef-hat automaton for 5 s (separate from the Peltier controller)" : "Needs the API token" },
        this.busy[c] ? "Sending…" : "▶ Start automaton animation"),
      m ? motorTrack(m) : h("span", { class: "tiny faint" }, "no motor command yet"),
      active ? h("button", { class: "btn btn-danger btn-sm", type: "button", onclick: () => this.cancelMotor(c) }, "Cancel") : null);

    const mainRow = h("div", { class: "ch-main" }, hero, snap);
    clear(card, head, staleBanner, mainRow, metrics, flagsEl, agent, motor,
      h("div", { class: "tiny faint" }, tel ? `Last telemetry ${timeAgo(tel.received_at_utc, this.serverNow())} (device time ${hhmmss(tel.timestamp_utc)})` : ""));
  }
}
