# Scientific assumptions: sourdough fermentation model

**Provenance: every number this model produces is `simulated`.** It is a mechanistic-but-phenomenological
ODE model. Its parameters are anchored to the literature where a source is cited and are otherwise
explicit assumptions or order-of-magnitude estimates. **It has not been calibrated against our own
dough yet.** Treat its outputs as hypotheses to test, not as measurements.

Code: `src/fermentai/fermentation/` (`kinetics.py`, `simulation.py`, `calibration.py`, `sensitivity.py`,
`plotting.py`). Parameters: `configs/kinetics.yaml`. Every entry there has a value, units, a min/max
range and a `reference`. Experiment: `configs/experiments.yaml`. Outputs: `data/predictions/` (regenerate
with `uv run python scripts/simulate_loaves.py`).

---

## 1. What is modelled

**Basis.** Everything is per kg of fresh dough. The recipe is 200 g flour, 135 g water, 40 g starter at
100 % hydration and 4.4 g salt, giving 379.4 g of dough. That is 220 g total flour and 155 g total water,
so 70.5 % baker's hydration, a water fraction of 0.409 kg/kg and a starter inoculum of 10.5 % of the
dough mass. Time is in hours from the end of mixing.

| State | Symbol | Units | Initial value |
|---|---|---|---|
| Fermentable substrate (hexose-equivalent) | S | g/kg dough | 28 (lumped; see §6) |
| Yeast biomass proxy | X_Y | relative to inoculum | 1 |
| Lactic acid bacteria (LAB) biomass proxy | X_L | relative to inoculum | 1 |
| Lactic acid (total) | La | g/kg dough | 1.0 (carried over from the starter) |
| Acetic acid (total) | Ac | g/kg dough | 0.2 (carried over from the starter) |
| Cumulative CO₂ produced | C | g CO₂/kg dough | 0 |
| *aux.* gas held in dough cells | G | g CO₂/kg dough | 0 |
| *aux.* gluten-network integrity | Q | 0–1 | 1 |

Biomass is a dimensionless proxy, normalised to the inoculated population. We do not count cells.
The yields and specific rates are therefore lumped parameters, defined per unit of that proxy.

## 2. Equations

The **temperature factors** use the cardinal temperature model with inflection (CTMI) of Rosso et al.
(1993). For T_min < T < T_max:

```
f_T(T) = (T − T_max)(T − T_min)² / ((T_opt − T_min)·[(T_opt − T_min)(T − T_opt) − (T_opt − T_max)(T_opt + T_min − 2T)])
```

f_T is 0 outside that interval and 1 at T_opt.

- Yeast: T_min 8, T_opt 27, T_max 36 °C. The 27 °C optimum is reported for *C. milleri*, now
  *Kazachstania humilis*, by Gänzle, Ehmann & Hammes (1998).
- LAB: T_min 8, T_opt 32, T_max 39 °C. Gänzle et al. (1998) report a lactobacilli optimum above the
  yeast's. The exact 32 °C value is the commonly quoted one for *F. sanfranciscensis* and depends on
  the strain.
- At 22 °C vs 27 °C: f_T,LAB is 0.52 vs 0.83 (×1.6), and f_T,Y is 0.82 vs 1.00 (×1.2). Warming
  therefore favours LAB over yeast. This is the mechanistic basis of the "B is more acidic" hypothesis.

The **pH / acid inhibition factors** use the form of Presser, Ratkowsky & Ross (1997):
`f_pH(pH; pH_min) = clip(1 − 10^(pH_min − pH), 0, 1)`.

- LAB growth: pH_min = 3.9. Gänzle et al. (1998) give a growth range of pH 3.9–6.7.
- LAB acid production when not growing: pH_min = 3.45 (assumption). This term bounds the final pH.
- Yeast: pH_min = 2.8, so yeast is essentially insensitive to pH, consistent with "unaffected by pH
  3.5–7" (Gänzle et al. 1998). Yeast is instead inhibited linearly by undissociated acid, expressed
  in acetic-acid equivalents in the aqueous phase: `f_HAc = max(0, 1 − ([HAc] + r·[HLa])/150 mmol L⁻¹)`.
  Complete inhibition at 150 mmol/L of undissociated acetic acid is from Gänzle et al. (1998)
  (`source: literature`). r = `undissociated_lactic_rel_potency` = 0.10 (range 0–0.3) is the
  relative potency of undissociated lactic vs acetic acid. It is an assumption (`source:
  assumption`), and it is the parameter the ALCHEMI hook may adjust (§9).

