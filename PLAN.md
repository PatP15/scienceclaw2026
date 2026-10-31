# FermentAI — ScienceClaw Hackathon
## Mac-Based Simulation, NVIDIA Brev and Demonstration Agent

You are the lead software, computational chemistry, scientific modeling and simulation engineer for **FermentAI**.

Implement the project directly on the user's Mac. Use NVIDIA Brev GPU instances for remote ALCHEMI computations over SSH.

**Begin implementation, not just planning.** Make reasonable technical decisions independently. Run tests, preserve outputs, document failures and continue to the next milestone.

The user has already installed an NVIDIA-provided skill for provisioning Brev instances. Discover and follow that installed skill before attempting manual provisioning.

## 1. Mission

FermentAI is a decentralized food-science laboratory that allows AI scientist agents to control physical sourdough fermentations, share experimental findings, generate scientifically informed predictions and design subsequent experiments based on human sensory feedback.

The hackathon demonstration includes:

- Two independently controlled sourdough fermentation chambers.
- Two ESP32-S3-CAM boards.
- Two independently driven Peltier heating/cooling systems.
- Temperature, humidity and gas-response sensors.
- At least one experimental pH sensor.
- Two side-view cameras tracking dough or starter expansion.
- Magnetic door switches.
- Two small motorized chef-hat automata, activated when agents propose new experiments.
- Two small sourdough test loaves, one intended to be milder and one more acidic.
- Real NVIDIA ALCHEMI molecular computations.
- A separate sourdough fermentation kinetic model.
- Two Formlabs-compatible STL crumb cross-sections.
- A locally hosted dashboard accessible from an iPad.
- A judge-facing text interface accepting sensory feedback.

The long-term concept is an international network of autonomous kitchen scientists collecting and sharing scientific evidence from distributed food-production environments.

## 2. Computing architecture — mandatory

The user's **Mac is the primary development and demonstration machine**.

All source code, dashboards, experiment records, model parameters and cached simulation results must be maintained locally.

NVIDIA Brev is used only for remote GPU-accelerated molecular calculations.

Do not develop the application primarily on the remote GPU machine.

Do not require remote GPU access to demonstrate the completed application.

Architecture:

```text
Mac:
  Claude Code
  Git repository
  Python environment
  Fermentation simulator
  Local FastAPI server
  Two scientist-agent processes
  SQLite experiment database
  iPad dashboard
  STL generation and analysis
       |
       | Brev CLI / SSH
       v
NVIDIA Brev:
  Linux GPU
  CUDA / PyTorch
  NVIDIA ALCHEMI
  AIMNet2
  Molecular optimizations
       |
       | Return results
       v
Mac:
  Cache optimized structures
  Cache simulation results
  Visualize molecular experiments
  Feed scientific findings into agent context
```

Develop shell scripts to automate synchronization of input files, remote execution, result downloading and cleanup.

Use SSH, SCP or rsync over authenticated SSH. Do not expose SSH passwords, private keys or authentication tokens in the repository.

## 3. NVIDIA Brev configuration

The user has $150 of NVIDIA Brev credits.

Read the locally installed NVIDIA skill and official documentation:

- https://docs.nvidia.com/brev/latest/getting-started/quickstart
- https://docs.nvidia.com/brev/latest/cli/getting-started
- https://github.com/NVIDIA/nvalchemi-toolkit
- https://nvidia.github.io/nvalchemi-toolkit/

Discover current GPU pricing, availability and credit balance.

Select one suitable NVIDIA GPU. Prefer inexpensive single-GPU compute sufficient for AIMNet2 calculations.

Use the existing Brev account through browser authentication.

Do not request the user to paste credentials into the repository or chat transcript.

For initial exploration:
- Target spending at most $40 in Brev credits.
- Do not launch an instance without verifying its listed hourly rate and that the intended credits apply.
- Ask for approval before spending beyond the exploration budget.
- Save checkpoints frequently.
- Stop GPU instances when computations finish.
- Preserve all results on the Mac before releasing compute.

