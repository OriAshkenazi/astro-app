import json

import pytest

from astro_app.cli import main


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ASTRO_HOME", str(tmp_path / "home"))


def test_accounts_empty(capsys):
    assert main(["accounts"]) == 0
    assert "astro-app login" in capsys.readouterr().out


def test_ping_requires_sign_in(capsys):
    assert main(["ping"]) == 1
    assert "Sign in with ChatGPT" in capsys.readouterr().out


def test_chart_ambiguous_time_then_fold(tmp_path, capsys):
    args = ["chart", "--date", "2021-11-07", "--time", "01:30", "--lat", "40.71", "--lon", "-74.0",
            "--tz", "America/New_York", "--place", "New York", "--out", str(tmp_path / "out")]
    assert main(args) == 2 and "--fold 1" in capsys.readouterr().out
    assert main(args + ["--fold", "1"]) == 0
    chart = json.loads((tmp_path / "out" / "chart.json").read_text())
    assert chart["time"]["utc_datetime"] == "2021-11-07T06:30:00Z"
    assert (tmp_path / "out" / "wheel.svg").read_text().startswith("<svg")