**Growth** is Monod in substrate, with a logistic cap:

```
μ_Y = μ_max,Y · f_T,Y · S/(K_s,Y + S) · f_HAc · f_pH,Y · (1 − X_Y / X_max,Y)
μ_L = μ_max,L · f_T,L · S/(K_s,L + S) · f_pH,L,growth · (1 − X_L / X_max,L)
dX_Y/dt = μ_Y X_Y          dX_L/dt = μ_L X_L
```

**Acids** follow Luedeking & Piret (1959), with a growth-associated term plus a non-growth term:

```
r_La = α · dX_L/dt + β · f_T,L · S/(K_s,L + S) · f_pH,L,acid · X_L
dLa/dt = r_La
dAc/dt = r_La · (M_Ac / M_La) / FQ(T),    FQ(T) = FQ_ref · exp(k_FQ (T − 25 °C))
```

FQ is the molar fermentation quotient, lactic:acetic. We set FQ_ref = 4 and k_FQ = 0.03 /°C. Both are
assumptions; see §4.

**Substrate and CO₂:**

```
r_S,Y  = q_s,Y · f_T,Y · S/(K_s,Y + S) · f_HAc · f_pH,Y · X_Y
r_S,L  = (r_La / M_La) · (h · 1 + (1 − h) · ½) · M_hexose
dS/dt  = −(r_S,Y + r_S,L)
dC/dt  = Y_CO2 · r_S,Y + h · (r_La / M_La) · M_CO2
```

- r_S,L converts lactate to hexose consumed: hetero 1 hexose → 1 lactate; homo 1 hexose → 2 lactate.
- In dC/dt, heterofermentation releases 1 CO₂ per lactate.
- Y_CO2 = 0.44 g/g. Alcoholic fermentation gives a theoretical 0.489 g/g, and we assume ~90 % of the
  sugar is routed to ethanol.
- h = 0.6 is the fraction of lactate formed heterofermentatively. This is an assumption for a mixed
  type-I sourdough community.

**Buffered pH (§3)** is solved algebraically at every step from La and Ac.

**Dough expansion** is phenomenological. It is defined as V/V₀, which equals relative height h/h₀ in a
straight-sided vessel:

```
E      = 1 + (G / ρ_CO2(T)) · φ / v_dough          ρ_CO2 = ideal-gas density at 1 atm; v_dough = 0.87 L/kg
E_cap  = 1 + (E_max − 1) · Q                       gas-holding capacity of the gluten network
dG/dt  = dC/dt · (1 − e^(−C/C_sat)) · max(0, 1 − (E/E_cap)^n) − [k_leak + k_rup · max(0, E/E_cap − 1)] · G
dQ/dt  = −k_Q · Q10^((T − 25)/10) · (1 + a · max(0, pH_onset − pH)) · Q
```

The terms of dG/dt mean the following:

1. Newly produced CO₂ first saturates the dough liquor. C_sat ≈ 0.6 g/kg, from CO₂ solubility of
   ~1.5 g/L × 0.41 L water/kg.
2. The fraction of new gas that is retained falls as the cells crowd toward capacity.
3. Gas leaks slowly all the time (k_leak).
4. Rupture is fast once the rise exceeds the current capacity (k_rup).

Q degrades over time. The degradation is faster when the dough is warmer and more acidic. The
qualitative mechanism is acid-activated cereal proteases (Thiele, Gänzle & Vogel 2002; Gänzle, Loponen
& Gobbetti 2008). The **rate constants are assumptions**.

The result is a rise that saturates near capacity, holds, then falls as capacity degrades and sugar
runs out. That fall is the modelled *overproofing*. Cooling a ripe dough slows gas production but does
not deflate it. Because E uses the gas density at the current temperature, a cooling step does
produce a small thermal contraction.

**Overproof signal (`overproof_risk`, 0–1, heuristic).**

- Approaching capacity (E/E_cap from 0.85 to 1) ramps the signal to 0.45. At that point the dough is
  near its peak, which may also mean it is ready.
- Losing 0–10 % of the peak rise ramps it from 0 to 1. A loss of 5 % (value ≥ 0.5) is reported as
  overproof onset.
- The loss is computed on the rise referenced to 25 °C, so a cooling step is not mistaken for
  collapse.

