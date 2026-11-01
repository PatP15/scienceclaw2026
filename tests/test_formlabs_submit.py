"""The human print gate and the CLI surface. No test here can reach a printer: every path that
would open a PreForm connection is given a stub that records calls."""

import contextlib

import pytest

from fermentai.formlabs.cli import main
from fermentai.formlabs.prepare import (
    CONFIRM_ENV,
    CONFIRM_VALUE,
    print_confirmation_status,
    submit_print,
)


def _must_not_connect(*_a, **_k):
    raise AssertionError("submit_print connected to PreForm without both confirmations")


@pytest.fixture
def form(tmp_path):
    f = tmp_path / "x.form"
    f.write_bytes(b"x")
    return f


@pytest.mark.parametrize("flag,env", [(False, {}), (True, {}),
                                      (False, {CONFIRM_ENV: CONFIRM_VALUE}),
                                      (True, {CONFIRM_ENV: "1"}),
                                      (True, {CONFIRM_ENV: "true"})])
def test_submit_refuses_without_both_confirmations(form, flag, env, capsys):
    rc = submit_print(form, "Form4-Test", i_confirm_print=flag, env=env,
                      open_preform_fn=_must_not_connect)
    assert rc == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "No job was submitted" in out
    assert "not for food contact" in out


def test_confirmation_status_lists_whats_missing():
    s = print_confirmation_status(False, {})
    assert s["confirmed"] is False and len(s["missing"]) == 2
    assert print_confirmation_status(True, {CONFIRM_ENV: CONFIRM_VALUE})["confirmed"] is True


def test_submit_with_both_but_no_printer_still_refuses(form):
    rc = submit_print(form, None, i_confirm_print=True, env={CONFIRM_ENV: CONFIRM_VALUE},
                      open_preform_fn=_must_not_connect)
    assert rc == 2


def test_submit_with_both_only_queues_never_print_now(form):
    calls = []

    class StubApi:
        def open_form_file(self, file):
            calls.append(("open", file))
            return {"project_name": "p", "scenes": [{"scene_id": "s1", "new_model_ids": []}]}

        def print_scene(self, **kw):
            calls.append(("print", kw))
            return {"job_id": "stub"}

    @contextlib.contextmanager
    def stub_open(backend, allow_print=False):
        assert backend == "live" and allow_print is True
        yield type("H", (), {"api": StubApi()})()

    rc = submit_print(form, "Form4-Test", i_confirm_print=True,
                      env={CONFIRM_ENV: CONFIRM_VALUE}, open_preform_fn=stub_open)
    assert rc == 0
    kind, kw = calls[-1]
    assert kind == "print" and kw["print_now"] is False and kw["printer"] == "Form4-Test"


def test_cli_submit_refuses(form, monkeypatch, capsys):
    monkeypatch.delenv(CONFIRM_ENV, raising=False)
    assert main(["submit", "--form", str(form), "--printer", "P"]) == 2
    assert main(["submit", "--form", str(form), "--printer", "P", "--i-confirm-print"]) == 2
    assert "REFUSED" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--help"], ["prepare", "--help"], ["submit", "--help"],
                                  ["status", "--help"]])
def test_cli_help(argv, capsys):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    if argv[0] == "submit":
        assert "--i-confirm-print" in out and CONFIRM_ENV in out
    if argv[0] == "prepare":
        assert "--backend" in out and "open_face_up" in out


def test_cli_no_args_prints_help(capsys):
    assert main([]) == 0
    assert "prepare" in capsys.readouterr().out
