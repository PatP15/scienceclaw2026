"""Scripted replay demos in data/demos (fermentai.agents.demos)."""

from fermentai.agents import schedules as S
from fermentai.agents.demos import DEMOS, generate_demo, load_demos


def test_demo_files_exist_and_are_labeled_replay():
    demos = load_demos()
    assert len(demos) >= 2
    assert {d["id"] for d in demos} == {s["id"] for s in DEMOS}
    for d in demos:
        assert d["label"] == "Replay" and d["provenance"] == "replay" and "REPLAY" in d["disclaimer"]
        assert d["feedback_text"] and d["expected_interpretation"]["desired_summary"]
        assert all(f["evidence_source"] == "replay" for f in d["result"]["findings_logged"])
        for c in ("A", "B"):
            assert S.validate_schedule(d["resulting_experiment"]["schedules"][c]["points"]) == []


def test_demo_is_reproducible_from_spec():
    spec = DEMOS[0]
    a, b = generate_demo(spec), generate_demo(spec)
    assert a == b