**Numerics.** The ODE is integrated with SciPy `solve_ivp` (LSODA, rtol 1e-6, atol 1e-8, max step
0.25 h). Integration is split at the schedule breakpoints, so step changes in temperature are exact.
States are clipped at ≥ 0. A 24 h run takes about 20 ms. The model runs on CPU only.

**Dough temperature.** The temperature schedule is treated as the *dough* temperature. Thermal lag
between chamber air and dough is not modelled (§7).

## 3. pH mapping: why buffering matters

Dough pH is **not** a function of acid concentration alone. Flour proteins, phosphates and phytate,
plus other ash components, buffer strongly. Doubling the acid content therefore lowers the pH much less
than in water. We solve a proton balance for [H⁺]:

```
Σ_acids  C_a · (1 − 1/(1 + 10^(pH − pKa)))   =   Σ_buffers  B_i · (θ_i(pH) − θ_i(pH_flour))  +  w · ([H⁺] − [H⁺]_flour)
```

- The left side is the protons released by lactic (pKa 3.86) and acetic (pKa 4.76) acid.
- The right side is the protons bound by two lumped buffer groups. B₁ = 40 mmol/kg has pKa 4.6.
  B₂ = 25 mmol/kg has pKa 6.3. θ_i is the protonated fraction of group i.
- The reference point is an acid-free flour/water dough at pH_flour = 6.0.

The buffer sizes are **assumptions**, tuned to typical behaviour:

- A dough inoculated with 10 % ripe starter starts at about pH 5.2.
- With ~7 g/kg lactic acid and ~1.4 g/kg acetic acid, the dough is at about pH 3.8.
- The corresponding titratable acidity is ~12 mL 0.1 N NaOH per 10 g, which is in the range usually
  reported for sourdough bread doughs.

Ideal behaviour is assumed: activities equal concentrations, there is no ionic-strength correction,
and the pKa values are taken at 25 °C. Real flours differ, mainly in ash and buffering. **The buffer
parameters are among the most influential for pH (§8), so the first real pH readings should be used to
calibrate `buffer_b1` and `ph_flour`.**

## 4. Total acidity, the lactic:acetic ratio, pH and perceived sourness are different things

The model reports these separately. Agents should not conflate them.

- **Total (titratable) acidity, TTA** (`tta_ml_per_10g`). This is how much base is needed to bring the
  dough to pH 8.5. It counts all acid, both dissociated and undissociated, plus the buffer. TTA keeps
  rising while pH flattens out.
- **pH** (`ph`). This is free proton activity. It is set by the acids *and* the buffer. Two doughs can
  have the same pH and different TTA.
- **Lactic:acetic balance** (`fermentation_quotient`, molar). Acetic acid has the higher pKa, so more
  of it is undissociated at dough pH. It is also volatile and sharp. A small amount of acetic acid
  changes the aroma and perceived sharpness disproportionately. In heterofermentative LAB, acetate
  formation depends on electron acceptors such as fructose and O₂ (Gänzle 2014). It is **not**
  modelled mechanistically here: FQ(T) is an empirical assumption. Baking lore says warmer conditions
  favour lactic acid and cooler, stiffer conditions favour acetic acid. The evidence for this is
  mixed, and k_FQ ranges from 0 to 0.08 /°C.
- **Perceived sourness.** This is a sensory outcome. It depends on TTA, pH, the acetic fraction, salt,
  bake loss of volatile acetic acid, crumb texture and the taster. The model **does not predict
  perceived sourness**. Only the blind tasting can measure it.

## 5. Chamber A (22 °C) and Chamber B (27 °C) are hypotheses

The model supports the *direction* of the hypothesis. At 27 °C, LAB activity rises more than yeast
activity, so acids accumulate faster and the pH falls faster than at 22 °C. That is not a guarantee of
a detectably sourer loaf, for three reasons:

- Real starters differ in community composition and temperature optima.
- A long, cool fermentation can still build substantial and sharper, more acetic acidity. In the model,
  A has the lower FQ, i.e. proportionally more acetic acid.
- Sensory differences of ~0.2 pH units may or may not be perceptible after baking.

With the nominal parameters (128-sample Monte Carlo, seed 0; see `data/predictions/`):

