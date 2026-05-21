"""Cassette: the file on disk + the in-memory entries.

YAML format chosen for git-friendliness (humans can read diffs). One file
per test (typically). Streamed responses store chunks inside `body_stream`
so replay can re-emit them at configurable cadence.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML

from encore.scrubbers import scrub_headers, scrub_obj, scrub_string

CASSETTE_VERSION = 1


# ──────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────────────────


class RecordedRequest(BaseModel):
    """A request as it left the producer. Bodies are JSON-decoded if possible."""

    model_config = ConfigDict(extra="allow")

    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None  # decoded JSON or raw string
    body_raw: str | None = None  # set when body wasn't JSON

    @property
    def provider(self) -> str:
        host = self.url.lower()
        if "anthropic" in host:
            return "anthropic"
        if "openai" in host:
            return "openai"
        if "googleapis" in host or "google.com" in host:
            return "google"
        if "mistral" in host:
            return "mistral"
        if "groq" in host:
            return "groq"
        return "unknown"


class RecordedResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    body_raw: str | None = None
    # Streamed chunks. Stored as raw strings so we can re-emit byte-for-byte.
    body_stream: list[str] | None = None
    is_streaming: bool = False


class Interaction(BaseModel):
    """One recorded request/response pair."""

    model_config = ConfigDict(extra="allow")

    id: str
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float | None = None
    request: RecordedRequest
    response: RecordedResponse


class CassetteFile(BaseModel):
    """The whole YAML file."""

    model_config = ConfigDict(extra="allow")

    version: int = CASSETTE_VERSION
    interactions: list[Interaction] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────
# IO
# ──────────────────────────────────────────────────────────────────────


_yaml = YAML(typ="safe")
_yaml.default_flow_style = False
_yaml.width = 100  # readable diffs


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = _yaml.load(f) or {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        _yaml.dump(data, f)


def load_cassette(path: Path) -> CassetteFile:
    """Read a YAML cassette from disk. Empty / missing returns an empty cassette."""
    if not path.exists():
        return CassetteFile()
    raw = _read_yaml(path)
    if not raw:
        return CassetteFile()
    return CassetteFile.model_validate(raw)


def save_cassette(path: Path, cassette: CassetteFile, *, scrub: bool = True) -> None:
    """Write a cassette to disk, scrubbing secrets if requested."""
    payload: dict[str, Any] = cassette.model_dump(mode="json")
    if scrub:
        payload = _scrub_payload(payload)
    _write_yaml(path, payload)


def _scrub_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for interaction in payload.get("interactions", []):
        req = interaction.get("request") or {}
        if "headers" in req and isinstance(req["headers"], dict):
            req["headers"] = scrub_headers(req["headers"])
        if "body" in req:
            req["body"] = scrub_obj(req["body"])
        if "body_raw" in req and isinstance(req["body_raw"], str):
            req["body_raw"] = scrub_string(req["body_raw"])
        if "url" in req and isinstance(req["url"], str):
            req["url"] = scrub_string(req["url"])

        res = interaction.get("response") or {}
        if "headers" in res and isinstance(res["headers"], dict):
            res["headers"] = scrub_headers(res["headers"])
        if "body" in res:
            res["body"] = scrub_obj(res["body"])
        if "body_raw" in res and isinstance(res["body_raw"], str):
            res["body_raw"] = scrub_string(res["body_raw"])
        if "body_stream" in res and isinstance(res["body_stream"], list):
            res["body_stream"] = [scrub_string(s) for s in res["body_stream"]]
    return payload


# ──────────────────────────────────────────────────────────────────────
# Body decoding
# ──────────────────────────────────────────────────────────────────────


def decode_body(raw_bytes: bytes | str) -> tuple[Any, str | None]:
    """Try JSON-decode. Return (decoded_or_None, raw_string).

    decoded is preferred (clean YAML, semantic matching). raw_string is the
    fallback (multipart, binary, malformed JSON).
    """
    if isinstance(raw_bytes, bytes):
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None, "<binary>"
    else:
        text = raw_bytes

    if not text.strip():
        return None, ""

    try:
        return json.loads(text), None
    except (json.JSONDecodeError, ValueError):
        return None, text


def encode_body(decoded: Any, fallback_raw: str | None) -> bytes:
    """Reverse of decode_body. Produce bytes ready for HTTP transport."""
    if decoded is not None:
        return json.dumps(decoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if fallback_raw is not None:
        return fallback_raw.encode("utf-8")
    return b""


# ──────────────────────────────────────────────────────────────────────
# Streamed body assembly
# ──────────────────────────────────────────────────────────────────────


def join_stream_chunks(chunks: list[str]) -> bytes:
    """Reconstruct full bytes from recorded stream chunks (preserves order
    + raw separators that SSE uses, e.g. '\\n\\n' between events)."""
    buf = io.BytesIO()
    for chunk in chunks:
        buf.write(chunk.encode("utf-8"))
    return buf.getvalue()
