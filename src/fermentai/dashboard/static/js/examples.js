// Scripted example inputs for the "Replay example" dropdown. They are clearly labelled REPLAY in
// the UI and recorded with judge_name "Replay: <title>"; they are not real tasting observations.
// The texts mirror the agents' replay files in data/demos/ (demo_0N_*.json). Those files are not
// exposed over the API yet, so the feedback strings are bundled here and re-run live through the
// decision cycle (the recorded demo contexts, e.g. demo 03's simulated over-proof run, are not replayed).
export const REPLAY_EXAMPLES = [
  {
    id: "demo_01_sour_but_airy",
    title: "More sour, keep it airy",
    text: "Loaf A was mild and fluffy. Loaf B was more sour but a little dense. I'd like a stronger sour flavor without losing the airy texture.",
  },
  {
    id: "demo_02_too_vinegary",
    title: "B too vinegary, A bland",
    text: "Loaf B was way too vinegary and it collapsed a bit. Loaf A was nicer but bland. Next time make it milder than B but with more flavor than A.",
  },
  {
    id: "demo_03_overproof_in_progress",
    title: "Both fluffy, not sour enough",
    text: "Both were fluffy but not sour enough.",
  },
];