| Simulated | Chamber A, 22 °C | Chamber B, 27 °C |
|---|---|---|
| pH at 12 h (5–95 %) | 4.31 (4.03–4.65) | 4.02 (3.86–4.32) |
| pH at 16 h (5–95 %) | 4.13 (3.92–4.50) | 3.90 (3.77–4.28) |
| Final pH at 24 h | 4.02 | 3.89 |
| Lactic / acetic at 16 h (g/kg) | 4.7 / 0.88 | 6.5 / 1.07 |
| TTA at 24 h (mL 0.1 N / 10 g) | 9.5 | 11.0 |
| Peak rise, V/V₀ (5–95 %) | 2.11 (1.88–2.49) at 9.5 h (peak 7.2–12.4 h) | 2.09 (1.85–2.47) at 7.2 h (peak 5.7–8.9 h) |
| Time to 2× rise | 7.2 h | 5.7 h |
| Overproof onset (≥ 5 % of rise lost) | 12.9 h | 9.5 h |
| Gluten-weakening index at 16 h | 0.49 | 0.77 |

**Paired comparison** (`comparison_summary.json`). This uses the same 128 parameter draws for both
chambers. In **all** draws B is more acidic at 12 h: the median ΔpH(B − A) is −0.25, with a 5–95 %
range of −0.41 to −0.15. In all draws B also peaks earlier, by a median of 2.4 h. The *direction* is
therefore robust within this model. The *magnitude* is not: the absolute pH bands of A and B overlap
heavily, mostly because flour buffering and LAB vigour are uncertain.

### Overproofing risk at 27 °C

At 27 °C the modelled dough peaks at about 7 h, with a 5–95 % range of 5.7–8.9 h. Rise is lost from
about 9.5 h, and by 16 h the dough is down to 1.81× from 2.09×. **Holding 27 °C for the full 12–16 h
window is therefore predicted to overproof the B dough.**

Even A at 22 °C is predicted to peak at about 9–10 h and to start losing rise at about 13 h. The 10 %
inoculum, 18 % of flour weight, is fairly high for a 12–16 h window.

The agents should watch the observed rise:

- End the bulk ferment or lower the temperature once the rise plateaus or starts to fall. Do not hold
  the setpoint blindly.
- Example from the model: B at 27 °C, cooled to 22 °C at 6 h, gives pH 4.01 at 16 h and delays
  overproof onset to about 11 h. B cooled to 18 °C at 4 h gives pH 4.25 at 16 h and no overproof
  signal within 16 h.

The bake-readiness decision stays with a human.

## 6. Key assumptions, stated plainly

- **Substrate is lumped.** S₀ = 28 g/kg combines free flour sugars with the maltose and glucose that
  flour amylases release during the window. Amylolysis is not a separate dynamic process. Sugar
  exhaustion, at about 17 h at 27 °C and about 21 h at 22 °C, is therefore approximate. In real dough,
  amylases keep supplying maltose.
- **Inoculum.** We assume a *ripe, mature* starter: about 8–12 g lactic and 1–3 g acetic acid per kg
  of starter, and a typical LAB:yeast ratio. The starter is 4–5 days old and may not be mature. If it
  fails the readiness checklist in `configs/experiments.yaml`, these initial conditions are wrong and
  rise will be slower and less predictable. Any commercial-yeast fallback changes the yeast terms and
  must be disclosed.
- **Salt** at 2 % of flour is folded into μ_max and q_s. It is not a separate factor.
- **No oxygen, fructose or electron-acceptor chemistry.** The acetate is empirical (FQ).
- **No ethanol inhibition, and no yeast–LAB interactions** such as maltose/glucose cross-feeding
  beyond shared substrate.
- **The expansion model is phenomenological.** E_max, k_rup, the crowding exponent n and the gluten
  decay rate k_Q are assumptions. They make the rise saturate, hold and collapse at plausible times.
  They are not a rheological model.
- **One well-mixed compartment** at the scheduled temperature.

## 7. Limitations

- **Not calibrated to this flour, starter or vessel.** Use `calibrate()` with real pH and rise data.
  The best first candidates are `buffer_b1` or `ph_flour` for pH and `qs_yeast` or `expansion_max` for
  rise. `calibrate` accepts sparse and missing data and labels the result `calibrated`,
  `poorly_constrained`, `under_determined`, `uncalibrated` or `failed`.
- **Uncertainty envelopes reflect parameter ranges, not structural error.** Their width depends on the
  configured min/max values, which are themselves judgements. Parameters are sampled independently,
  except that the cardinal temperatures are kept ordered. Mode = nominal value; triangular or truncated
  normal distribution.
