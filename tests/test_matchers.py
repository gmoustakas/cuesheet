"""Matcher tests."""
from __future__ import annotations

from encore.cassette import Interaction, RecordedRequest, RecordedResponse
from encore.matchers import (
    default_matcher,
    find_match,
    match_messages,
    match_method,
    match_model,
)


def _req(**kwargs) -> RecordedRequest:
    body = kwargs.pop("body", None) or {"model": "claude-sonnet-4-5", "messages": []}
    return RecordedRequest(
        method=kwargs.pop("method", "POST"),
        url=kwargs.pop("url", "https://api.anthropic.com/v1/messages"),
        headers=kwargs.pop("headers", {}),
        body=body,
    )


def test_method_matcher() -> None:
    assert match_method(_req(method="POST"), _req(method="post"))
    assert not match_method(_req(method="POST"), _req(method="GET"))


def test_model_matcher() -> None:
    a = _req(body={"model": "claude-sonnet-4-5"})
    b = _req(body={"model": "claude-sonnet-4-5"})
    c = _req(body={"model": "gpt-4o"})
    assert match_model(a, b)
    assert not match_model(a, c)


def test_messages_matcher() -> None:
    a = _req(body={"messages": [{"role": "user", "content": "hi"}]})
    b = _req(body={"messages": [{"role": "user", "content": "hi  "}]})  # trailing ws
    c = _req(body={"messages": [{"role": "user", "content": "bye"}]})
    assert match_messages(a, b)
    assert not match_messages(a, c)


def test_default_matcher_combines() -> None:
    a = _req(body={"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    b = _req(body={"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    c = _req(body={"model": "y", "messages": [{"role": "user", "content": "hi"}]})
    m = default_matcher()
    assert m(a, b)
    assert not m(a, c)


def test_find_match() -> None:
    a = _req(body={"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    b = _req(body={"model": "x", "messages": [{"role": "user", "content": "bye"}]})
    interactions = [
        Interaction(
            id=str(i),
            request=req,
            response=RecordedResponse(status_code=200, body={"id": f"resp-{i}"}),
        )
        for i, req in enumerate([a, b])
    ]
    m = default_matcher()
    found = find_match(interactions, a, m)
    assert found is not None
    assert found.response.body["id"] == "resp-0"


def test_custom_match_on_subset() -> None:
    a = _req(body={"model": "x", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.1})
    b = _req(body={"model": "x", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.9})
    strict = default_matcher()
    loose = default_matcher("method", "url", "model", "messages")
    assert not strict(a, b)
    assert loose(a, b)
