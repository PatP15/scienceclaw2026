// Digital twin, embedded per station in the lab detail view (#lab/<id>, collapsed by default):
// that chamber's model forecast (median + 5-95 % band) vs its own observed telemetry.
import { get, getText } from "./api.js";
import { h, clear, fmt, num, parseCSV, provBadge, normProv, PROV_LABEL, statusBadge, plotLayout, PLOT_CONFIG, whenPlotly, COLORS, timeAgo } from "./util.js";

const ALL_CHAMBERS = ["A", "B"];
const CHARTS = [
  { key: "ph", title: "pH — predicted vs observed", unit: "pH", col: "ph", obs: (r) => num(r.ph), obsLabel: "pH" },
  { key: "expansion", title: "Rise (expansion) — predicted vs observed", unit: "× initial volume", col: "expansion_ratio", obsRatio: true, obsLabel: "starter height ÷ first height" },
  { key: "lactic", title: "Lactic acid (estimate)", unit: "g / kg dough", col: "lactic_acid" },
  { key: "acetic", title: "Acetic acid (estimate)", unit: "g / kg dough", col: "acetic_acid" },
  { key: "co2", title: "Cumulative CO₂ (model output)", unit: "g / kg dough", col: "co2_cumulative", sub: "Modelled production — the BME680 gas sensor does not measure CO₂." },
];

function findFile(files, re) { return files.find((f) => re.test(f.path)); }

export class TwinPanel {
  constructor(store) {
    this.store = store;
    this.root = document.getElementById("lab-twin");
    this.chartsEl = document.getElementById("twin-charts");
    this.statusEl = document.getElementById("twin-status");
    this.chamber = null;
    this.chambers = [];
    this.loaded = false;
    this.visible = false;
    document.getElementById("twin-reload").addEventListener("click", () => this.load(true));
    // Refresh the observed overlay occasionally while the section is open.
    setInterval(() => { if (this.visible && this.loaded) this.loadObserved().then(() => { this.renderStatus(); this.draw(); }); }, 30000);
  }

  /** Called by the lab detail view: which station the section shows, and whether it is open. */
  setChamber(c) {
    if (c === this.chamber) return;
    this.chamber = c; this.chambers = [c];
    delete this.chartsEl.dataset.built;
    clear(this.chartsEl);
    if (this.loaded) { this.needsRefresh = true; if (this.visible) this.refresh(); }
  }
  setOpen(open) {
    this.visible = open;
    if (!open) return;
    if (!this.loaded) this.load(); else if (this.needsRefresh) this.refresh(); else this.resize();
  }
  async refresh() { this.needsRefresh = false; await this.loadObserved(); this.renderStatus(); await this.draw(); }
  resize() { if (window.Plotly) document.querySelectorAll("#twin-charts .chart").forEach((el) => { try { window.Plotly.Plots.resize(el); } catch { /* not drawn */ } }); }

  async load(force = false) {
    this.loaded = true;
    clear(this.statusEl, h("div", { class: "card muted" }, "Loading predictions…"));
    let listing;
    try { listing = await get("/api/v1/predictions"); }
    catch (e) { clear(this.statusEl, h("div", { class: "card" }, statusBadge("unavailable", "bad"), ` Could not list predictions: ${e.message}`)); return; }
    const files = listing.files || [];
    this.files = files;
    const want = {};
    for (const c of ALL_CHAMBERS) {
      want[`pred${c}`] = findFile(files, new RegExp(`(^|/)chamber_${c}_prediction\\.csv$`));
      want[`unc${c}`] = findFile(files, new RegExp(`(^|/)chamber_${c}_(uncertainty|envelope)\\.csv$`));
      want[`sum${c}`] = findFile(files, new RegExp(`(^|/)chamber_${c}_summary\\.json$`));
    }
    want.manifest = findFile(files, /(^|\/)run_manifest\.json$/);
    want.compare = findFile(files, /(^|\/)comparison_summary\.json$/);
    want.calib = files.find((f) => /calibrat/i.test(f.path) && /\.(json)$/.test(f.path));
    const fetchOne = async (f, kind) => {
      if (!f) return null;
      try { return kind === "csv" ? parseCSV(await getText(f.url, { timeoutMs: 30000 })) : await get(f.url); }
      catch (e) { return { __error: e.message }; }
    };
    const entries = await Promise.all(Object.entries(want).map(async ([k, f]) => [k, await fetchOne(f, f && f.suffix === ".csv" ? "csv" : "json")]));
    this.data = Object.fromEntries(entries);
    this.wantFiles = want;
    await this.loadObserved();
    this.renderStatus();
    await this.draw();
  }