- **No dough-temperature lag.** Chamber setpoint changes reach the dough over roughly 0.5–1 h in a
  ~380 g mass. Feed the measured dough temperature as the schedule when it is available.
  `calibrate(schedule=None)` builds the schedule from observed temperatures.
- **The pH model is ideal** and uses lumped buffers. Flour-specific buffering can shift absolute pH by
  roughly ±0.2–0.3.
- **The rise is for a representative sample in a straight-sided vessel.** Shaped loaves and the final
  proof are not modelled.
- **The model does not predict flavour, aroma or perceived sourness.**

### CO₂: model versus sensor (important)

`co2_cumulative` is the **modelled cumulative CO₂ production** per kg of dough. Most of it dissolves,
is retained in gas cells, or escapes. It is not a gas-phase concentration and is not measured by any
sensor in this project.

The **BME680 reports gas resistance**, a broad response to VOCs such as ethanol and acetic acid. It
**does not measure CO₂**, and we do **not** map VOC readings to CO₂. Any comparison between the two
signals is qualitative at most and must be labelled as such.

## 8. Sensitivity (simulated)

Full tables are in `data/predictions/sensitivity.md` and `sensitivity_{oat,global}_{A,B}.csv`. They
use one-at-a-time swings across each configured range plus Spearman rank correlations over 128 joint
samples.

**pH at 12 h.** This is dominated by LAB vigour and buffering:

| Parameter | Swing across range, A | Swing across range, B |
|---|---|---|
| `mu_max_lab` | −0.39 | −0.30 |
| `buffer_b1` | +0.38 | +0.30 |
| `t_opt_lab` | +0.34 | +0.18 |
| `xmax_lab` | −0.30 | −0.34 |
| `beta_lactic` | −0.28 | −0.29 |

A higher LAB T_opt *raises* pH at fixed temperature, because 22–27 °C then sits further below the
optimum.

**Peak expansion height.** This is overwhelmingly set by `expansion_max`: about +0.85 in V/V₀ across
the range, with Spearman ρ ≈ 0.9. Next is `k_gluten_decay` at about −0.22 to −0.24. The model cannot
predict absolute rise height without calibration.

**Peak expansion time** (and hence overproof timing):

| Parameter | Effect, A | Effect, B |
|---|---|---|
| `qs_yeast` (yeast fermentation rate) | −4.7 h | −3.3 h |
| `t_opt_yeast` | +3.4 h | not listed |
| `mu_max_yeast` | −3.2 h | −2.1 h |
| `expansion_max` | +2.8 h | +2.2 h |
| `k_gluten_decay` | −2.6 h | −2.1 h |

A single early rise curve from each chamber is the most informative measurement for predicting when to
end fermentation. A few pH readings constrain the buffer/LAB parameters that control acidity.

## 9. Upstream coupling: ALCHEMI molecular descriptors → relative acid terms

`kinetics.apply_molecular_descriptors(params, path="data/alchemi/analysis/molecular_descriptors.json", return_report=False)`
reads per-acid descriptors from the ALCHEMI/AIMNet2 acid–water cluster calculations. The fields are
`assoc_energy_per_water_kcal`, `deprot_energy_n6_kcal` and `hbond_min_A`. The hook uses only
**lactic-minus-acetic differences** to nudge three acid-strength parameters, always inside their
configured `[min, max]`.

| Parameter | Default (`source`) | ALCHEMI signal used | Mapping |
|---|---|---|---|
| `undissociated_lactic_rel_potency` | 0.10 (assumption) | Δ association energy per water; Δ shortest H-bond | See note 1 below |
| `pka_lactic`, `pka_acetic` | 3.86 / 4.76 (literature) | Δ deprotonation energy in the n = 6 cluster | See note 2 below |

1. **Potency mapping.** A hydrophilicity score is computed as
   `h = mean(−ΔE_assoc/2 kcal, −Δd_HB/0.50 Å)`. h > 0 means lactic acid is more strongly hydrated.
   In that case the potency moves toward its minimum by `tanh(h)` of the gap; otherwise it moves
   toward its maximum.
2. **pKa mapping.** The cluster-implied gap is `ΔE_deprot / (RT ln 10)`. 10 % of its discrepancy
   from the literature gap (−0.90) is applied, capped at ±0.025 per acid. The lactic–acetic gap
   therefore moves by at most 0.05.

**What happens with each kind of input:**

