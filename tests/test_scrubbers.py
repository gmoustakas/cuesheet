"""Scrubber tests."""
from __future__ import annotations

from cuesheet.scrubbers import (
    add_scrubber,
    reset_scrubbers,
    scrub_headers,
    scrub_obj,
    scrub_string,
)


def test_redacts_anthropic_key() -> None:
    text = "my key is sk-ant-secretwithanotherbunchofchars"
    assert "sk-ant-secret" not in scrub_string(text)


def test_redacts_openai_key() -> None:
    text = "key=sk-abcdefghijklmnopqrstuvwxyzabcdef0123456"
    assert "sk-abc" not in scrub_string(text)


def test_redacts_jwt() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.abc123signature"
    assert "<REDACTED>" in scrub_string(jwt)


def test_redacts_email() -> None:
    assert "<REDACTED>" in scrub_string("contact me@georgemou.gr please")


def test_custom_scrubber_runs() -> None:
    reset_scrubbers()
    add_scrubber(r"INTERNAL-[A-Z]{4}")
    assert "<REDACTED>" in scrub_string("flag=INTERNAL-DEMO over there")


def test_scrub_headers_drops_auth() -> None:
    headers = {"Authorization": "Bearer foo123", "Content-Type": "application/json"}
    out = scrub_headers(headers)
    assert out["Authorization"] == "<REDACTED>"
    assert out["Content-Type"] == "application/json"


def test_scrub_obj_walks_dict() -> None:
    obj = {"user": "alice@example.com", "items": [{"token": "sk-ant-secretbunchofchars1234567890"}]}
    out = scrub_obj(obj)
    assert "alice" not in str(out)
    assert "sk-ant" not in str(out)