  async loadObserved() {
    this.obs = {};
    await Promise.all(this.chambers.map(async (c) => {
      try { this.obs[c] = await get(`/api/v1/chambers/${c}/telemetry?limit=5000`); } catch { this.obs[c] = []; }
    }));
  }

  observedSeries(c, spec) {
    const rows = (this.obs[c] || []).filter((r) => r.timestamp_utc || r.received_at_utc);
    if (!rows.length) return null;
    const t0 = Date.parse(rows[0].timestamp_utc || rows[0].received_at_utc);
    let h0 = null;
    const xs = [], ys = [];
    for (const r of rows) {
      let y;
      if (spec.obsRatio) {
        const hmm = num(r.starter_height_mm);
        if (hmm === null) continue;
        if (h0 === null) h0 = hmm;
        y = h0 > 0 ? hmm / h0 : null;
      } else y = spec.obs ? spec.obs(r) : null;
      if (y === null) continue;
      xs.push((Date.parse(r.timestamp_utc || r.received_at_utc) - t0) / 3.6e6);
      ys.push(y);
    }
    if (!xs.length) return null;
    const provs = [...new Set(rows.map((r) => r.source).filter(Boolean))];
    return { xs, ys, prov: provs.join("+") || "unknown", label: provs.map((p) => PROV_LABEL[normProv(p, "device")] || p).join(" + ") || "unknown", n: xs.length };
  }

  renderStatus() {
    const d = this.data;
    const have = this.chambers.filter((c) => Array.isArray(d[`pred${c}`]) && d[`pred${c}`].length);
    const man = d.manifest && !d.manifest.__error ? d.manifest : null;
    const cards = [];
    cards.push(h("div", { class: "card" },
      h("b", null, "Model run "), provBadge(man?.provenance || "simulated"), " ",
      have.length ? `${have.map((c) => `Chamber ${c}`).join(" + ")} loaded` : statusBadge("no prediction CSVs yet", "warn"),
      man ? h("div", { class: "tiny faint" }, `${man.generator || ""} · ${man.model || ""} · generated ${man.generated_utc ? timeAgo(man.generated_utc) : "?"} · ${man.n_uncertainty_samples ?? "?"} Monte-Carlo samples (seed ${man.seed ?? "?"})`) : null,
      man?.note ? h("div", { class: "tiny faint" }, man.note) : null));

    // Calibration status: honest about whether a fit to observed data exists.
    const cal = d.calib && !d.calib.__error ? d.calib : null;
    const nObs = (c, key) => (this.obs[c] || []).filter((r) => num(r[key]) !== null).length;
    const obsTxt = this.chambers.map((c) => `${c}: ${nObs(c, "ph")} pH, ${nObs(c, "starter_height_mm")} height`).join(" · ");
    cards.push(h("div", { class: "card" },
      h("b", null, "Model calibration "),
      cal ? statusBadge(cal.success === false ? "fit failed" : "fitted to observations", cal.success === false ? "bad" : "good") : statusBadge("not fitted to observations", "warn"),
      h("div", { class: "tiny faint" }, cal
        ? `${this.wantFiles.calib.path}: ${cal.n_observations ?? "?"} observations; RMSE ${cal.rmse ? Object.entries(cal.rmse).map(([k, v]) => `${k} ${fmt(v, 3)}`).join(", ") : "n/a"}`
        : "Predictions use the literature-prior parameters in configs/kinetics.yaml (no fit to this run's measurements yet)."),
      h("div", { class: "tiny faint" }, `Observed points available — ${obsTxt}`)));
    clear(this.statusEl, cards);
  }

