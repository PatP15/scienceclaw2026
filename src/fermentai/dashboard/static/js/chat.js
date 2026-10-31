// "Talk to Sous Chef A/B": chat panel on the Taste tab. Contract (API):
//   POST   /api/v1/chat {session_id?, text, mode, sous_chef, judge_name?} -> {session_id, reply, stage, understood,
//          questions_remaining, transcript[{role, text, ts}], handoff_result, persisted?, reasoning_mode_label}
//   GET    /api/v1/chat/{session_id}      DELETE /api/v1/chat/{session_id}      SSE `chat` {session_id, role, text, stage}
// Degrades to a "chat not available yet" state when the route is missing (404) or the service is down (503).
import { get, post, del, getToken, ApiError } from "./api.js";
import { h, clear, icon, emptyCard, statusBadge, toast, brigadeText, agentSide, sparkline, fmt, num } from "./util.js";

const SESSION_KEY = "fermentai.chat.session";
const CHEF_KEY = "fermentai.chat.sousChef";
const STAGE_LABEL = { greeting: "greeting", collecting: "listening", confirming: "confirming", handoff: "handing off to Head Chef", choosing: "choose next step", done: "plan called", reset: "new tasting" };
const DIM_WORD = { sourness: ["more sour", "milder", "keep the sourness"], texture: ["more open / airy", "tighter crumb", "keep it airy"],
  density: ["denser", "less dense", "keep it light"], aroma: ["more aroma", "less aroma", "keep the aroma"], flavor_intensity: ["more flavour", "less flavour", "keep the flavour"] };

function lsGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function lsSet(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } }

/** Small chips from a (partial) sensory interpretation of unknown-but-plausible shape. */
export function understoodChips(u) {
  if (!u || typeof u !== "object") return [];
  const chips = [];
  const loaves = u.loaves || u.sensory_interpretation?.loaves;
  for (const L of ["A", "B"]) {
    let words = [];
    // parse_feedback shape {L: {attributes: {dim: {...}}}} or chat shape {L: {dim: {label, score}}}
    const attrs = loaves?.[L]?.attributes || (loaves?.[L] && typeof loaves[L] === "object" && !Array.isArray(loaves[L]) ? loaves[L] : null);
    if (attrs && typeof attrs === "object") {
      for (const a of Object.values(attrs)) {
        if (!a || typeof a !== "object") continue;
        const w = (a.evidence?.[0]?.text || String(a.label || "").split("/").pop() || "").toString().trim();
        if (w && !words.includes(w)) words.push(w);
      }
    } else {
      const flat = u[`loaf_${L}`] ?? u[`loaf_${L.toLowerCase()}`] ?? u[L];
      if (Array.isArray(flat)) words = flat.map(String);
      else if (typeof flat === "string") words = [flat];
    }
    if (words.length) chips.push({ side: L, text: `${L}: ${words.slice(0, 4).join(" · ")}` });
  }
  let wants = Array.isArray(u.structured_preference) ? u.structured_preference.map(String) : [];
  if (!wants.length && u.desired && typeof u.desired === "object") {
    for (const [dim, d] of Object.entries(u.desired)) {
      const w = DIM_WORD[dim] || [`more ${dim}`, `less ${dim}`, `keep ${dim}`];
      wants.push(d?.direction === "increase" ? w[0] : d?.direction === "decrease" ? w[1] : w[2]);
    }
  }
  if (!wants.length && Array.isArray(u.wants)) wants = u.wants.map(String);
  if (wants.length) chips.push({ side: "wants", text: `wants: ${wants.slice(0, 4).join(", ")}` });
  const other = u.compared_other;
  if (other) {
    const o = typeof other === "object" ? other : {};
    const vs = o.station || o.other_station || (u.station === "A" ? "B" : u.station === "B" ? "A" : null);
    const sum = typeof other === "string" ? other : o.summary || o.text || "";
    chips.push({ side: vs || "cmp", text: `compared${vs ? ` with ${vs}` : ""}${sum ? `: ${String(sum).slice(0, 40)}` : ""}` });
  }
  if (u.preferred_loaf) chips.push({ side: "pref", text: `prefers ${u.preferred_loaf}` });
  return chips;
}

