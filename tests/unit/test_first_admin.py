"""F-06: the first administrator on the launcher's first start. Answers that break the account rules are
explained and asked again, keeping what was accepted; the rules are the same as for every account; a failed
start names the data folder and log file actually in use (a trial folder, not the default)."""
import argparse
import sys

import pytest

from aquacrop import launcher
from aquacrop.cli import _cfg, _db_path
from aquacrop.domain.model import new_id
from aquacrop.historian.writer import Historian
from aquacrop.security.auth import Accounts, load_or_create_secret, validate_username


def _users(cfg, state):
    h = Historian(_db_path(cfg, state), cfg, new_id("t"))
    try:
        acc = Accounts(h, load_or_create_secret(state / "secret.key"), idle_minutes=30, absolute_hours=12)
        return [u["username"] for u in acc.list_users()]
    finally:
        h.close()


def _answers(seq):
    it = iter(seq)

    def ask(prompt=""):
        print(prompt, end="")
        return next(it)
    return ask


def test_an_accented_name_and_rejected_passwords_are_explained_and_asked_again(tmp_path, capsys):
    cfg, state = _cfg("installation-80"), tmp_path / "trial"
    state.mkdir()
    names = _answers(["José", ""])                       # Enter takes the suggestion
    secrets = _answers(["short", "jose-river-lamp-42",   # too short; contains the login name
                        "river lamp orange 42", "river lamp orange 43",   # typed differently twice
                        "river lamp orange 42", "river lamp orange 42"])
    assert launcher.first_admin(cfg, state, username="admin", password_stdin=False, ask=names, ask_secret=secrets)
    out = capsys.readouterr().out
    assert "No spaces or accents. Example: jose" in out and "at least 12 characters" in out
    assert "must not contain the login name" in out and "Nothing appears on the screen" in out
    assert '"José" cannot be used: it contains a space, an accent' in out and 'Try "jose"' in out
    assert "password must be at least 12 characters" in out and "password must not contain the username" in out
    assert "The two passwords were different" in out
    assert "Administrator 'jose' created" in out
    assert _users(cfg, state) == ["jose"]


def test_the_rules_are_unchanged_and_a_cancel_creates_nothing(tmp_path, capsys):
    cfg, state = _cfg("installation-80"), tmp_path / "s"
    state.mkdir()

    def cancel(prompt=""):
        raise EOFError
    assert not launcher.first_admin(cfg, state, username="admin", password_stdin=False, ask=cancel, ask_secret=cancel)
    assert "no account was created" in capsys.readouterr().err
    assert _users(cfg, state) == []
    for typed, suggested in (("a", ""), ("jo sé", "jose"), ("x" * 33, "x" * 32), ("ana!", "ana"),
                             ("José María", "josemaria")):
        assert launcher.suggest_username(typed) == suggested
        if suggested:
            validate_username(suggested)                 # a suggestion always passes the unchanged rule
        with pytest.raises(ValueError):
            validate_username(typed)


@pytest.mark.parametrize("name,pw,why", [("José", "a long enough password", "username: 2-32 chars"),
                                         ("owner", "owner-has-a-long-password", "must not contain the username")])
def test_password_stdin_refuses_with_the_reason(tmp_path, monkeypatch, capsys, name, pw, why):
    cfg, state = _cfg("installation-80"), tmp_path / "s"
    state.mkdir()
    monkeypatch.setattr(sys, "stdin", type("S", (), {"readline": staticmethod(lambda: pw + "\n")})())
    assert not launcher.first_admin(cfg, state, username=name, password_stdin=True)
    assert why in capsys.readouterr().err
    assert _users(cfg, state) == []


def _args(state, **kw):
    return argparse.Namespace(config="installation-80", state_dir=str(state), port=kw.get("port", 0) or 8765,
                              username=kw.get("username", "admin"), password_stdin=True, no_browser=True,
                              check_only=False)


def test_a_failed_start_names_the_trial_folder_and_its_log(tmp_path, monkeypatch, capsys):
    trial = tmp_path / "demo-trial"
    monkeypatch.setattr(sys, "stdin", type("S", (), {"readline": staticmethod(lambda: "short\n")})())
    monkeypatch.setattr(launcher, "port_free", lambda port: True)
    assert launcher.cmd_launch(_args(trial)) == 3
    err = capsys.readouterr().err
    assert f"Data folder: {trial.resolve()}" in err
    assert f"Log file:    {trial.resolve() / 'logs' / 'aquacrop-launch.log'}" in err
    assert "first administrator not created" in (trial / "logs" / "aquacrop-launch.log").read_text(encoding="utf-8")


def test_failed_prerequisites_say_no_log_was_written(tmp_path, monkeypatch, capsys):
    trial = tmp_path / "other-trial"
    monkeypatch.setattr(launcher, "port_free", lambda port: False)
    assert launcher.cmd_launch(_args(trial)) == 2
    err = capsys.readouterr().err
    assert f"Data folder: {trial.resolve()}" in err and "none written" in err
