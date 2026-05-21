"""CLI tests."""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cuesheet.cassette import (
    CassetteFile,
    Interaction,
    RecordedRequest,
    RecordedResponse,
    save_cassette,
)
from cuesheet.cli import main


def _seed_cassette(tmp_path: Path, name: str = "test_x.yaml") -> Path:
    cassettes_dir = tmp_path / "tests" / "cassettes"
    cassettes_dir.mkdir(parents=True)
    path = cassettes_dir / name
    interaction = Interaction(
        id="abc",
        request=RecordedRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"content-type": "application/json"},
            body={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]},
        ),
        response=RecordedResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body={"id": "msg_1", "content": [{"type": "text", "text": "hello"}]},
        ),
    )
    save_cassette(path, CassetteFile(interactions=[interaction]), scrub=False)
    return path


def test_list_empty(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "No cassettes" in result.output


def test_list_finds_cassettes(tmp_path: Path) -> None:
    _seed_cassette(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["list", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "test_x.yaml" in result.output
    assert "anthropic" in result.output


def test_inspect(tmp_path: Path) -> None:
    path = _seed_cassette(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["inspect", str(path)])
    assert result.exit_code == 0
    assert "claude-sonnet-4-5" in result.output


def test_stats(tmp_path: Path) -> None:
    _seed_cassette(tmp_path)
    runner = CliRunner()
    result = runner.invoke(main, ["stats", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "Cassette files" in result.output


def test_scrub(tmp_path: Path) -> None:
    # Seed a cassette WITH a secret in the body, then scrub
    cassettes_dir = tmp_path / "tests" / "cassettes"
    cassettes_dir.mkdir(parents=True)
    path = cassettes_dir / "with_secret.yaml"
    interaction = Interaction(
        id="x",
        request=RecordedRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"authorization": "Bearer sk-ant-secretvalueabcdefghij"},
            body={"model": "claude-sonnet-4-5"},
        ),
        response=RecordedResponse(status_code=200, body={}),
    )
    save_cassette(path, CassetteFile(interactions=[interaction]), scrub=False)
    assert "sk-ant" in path.read_text()

    runner = CliRunner()
    result = runner.invoke(main, ["scrub", str(path)])
    assert result.exit_code == 0
    assert "sk-ant" not in path.read_text()
    assert "<REDACTED>" in path.read_text()