Useful CLI operations:
- `brev list`
- `brev search`
- `brev login`
- `brev refresh`
- `brev shell <instance>`
- `ssh <instance>`
- `brev stop <instance>`

Check exact current CLI syntax using the installed NVIDIA skill.

Save an environment report with:
- GPU model
- CUDA version
- PyTorch version
- ALCHEMI version
- AIMNet2 checkpoint
- Python version
- Dependency lockfile
- Runtime
- Estimated GPU compute cost

## 4. Real NVIDIA ALCHEMI calculations

Perform genuine, reproducible atomistic computations using NVIDIA ALCHEMI.

Relevant official examples:
- https://nvidia.github.io/nvalchemi-toolkit/examples/advanced/08_aimnet2_ewald_pipeline.html
- https://nvidia.github.io/nvalchemi-toolkit/examples/basic/05_nvt_langevin.html

First reproduce an official AIMNet2 energy/force calculation.

Do not proceed to sourdough-related molecular experiments until the GPU successfully calculates real finite energies and forces.

Study two molecules:
1. Lactic acid
2. Acetic acid

Use RDKit to generate explicitly hydrogenated molecular structures.

Construct water clusters of 1, 3 and 6 molecules around each acid.

Generate multiple initial conformations with reproducible seeds.

Optimize using documented ALCHEMI-supported methods.

For each calculation, preserve:
- Initial coordinates
- Optimized coordinates
- Total energy
- Maximum force
- Convergence status
- Iteration count
- Molecular identities
- Random seed
- Potential checkpoint
- Actual execution log

Produce energy summaries, hydrogen-bond analyses and interactive molecular visualizations.

Calculate cluster association energies using consistent isolated-species reference calculations.

Optional: Run short temperature-dependent molecular-dynamics experiments if geometry optimization is already complete.

**Scientific constraint:** ALCHEMI does not directly predict sourdough pH, gas production, crumb structure or perceived sourness.

Use ALCHEMI to investigate molecular interactions relevant to fermentation products. Use a separate macroscopic model for fermentation kinetics.

Do not manufacture or substitute GPU results.

## 5. Sourdough experiment specification

Two miniature loaves will be made from the same mother starter.

The starter is currently approximately four to five days old.

Before assuming it is ready for bread, implement a readiness checklist:
- Reliably doubles within approximately 6–8 hours after feeding
- Contains abundant bubbles
- Has a clean fermented aroma
- No visible mold or abnormal discoloration

If the starter is not mature, report the risk and provide an explicitly labeled contingency.

Recipe per loaf:

- 200 g bread flour
- 135 g water
- 40 g ripe starter at 100% hydration
- 4.4 g salt

Total dough mass: approximately 379.4 g.

Total baker's hydration, accounting for the starter: approximately 70.5%.

Use identical flour, hydration, feeding history, starter inoculation and initial mixing procedure.

The intended experimental observation window is 12–16 hours, including fermentation and proofing as appropriate.

Two initial experimental profiles:

**Chamber A: Mild hypothesis**
- Initial temperature target: 22°C
- Desired outcome: relatively mild acidity with sufficient expansion

**Chamber B: More-acidic hypothesis**
- Initial temperature target: 27°C
- Desired outcome: increased acidification and detectable flavor differences

The agents may independently revise temperature schedules based on predicted and observed fermentation progress.

They must not blindly hold an elevated temperature for 16 hours if signs of overproofing appear.

The actual bake-readiness decision remains subject to human confirmation.

Do not claim that these temperatures guarantee distinct flavor profiles.

Treat the distinction between total acidity, lactic/acetic acid balance, pH and perceived sourness carefully.

## 6. Fermentation kinetic model

Build a CPU-based model using SciPy.

