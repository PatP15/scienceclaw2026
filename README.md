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

## Repository layout

Populated as the plan is executed. See `PLAN.md` (when present) for the task breakdown and `docs/` for source context.
