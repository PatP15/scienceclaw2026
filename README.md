# FermentAI

Agent-driven sourdough fermentation experiments for ScienceClaw 2026.

Two miniature sourdough loaves ferment in two independent chambers, each controlled by its own ESP32 and its own "scientist" agent. The agents propose temperature schedules, watch pH and rise, exchange findings with each other, and adjust the experiment as it runs. Remote GPU work (NVIDIA ALCHEMI molecular calculations) runs on NVIDIA Brev; everything the live demo depends on runs locally on a Mac.

## Architecture

```
                       ┌──────────────────────────┐
   internet (optional) │  NVIDIA Brev GPU instance │  ALCHEMI molecular calcs
   ─────────────────── │  (ssh fermentai-gpu)      │  results pulled back to Mac
                       └────────────┬─────────────┘
                                    │ SSH
                       ┌────────────▼─────────────┐
                       │   Mac (central host)      │  Claude Code, scientist agents,
                       │   fermentation kinetics,  │  LLM inference (cloud or
                       │   crumb generation,       │  deterministic fallback),
                       │   dashboard               │  telemetry
                       └───┬──────────────┬───────┘
          local Wi-Fi AP   │              │  local network
                 ┌─────────▼──┐     ┌─────▼──────────────────┐
                 │   iPad     │     │  ESP32 chamber A  ⇄  ESP32 chamber B │
                 │ (judges)   │     │  temp control, sensors, motors        │
                 └────────────┘     └────────────────────────────────────────┘
```

Three design decisions:

- **NVIDIA.** Real ALCHEMI molecular calculations run remotely on Brev. Fermentation kinetics, crumb generation and the dashboard run on the Mac.
- **Decentralization.** The two ESP32s are independent physical control nodes. Their scientist agents keep separate state and exchange experimental findings over the local network. LLM inference runs on the host or a cloud service, never on the ESP32s.
- **Offline demo.** The iPad talks to the Mac over a dedicated local Wi-Fi access point. If cloud inference is unavailable, a transparent deterministic fallback still accepts judge feedback, chooses the next experiment, commands the motors and animates agent-to-agent messages. The demo must not depend on Brev, internet access or GPU availability.

### Authentication modes