States:
- Fermentable substrate concentration
- Yeast biomass proxy
- Lactic acid bacterial biomass proxy
- Lactic acid
- Acetic acid
- Cumulative CO₂ generation

Use documented and configurable differential equations.

Include:
- Substrate-limited growth
- Temperature-dependent activity
- Substrate consumption
- Acid production
- CO₂ generation
- Acid inhibition where appropriate
- Empirical or buffering-aware pH mapping
- Optional phenomenological dough expansion model

Model parameters must have units, literature references and uncertainty ranges.

Calibrate available parameters when physical observations are supplied.

The model must accept time-varying temperature schedules.

Produce:
- Temperature vs time
- pH vs time
- Acid concentration vs time
- Cumulative modeled CO₂ vs time
- Predicted dough expansion
- Uncertainty envelopes

Generate two comparison scenarios for the sourdough experiments.

Distinguish model-predicted CO₂ from BME680 environmental gas resistance. The BME680 does not directly measure CO₂.

Do not invent a calibrated mapping from environmental VOC readings to CO₂ concentration.

## 7. Physical data contract

Tomorrow a second development agent will implement the ESP32 firmware.

You must create a frozen, documented interface for that work.

Required telemetry schema:

```json
{
  "schema_version": "1.0",
  "chamber_id": "A",
  "experiment_id": "trial_001",
  "timestamp_utc": "2026-10-31T12:00:00Z",
  "chamber_temperature_c": 24.5,
  "starter_temperature_c": 23.9,
  "relative_humidity_percent": 72.0,
  "gas_resistance_ohms": 105432.0,
  "ph": null,
  "ph_method": null,
  "starter_height_mm": 28.0,
  "door_open": false,
  "peltier_mode": "idle",
  "temperature_setpoint_c": 24.0,
  "motor_running": false,
  "source": "simulated",
  "quality_flags": []
}
```

The payload above is only an illustrative test fixture, not a real sensor reading.

Support nullable fields, missing sensors and timestamps.

Create REST endpoints:

- `POST /api/v1/telemetry`
- `POST /api/v1/images`
- `GET /api/v1/chambers/{id}/state`
- `GET /api/v1/chambers/{id}/desired-control`
- `POST /api/v1/control-ack`
- `POST /api/v1/experiments`
- `POST /api/v1/simulations/run`
- `POST /api/v1/taste-feedback`
- `GET /api/v1/agents/{id}/messages`

All commands must be:
- Versioned
- Authenticated
- Timestamped
- Logged
- Bounded by safety limits
- Acknowledged by the physical device

Use short-lived command IDs or expiration times to prevent replay of stale actuator instructions.

Do not allow the LLM to control electrical power directly.

The ESP32 must locally enforce temperature limits, sensor failure handling, fan protection and safe switching between heating and cooling modes.

Implement sensor ingestion independently from molecular simulation.

## 8. Local network and offline demonstration

The demonstration must work without external Wi-Fi.

Assume:
- Two ESP32-S3 devices
- One Mac
- One iPad
- A dedicated local Wi-Fi access point or travel router

All devices connect to the same local network.

The Mac runs the backend and serves the dashboard on a LAN-accessible interface.

Prefer FastAPI plus locally bundled HTML/JavaScript/Plotly assets, avoiding external CDN dependencies.

Use SQLite for local state and event logging.

Make dashboard access possible through a stable LAN IP address or local hostname.

Support:
- Live local telemetry
- WebSocket or server-sent-event updates
- Cached real NVIDIA results
- Cached fermentation predictions
- Offline deterministic scientific decision policies
- Offline visualizations and taste feedback submission

Do not require:
- NVIDIA Brev availability
- Cloud hosting
- Internet-connected iPad
- A live GPU
- A running cloud LLM

The dashboard must not accidentally depend on an online CDN.

Document and test an entirely disconnected local-network mode.

## 9. Hosted LLM integration

Use a configurable model adapter.

