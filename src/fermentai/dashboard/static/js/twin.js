// Section 2: Digital Twin - model predictions (from /api/v1/predictions) vs observed telemetry.
import { get, getText } from "./api.js";
import { h, clear, fmt, num, parseCSV, provBadge, statusBadge, plotLayout, PLOT_CONFIG, whenPlotly, COLORS, timeAgo } from "./util.js";

const CHAMBERS = ["A", "B"];
const CHARTS = [
  { key: "temperature", title: "Temperature", unit: "°C", col: "temperature_c", obs: (r) => num(r.chamber_temperature_c), obsLabel: "chamber temperature" },
  { key: "ph", title: "pH — predicted vs observed", unit: "pH", col: "ph", obs: (r) => num(r.ph), obsLabel: "pH" },
  { key: "expansion", title: "Expansion — predicted vs observed", unit: "× initial volume", col: "expansion_ratio", obsRatio: true, obsLabel: "starter height ÷ first height" },
  { key: "lactic", title: "Lactic acid (estimate)", unit: "g / kg dough", col: "lactic_acid" },
  { key: "acetic", title: "Acetic acid (estimate)", unit: "g / kg dough", col: "acetic_acid" },
  { key: "co2", title: "Cumulative CO₂ (model output)", unit: "g / kg dough", col: "co2_cumulative", sub: "Modelled production — the BME680 gas sensor does not measure CO₂." },
];

function findFile(files, re) { return files.find((f) => re.test(f.path)); }

export class TwinPanel {
  constructor(store) {
    this.store = store;
    this.root = document.getElementById("tab-twin");
    this.chartsEl = document.getElementById("twin-charts");
    this.statusEl = document.getElementById("twin-status");
    this.summaryEl = document.getElementById("twin-summary");
    this.morphEl = document.getElementById("twin-morphology");
    this.loaded = false;
    this.visible = false;
    document.getElementById("twin-reload").addEventListener("click", () => this.load(true));
    // Refresh the observed overlay occasionally while the tab is open.
    setInterval(() => { if (this.visible && this.loaded) this.loadObserved().then(() => this.draw()); }, 30000);
  }

  show() { this.visible = true; if (!this.loaded) this.load(); else this.resize(); }
  hide() { this.visible = false; }
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
    for (const c of CHAMBERS) {
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
    this.renderSummary();
    this.renderMorphology();
  }