- Every adjusted parameter is reported with `source: "alchemi-informed"` alongside its
  `literature_value`, the input differences and the mapping applied (`return_report=True`).
- If the file is **absent, unreadable, partial or non-numeric**, the affected parameters are returned
  unchanged and labelled `literature` or `assumption`. Values are never NaN and never out of range.
- `scripts/simulate_loaves.py` writes `data/predictions/molecular_coupling_report.json`. The baseline
  chamber CSVs stay literature-based for reproducibility. When descriptors exist, the script also
  writes `chamber_{A,B}_prediction_alchemi_informed.csv` and records the effect on pH and peak timing.

**Rationale.** The direction of the potency mapping follows standard weak-acid preservative
reasoning: undissociated acids act after crossing membranes, so a more strongly hydrated, more
hydrophilic undissociated acid is expected to be a weaker inhibitor. Lactic acid carries an extra
hydroxyl group and is the more hydrophilic of the two. The cluster energetics test that relative
ordering. They are not used as an absolute potency.

**Limits, stated honestly:**

- **Cluster-phase energetics are not solution pKa.** The descriptors are 0 K electronic energies of
  small microsolvated clusters (≤ 6 waters) from a machine-learned potential. They contain no
  zero-point, thermal or entropic terms, no bulk dielectric, no conformational averaging, and no ions
  or flour matrix. Solution pKa is a free-energy property of fully solvated species. Errors of several
  kcal/mol, i.e. several pKa units, are normal for this kind of estimate. That is why the pKa
  correction is shrunk to 10 % and capped at a few hundredths of a unit. It works as a consistency
  check on the *relative* ordering, and the report flags whether the cluster reproduces "lactic is
  the stronger acid". It is not a pKa prediction.
- **Hydration energy is not inhibitory potency.** Microbial inhibition also depends on membrane
  transport, intracellular pH buffering and anion accumulation. The hook only moves the potency within
  an already-assumed plausible range.
- **The H-bond signal is deliberately down-weighted.** `hbond_min_A` is the single shortest
  acid–water contact across the optimised structures. It is an extreme value and therefore noisy, so
  its scale is loose (0.50 Å).
- **The scales are assumptions.** The 2 kcal/mol and 0.50 Å scales, the 10 % shrinkage and the ±0.025
  cap (`molecular_coupling` in `configs/kinetics.yaml`) are design choices, not fitted values.
- **The effect is small by design.** With a synthetic test descriptor set that maximally pushes both
  mappings, pH at 12 h changes by about 0.01 and peak timing by ≤ 0.05 h. The molecular evidence
  informs the model; it does not drive it.

### Result with the real descriptor file

This is the file present on 2026-10-31. Its provenance is "real: ALCHEMI/AIMNet2 on NVIDIA RTX
A6000", checkpoint `aimnet2_wb97m_d3_3`. The hook returns `status: partial`.

| Input (lactic − acetic) | Value | What it means |
|---|---|---|
| ΔE_assoc per water | −0.115 kcal/mol | Lactic acid is marginally more strongly hydrated |
| Δ shortest H-bond | +0.148 Å | Lactic acid's shortest acid→water contact is longer |

The combined score is h = −0.12. `undissociated_lactic_rel_potency` therefore moves from 0.10 to
0.124 and is labelled `alchemi-informed`. No deprotonation energies are present, because no ion
pair survived optimisation, so `pka_lactic` and `pka_acetic` stay at their literature values.

The effect on the chamber predictions is negligible:

- pH changes by ≤ 0.002 at 12 h and 16 h.
- Peak time is unchanged.
- B's overproof onset shifts by 0.05 h.

The details are in `data/predictions/molecular_coupling_report.json`. The two signals point in
mildly opposite directions, and both differences are small relative to the method's error. This is
the expected outcome for cluster energetics of two similar carboxylic acids. The coupling is
transparent and traceable, but it is not decisive for the fermentation forecast.

## 10. Downstream hand-off: crumb morphology

`simulation.summarize_for_morphology(df, t_end_h=None) -> dict` returns the following scalars with
`provenance: "simulated"`. `t_end_h` is the actual or planned end of fermentation.

