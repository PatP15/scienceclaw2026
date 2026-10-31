// "Compare labs": overlaid charts, one line per lab (station colour). Only series a lab actually reports are drawn.
import { get, getText } from "./api.js";
import { h, clear, fmt, num, parseCSV, deviceBadge, statusBadge, plotLayout, PLOT_CONFIG, whenPlotly, COLORS, tfield } from "./util.js";

const colorOf = (c) => COLORS[c] || COLORS.accent;
const bandOf = (c) => COLORS[`${c}band`] || "rgba(232,180,90,0.18)";
const RANGES = [["1h", "1 h", 1], ["6h", "6 h", 6], ["24h", "24 h", 24], ["exp", "Since experiment start", null]];
const rowT = (r) => Date.parse(r.timestamp_utc || r.received_at_utc);
const CHARTS = [
  { key: "air", title: "Air temperature · setpoint steps", unit: "°C", get: (r) => num(tfield(r, "chamber_temperature_c")), step: true },
  { key: "starter", title: "Starter temperature", unit: "°C", get: (r) => num(tfield(r, "starter_temperature_c")) },
  { key: "humidity", title: "Humidity", unit: "%", get: (r) => num(tfield(r, "relative_humidity_percent")) },
];
const FORECASTS = [
  { key: "ph", title: "pH forecast (modeled)", col: "ph" },
  { key: "rise", title: "Rise forecast (modeled)", col: "expansion_ratio" },
];

export class ComparePanel {
  constructor(store, live) {
    this.store = store; this.live = live;
    this.root = document.getElementById("compare-body");
    this.ids = []; this.picked = this.loadPicked(); this.range = "6h";
    this.rows = {}; this.pred = null; this.loading = false; this.visible = false;
    setInterval(() => { if (this.visible) this.refresh(); }, 30000);
    // the lab list arrives after the first show() on a direct #compare load
    setInterval(() => { if (this.visible && this.live.ids.join() !== this.ids.join()) this.refresh(); }, 1000);
  }
  loadPicked() { try { const v = JSON.parse(localStorage.getItem("fermentai.compare.labs")); return Array.isArray(v) ? v : null; } catch { return null; } }
  savePicked() { try { localStorage.setItem("fermentai.compare.labs", JSON.stringify(this.picked)); } catch { /* storage blocked */ } }
  show() { this.visible = true; this.ids = this.live.ids.slice(); this.refresh(); }
  hide() { this.visible = false; }
  resize() { if (window.Plotly) document.querySelectorAll("#compare-body .cmp-chart").forEach((el) => { try { window.Plotly.Plots.resize(el); } catch { /* not drawn */ } }); }

  online(c) { const s = this.live.state[c]; return !!s?.latest_telemetry && !this.live.isStale(s); }
  chosen() { return (this.picked || this.ids.filter((c) => this.live.state[c]?.kind === "lab" && this.online(c))).filter((c) => this.ids.includes(c)); }

  async refresh() {
    if (this.loading) return;
    this.loading = true;
    this.ids = this.live.ids.slice();
    try {
      const [, , hrs] = RANGES.find((r) => r[0] === this.range);
      let limit = 20000;
      if (hrs) limit = Math.min(20000, Math.ceil(hrs * 3600 / 3) + 200);
      await Promise.all(this.ids.map(async (c) => { try { this.rows[c] = await get(`/api/v1/chambers/${c}/telemetry?limit=${limit}`); } catch { this.rows[c] = this.rows[c] || []; } }));
      if (!this.pred) this.pred = await this.loadPred();
      if (!this.exps) { try { this.exps = await get("/api/v1/experiments?limit=20"); } catch { this.exps = []; } }
      this.build();
      await this.draw();
    } finally { this.loading = false; }
  }

  async loadPred() {
    const out = {};
    try {
      const files = (await get("/api/v1/predictions")).files || [];
      const find = (re) => files.find((f) => re.test(f.path));
      await Promise.all(this.ids.map(async (c) => {
        const u = find(new RegExp(`(^|/)chamber_${c}_(uncertainty|envelope)\\.csv$`)), p = find(new RegExp(`(^|/)chamber_${c}_prediction\\.csv$`)), s = find(new RegExp(`(^|/)chamber_${c}_summary\\.json$`));
        const one = async (f, csv) => { if (!f) return null; try { return csv ? parseCSV(await getText(f.url, { timeoutMs: 30000 })) : await get(f.url); } catch { return null; } };
        out[c] = { unc: await one(u, true), pred: await one(p, true), sum: await one(s, false) };
      }));
    } catch { /* offline predictions */ }
    return out;
  }

