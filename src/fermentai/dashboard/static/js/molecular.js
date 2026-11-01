// Section 3: NVIDIA Molecular Explorer - cached ALCHEMI/AIMNet2 results.
// Layout: one header strip + six "answer cards" (question, one picture, one number, one line).
// Every number is read from the synced analysis files at runtime; tables and method notes live
// behind each card's "Details" disclosure.
import { get, getText } from "./api.js";
import { h, clear, esc, fmt, parseCSV, provBadge, statusBadge, plotLayout, PLOT_CONFIG, whenPlotly, COLORS, renderValue, num } from "./util.js";

const ACID_COLORS = { acetic_acid: COLORS.acetic, lactic_acid: COLORS.lactic };
const pretty = (s) => String(s || "").replace(/_/g, " ");
const FILES = "/api/v1/molecular/files/";
const RT_LN10_298 = 0.0019872043 * 298.15 * Math.LN10;   // kcal/mol per pKa unit at 298.15 K (same as c2 analysis)
const minus = (s) => String(s).replace(/^-/, "−").replace(/ -/g, " −");
const sgn = (v, d = 1) => (num(v) === null ? "—" : minus(fmt(v, d)));
const mean = (xs) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);

/** JSON that may contain NaN/Infinity literals (Python json.dump default) -> parsed with nulls. */
function parseLooseJSON(text) {
  return JSON.parse(text.replace(/([:,[\s])-?(?:NaN|Infinity)(?=[,\]}\s])/g, "$1null"));
}
const svg = (w, hgt, body, label) =>
  `<svg class="q-svg" viewBox="0 0 ${w} ${hgt}" role="img" aria-label="${esc(label)}" preserveAspectRatio="xMidYMid meet">${body}</svg>`;