Preferred online options:
- Anthropic Claude through supported subscription-backed CLI/Agent SDK usage, where permitted
- Anthropic API with a separately configured developer key

Inspect the currently available model identifiers instead of hardcoding a possibly nonexistent model version.

The user's Claude Code subscription may support programmatic CLI usage subject to limits. Verify the actual account and authentication configuration.

Do not embed Claude credentials in ESP32 firmware.

Each chamber has an independent logical scientist agent with:
- Its own experimental context
- Separate reasoning history
- Local measurements
- Separate hypotheses
- Separate experiment decisions

Use structured message exchange rather than a single coordinator writing both decisions.

The Mac backend may host and schedule the logical agents, but preserve independent decision ownership.

Implement a fully offline fallback policy that can still select among validated candidate temperature schedules.

Label this as a deterministic fallback, not live cloud LLM inference.

## 10. Agent collaboration and scientific benchmarking

The system should show that agent communication changes experimental decisions.

Implement:
- Scientist A
- Scientist B
- A shared append-only scientific findings log

Agents publish:
- Observation
- Experimental hypothesis
- Prediction
- Uncertainty
- Proposed next measurement
- Proposed intervention
- Rationale
- Source of evidence

Agents can read and challenge peer findings.

For evaluation, compare:
1. Agents making independent experimental choices without peer communication.
2. Agents sharing evidence and coordinating experiment selection.

Use a fixed computational experiment budget.

Measure:
- Number of redundant experiments
- Parameter uncertainty reduction
- Prediction error on held-out simulated scenarios
- Information gained per experiment

Run the benchmark using the fermentation simulation.

Do not force the collaborative system to win. Report actual observed benchmark performance.

Keep synthetic benchmarks clearly labeled as in-silico experiments.

## 11. Judge-facing interactive demonstration

This is one of the most important deliverables.

The dashboard must contain a large interactive panel titled:

**What did the bread taste like?**

The judge or operator should be able to submit feedback for both loaves.

Example input:

"Loaf A was mild and fluffy. Loaf B was more sour but a little dense. I'd like a stronger sour flavor without losing the airy texture."

The system must:

1. Accept and save the feedback.
2. Show the feedback being interpreted into structured sensory preferences.
3. Compare it with the recorded experiments.
4. Create or select a new hypothesis.
5. Recommend a new temperature schedule.
6. Publish an agent-to-agent scientific message.
7. Have the second agent respond with an independent critique or complementary proposal.
8. Display a proposed next experiment.
9. Send a short, safe command to the ESP32s that activates their decorative chef-hat motors.
10. Display a visibly updated experiment plan.

The motor animation is a physical indicator of the new experiment being planned, not proof that the machine has mixed, fed or baked another loaf.

Provide both:
- **Live mode:** Uses available measurements and online LLM inference.
- **Offline demonstration mode:** Uses cached measurements, transparent scripted examples and a deterministic decision engine.

The demonstration must continue functioning if the internet fails.

All scripted data must be labeled "Replay" or "Simulated".

The judge should not need to type any special exact phrase to make the demo work. Handle arbitrary ordinary flavor descriptions.

Separate sensory-feedback parsing from physical actuator commands.

The motor command should be bounded in duration, independently cancellable and unable to alter the Peltier safety controller.

## 12. Formlabs bread morphology

Generate:

- `crumb_mild.stl`
- `crumb_sour.stl`

Each should be a 3D-printable, open-faced bread-crumb cross-section.

Use realistic but parameterized geometry:
- Porosity
- Pore sizes
- Pore eccentricity
- Spatial clustering
- Solid connecting wall thickness
- Overall morphology

Use a stochastic pore model with explicit random seeds.

Output approximate 80 × 80 × 12 mm models, adjusted if required for the printer.

Validate:
- Watertightness
- Positive volume
- No self-intersections
- No disconnected fragments
- Minimum structural thickness
- No unvented cavities trapping uncured resin

