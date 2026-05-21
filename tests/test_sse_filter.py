"""SSE-frame parser used by the web UI's stream-chunks pretty view."""
from __future__ import annotations

from cuesheet.web.app import _filter_sse_parse


def test_sse_parses_event_and_json_data() -> None:
    chunk = (
        'event: content_block_delta\n'
        'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
    )
    out = _filter_sse_parse(chunk)
    assert out["event"] == "content_block_delta"
    assert out["data"] is not None
    assert '"text": "hi"' in out["data_pretty"]


def test_sse_parses_event_with_non_json_data() -> None:
    out = _filter_sse_parse("event: ping\ndata: hello world\n\n")
    assert out["event"] == "ping"
    assert out["data"] == "hello world"
    assert out["data_pretty"] is None


def test_sse_parses_data_only_frame() -> None:
    out = _filter_sse_parse('data: {"x": 1}\n\n')
    assert out["event"] is None
    assert out["data"] is not None
    assert '"x": 1' in out["data_pretty"]


def test_sse_handles_multi_line_data() -> None:
    chunk = "event: x\ndata: {\"a\":1,\ndata: \"b\":2}\n\n"
    out = _filter_sse_parse(chunk)
    assert out["event"] == "x"
    # Multi-line data is concatenated per SSE spec
    assert '"a"' in out["data"] and '"b"' in out["data"]


def test_sse_handles_empty_input() -> None:
    out = _filter_sse_parse("")
    assert out == {"event": None, "data": None, "data_pretty": None}


def test_sse_handles_non_string_input() -> None:
    # The filter is hardened against accidental non-string body chunks
    out = _filter_sse_parse(None)  # type: ignore[arg-type]
    assert out["event"] is None
    assert out["data"] is None