const txt = (x, y, s, { size = 13, fill = COLORS.text2, anchor = "middle", weight = 400 } = {}) =>
  `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" text-anchor="${anchor}" font-weight="${weight}">${esc(s)}</text>`;

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
    for (const id of ["mol-assoc-chart", "mol-md-chart"]) { const el = document.getElementById(id); if (el && el.data) try { window.Plotly.Plots.resize(el); } catch { /* */ } }
  }

  /** Summary endpoint first; if it fails (e.g. HTTP 500 on NaN in the digest), read the file directly. */
  async fetchDigest() {
    let degraded = null;
    try {
      const s = await get("/api/v1/molecular/summary");
      if (!s || !s.available) return { unavailable: s?.reason || "No ALCHEMI analysis files have been synced yet." };
      const sums = s.summaries || {};
      const key = Object.keys(sums).find((k) => /analysis_summary\.json$/.test(k));
      const c2key = Object.keys(sums).find((k) => /c2_summary\.json$/.test(k));
      return { digest: key ? sums[key] : null, c2: c2key ? sums[c2key] : null, files: s.files || [], degraded };
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
      return { digest, c2: null, files, degraded };
    } catch (e2) {
      return { unavailable: `${degraded || ""} The analysis file could not be read either (${e2.message}).` };
    }
  }

  async load() {
    try { await this.render(); }
    catch (e) { console.error(e); this.placeholder(`The molecular panel failed to render (${e.message}).`); }
  }

  async render() {
    this.loaded = true;
    clear(this.body, h("div", { class: "card muted" }, "Loading ALCHEMI summary…"));
    const { digest, c2: c2file, files = [], degraded, unavailable } = await this.fetchDigest();
    if (unavailable || !digest) return this.placeholder(unavailable || "analysis_summary.json missing.");
    const fileUrl = (name) => (files.find((f) => f.path === name || f.name === name) || {}).url;
    const c2 = c2file || digest.campaign2 || {};
    const prov = digest.provenance || c2.provenance || "";
    const isReal = /^real/i.test(String(prov));
    const computed = () => (isReal ? provBadge(prov, "computed") : provBadge(prov || "unknown"));
    const readCSV = async (name) => { const u = fileUrl(name); if (!u) return []; try { return parseCSV(await getText(u)); } catch { return []; } };
    const [env, assocRows, mdLongRows, coupling] = await Promise.all([
      get("/api/v1/molecular/environment").catch(() => null),
      readCSV("c2_association_by_n.csv"),
      readCSV("c2_md_long_summary.csv"),
      get("/api/v1/predictions/molecular_coupling_report.json").catch((e) => ({ error: e.message })),
    ]);

    const header = this.headerStrip(digest, env, prov, computed, degraded);
    const cards = [
      this.cardAcidity(c2, computed, digest),
      this.cardTrust(c2, computed),
      this.cardWater(assocRows, mdLongRows, computed, digest),
      this.cardGluten(c2, computed),
      this.cardDough(coupling),
      this.cardViewer(fileUrl, files, computed),
    ];
    this.viewerSlot = h("div", { id: "mol-viewer-slot" });
    clear(this.body, header, h("div", { class: "q-grid" }, cards), this.viewerSlot);
  }

  // ------------------------------------------------------------------ header strip
  headerStrip(digest, env, prov, computed, degraded) {
    const v = digest.versions || {}, m = digest.method || {}, md = digest.md || {};
    const report = String(env?.report_md || "");
    const c2opt = num((report.match(/Campaign 2:\s*(\d+)\s+optimisations/i) || [])[1]);
    const c2md = num((report.match(/(\d+)\s+long-MD runs/i) || [])[1]);
    const nOpt = num(digest.n_calculations) !== null ? digest.n_calculations + (c2opt || 0) : null;
    const nMD = num(md.n_runs) !== null ? md.n_runs + (c2md || 0) : null;
    const gpu = String(v.gpu || "").replace(/^NVIDIA\s+/, "");
    const model = /aimnet2/i.test(String(m.checkpoint || digest.model || "")) ? "AIMNet2" : (m.checkpoint || "");
    const parts = [`NVIDIA ALCHEMI${model ? ` + ${model}` : ""}`, gpu, nOpt !== null ? `${nOpt.toLocaleString("en-US")} optimisations` : null, nMD !== null ? `${nMD} MD runs` : null].filter(Boolean);
    const total = (report.match(/Total across sessions:\s*([^\n]+)/i) || [])[1];
    const xc = digest.crosscheck_upstream_aimnet;
    return h("div", { class: "card q-strip" },
      h("div", { class: "q-strip-line" }, computed(), h("span", null, parts.join(" · "))),
      h("details", { class: "q-details" }, h("summary", null, "Run record & caveat"),
        digest.caveat ? h("p", { class: "small warn-text" }, digest.caveat) : null,
        degraded ? h("p", { class: "tiny warn-text" }, degraded) : null,
        h("dl", { class: "kv" },
          kv("Provenance", prov),
          kv("GPU", v.gpu ? `${v.gpu}${v.gpu_memory_gb ? ` (${Math.round(v.gpu_memory_gb)} GB, sm ${v.gpu_capability || "?"})` : ""}` : null),
          kv("Toolkit", ["nvalchemi-toolkit", "nvalchemi-toolkit-ops", "aimnet", "warp-lang", "torch"].filter((k) => v[k]).map((k) => `${k} ${v[k]}`).join(" · ")),
          kv("Checkpoint", m.checkpoint),
          kv("Campaign 1", `${digest.n_calculations ?? "?"} optimisations (${digest.n_converged ?? "?"} converged, ${digest.n_failed ?? "?"} failed) · ${md.n_runs ?? "?"} MD runs`),
          c2opt !== null ? kv("Campaign 2", `${c2opt} optimisations · ${c2md ?? "?"} long-MD runs (environment report)`) : null,
          xc ? kv("Cross-check", `vs upstream aimnet on ${xc.n} structures: max |ΔE| ${fmt(xc.max_abs_delta_energy_ev * 1000, 1)} meV`) : null,
          env?.cost?.line ? kv("Cost (campaign 1)", env.cost.line.replace(/\*\*/g, "")) : null,
          total ? kv("Cost (all sessions)", total.replace(/\*\*/g, "")) : null),
        h("details", null, h("summary", null, "Full method & version record"), renderValue({ method: m, versions: v }))));
  }

  // ------------------------------------------------------------------ 1. acid strength
  cardAcidity(c2, computed, digest) {
    const pk = c2.relative_pka_estimate || {};
    const exp = pk.experiment_pka || {};
    const pL = num(exp.lactic_acid), pA = num(exp.acetic_acid);
    const est = num(pk.value), sig = num(pk.uncertainty), dExp = num(pk.experiment);
    // GPU ΔpKa by water count: Boltzmann ΔΔG(qh) of the exchange cycle / (RT ln 10), from thermo_cycles.
    const byN = (c2.thermo_cycles || []).filter((t) => num(t.dd_boltzmann_kcal) !== null)
      .map((t) => ({ n: t.n_water, dpka: t.dd_boltzmann_kcal / RT_LN10_298 })).sort((a, b) => a.n - b.n);
    if (pL === null || pA === null || est === null) return qcard("Which acid is stronger?", computed(), h("div", { class: "empty" }, "No relative pKa estimate in c2_summary.json."));
    const lactStronger = dExp < 0;
    const agree = Math.sign(est) === Math.sign(dExp);
    // picture: pKa axis; measured dots; GPU lactic positions anchored on measured acetic pKa
    const W = 400, H = 176, L = 8, R = W - 8;
    const gpuP = byN.map((r) => ({ ...r, p: pA + r.dpka }));
    let lo = Math.floor(Math.min(pL, ...gpuP.map((r) => r.p), pA + est - (sig || 0)) - 0.3); const hi = Math.ceil(pA + 2.4);
    const step = hi - lo > 6 ? 2 : 1; if (step === 2 && lo % 2) lo -= 1;
    const X = (p) => L + ((p - lo) / (hi - lo)) * (R - L);
    const yExp = 34, yGpu = 98, yAx = 132;
    let b = "";
    for (let t = lo; t <= hi && X(t) < R - 30; t += step) b += `<line x1="${X(t)}" x2="${X(t)}" y1="${yAx}" y2="${yAx + 5}" stroke="${COLORS.muted}"/>` + txt(X(t), yAx + 19, minus(String(t)), { size: 13, fill: COLORS.muted });
    b += `<line x1="${L}" x2="${R}" y1="${yAx}" y2="${yAx}" stroke="${COLORS.border}" stroke-width="1.5"/>`;
    b += txt(R, yAx + 19, "pKa", { size: 13, fill: COLORS.muted, anchor: "end", weight: 700 });
    b += txt(L, yAx + 40, "← stronger acid", { size: 12, fill: COLORS.muted, anchor: "start" }) + txt(R, yAx + 40, "weaker acid →", { size: 12, fill: COLORS.muted, anchor: "end" });
    b += txt(L, yExp + 5, "MEASURED", { size: 12, fill: COLORS.muted, anchor: "start", weight: 700 });
    b += txt(L, yGpu - 28, "GPU · number = waters in cluster", { size: 12, fill: COLORS.muted, anchor: "start", weight: 700 });
    if (sig !== null) b += `<rect x="${X(pA + est - sig)}" y="${yGpu - 9}" width="${X(pA + est + sig) - X(pA + est - sig)}" height="18" rx="9" fill="${COLORS.lactic}" opacity=".18"/>`;
    if (gpuP.length > 1) b += `<path d="M${gpuP.map((r) => `${X(r.p)},${yGpu}`).join(" L")}" stroke="${COLORS.lactic}" stroke-width="1.5" stroke-dasharray="3 3" fill="none" opacity=".7"/>`;
    b += `<path d="M${X(pL)},${yExp + 9} L${X(pL)},${yGpu - 9}" stroke="${COLORS.lactic}" stroke-dasharray="2 4" opacity=".55"/>`;
    gpuP.forEach((r) => { b += `<circle cx="${X(r.p)}" cy="${yGpu}" r="7" fill="${COLORS.surface}" stroke="${COLORS.lactic}" stroke-width="2.5"><title>n = ${r.n} waters: ΔpKa ${fmt(r.dpka, 2)}</title></circle>` + txt(X(r.p), yGpu + 25, `${r.n}`, { size: 14, fill: COLORS.text, weight: 700 }); });
    b += `<circle cx="${X(pA)}" cy="${yExp}" r="8" fill="${COLORS.acetic}"/>` + txt(X(pA) + 13, yExp + 5, `acetic ${fmt(pA, 2)}`, { size: 14, fill: COLORS.text, weight: 600, anchor: "start" });
    b += `<circle cx="${X(pL)}" cy="${yExp}" r="8" fill="${COLORS.lactic}"/>` + txt(X(pL) - 13, yExp + 5, `lactic ${fmt(pL, 2)}`, { size: 14, fill: COLORS.text, weight: 600, anchor: "end" });
    const pic = h("div", { class: "q-pic", html: svg(W, H, b, `pKa scale: lactic ${pL}, acetic ${pA}; GPU estimates by water count`) });
    const dep = Array.isArray(digest.deprotonation) ? digest.deprotonation : [];
    return qcard("Which acid is stronger?", computed(), pic,
      bigNum(`${sgn(est)}${sig !== null ? ` ± ${fmt(sig, 1)}` : ""}`, "ΔpKa GPU", `${sgn(dExp, 2)} measured`),
      h("p", { class: "q-take" }, `${lactStronger ? "Lactic" : "Acetic"} is stronger — the GPU ${agree ? "agrees on direction" : "gets the direction wrong"}.`),
      details(
        h("p", { class: "small" }, "Dots on the GPU row: lactic pKa implied by the GPU gap, placed relative to measured acetic (", fmt(pA, 2), "). Numbers under the dots = explicit waters. The band = combined estimate ± uncertainty (n = ", byN.filter((r) => [6, 10].includes(r.n)).map((r) => r.n).join(", ") || "?", ")."),
        tbl(["Waters", "ΔpKa (lactic − acetic)", "ΔΔG (kcal/mol)"], byN.map((r) => [r.n, fmt(r.dpka, 2), fmt(r.dpka * RT_LN10_298, 2)])),
        pk.method ? h("p", { class: "tiny faint" }, "Method: ", pk.method) : null,
        pk.note ? h("p", { class: "tiny faint" }, "Note: ", pk.note) : null,
        dep.length ? h("details", null, h("summary", null, "Campaign 1 deprotonation energies (gas-phase clusters)"),
          tbl(["Acid", "n H₂O", "ΔE → ions (kcal/mol)", "ΔE → ion pair", "Ion pairs surviving"], dep.map((r) => [pretty(r.acid), r.n_water, fmt(r.dE_deprot_separated_ions_kcal_mol, 1),
            num(r.dE_deprot_ion_pair_kcal_mol) === null ? "none survived" : fmt(r.dE_deprot_ion_pair_kcal_mol, 1),
            num(r.n_ion_pair_starts) === null ? "—" : `${fmt(r.n_ion_pairs_surviving, 0)} / ${fmt(r.n_ion_pair_starts, 0)}`]))) : null));
  }

  // ------------------------------------------------------------------ 2. trust checks
  cardTrust(c2, computed) {
    const pk = c2.relative_pka_estimate || {}, ap = c2.acid_pairs || {};
    const tiles = [];
    const lit = Array.isArray(ap.acetic_dimer_literature_kcal) ? ap.acetic_dimer_literature_kcal.map(Number) : null;
    if (num(ap.acetic_dimer_kcal) !== null && lit) {
      const [lo, hi] = [Math.min(...lit), Math.max(...lit)];
      const ok = ap.acetic_dimer_kcal >= lo && ap.acetic_dimer_kcal <= hi;
      tiles.push(h("div", { class: `q-tile ${ok ? "ok" : "bad"}` },
        h("div", { class: "q-tile-k" }, "Acetic acid dimer"),
        h("div", { class: "q-tile-v" }, sgn(ap.acetic_dimer_kcal), h("span", { class: "q-unit" }, " kcal/mol")),
        h("div", { class: "q-tile-ref" }, `literature ${sgn(lo, 0)} … ${sgn(hi, 0)}`),
        verdict(ok, ok ? "in range" : "out of range")));
    }
    const est = num(pk.value), dExp = num(pk.experiment), sig = num(pk.uncertainty);
    let ratio = null;
    if (est !== null && dExp !== null) {
      const signOk = Math.sign(est) === Math.sign(dExp);
      const magOk = sig !== null && Math.abs(est - dExp) <= sig;
      ratio = dExp ? est / dExp : null;
      tiles.push(h("div", { class: `q-tile ${signOk && magOk ? "ok" : "mixed"}` },
        h("div", { class: "q-tile-k" }, "Acidity gap (ΔpKa)"),
        h("div", { class: "q-tile-v" }, sgn(est), h("span", { class: "q-unit" }, ` vs ${sgn(dExp, 2)}`)),
        verdict(signOk, signOk ? "direction right" : "direction wrong"),
        verdict(magOk, magOk ? "size within error" : (ratio !== null && Math.abs(ratio) > 1 ? `size ${fmt(Math.abs(ratio), 1)}× too big` : "size too small"))));
    }
    if (!tiles.length) return qcard("Is the GPU model trustworthy?", computed(), h("div", { class: "empty" }, "No comparison with experiment in c2_summary.json."));
    return qcard("Is the GPU model trustworthy?", computed(), h("div", { class: "q-tiles" }, tiles),
      h("p", { class: "q-take" }, "Binding energies check out; the acidity gap is overestimated."),
      details(
        h("p", { class: "small" }, "Acetic-acid cyclic dimer binding vs the published range; lactic − acetic ΔpKa vs experiment (",
          `pKa ${fmt(pk.experiment_pka?.lactic_acid, 2)} vs ${fmt(pk.experiment_pka?.acetic_acid, 2)}`, "). Deviation ", sgn(pk.deviation_from_experiment, 2),
          sig ? ` pKa units = ${fmt(Math.abs(pk.deviation_from_experiment) / sig, 1)} σ.` : "."),
        h("p", { class: "tiny faint" }, "Small explicit-water clusters without a bulk solvent model typically exaggerate acidity differences; treat the gap's size as an upper bound, its sign as the signal.")));
  }

  // ------------------------------------------------------------------ 3. water
  cardWater(assocRows, mdLongRows, computed, digest) {
    const acids = ["lactic_acid", "acetic_acid"];
    const assoc = Object.fromEntries(acids.map((a) => {
      const vals = assocRows.filter((r) => r.species === a).map((r) => num(r.dF_assoc_per_water_kcal)).filter((x) => x !== null);
      return [a, vals.length ? { mean: mean(vals), min: Math.min(...vals), max: Math.max(...vals), n: assocRows.filter((r) => r.species === a).map((r) => r.n_water) } : null];
    }));
    // H-bonds acid->water from the long (20 ps) MD, neutral acid, temperature closest to 22 °C
    const hb = Object.fromEntries(acids.map((a) => {
      const rows = mdLongRows.filter((r) => r.species === a && num(r.mean_acid_water_hbonds) !== null);
      if (!rows.length) return [a, null];
      const best = rows.reduce((p, r) => (Math.abs(r.temperature_k - 295.15) < Math.abs(p.temperature_k - 295.15) ? r : p));
      return [a, best];
    }));
    let pic = null;
    if (assoc.lactic_acid && assoc.acetic_acid) {
      const W = 400, H = 96, L = 62, R = W - 56;
      const vmax = Math.ceil(Math.max(...acids.map((a) => Math.abs(assoc[a].min))) + 0.5);
      const X = (v) => L + (Math.abs(v) / vmax) * (R - L);
      let b = "";
      acids.forEach((a, i) => {
        const y = 14 + i * 40, r = assoc[a];
        b += txt(L - 8, y + 16, a.split("_")[0], { size: 14, fill: COLORS.text, anchor: "end", weight: 600 });
        b += `<rect x="${L}" y="${y}" width="${X(r.mean) - L}" height="24" rx="4" fill="${ACID_COLORS[a]}"/>`;
        b += `<line x1="${X(r.min)}" x2="${X(r.max)}" y1="${y + 12}" y2="${y + 12}" stroke="${COLORS.text}" stroke-width="1.5" opacity=".7"/>`;
        b += `<line x1="${X(r.min)}" x2="${X(r.min)}" y1="${y + 6}" y2="${y + 18}" stroke="${COLORS.text}" opacity=".7"/><line x1="${X(r.max)}" x2="${X(r.max)}" y1="${y + 6}" y2="${y + 18}" stroke="${COLORS.text}" opacity=".7"/>`;
        b += txt(Math.max(X(r.mean), X(r.min)) + 8, y + 17, `${sgn(r.mean)}`, { size: 14, fill: COLORS.text, anchor: "start", weight: 700 });
      });
      b += txt(L, H - 2, `kcal/mol per water · whisker = range over ${Math.min(...assoc.lactic_acid.n)}–${Math.max(...assoc.lactic_acid.n)} waters`, { size: 11, fill: COLORS.muted, anchor: "start" });
      pic = h("div", { class: "q-pic", html: svg(W, H, b, "Hydration energy per water, lactic vs acetic") });
    }
    const hbNums = hb.lactic_acid && hb.acetic_acid ? h("div", { class: "q-pair" },
      acids.map((a) => h("div", { class: "q-pair-item" }, h("div", { class: "q-big", style: { color: ACID_COLORS[a] } }, fmt(hb[a].mean_acid_water_hbonds, 1)),
        h("div", { class: "q-sub" }, `${a.split("_")[0]} · H-bonds to water`)))) : null;
    const T = hb.lactic_acid ? Math.round(hb.lactic_acid.temperature_k - 273.15) : null;
    const same = assoc.lactic_acid && assoc.acetic_acid && Math.abs(assoc.lactic_acid.mean - assoc.acetic_acid.mean) < 0.5;
    const clusters = Array.isArray(digest.lowest_energy_clusters) ? digest.lowest_energy_clusters : [];
    if (!pic && !hbNums) return qcard("How do the acids hold water?", computed(), h("div", { class: "empty" }, "c2_association_by_n.csv / c2_md_long_summary.csv not available."));
    const det = details(
      h("p", { class: "small" }, "Bars: Boltzmann-averaged association free energy per water, mean over n = ", (assoc.lactic_acid?.n || []).join(", "), " waters (c2_association_by_n.csv). Big numbers: mean acid→water H-bonds over 3 × 20 ps MD replicas at ", T ?? "?", " °C, 6 waters (c2_md_long_summary.csv)."),
      tbl(["Species", "n H₂O", "ΔF per water (kcal/mol)", "ΔE total (kcal/mol)", "Minima"], assocRows.filter((r) => acids.includes(r.species)).map((r) => [pretty(r.species), r.n_water, fmt(r.dF_assoc_per_water_kcal, 2), fmt(r.dE_assoc_kcal, 1), r.n_minima])),
      tbl(["MD run (20 ps)", "T (°C)", "Acid–water H-bonds", "± SD replicas"], mdLongRows.map((r) => [pretty(r.species), fmt(r.temperature_k - 273.15, 0), fmt(r.mean_acid_water_hbonds, 2), fmt(r.sd_acid_water_hbonds_over_replicas, 2)])),
      h("details", { "data-md": "1" }, h("summary", null, "Campaign 1: short MD H-bonds vs temperature (chart)"), h("div", { class: "chart", id: "mol-md-chart", style: { height: "240px" } })),
      clusters.length ? h("details", null, h("summary", null, "Campaign 1: lowest-energy clusters"),
        tbl(["Acid", "n H₂O", "ΔE assoc", "per H₂O", "H-bonds"], clusters.map((r) => [pretty(r.acid), r.n_water, fmt(r.dE_assoc_kcal_mol, 2), fmt(r.dE_assoc_per_water_kcal_mol, 2), r.n_hbonds ?? "—"]))) : null);
    const mdDet = det.querySelector("details[data-md]");
    mdDet.addEventListener("toggle", () => { if (mdDet.open && !this.mdDrawn) { this.mdDrawn = true; this.drawMD(digest); } });
    return qcard("How do the acids hold water?", computed(), pic, hbNums,
      h("p", { class: "q-take" }, same ? `Same grip per water; lactic makes more H-bonds${T !== null ? ` (${T} °C)` : ""}.` : "Hydration energies differ; see details."),
      det);
  }

  // ------------------------------------------------------------------ 4. gluten proxy
  cardGluten(c2, computed) {
    const co = c2.cosolutes || {};
    const L = num(co.lactic_acid?.gln_gln?.cosolute_to_hydrated_acid_kcal), A = num(co.acetic_acid?.gln_gln?.cosolute_to_hydrated_acid_kcal);
    if (L === null || A === null) return qcard("Does it touch gluten?", computed(), h("div", { class: "empty" }, "No glutamine-dipeptide results in c2_summary.json."));
    const W = 400, H = 96, X0 = 62, R = W - 56, vmax = Math.ceil(Math.max(Math.abs(L), Math.abs(A)) / 5) * 5;
    const X = (v) => X0 + (Math.abs(v) / vmax) * (R - X0);
    let b = "";
    [["lactic_acid", L], ["acetic_acid", A]].forEach(([a, v], i) => {
      const y = 14 + i * 40;
      b += txt(X0 - 8, y + 16, a.split("_")[0], { size: 14, fill: COLORS.text, anchor: "end", weight: 600 });
      b += `<rect x="${X0}" y="${y}" width="${X(v) - X0}" height="24" rx="4" fill="${ACID_COLORS[a]}"/>` + txt(X(v) + 8, y + 17, sgn(v), { size: 14, fill: COLORS.text, anchor: "start", weight: 700 });
    });
    b += txt(X0, H - 2, "kcal/mol binding to glutamine dipeptide (3 waters)", { size: 11, fill: COLORS.muted, anchor: "start" });
    const rows = [];
    for (const [acid, m] of Object.entries(co)) for (const [c, r] of Object.entries(m || {})) rows.push([pretty(acid), pretty(c), fmt(r.cosolute_to_hydrated_acid_kcal, 1), fmt(r.total_kcal, 1), fmt(r.gap_to_2nd_kcal, 1)]);
    return qcard("Does it touch gluten?", computed(),
      h("div", { class: "q-pic", html: svg(W, H, b, `Glutamine dipeptide binding: lactic ${fmt(L, 1)}, acetic ${fmt(A, 1)} kcal/mol`) }),
      bigNum(sgn(L - A), "kcal/mol", L < A ? "lactic binds tighter" : "acetic binds tighter"),
      h("p", { class: "q-take" }, statusBadge("hypothesis-level proxy", "warn"), " a fragment, not real gluten."),
      details(
        h("p", { class: "small" }, "Gln–Gln dipeptide stands in for glutamine-rich gluten segments. One small cluster per pair, lowest minimum found; a direction to test, not evidence about dough."),
        tbl(["Acid", "Cosolute", "Cosolute → hydrated acid", "Total (kcal/mol)", "Gap to 2nd"], rows)));
  }

  // ------------------------------------------------------------------ 5. coupling into the dough model
  cardDough(rep) {
    const title = "What does it change in the dough model?";
    if (!rep || rep.error || !rep.per_chamber) return qcard(title, provBadge("simulated"), h("div", { class: "empty" }, `No coupling report (data/predictions/molecular_coupling_report.json)${rep?.error ? `: ${rep.error}` : ""}.`));
    const per = Object.entries(rep.per_chamber);
    const first = per[0]?.[1] || {};
    const P = first.parameters || {};
    const gapLit = num(P.pka_lactic?.literature_value) !== null && num(P.pka_acetic?.literature_value) !== null ? P.pka_lactic.literature_value - P.pka_acetic.literature_value : null;
    const gapUsed = num(P.pka_lactic?.value) !== null && num(P.pka_acetic?.value) !== null ? P.pka_lactic.value - P.pka_acetic.value : null;
    const pot = P.undissociated_lactic_rel_potency;
    let dpH = null; const timing = [];
    for (const [, c] of per) for (const [k, e] of Object.entries(c.effect_on_chamber || {})) {
      const d = num(e.alchemi_informed) !== null && num(e.literature) !== null ? Math.abs(e.alchemi_informed - e.literature) : null;
      if (d === null) continue;
      if (/^ph/i.test(k)) dpH = Math.max(dpH ?? 0, d); else timing.push(d);
    }
    const timingSame = timing.length && timing.every((d) => d < 1e-9);
    const row = (label, a, b2) => h("div", { class: "q-chg" }, h("span", { class: "q-chg-k" }, label), h("span", { class: "q-chg-a" }, a), h("span", { class: "q-chg-arrow" }, "→"), h("span", { class: "q-chg-b" }, b2));
    const effRows = [];
    for (const [cid, c] of per) for (const [k, e] of Object.entries(c.effect_on_chamber || {})) effRows.push([cid, pretty(k), fmt(e.literature, 3), fmt(e.alchemi_informed, 3)]);
    const parRows = Object.entries(P).map(([k, p]) => [pretty(k), fmt(p.literature_value, 3), fmt(p.value, 3), p.source || "", p.mapping || ""]);
    return qcard(title, provBadge(rep.provenance || "simulated"),
      h("div", { class: "q-chgs" },
        gapLit !== null ? row("pKa gap", sgn(gapLit, 2), sgn(gapUsed, 2)) : null,
        pot ? row("lactic inhibition", fmt(pot.literature_value, 2), fmt(pot.value, 3)) : null),
      dpH !== null ? bigNum(`≤ ${fmt(Math.ceil(dpH * 1000) / 1000, 3)}`, "pH shift", timingSame ? "rise timing unchanged" : "rise timing shifts") : null,
      h("p", { class: "q-take" }, "Bounds the acid terms; does not drive the bread."),
      details(
        (first.notes || []).length ? h("p", { class: "small warn-text" }, first.notes.join("; ")) : null,
        tbl(["Parameter", "Literature", "Used", "Source", "Mapping"], parRows),
        tbl(["Chamber", "Output", "Literature params", "ALCHEMI-informed"], effRows),
        h("p", { class: "tiny faint" }, provBadge("estimated", "alchemi-informed"), " Bounded, deliberately small nudges; the fermentation model, not ALCHEMI, predicts pH and rise.")));
  }

  // ------------------------------------------------------------------ 6. 3D viewer (lazy)
  cardViewer(fileUrl, files, computed) {
    const views = [["c2_molecular_explorer.html", "3D structures (campaign 2)"], ["molecular_explorer.html", "3D structures (campaign 1)"], ["c2_convergence_vs_n.html", "Convergence vs waters"],
      ["association_energy.html", "Association energy"], ["deprotonation_energy.html", "Deprotonation"], ["md_hbonds_vs_temperature.html", "MD H-bonds"]].filter(([f]) => fileUrl(f));
    const open = (f, label) => {
      clear(this.viewerSlot, h("div", { class: "card q-viewer" },
        h("div", { class: "q-viewer-head" }, h("h3", null, label, " ", computed()), h("button", { class: "btn btn-ghost", type: "button", onclick: () => clear(this.viewerSlot) }, "Close")),
        h("iframe", { class: "explorer-frame", src: fileUrl(f), title: label, loading: "lazy" })));
      this.viewerSlot.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    const main = views[0];
    return qcard("See the molecules", computed(),
      main ? h("button", { class: "btn q-open", type: "button", onclick: () => open(...main) }, h("span", { class: "q-open-ico", "aria-hidden": "true" }, "⚛"), "Open 3D viewer")
        : h("div", { class: "empty" }, "No 3D viewer generated yet."),
      h("p", { class: "q-take" }, "Rotatable acid–water clusters, loaded on demand."),
      details(
        views.length > 1 ? h("div", { class: "q-links" }, views.slice(1).map(([f, label]) => h("button", { class: "btn btn-ghost", type: "button", onclick: () => open(f, label) }, label))) : null,
        h("details", null, h("summary", null, `Result files (${files.length})`),
          h("ul", { class: "small", style: { margin: 0, paddingLeft: "18px" } }, files.map((f) => h("li", null, h("a", { href: f.url, target: "_blank" }, f.path), f.bytes ? h("span", { class: "faint" }, ` · ${(f.bytes / 1024).toFixed(0)} kB`) : null))))));
  }

  async drawMD(digest) {
    const el = document.getElementById("mol-md-chart");
    const md = digest.md || {};
    let rows = [];
    try { rows = parseCSV(await getText(`${FILES}md_summary.csv`)); } catch { rows = []; }
    if (!rows.length && Array.isArray(md.runs)) rows = md.runs;
    rows = rows.filter((r) => r.status === "completed" && num(r.mean_acid_water_hbonds_second_half) !== null);
    if (!rows.length) { clear(el, h("div", { class: "empty" }, "md_summary.csv not available.")); return; }
    let Plotly;
    try { Plotly = await whenPlotly(); } catch (e) { clear(el, h("div", { class: "empty" }, e.message)); return; }
    const groups = new Map();
    for (const r of rows) {
      const k = r.start_structure;
      if (!groups.has(k)) groups.set(k, new Map());
      const g = groups.get(k), T = Number(r.temperature_k);
      if (!g.has(T)) g.set(T, []);
      g.get(T).push(Number(r.mean_acid_water_hbonds_second_half));
    }
    const traces = [...groups.entries()].sort().map(([k, g]) => {
      const acid = k.startsWith("lactic") ? "lactic_acid" : "acetic_acid";
      const anion = k.includes("anion");
      const n = (k.match(/__n(\d+)/) || [])[1];
      const Ts = [...g.keys()].sort((a, b) => a - b);
      const m = Ts.map((T) => mean(g.get(T)));
      const name = `${anion ? "anion " : ""}${acid.split("_")[0]} · ${n} H₂O`;
      return { x: Ts.map((T) => T - 273.15), y: m, type: "scatter", mode: "lines+markers", name,
        error_y: { type: "data", symmetric: false, array: Ts.map((T, i) => Math.max(...g.get(T)) - m[i]), arrayminus: Ts.map((T, i) => m[i] - Math.min(...g.get(T))), color: ACID_COLORS[acid], thickness: 1, width: 3 },
        line: { color: ACID_COLORS[acid], width: 2, dash: anion ? "dot" : n === "3" ? "solid" : "dash" },
        marker: { size: 7, color: ACID_COLORS[acid], symbol: anion ? "diamond" : "circle" },
        hovertemplate: `${name}: %{y:.2f} H-bonds at %{x:.0f} °C<extra></extra>` };
    });
    await Plotly.react(el, traces, plotLayout({ showlegend: true, hovermode: "closest", legend: { orientation: "h", y: -0.32, font: { size: 9, color: COLORS.text2 } },
      margin: { l: 44, r: 10, t: 8, b: 78 }, xaxis: { title: { text: "MD temperature (°C)", font: { size: 10 } } },
      yaxis: { title: { text: "acid–water H-bonds", font: { size: 10 } } } }), PLOT_CONFIG);
  }

  placeholder(reason) {
    clear(this.body, h("div", { class: "card placeholder-hero" },
      h("div", { class: "big-ico" }, "⚛"),
      h("h3", null, "NVIDIA ALCHEMI results not yet available on this Mac"),
      h("p", { class: "muted" }, reason),
      h("p", { class: "small muted" }, "Once the remote GPU run is synced (scripts/collect_brev_results.sh) and analysed, this panel answers: which acid is stronger, whether the GPU model matches experiment, how the acids hold water, and what changes in the dough model."),
      h("p", { class: "small" }, statusBadge("no values shown", "info"), " Nothing here is estimated or fabricated — the panel stays empty until real outputs exist.")));
  }
}

