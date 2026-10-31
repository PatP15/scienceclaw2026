// Section 3: NVIDIA Molecular Explorer - cached ALCHEMI/AIMNet2 results (never fabricated).
import { get, getText } from "./api.js";
import { h, clear, fmt, parseCSV, provBadge, statusBadge, plotLayout, PLOT_CONFIG, whenPlotly, COLORS, renderValue, num } from "./util.js";

const ACID_COLORS = { acetic_acid: COLORS.acetic, lactic_acid: COLORS.lactic };
const pretty = (s) => String(s || "").replace(/_/g, " ");
const FILES = "/api/v1/molecular/files/";

// Run facts that are not (yet) in analysis_summary.json; copied from data/alchemi/environment/report.md
// (generated 2026-10-31T04:45:01Z by fermentai.molecular.alchemi_runner report). That file is not served
// by the API, so the dashboard carries this sourced excerpt. Shown only when the digest's GPU matches.
const RUN_REPORT = {
  source: "data/alchemi/environment/report.md",
  gpu: "NVIDIA RTX A6000",
  instance: "Brev hyperstack_A6000 (fermentai-gpu), created 2026-10-31T03:36:39Z, deleted 04:44:50Z",
  cost: "≈ $0.68 (1.14 h × $0.60/h list price; estimate — the Brev invoice is authoritative)",
  wall: "optimisations 21.5 min, MD 154.3 min (summed per-run wall time)",
};

/** JSON that may contain NaN/Infinity literals (Python json.dump default) -> parsed with nulls. */
function parseLooseJSON(text) {
  return JSON.parse(text.replace(/([:,[\s])-?(?:NaN|Infinity)(?=[,\]}\s])/g, "$1null"));
}

export class MolecularPanel {
  constructor() {
    this.body = document.getElementById("mol-body");
    this.loaded = false;
    document.getElementById("mol-reload").addEventListener("click", () => this.load());
  }
  show() { if (!this.loaded) this.load(); else this.resize(); }
  hide() {}
  resize() {
    if (!window.Plotly) return;
    for (const id of ["mol-assoc-chart", "mol-md-chart"]) { const el = document.getElementById(id); if (el) try { window.Plotly.Plots.resize(el); } catch { /* */ } }
  }

  /** Summary endpoint first; if it fails (e.g. HTTP 500 on NaN in the digest), read the file directly. */
  async fetchDigest() {
    let degraded = null;
    try {
      const s = await get("/api/v1/molecular/summary");
      if (!s || !s.available) return { unavailable: s?.reason || "No ALCHEMI analysis files have been synced yet." };
      const key = Object.keys(s.summaries || {}).find((k) => /analysis_summary\.json$/.test(k));
      return { digest: key ? s.summaries[key] : null, files: s.files || [], degraded };
    } catch (e) {
      degraded = `The molecular summary endpoint failed (${e.status ? `HTTP ${e.status}` : e.message}); reading analysis_summary.json directly.`;
    }
    try {
      const digest = parseLooseJSON(await getText(`${FILES}analysis_summary.json`));
      const files = Object.values(digest.files || {}).map((p) => {
        const name = String(p).split("/").pop();
        return { path: name, name, url: FILES + name, suffix: name.slice(name.lastIndexOf(".")), bytes: null };
      });
      files.unshift({ path: "analysis_summary.json", name: "analysis_summary.json", url: `${FILES}analysis_summary.json`, suffix: ".json", bytes: null });
      return { digest, files, degraded };
    } catch (e2) {
      return { unavailable: `${degraded || ""} The analysis file could not be read either (${e2.message}).` };
    }
  }