  async draw() {
    let Plotly;
    try { Plotly = await whenPlotly(); }
    catch (e) { clear(this.chartsEl, h("div", { class: "empty" }, e.message)); return; }
    const d = this.data;
    if (!this.chartsEl.dataset.built) {
      clear(this.chartsEl,
        h("div", { class: "card", style: { gridColumn: "1 / -1", padding: "6px 12px" } },
          h("span", { class: "small legend-strip" },
            h("span", { style: { color: this.chamber === "B" ? COLORS.B : COLORS.A, fontWeight: 700 } }, `━ ${this.chamber} predicted`),
            h("span", { class: "muted" }, "▒ 5–95 % band"),
            h("span", { class: "muted" }, "● observed (h since first record)"),
            provBadge("simulated"))),
        CHARTS.map((s) => h("div", { class: "chart-card" },
          h("h4", null, s.title), h("div", { class: "chart-sub" }, s.sub || s.unit),
          h("div", { class: "chart", id: `twin-${s.key}` }))));
      this.chartsEl.dataset.built = "1";
    }
    for (const spec of CHARTS) {
      const traces = [], annotations = [];
      for (const c of this.chambers) {
        const col = c === "A" ? COLORS.A : COLORS.B;
        const unc = d[`unc${c}`];
        if (Array.isArray(unc) && unc.length && `${spec.col}_p05` in unc[0]) {
          const x = unc.map((r) => r.time_h);
          traces.push({ x, y: unc.map((r) => r[`${spec.col}_p95`]), type: "scatter", mode: "lines", line: { width: 0 }, hoverinfo: "skip", showlegend: false });
          traces.push({ x, y: unc.map((r) => r[`${spec.col}_p05`]), type: "scatter", mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: c === "A" ? COLORS.Aband : COLORS.Bband, hoverinfo: "skip", showlegend: false, name: `${c} 5–95 %` });
        }
        const pred = d[`pred${c}`];
        if (Array.isArray(pred) && pred.length && spec.col in pred[0]) {
          const x = pred.map((r) => r.time_h), y = pred.map((r) => r[spec.col]);
          traces.push({ x, y, type: "scatter", mode: "lines", name: `${c} predicted`, line: { color: col, width: 2 }, hovertemplate: `${c} predicted %{y:.2f}<extra></extra>` });
          annotations.push({ x: x[x.length - 1], y: y[y.length - 1], text: `<b>${c}</b>`, showarrow: false, xanchor: "left", xshift: 3, font: { color: COLORS.text, size: 11 } });
        }
        const obs = this.observedSeries(c, spec);
        if (obs) {
          traces.push({ x: obs.xs, y: obs.ys, type: "scatter", mode: "markers", name: `${c} observed (${obs.label})`,
            marker: { color: col, size: 8, line: { color: COLORS.surface, width: 2 }, symbol: "circle" },
            hovertemplate: `${c} observed %{y:.2f} (${obs.label})<extra></extra>` });
        }
      }
      if (annotations.length === 2) {
        // Nudge the end-of-line A/B labels apart when the curves finish close together.
        const ys = traces.filter((t) => t.mode === "lines" && t.name).flatMap((t) => t.y).filter((v) => typeof v === "number");
        const range = Math.max(...ys) - Math.min(...ys) || 1;
        const [a, b] = annotations;
        if (Math.abs(a.y - b.y) < 0.06 * range) { const up = a.y >= b.y ? a : b; up.yshift = 7; (up === a ? b : a).yshift = -7; }
      }
      const el = document.getElementById(`twin-${spec.key}`);
      if (!traces.length) { clear(el, h("div", { class: "empty", style: { marginTop: "60px" } }, "No data for this variable yet.")); continue; }
      const layout = plotLayout({ showlegend: false, annotations, margin: { l: 44, r: 18, t: 6, b: 32 },
        xaxis: { title: { text: "hours", font: { size: 10 } } } });
      if (el.firstChild && el.firstChild.classList && el.firstChild.classList.contains("empty")) el.replaceChildren();
      await Plotly.react(el, traces, layout, PLOT_CONFIG);
    }
  }
}
