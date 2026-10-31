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
  3.5–7" (Gänzle et al. 1998). Yeast is instead inhibited linearly by undissociated acetic acid in
  the aqueous phase: `f_HAc = max(0, 1 − [HAc]/150 mmol L⁻¹)`. Complete inhibition at 150 mmol/L is
  from Gänzle et al. (1998).

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
| pH at 12 h (5–95 %) | 4.31 (3.99–4.62) | 4.02 (3.83–4.33) |
| pH at 16 h (5–95 %) | 4.13 (3.86–4.48) | 3.91 (3.72–4.25) |
| Final pH at 24 h | 4.03 | 3.90 |
| Lactic / acetic at 16 h (g/kg) | 4.7 / 0.88 | 6.5 / 1.06 |
| TTA at 24 h (mL 0.1 N / 10 g) | 9.4 | 10.8 |
| Peak rise, V/V₀ (5–95 %) | 2.11 at 9.4 h (peak 7.0–12.6 h) | 2.09 at 7.2 h (peak 5.5–9.4 h) |
| Time to 2× rise | 7.2 h | 5.6 h |
| Overproof onset (≥ 5 % of rise lost) | 12.8 h | 9.5 h |

**Paired comparison** (`comparison_summary.json`). This uses the same 128 parameter draws for both
chambers. In **all** draws B is more acidic at 12 h: the median ΔpH(B − A) is −0.25, with a 5–95 %
range of −0.40 to −0.14. In all draws B also peaks earlier, by a median of 2.3 h. The *direction* is
therefore robust within this model. The *magnitude* is not: the absolute pH bands of A and B overlap
heavily, mostly because flour buffering and LAB vigour are uncertain.

### Overproofing risk at 27 °C

At 27 °C the modelled dough peaks at about 7 h, with a 5–95 % range of 5.5–9.4 h. Rise is lost from
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

## 9. References (verified to exist; values taken from them are flagged in `configs/kinetics.yaml`)

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