  async load() {
    this.loaded = true;
    clear(this.body, h("div", { class: "card muted" }, "Loading ALCHEMI summary…"));
    const { digest, files = [], degraded, unavailable } = await this.fetchDigest();
    if (unavailable || !digest) return this.placeholder(unavailable || "analysis_summary.json missing.");
    const fileUrl = (name) => (files.find((f) => f.path === name || f.name === name) || {}).url;
    const prov = digest.provenance || "";
    const isReal = /^real/i.test(String(prov));
    const realBadge = () => (isReal ? provBadge(prov, "computed · NVIDIA GPU") : provBadge(prov || "unknown"));
    const v = digest.versions || {}, m = digest.method || {}, md = digest.md || {};

    // ---------------------------------------------------------------- validation vs published experiment
    const validation = experimentCheck(digest.campaign2);

    // ---------------------------------------------------------------- provenance + caveat
    const showReport = isReal && String(v.gpu || "").includes("A6000");
    const xc = digest.crosscheck_upstream_aimnet;
    const provCard = h("div", { class: "card" },
      h("h3", null, "Computation provenance ", realBadge()),
      isReal ? h("p", { class: "small", title: `stored provenance: ${prov}` }, h("b", null, `Computed on ${v.gpu || "an NVIDIA GPU"} via ALCHEMI`),
        validation ? " — compared against published experiment: ΔpKa, dimer energy" : "")
        : h("p", { class: "small" }, prov || "provenance not recorded"),
      validation,
      h("dl", { class: "kv" },
        kv("GPU", v.gpu ? `${v.gpu}${v.gpu_memory_gb ? ` (${Math.round(v.gpu_memory_gb)} GB, sm ${v.gpu_capability || "?"})` : ""}` : null),
        kv("Toolkit", ["nvalchemi-toolkit", "nvalchemi-toolkit-ops", "aimnet", "warp-lang", "torch"].filter((k) => v[k]).map((k) => `${k} ${v[k]}`).join(" · ")),
        kv("Checkpoint", m.checkpoint || (Array.isArray(digest.model) ? digest.model.join(", ") : digest.model)),
        kv("Optimisations", `${digest.n_calculations ?? "?"} (${digest.n_converged ?? "?"} converged, ${digest.n_failed ?? "?"} failed) · ${m.optimizer || ""}${m.fmax_threshold_ev_per_angstrom ? ` fmax ≤ ${m.fmax_threshold_ev_per_angstrom} eV/Å` : ""}`),
        kv("MD runs", md.n_runs !== undefined ? `${md.n_runs} (${md.n_completed ?? "?"} completed)` : null),
        xc ? kv("Cross-check", `vs upstream aimnet calculator on ${xc.n} structures: max |ΔE| ${fmt(xc.max_abs_delta_energy_ev * 1000, 1)} meV, max |ΔF| ${fmt(xc.max_abs_force_diff_ev_per_angstrom, 4)} eV/Å`) : null,
        showReport ? kv("GPU cost", RUN_REPORT.cost) : null,
        showReport ? kv("Instance", RUN_REPORT.instance) : null,
        showReport ? kv("Wall time", RUN_REPORT.wall) : null),
      showReport ? h("div", { class: "tiny faint" }, `Cost/instance lines: ${RUN_REPORT.source}.`) : null,
      digest.caveat ? h("div", { class: "stale-banner", style: { marginTop: "8px", background: "rgba(232,180,90,.1)", borderColor: "rgba(232,180,90,.5)", color: "#f3d39b" } }, "Caveat: ", digest.caveat) : null,
      degraded ? h("div", { class: "tiny warn-text", style: { marginTop: "6px" } }, "⚠ ", degraded) : null,
      h("details", null, h("summary", null, "Full method & version record"), renderValue({ method: m, versions: v })));

    // ---------------------------------------------------------------- association energies
    const clusters = Array.isArray(digest.lowest_energy_clusters) ? digest.lowest_energy_clusters : [];
    const energyCard = h("div", { class: "card" },
      h("h3", null, "Acid–water association energy (n = 1, 3, 6) ", realBadge()),
      h("div", { class: "chart", id: "mol-assoc-chart", style: { height: "220px" } }),
      clusters.length ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, h("th", null, "Acid"), h("th", { class: "num" }, "n H₂O"), h("th", { class: "num" }, "ΔE assoc (kcal/mol)"),
          h("th", { class: "num" }, "per H₂O"), h("th", { class: "num" }, "acid→water network"), h("th", { class: "num" }, "H-bonds (inter)"))),
        h("tbody", null, clusters.map((r) => h("tr", null,
          h("td", null, h("span", { style: { color: ACID_COLORS[r.acid] || COLORS.text } }, "■ "), pretty(r.acid)),
          h("td", { class: "num" }, r.n_water), h("td", { class: "num" }, fmt(r.dE_assoc_kcal_mol, 2)),
          h("td", { class: "num" }, fmt(r.dE_assoc_per_water_kcal_mol, 2)), h("td", { class: "num" }, fmt(r.dE_acid_to_water_network_kcal_mol, 2)),
          h("td", { class: "num" }, r.n_hbonds ?? "—", r.n_intermolecular_hbonds !== undefined && r.n_intermolecular_hbonds !== null ? ` (${r.n_intermolecular_hbonds})` : "")))))) :
        h("div", { class: "empty" }, "No acid–water association energies in the digest."),
      digest.definitions ? h("details", null, h("summary", null, "Definitions"), renderValue(digest.definitions)) : null);

    // ---------------------------------------------------------------- deprotonation
    const dep = Array.isArray(digest.deprotonation) ? digest.deprotonation : [];
    const depCard = h("div", { class: "card span2" },
      h("h3", null, "Deprotonation energetics (gas-phase microsolvated clusters) ", realBadge()),
      h("p", { class: "tiny faint" }, "Cluster-phase 0 K electronic energies — a relative acidity/solvation signal, not a solution-phase pKa or pH prediction. Ion pairs that collapsed back to the neutral acid during optimisation are counted, not hidden."),
      dep.length ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, h("th", null, "Acid"), h("th", { class: "num" }, "n H₂O"), h("th", { class: "num" }, "ΔE → separated ions (kcal/mol)"),
          h("th", { class: "num" }, "ΔE → ion pair (kcal/mol)"), h("th", null, "Ion-pair type"), h("th", { class: "num" }, "Ion pairs surviving / starts"),
          h("th", { class: "num" }, "Sampling gap neutral / anion"))),
        h("tbody", null, dep.map((r) => h("tr", null,
          h("td", null, h("span", { style: { color: ACID_COLORS[r.acid] || COLORS.text } }, "■ "), pretty(r.acid)),
          h("td", { class: "num" }, r.n_water),
          h("td", { class: "num" }, fmt(r.dE_deprot_separated_ions_kcal_mol, 1)),
          h("td", { class: "num" }, num(r.dE_deprot_ion_pair_kcal_mol) === null ? h("span", { class: "faint" }, "none survived") : fmt(r.dE_deprot_ion_pair_kcal_mol, 1)),
          h("td", null, r.ion_pair_type ? pretty(r.ion_pair_type) : "—"),
          h("td", { class: "num" }, num(r.n_ion_pair_starts) === null ? "—" : `${fmt(r.n_ion_pairs_surviving, 0)} / ${fmt(r.n_ion_pair_starts, 0)}`),
          h("td", { class: "num" }, `${fmt(r.neutral_gap_to_2nd_kcal_mol, 2)} / ${fmt(r.anion_gap_to_2nd_kcal_mol, 2)}`)))))) :
        h("div", { class: "empty" }, "No deprotonation results in the digest."),
      fileUrl("deprotonation_energy.html") || dep.length ? h("p", { class: "tiny faint" }, "Interactive chart: open “Deprotonation” in the viewer below.") : null);

    // ---------------------------------------------------------------- MD H-bonds vs temperature
    const mdCard = h("div", { class: "card" },
      h("h3", null, "MD: acid–water H-bonds vs temperature ", realBadge()),
      h("p", { class: "tiny faint" }, `${md.n_runs ?? "?"} short Langevin MD runs (ALCHEMI/AIMNet2 on the GPU; mean over the second half of each run, seeds pooled; bars = min–max across seeds). Short trajectories: qualitative trend only.`),
      h("div", { class: "chart", id: "mol-md-chart", style: { height: "240px" } }));

    // ---------------------------------------------------------------- coupling to the kinetic model
    const couplingCard = h("div", { class: "card" }, h("h3", null, "Coupling into the fermentation model ", provBadge("estimated", "alchemi-informed")), h("div", { id: "mol-coupling" }, h("span", { class: "muted small" }, "Loading…")));

    // ---------------------------------------------------------------- interactive viewers
    const views = [["molecular_explorer.html", "3D structures"], ["association_energy.html", "Association energy"], ["deprotonation_energy.html", "Deprotonation"], ["md_hbonds_vs_temperature.html", "MD H-bonds"]]
      .filter(([f]) => fileUrl(f));
    const slot = h("div", { id: "mol-explorer-slot" }, h("div", { class: "empty" }, "Choose a viewer. Each is a self-contained Plotly page (several MB) generated from the real outputs and served from this Mac."));
    const explorerCard = h("div", { class: "card span2" },
      h("h3", null, "Interactive viewers ", realBadge()),
      views.length ? h("div", { class: "seg", role: "radiogroup", style: { marginBottom: "8px", flexWrap: "wrap" } }, views.map(([f, label]) => h("button", { class: "seg-btn", type: "button", role: "radio", "aria-checked": "false", onclick: (ev) => {
        ev.currentTarget.parentElement.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-checked", String(b === ev.currentTarget)));
        clear(slot, h("iframe", { class: "explorer-frame", src: fileUrl(f), title: label, loading: "lazy" }));
      } }, label))) : h("div", { class: "empty" }, "No HTML viewers generated yet."),
      slot);

    const hbCard = h("div", { class: "card" }, h("h3", null, "Hydrogen-bond summary (optimised structures)"), h("div", { id: "mol-hb" }, h("span", { class: "muted small" }, "Loading…")));
    const filesCard = h("div", { class: "card" }, h("details", null, h("summary", null, `Result files (${files.length})`),
      h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, files.map((f) => h("li", null, h("a", { href: f.url, target: "_blank" }, f.path), f.bytes ? h("span", { class: "faint" }, ` · ${(f.bytes / 1024).toFixed(0)} kB`) : null)))));

    clear(this.body, h("div", { class: "mol-grid" }, provCard, energyCard, mdCard, couplingCard, depCard, explorerCard, hbCard, filesCard));
    this.drawAssoc(clusters);
    this.drawMD(fileUrl("md_summary.csv"), md);
    this.loadHbonds(fileUrl("hbond_summary.csv"), clusters);
    this.loadCoupling();
  }

  async drawAssoc(clusters) {
    const el = document.getElementById("mol-assoc-chart");
    if (!clusters.length) { el.remove(); return; }
    let Plotly;
    try { Plotly = await whenPlotly(); } catch (e) { clear(el, h("div", { class: "empty" }, e.message)); return; }
    const acids = [...new Set(clusters.map((r) => r.acid))];
    const traces = acids.map((a) => {
      const rows = clusters.filter((r) => r.acid === a && num(r.dE_assoc_kcal_mol) !== null).sort((x, y) => x.n_water - y.n_water);
      return { type: "bar", name: pretty(a), x: rows.map((r) => `${r.n_water} H₂O`), y: rows.map((r) => r.dE_assoc_kcal_mol),
        marker: { color: ACID_COLORS[a] || COLORS.accent, line: { color: COLORS.surface, width: 2 } },
        hovertemplate: `${pretty(a)} · %{x}: %{y:.2f} kcal/mol<extra></extra>` };
    });
    await Plotly.react(el, traces, plotLayout({ barmode: "group", showlegend: true, hovermode: "closest",
      yaxis: { title: { text: "ΔE assoc (kcal/mol)", font: { size: 10 } } }, margin: { l: 50, r: 10, t: 24, b: 30 } }), PLOT_CONFIG);
  }

  async drawMD(url, md) {
    const el = document.getElementById("mol-md-chart");
    let rows = [];
    try { if (url) rows = parseCSV(await getText(url)); } catch { rows = []; }
    if (!rows.length && Array.isArray(md.runs)) rows = md.runs;
    rows = rows.filter((r) => r.status === "completed" && num(r.mean_acid_water_hbonds_second_half) !== null);
    if (!rows.length) { clear(el, h("div", { class: "empty" }, "md_summary.csv not available.")); return; }
    let Plotly;
    try { Plotly = await whenPlotly(); } catch (e) { clear(el, h("div", { class: "empty" }, e.message)); return; }
    const groups = new Map();
    for (const r of rows) {
      const k = r.start_structure;
      if (!groups.has(k)) groups.set(k, new Map());
      const g = groups.get(k);
      const T = Number(r.temperature_k);
      if (!g.has(T)) g.set(T, []);
      g.get(T).push(Number(r.mean_acid_water_hbonds_second_half));
    }
    const traces = [...groups.entries()].sort().map(([k, g]) => {
      const acid = k.startsWith("lactic") ? "lactic_acid" : "acetic_acid";
      const anion = k.includes("anion");
      const n = (k.match(/__n(\d+)/) || [])[1];
      const Ts = [...g.keys()].sort((a, b) => a - b);
      const mean = Ts.map((T) => g.get(T).reduce((s, x) => s + x, 0) / g.get(T).length);
      const lo = Ts.map((T, i) => mean[i] - Math.min(...g.get(T)));
      const hi = Ts.map((T, i) => Math.max(...g.get(T)) - mean[i]);
      const name = `${anion ? "anion " : ""}${acid.split("_")[0]} · ${n} H₂O`;
      return { x: Ts.map((T) => T - 273.15), y: mean, type: "scatter", mode: "lines+markers", name,
        error_y: { type: "data", symmetric: false, array: hi, arrayminus: lo, color: ACID_COLORS[acid], thickness: 1, width: 3 },
        line: { color: ACID_COLORS[acid], width: 2, dash: anion ? "dot" : n === "3" ? "solid" : "dash" },
        marker: { size: 8, color: ACID_COLORS[acid], line: { color: COLORS.surface, width: 2 }, symbol: anion ? "diamond" : "circle" },
        hovertemplate: `${name}: %{y:.2f} H-bonds at %{x:.0f} °C<extra></extra>` };
    });
    await Plotly.react(el, traces, plotLayout({ showlegend: true, hovermode: "closest", legend: { orientation: "h", y: -0.32, font: { size: 9, color: COLORS.text2 } },
      margin: { l: 44, r: 10, t: 8, b: 78 }, xaxis: { title: { text: "MD thermostat temperature (°C)", font: { size: 10 } } },
      yaxis: { title: { text: "acid–water H-bonds", font: { size: 10 } } } }), PLOT_CONFIG);
  }

  async loadCoupling() {
    const slot = document.getElementById("mol-coupling");
    let rep;
    try { rep = await get("/api/v1/predictions/molecular_coupling_report.json"); }
    catch (e) { clear(slot, h("div", { class: "empty" }, `No coupling report (data/predictions/molecular_coupling_report.json): ${e.message}`)); return; }
    const per = rep.per_chamber || {};
    const first = Object.values(per)[0] || {};
    const same = Object.values(per).every((c) => JSON.stringify(c.parameters) === JSON.stringify(first.parameters));
    const entries = same ? [["A & B", first]] : Object.entries(per);
    clear(slot,
      h("p", { class: "tiny faint" }, "Which kinetic-model parameters the ALCHEMI descriptors moved, relative to the literature value. Bounded, deliberately small nudges — the fermentation model, not ALCHEMI, predicts pH and rise."),
      entries.map(([cid, c]) => h("div", null,
        h("div", { class: "small", style: { margin: "4px 0" } }, h("b", null, `Chamber ${cid}`), " ", statusBadge(c.status || "?", ["complete", "applied"].includes(c.status) ? "good" : "warn")),
        h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
          h("thead", null, h("tr", null, h("th", null, "Parameter"), h("th", { class: "num" }, "Literature"), h("th", { class: "num" }, "Used"), h("th", { class: "num" }, "Change"), h("th", null, "Source"))),
          h("tbody", null, Object.entries(c.parameters || {}).map(([k, p]) => {
            const lit = num(p.literature_value), val = num(p.value);
            const d = lit !== null && val !== null ? val - lit : null;
            const moved = d !== null && Math.abs(d) > 1e-12;
            return h("tr", null, h("td", null, pretty(k), p.mapping ? h("div", { class: "tiny faint" }, p.mapping) : null),
              h("td", { class: "num" }, fmt(lit, 3)), h("td", { class: "num" }, fmt(val, 3)),
              h("td", { class: `num ${moved ? "" : "faint"}` }, moved ? `${d > 0 ? "+" : ""}${fmt(d, 3)}${lit ? ` (${d > 0 ? "+" : ""}${fmt((d / lit) * 100, 0)} %)` : ""}` : "unchanged"),
              h("td", null, p.source === "alchemi-informed" ? provBadge("estimated", "alchemi-informed") : statusBadge(p.source || "literature", "info")));
          })))),
        (c.notes || []).length ? h("div", { class: "tiny warn-text" }, "Notes: ", c.notes.join("; ")) : null,
        c.descriptor_provenance?.note ? h("div", { class: "tiny faint" }, c.descriptor_provenance.note) : null)));
  }

  async loadHbonds(url, clusters) {
    const slot = document.getElementById("mol-hb");
    if (!url) { clear(slot, h("div", { class: "empty" }, "hbond_summary.csv not available yet.")); return; }
    try {
      const rows = parseCSV(await getText(url));
      const best = new Set(clusters.map((c) => c.id));
      const shown = best.size ? rows.filter((r) => best.has(r.id)) : rows.slice(0, 20);
      const cols = ["id", "n_water", "n_hbonds", "n_intermolecular", "n_water_to_acid", "n_acid_to_water", "min_d_HA_angstrom"].filter((c) => c in (shown[0] || {}));
      clear(slot,
        h("p", { class: "tiny faint" }, best.size ? `Lowest-energy cluster per acid × n(H₂O) (${shown.length} of ${rows.length} structures).` : `${rows.length} structures.`),
        h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
          h("thead", null, h("tr", null, cols.map((c) => h("th", { class: c === "id" ? "" : "num" }, pretty(c))))),
          h("tbody", null, shown.map((r) => h("tr", null, cols.map((c) => h("td", { class: typeof r[c] === "number" ? "num" : "" }, typeof r[c] === "number" && !Number.isInteger(r[c]) ? fmt(r[c], 2) : String(r[c] ?? "—")))))))));
    } catch (e) { clear(slot, h("div", { class: "empty" }, `Could not read hbond_summary.csv (${e.message}).`)); }
  }

  placeholder(reason) {
    clear(this.body, h("div", { class: "card placeholder-hero" },
      h("div", { class: "big-ico" }, "⚛"),
      h("h3", null, "NVIDIA ALCHEMI results not yet available on this Mac"),
      h("p", { class: "muted" }, reason),
      h("p", { class: "small muted" }, "Once the remote GPU run is synced (scripts/collect_brev_results.sh) and analysed, this panel shows the acid–water association-energy table, per-cluster optimised 3D structures, hydrogen-bond counts and the GPU / software provenance."),
      h("p", { class: "small" }, statusBadge("no values shown", "info"), " Nothing here is estimated or fabricated — the panel stays empty until real outputs exist.")));
  }
}

