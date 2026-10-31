"""Molecular Explorer answer cards: values come from the synced files at runtime, never hard-coded."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "fermentai" / "dashboard" / "static"
JS = (STATIC / "js" / "molecular.js").read_text()


def test_molecular_cards_present():
    for q in ("Which acid is stronger?", "Is the GPU model trustworthy?", "How do the acids hold water?",
              "Does it touch gluten?", "What does it change in the dough model?", "See the molecules"):
        assert q in JS, q


def test_result_numbers_are_not_hard_coded():
    # Experimental pKa, GPU results and run counts must be read from c2_summary.json / CSVs / reports.
    for lit in ("3.86", "4.76", "1066", "1,066", "15.26", "15.3", "19.07", "15.76", "0.127", "0.013", "3.98", "2.33"):
        assert not re.search(rf"(?<![\d.]){re.escape(lit)}(?![\d])", JS), f"hard-coded result value {lit} in molecular.js"


def test_3d_viewer_is_lazy():
    # The several-MB explorer page is only loaded after a click (no iframe in the initial render).
    assert "Open 3D viewer" in JS
    idx = JS.index("explorer-frame")
    assert "const open = " in JS[:idx] and JS.index("const open = ") < idx


def test_molecular_stylesheet_linked():
    html = (STATIC / "index.html").read_text()
    assert 'href="css/molecular.css"' in html and (STATIC / "css" / "molecular.css").is_file()
