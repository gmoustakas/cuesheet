"""Mode behavior tests."""
from __future__ import annotations

from cuesheet.modes import Mode


def test_default_mode() -> None:
    assert Mode("record_new") == Mode.RECORD_NEW


def test_mode_capabilities() -> None:
    assert Mode.RECORD_NEW.can_record()
    assert Mode.RECORD_NEW.can_replay()

    assert Mode.RECORD_ALWAYS.can_record()
    assert Mode.RECORD_ALWAYS.forces_network()

    assert Mode.REPLAY_ONLY.can_replay()
    assert not Mode.REPLAY_ONLY.can_record()
    assert Mode.REPLAY_ONLY.fails_on_missing()

    assert not Mode.BYPASS.can_replay()
    assert Mode.BYPASS.forces_network()
