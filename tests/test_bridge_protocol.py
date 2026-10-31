"""serial_v1 framing / parsing units + the contract document's example lines."""

import json
import re
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from fermentai.api.schemas import Command, ControlAckIn, TelemetryIn
from fermentai.bridge import protocol as P

DOC = Path(__file__).resolve().parents[1] / "contracts" / "serial_v1.md"


def test_framer_partial_crlf_empty_and_overlong():
    f = P.LineFramer(max_bytes=64)
    assert f.feed(b'{"type":"log"') == []                       # partial line buffered
    assert f.feed(b',"msg":"a"}\r\n\n\nplain text\n') == [b'{"type":"log","msg":"a"}', b"plain text"]
    assert f.feed(b"x" * 100) == [] and f.overlong == 1          # runaway line dropped, buffer bounded
    assert len(f.buf) == 0
    assert f.feed(b"yyy\nok\n") == [b"ok"]                     # tail of overlong line discarded
    assert f.feed(b"z" * 70 + b"\nfine\n") == [b"fine"] and f.overlong == 2


@pytest.mark.parametrize("raw, kind", [
    (b"ESP-ROM:esp32s3-20210327", "log"),
    (b"rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)", "log"),
    (b'{"type":"telemetry","v":"1.0","payload":{', "log"),     # truncated JSON -> log text
    (b"[1, 2]", "log"),
    (b'{"no_type":1}', "invalid"),
    (b'{"type":"launch_missiles","v":"1.0"}', "invalid"),
    (b'{"type":"hello","v":"2.0"}', "invalid"),
    (b"\xff\xfe garbage \x00", "log"),
    (b'{"type":"hello","v":"1.0","chamber_id":"A"}', "msg"),
    (b'{"type":"hello","chamber_id":"A"}', "msg"),                # v defaults to 1.0
])
def test_parse_line_kinds(raw, kind):
    assert P.parse_line(raw).kind == kind


def test_parse_replaces_nan():
    p = P.parse_line(b'{"type":"telemetry","v":"1.0","payload":{"ph":NaN,"x":[Infinity,1]}}')
    assert p.kind == "msg" and p.nan_fixed
    assert p.msg["payload"] == {"ph": None, "x": [None, 1]}


def test_encode_envelope_and_limit():
    line = P.encode(P.time_msg("2026-10-31T15:00:00Z"))
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line) == {"type": "time", "v": "1.0", "server_time_utc": "2026-10-31T15:00:00Z"}
    with pytest.raises(ValueError):
        P.encode({"type": "log", "msg": "x" * 9000})
    with pytest.raises(ValueError):
        P.encode({"type": "log", "msg": float("nan")})


def _doc_lines():
    text = DOC.read_text()
    blocks = re.findall(r"```jsonl\n(.*?)```", text, re.DOTALL)
    return [json.loads(line) for b in blocks for line in b.strip().splitlines()]


def test_contract_doc_examples_are_valid():
    lines = _doc_lines()
    types = {m["type"] for m in lines}
    assert {"hello", "telemetry", "ack", "image", "log", "hello_ack", "time", "command", "ping"} <= types
    for m in lines:
        assert m["v"] == "1.0"
        assert P.encode(m)                                         # <= 8 KB, finite
        if m["type"] in P.DEVICE_TYPES:
            assert P.parse_line(json.dumps(m).encode()).kind == "msg"
        if m["type"] == "telemetry":
            TelemetryIn.model_validate(m["payload"])
        elif m["type"] == "ack":
            ControlAckIn.model_validate(m["payload"])
        elif m["type"] == "command":
            TypeAdapter(Command).validate_python(m["payload"])
        elif m["type"] == "image":
            import base64
            import hashlib
            data = base64.b64decode(m["data"])
            assert len(data) == m["bytes"] and hashlib.sha256(data).hexdigest() == m["sha256"]


def test_contract_readme_links_serial_doc():
    readme = (DOC.parent / "README.md").read_text()
    assert "serial_v1.md" in readme