Generate side-by-side PNG renders and geometric summary statistics.

Important: Sourdough sourness does not uniquely determine crumb structure.

The two initial printed models are illustrative hypotheses about possible morphology differences, not direct ALCHEMI predictions.

When real loaf cross-section images become available, implement image analysis to measure observed pore-area fractions and pore-size distributions.

Compare physical bread photographs against the generated morphology parameters.

Prepare optional integration with the official Formlabs PreForm API:

https://developer.formlabs.com/api-reference/

Prefer exporting both validated STLs first. Printer settings and automated support generation are secondary.

Do not send print jobs without human confirmation.

Printed resin artifacts are for display, not direct food contact.

## 13. iPad dashboard

Implement a polished full-screen dashboard with these sections:

### Live Laboratories
- Chamber A and B side by side
- Current temperatures
- Temperature setpoints
- Peltier mode
- Starter-height measurements
- Humidity and gas-response signal
- Available pH observations
- Door state
- Camera snapshots
- Agent status and latest decision

### Digital Twin
- Simulated fermentation timelines
- Predicted vs observed pH
- Predicted vs observed expansion
- Lactic/acetic acid estimates
- CO₂ model outputs
- Uncertainty bands
- Model calibration status

### NVIDIA Molecular Explorer
- Actual ALCHEMI optimized structures
- Acetic and lactic acid clusters
- Interaction energy comparisons
- Hydrogen-bond structures
- Simulation provenance

### Taste Feedback / Next Experiment
- Free-text judge input
- Structured sensory interpretation
- New experimental hypotheses
- Agent-to-agent communications
- Recommended experiment
- Start automaton animation button

Distinguish real observations, simulation predictions, imported human measurements and scripted replay at every point.

Make the dashboard usable on a landscape-oriented iPad.

## 14. Repository structure

Use a Python monorepo:

```text
fermentai/
  README.md
  AGENTS.md
  pyproject.toml
  uv.lock
  .env.example
  configs/
    experiments.yaml
    kinetics.yaml
    molecular.yaml
    hardware.yaml
  src/fermentai/
    molecular/
      structures.py
      alchemi_runner.py
      analysis.py
    fermentation/
      kinetics.py
      calibration.py
      simulation.py
    agents/
      scientist.py
      communication.py
      offline_policy.py
      llm_adapter.py
    design/
      optimizer.py
      benchmark.py
    geometry/
      crumb.py
      validation.py
      image_analysis.py
    api/
      app.py
      schemas.py
      routes.py
    dashboard/
      ...
  scripts/
    setup_brev.sh
    run_remote_alchemi.sh
    collect_brev_results.sh
    simulate_loaves.py
    generate_stls.py
    benchmark_agents.py
    start_demo.sh
    stop_brev.sh
  data/
    alchemi/
    predictions/
    experiments/
    telemetry/
    images/
    stl/
    demos/
  firmware_contract/
    README_FOR_ESP32_AGENT.md
    telemetry_example.json
    command_example.json
  tests/
    ...
  docs/
    scientific_assumptions.md
    validation_status.md
    simulation_provenance.md
    network_setup.md
    demo_script.md
```

Use Python 3.12 where compatible.

Recommended libraries:
- nvalchemi-toolkit
- PyTorch
- RDKit
- ASE
- SciPy
- NumPy
- pandas
- FastAPI
- Pydantic
- SQLite
- Plotly
- trimesh
- pytest
- uv

Use simple, reliable modules over unnecessary orchestration frameworks.

## 15. Execution order

Prioritize work so the project is demonstrable even if later features fail.

### Priority 0: Mac/Brev connection
- Inspect installed NVIDIA skill.
- Verify `brev` CLI.
- Verify login and available credits.
- Choose affordable GPU.
- Open SSH session.
- Run `nvidia-smi`.