  async loadObserved() {
    this.obs = {};
    await Promise.all(CHAMBERS.map(async (c) => {
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
    return { xs, ys, prov: provs.join("+") || "unknown", n: xs.length };
  }

  renderStatus() {
    const d = this.data;
    const have = CHAMBERS.filter((c) => Array.isArray(d[`pred${c}`]) && d[`pred${c}`].length);
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
    const obsTxt = CHAMBERS.map((c) => `${c}: ${nObs(c, "ph")} pH, ${nObs(c, "starter_height_mm")} height`).join(" · ");
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
          h("span", { class: "small" },
            h("span", { style: { color: COLORS.A, fontWeight: 700 } }, "━ Chamber A predicted"), "   ",
            h("span", { style: { color: COLORS.B, fontWeight: 700 } }, "━ Chamber B predicted"), "   ",
            h("span", { class: "muted" }, "▒ 5–95 % Monte-Carlo band   ● observed telemetry (hours since first record)  "),
            provBadge("simulated"), " predictions ")),
        CHARTS.map((s) => h("div", { class: "chart-card" },
          h("h4", null, s.title), h("div", { class: "chart-sub" }, s.sub || s.unit),
          h("div", { class: "chart", id: `twin-${s.key}` }))));
      this.chartsEl.dataset.built = "1";
    }
    for (const spec of CHARTS) {
      const traces = [], annotations = [];
      for (const c of CHAMBERS) {
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
          traces.push({ x: obs.xs, y: obs.ys, type: "scatter", mode: "markers", name: `${c} observed (${obs.prov})`,
            marker: { color: col, size: 8, line: { color: "#1a1a19", width: 2 }, symbol: "circle" },
            hovertemplate: `${c} observed %{y:.2f} (${obs.prov})<extra></extra>` });
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

  renderSummary() {
    const d = this.data;
    const sums = CHAMBERS.map((c) => (d[`sum${c}`] && !d[`sum${c}`].__error ? d[`sum${c}`] : null));
    if (!sums.some(Boolean)) { clear(this.summaryEl, h("h3", null, "Prediction summary"), h("div", { class: "empty" }, "No chamber summary JSON yet.")); return; }
    const band = (s, k) => (s && Array.isArray(s[`${k}_p05_p95`]) ? h("span", { class: "tiny faint" }, ` (${fmt(s[`${k}_p05_p95`][0], 2)}–${fmt(s[`${k}_p05_p95`][1], 2)})`) : null);
    const rows = [
      ["pH at 12 h", "ph_at_12h", 2], ["pH at 16 h", "ph_at_16h", 2], ["Final pH", "final_ph", 2],
      ["Peak expansion (×)", "peak_expansion_ratio", 2], ["Peak at (h)", "peak_expansion_time_h", 1],
      ["Time to double (h)", "time_to_double_h", 1], ["Over-proof onset (h)", "overproof_onset_h", 1],
      ["Final lactic (g/kg)", "final_lactic_g_per_kg", 2], ["Final acetic (g/kg)", "final_acetic_g_per_kg", 2],
      ["Fermentation quotient", "final_fermentation_quotient", 2], ["Final CO₂ (g/kg, model)", "final_co2_g_per_kg", 1],
    ];
    const tbl = h("table", { class: "tbl" },
      h("thead", null, h("tr", null, h("th", null, "Predicted (nominal run; 5–95 % band)"), CHAMBERS.map((c, i) => h("th", { class: "num" }, h("span", { class: `chip chip-${c}` }, c), sums[i] ? ` ${sums[i].schedule?.breakpoints ? describeSchedule(sums[i].schedule) : ""}` : "")))),
      h("tbody", null, rows.map(([lbl, k, dg]) => h("tr", null, h("td", null, lbl), sums.map((s) => h("td", { class: "num" }, s ? fmt(s[k], dg) : "—", band(s, k)))))));
    const hyp = CHAMBERS.map((c, i) => sums[i] ? h("div", { class: "small", style: { marginTop: "6px" } },
      h("span", { class: `chip chip-${c}` }, c), " ", h("b", null, sums[i].hypothesis || sums[i].name || ""),
      sums[i].caveat ? h("div", { class: "tiny faint" }, "Caveat: ", sums[i].caveat) : null) : null);
    const cmp = d.compare && !d.compare.__error ? d.compare : null;
    const cmpEl = cmp ? h("div", { class: "small", style: { marginTop: "8px" } }, provBadge(cmp.provenance || "simulated"), " ",
      `Paired Monte-Carlo (${cmp.n_samples ?? "?"} draws): B − A pH at 12 h = ${fmt(cmp.ph_12h_B_minus_A_median, 2)}`,
      Array.isArray(cmp.ph_12h_B_minus_A_p05_p95) ? ` (5–95 %: ${fmt(cmp.ph_12h_B_minus_A_p05_p95[0], 2)} to ${fmt(cmp.ph_12h_B_minus_A_p05_p95[1], 2)})` : "",
      cmp.peak_time_B_minus_A_median_h !== undefined ? `; B peaks ${fmt(Math.abs(cmp.peak_time_B_minus_A_median_h), 1)} h ${cmp.peak_time_B_minus_A_median_h < 0 ? "earlier" : "later"}` : "", ".") : null;
    clear(this.summaryEl, h("h3", null, "Prediction summary ", provBadge("simulated")), h("div", { class: "tbl-wrap" }, tbl), hyp, cmpEl);
  }

  async renderMorphology() {
    let list;
    try { list = await get("/api/v1/stl"); } catch { list = { files: [] }; }
    const files = list.files || [];
    const pngs = files.filter((f) => f.suffix === ".png");
    const preferred = pngs.find((f) => /comparison/.test(f.name)) ? [pngs.find((f) => /comparison/.test(f.name))] : pngs.slice(0, 2);
    const stls = files.filter((f) => f.suffix === ".stl");
    clear(this.morphEl,
      h("h3", null, "Crumb morphology (Formlabs) ", provBadge("simulated")),
      h("p", { class: "tiny faint" }, "Stochastic pore-model STLs: illustrative hypotheses about crumb structure — not ALCHEMI predictions, and sourness does not determine crumb."),
      preferred.length ? h("div", { class: "morph-imgs" }, preferred.map((f) => h("img", { src: f.url, alt: f.name, loading: "lazy" }))) : h("div", { class: "empty" }, "No STL renders yet."),
      stls.length ? h("div", { class: "tiny faint", style: { marginTop: "6px" } }, "STL files: ", stls.map((f) => `${f.name} (${(f.bytes / 1e6).toFixed(1)} MB)`).join(", ")) : null);
  }
}

function describeSchedule(s) {
  const bp = s.breakpoints || [];
  if (!bp.length) return "";
  if (bp.length === 1) return `${fmt(bp[0][1], 0)} °C`;
  return bp.map((p) => `${fmt(p[1], 0)}°`).join("→");
}
