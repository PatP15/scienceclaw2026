// Section 3: NVIDIA Molecular Explorer - cached ALCHEMI/AIMNet2 results (never fabricated).
import { get, getText } from "./api.js";
import { h, clear, fmt, parseCSV, provBadge, statusBadge, plotLayout, PLOT_CONFIG, whenPlotly, COLORS, renderValue, num } from "./util.js";

const ACID_COLORS = { acetic_acid: COLORS.acetic, lactic_acid: COLORS.lactic };
const pretty = (s) => String(s || "").replace(/_/g, " ");

export class MolecularPanel {
  constructor() {
    this.body = document.getElementById("mol-body");
    this.loaded = false;
    document.getElementById("mol-reload").addEventListener("click", () => this.load());
  }
  show() { if (!this.loaded) this.load(); else this.resize(); }
  hide() {}
  resize() { const el = document.getElementById("mol-assoc-chart"); if (el && window.Plotly) try { window.Plotly.Plots.resize(el); } catch { /* */ } }

  async load() {
    this.loaded = true;
    clear(this.body, h("div", { class: "card muted" }, "Loading ALCHEMI summary…"));
    let s;
    try { s = await get("/api/v1/molecular/summary"); }
    catch (e) { return this.placeholder(`The local API did not return the molecular summary (${e.message}).`); }
    if (!s || !s.available) return this.placeholder(s?.reason || "No ALCHEMI analysis files have been synced yet.");
    const files = s.files || [];
    const summaries = s.summaries || {};
    const digestKey = Object.keys(summaries).find((k) => /analysis_summary\.json$/.test(k)) || Object.keys(summaries)[0];
    const digest = digestKey ? summaries[digestKey] : null;
    const fileUrl = (name) => (files.find((f) => f.path === name || f.name === name) || {}).url;

    // Real-result guard: we only show numbers when the digest says they came from a real run.
    const prov = digest?.provenance || s.provenance || "";
    const isReal = /^real/i.test(String(prov));

    const provCard = h("div", { class: "card" },
      h("h3", null, "Simulation provenance ", isReal ? provBadge("real", "real · GPU computed") : provBadge(prov || "unknown")),
      h("p", { class: "small" }, prov || "provenance not recorded"),
      digest ? provenanceList(digest) : null,
      digest?.caveat ? h("p", { class: "tiny faint", style: { marginTop: "8px" } }, "Caveat: ", digest.caveat) : null);

    const clusters = Array.isArray(digest?.lowest_energy_clusters) ? digest.lowest_energy_clusters : [];
    const tbl = clusters.length ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, h("th", null, "Acid"), h("th", { class: "num" }, "n H₂O"), h("th", { class: "num" }, "ΔE assoc (kcal/mol)"),
        h("th", { class: "num" }, "per H₂O"), h("th", { class: "num" }, "acid→water network"), h("th", { class: "num" }, "H-bonds (inter)"))),
      h("tbody", null, clusters.map((r) => h("tr", null,
        h("td", null, h("span", { style: { color: ACID_COLORS[r.acid] || COLORS.text } }, "■ "), pretty(r.acid)),
        h("td", { class: "num" }, r.n_water), h("td", { class: "num" }, fmt(r.dE_assoc_kcal_mol, 2)),
        h("td", { class: "num" }, fmt(r.dE_assoc_per_water_kcal_mol, 2)), h("td", { class: "num" }, fmt(r.dE_acid_to_water_network_kcal_mol, 2)),
        h("td", { class: "num" }, r.n_hbonds ?? "—", r.n_intermolecular_hbonds !== undefined && r.n_intermolecular_hbonds !== null ? ` (${r.n_intermolecular_hbonds})` : "")))))) :
      h("div", { class: "empty" }, "No acid–water association energies in the digest yet.");

    const energyCard = h("div", { class: "card" },
      h("h3", null, "Interaction energies — lowest-energy conformers ", isReal ? provBadge("real") : null),
      h("div", { class: "chart", id: "mol-assoc-chart", style: { height: "220px" } }),
      tbl,
      digest?.definitions ? h("details", null, h("summary", null, "Definitions"), renderValue(digest.definitions)) : null);

    const explorerUrl = fileUrl("molecular_explorer.html");
    const assocHtml = fileUrl("association_energy.html");
    const explorerCard = h("div", { class: "card span2" },
      h("h3", null, "Optimised structures (3D) ", isReal ? provBadge("real") : null),
      explorerUrl
        ? h("div", null,
          h("p", { class: "small muted" }, "Self-contained Plotly viewer generated from the optimised ALCHEMI geometries (≈5 MB, loads from this Mac)."),
          h("div", { id: "mol-explorer-slot" }, h("button", { class: "btn btn-primary", type: "button", onclick: (ev) => {
            ev.currentTarget.parentElement.replaceChildren(h("iframe", { class: "explorer-frame", src: explorerUrl, title: "ALCHEMI molecular explorer", loading: "lazy" }));
          } }, "Open 3D structure explorer")),
          assocHtml ? h("p", { class: "tiny faint" }, "Also available: ", h("a", { href: assocHtml, target: "_blank" }, "association-energy chart (HTML)")) : null)
        : h("div", { class: "empty" }, "molecular_explorer.html not generated yet (run fermentai.molecular.analysis after the GPU results are synced)."));

    const hbCard = h("div", { class: "card" }, h("h3", null, "Hydrogen-bond summary"), h("div", { id: "mol-hb" }, h("span", { class: "muted small" }, "Loading…")));
    const filesCard = h("div", { class: "card" }, h("h3", null, "Result files"),
      h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, files.map((f) => h("li", null, h("a", { href: f.url, target: "_blank" }, f.path), h("span", { class: "faint" }, ` · ${(f.bytes / 1024).toFixed(0)} kB`)))));

    const known = new Set(["provenance", "model", "method", "versions", "n_calculations", "n_converged", "n_failed", "references", "lowest_energy_clusters", "definitions", "caveat", "files"]);
    const extraCards = Object.entries(digest || {}).filter(([k, v]) => !known.has(k) && Array.isArray(v) && v.length && typeof v[0] === "object")
      .map(([k, rows]) => h("div", { class: "card span2" }, h("h3", null, pretty(k), " ", isReal ? provBadge("real") : null), recordsTable(rows)));
    clear(this.body, h("div", { class: "mol-grid" }, energyCard, provCard, explorerCard, hbCard, filesCard, extraCards));
    this.drawAssoc(clusters);
    this.loadHbonds(fileUrl("hbond_summary.csv"), clusters);
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
        marker: { color: ACID_COLORS[a] || COLORS.accent, line: { color: "#1a1a19", width: 2 } },
        hovertemplate: `${pretty(a)} · %{x}: %{y:.2f} kcal/mol<extra></extra>` };
    });
    await Plotly.react(el, traces, plotLayout({ barmode: "group", showlegend: true, hovermode: "closest",
      yaxis: { title: { text: "ΔE assoc (kcal/mol)", font: { size: 10 } } }, margin: { l: 50, r: 10, t: 24, b: 30 } }), PLOT_CONFIG);
  }

  async loadHbonds(url, clusters) {
    const slot = document.getElementById("mol-hb");
    if (!url) { clear(slot, h("div", { class: "empty" }, "hbond_summary.csv not available yet.")); return; }
    try {
      const rows = parseCSV(await getText(url));
      const best = new Set(clusters.map((c) => c.id));
      const shown = best.size ? rows.filter((r) => best.has(r.id)) : rows.slice(0, 20);
      const cols = Object.keys(shown[0] || rows[0] || {}).filter((k) => k !== "provenance").slice(0, 7);
      clear(slot,
        h("p", { class: "tiny faint" }, best.size ? `Lowest-energy cluster per acid × n(H₂O) (${shown.length} of ${rows.length} structures).` : `${rows.length} structures.`),
        h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
          h("thead", null, h("tr", null, cols.map((c) => h("th", null, pretty(c))))),
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

function provenanceList(d) {
  const m = d.method || {}, v = d.versions || {};
  const row = (k, val) => (val === undefined || val === null || val === "" ? null : [h("dt", null, k), h("dd", null, String(val))]);
  const keyVersions = ["nvalchemi-toolkit", "nvalchemi-toolkit-ops", "aimnet", "torch", "torch_cuda", "warp-lang", "ase"].filter((k) => v[k]).map((k) => `${k} ${v[k]}`).join(" · ");
  return h("div", null,
    h("dl", { class: "kv" },
      row("Calculations", `${d.n_calculations ?? "?"} total · ${d.n_converged ?? "?"} converged · ${d.n_failed ?? "?"} failed`),
      row("GPU", v.gpu ? `${v.gpu}${v.gpu_memory_gb ? ` (${Math.round(v.gpu_memory_gb)} GB` : ""}${v.gpu_capability ? `, sm ${v.gpu_capability})` : v.gpu_memory_gb ? ")" : ""}` : null),
      row("Engine", m.engine), row("Checkpoint", m.checkpoint || (Array.isArray(d.model) ? d.model.join(", ") : d.model)),
      row("Optimizer", m.optimizer ? `${m.optimizer}${m.fmax_threshold_ev_per_angstrom ? `, fmax ≤ ${m.fmax_threshold_ev_per_angstrom} eV/Å` : ""}${m.max_steps ? `, ≤ ${m.max_steps} steps` : ""}` : null),
      row("Software", keyVersions || null), row("Platform", v.platform)),
    h("details", null, h("summary", null, "Full method & version record"), renderValue({ method: m, versions: v })));
}

function recordsTable(rows) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))].slice(0, 9);
  return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
    h("thead", null, h("tr", null, cols.map((c) => h("th", null, pretty(c))))),
    h("tbody", null, rows.slice(0, 40).map((r) => h("tr", null, cols.map((c) => {
      const x = r[c];
      return h("td", { class: typeof x === "number" ? "num" : "" }, typeof x === "number" ? (Number.isInteger(x) ? String(x) : fmt(x, 3)) : x === null || x === undefined ? "—" : typeof x === "object" ? JSON.stringify(x) : String(x));
    }))))));
}
