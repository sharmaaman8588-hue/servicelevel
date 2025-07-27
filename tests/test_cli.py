from pathlib import Path

import pytest
from typer.testing import CliRunner

from servicelevel import mart
from servicelevel.cli import app

runner = CliRunner()


@pytest.fixture(scope="module")
def data(tmp_path_factory, con):
    out = tmp_path_factory.mktemp("cli") / "mart"
    mart.export_mart(con, out)
    return out


def test_erlang_command_prints_the_textbook_answer():
    result = runner.invoke(
        app,
        [
            "erlang",
            "--calls",
            "100",
            "--aht",
            "180",
            "--interval",
            "1800",
            "--target",
            "0.8",
            "--threshold",
            "20",
        ],
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "10.00" in out and "0.8884" in out and "0.7956" in out and "7.8" in out
    assert any(line.split()[-1] == "14" for line in out.splitlines() if "Required agents" in line)


def test_erlang_command_applies_shrinkage_and_rejects_bad_input():
    ok = runner.invoke(app, ["erlang", "--calls", "100", "--aht", "180", "--shrinkage", "0.3"])
    assert ok.exit_code == 0 and ok.output.split()[-1] == "20"
    bad = runner.invoke(app, ["erlang", "--calls", "100", "--aht", "180", "--target", "1.5"])
    assert bad.exit_code == 2


def test_sl_command_prints_all_four_definitions(data):
    result = runner.invoke(
        app, ["sl", "--queue", "billing", "--week", "2025-W10", "--data", str(data)]
    )
    assert result.exit_code == 0, result.output
    for header in ("SL1", "SL2", "SL3", "SL4", "SL1 interval mean", "Tue 2025-03-04"):
        assert header in result.output


def test_sl_command_rejects_unknown_queue_or_week(data):
    for args in (
        ["--queue", "nope", "--week", "2025-W10"],
        ["--queue", "billing", "--week", "2024-W01"],
    ):
        result = runner.invoke(app, ["sl", *args, "--data", str(data)])
        assert result.exit_code == 1


def test_staffing_command_lists_a_day_and_the_largest_gaps(data):
    day = runner.invoke(
        app, ["staffing", "--queue", "billing", "--date", "2025-03-04", "--data", str(data)]
    )
    assert day.exit_code == 0, day.output
    assert "11:30" in day.output and day.output.count(":30") >= 20
    top = runner.invoke(app, ["staffing", "--queue", "billing", "--top", "5", "--data", str(data)])
    assert top.exit_code == 0
    assert "largest staffing gap" in top.output and "+" in top.output


def test_validate_pbip_command_passes_and_fails(data, tmp_path):
    pbip_file = Path(__file__).resolve().parents[1] / "powerbi" / "ServiceLevel.pbip"
    ok = runner.invoke(app, ["validate-pbip", "--pbip", str(pbip_file), "--data", str(data)])
    assert ok.exit_code == 0 and "OK" in ok.output
    broken = tmp_path / "broken"
    broken.mkdir()
    for name in mart.MART_FILES:
        text = (data / name).read_text()
        (broken / name).write_text(
            text.replace("handle_seconds", "handle_secs", 1) if name == "Interval.csv" else text
        )
    bad = runner.invoke(app, ["validate-pbip", "--pbip", str(pbip_file), "--data", str(broken)])
    assert bad.exit_code == 1 and "handle_seconds" in bad.output


def test_generate_and_build_end_to_end(tmp_path):
    raw = tmp_path / "raw" / "interval.csv"
    out = tmp_path / "mart"
    pbip_dir = tmp_path / "pbi"
    assert runner.invoke(app, ["generate", "--out", str(raw)]).exit_code == 0
    built = runner.invoke(
        app, ["build", "--raw", str(raw), "--out", str(out), "--pbip-dir", str(pbip_dir)]
    )
    assert built.exit_code == 0, built.output
    assert (pbip_dir / "ServiceLevel.pbip").is_file()
    valid = runner.invoke(
        app, ["validate-pbip", "--pbip", str(pbip_dir / "ServiceLevel.pbip"), "--data", str(out)]
    )
    assert valid.exit_code == 0, valid.output


def test_commands_explain_a_missing_mart(tmp_path):
    result = runner.invoke(
        app, ["sl", "--queue", "billing", "--week", "2025-W10", "--data", str(tmp_path)]
    )
    assert result.exit_code == 1
    missing = runner.invoke(app, ["build", "--raw", str(tmp_path / "none.csv")])
    assert missing.exit_code == 1


def test_sl_command_without_a_week_reports_every_queue_over_the_period(data):
    result = runner.invoke(app, ["sl", "--data", str(data)])
    assert result.exit_code == 0, result.output
    for queue in ("billing", "technical", "sales"):
        assert queue in result.output
    assert runner.invoke(app, ["sl", "--week", "2025-W10", "--data", str(data)]).exit_code == 2
