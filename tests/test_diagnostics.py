"""Replay-miss diagnostics: closest-match scoring, structured exception, diff."""
from __future__ import annotations

from pathlib import Path

from cuesheet.cassette import (
    CassetteFile,
    Interaction,
    RecordedRequest,
    RecordedResponse,
    save_cassette,
)
from cuesheet.matchers import find_closest_miss
from cuesheet.modes import Mode
from cuesheet.session import CassetteMissingMatch, Session


def _req(model: str, messages: list, **extras) -> RecordedRequest:
    return RecordedRequest(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        body={"model": model, "messages": messages, **extras},
    )


def _interaction(req: RecordedRequest, text: str = "ok") -> Interaction:
    return Interaction(
        id="t",
        request=req,
        response=RecordedResponse(
            status_code=200,
            body={"content": [{"type": "text", "text": text}]},
        ),
    )


def test_find_closest_miss_returns_highest_scoring_candidate() -> None:
    recorded = [
        _interaction(_req("claude-sonnet-4-5", [{"role": "user", "content": "hi"}])),
        _interaction(_req("claude-haiku-4-5", [{"role": "user", "content": "hello"}])),
    ]
    # live request matches model but not messages of the first cassette entry
    live = _req("claude-sonnet-4-5", [{"role": "user", "content": "different"}])
    report = find_closest_miss(recorded, live)
    assert report is not None
    # first cassette entry scores higher (model matches)
    assert report.candidate is recorded[0]
    assert "model" in report.matched
    assert "messages" in report.failed


def test_find_closest_miss_returns_none_for_empty_cassette() -> None:
    assert find_closest_miss([], _req("x", [])) is None


def test_session_decide_fail_includes_closest(tmp_path: Path) -> None:
    cassette_path = tmp_path / "test_thing.yaml"
    save_cassette(
        cassette_path,
        CassetteFile(interactions=[
            _interaction(_req("claude-sonnet-4-5", [{"role": "user", "content": "original"}])),
        ]),
        scrub=False,
    )
    session = Session(path=cassette_path, mode=Mode.REPLAY_ONLY)
    live = _req("claude-sonnet-4-5", [{"role": "user", "content": "changed"}])
    decision = session.decide(live)
    assert decision.action == "fail"
    assert decision.closest is not None
    assert "model" in decision.closest.matched
    assert "messages" in decision.closest.failed


def test_exception_diagnostic_renders_unified_diff(tmp_path: Path) -> None:
    cassette_path = tmp_path / "test_x.yaml"
    candidate = _interaction(_req(
        "claude-sonnet-4-5",
        [{"role": "user", "content": "Summarize: TCP slow-start grows cwnd exponentially."}],
    ))
    from cuesheet.matchers import find_closest_miss
    live = _req(
        "claude-sonnet-4-5",
        [{"role": "user", "content": "Summarize: TCP slow-start is great."}],
    )
    report = find_closest_miss([candidate], live)
    exc = CassetteMissingMatch(
        "no matching interaction",
        cassette_path=cassette_path,
        request=live,
        closest=report,
    )
    diag = exc.diagnostic()
    assert "closest match" in diag
    assert "model" in diag           # matched primitive listed
    assert "messages" in diag        # failed primitive listed
    assert "body diff:" in diag
    # diff should mention both sides
    assert "-" in diag and "+" in diag


def test_exception_diagnostic_with_empty_cassette() -> None:
    """No candidates is a valid state; the diagnostic should still render."""
    live = _req("any-model", [{"role": "user", "content": "x"}])
    exc = CassetteMissingMatch(
        "no matching interaction",
        cassette_path=Path("/tmp/x.yaml"),
        request=live,
        closest=None,
    )
    diag = exc.diagnostic()
    assert "cassette has no interactions" in diag