### Priority 1: Real NVIDIA result
- Install ALCHEMI.
- Run official AIMNet2 example.
- Optimize real acid/water structures.
- Save energies, coordinates and metadata locally.

### Priority 2: Fermentation simulator
- Implement ODE model.
- Run two experimental temperature schedules.
- Generate 12–24-hour predictions.
- Plot pH, CO₂, acid production and expansion.
- Document assumptions and sensitivity.

### Priority 3: Formlabs STLs
- Generate two open-faced crumb structures.
- Validate meshes.
- Render and save previews.
- Export STLs.

### Priority 4: Hardware API
- Define JSON schemas.
- Implement telemetry ingestion.
- Implement temperature-command retrieval.
- Implement motor animation commands.
- Generate tomorrow's ESP32 handoff documentation.

### Priority 5: Offline judge demo
- Create local dashboard.
- Implement taste feedback.
- Implement offline agent-policy fallback.
- Show agent-to-agent messages.
- Generate motor commands for next-experiment announcements.
- Verify operation without internet.

### Priority 6: Online agents and validation
- Integrate Claude-based scientist reasoning.
- Compare independent and collaborative policies.
- Enable ingestion of real physical data.
- Implement model calibration and sensory feedback history.

Do not sacrifice a working demo to pursue optional complexity.

## 16. Acceptance tests

Before completion, demonstrate that:

1. Real NVIDIA ALCHEMI energies and forces were calculated on the remote GPU.
2. Results were successfully transferred back to the Mac.
3. The Mac can reproduce fermentation forecasts without a GPU.
4. Both crumb STLs are valid and visually distinguishable.
5. The API can ingest simulated test telemetry for both chambers.
6. The API can return independent temperature setpoints.
7. The dashboard is reachable from an iPad or second browser over a local network.
8. The dashboard does not require internet, cloud fonts or CDN resources.
9. Judge feedback results in an actual experiment recommendation.
10. A motor command can be emitted and logged, with a simulated device acknowledged during software-only testing.
11. Distinct scientist agents exchange structured findings.
12. The system can clearly explain which data are real, modeled, estimated or replayed.

## 17. Required outputs

At completion, deliver:

- Working code repository on the Mac
- Mac installation and startup instructions
- Brev SSH execution and result-sync scripts
- Actual NVIDIA molecular simulation files
- Actual GPU computation logs
- Two fermentation prediction CSV files
- Two predicted-versus-observed-ready chart sets
- Two valid crumb STL files
- STL preview images
- Local FastAPI backend
- iPad-ready dashboard
- Agent communication and decision engine
- Judge sensory feedback interface
- Offline fallback demonstration
- `README_FOR_ESP32_AGENT.md`
- Exact firmware API contracts
- Test report
- Scientific assumptions document
- Validation status report
- Four-slide pitch outline
- Three-minute demonstration script

## 18. Reporting and operating rules

Work autonomously through the milestones.

After each milestone, preserve working files and test results.

Keep the Mac repository as the source of truth.

Never fabricate ALCHEMI outputs, physical measurements, sensory observations or benchmark wins.

A convincing simulation is useful, but it must be identified as a simulation.

If GPU work fails, preserve the error report and finish the fermentation simulator, local dashboard, API and STLs.

If internet access fails, ensure the offline demo still accepts judge feedback and generates a real local motor command when hardware is connected.

If the actual hardware has not been connected, demonstrate the command using a labeled simulated device.

Never allow a language-model decision or judge-input text to bypass electrical or thermal safety checks.

Do not start paid GPU resources beyond the approved budget, launch physical print jobs, or activate high-power experimental hardware without necessary approvals and safeguards.

**Success means a real, working prototype: a Mac-hosted collective of two scientific agents, genuine remotely computed NVIDIA molecular evidence, reproducible fermentation predictions, two Formlabs-printable morphology specimens, and an offline-capable taste-to-experiment demonstration.**
