# FermentAI: three-minute live demo script

This demo follows the four-slide investor pitch (`docs/pitch/slides.md`). It has to prove three
things to investors and engineers:

- the taste → next-experiment loop works end to end, offline;
- NVIDIA ALCHEMI and Formlabs PreForm each have a concrete job in it;
- we are exact about what is **measured** (physical world: sensors, photos, tasting), **computed** (a
  calculation that actually ran, with logs) or **modeled** (our own process model), and what is pending.

Two roles: the **Presenter** talks, and the **Driver** sits at the Mac. One person can do both.
There is no iPad: the dashboard runs full-screen in a browser on the Mac, turned toward the judges,
and the judges type or talk at the laptop. Lines in quotes are spoken. Lines starting with *Click*
are actions in the dashboard. The dashboard's own provenance badges read real / simulated / replay;
its "real" means sensor data, which is what the pitch calls **measured**. The UI names used here are:

- the tabs **Live Laboratories**, **Digital Twin**, **NVIDIA Molecular Explorer** and
  **Taste → Next Experiment**;
- the top-bar mode indicator **LIVE / REPLAY / SIMULATION**;
- the reasoning label **Online Agent** / **Offline Policy**;
- the **Talk to Sous Chef A** chat panel on the Taste tab (with a Sous Chef A / B switch, **Send** and
  **New tasting**). Its stages are listening → confirming → handing off to Head Chef → plan called;
- the fallbacks: **What did the bread taste like?** form, **Replay example**, **Interpret & plan next
  experiment** and **▶ Start automaton animation**.

Spoken words come to about 299 (2 min 08 s at 140 wpm). The other ~50 s is for tasting, the chat and
waiting for the agents.

---

## Before the judges arrive (T−10 min, not timed)

1. **Start the backend.** On the Mac, run `scripts/start_demo.sh`. It prints the local URL with
   `?token=…`.
2. **Start the chambers.**
   - **With the ESP32 chambers:** power them on and check that both cards in Live Laboratories update.
     The ESP32 firmware is *compiled but unverified on hardware*, so do a full dry run before the
     judges arrive.
   - **Without them**, or if either chamber fails the dry run: in a second terminal, run
     `uv run python -m simulated_devices --chambers A B`. The mode indicator then reads
     **SIMULATION**, and the chamber cards and camera frames are labelled simulated.
3. **Open the dashboard on the Mac.** Open `http://127.0.0.1:8000/?token=…`, using the token that
   `start_demo.sh` printed, in a full-screen browser window. Turn the screen and keyboard toward the
   judges. The token pill should change from **Read-only** to write access, and the connection pill
   should go green.
4. **Choose how the agents reason.** Go to **Taste → Next Experiment**, then the mode switch:
   - **Offline deterministic** is the default. Results are labelled **Offline Policy**.
   - Use **Live LLM** only if internet and Claude are confirmed working right now. Results are then
     labelled **Online Agent**.
5. **Run the disconnected-mode test** in `docs/network_setup.md` §6 once, so that a motor command
   reaches `completed`.
6. **Check the chat.** On **Taste → Next Experiment**, confirm the **Talk to Sous Chef A** panel shows
   the Sous Chef's greeting. If it says the chat service is not running, use the form fallback below.
   Click **New tasting** so the judges start from a clean session.
7. **Set out the loaves.** Slice Loaf A (blue card) and Loaf B (orange card) on two plates. Put the
   printed chamber specimens, `crumb_chamber_A` and `crumb_chamber_B`, next to them.
8. **Prepare the scripted input.** In the **Replay example** dropdown, find
   *"PLAN_MASTER §14 judge script ('more sour, but still fluffy')"* (`demo_04`) and leave it
   unselected. It is the fallback input. It reads: *"Loaf A tastes mild and has nice texture. Loaf B
   is more sour, which I prefer, but the crumb is too dense. I'd like a more sour loaf that's still
   fluffy."*

---

## The run (0:00–3:00)

