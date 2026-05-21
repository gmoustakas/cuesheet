"""Recording / replay modes.

Each mode answers two questions at request time:
  1. If a matching cassette entry exists - replay it?
  2. If none exists - hit the real network and record?
"""
from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    """Recording behaviour.

    record_new   - Replay if cassette has the request; record otherwise. *Default.*
    record_once  - Record only if the cassette file is empty/missing; never re-record.
    record_always- Always call the network and overwrite the cassette entry.
    replay_only  - Never call the network; raise if a matching entry is missing.
    bypass       - Ignore the cassette completely; always call the network.
    """

    RECORD_NEW = "record_new"
    RECORD_ONCE = "record_once"
    RECORD_ALWAYS = "record_always"
    REPLAY_ONLY = "replay_only"
    BYPASS = "bypass"

    def can_record(self) -> bool:
        return self in {Mode.RECORD_NEW, Mode.RECORD_ONCE, Mode.RECORD_ALWAYS}

    def can_replay(self) -> bool:
        return self in {Mode.RECORD_NEW, Mode.RECORD_ONCE, Mode.REPLAY_ONLY}

    def forces_network(self) -> bool:
        return self in {Mode.RECORD_ALWAYS, Mode.BYPASS}

    def fails_on_missing(self) -> bool:
        return self == Mode.REPLAY_ONLY