A signed-in Claude subscription can back interactive Claude Code and (per Anthropic's October 2026 guidance) `claude -p` / the Agent SDK. A separately configured Anthropic API key incurs Console/API charges. The application must support **both** modes explicitly and never assume they are interchangeable. The ESP32s are never designed around direct subscription access.

## The experiment

Two identical ~380 g doughs; only the fermentation temperature schedule varies.

**Recipe, per loaf** (70.5% hydration, 2% salt):

| Ingredient | Amount |
| --- | --- |
| Bread flour | 200 g |
| Water | 135 g |
| Ripe starter (100% hydration) | 40 g |
| Salt | 4.4 g |

Total flour includes 20 g from the starter; total water includes another 20 g. Same flour and starter for both batches.

| | Chamber A | Chamber B |
| --- | --- | --- |
| Objective | Mild, less acidic | More acidic / tangy |
| Starting setpoint | 22 °C | 27 °C |
| Experiment window | 12–16 h | 12–16 h |
| Hydration & inoculation | Identical | Identical |
| Temperature adjustments | Agent-proposed | Agent-proposed |
| Measurements | pH, rise, temperature | pH, rise, temperature |
| Final evaluation | Blind tasting | Blind tasting |

The setpoints are hypotheses, not guaranteed flavor outcomes. Agents prioritize observed fermentation progress over elapsed time: 12–16 h at 27 °C can overproof a small batch, so they should recommend ending fermentation or lowering temperature rather than holding the setpoint blindly.

**Workflow**

1. Feed the mother starter; confirm it reliably doubles within ~6–8 h before relying on it. The current starter is only 4–5 days old and may need more time. If it isn't ready, use a mature starter; any commercial-yeast fallback must be disclosed.
2. Mix two identical doughs, rest 20–30 min, then several folds over the first 1–2 h.
3. Place each dough in its own covered, food-safe vessel in its chamber. Use a small representative sample for camera rise measurements where helpful.
4. Log temperature schedule, rise and periodic pH. Shape and proof when the dough is ready, not purely by the clock.
5. Bake ~450 °F / 232 °C, covered 18–20 min, then uncovered ~15–20 min, adjusted for the mini loaf.
6. Cool, slice, photograph the crumb, blind-judge sourness, texture and aroma.

**pH caveat.** There is one pH probe. Permanent immersion is experimental; use a separate non-edible sample if the probe isn't food-contact certified, and calibrate/clean before trusting readings in thick dough. We do not claim continuous pH from both chambers.

## NVIDIA Brev (run on the Mac)

The `brev-cli` Claude Code skill is installed in `~/.claude/skills/brev-cli/`. Core commands:

```bash
brew install brevdev/homebrew-brev/brev   # or the release-archive installer
brev login                                 # browser auth
brev ls                                    # existing instances
brev search                                # GPU options and pricing

# after provisioning:
brev refresh
brev shell fermentai-gpu                   # or: ssh fermentai-gpu
nvidia-smi                                 # on the remote

brev stop fermentai-gpu                    # when finished
```

`fermentai-gpu` is the proposed instance name; it does not exist yet. Check availability and pricing before creating it. Stopped instances avoid compute charges but may still incur storage charges, and GPU capacity may not be immediately available again, so copy all important results to the Mac before stopping.

## Install & run

Requirements: macOS (Apple Silicon or Intel), [uv](https://docs.astral.sh/uv/), Python 3.12 (uv installs it).
No GPU, no internet and no Brev account are needed to run the demo. The molecular results are already
cached in `data/alchemi/`.

```bash
uv sync                                   # create .venv/ from uv.lock
cp .env.example .env                      # then set a unique FERMENTAI_API_TOKEN (and optional per-device tokens)
uv run pytest tests/ simulated_devices/tests -q   # all tests (~2 min; tests/integration starts real servers)
```

Main entry points:

| Command | What it does |
| --- | --- |
| `scripts/start_demo.sh` | Backend + dashboard on `0.0.0.0:8000` for real ESP32s; prints the iPad URL |
| `scripts/start_demo.sh --with-simulated-devices` | Same, plus two labelled **SIMULATED** virtual chambers |
| `scripts/offline_check.sh` | Disconnected-mode check: LAN bind, judge loop, outbound-network audit (~15 s) |
| `scripts/run_acceptance.sh` | Every suite + contract + offline check → `docs/test_report.md` (~2–3 min) |
| `uv run python scripts/simulate_loaves.py` | Regenerate the fermentation predictions in `data/predictions/` |
| `uv run python scripts/generate_stls.py` | Regenerate the crumb STLs in `data/stl/` |
| `uv run python -m simulated_devices -h` | Virtual ESP32s on their own (and `--replay` for recorded telemetry) |
| `uv run python -m fermentai.api.contract --check` | Check that `contracts/` and `firmware_contract/` match the API models |

## Demo quick start

1. `scripts/start_demo.sh --with-simulated-devices`. Drop the flag when both ESP32s are powered and
   on the demo Wi-Fi.
2. On the iPad, which must be on the same local Wi-Fi as the Mac (see `docs/network_setup.md`), open
   the URL the script prints: `http://<mac-ip>:8000/?token=<FERMENTAI_API_TOKEN>`. The token is read
   once, stored in the browser and removed from the address bar. Without it the dashboard is
   read-only. On the Mac itself, use `http://127.0.0.1:8000/`.
3. Open **Taste → Next Experiment** and type the judge's words, for example *"Loaf A tastes mild and
   has nice texture. Loaf B is more sour, which I prefer, but the crumb is too dense. I'd like a more
   sour loaf that's still fluffy."* Then tap **Interpret & plan next experiment**.
4. The two scientist agents exchange findings and propose the next experiment. Both chef-hat motors
   get a 3 s `animate_motor` command, and each device acks `accepted → completed`.
5. The offline policy is the default and is labelled "Offline Policy". Live LLM mode
   (`FERMENTAI_LLM_MODE=claude_cli|anthropic_api`) is optional and falls back to the offline policy
   on its own.

Before judges arrive, run `scripts/offline_check.sh` and the physical checklist in
`docs/acceptance_tests.md`. The spoken script is `docs/demo_script.md`.

## Repository layout

```
.
├── PLAN.md, PLAN_MASTER.md      full spec (PLAN) and master orchestration plan (workstreams A–I)
├── AGENTS.md                    conventions for humans/agents working in the repo
├── pyproject.toml, uv.lock      uv-managed Python 3.12 project (package: src/fermentai)
├── configs/                     hardware.yaml (limits, devices, TTLs), kinetics.yaml, experiments.yaml, molecular.yaml
├── contracts/                   JSON schemas v1.1 (telemetry, command, ack, agent message, ...) + examples
├── firmware_contract/           README_FOR_ESP32_AGENT.md + example payloads for the firmware
├── firmware/                    ESP32-S3 PlatformIO firmware (chambers A/B), native unit tests, build logs
├── simulated_devices/           virtual ESP32s: thermal plant, sensors, camera, motor, replay, CLI
├── remote/                      code that runs on the Brev GPU (ALCHEMI/AIMNet2 runners, env setup)
├── scripts/                     start_demo, offline_check, run_acceptance, Brev setup/sync, generators
├── src/fermentai/
│   ├── api/                     FastAPI backend, SQLite store, device auth, commands, SSE, contract tools
│   ├── agents/                  scientist agents, sensory parser, offline policy, LLM adapter, findings log
│   ├── fermentation/            sourdough kinetics model, scenarios, uncertainty, calibration, plots
│   ├── molecular/               ALCHEMI structure builders, runner, analysis (Mac-side)
│   ├── geometry/                crumb STL generator, mesh validation, simulation→crumb mapping, renders
│   ├── design/                  in-silico agent-collaboration benchmark
│   └── dashboard/static/        offline iPad dashboard (plain ES modules, vendored Plotly)
├── tests/                       unit tests (test_<area>_*.py)
│   └── integration/             end-to-end: real server + virtual devices + agents + network audit
├── data/
│   ├── alchemi/                 real GPU results, logs, environment report, analysis   (real)
│   ├── predictions/             fermentation forecasts A/B, uncertainty, charts         (simulated)
│   ├── stl/                     crumb specimens A/B + validation + previews             (simulated)
│   ├── demos/                   scripted replay scenarios + dashboard screenshots       (replay)
│   ├── benchmarks/, figures/    benchmark outputs, pitch figures
│   └── experiments/, images/    runtime state (findings log, uploaded frames; gitignored/regenerated)
└── docs/                        see below
```

Where to read next:

- `PLAN.md`, the spec, including §16 acceptance tests and §17 required outputs. `PLAN_MASTER.md` covers
  the workstreams, contracts and the §14 judge workflow.
- `docs/STATUS.md` is the consolidated status: implemented, tested, simulated, hardware-unverified,
  known bugs, Brev spend.
- `docs/acceptance_tests.md` gives PLAN §16 item by item. `docs/test_report.md` is the last automated
  run. `docs/validation_status.md` says what is validated against what, and what is not.
- `docs/network_setup.md` (offline LAN, iPad, ESP32 addressing), `docs/demo_script.md` (3-minute script),
  `docs/pitch/slides.md`.
- `docs/simulation_provenance.md` (ALCHEMI), `docs/scientific_assumptions.md` (kinetics),
  `docs/morphology_coupling.md` (crumb STLs), `docs/benchmark_results.md` (agent benchmark).
- `contracts/README.md` and `firmware_contract/README_FOR_ESP32_AGENT.md` for the device interface.
