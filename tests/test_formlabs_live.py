"""LIVE test against a real PreFormServer (opt-in: FERMENTAI_FORMLABS_LIVE=1).

Starts PreFormServer (or attaches to one on port 44388), prepares chamber B alone, and checks the
server wrote a real .form. Never prints. Skipped by default and when no server is installed.
"""

import os

import pytest

pytest.importorskip("formlabs_sdk")

from fermentai.formlabs import PROFILES, load_chamber_inputs, open_preform
from fermentai.formlabs.client import locate_preform_server, server_version
from fermentai.formlabs.prepare import prepare_build
from fermentai.formlabs.replay import MOCK_FORM_BYTES

pytestmark = pytest.mark.skipif(
    os.environ.get("FERMENTAI_FORMLABS_LIVE") != "1"
    or not (locate_preform_server() or server_version("http://localhost:44388")),
    reason="live PreForm test is opt-in (FERMENTAI_FORMLABS_LIVE=1) and needs PreFormServer")


def test_live_prepare_chamber_b(tmp_path):
    chambers = load_chamber_inputs(names=("B",))
    with open_preform("live", server_log=tmp_path / "server.log") as h:
        assert h.backend == "live"
        r = prepare_build(h.api, PROFILES["form4-grey-v5"], chambers, tmp_path, "live_B",
                          views=("iso",))
        transcript = h.transcript
    form = tmp_path / "live_B.form"
    assert form.is_file() and form.read_bytes() != MOCK_FORM_BYTES
    assert form.stat().st_size > 100_000
    assert r["estimates"]["print_time_s"] > 0
    assert r["form_roundtrip"]["ok"]
    assert r["job_submitted"] is False
    assert all(e["path"] != "/v1/scene/print/" for e in transcript)