| Key | Meaning |
|---|---|
| `co2_cumulative_g_per_kg` | Total modelled CO₂ production |
| `co2_retained_g_per_kg` | Gas held in the dough cells at `t_end_h` (= column `co2_retained`). It saturates at the gas-holding capacity |
| `co2_retained_peak_g_per_kg` | Peak retained gas |
| `expansion_ratio_final` | Rise at `t_end_h` |
| `expansion_ratio_peak` | Peak rise |
| `peak_expansion_time_h` | Time of peak rise |
| `max_expansion_rate_per_h` | Maximum rate of rise, (V/V₀)/h |
| `max_expansion_rate_time_h` | Time of maximum rate of rise |
| `overproof_flag` | Whether overproof onset (≥ 5 % of the rise lost) occurs before `t_end_h` |
| `overproof_onset_h` | Time of overproof onset |
| `overproof_risk_final` | Overproof signal at `t_end_h` |
| `expansion_fall_from_peak_fraction` | Fraction of the peak rise lost by `t_end_h` |
| `final_ph` | pH at `t_end_h` |
| `final_tta_ml_per_10g` | Titratable acidity at `t_end_h` |
| `final_fermentation_quotient` | Molar lactic:acetic ratio at `t_end_h` |
| `gluten_weakening_index` | See below |

**Gluten-weakening index** (column `gluten_weakening_index`). This is a **phenomenological
assumption**:

```
GWI(t) = 1 − exp(−D(t)/D_ref)
D(t)   = ∫ max(0, pH_onset − pH) · Q10^((T − 25)/10) dt
```

pH_onset = 5.0, Q10 = 2 and D_ref = 8 pH·h (`gwi_dose_ref`, range 4–16). It measures cumulative
temperature-weighted acid exposure, motivated by acid-activated cereal proteolysis and the
weakening of gluten (Gänzle, Loponen & Gobbetti 2008). It is in [0, 1], it never decreases, and it
does not feed back into the ODE. The ODE's own `gluten_integrity` state, which also decays with time
at neutral pH, sets the expansion capacity.

The index is not a rheological measurement. Any mapping from it to crumb openness, wall thickness or
cell size, which is done by the morphology agent, is a further assumption.

`scripts/simulate_loaves.py` writes `data/predictions/chamber_{A,B}_morphology.json` with these
scalars at the window start (12 h), the window end (16 h) and the horizon (24 h), for the baseline
schedules held throughout. At 16 h:

| | A | B |
|---|---|---|
| Peak rise | 2.11 at 9.5 h | 2.09 at 7.2 h |
| Maximum rate of rise | 0.21 /h | 0.26 /h |
| Retained CO₂ | 1.54 g/kg | 1.26 g/kg |
| Overproof flag | True (12.9 h) | True (9.5 h) |
| pH | 4.13 | 3.90 |
| GWI | 0.49 | 0.77 |

## 11. References (verified to exist; values taken from them are flagged in `configs/kinetics.yaml`)

- Gänzle MG, Ehmann M, Hammes WP (1998). Modeling of growth of *Lactobacillus sanfranciscensis* and
  *Candida milleri* in response to process parameters of sourdough fermentation. *Appl Environ
  Microbiol* 64:2616–2623.
- Rosso L, Lobry JR, Flandrois JP (1993). An unexpected correlation between cardinal temperatures of
  microbial growth highlighted by a new model. *J Theor Biol* 162:447–463.
- Presser KA, Ratkowsky DA, Ross T (1997). Modelling the growth rate of *Escherichia coli* as a function
  of pH and lactic acid concentration. *Appl Environ Microbiol* 63:2355–2360.
- Luedeking R, Piret EL (1959). A kinetic study of the lactic acid fermentation. Batch process at
  controlled pH. *J Biochem Microbiol Technol Eng* 1:393–412.
- Gänzle MG, Loponen J, Gobbetti M (2008). Proteolysis in sourdough fermentations: mechanisms and
  potential for improved bread quality. *Trends Food Sci Technol* 19:513–521.
- Thiele C, Gänzle MG, Vogel RF (2002). Contribution of sourdough lactobacilli, yeast, and cereal
  enzymes to the generation of amino acids in dough relevant for bread flavor. *Cereal Chem* 79:45–51.
- Gänzle MG (2014). Enzymatic and bacterial conversions during sourdough fermentation. *Food Microbiol*
  37:2–10.
- De Vuyst L, Neysens P (2005). The sourdough microflora: biodiversity and metabolic interactions.
  *Trends Food Sci Technol* 16:43–56.

pKa values and CO₂ solubility are standard physical-chemistry data (e.g. CRC Handbook). Every other
numeric value is labelled `assumption` or `order-of-magnitude estimate` in `configs/kinetics.yaml`.
