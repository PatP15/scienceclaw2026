# FermentAI: three-minute live demo script

This demo follows the four-slide investor pitch (`docs/pitch/slides.md`). It has to prove three
things to investors and engineers:

- the taste → next-experiment loop works end to end, offline;
- NVIDIA ALCHEMI and Formlabs PreForm each have a concrete job in it;
- we are exact about what is real, simulated or pending.

Two roles: the **Presenter** talks, and the **Driver** holds the iPad. One person can do both. Lines in
quotes are spoken. Lines starting with *Tap* are actions on the iPad, which is in landscape on the
dashboard. The UI names used here are:

- the tabs **Live Laboratories**, **Digital Twin**, **NVIDIA Molecular Explorer** and
  **Taste → Next Experiment**;
- the top-bar mode indicator **LIVE / REPLAY / SIMULATION**;
- the reasoning label **Online Agent** / **Offline Policy**;
- **Replay example**, **Interpret & plan next experiment** and **▶ Start automaton animation**.

Spoken words come to about 275 (1 min 58 s at 140 wpm). The other ~60 s is for tasting, typing and
waiting for the agents.

---

## Before the judges arrive (T−10 min, not timed)

1. **Start the backend.** On the Mac, run `scripts/start_demo.sh`. It prints the iPad URL with
   `?token=…`.
2. **Start the chambers.**
   - **With the ESP32 chambers:** power them on and check that both cards in Live Laboratories update.
     The ESP32 firmware is *compiled but unverified on hardware*, so do a full dry run before the
     judges arrive.
   - **Without them**, or if either chamber fails the dry run: in a second terminal, run
     `uv run python -m simulated_devices --chambers A B`. The mode indicator then reads
     **SIMULATION**, and the chamber cards and camera frames are labelled simulated.
3. **Open the dashboard.** Open the printed URL on the iPad. The token pill should change from
   **Read-only** to write access, and the connection pill should go green.
4. **Choose how the agents reason.** Go to **Taste → Next Experiment**, then the mode switch:
   - **Offline deterministic** is the default. Results are labelled **Offline Policy**.
   - Use **Live LLM** only if internet and Claude are confirmed working right now. Results are then
     labelled **Online Agent**.
5. **Run the disconnected-mode test** in `docs/network_setup.md` §6 once, so that a motor command
   reaches `completed`.
6. **Set out the loaves.** Slice Loaf A (blue card) and Loaf B (orange card) on two plates. Put the
   printed chamber specimens, `crumb_chamber_A` and `crumb_chamber_B`, next to them.
7. **Prepare the scripted input.** In the **Replay example** dropdown, find
   *"PLAN_MASTER §14 judge script ('more sour, but still fluffy')"* (`demo_04`) and leave it
   unselected. It is the fallback input. It reads: *"Loaf A tastes mild and has nice texture. Loaf B
   is more sour, which I prefer, but the crumb is too dense. I'd like a more sour loaf that's still
   fluffy."*

---

## The run (0:00–3:00)

| Time | Screen / action | Say |
|---|---|---|
| **0:00–0:20** | Hand each judge a slice of A and B. *Tap* **Live Laboratories**. | "Two loaves, same dough, two temperatures. These are the two chambers that fermented them. Each one has its own AI scientist agent." |
| **0:20–0:40** | Point at the A and B cards and at the mode indicator. | "A is blue and holds 22 degrees. B is orange, at 27. The indicator up here says whether you're seeing live hardware, a replay or a simulation, and every number carries its own badge." |
| **0:40–0:58** | *Tap* **Digital Twin**. Point at the pH and rise curves, then at the crumb specimens on the table if they're printed. | "This is our fermentation model, and it's a simulation. It predicts B acidifies faster and overproofs early. Each forecast becomes a crumb specimen, prepared for Formlabs printing." |
| **0:58–1:15** | *Tap* **NVIDIA Molecular Explorer**. | "This part is real: NVIDIA ALCHEMI on a Brev GPU. A thermodynamic cycle puts lactic acid about four pKa units stronger than acetic. Experiment says one, so the sign is right. That bounds the model's acid terms. It doesn't drive the bread." |
| **1:15–1:42** | *Tap* **Taste → Next Experiment**. Hand the iPad to a judge, or type their words for them into the box under **What did the bread taste like?** | "Now you're the scientist. What did the bread taste like? Plain words are fine." |
| **1:42–1:47** | *Tap* **Interpret & plan next experiment**. | "It's turning that into sensory preferences." |
| **1:47–2:20** | Scroll the steps as they fill: interpretation, comparison, Agent A hypothesis, Agent B critique, recommended experiment. Point at the **Offline Policy** or **Online Agent** label, then at **Updated experiment plan**. | "Here are your words as structured preferences. Agent A proposes a new temperature schedule. Agent B checks it against its own model and pushes back. They agree, and the plan updates. This label says the deterministic offline policy made the decision, not a live model." *(If it says Online Agent, say "Claude made this decision live".)* |
| **2:20–2:35** | Look at the chambers. The chef-hats wiggle for up to 5 s. If they don't, *tap* **Live Laboratories**, then **▶ Start automaton animation** on A and on B. | "The chef-hats announce the new experiment. The motor is a separate, time-limited command. The language model never controls power. Setpoints are clamped by the API and again in the firmware." |
| **2:35–2:55** | Stay on the plan card. | *(Read the real-vs-simulated line below, word for word.)* |
| **2:55–3:00** | — | "Taste, type, and the lab plans the next experiment. Thank you." |

