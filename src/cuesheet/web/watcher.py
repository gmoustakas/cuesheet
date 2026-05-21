"""Filesystem watcher for cassette files. Pushes change events to SSE subscribers.

Why watchfiles: it uses inotify (Linux) / kqueue (macOS) / ReadDirectoryChangesW
(Windows). Efficient, no polling, scales to thousands of files.

Behaviour:
  - Watch the configured root recursively for *.yaml changes
  - Filter out changes that don't look like cassettes
  - Debounce bursts (multiple writes in the same 200ms window collapse into one)
  - Broadcast {type, kind, path, timestamp} to every connected SSE client
  - Slow subscribers drop events rather than block the watcher
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from watchfiles import Change, awatch

logger = logging.getLogger("cuesheet.web.watcher")


def _is_cassette_path(path: str) -> bool:
    """Match anything that looks like a cassette: lives in a `cassette(s)/`
    directory, or its filename begins with `test_` (matching the convention
    cuesheet's pytest plugin uses for auto-discovered cassettes).

    We inspect the parent directory names and the basename - never the full
    path string - so a pytest tmp dir like `/tmp/pytest-of-x/test_foo0/` does
    not produce false positives just because `test_` appears somewhere upstream.
    """
    p = Path(path)
    if p.suffix.lower() != ".yaml":
        return False
    # Walk up: the parent chain must contain a "cassette(s)" directory, OR
    # the file's own stem starts with "test_".
    for parent in p.parents:
        name = parent.name.lower()
        if name in ("cassette", "cassettes"):
            return True
    return p.stem.startswith("test_")


_CHANGE_KIND = {
    Change.added: "added",
    Change.modified: "modified",
    Change.deleted: "deleted",
}


class LiveWatcher:
    """Single watcher per cuesheet.web app. Owns one asyncio task and a fan-out
    of subscriber queues."""

    def __init__(self, root: Path, *, debounce_ms: int = 200) -> None:
        self.root = root.resolve()
        self.debounce_ms = debounce_ms
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._events_broadcast = 0

    # ── lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="cuesheet-watcher")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            self._task.cancel()
        self._task = None

    @property
    def events_broadcast(self) -> int:
        return self._events_broadcast

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ── fan-out ───────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        self._events_broadcast += 1
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop for this subscriber rather than block the others
                logger.debug("subscriber queue full; dropping event")

    # ── main loop ─────────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            async for changes in awatch(
                self.root,
                stop_event=self._stop_event,
                debounce=self.debounce_ms,
                recursive=True,
            ):
                relevant: list[dict[str, Any]] = []
                for change, raw_path in changes:
                    if not _is_cassette_path(raw_path):
                        continue
                    try:
                        rel = str(Path(raw_path).relative_to(self.root))
                    except ValueError:
                        rel = raw_path
                    relevant.append({
                        "kind": _CHANGE_KIND.get(change, "modified"),
                        "path": rel,
                    })
                if not relevant:
                    continue
                await self._broadcast({
                    "type": "cassette_changed",
                    "changes": relevant,
                    "timestamp": time.time(),
                })
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("watcher loop crashed")