  window(c) {
    const rows = (this.rows[c] || []).filter((r) => r.timestamp_utc || r.received_at_utc);
    const now = this.live.serverNow();
    let t0;
    if (this.range === "exp") {
      const e = (this.exps || []).find((x) => x.status === "running");
      t0 = e ? Date.parse(e.created_at_utc) : Math.min(...this.chosen().map((k) => (this.rows[k] || []).length ? rowT(this.rows[k][0]) : now), now);
    } else t0 = now - RANGES.find((r) => r[0] === this.range)[2] * 3600e3;
    return { rows: rows.filter((r) => rowT(r) >= t0), t0, t1: now };
  }

  build() {
    const chosen = this.chosen();
    const chips = h("div", { class: "cmp-bar" },
      h("div", { class: "cmp-chips" }, this.ids.map((c) => h("button", { type: "button", class: `chip chip-${c} cmp-chip${chosen.includes(c) ? " on" : ""}`, "aria-pressed": String(chosen.includes(c)),
        title: this.online(c) ? "online" : "offline / stale",
        onclick: () => { const s = new Set(chosen); s.has(c) ? s.delete(c) : s.add(c); this.picked = [...s]; this.savePicked(); this.build(); this.draw(); } }, `Lab ${c}${this.online(c) ? "" : " ·off"}`))),
      h("div", { class: "seg seg-sm", role: "radiogroup", "aria-label": "Time range" }, RANGES.map(([k, label]) => h("button", { type: "button", class: "seg-btn", role: "radio", "aria-checked": String(this.range === k),
        onclick: () => { this.range = k; this.refresh(); } }, label))));
    const shown = [...CHARTS.filter((s) => chosen.some((c) => this.window(c).rows.some((r) => s.get(r) !== null))), ...(this.hasForecast(chosen) ? FORECASTS : [])];
    this.shown = shown;
    clear(this.root, chips, this.summary(chosen),
      chosen.length ? h("div", { class: "chart-grid cmp-grid" }, shown.map((s) => h("div", { class: "chart-card" }, h("h4", null, s.title), h("div", { class: "chart-sub" }, s.unit || (s.col ? "hours from model start · band 5–95 %" : "")),
        h("div", { class: "chart cmp-chart", id: `cmp-${s.key}` }))), this.doors(chosen)) : h("div", { class: "empty" }, "Pick at least one lab."));
  }
  hasForecast(chosen) { return chosen.some((c) => this.pred?.[c]?.pred?.length); }