**What to expect from the scripted input.** With `demo_04` in Offline Policy mode, the plan should be
**Trial 002**:

- Chamber A: *Hot 32 °C 5 h → cool 18 °C*.
- Chamber B: *Steady 24 °C, 12 h*.

Those come from `data/demos/demo_04_master_plan_judge_script.json`. A judge's own words will
usually give a different plan, and that's fine.

### The "what's real vs simulated" line (say it word for word)

With the chamber hardware connected:

> "To be precise: your feedback, the chamber readings and the motor command are real, and so are the
> NVIDIA ALCHEMI energies, computed on a GPU. The fermentation curves and the printed crumb are model
> predictions, labelled simulated. Anything scripted says replay."

With virtual devices (the mode indicator reads **SIMULATION**):

> "To be precise: your feedback and the NVIDIA ALCHEMI energies are real. Today's chamber readings
> come from labelled virtual devices, and the curves and printed crumb are model predictions.
> Anything scripted says replay."

---

## Fallbacks: what to do and what to say

| What's missing | What to do | What to say |
|---|---|---|
| **Internet** (venue Wi-Fi is down, or Claude is unreachable) | Leave the mode on **Offline deterministic**. The backend also falls back by itself if a live call fails, and the result says **Offline Policy**. | "We're running the offline policy. It's deterministic and labelled, so you get the same answer every time." |
| **GPU / Brev** | Do nothing. The demo never calls the GPU live, and the Brev instance has already been deleted. All results are cached on the Mac. | "Those were computed earlier on the GPU and cached here." |
| **Chamber hardware** (ESP32 offline, Peltier fault, firmware misbehaving) | Run `uv run python -m simulated_devices --chambers A B`. The indicator reads **SIMULATION**. | Use the virtual-device version of the real-vs-simulated line. |
| **Chef-hat motor doesn't move** | Go to **Live Laboratories**, then **▶ Start automaton animation**. Use **Cancel** if it sticks. Virtual devices acknowledge the command in their log. | "The command was sent and acknowledged. The motor is just for show, so it never touches the heater." |
| **The judge won't type** | In **Replay example**, pick the `demo_04` "PLAN_MASTER §14 judge script" entry, then tap **Interpret & plan next experiment**. It shows a **REPLAY** banner, and the decision cycle and motors run live on the scripted text. | "Here's a scripted example, labelled replay. The decision you see is computed live from it." |
| **The time is nearly up**, or the backend is struggling | Pick the `demo_04` entry, then tap **▶ Show recorded replay**. This shows only the stored output, and nothing is sent to the devices. | "This is the recorded result for that input, labelled replay." |
| **iPad can't reach the Mac** | Open `http://127.0.0.1:8000/` in the Mac browser and turn the screen toward the judges. | (Nothing. Keep going.) |
| **Write actions are greyed out** (the pill says **Read-only**) | Tap the token pill, then paste the `FERMENTAI_API_TOKEN` printed by `start_demo.sh`. | (Nothing. Do this before the judges arrive.) |

---

## Notes and limits (for the team, not for the judges)

- **ESP32 firmware status: compiled, not yet verified on hardware.** Don't claim the firmware has
  run on the chambers until someone has watched it do so.
- Do not claim continuous pH from both chambers. There is one experimental pH probe (see README).
- **The printed specimens are simulation-derived hypotheses.** Kinetics do not uniquely determine
  crumb. The real loaf photo is the check. The mild/sour presets are only a baseline.
- **The relative pKa is an overestimate.** The cluster thermodynamic cycle gives −3.9 ± 1.7 against
  −0.9 from experiment. The sign is right and it converges with cluster size, but the size is not.
  If asked, say "in water clusters", not "in dough".
- **The benchmark is in-silico.** If asked, say that talking changes choices and removes duplicates,
  but the final recommendation is the same.
- The motor animation marks a *planned* experiment. The machine has not mixed or baked anything new.
- **Rehearse once against the build you will demo.** When this was written, the dashboard changes
  this script relies on were in the working tree but not yet committed:
  - the top-bar `#data-mode` LIVE/REPLAY/SIMULATION indicator, which is derived from the telemetry
    source actually arriving;
  - the Online Agent / Offline Policy pill;
  - replay entries loaded from `GET /api/v1/demos`, so `demo_04` comes from `data/demos/`;
  - **▶ Show recorded replay**.

  If a label differs on the day, follow the screen. If the replay list is empty, type the `demo_04`
  sentence by hand. It is printed above.
