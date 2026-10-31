# FermentAI — Revised Hackathon Agent Plan (source context, verbatim)

We're going to make your Mac the central development and demonstration machine, with NVIDIA Brev used only for remote GPU computations. Claude Code will run locally on the Mac, connect to Brev through SSH, execute the NVIDIA ALCHEMI simulations, retrieve the results, and integrate everything into the same codebase that will eventually communicate with your two ESP32 chambers.

This is a better setup for the hackathon because your actual demo won't depend on Brev, a working internet connection, or an available GPU.

## 1. Final architecture

Logical architecture. The Brev connection requires internet, but the physical experiment, local agent-policy fallback, telemetry and dashboard do not.

Three key decisions:
- NVIDIA: Run real ALCHEMI molecular calculations remotely, while fermentation kinetics, crumb generation and the dashboard run on your Mac.
- Decentralization: The two ESP32s are independent physical control nodes. Their associated scientist agents maintain separate states and exchange experimental findings over a local network. The LLM inference itself runs on a host or cloud service, not on the ESP32s.
- Offline demo: The iPad communicates with your Mac through a dedicated local Wi-Fi access point. If cloud inference is unavailable, a transparent deterministic fallback can still accept judge feedback, choose a new experiment, command the motors, and animate the agent-to-agent messages.

There's one authentication distinction: your Claude subscription can cover interactive Claude Code usage, but Claude API requests from the running application are a separately configured developer capability, with billing depending on your plan. We should not design the ESP32s around direct subscription access.

## 2. The actual sourdough experiment

Two identical miniature sourdough loaves, each approximately 380 g before baking. Vary the fermentation temperature schedules, not the ingredients, so the comparison is interpretable.

Recipe — per test loaf (~380 g dough, 70.5% total hydration, 2% salt):
- Bread flour 200 g
- Water 135 g
- Ripe starter, 100% hydration 40 g
- Salt 4.4 g

Total flour includes 20 g from the starter; total water includes another 20 g. Make two identical batches using the same flour and starter.

| Condition | Chamber A | Chamber B |
| --- | --- | --- |
| Objective | Mild, less acidic | More acidic/tangy |
| Starting setpoint | 22°C | 27°C |
| Total experiment window | 12–16 hours | 12–16 hours |
| Hydration and inoculation | Identical | Identical |
| Temperature adjustments | Agent-proposed | Agent-proposed |
| Primary measurements | pH, rise, temperature | pH, rise, temperature |
| Final evaluation | Blind tasting | Blind tasting |

The 22°C and 27°C conditions are hypotheses, not guaranteed flavor outcomes. A warmer starter can acidify faster, but prolonged cooler fermentation can also produce pronounced sourness, particularly a sharper acetic character.

Instruct the agents to prioritize observing actual fermentation progress. Twelve to sixteen hours at 27°C could overproof a small dough batch; the agents should recommend ending fermentation or lowering temperature rather than blindly holding the starting setpoint.

Baking workflow:
1. Feed the mother starter and verify that it becomes visibly vigorous.
2. Mix two identical doughs, rest 20–30 minutes, then develop gluten with several folds over the first 1–2 hours.
3. Put each dough into its own covered, food-safe fermentation vessel in its assigned chamber. Use a small representative sample where helpful for camera rise measurements.
4. Record the temperature schedule, rise, and periodic pH measurements. Shape and proof when the dough is ready, rather than using elapsed time as the only criterion.
5. Bake at approximately 450°F (232°C), initially covered for 18–20 minutes and then uncovered for roughly 15–20 minutes, adjusting for the miniature loaf size and oven.
6. Cool before slicing, photograph the crumb, and have judges compare sourness, texture, and aroma.

The starter has only been going for four to five days. Make sure it reliably doubles within about 6–8 hours after feeding before relying on it to raise both loaves. A new starter may need additional days even when it looks bubbly. If it isn't ready, use a known mature starter if one is available; any commercial-yeast-assisted fallback must be disclosed.

For the single pH probe, keep permanent immersion experimental. Use a separate, non-edible representative sample if the probe isn't certified for food contact, and don't assume the probe is reliable in thick dough without calibration and cleaning. Because there is only one, do not claim continuous pH measurements from both chambers.

## 3. NVIDIA Brev — Mac CLI setup

NVIDIA officially supports macOS CLI access, browser authentication, named-instance SSH connections, and stopping GPU instances while retaining workspace files.

The Claude Code agent should use the installed NVIDIA brev-cli skill. Core commands (run on the Mac, not the remote GPU):

```bash
brew install brevdev/homebrew-brev/brev
brev login
brev list
brev search
# after provisioning:
brev refresh
brev shell fermentai-gpu
ssh fermentai-gpu
nvidia-smi            # on the remote
brev stop fermentai-gpu
```

`fermentai-gpu` is the proposed name, not an instance that already exists. Inspect current GPU availability and pricing before creating it. Stopped instances avoid compute charges but may retain storage charges. Save all important results to the Mac before stopping, because GPU capacity may not be immediately available again.

Authentication correction: Anthropic's October 2026 guidance says `claude -p` and the Agent SDK can currently use a signed-in Claude subscription's limits, so a subscription-backed local inference option is worth testing. A separately authenticated Anthropic API key can still incur Console/API charges, so the software must support both authentication modes rather than assuming they are interchangeable.