| Time | Screen / action | Say |
|---|---|---|
| **0:00–0:20** | Hand each judge a slice of A and B. *Click* **Live Laboratories**. | "Two loaves, same dough, two temperatures. These are the two stations that fermented them. Each one has its own sous chef, an AI scientist agent." |
| **0:20–0:40** | Point at the A and B cards and at the mode indicator. | "A is blue and holds 22 degrees. B is orange, at 27. The indicator up here says whether you're seeing live hardware, a replay or a simulation, and every number carries its own badge." |
| **0:40–0:58** | *Click* **Digital Twin**. Point at the pH and rise curves, then at the crumb specimens on the table if they're printed. | "This is our fermentation model, so this is modeled, not measured. It predicts B acidifies faster and overproofs early. Each forecast becomes a crumb specimen, prepared for Formlabs printing." |
| **0:58–1:15** | *Click* **NVIDIA Molecular Explorer**. | "This part is computed: NVIDIA ALCHEMI on a Brev GPU, checked against published experiments. A thermodynamic cycle puts lactic acid about four pKa units stronger than acetic. Experiment says one, so the sign is right. That bounds the model's acid terms. It doesn't drive the bread." |
| **1:15–1:30** | *Click* **Taste → Next Experiment**. Seat a judge at the laptop, in front of **Talk to Sous Chef A**. | "Now you're the guest. Tell the Sous Chef how the bread tasted, in your own words." |
| **1:30–1:50** | Short scripted chat (the judge types; the Driver types for them if they'd rather talk). (1) The judge answers the greeting, e.g. "A was mild and fluffy, B more sour but dense." Click **Send**. (2) The Sous Chef asks one or two questions, for example what to change. The judge answers, e.g. "more sour, but keep it airy", and clicks **Send**. (3) The Sous Chef reads back what it understood (chips) and asks to confirm. The judge says "yes". | "It asks a couple of questions, like a waiter would, then checks it understood you." |
| **1:50–2:20** | The **Head Chef** bubble appears in the chat ("handing off to Head Chef"). Then the steps fill in: goal, Sous Chef A's proposal, Sous Chef B's critique, agreed experiment. (On screen, Agent/Scientist A and B are the sous chefs from slide 2.) Point at the **Offline Policy** or **Online Agent** label, then at **Updated experiment plan**. | "The Head Chef turned that into the goal. Sous Chef A proposes a new temperature schedule. Sous Chef B checks it against its own simulations and pushes back. They agree, and the plan updates. This label says the deterministic offline policy decided, not a live model." *(If it says Online Agent, say "Claude made this decision live".)* |
| **2:20–2:35** | Look at the chambers. The chef-hats wiggle for up to 5 s. If they don't, *click* **Live Laboratories**, then **▶ Start automaton animation** on A and on B. | "The chef-hats announce the new experiment. The motor is a separate, time-limited command. The language model never controls power. Setpoints are clamped by the API and again in the firmware." |
| **2:35–2:55** | Stay on the plan card. | *(Read the measured / computed / modeled line below, word for word.)* |
| **2:55–3:00** | — | "Taste, tell the Sous Chef, and the lab plans the next experiment. Thank you." |

**What to expect from the scripted input.** With `demo_04` in Offline Policy mode, the plan should be
**Trial 002**:

- Chamber A: *Hot 32 °C 5 h → cool 18 °C*.
- Chamber B: *Steady 24 °C, 12 h*.

Those come from `data/demos/demo_04_master_plan_judge_script.json`. A judge's own words will
usually give a different plan, and that's fine.

### The "measured / computed / modeled" line (say it word for word)

With the chamber hardware connected:

> "To be precise: your feedback and the chamber readings are measured. The NVIDIA ALCHEMI energies and
> the PreForm print prep are computed. The fermentation curves and the printed crumb are modeled, not
> yet validated against a bake. Anything scripted says replay."

With virtual devices (the mode indicator reads **SIMULATION**):

> "To be precise: the only thing measured today is your feedback. The chamber readings come from
> labelled virtual devices. The NVIDIA ALCHEMI energies and PreForm print prep are computed, and the
> curves and printed crumb are modeled. Anything scripted says replay."

---

## Fallbacks: what to do and what to say

| What's missing | What to do | What to say |
|---|---|---|
| **Internet** (venue Wi-Fi is down, or Claude is unreachable) | Leave the mode on **Offline deterministic**. The backend also falls back by itself if a live call fails, and the result says **Offline Policy**. | "We're running the offline policy. It's deterministic and labelled, so you get the same answer every time." |
| **GPU / Brev** | Do nothing. The demo never calls the GPU live, and the Brev instance has already been deleted. All results are cached on the Mac. | "Those were computed earlier on the GPU and cached here." |
| **Chamber hardware** (ESP32 offline, Peltier fault, firmware misbehaving) | Run `uv run python -m simulated_devices --chambers A B`. The indicator reads **SIMULATION**. | Use the virtual-device version of the measured / computed / modeled line. |
| **Chef-hat motor doesn't move** | Click **Live Laboratories**, then **▶ Start automaton animation**. Use **Cancel** if it sticks. Virtual devices acknowledge the command in their log. | "The command was sent and acknowledged. The motor is just for show, so it never touches the heater." |
| **Chat panel says the chat service is not running**, or a chat stalls | Click **New tasting** once. If it still fails, type the judge's words into the **What did the bread taste like?** form below the chat and click **Interpret & plan next experiment**. It runs the same Head Chef → line cooks → Sous Chefs cycle. | "Same kitchen, just the order form instead of the waiter." |
| **The judge won't type or talk** | In **Replay example**, pick the `demo_04` "PLAN_MASTER §14 judge script" entry, then click **Interpret & plan next experiment**. It shows a **REPLAY** banner, and the decision cycle and motors run live on the scripted text. | "Here's a scripted example, labelled replay. The decision you see is computed live from it." |
| **The time is nearly up**, or the backend is struggling | Pick the `demo_04` entry, then click **▶ Show recorded replay**. This shows only the stored output, and nothing is sent to the devices. | "This is the recorded result for that input, labelled replay." |
| **Browser shows nothing** | Check that `start_demo.sh` is still running, then reload `http://127.0.0.1:8000/?token=…`. | (Nothing. Keep going.) |
| **Write actions are greyed out** (the pill says **Read-only**) | Click the token pill, then paste the `FERMENTAI_API_TOKEN` printed by `start_demo.sh`. | (Nothing. Do this before the judges arrive.) |

---

## Notes and limits (for the team, not for the judges)

- **ESP32 firmware status: compiled, not yet verified on hardware.** Don't claim the firmware has
  run on the chambers until someone has watched it do so.
- Do not claim continuous pH from both chambers. There is one experimental pH probe (see README).
- **The printed specimens are modeled hypotheses.** Kinetics do not uniquely determine crumb. The
  photo of the actual loaf, once measured, is the check. The mild/sour presets are only a baseline.
- **The relative pKa is an overestimate.** The cluster thermodynamic cycle gives −3.9 ± 1.7 against
  −0.9 from experiment. The sign is right and it converges with cluster size, but the size is not.
  If asked, say "in water clusters", not "in dough".
- **The benchmark is modeled, in-silico.** If asked, say that talking changes choices and removes duplicates,
  but the final recommendation is the same.
- The motor animation marks a *planned* experiment. The machine has not mixed or baked anything new.
- **Rehearse once against the build you will demo.** When this was written, the dashboard changes
  this script relies on were in the working tree but not yet committed:
  - the top-bar `#data-mode` LIVE/REPLAY/SIMULATION indicator, which is derived from the telemetry
    source actually arriving;
  - the Online Agent / Offline Policy pill;
  - replay entries loaded from `GET /api/v1/demos`, so `demo_04` comes from `data/demos/`;
  - **▶ Show recorded replay**;
  - the **Talk to Sous Chef A/B** chat panel (`src/fermentai/dashboard/static/js/chat.js`), which needs the
    `/api/v1/chat` service. Without it the panel says so, and the form fallback is used.

  If a label differs on the day, follow the screen. If the replay list is empty, type the `demo_04`
  sentence by hand. It is printed above.
