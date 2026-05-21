"""Tests for the new CLI commands: diff, init, and the extended stats."""
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


def _build_cassette(path: Path, interactions: list[Interaction]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    save_cassette(path, CassetteFile(interactions=interactions), scrub=False)
    return path


def _make(model: str, prompt: str, response: str = "ok", usage: dict | None = None) -> Interaction:
    body: dict = {"content": [{"type": "text", "text": response}]}
    if usage is not None:
        body["usage"] = usage
    return Interaction(
        id="x",
        request=RecordedRequest(
            method="POST", url="https://api.anthropic.com/v1/messages",
            body={"model": model, "messages": [{"role": "user", "content": prompt}]},
        ),
        response=RecordedResponse(status_code=200, body=body),
    )


# ── stats with tokens & cost ──────────────────────────────────────────


def test_stats_reports_tokens_and_cost(tmp_path: Path) -> None:
    _build_cassette(
        tmp_path / "tests" / "cassettes" / "test_a.yaml",
        [
            _make("claude-sonnet-4-5", "hi", "hello",
                  usage={"input_tokens": 1000, "output_tokens": 500}),
        ],
    )
    runner = CliRunner()
    result = runner.invoke(main, ["stats", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Input tokens" in out
    assert "1,000" in out
    assert "Output tokens" in out
    assert "Estimated cost" in out
    assert "By model:" in out
    assert "claude-sonnet-4-5" in out


def test_stats_flags_unpriced_models(tmp_path: Path) -> None:
    _build_cassette(
        tmp_path / "tests" / "cassettes" / "test_unpriced.yaml",
        [
            _make("totally-custom-model", "p", "r",
                  usage={"input_tokens": 10, "output_tokens": 5}),
        ],
    )
    runner = CliRunner()
    result = runner.invoke(main, ["stats", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "no built-in price" in result.output
    assert "totally-custom-model" in result.output


# ── diff ──────────────────────────────────────────────────────────────


def test_diff_detects_response_change(tmp_path: Path) -> None:
    a = _build_cassette(
        tmp_path / "tests" / "cassettes" / "a.yaml",
        [_make("claude-sonnet-4-5", "hi", "answer A")],
    )
    b = _build_cassette(
        tmp_path / "tests" / "cassettes" / "b.yaml",
        [_make("claude-sonnet-4-5", "hi", "answer B")],
    )
    runner = CliRunner()
    result = runner.invoke(main, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    out = result.output
    assert "Matched pairs" in out
    # one matched pair (same request)
    assert "1" in out
    # response body diverges, so a unified diff should appear with both sides
    assert "answer A" in out
    assert "answer B" in out


def test_diff_detects_added_and_removed(tmp_path: Path) -> None:
    a = _build_cassette(
        tmp_path / "tests" / "cassettes" / "a.yaml",
        [
            _make("claude-sonnet-4-5", "common", "r"),
            _make("claude-sonnet-4-5", "only-in-a", "r"),
        ],
    )
    b = _build_cassette(
        tmp_path / "tests" / "cassettes" / "b.yaml",
        [
            _make("claude-sonnet-4-5", "common", "r"),
            _make("claude-sonnet-4-5", "only-in-b", "r"),
        ],
    )
    runner = CliRunner()
    result = runner.invoke(main, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "removed" in result.output
    assert "added" in result.output


def test_diff_identical_cassettes(tmp_path: Path) -> None:
    a = _build_cassette(
        tmp_path / "a.yaml",
        [_make("claude-sonnet-4-5", "x", "y")],
    )
    b = _build_cassette(
        tmp_path / "b.yaml",
        [_make("claude-sonnet-4-5", "x", "y")],
    )
    runner = CliRunner()
    result = runner.invoke(main, ["diff", str(a), str(b)])
    assert result.exit_code == 0
    assert "No differences" in result.output


# ── init ──────────────────────────────────────────────────────────────


def test_init_creates_scaffold(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "tests" / "cassettes" / ".gitkeep").exists()
    assert (tmp_path / "tests" / "conftest.py").exists()
    assert (tmp_path / "tests" / "test_cuesheet_example.py").exists()
    # Example file imports cuesheet
    example = (tmp_path / "tests" / "test_cuesheet_example.py").read_text()
    assert "import cuesheet" in example
    assert "@cuesheet.cassette(" in example


def test_init_idempotent(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["init", "--target", str(tmp_path)])
    # Mutate the conftest, then re-run init without --force; should NOT overwrite.
    custom = (tmp_path / "tests" / "conftest.py")
    custom.write_text("# user-edited\n")
    result = runner.invoke(main, ["init", "--target", str(tmp_path)])
    assert result.exit_code == 0
    assert "skipped" in result.output
    assert custom.read_text() == "# user-edited\n"


def test_init_force_overwrites(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(main, ["init", "--target", str(tmp_path)])
    custom = (tmp_path / "tests" / "conftest.py")
    custom.write_text("# user-edited\n")
    result = runner.invoke(main, ["init", "--target", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert custom.read_text() != "# user-edited\n"
