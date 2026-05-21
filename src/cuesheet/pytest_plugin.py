"""pytest plugin: zero-config `cuesheet_cassette` fixture.

Loading: declared via `[project.entry-points."pytest11"]` in pyproject.toml -
pytest discovers it automatically when both pytest and cuesheet are installed.

Usage:

    def test_my_agent(cuesheet_cassette):
        # auto-uses tests/cassettes/<test_name>.yaml
        ...

Or per-test override:

    @pytest.mark.cuesheet(path="custom.yaml", mode="replay_only")
    def test_my_agent(cuesheet_cassette):
        ...
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cuesheet.api import cassette
from cuesheet.session import Session


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "cuesheet(path, mode, match_on): customize the cassette for this test",
    )


@pytest.fixture
def cuesheet_cassette(request) -> Session:
    """Open a cassette for the current test.

    Defaults:
      - path: tests/cassettes/<module>/<test_name>.yaml
      - mode: CUESHEET_DEFAULT_MODE env var, else record_new
    """
    marker = request.node.get_closest_marker("cuesheet")
    overrides = marker.kwargs if marker else {}

    default_path = _default_path_for(request)
    path = Path(overrides.get("path", default_path))
    mode = overrides.get("mode")
    match_on = overrides.get("match_on")

    ctx = cassette(path, mode=mode, match_on=match_on)
    session = ctx.__enter__()
    try:
        yield session
    finally:
        ctx.__exit__(None, None, None)


def _default_path_for(request) -> Path:
    """Resolve the conventional cassette path for a test."""
    base = Path(request.config.rootdir) / "tests" / "cassettes"
    module = Path(request.node.fspath).stem
    name = request.node.name.replace("[", "_").replace("]", "_").replace("/", "_")
    return base / module / f"{name}.yaml"
