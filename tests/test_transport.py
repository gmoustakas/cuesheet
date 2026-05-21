"""End-to-end transport tests using httpx.MockTransport as the inner layer.

These tests exercise the full path: producer code → EncoreTransport →
inner mock → response captured → cassette written → on replay,
EncoreTransport synthesizes from cassette and inner is never touched.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

import encore
from encore.session import CassetteMissingMatch


def _mock_transport(handler) -> httpx.MockTransport:
    """Wrap a handler function as an httpx MockTransport."""
    return httpx.MockTransport(handler)


def _anthropic_handler(payload: dict[str, Any]):
    """Handler that returns a JSON body for any POST to api.anthropic.com."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


def test_record_then_replay(tmp_path: Path) -> None:
    """First run hits the inner (mock) transport; second run replays."""
    cassette_path = tmp_path / "rec.yaml"
    handler = _anthropic_handler(
        {"id": "msg_test", "content": [{"type": "text", "text": "from-network"}]}
    )

    # ── record ─────────────────────────────────────────────────────────
    with encore.cassette(cassette_path):
        client = httpx.Client(transport=_mock_transport(handler))
        r = client.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert r.json()["content"][0]["text"] == "from-network"

    assert cassette_path.exists()

    # ── replay (no inner handler reachable, but cassette satisfies) ───
    def blocked_handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("inner transport should not be hit in replay_only mode")
    with encore.cassette(cassette_path, mode="replay_only"):
        client = httpx.Client(transport=_mock_transport(blocked_handler))
        r = client.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert r.json()["content"][0]["text"] == "from-network"


def test_replay_only_fails_without_cassette(tmp_path: Path) -> None:
    cassette_path = tmp_path / "missing.yaml"
    handler = _anthropic_handler({"unused": True})
    with encore.cassette(cassette_path, mode="replay_only"):
        client = httpx.Client(transport=_mock_transport(handler))
        with pytest.raises(CassetteMissingMatch):
            client.post(
                "https://api.anthropic.com/v1/messages",
                json={"model": "claude-sonnet-4-5", "messages": []},
            )


def test_non_intercepted_host_pass_through(tmp_path: Path) -> None:
    """Hosts not in the intercept list call the inner transport directly."""

    seen_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_calls.append(str(request.url))
        return httpx.Response(204)

    with encore.cassette(tmp_path / "nothing.yaml", mode="replay_only"):
        client = httpx.Client(transport=_mock_transport(handler))
        r = client.get("https://example.com/x")
    assert r.status_code == 204
    assert seen_calls == ["https://example.com/x"]


def test_bypass_mode_never_writes_cassette(tmp_path: Path) -> None:
    cassette_path = tmp_path / "bypass.yaml"
    handler = _anthropic_handler({"v": 1})
    with encore.cassette(cassette_path, mode="bypass"):
        client = httpx.Client(transport=_mock_transport(handler))
        r = client.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": "claude-sonnet-4-5", "messages": []},
        )
        assert r.json()["v"] == 1
    assert not cassette_path.exists()


def test_record_always_overwrites(tmp_path: Path) -> None:
    cassette_path = tmp_path / "always.yaml"
    payload = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "x"}]}

    # First record version 1
    with encore.cassette(cassette_path):
        client = httpx.Client(transport=_mock_transport(_anthropic_handler({"version": 1})))
        client.post("https://api.anthropic.com/v1/messages", json=payload)

    # Overwrite with version 2 via record_always
    with encore.cassette(cassette_path, mode="record_always"):
        client = httpx.Client(transport=_mock_transport(_anthropic_handler({"version": 2})))
        client.post("https://api.anthropic.com/v1/messages", json=payload)

    # Replay should get version 2
    def blocked(request: httpx.Request) -> httpx.Response:
        pytest.fail("should not call inner")
    with encore.cassette(cassette_path, mode="replay_only"):
        client = httpx.Client(transport=_mock_transport(blocked))
        r = client.post("https://api.anthropic.com/v1/messages", json=payload)
        assert r.json()["version"] == 2


def test_decorator_form(tmp_path: Path) -> None:
    cassette_path = tmp_path / "decorator.yaml"

    @encore.cassette(cassette_path)
    def call_api(handler) -> dict[str, Any]:
        client = httpx.Client(transport=_mock_transport(handler))
        return client.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": "claude-sonnet-4-5", "messages": []},
        ).json()

    first = call_api(_anthropic_handler({"v": "first"}))
    assert first["v"] == "first"

    def blocked(request: httpx.Request) -> httpx.Response:
        pytest.fail("should not call inner")
    second = call_api(blocked)
    assert second["v"] == "first"  # came from the cassette


async def test_async_client(tmp_path: Path) -> None:
    cassette_path = tmp_path / "async.yaml"

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"v": "async-recorded"})

    with encore.cassette(cassette_path):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                json={"model": "claude-sonnet-4-5", "messages": []},
            )
            assert r.json()["v"] == "async-recorded"

    async def blocked(request: httpx.Request) -> httpx.Response:
        pytest.fail("should not call inner on replay")

    with encore.cassette(cassette_path, mode="replay_only"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                json={"model": "claude-sonnet-4-5", "messages": []},
            )
            assert r.json()["v"] == "async-recorded"


def test_default_mode_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENCORE_DEFAULT_MODE", "replay_only")
    handler = _anthropic_handler({"unused": True})
    with encore.cassette(tmp_path / "from_env.yaml"):
        client = httpx.Client(transport=_mock_transport(handler))
        with pytest.raises(CassetteMissingMatch):
            client.post(
                "https://api.anthropic.com/v1/messages",
                json={"model": "claude-sonnet-4-5", "messages": []},
            )


def test_disable_lets_inner_run_even_inside_cassette(tmp_path: Path) -> None:
    """encore.disable() routes around the active session for one block."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"hit": "inner"})

    with encore.cassette(tmp_path / "x.yaml", mode="replay_only"), encore.disable():
        client = httpx.Client(transport=_mock_transport(handler))
        r = client.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": "claude-sonnet-4-5", "messages": []},
        )
        assert r.json()["hit"] == "inner"
    assert len(seen) == 1
