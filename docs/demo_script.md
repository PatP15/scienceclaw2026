# FermentAI: three-minute live demo script

Two roles: the **Presenter** talks, and the **Driver** holds the iPad. One person can do both. Lines in
quotes are spoken. Lines starting with *Tap* are actions on the iPad, which is in landscape on the
dashboard. Tab, button and control names are the exact labels in
`src/fermentai/dashboard/static/index.html`.

Spoken words come to about 270 (about 1 min 55 s at 140 wpm). The other ~65 s is for tasting, typing
and waiting for the agents.

---

## Before the judges arrive (T−10 min, not timed)

1. **Start the backend.** On the Mac, run `scripts/start_demo.sh`.
   - If no ESP32 is connected or responding, use `scripts/start_demo.sh --with-simulated-devices`
     instead. Telemetry is then labelled **SIMULATED** on every screen.
2. **Open the dashboard.** Open the printed `http://<mac-ip>:8000/?token=…` URL on the iPad. The top-bar
   pill should change from **Read-only** to write access, and the connection pill should go green.
3. **Check the decision mode.** Go to **Taste → Next Experiment**, then the mode switch:
   - **Offline deterministic** is the default. It is labelled and never needs the internet.
   - **Live LLM** is only for when internet and Claude are confirmed working right now.
4. **Run the disconnected-mode test** in `docs/network_setup.md` §6 once, so that a motor command
   reaches `completed`.
5. **Set out the loaves.** Slice Loaf A (blue card) and Loaf B (orange card) on two plates. Put the
   printed crumb specimens, mild and sour, next to them.
6. **Prepare the Replay fallback.** In the **Replay example** dropdown, find a scripted example but
   leave it unselected.

---

## The run (0:00–3:00)

| Time | Screen / action | Say |
|---|---|---|
| **0:00–0:20** | Hand each judge a slice of A and B. *Tap* **Live Laboratories**. | "Two loaves, same dough, two temperatures. These are the two chambers that fermented them. Each one is an ESP32 with a Peltier, sensors and a camera, run by its own AI scientist agent." |
| **0:20–0:40** | Point at the A and B cards: temperature, setpoint, rise, provenance badge. | "Chamber A is blue and holds 22 degrees. B is orange and warmer, at 27. Every number carries a badge saying whether it's real, simulated or replay." |
| **0:40–1:00** | *Tap* **Digital Twin**. Point at the pH and rise curves. | "This is our fermentation model, and it is a simulation. It predicts B gets more acidic and overproofs earlier, so the agents learn to cool it down partway." |
| **1:00–1:15** | *Tap* **NVIDIA Molecular Explorer**. | "The acid chemistry comes from real NVIDIA ALCHEMI calculations on a Brev GPU, cached on this Mac, so the demo doesn't need the GPU or the internet." *(When the numbers are in, add: "[ALCHEMI: ΔE_assoc lactic vs acetic — pending]".)* |
| **1:15–1:45** | *Tap* **Taste → Next Experiment**. Hand the iPad to a judge, or type their words for them into the box under **What did the bread taste like?** | "Now you're the scientist. What did the bread taste like? Plain words are fine, there's no magic phrase." |
| **1:45–1:50** | *Tap* **Interpret & plan next experiment**. | "It's parsing that into sensory preferences." |
| **1:50–2:20** | Scroll the step list as it fills: interpretation, comparison, Agent A hypothesis, Agent B critique, recommended experiment. Then point at **Updated experiment plan**. | "Here are your words as structured preferences. Agent A proposes a new temperature schedule. Agent B checks it against its own model and pushes back. They agree, and the plan on the right updates." |
| **2:20–2:35** | Look at the chambers. The chef-hats wiggle for up to 5 s. If they don't, *tap* **Live Laboratories**, then **▶ Start automaton animation** on A and on B. | "The chef-hats announce the new experiment. That motor is a separate, time-limited command. The language model never controls power. Setpoints are clamped by the API and again in the firmware." |
| **2:35–2:55** | Stay on the plan card. | *(Read the real-vs-simulated line below, word for word.)* |
| **2:55–3:00** | — | "Taste, type, and the lab runs the next experiment. Thank you." |

### The "what's real vs simulated" line (say it word for word)

> "To be precise about what's real: the chambers, their sensor readings, your feedback and the motor
> command are real, and so are the NVIDIA ALCHEMI energies, computed on a GPU. The fermentation curves
> and the printed crumb are model predictions, labelled simulated. Anything scripted says replay."

If the chambers are running with `--with-simulated-devices`, replace the first sentence with:
"To be precise about what's real: your feedback and the NVIDIA ALCHEMI energies are real. Today the
chamber readings come from a labelled simulated device, so they say simulated."

---

## Fallbacks: what to do and what to say

| What's missing | What to do | What to say |
|---|---|---|
| **Internet** (venue Wi-Fi is down, or Claude is unreachable) | Leave the mode on **Offline deterministic**. The backend also falls back by itself if a live call fails. | "We're running the offline policy. It's deterministic and labelled, so you get the same answer every time." |
| **GPU / Brev** | Do nothing. The demo never calls the GPU live, and results are cached on the Mac. If the ALCHEMI numbers aren't signed off yet, skip the bracketed sentence at 1:00. | "Those were computed earlier on the GPU and cached here." |
| **Chamber hardware** (ESP32 offline, Peltier fault) | Restart with `scripts/start_demo.sh --with-simulated-devices`. The cards show **SIMULATED**. | Use the simulated-device version of the real-vs-simulated line. |
| **Chef-hat motor doesn't move** | Go to **Live Laboratories**, then **▶ Start automaton animation**. Use **Cancel** if it sticks. A simulated device acknowledges the command in the log. | "The command was sent and acknowledged. The motor is just for show, so it never touches the heater." |
| **The judge won't type, or the time is nearly up** | In **Replay example**, pick a scripted example, then tap **Interpret & plan next experiment**. It is labelled **REPLAY**. | "Here's a recorded example, labelled replay." |
| **iPad can't reach the Mac** | Open `http://127.0.0.1:8000/` in the Mac browser and turn the screen toward the judges. | (Nothing. Keep going.) |
| **Write actions are greyed out** (the pill says **Read-only**) | Tap the token pill, then paste the `FERMENTAI_API_TOKEN` printed by `start_demo.sh`. | (Nothing. Do this before the judges arrive.) |

---

## Notes and limits (for the team, not for the judges)

- Do not claim continuous pH from both chambers. There is one experimental pH probe (see README).
- Don't say the printed crumb "is" the loaf's crumb. It's a parameterised hypothesis, and the real loaf
  photo is the check.
- The motor animation marks a *planned* experiment. The machine has not mixed or baked anything new.
- When this was written, the taste-panel JavaScript (`taste.js`) had not landed. The step names at 1:50
  follow PLAN §11. Rehearse once against the live build and update this table if the labels differ.
