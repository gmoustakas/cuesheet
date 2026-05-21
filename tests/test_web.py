"""Web UI tests."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from encore.cassette import (
    CassetteFile,
    Interaction,
    RecordedRequest,
    RecordedResponse,
    save_cassette,
)


def _seed_cassette(tmp_path: Path, name: str = "test_x.yaml") -> Path:
    cassettes_dir = tmp_path / "tests" / "cassettes"
    cassettes_dir.mkdir(parents=True, exist_ok=True)
    path = cassettes_dir / name
    interaction = Interaction(
        id="abc",
        request=RecordedRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"content-type": "application/json"},
            body={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]},
        ),
        response=RecordedResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body={"id": "msg_1", "content": [{"type": "text", "text": "hello"}]},
        ),
    )
    save_cassette(path, CassetteFile(interactions=[interaction]), scrub=False)
    return path


def test_index_empty(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "encore" in r.text
    assert "No cassettes" in r.text


def test_index_lists_cassette(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    _seed_cassette(tmp_path)
    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "test_x.yaml" in r.text
    assert "anthropic" in r.text


def test_index_search(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    _seed_cassette(tmp_path, "test_foo.yaml")
    _seed_cassette(tmp_path, "test_bar.yaml")
    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/?search=foo")
    assert r.status_code == 200
    assert "test_foo.yaml" in r.text
    assert "test_bar.yaml" not in r.text


def test_cassette_detail(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    _seed_cassette(tmp_path)
    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/cassette", params={"path": "tests/cassettes/test_x.yaml"})
    assert r.status_code == 200
    assert "claude-sonnet-4-5" in r.text
    assert "anthropic" in r.text


def test_cassette_detail_404(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/cassette", params={"path": "tests/cassettes/nope.yaml"})
    assert r.status_code == 404


def test_path_traversal_blocked(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    app = build_app(root=tmp_path)
    client = TestClient(app)
    # Try to escape the root directory
    r = client.get("/cassette", params={"path": "../../etc/passwd"})
    assert r.status_code == 404
    r = client.get("/cassette", params={"path": "/etc/passwd"})
    assert r.status_code == 404


def test_api_list(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    _seed_cassette(tmp_path)
    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/api/cassettes")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["cassettes"][0]["interactions"] == 1


def test_api_stats(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    _seed_cassette(tmp_path, "test_a.yaml")
    _seed_cassette(tmp_path, "test_b.yaml")
    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/api/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["cassettes"] == 2
    assert data["interactions"] == 2
    assert data["by_provider"].get("anthropic") == 2


def test_healthz(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from encore.web.app import build_app

    app = build_app(root=tmp_path)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
