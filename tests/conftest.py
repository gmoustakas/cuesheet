"""Shared fixtures."""
from __future__ import annotations

import pytest

from encore.scrubbers import reset_scrubbers
from encore.transport import is_installed, uninstall


@pytest.fixture(autouse=True)
def isolate():
    """Make sure each test starts with a clean transport + scrubber state."""
    yield
    if is_installed():
        uninstall()
    reset_scrubbers()