  summary(chosen) {
    const rows = chosen.map((c) => {
      const s = this.live.state[c] || {}, tel = s.latest_telemetry, sum = this.pred?.[c]?.sum;
      return h("tr", null, h("td", null, h("span", { class: `chip chip-${c}` }, c)),
        h("td", null, s.experiment_id || sum?.name || "—"),
        h("td", { class: "num" }, fmt(sum?.ph_at_16h, 2)), h("td", { class: "num" }, sum?.overproof_onset_h != null ? `${fmt(sum.overproof_onset_h, 1)} h` : "—"),
        h("td", null, this.online(c) ? statusBadge("fresh", "good") : statusBadge("stale", "warn")),
        h("td", null, tel ? deviceBadge(tel.source) : "—"));
    });
    return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["Lab", "Experiment / schedule", "pH @16 h (model)", "Overproof (model)", "Data", "Source"].map((t, i) => h("th", { class: i === 2 || i === 3 ? "num" : "" }, t)))),
      h("tbody", null, rows)));
  }

  doors(chosen) {
    const NS = "http://www.w3.org/2000/svg";
    const lines = chosen.map((c) => {
      const { rows, t0, t1 } = this.window(c);
      if (!rows.length || rows.every((r) => tfield(r, "door_open") === null)) return null;
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 300 14"); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("class", "door-line");
      const bg = document.createElementNS(NS, "rect"); for (const [k, v] of [["y", 4], ["width", 300], ["height", 6], ["rx", 3], ["fill", "#2d323a"]]) bg.setAttribute(k, v); svg.append(bg);
      let opens = 0, prev = false;
      rows.forEach((r, i) => {
        const open = tfield(r, "door_open") === true;
        if (open && !prev) opens++;
        prev = open;
        if (!open) return;
        const a = rowT(r), b = i + 1 < rows.length ? rowT(rows[i + 1]) : t1;
        const seg = document.createElementNS(NS, "rect");
        for (const [k, v] of [["x", ((a - t0) / (t1 - t0)) * 300], ["y", 2], ["width", Math.max(2, ((b - a) / (t1 - t0)) * 300)], ["height", 10], ["rx", 2], ["fill", colorOf(c)]]) seg.setAttribute(k, v);
        svg.append(seg);
      });
      return h("div", { class: "cmp-door" }, h("span", { class: `chip chip-${c}` }, c), svg, h("span", { class: "tiny faint" }, `${opens} open${opens === 1 ? "" : "s"}`));
    }).filter(Boolean);
    return lines.length ? h("div", { class: "chart-card", style: { gridColumn: "1 / -1" } }, h("h4", null, "Door opens"), lines) : null;
  }

  async draw() {
    let Plotly;
    try { Plotly = await whenPlotly(); } catch { return; }
    const chosen = this.chosen();
    for (const s of this.shown || []) {
      const el = document.getElementById(`cmp-${s.key}`);
      if (!el) continue;
      const traces = [];
      for (const c of chosen) {
        if (s.col) {
          const d = this.pred?.[c];
          if (!d) continue;
          if (d.unc?.length && `${s.col}_p05` in d.unc[0]) {
            const x = d.unc.map((r) => r.time_h);
            traces.push({ x, y: d.unc.map((r) => r[`${s.col}_p95`]), type: "scatter", mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false });
            traces.push({ x, y: d.unc.map((r) => r[`${s.col}_p05`]), type: "scatter", mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: bandOf(c), hoverinfo: "skip", showlegend: false });
          }
          if (d.pred?.length && s.col in d.pred[0]) traces.push({ x: d.pred.map((r) => r.time_h), y: d.pred.map((r) => r[s.col]), type: "scatter", mode: "lines", name: `${c} forecast`, line: { color: colorOf(c), width: 2 } });
          continue;
        }
        const { rows } = this.window(c);
        const pts = rows.map((r) => [new Date(rowT(r)), s.get(r)]).filter((p) => p[1] !== null);
        if (!pts.length) continue;
        const stride = Math.ceil(pts.length / 800);
        const ds = pts.filter((_, i) => i % stride === 0);
        traces.push({ x: ds.map((p) => p[0]), y: ds.map((p) => p[1]), type: "scatter", mode: "lines", name: `${c} measured`, line: { color: colorOf(c), width: 2 }, hovertemplate: `${c} %{y:.1f} ${s.unit}<extra></extra>` });
        if (s.step) {
          const sp = rows.map((r) => [new Date(rowT(r)), num(tfield(r, "temperature_setpoint_c"))]).filter((p) => p[1] !== null);
          const st = sp.filter((p, i) => i === 0 || i === sp.length - 1 || p[1] !== sp[i - 1][1]);
          if (st.length) traces.push({ x: st.map((p) => p[0]), y: st.map((p) => p[1]), type: "scatter", mode: "lines", name: `${c} setpoint`, line: { color: colorOf(c), width: 1.5, dash: "dash", shape: "hv" }, hovertemplate: `${c} setpoint %{y:.1f} °C<extra></extra>` });
        }
      }
      const layout = plotLayout({ showlegend: false, margin: { l: 40, r: 10, t: 6, b: 28 }, xaxis: s.col ? { title: { text: "hours", font: { size: 10 } } } : { type: "date", tickformat: "%H:%M" } });
      if (!traces.length) { clear(el, h("div", { class: "empty" }, "No data.")); continue; }
      if (el.firstChild?.classList?.contains("empty")) el.replaceChildren();
      await Plotly.react(el, traces, layout, PLOT_CONFIG);
    }
  }
}
