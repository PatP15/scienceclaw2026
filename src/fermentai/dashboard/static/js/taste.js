// Section 4: "What did the bread taste like?" -> interpretation -> hypothesis -> agents -> next experiment.
import { get, post, getToken, ApiError } from "./api.js";
import { h, clear, fmt, num, provBadge, statusBadge, plotLayout, PLOT_CONFIG, whenPlotly, COLORS, agentSide, normSchedule, renderValue, toast, sleep, timeAgo, hhmmss } from "./util.js";
import { messageText, messageSender, messageRecipient, messageKind, messageBody } from "./messages.js";
import { motorTrack } from "./live.js";
import { REPLAY_EXAMPLES } from "./examples.js";

const STEP_DEFS = [
  { id: "saved", title: "Feedback saved" },
  { id: "interp", title: "Structured sensory interpretation", wide: true },
  { id: "compare", title: "Comparison with recorded experiments" },
  { id: "hyp", title: "Hypothesis" },
  { id: "sched", title: "Recommended temperature schedules A / B" },
  { id: "lead", title: "Agent message" },
  { id: "peer", title: "Second agent: critique / counter-proposal" },
  { id: "next", title: "Proposed next experiment" },
  { id: "motor", title: "Automaton motor commands (both chambers)" },
  { id: "plan", title: "Updated experiment plan" },
];
const DIM_LABEL = { sourness: "Sourness", texture: "Crumb openness", density: "Density", aroma: "Aroma", flavor_intensity: "Flavour intensity" };
const STEP_DELAY_MS = 650;

export class TastePanel {
  constructor(store, stream, live) {
    this.store = store; this.stream = stream; this.live = live;
    this.form = document.getElementById("taste-form");
    this.text = document.getElementById("taste-text");
    this.stepsEl = document.getElementById("taste-steps");
    this.planBody = document.getElementById("plan-body");
    this.submitBtn = document.getElementById("taste-submit");
    this.hint = document.getElementById("taste-hint");
    this.mode = "offline";
    this.replay = null;
    this.pending = false;
    this.current = null;        // response currently displayed
    this.motorCmds = {};        // command_id -> cmd
    this.newExperimentId = null;
    this.beforeSetpoints = null;
    this.buildForm();
    this.buildSteps();
    this.wire();
  }