function qcard(title, badge, ...children) {
  return h("section", { class: "card qcard" }, h("div", { class: "q-head" }, h("h3", { class: "q-title" }, title), h("span", { class: "q-badges" }, badge)), children);
}
function bigNum(value, unit, sub) {
  return h("div", { class: "q-num" }, h("span", { class: "q-big" }, value), unit ? h("span", { class: "q-unit" }, ` ${unit}`) : null, sub ? h("span", { class: "q-sub" }, sub) : null);
}
function verdict(ok, text) {
  return h("div", { class: `q-verdict ${ok ? "verdict-good" : "verdict-bad"}` }, h("span", { "aria-hidden": "true" }, ok ? "✓ " : "✗ "), text);
}
function details(...children) {
  return h("details", { class: "q-details" }, h("summary", null, "Details"), children);
}
function tbl(cols, rows) {
  if (!rows.length) return null;
  return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
    h("thead", null, h("tr", null, cols.map((c, i) => h("th", { class: i ? "num" : "" }, c)))),
    h("tbody", null, rows.map((r) => h("tr", null, r.map((c, i) => h("td", { class: i ? "num" : "" }, String(c ?? "—"))))))));
}
function kv(k, val) { return val === undefined || val === null || val === "" ? null : [h("dt", null, k), h("dd", null, String(val))]; }
