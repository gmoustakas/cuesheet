"""LiveWatcher tests.

The watcher is async + filesystem-driven, so tests:
  - start the watcher
  - subscribe a queue
  - write a real cassette file in the watched dir
  - assert an event lands within a generous timeout
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("watchfiles")

from encore.cassette import (
    CassetteFile,
    Interaction,
    RecordedRequest,
    RecordedResponse,
    save_cassette,
)
from encore.web.watcher import LiveWatcher


def _write_cassette(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    save_cassette(
        path,
        CassetteFile(
            interactions=[
                Interaction(
                    id="x",
                    request=RecordedRequest(
                        method="POST",
                        url="https://api.anthropic.com/v1/messages",
                        body={"model": "claude-sonnet-4-5", "messages": []},
                    ),
                    response=RecordedResponse(status_code=200, body={}),
                )
            ]
        ),
        scrub=False,
    )


async def test_watcher_broadcasts_on_new_cassette(tmp_path: Path) -> None:
    cassettes = tmp_path / "tests" / "cassettes"
    cassettes.mkdir(parents=True)
    watcher = LiveWatcher(tmp_path, debounce_ms=50)
    await watcher.start()
    q = watcher.subscribe()
    try:
        # Small delay to make sure the watcher loop is running before we write
        await asyncio.sleep(0.05)
        _write_cassette(cassettes / "test_demo.yaml")

        event = await asyncio.wait_for(q.get(), timeout=4.0)
        assert event["type"] == "cassette_changed"
        assert any(c["path"].endswith("test_demo.yaml") for c in event["changes"])
    finally:
        watcher.unsubscribe(q)
        await watcher.stop()


async def test_watcher_ignores_non_cassette_yaml(tmp_path: Path) -> None:
    watcher = LiveWatcher(tmp_path, debounce_ms=50)
    await watcher.start()
    q = watcher.subscribe()
    try:
        await asyncio.sleep(0.05)
        # Not under cassette(s)/ and not named test_*
        unrelated = tmp_path / "random.yaml"
        unrelated.write_text("foo: bar\n")
        # Give the watcher a chance to fire (it shouldn't). On Python 3.10
        # asyncio.wait_for raises asyncio.TimeoutError, which is distinct from
        # the builtin TimeoutError; on 3.11+ they're the same class. Match
        # asyncio.TimeoutError to cover both.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q.get(), timeout=1.0)
    finally:
        watcher.unsubscribe(q)
        await watcher.stop()


async def test_watcher_subscribe_unsubscribe(tmp_path: Path) -> None:
    watcher = LiveWatcher(tmp_path, debounce_ms=50)
    await watcher.start()
    try:
        q1 = watcher.subscribe()
        q2 = watcher.subscribe()
        assert watcher.subscriber_count == 2
        watcher.unsubscribe(q1)
        assert watcher.subscriber_count == 1
        watcher.unsubscribe(q2)
        assert watcher.subscriber_count == 0
    finally:
        await watcher.stop()
