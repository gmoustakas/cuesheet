"""Session: the in-memory state that ties one cassette file + matcher + mode
together. Lives for the duration of a `with cassette(...)` block or a
decorated function call.

Producers ask the session: "I'm about to send this request - what do?"
The session returns one of:
  - REPLAY(interaction): use this saved response
  - RECORD(): hit the network; come back with the response and I'll save it
  - BYPASS: just hit the network, don't save
  - FAIL(reason): raise an error (e.g. replay_only with no match)
"""
from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cuesheet.cassette import (
    CassetteFile,
    Interaction,
    RecordedRequest,
    RecordedResponse,
    load_cassette,
    save_cassette,
)
from cuesheet.matchers import Matcher, default_matcher, find_match
from cuesheet.modes import Mode


class CuesheetError(Exception):
    """Base class for cuesheet exceptions."""


class CassetteMissingMatch(CuesheetError):
    """replay_only and no matching interaction in the cassette."""


class CassetteWriteError(CuesheetError):
    pass


@dataclass
class Decision:
    """What the session tells the transport to do for one request."""

    action: str  # "replay" | "record" | "bypass" | "fail"
    interaction: Interaction | None = None
    reason: str | None = None


@dataclass
class Session:
    path: Path
    mode: Mode = Mode.RECORD_NEW
    matcher: Matcher = field(default_factory=lambda: default_matcher())
    cassette: CassetteFile = field(default_factory=CassetteFile)
    scrub: bool = True

    # Stats updated as the session runs
    replayed_count: int = 0
    recorded_count: int = 0

    # Protect cassette mutations across worker threads (e.g. async sub-tasks)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.cassette = load_cassette(self.path)

    # ── decision ──────────────────────────────────────────────────────

    def decide(self, request: RecordedRequest) -> Decision:
        """What should the transport do for this request?"""
        if self.mode == Mode.BYPASS:
            return Decision(action="bypass")

        if self.mode == Mode.RECORD_ALWAYS:
            return Decision(action="record")

        if self.mode.can_replay():
            match = find_match(self.cassette.interactions, request, self.matcher)
            if match is not None:
                self.replayed_count += 1
                return Decision(action="replay", interaction=match)

        if self.mode.fails_on_missing():
            return Decision(
                action="fail",
                reason=(
                    f"no matching interaction in {self.path} (mode=replay_only). "
                    f"Re-run with mode='record_new' to capture this request."
                ),
            )

        if self.mode.can_record():
            return Decision(action="record")

        return Decision(action="bypass")

    # ── save ──────────────────────────────────────────────────────────

    def add_interaction(self, request: RecordedRequest, response: RecordedResponse,
                        duration_ms: float | None = None) -> Interaction:
        with self._lock:
            interaction = Interaction(
                id=uuid.uuid4().hex,
                recorded_at=datetime.now(timezone.utc),
                duration_ms=duration_ms,
                request=request,
                response=response,
            )
            # record_always replaces any earlier match for this request
            if self.mode == Mode.RECORD_ALWAYS:
                self.cassette.interactions = [
                    i for i in self.cassette.interactions
                    if not self.matcher(i.request, request)
                ]
            self.cassette.interactions.append(interaction)
            self.recorded_count += 1
            return interaction

    def flush(self) -> None:
        """Persist the cassette to disk. Idempotent; called automatically on
        session exit but also exposed for explicit save points."""
        try:
            save_cassette(self.path, self.cassette, scrub=self.scrub)
        except OSError as exc:
            raise CassetteWriteError(f"could not save cassette {self.path}: {exc}") from exc


# ──────────────────────────────────────────────────────────────────────
# Per-task active session
# ──────────────────────────────────────────────────────────────────────


_current: ContextVar[Session | None] = ContextVar("cuesheet_session", default=None)


def current() -> Session | None:
    return _current.get()


@contextmanager
def activate(session: Session) -> Iterator[Session]:
    """Bind a session to the current task. Stacks: nested cassettes are allowed
    (innermost wins for new requests; outer sessions still see their existing
    matches)."""
    token = _current.set(session)
    try:
        yield session
    finally:
        if session.recorded_count > 0:
            session.flush()
        _current.reset(token)