export class ChatPanel {
  /** opts.station: bind to one station's Sous Chef (own session, fixed chef, reveal inside the lab view). */
  constructor(root, taste, opts = {}) {
    this.root = root;
    this.taste = taste;
    this.station = opts.station || null;
    this.sessionKey = this.station ? `${SESSION_KEY}.${this.station}` : SESSION_KEY;
    this.sessionId = lsGet(this.sessionKey);
    this.sousChef = this.station || (lsGet(CHEF_KEY) === "B" ? "B" : "A");
    this.transcript = [];
    this.stage = null;
    this.understood = null;
    this.available = null;     // null = unknown, true, false
    this.reason = "";
    this.pending = false;
    this.revealedKey = null;
    this.build();
  }

  // ------------------------------------------------------------------ DOM
  build() {
    this.titleEl = h("h2", { class: "chat-title" });
    this.stageEl = h("span", { class: "chat-stage" });
    this.seg = h("div", { class: "seg seg-sm", role: "radiogroup", "aria-label": "Which Sous Chef" },
      ["A", "B"].map((c) => h("button", { type: "button", class: "seg-btn", role: "radio", dataset: { chef: c }, onclick: () => this.setChef(c) }, `Sous Chef ${c}`)));
    this.newBtn = h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: () => this.reset() }, "New tasting");
    this.log = h("div", { class: "chat-log", role: "log", "aria-live": "polite" });
    this.chips = h("div", { class: "chat-chips" });
    this.input = h("textarea", { class: "chat-input", rows: "2", maxlength: "2000", placeholder: "How did this loaf taste?", "aria-label": "Message to the Sous Chef" });
    this.input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.send(); } });
    this.sendBtn = h("button", { class: "btn btn-primary btn-big chat-send", type: "button", onclick: () => this.send() }, "Send");
    this.hint = h("div", { class: "tiny faint chat-hint" });
    clear(this.root,
      h("div", { class: "chat-head" }, h("span", { class: "chat-ico hat", "data-hat": this.sousChef }, icon("toque", { size: 24 })), this.titleEl, this.stageEl,
        h("span", { class: "chat-head-actions" }, this.station ? null : this.seg, this.newBtn)),
      this.log, this.chips,
      h("div", { class: "chat-compose" }, this.input, this.sendBtn),
      this.hint);
    this.setChef(this.sousChef, false);
    this.renderLog();
  }

  setChef(c, persist = true) {
    this.sousChef = c === "B" ? "B" : "A";
    if (persist) lsSet(CHEF_KEY, this.sousChef);
    this.root.dataset.chef = this.sousChef;
    this.seg.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.chef === this.sousChef)));
    clear(this.titleEl, this.station ? `Talk to Sous Chef ${this.sousChef} about this loaf` : `Talk to Sous Chef ${this.sousChef}`);
    this.titleEl.title = `agent id: scientist_${this.sousChef}`;
  }

  setStage(s) {
    this.stage = s;
    clear(this.stageEl, s ? statusBadge(STAGE_LABEL[s] || s, s === "done" ? "good" : s === "handoff" ? "warn" : "info") : null);
  }

  updateHint() {
    const parts = [];
    if (this.available === false) parts.push(`Chat not available yet${this.reason ? ` (${this.reason})` : ""} — the form below runs the same cycle.`);
    else if (!getToken()) parts.push("Read-only: click “Read-only” to enter the API token (or open the ?token=… URL) to chat.");
    else parts.push(`Enter ↵ send · ${this.taste.mode === "live" ? "Online Agent" : "Offline Policy"}`);
    this.hint.textContent = parts.join(" ");
    const disabled = this.available === false || !getToken() || this.pending;
    this.input.disabled = this.available === false || !getToken();
    this.sendBtn.disabled = disabled;
  }

  bubble(entry) {
    const role = String(entry.role || "").toLowerCase();
    const isGuest = role === "guest" || role === "user" || role === "judge";
    const isHead = role === "head_chef" || role === "headchef" || role === "system" || role === "orchestrator";
    const side = isGuest ? "guest" : isHead ? "head" : (agentSide(entry.agent_id || entry.sous_chef || role) || this.sousChef);
    const name = isGuest ? "Guest" : isHead ? "Head Chef" : `Sous Chef ${side}`;
    const av = h("span", { class: "chat-av", title: isHead || isGuest ? name : `${name} (agent id: scientist_${side})` },
      icon(isGuest ? "guest" : isHead ? "headchef" : "toque", { size: 18 }));
    return h("div", { class: `cmsg ${isGuest ? "cmsg-guest" : isHead ? "cmsg-head" : `cmsg-chef chef chef-${side}`}` },
      isGuest ? null : av,
      h("div", { class: "cbubble" },
        h("div", { class: "cwho" }, name, entry.ts ? h("span", { class: "cts" }, ` · ${String(entry.ts).slice(11, 16)}`) : null),
        h("div", { class: "ctext" }, brigadeText(entry.text || ""))),
      isGuest ? av : null);
  }

  renderLog() {
    if (this.available === false) {
      clear(this.log, emptyCard({ icon: "toque", title: "Chat not available yet", detail: "Fallback: Kitchen pass → free-text form." }));
    } else if (!this.transcript.length) {
      clear(this.log, h("div", { class: "chat-empty" }, this.bubble({ role: `scientist_${this.sousChef}`,
        text: this.station ? `How did loaf ${this.sousChef} taste?` : "How did the bread taste?" })));
    } else {
      clear(this.log, this.transcript.map((e) => this.bubble(e)));
    }
    const sg = this.suggestionsBlock();
    if (sg) this.log.append(sg);
    if (this.pending) this.log.append(h("div", { class: "cmsg chef chef-" + this.sousChef + " typing" }, h("span", { class: "chat-av" }, icon("toque", { size: 18 })),
      h("div", { class: "cbubble" }, h("span", { class: "dots" }, h("i"), h("i"), h("i")))));
    this.log.scrollTop = this.log.scrollHeight;
    const chips = understoodChips(this.understood);
    clear(this.chips, chips.length ? [h("span", { class: "tiny faint" }, "Understood so far:"), chips.map((c) => h("span", { class: `uchip uchip-${c.side}` }, c.text))] : null);
    this.updateHint();
  }

  /** Three Head Chef suggestion cards (click = send "1"/"2"/"3"); once chosen, a cream "Next at Station" ticket. */
  suggestionsBlock() {
    const sug = this.hres?.hr?.suggestions;
    if (!Array.isArray(sug) || !sug.length) return null;
    const chosen = this.hres.chosen;
    const chosenId = chosen && (chosen.id ?? chosen.suggestion_id), chosenRank = chosen && (chosen.rank ?? null);
    const isChosen = (x) => chosen && ((chosenId !== undefined && chosenId !== null && x.id === chosenId) || (chosenRank !== null && x.rank === chosenRank));
    const chips = (x) => [["pH@16h", x.predicted?.ph_16h, 2, ""], ["peak rise", x.predicted?.peak_rise, 1, "×"], ["overproof", x.predicted?.overproof_h, 1, " h"]]
      .filter(([, v]) => num(v) !== null).map(([l, v, d, u]) => h("span", { class: "sg-chip" }, `${l} ${fmt(v, d)}${u}`));
    const spark = (x) => Array.isArray(x.points) && x.points.length > 1 ? sparkline([{ values: x.points.map((p) => ({ t: num(p[0]), v: num(p[1]) })), color: "currentColor" }], { height: 26 }) : null;
    if (chosen) {
      const x = sug.find(isChosen) || chosen;
      const expId = chosen.experiment_id || this.hres.exp?.experiment_id || this.hres.hr.next_experiment?.id;
      return h("div", { class: "sg-ticket" },
        h("div", { class: "sg-ticket-h" }, `Next at Station ${this.sousChef}`),
        h("div", { class: "sg-title" }, x.title || x.schedule_id || "chosen plan"), spark(x),
        h("div", { class: "sg-chips" }, chips(x)), x.why ? h("div", { class: "sg-why" }, x.why) : null,
        expId ? h("button", { class: "btn btn-primary btn-sm", type: "button", disabled: !getToken(), title: getToken() ? "" : "Needs the API token",
          onclick: async (ev) => { const b = ev.currentTarget; b.disabled = true;
            try { await post(`/api/v1/experiments/${encodeURIComponent(expId)}/apply`); b.textContent = "Applied"; toast("Applied to station (safety-clamped setpoint path).", "good"); }
            catch (e) { b.disabled = false; toast(`Apply failed: ${e.message}`, "bad"); } } }, "Apply to station") : null);
    }
    return h("div", { class: "sg-list" }, sug.slice(0, 3).map((x) => h("button", { type: "button", class: "sg-card", disabled: this.pending || !getToken(),
      title: `Send "${x.rank}"`, onclick: () => { this.input.value = String(x.rank); this.send(); } },
      h("div", { class: "sg-top" }, h("span", { class: "sg-title" }, x.title || x.schedule_id || `Option ${x.rank}`),
        x.rank === 1 ? h("span", { class: "sg-pick" }, "Head Chef's pick") : null),
      spark(x), h("div", { class: "sg-chips" }, chips(x), x.risk ? h("span", { class: "sg-chip sg-risk" }, `risk: ${x.risk}`) : null),
      x.why ? h("div", { class: "sg-why" }, x.why) : null)));
  }

  // ------------------------------------------------------------------ data
  async init(stream, apiPaths) {
    this.available = apiPaths ? apiPaths.has("/api/v1/chat") : null;
    if (this.available === false) this.reason = "no /api/v1/chat route on this server";
    stream.on("chat", (f) => this.onEvent(f.data || {}));
    document.addEventListener("fermentai:token", () => this.updateHint());
    document.addEventListener("fermentai:taste-mode", () => this.updateHint());
    if (this.available !== false && this.sessionId) {
      try { this.apply(await get(`/api/v1/chat/${encodeURIComponent(this.sessionId)}`), { reveal: false }); }
      catch (e) {
        if (e.status === 404 && /unknown chat session/i.test(e.message)) { this.sessionId = null; lsSet(this.sessionKey, null); }
        else this.markUnavailable(e);
      }
    }
    this.renderLog();
  }

  markUnavailable(e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 503)) {
      this.available = false;
      this.reason = e.status === 503 ? (String(e.message).replace(/\s*\[.*\]$/, "").slice(0, 140) || "service unavailable") : "no /api/v1/chat route on this server";
      return true;
    }
    return false;
  }

  apply(r, { reveal = true } = {}) {
    if (!r || typeof r !== "object") return;
    if (r.session_id) { this.sessionId = r.session_id; lsSet(this.sessionKey, r.session_id); }
    this.available = true;
    if (Array.isArray(r.transcript)) this.transcript = r.transcript.filter((e) => e && typeof e === "object");
    else if (r.reply) this.transcript.push({ role: `scientist_${this.sousChef}`, text: r.reply });
    if (r.understood) this.understood = r.understood;
    const hr = r.handoff_result || r.persisted?.result;
    if (hr) this.hres = { hr, chosen: hr.chosen_suggestion || r.persisted?.chosen || r.chosen_suggestion || null, exp: r.persisted?.experiment };
    if (!this.station && (r.sous_chef === "A" || r.sous_chef === "B")) this.setChef(r.sous_chef, false);
    this.setStage(r.stage || null);
    if (r.reasoning_mode_label) document.dispatchEvent(new CustomEvent("fermentai:mode", { detail: { reasoning: r.reasoning_mode_label, modeLabel: r.reasoning_mode_label } }));
    if ((r.stage === "handoff" || r.stage === "done") && (r.persisted || r.handoff_result)) this.handoff(r, reveal);
    this.renderLog();
  }

  /** Play the existing 12-step reveal from the hand-off, exactly as for a form submission. */
  handoff(r, reveal) {
    const resp = r.persisted && r.persisted.result ? r.persisted : {
      feedback_id: r.feedback_id ?? null, received_at_utc: new Date().toISOString(), mode: this.taste.mode,
      pipeline_available: true, result: r.handoff_result, experiment: r.handoff_result?.next_experiment
        ? { experiment_id: r.handoff_result.next_experiment.id, title: r.handoff_result.next_experiment.title, schedules: r.handoff_result.next_experiment.schedules, status: "proposed", provenance: r.handoff_result.provenance?.decision_source || "estimated" } : {},
      stored_messages: [], motor_commands: [], applied_setpoints: {}, schedule_applied: false };
    const key = String(resp.feedback_id ?? r.handoff_result?.cycle_id ?? this.sessionId);
    // Head Chef summary as a chat bubble (unless the transcript already carries one after the hand-off).
    if (!this.transcript.some((e) => /head/i.test(String(e.role)))) {
      const res = resp.result || {};
      const nx = res.next_experiment || {};
      const goal = res.sensory_interpretation?.desired_summary;
      this.transcript.push({ role: "head_chef", text: `Plan called: ${nx.title || resp.experiment?.title || "next experiment"}.${goal ? ` Goal — ${goal}` : ""}${nx.objective && nx.objective !== goal ? ` ${nx.objective}` : ""}` });
    }
    if (!reveal || this.revealedKey === key) { this.revealedKey = key; return; }
    this.revealedKey = key;
    const guestText = this.transcript.filter((e) => e.role === "guest").map((e) => e.text).join("\n");
    this.taste.beforeSetpoints = this.taste.currentSetpoints();
    const slot = this.station ? document.getElementById("lab-reveal") : null;   // play inside the lab view
    this.taste.resetSteps(slot);
    this.taste.stepsEl.scrollIntoView({ behavior: "smooth", block: "start" });
    this.taste.renderCycle(resp, { animate: true, text: guestText, label: `Sous Chef ${this.sousChef} chat`, slot });
  }

  async send() {
    const text = this.input.value.trim();
    if (!text || this.pending) return;
    if (!getToken()) { toast("Write access needed to chat: click “Read-only” to enter the API token.", "bad"); return; }
    this.pending = true;
    this.taste.pending = true;  // the hand-off's SSE feedback_result must not trigger a second reveal
    document.dispatchEvent(new CustomEvent("fermentai:thinking", { detail: { station: this.sousChef, on: true } }));
    this.transcript.push({ role: "guest", text, ts: new Date().toISOString() });
    this.input.value = "";
    this.renderLog();
    try {
      const judge = document.getElementById("taste-judge")?.value.trim();
      const r = await post("/api/v1/chat", { session_id: this.sessionId || undefined, text, mode: this.taste.mode, sous_chef: this.sousChef, judge_name: judge || undefined },
        { timeoutMs: this.taste.mode === "live" ? 180000 : 60000 });
      this.pending = false;
      this.apply(r);
    } catch (e) {
      this.pending = false;
      this.transcript.pop();
      this.input.value = text;
      if (this.markUnavailable(e)) toast("Chat not available on this server yet — use the form below.", "bad");
      else toast(`Chat failed: ${e.message}`, "bad");
      this.renderLog();
    } finally {
      this.taste.pending = false;
      document.dispatchEvent(new CustomEvent("fermentai:thinking", { detail: { station: this.sousChef, on: false } }));
    }
  }

  async reset() {
    const sid = this.sessionId;
    this.sessionId = null; lsSet(this.sessionKey, null);
    this.transcript = []; this.understood = null; this.revealedKey = null; this.hres = null; this.setStage(null);
    this.renderLog();
    if (sid && this.available !== false && getToken()) { try { await del(`/api/v1/chat/${encodeURIComponent(sid)}`); } catch { /* best effort */ } }
  }

  onEvent(d) {
    if (!d || !this.sessionId || d.session_id !== this.sessionId) return;
    if (d.stage === "reset") { this.sessionId = null; lsSet(this.sessionKey, null); this.transcript = []; this.understood = null; this.hres = null; this.setStage(null); this.renderLog(); return; }
    if (this.pending) return;  // our own turn: the POST response carries the full transcript
    // Another screen spoke in this session: refresh from the server.
    get(`/api/v1/chat/${encodeURIComponent(this.sessionId)}`).then((r) => this.apply(r)).catch(() => {});
  }
}