function kv(k, val) { return val === undefined || val === null || val === "" ? null : [h("dt", null, k), h("dd", null, String(val))]; }

/** Campaign-2 comparison with published experiment (ΔpKa lactic−acetic, acetic-acid dimer energy).
 *  Reports agreement honestly: values, experiment, deviation / range - no pass/fail spin. */
function experimentCheck(c2) {
  if (!c2 || typeof c2 !== "object") return null;
  const pk = c2.relative_pka_estimate, ap = c2.acid_pairs;
  const rows = [];
  if (pk && num(pk.value) !== null && num(pk.experiment) !== null) {
    const dev = pk.value - pk.experiment, sig = num(pk.uncertainty);
    const z = sig ? Math.abs(dev) / sig : null;
    rows.push(h("tr", null, h("td", null, "ΔpKa (lactic − acetic)"),
      h("td", { class: "num" }, `${fmt(pk.value, 1)}${sig !== null ? ` ± ${fmt(sig, 1)}` : ""}`),
      h("td", { class: "num" }, fmt(pk.experiment, 2)),
      h("td", null, Math.sign(pk.value) === Math.sign(pk.experiment) ? "sign correct; " : "sign wrong; ",
        `off by ${fmt(dev, 1)} pKa units${z !== null ? ` (${fmt(z, 1)} σ)` : ""}`)));
  }
  if (ap && num(ap.acetic_dimer_kcal) !== null && Array.isArray(ap.acetic_dimer_literature_kcal)) {
    const [lo, hi] = [Math.min(...ap.acetic_dimer_literature_kcal), Math.max(...ap.acetic_dimer_literature_kcal)];
    const inside = ap.acetic_dimer_kcal >= lo && ap.acetic_dimer_kcal <= hi;
    rows.push(h("tr", null, h("td", null, "Acetic-acid dimer binding (kcal/mol)"),
      h("td", { class: "num" }, fmt(ap.acetic_dimer_kcal, 1)), h("td", { class: "num" }, `${fmt(lo, 0)} to ${fmt(hi, 0)}`),
      h("td", null, inside ? h("span", { class: "verdict-good" }, "within published range") : h("span", { class: "verdict-bad" }, "outside published range"))));
  }
  if (!rows.length) return null;
  return h("div", { style: { margin: "6px 0" } },
    h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, h("th", null, "Check vs published experiment"), h("th", { class: "num" }, "Computed"), h("th", { class: "num" }, "Experiment"), h("th", null, "Agreement"))),
      h("tbody", null, rows))),
    pk?.note ? h("div", { class: "tiny faint" }, "ΔpKa: ", pk.note, ".") : null);
}