  // ------------------------------------------------------------------ form
  buildForm() {
    document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => this.setMode(b.dataset.mode)));
    const sel = document.getElementById("replay-select");
    REPLAY_EXAMPLES.forEach((ex, i) => sel.append(h("option", { value: String(i) }, `Replay · ${ex.title}`)));
    sel.addEventListener("change", () => {
      const ex = REPLAY_EXAMPLES[Number(sel.value)];
      if (!sel.value || !ex) { this.setReplay(null); return; }
      this.text.value = ex.text;
      document.getElementById("taste-a").value = ex.loaf_a_notes || "";
      document.getElementById("taste-b").value = ex.loaf_b_notes || "";
      this.setReplay(ex);
    });
    this.text.addEventListener("input", () => { if (this.replay && this.text.value !== this.replay.text) this.setReplay(null); });
    this.form.addEventListener("submit", (ev) => { ev.preventDefault(); this.submit(); });
    this.updateHint();
    document.addEventListener("fermentai:token", () => this.updateHint());
  }
  setMode(m) {
    this.mode = m === "live" ? "live" : "offline";
    document.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === this.mode)));
    this.updateHint();
  }
  setReplay(ex) {
    this.replay = ex;
    if (!ex) document.getElementById("replay-select").value = "";
    let banner = this.form.querySelector(".replay-banner");
    if (ex && !banner) { banner = h("div", { class: "replay-banner" }); this.form.querySelector(".taste-controls").before(banner); }
    if (ex) banner.textContent = `REPLAY — scripted example input “${ex.title}”. It is not a live judge's tasting; the analysis below is computed live from it.`;
    else if (banner) banner.remove();
  }
  updateHint() {
    const parts = [];
    if (!getToken()) parts.push("⚠ Read-only: open the ?token=… URL from start_demo.sh (or tap “Read-only” above) to submit.");
    parts.push(this.mode === "live"
      ? "Live mode asks the configured LLM; if it is unreachable the server falls back to the deterministic policy and says so."
      : "Offline mode uses the deterministic decision engine (no internet needed).");
    this.hint.textContent = parts.join(" ");
  }

  // ------------------------------------------------------------------ steps scaffold
  buildSteps() {
    this.steps = {};
    clear(this.stepsEl, STEP_DEFS.map((d) => {
      const body = h("div", { class: "step-body muted small" }, "Waiting for feedback…");
      const title = h("h3", null, d.title);
      const li = h("li", { class: `step${d.wide ? " wide" : ""}`, id: `step-${d.id}` }, title, body);
      this.steps[d.id] = { li, body, title, def: d };
      return li;
    }));
  }
  resetSteps() {
    for (const s of Object.values(this.steps)) {
      s.li.className = `step${s.def.wide ? " wide" : ""}`;
      clear(s.title, s.def.title);
      clear(s.body, h("span", { class: "muted small" }, "…"));
    }
  }
  setStep(id, { title, badges = [], body, state = "shown" }) {
    const s = this.steps[id];
    clear(s.title, title || s.def.title, ...badges);
    if (body !== undefined) clear(s.body, body);
    s.li.classList.remove("active", "error");
    s.li.classList.add("shown");
    if (state === "active") s.li.classList.add("active");
    if (state === "error") s.li.classList.add("error");
  }

  // ------------------------------------------------------------------ events
  wire() {
    const onCmd = (f) => {
      const d = f.data || {};
      if (!d.command_id || !this.motorCmds[d.command_id]) return;
      const status = f.type === "ack" ? (d.command_status || d.status) : d.status;
      this.motorCmds[d.command_id] = { ...this.motorCmds[d.command_id], status, acked: this.motorCmds[d.command_id].acked || f.type === "ack" };
      this.renderMotor();
    };
    this.stream.on("command", onCmd).on("ack", onCmd);
    this.stream.on("experiment", () => this.loadPlan());
    this.stream.on("setpoint", () => this.loadPlan());
    this.stream.on("feedback_result", (f) => {
      // A cycle submitted from another device (or a replay script) - show it too.
      const fid = f.data?.feedback_id;
      if (this.pending || !fid || (this.current && this.current.feedback_id === fid)) return;
      this.loadCycle(fid, true);
    });
  }

  async init() {
    await this.loadPlan();
    try {
      const rows = await get("/api/v1/taste-feedback?limit=1");
      if (rows.length && rows[0].result) {
        this.renderCycle(rows[0].result, { animate: false, label: `Last cycle · feedback #${rows[0].id} · ${timeAgo(rows[0].created_at_utc)}`, replay: /^replay/i.test(rows[0].judge_name || ""), text: rows[0].text });
      }
    } catch { /* none yet */ }
  }

  async loadCycle(fid, animate) {
    try {
      const rows = await get("/api/v1/taste-feedback?limit=10");
      const row = rows.find((r) => r.id === fid);
      if (row && row.result) {
        this.beforeSetpoints = this.currentSetpoints();
        this.renderCycle(row.result, { animate, label: `Feedback #${fid} (submitted from another screen)`, replay: /^replay/i.test(row.judge_name || ""), text: row.text });
      }
    } catch { /* ignore */ }
  }

  currentSetpoints() {
    const out = {};
    for (const c of ["A", "B"]) { const st = this.live.state[c]; if (st) out[c] = st.desired_setpoint_c; }
    return out;
  }

  // ------------------------------------------------------------------ submit
  async submit() {
    const text = this.text.value.trim();
    if (!text) { toast("Please describe how the bread tasted.", "bad"); this.text.focus(); return; }
    if (!getToken()) { toast("Write access needed: open the ?token=… URL or tap “Read-only” to enter the token.", "bad"); return; }
    if (this.pending) return;
    this.pending = true;
    this.submitBtn.disabled = true;
    this.submitBtn.textContent = this.mode === "live" ? "Thinking (live LLM)…" : "Interpreting…";
    this.beforeSetpoints = this.currentSetpoints();
    this.beforeExperimentIds = new Set((this.experiments || []).map((e) => e.experiment_id));
    this.resetSteps();
    this.stepsEl.scrollIntoView({ behavior: "smooth", block: "start" });
    this.setStep("saved", { body: h("span", null, h("span", { class: "spinner" }), " Saving feedback and running the decision cycle…"), state: "active" });
    const a = document.getElementById("taste-a").value.trim();
    const b = document.getElementById("taste-b").value.trim();
    const judge = document.getElementById("taste-judge").value.trim();
    const body = {
      text, mode: this.mode,
      loaf_a_notes: a || null, loaf_b_notes: b || null,
      judge_name: this.replay ? `Replay: ${this.replay.title}`.slice(0, 100) : (judge || null),
    };
    const wasReplay = !!this.replay;
    try {
      const resp = await post("/api/v1/taste-feedback", body, { timeoutMs: this.mode === "live" ? 180000 : 60000 });
      await this.renderCycle(resp, { animate: true, replay: wasReplay, text });
    } catch (e) {
      this.setStep("saved", { title: "Feedback not processed", badges: [statusBadge("error", "bad")], state: "error",
        body: h("div", null, e instanceof ApiError && (e.status === 401 || e.status === 403) ? "The API rejected the token. Re-open the URL printed by start_demo.sh." : `The local API returned an error: ${e.message}`) });
    } finally {
      this.pending = false;
      this.submitBtn.disabled = false;
      this.submitBtn.textContent = "Interpret & plan next experiment";
    }
  }

  // ------------------------------------------------------------------ render a full cycle
  async renderCycle(resp, { animate = true, label = null, replay = false, text = null } = {}) {
    this.current = resp;
    const r = resp.result || {};
    const isReplay = !!replay;
    document.dispatchEvent(new CustomEvent("fermentai:mode", { detail: { modeLabel: r.mode_label, pipeline: resp.pipeline_available } }));
    const msgs = (resp.stored_messages && resp.stored_messages.length ? resp.stored_messages : (r.agent_messages || []).map((m) => ({ content: m, sender: messageSender(m), recipient: messageRecipient(m), kind: messageKind(m) })));
    const sides = msgs.map((m) => agentSide(m.sender ?? messageSender(m)));
    const lead = sides.find((s) => s) || "A";
    const peer = lead === "A" ? "B" : "A";
    const firstPeer = sides.findIndex((s) => s === peer);
    const leadMsgs = firstPeer < 0 ? msgs : msgs.slice(0, firstPeer);
    const peerMsgs = firstPeer < 0 ? [] : msgs.slice(firstPeer);
    this.newExperimentId = resp.experiment?.experiment_id || null;
    this.motorCmds = Object.fromEntries((resp.motor_commands || []).map((c) => [c.command_id, c]));

    const modeBadge = statusBadge(r.mode_label || resp.mode || "mode unknown", /placeholder/i.test(r.mode_label || "") ? "bad" : "info");
    const steps = [
      ["saved", () => ({ badges: [provBadge(isReplay ? "replay" : "imported_human", isReplay ? "replay input" : "judge input"), modeBadge], body: this.bodySaved(resp, label, isReplay, text) })],
      ["interp", () => ({ badges: [provBadge("estimated", "interpreted"), r.sensory_interpretation?.method ? statusBadge(r.sensory_interpretation.method, "info") : null], body: this.bodyInterp(r.sensory_interpretation) })],
      ["compare", () => ({ badges: [provBadge(this.compareProv(r))], body: this.bodyCompare(r) })],
      ["hyp", () => ({ badges: [provBadge("estimated", "hypothesis")], body: this.bodyHyp(r.hypothesis) })],
      ["sched", () => ({ badges: [provBadge("estimated", "proposal")], body: this.bodySched(r.recommended_schedules) })],
      ["lead", () => ({ title: `Scientist ${lead} proposes`, body: this.chat(leadMsgs, "No agent message was returned in this cycle.") })],
      ["peer", () => ({ title: `Scientist ${peer} responds — critique / counter-proposal`, body: this.chat(peerMsgs, `Scientist ${peer} did not send a separate response in this cycle.`) })],
      ["next", () => ({ badges: [provBadge(resp.experiment?.provenance || "estimated")], body: this.bodyNext(resp) })],
      ["motor", () => ({ body: h("div", { id: "taste-motor" }) })],
      ["plan", () => ({ body: h("div", { id: "taste-plan" }) })],
    ];
    for (const [id, make] of steps) {
      let spec;
      try { spec = make(); } catch (e) { console.error(e); spec = { body: h("div", { class: "empty" }, `Could not render this step (${e.message}).`) }; }
      this.setStep(id, { ...spec, state: animate ? "active" : "shown" });
      if (id === "sched") this.drawSched(r.recommended_schedules);
      if (id === "motor") this.renderMotor();
      if (id === "plan") { await this.loadPlan(); this.renderPlanStep(resp); }
      if (animate) {
        this.steps[id].li.scrollIntoView({ behavior: "smooth", block: "nearest" });
        await sleep(STEP_DELAY_MS);
        this.steps[id].li.classList.remove("active");
      }
    }
    if (resp.motor_commands?.length) this.pollMotors();
  }

  bodySaved(resp, label, isReplay, text) {
    const r = resp.result || {};
    const raw = r.sensory_interpretation?.raw_text;
    return h("div", null,
      label ? h("div", { class: "tiny faint" }, label) : null,
      h("div", null, `Feedback #${resp.feedback_id} stored at ${hhmmss(resp.received_at_utc)} · requested mode: ${resp.mode}`),
      resp.pipeline_available === false ? h("div", { class: "stale-banner", style: { marginTop: "6px" } }, "Agents pipeline unavailable — the server saved your feedback and returned a clearly-labelled placeholder.") : null,
      isReplay ? h("div", { class: "replay-banner" }, "Replay: scripted example input, not a live tasting.") : null,
      raw || !text ? null : h("div", { class: "quote", style: { marginTop: "6px" } }, text));
  }

  bodyInterp(si) {
    if (!si) return h("div", { class: "empty" }, "No interpretation returned.");
    if (!si.loaves) return renderValue(si);
    const text = si.raw_text || "";
    const spans = [];
    const addEv = (ev, cls) => (ev || []).forEach((e) => { if (Number.isInteger(e.start) && Number.isInteger(e.end) && e.end > e.start) spans.push({ ...e, cls }); });
    for (const L of ["A", "B"]) for (const a of Object.values(si.loaves[L]?.attributes || {})) addEv(a.evidence, `ev-${L}`);
    for (const a of Object.values(si.unattributed?.attributes || {})) addEv(a.evidence, "");
    for (const d of Object.values(si.desired || {})) addEv(d.evidence, "ev-desired");
    spans.sort((x, y) => x.start - y.start || (y.end - y.start) - (x.end - x.start));
    const quote = h("div", { class: "quote" });
    let pos = 0;
    for (const s of spans) {
      if (s.start < pos) continue;
      quote.append(text.slice(pos, s.start), h("mark", { class: `ev ${s.cls}`, title: s.rule || "" }, text.slice(s.start, s.end)));
      pos = s.end;
    }
    quote.append(text.slice(pos));

    const loafCol = (L) => {
      const attrs = si.loaves[L]?.attributes || {};
      const keys = Object.keys(attrs);
      return h("div", { class: "score-col", style: { "--c": L === "A" ? COLORS.A : COLORS.B } },
        h("h4", null, h("span", { class: `chip chip-${L}` }, `Loaf ${L}`), si.loaves[L]?.mentioned ? "" : h("span", { class: "tiny faint" }, " not mentioned")),
        keys.length ? keys.map((k) => {
          const a = attrs[k];
          const sc = Math.max(0, Math.min(1, num(a.score) ?? 0.5));
          return h("div", { class: "score-row", title: (a.evidence || []).map((e) => `“${e.text}” (${e.rule})`).join("; ") },
            h("span", { class: "lab" }, DIM_LABEL[k] || k),
            h("span", { class: "bar" }, h("span", { class: "fill", style: { width: `${sc * 100}%` } }), h("span", { class: "mid" })),
            h("span", { class: "v" }, sc.toFixed(2)),
            h("span"), h("span", { class: "tiny faint", style: { gridColumn: "2 / 4", marginTop: "-3px" } }, `${a.label || ""} · conf ${fmt(a.confidence, 2)}`));
        }) : h("div", { class: "tiny faint" }, "no attributes recognised"));
    };
    const desired = Object.entries(si.desired || {});
    const unattr = Object.entries(si.unattributed?.attributes || {});
    return h("div", null,
      quote,
      h("div", { class: "tiny faint", style: { marginTop: "3px" } }, "Highlighted: ", h("mark", { class: "ev ev-A" }, "Loaf A evidence"), " ", h("mark", { class: "ev ev-B" }, "Loaf B evidence"), " ", h("mark", { class: "ev ev-desired" }, "desired change"), ` · overall confidence ${fmt(si.overall_confidence, 2)} · scales 0–1, 0.5 = neutral`),
      h("div", { class: "scores" }, loafCol("A"), loafCol("B")),
      unattr.length ? h("div", { class: "small", style: { marginTop: "6px" } }, h("b", null, "Unattributed: "), unattr.map(([k, a]) => `${DIM_LABEL[k] || k} ${a.label || fmt(a.score, 2)}`).join(", ")) : null,
      (si.comparisons || []).length ? h("div", { class: "small", style: { marginTop: "6px" } }, h("b", null, "Comparisons: "), si.comparisons.map((c) => `${c.higher} > ${c.lower} on ${DIM_LABEL[c.dimension] || c.dimension} (“${c.evidence?.text || ""}”)`).join("; ")) : null,
      h("div", { class: "small", style: { marginTop: "6px" } }, h("b", null, "Desired: "), si.desired_summary || (desired.length ? "" : "no explicit preference (exploration)")),
      desired.length ? h("ul", { class: "desired-list small" }, desired.map(([k, d]) => h("li", null,
        h("b", null, DIM_LABEL[k] || k), ` — ${d.direction}${d.strength ? ` (${d.strength})` : ""}${d.preferred_pole ? `, keep ${d.preferred_pole}` : ""}`,
        h("span", { class: "faint" }, ` · conf ${fmt(d.confidence, 2)} · “${(d.evidence || []).map((e) => e.text).join("”, “")}”`)))) : null,
      (si.flags || []).length ? h("div", { class: "small", style: { marginTop: "4px" } }, statusBadge("flags", "warn"), " ", si.flags.join(", ")) : null,
      (si.notes || []).length ? h("div", { class: "tiny faint" }, si.notes.join(" ")) : null);
  }

  compareProv(r) {
    if (r.provenance?.prediction_source) return r.provenance.prediction_source;
    const c = r.comparison_with_recorded || r.comparison || r.experiment_comparison || r.recorded_comparison || r.evidence;
    return c?.prediction_source || "estimated";
  }

  bodyCompare(r) {
    const cw = r.comparison_with_recorded;
    if (cw && typeof cw === "object" && (cw.A || cw.B)) {
      const cal = r.provenance?.calibration || {};
      const rank = Array.isArray(r.candidate_ranking) ? r.candidate_ranking.slice(0, 5) : [];
      return h("div", null,
        h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
          h("thead", null, h("tr", null, h("th", null, "Loaf"), h("th", null, "Recorded schedule"), h("th", { class: "num" }, "Judged sour"), h("th", { class: "num" }, "Judged open"), h("th", { class: "num" }, "Model rise"), h("th", { class: "num" }, "Over-proof risk"))),
          h("tbody", null, Object.entries(cw).map(([L, x]) => h("tr", null,
            h("td", null, h("span", { class: `chip chip-${agentSide(L) || "sys"}` }, L)),
            h("td", null, scheduleText(x.recorded_schedule), x.recorded_source ? h("div", { class: "tiny faint" }, x.recorded_source) : null,
              (x.recorded_violations || []).length ? h("div", { class: "tiny", style: { color: "#ffb0b0" } }, "⚠ ", x.recorded_violations.join("; ")) : null),
            h("td", { class: "num" }, fmt(x.judged?.sourness, 2)), h("td", { class: "num" }, fmt(x.judged?.openness, 2)),
            h("td", { class: "num" }, fmt(x.model_rise_progress, 2), "×"), h("td", { class: "num" }, num(x.model_overproof_risk) === null ? "—" : `${Math.round(x.model_overproof_risk * 100)} %`)))))),
        Object.keys(cal).length ? h("div", { class: "tiny faint" }, "Judged ↔ model anchoring: ", Object.entries(cal).map(([k, v]) => `${k} ${v.status || ""} (${v.n_points ?? "?"} pts)`).join(", ")) : null,
        rank.length ? h("details", { open: true }, h("summary", null, `Validated candidate schedules ranked (${rank.length} best shown)`),
          h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
            h("thead", null, h("tr", null, h("th", null, "Candidate"), h("th", { class: "num" }, "Cost"), h("th", { class: "num" }, "Pred. sour"), h("th", { class: "num" }, "Pred. open"), h("th", { class: "num" }, "Over-proof"))),
            h("tbody", null, rank.map((c, i) => h("tr", null, h("td", null, i === 0 ? "★ " : "", c.name || c.id, h("div", { class: "tiny faint" }, (c.route || "").replace(/_/g, " "))),
              h("td", { class: "num" }, fmt(c.score, 3)), h("td", { class: "num" }, fmt(c.pred_sourness, 2)), h("td", { class: "num" }, fmt(c.pred_openness, 2)),
              h("td", { class: "num" }, num(c.overproof_risk) === null ? "—" : `${Math.round(c.overproof_risk * 100)} %`))))))) : null);
    }
    const c = r.comparison ?? r.experiment_comparison ?? r.recorded_comparison ?? r.evidence ?? null;
    if (c && c.recorded) {
      const rows = Object.entries(c.recorded);
      return h("div", null,
        h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
          h("thead", null, h("tr", null, h("th", null, "Loaf"), h("th", null, "Recorded schedule"), h("th", { class: "num" }, "Judged sour"), h("th", { class: "num" }, "Judged open"), h("th", { class: "num" }, "Model sour"), h("th", { class: "num" }, "Model open"))),
          h("tbody", null, rows.map(([L, x]) => h("tr", null,
            h("td", null, h("span", { class: `chip chip-${L}` }, L)),
            h("td", null, scheduleText(x.points || x), x.source ? h("div", { class: "tiny faint" }, x.source) : null),
            h("td", { class: "num" }, fmt(x.judged?.sourness, 2)), h("td", { class: "num" }, fmt(x.judged?.openness, 2)),
            h("td", { class: "num" }, fmt(x.pred_sourness, 2)), h("td", { class: "num" }, fmt(x.pred_openness, 2))))))),
        c.candidates ? h("div", { class: "tiny faint" }, `${c.candidates.length} validated candidate schedules scored (${c.prediction_source || "model"} predictions${c.policy ? `, policy ${c.policy}` : ""}).`) : null);
    }
    if (c) return renderValue(c);
    // Dashboard-side fallback: what was actually running when the bread was judged.
    const si = r.sensory_interpretation || {};
    const rows = ["A", "B"].map((L) => {
      const st = this.live.state[L] || {};
      const before = this.beforeSetpoints?.[L];
      const attrs = si.loaves?.[L]?.attributes || {};
      return h("tr", null,
        h("td", null, h("span", { class: `chip chip-${L}` }, L)),
        h("td", null, st.experiment_id || "—"),
        h("td", { class: "num" }, fmt(before ?? st.desired_setpoint_c, 1, " °C")),
        h("td", null, Object.entries(attrs).map(([k, a]) => `${DIM_LABEL[k] || k}: ${a.label || fmt(a.score, 2)}`).join(", ") || "—"));
    });
    return h("div", null,
      h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, h("th", null, "Chamber"), h("th", null, "Recorded experiment"), h("th", { class: "num" }, "Setpoint when judged"), h("th", null, "Judged as"))),
        h("tbody", null, rows))),
      h("div", { class: "tiny faint" }, "Dashboard-side summary of the recorded chamber state (the pipeline did not return a separate comparison object)."));
  }

  bodyHyp(hyp) {
    if (!hyp) return h("div", { class: "empty" }, "No hypothesis returned.");
    if (typeof hyp === "string") return h("div", { class: "quote", style: { borderLeftColor: COLORS.accent, fontSize: "1rem" } }, hyp);
    const main = hyp.statement || hyp.hypothesis_statement || hyp.hypothesis || hyp.text || messageText(hyp);
    const facts = [
      ["Proposed by", hyp.proposed_by], ["Why this agent leads", hyp.lead_reason], ["Challenged by", hyp.challenged_by],
      ["Peer agrees", typeof hyp.peer_agrees === "boolean" ? (hyp.peer_agrees ? "yes" : "no — see critique") : hyp.peer_agrees],
      ["Mechanism", hyp.mechanism], ["Alternative hypothesis", hyp.alternative_hypothesis],
      ["Independent variable", hyp.independent_variable], ["Confidence", hyp.confidence],
    ].filter(([, v]) => v !== undefined && v !== null && v !== "");
    const preds = hyp.predictions && typeof hyp.predictions === "object" ? Object.entries(hyp.predictions) : [];
    const shown = new Set(["statement", "hypothesis_statement", "hypothesis", "text", "proposed_by", "lead_reason", "challenged_by", "peer_agrees", "mechanism", "alternative_hypothesis", "independent_variable", "confidence", "predictions"]);
    const rest = Object.fromEntries(Object.entries(hyp).filter(([k, v]) => !shown.has(k) && v !== main));
    return h("div", null,
      main ? h("div", { class: "quote", style: { fontSize: "1rem" } }, main) : null,
      facts.length ? h("dl", { class: "kv", style: { marginTop: "8px" } }, facts.map(([k, v]) => [h("dt", null, k), h("dd", null, typeof v === "object" ? renderValue(v) : String(v))])) : null,
      preds.length ? h("div", { class: "tbl-wrap", style: { marginTop: "6px" } }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, h("th", null, "Predicted"), h("th", { class: "num" }, "Sour"), h("th", { class: "num" }, "Open"), h("th", { class: "num" }, "Final pH"), h("th", { class: "num" }, "Peak rise (h)"), h("th", { class: "num" }, "Over-proof"), h("th", null, ""))),
        h("tbody", null, preds.map(([L, p]) => h("tr", null, h("td", null, h("span", { class: `chip chip-${agentSide(L) || "sys"}` }, L)),
          h("td", { class: "num" }, fmt(p.sourness, 2), p.uncertainty ? h("span", { class: "tiny faint" }, ` ±${fmt(p.uncertainty, 2)}`) : null), h("td", { class: "num" }, fmt(p.openness, 2)),
          h("td", { class: "num" }, fmt(p.final_ph, 2)), h("td", { class: "num" }, fmt(p.time_to_peak_rise_h, 1)),
          h("td", { class: "num" }, num(p.overproof_risk) === null ? "—" : `${Math.round(p.overproof_risk * 100)} %`), h("td", null, p.provenance ? provBadge(p.provenance) : null)))))) : null,
      Object.keys(rest).length ? h("details", null, h("summary", null, "Full hypothesis record (targets, held constant, uncertainty)"), renderValue(rest)) : null);
  }

  bodySched(scheds) {
    if (!scheds || typeof scheds !== "object" || !Object.keys(scheds).length) return h("div", { class: "empty" }, "No schedules recommended.");
    const items = Object.entries(scheds).map(([cid, s]) => {
      const n = normSchedule(s);
      const meta = n.meta || {};
      return h("div", { class: "small" }, h("span", { class: `chip chip-${agentSide(cid) || "sys"}` }, cid), " ",
        h("b", null, meta.name || meta.schedule_id || meta.id || ""), " ", scheduleText(n.points),
        meta.summary ? h("div", { class: "tiny faint" }, meta.summary) : null);
    });
    return h("div", null, h("div", { class: "mini-chart", id: "taste-sched-chart" }), items);
  }

  async drawSched(scheds) {
    const el = document.getElementById("taste-sched-chart");
    if (!el || !scheds) return;
    let Plotly;
    try { Plotly = await whenPlotly(); } catch { return; }
    const traces = Object.entries(scheds).map(([cid, s]) => {
      const n = normSchedule(s);
      const side = agentSide(cid);
      let pts = n.points.slice();
      if (pts.length === 1) pts = [pts[0], [Math.max(pts[0][0] + 12, 12), pts[0][1]]]; // constant hold drawn over 12 h
      return { x: pts.map((p) => p[0]), y: pts.map((p) => p[1]), type: "scatter", mode: "lines+markers", name: `Chamber ${cid}`,
        line: { color: side === "B" ? COLORS.B : COLORS.A, width: 2, shape: n.mode === "hv" ? "hv" : "linear" }, marker: { size: 8, line: { color: "#1a1a19", width: 2 } },
        hovertemplate: `${cid}: %{y:.1f} °C at %{x:.1f} h<extra></extra>` };
    });
    await Plotly.react(el, traces, plotLayout({ showlegend: true, margin: { l: 40, r: 10, t: 22, b: 30 },
      xaxis: { title: { text: "hours", font: { size: 10 } } }, yaxis: { title: { text: "°C", font: { size: 10 } } } }), PLOT_CONFIG);
  }

  chat(msgs, emptyText) {
    if (!msgs.length) return h("div", { class: "empty" }, emptyText);
    return h("div", { class: "chat" }, msgs.map((m) => {
      const sender = m.sender ?? messageSender(m.content ?? m);
      const side = agentSide(sender);
      const body = messageBody(m.content ?? m);
      const text = messageText(m.content ?? m).replace(/^\s*\[[^\]]{1,60}(→|->)[^\]]{1,60}\]\s*/, "");
      const kind = m.kind ?? messageKind(m.content ?? m);
      const extra = ["prediction", "uncertainty", "proposed_measurement", "proposed_intervention", "rationale", "decision_change_note", "alternative_hypothesis"]
        .filter((k) => typeof body[k] === "string" && body[k] && body[k] !== text)
        .map((k) => h("div", { class: "msg-field" }, h("b", null, k.replace(/_/g, " ") + ": "), body[k]));
      const payload = body.payload && typeof body.payload === "object" ? body.payload : null;
      const concerns = Array.isArray(body.concerns || payload?.concerns) ? (body.concerns || payload.concerns) : [];
      return h("div", { class: `msg from-${side || "sys"}` },
        h("div", { class: "msg-head" },
          h("span", { class: `chip chip-${side || "sys"}` }, side ? `Scientist ${side}` : String(sender || "system")),
          m.recipient ?? messageRecipient(m.content ?? m) ? `→ ${m.recipient ?? messageRecipient(m.content ?? m)}` : "",
          kind ? statusBadge(kind, kind === "critique" ? "warn" : "info") : null,
          body.evidence_source ? provBadge(body.evidence_source) : null),
        h("div", null, text || renderValue(body)),
        concerns.length ? h("ul", { class: "desired-list small" }, concerns.map((c) => h("li", null, typeof c === "string" ? c : messageText(c)))) : null,
        extra);
    }));
  }

  bodyNext(resp) {
    const e = resp.experiment || {};
    const nx = resp.result?.next_experiment && typeof resp.result.next_experiment === "object" ? resp.result.next_experiment : {};
    const scheds = nx.schedules || e.schedules || {};
    const skip = new Set(["title", "hypothesis", "schedules", "experiment_id", "id", "status", "provenance", "objective", "requires_human_confirmation"]);
    const extra = Object.fromEntries(Object.entries(nx).filter(([k]) => !skip.has(k)));
    let hyp = nx.hypothesis ?? e.hypothesis;
    if (typeof hyp === "string" && hyp.trim().startsWith("{")) { try { hyp = JSON.parse(hyp); } catch { /* keep string */ } }
    const hypText = typeof hyp === "string" ? hyp : hyp ? (hyp.statement || hyp.text || messageText(hyp)) : "";
    const safety = resp.result?.safety_note;
    return h("div", null, h("div", { class: "exp-card" },
      h("div", { class: "exp-title" }, nx.title || e.title || "Next experiment"),
      h("div", { class: "tiny faint" }, `${e.experiment_id || nx.id || ""} · status ${e.status || nx.status || "proposed"} · proposed by ${e.proposed_by || "agents"}`,
        nx.requires_human_confirmation ? " · requires human confirmation" : ""),
      nx.objective ? h("p", { class: "small", style: { marginBottom: "4px" } }, h("b", null, "Objective: "), nx.objective) : null,
      hypText && hypText !== nx.objective ? h("p", { class: "small faint" }, hypText) : null,
      Object.entries(scheds).map(([cid, sc]) => {
        const n = normSchedule(sc), m = n.meta || {}, p = m.predicted || {};
        return h("div", { class: "small", style: { marginTop: "6px" } },
          h("span", { class: `chip chip-${agentSide(cid) || "sys"}` }, `Chamber ${cid}`), " ", h("b", null, m.name || m.schedule_id || ""), " ",
          h("span", { class: "faint" }, scheduleText(n.points)), m.owner_agent ? h("span", { class: "tiny faint" }, ` · owner ${m.owner_agent}`) : null,
          Object.keys(p).length ? h("div", { class: "tiny" }, `predicted: sour ${fmt(p.sourness, 2)} · open ${fmt(p.openness, 2)} · final pH ${fmt(p.final_ph, 2)} · peak rise ${fmt(p.time_to_peak_rise_h, 1)} h · over-proof ${num(p.overproof_risk) === null ? "—" : Math.round(p.overproof_risk * 100) + " %"} `, p.provenance ? provBadge(p.provenance) : null) : null,
          m.summary ? h("div", { class: "tiny faint" }, m.summary) : null);
      }),
      Object.keys(extra).length ? h("details", null, h("summary", null, "Details"), renderValue(extra)) : null),
      safety ? h("p", { class: "tiny faint", style: { marginTop: "6px" } }, "🛡 ", safety) : null);
  }

  // ------------------------------------------------------------------ motor
  renderMotor() {
    const el = document.getElementById("taste-motor");
    if (!el) return;
    const cmds = Object.values(this.motorCmds);
    if (!cmds.length) { clear(el, h("div", { class: "empty" }, "No motor command was issued in this cycle.")); return; }
    clear(el, cmds.sort((a, b) => String(a.chamber_id).localeCompare(String(b.chamber_id))).map((c) => h("div", { class: "motor-row", style: { marginBottom: "6px", "--c": c.chamber_id === "B" ? COLORS.B : COLORS.A } },
      h("span", { class: `chip chip-${c.chamber_id}` }, `Chamber ${c.chamber_id}`),
      h("span", { class: "small" }, `${c.pattern || "wiggle"} ≤ ${fmt(c.duration_s, 0)} s`),
      motorTrack(c))),
    h("div", { class: "tiny faint" }, "Decorative chef-hat automaton = “new experiment planned”. Separate command type, bounded ≤ 10 s, cancellable, cannot touch the Peltier controller. It does not mean bread was mixed, fed or baked."));
  }
  async pollMotors() {
    const t0 = Date.now();
    while (Date.now() - t0 < 45000) {
      await sleep(2500);
      const open = Object.values(this.motorCmds).filter((c) => !["completed", "failed", "cancelled", "rejected", "expired", "expired_unacked", "superseded"].includes(c.status));
      if (!open.length) return;
      try {
        const rows = await get("/api/v1/commands?command_type=motor&limit=20");
        for (const r of rows) if (this.motorCmds[r.command_id]) this.motorCmds[r.command_id] = { ...this.motorCmds[r.command_id], status: r.status, acked: r.acked };
        this.renderMotor();
      } catch { /* keep trying */ }
    }
  }

  // ------------------------------------------------------------------ plan
  async loadPlan() {
    try { this.experiments = await get("/api/v1/experiments?limit=8"); } catch { this.experiments = this.experiments || []; }
    this.renderPlanCard();
  }
  setpointRows(applied) {
    return ["A", "B"].map((c) => {
      const st = this.live.state[c] || {};
      const before = this.beforeSetpoints?.[c];
      const a = applied?.[c];
      const after = a && !a.skipped ? a.applied_c : st.desired_setpoint_c;
      const prev = a && !a.skipped ? a.previous_c : before;
      const changed = num(prev) !== null && num(after) !== null && Math.abs(prev - after) > 1e-6;
      return [h("span", { class: `chip chip-${c}` }, c),
        h("span", { class: "small" }, changed ? [h("span", { class: "strike" }, fmt(prev, 1, " °C")), h("span", { class: "arrow" }, " → "), h("b", null, fmt(after, 1, " °C"))] : `${fmt(after, 1, " °C")} (unchanged)`,
          a?.clamped ? statusBadge("clamped", "warn") : null, a?.step_limited ? statusBadge("step-limited", "warn") : null, a?.rejected ? statusBadge("rejected", "bad") : null)];
    });
  }
  renderPlanCard() {
    const exps = this.experiments || [];
    const header = document.querySelector("#plan-card h3");
    if (header) clear(header, "Updated experiment plan", this.newExperimentId ? statusBadge("updated", "good") : null);
    clear(this.planBody,
      h("div", { class: "small muted" }, "Chamber setpoints now"),
      h("div", { class: "sp-change" }, this.setpointRows(this.current?.applied_setpoints)),
      h("div", { class: "small muted", style: { marginTop: "10px" } }, "Experiments (newest first)"),
      exps.length ? exps.slice(0, 4).map((e) => this.planItem(e)) : h("div", { class: "empty" }, "No experiments recorded yet. Submit taste feedback to create one."));
  }
  planItem(e) {
    const isNew = e.experiment_id === this.newExperimentId;
    return h("div", { class: `plan-item${isNew ? " new" : ""}` },
      h("div", { class: "t" }, isNew ? "★ " : "", e.title || e.experiment_id),
      h("div", { class: "tiny faint" }, `${e.experiment_id} · ${e.status} · ${e.proposed_by || ""} · ${timeAgo(e.created_at_utc)}`, " ", provBadge(e.provenance || "unknown")),
      e.schedules && Object.keys(e.schedules).length ? h("div", { class: "tiny" }, Object.entries(e.schedules).map(([cid, s]) => `${cid}: ${scheduleText(normSchedule(s).points)}`).join(" · ")) : null);
  }
  currentRunActions(cra) {
    if (!cra || typeof cra !== "object" || !Object.keys(cra).length) return null;
    return h("div", { class: "small", style: { marginTop: "8px" } }, h("div", { class: "small muted" }, "Current run (live chambers)"),
      Object.entries(cra).map(([cid, a]) => h("div", { class: "tiny", style: { marginTop: "3px" } },
        h("span", { class: `chip chip-${agentSide(cid) || "sys"}` }, cid), " ", h("b", null, a.action || a.status || ""),
        num(a.suggested_setpoint_c) !== null ? ` → ${fmt(a.suggested_setpoint_c, 1, " °C")}` : "",
        a.requires_human_confirmation ? " (needs human confirmation)" : "", a.rationale ? h("span", { class: "faint" }, ` — ${a.rationale}`) : null)));
  }
  renderPlanStep(resp) {
    const el = document.getElementById("taste-plan");
    if (!el) return;
    const e = resp.experiment || {};
    const applyBtn = h("button", { class: "btn btn-sm", type: "button", disabled: !getToken() || !e.experiment_id, onclick: async (ev) => {
      ev.currentTarget.disabled = true;
      try {
        const r = await post(`/api/v1/experiments/${encodeURIComponent(e.experiment_id)}/apply`, {});
        resp.applied_setpoints = r.applied_setpoints; resp.schedule_applied = true;
        toast("Experiment applied: first schedule step sent through the safety-clamped setpoint path.", "good");
        await this.loadPlan(); this.renderPlanStep(resp);
      } catch (err) { toast(`Apply failed: ${err.message}`, "bad"); ev.currentTarget.disabled = false; }
    } }, "Apply first step to chambers (operator)");
    clear(el,
      h("div", { class: "sp-change" }, this.setpointRows(resp.applied_setpoints)),
      h("div", { class: "small", style: { marginTop: "6px" } }, resp.schedule_applied
        ? [statusBadge("applied", "good"), " First schedule step sent via the clamped, step-limited setpoint path."]
        : [statusBadge("proposed", "info"), " Plan updated; chamber setpoints unchanged until an operator applies it. ", applyBtn]),
      this.currentRunActions(resp.result?.current_run_actions),
      h("div", { class: "small muted", style: { marginTop: "8px" } }, "Plan"),
      (this.experiments || []).slice(0, 3).map((x) => this.planItem(x)));
    this.renderPlanCard();
  }
}

function scheduleText(points) {
  if (!Array.isArray(points) || !points.length) return "—";
  const pts = points.map((p) => (Array.isArray(p) ? p : [p.t_h, p.setpoint_c]));
  if (pts.length === 1) return `${fmt(pts[0][1], 1)} °C hold`;
  return pts.map(([t, c]) => `${fmt(c, 0)}°C@${fmt(t, 1)}h`).join(" → ");
}
