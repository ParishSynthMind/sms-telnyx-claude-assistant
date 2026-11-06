import json

import pytest

from smsassist import cli
from tests.conftest import ROOT


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("TEMPLATES_PATH", f"{ROOT}/data/templates.json")
    monkeypatch.setenv("QUIET_HOURS", "")
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("TELNYX_FROM_NUMBER", "+15550100100")
    monkeypatch.delenv("TELNYX_API_KEY", raising=False)


def test_segments_command(capsys):
    assert cli.main(["segments", "hello 😀"]) == 0
    assert json.loads(capsys.readouterr().out) == {"encoding": "UCS-2", "length": 8, "segments": 1}


def test_send_template_dry_run_then_log(env, capsys):
    assert cli.main(["send", "--to", "+1 (555) 010-0199", "--template", "follow_up", "--var", "name=Sam"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"
    cli.main(["log"])
    assert "Brightside Dental: hi Sam" in capsys.readouterr().out


def test_opt_out_then_send_is_blocked(env, capsys):
    cli.main(["opt-out", "+15550100199"])
    assert cli.main(["send", "--to", "+15550100199", "--text", "hi"]) == 1
    assert '"reason": "opted_out"' in capsys.readouterr().out
    cli.main(["contacts"])
    assert "opted_out=True" in capsys.readouterr().out


def test_bad_number_and_missing_var_exit_2(env, capsys):
    assert cli.main(["send", "--to", "555", "--text", "hi"]) == 2
    assert cli.main(["send", "--to", "+15550100199", "--template", "appointment_reminder"]) == 2
    assert "needs" in capsys.readouterr().err


def test_templates_and_trail(env, capsys):
    cli.main(["templates"])
    assert "stop_confirmation:" in capsys.readouterr().out
    cli.main(["send", "--to", "+15550100199", "--text", "hi"])
    cli.main(["trail"])
    assert "send_dry_run" in capsys.readouterr().out
