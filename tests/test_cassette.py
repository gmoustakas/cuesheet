"""Cassette IO + round-trip tests."""
from __future__ import annotations

from pathlib import Path

from encore.cassette import (
    CassetteFile,
    Interaction,
    RecordedRequest,
    RecordedResponse,
    decode_body,
    encode_body,
    join_stream_chunks,
    load_cassette,
    save_cassette,
)


def _sample_interaction() -> Interaction:
    return Interaction(
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


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    cas = CassetteFile(interactions=[_sample_interaction()])
    save_cassette(path, cas, scrub=False)

    loaded = load_cassette(path)
    assert len(loaded.interactions) == 1
    assert loaded.interactions[0].request.body["model"] == "claude-sonnet-4-5"


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    cas = load_cassette(tmp_path / "nope.yaml")
    assert cas.interactions == []


def test_provider_detection() -> None:
    r = RecordedRequest(method="POST", url="https://api.anthropic.com/v1/messages")
    assert r.provider == "anthropic"
    r2 = RecordedRequest(method="POST", url="https://api.openai.com/v1/chat/completions")
    assert r2.provider == "openai"
    r3 = RecordedRequest(method="POST", url="https://api.somewhere-else.com/x")
    assert r3.provider == "unknown"


def test_decode_encode_round_trip() -> None:
    raw = b'{"a": 1, "b": "two"}'
    decoded, fallback = decode_body(raw)
    assert decoded == {"a": 1, "b": "two"}
    assert fallback is None
    # Re-encoded JSON may have different whitespace; what matters is that the
    # decoded value round-trips through encode→decode.
    re_encoded = encode_body(decoded, fallback)
    re_decoded, _ = decode_body(re_encoded)
    assert re_decoded == decoded


def test_decode_handles_non_json() -> None:
    decoded, fallback = decode_body(b"not json at all")
    assert decoded is None
    assert fallback == "not json at all"


def test_scrub_redacts_api_keys(tmp_path: Path) -> None:
    path = tmp_path / "secrets.yaml"
    interaction = Interaction(
        id="x",
        request=RecordedRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"authorization": "Bearer sk-ant-secret-abcdefghijklmnop"},
            body={"model": "claude-sonnet-4-5"},
        ),
        response=RecordedResponse(status_code=200, headers={}, body={}),
    )
    save_cassette(path, CassetteFile(interactions=[interaction]), scrub=True)
    content = path.read_text()
    assert "sk-ant-secret" not in content
    assert "<REDACTED>" in content


def test_split_sse_chunks() -> None:
    """Stream chunks should preserve SSE separators."""
    chunks = ["event: message_start\ndata: {}\n\n", "event: content_block_start\n\n", "event: done\n\n"]
    bytes_out = join_stream_chunks(chunks)
    assert b"event: message_start" in bytes_out
    assert b"event: done" in bytes_out
