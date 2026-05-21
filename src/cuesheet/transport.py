"""httpx transport interception.

Why httpx: every modern Python LLM SDK (anthropic, openai, mistralai,
google-generativeai, litellm, ...) is built on top of httpx. Hooking at
the transport layer makes cuesheet provider-agnostic.

Design choice: we WRAP whatever transport the user gives us rather than
subclassing httpx.HTTPTransport. This means:

  - Production: we wrap the default httpx.HTTPTransport
  - Tests: caller can pass an httpx.MockTransport for the inner layer,
    and our session logic still sits in front of it

So our transport's job is:
  1. For each request, ask the active Session what to do
  2. On REPLAY: synthesize an httpx.Response from the cassette entry
  3. On RECORD: delegate to the wrapped transport, capture response, save
  4. On BYPASS: just delegate

Streaming responses preserve their chunk boundaries for faithful replay.
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from cuesheet.cassette import (
    Interaction,
    RecordedRequest,
    RecordedResponse,
    decode_body,
    encode_body,
)
from cuesheet.session import (
    CassetteMissingMatch,
    current,
)

logger = logging.getLogger("cuesheet.transport")


# LLM hosts we intercept. Other traffic passes through untouched so this
# library doesn't break unrelated HTTP code in the same process.
_INTERCEPT_HOSTS = (
    "api.anthropic.com",
    "api.openai.com",
    "api.mistral.ai",
    "generativelanguage.googleapis.com",
    "api.groq.com",
    "api.cohere.ai",
    "api.deepseek.com",
    "api.together.xyz",
    # Local test servers, useful for tests that want cuesheet to engage:
    "kymo.test",
    "cuesheet.test",
)


def _should_intercept(url: httpx.URL) -> bool:
    host = (url.host or "").lower()
    if not host:
        return False
    return host in _INTERCEPT_HOSTS or host.endswith(".openai.azure.com")


# ──────────────────────────────────────────────────────────────────────
# Composed transport (sync + async)
# ──────────────────────────────────────────────────────────────────────


class CuesheetTransport(httpx.BaseTransport):
    """Wraps an inner transport. Routes through the active Session."""

    def __init__(self, inner: httpx.BaseTransport | None = None) -> None:
        self._inner = inner or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        session = current()
        if session is None or not _should_intercept(request.url):
            return self._inner.handle_request(request)

        recorded_req = _to_recorded_request(request)
        decision = session.decide(recorded_req)

        if decision.action == "fail":
            raise CassetteMissingMatch(
                decision.reason or "no matching interaction",
                cassette_path=session.path,
                request=recorded_req,
                closest=decision.closest,
            )
        if decision.action == "replay":
            assert decision.interaction is not None
            return _synthesize_response(decision.interaction)
        if decision.action == "bypass":
            return self._inner.handle_request(request)

        # record
        start = time.monotonic()
        real_response = self._inner.handle_request(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        recorded_resp = _capture_response_sync(real_response)
        session.add_interaction(recorded_req, recorded_resp, duration_ms=elapsed_ms)
        return _rehydrate_response(real_response, recorded_resp)

    def close(self) -> None:
        self._inner.close()


class CuesheetAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        session = current()
        if session is None or not _should_intercept(request.url):
            return await self._inner.handle_async_request(request)

        recorded_req = _to_recorded_request(request)
        decision = session.decide(recorded_req)

        if decision.action == "fail":
            raise CassetteMissingMatch(
                decision.reason or "no matching interaction",
                cassette_path=session.path,
                request=recorded_req,
                closest=decision.closest,
            )
        if decision.action == "replay":
            assert decision.interaction is not None
            return _synthesize_response(decision.interaction)
        if decision.action == "bypass":
            return await self._inner.handle_async_request(request)

        start = time.monotonic()
        real_response = await self._inner.handle_async_request(request)
        elapsed_ms = (time.monotonic() - start) * 1000
        recorded_resp = await _capture_response_async(real_response)
        session.add_interaction(recorded_req, recorded_resp, duration_ms=elapsed_ms)
        return _rehydrate_response(real_response, recorded_resp)

    async def aclose(self) -> None:
        await self._inner.aclose()


# ──────────────────────────────────────────────────────────────────────
# Globally install transports
# ──────────────────────────────────────────────────────────────────────


_orig_client_init = httpx.Client.__init__
_orig_async_client_init = httpx.AsyncClient.__init__
_installed = False


def install() -> None:
    """Globally replace httpx Client constructors so they auto-wrap their
    transport with CuesheetTransport. Safe to call multiple times.

    If the caller passes their own `transport=` (e.g. an httpx.MockTransport
    in tests), we WRAP that transport rather than replace it. This means the
    test author keeps their fake backend while cuesheet's session logic still
    intercepts in front.
    """
    global _installed
    if _installed:
        return

    def patched_client_init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        user_transport = kwargs.pop("transport", None)
        kwargs["transport"] = CuesheetTransport(inner=user_transport)
        _orig_client_init(self, *args, **kwargs)

    def patched_async_client_init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
        user_transport = kwargs.pop("transport", None)
        kwargs["transport"] = CuesheetAsyncTransport(inner=user_transport)
        _orig_async_client_init(self, *args, **kwargs)

    httpx.Client.__init__ = patched_client_init  # type: ignore[method-assign,assignment]
    httpx.AsyncClient.__init__ = patched_async_client_init  # type: ignore[method-assign,assignment]
    _installed = True


def uninstall() -> None:
    global _installed
    if not _installed:
        return
    httpx.Client.__init__ = _orig_client_init  # type: ignore[method-assign,assignment]
    httpx.AsyncClient.__init__ = _orig_async_client_init  # type: ignore[method-assign,assignment]
    _installed = False


def is_installed() -> bool:
    return _installed


# ──────────────────────────────────────────────────────────────────────
# Request capture
# ──────────────────────────────────────────────────────────────────────


def _to_recorded_request(request: httpx.Request) -> RecordedRequest:
    try:
        body_bytes = request.content
    except Exception:
        body_bytes = b""
    decoded, raw = decode_body(body_bytes)
    return RecordedRequest(
        method=request.method,
        url=str(request.url),
        headers=dict(request.headers),
        body=decoded,
        body_raw=raw,
    )


# ──────────────────────────────────────────────────────────────────────
# Response capture
# ──────────────────────────────────────────────────────────────────────


def _capture_response_sync(response: httpx.Response) -> RecordedResponse:
    content_type = response.headers.get("content-type", "").lower()
    is_streaming = "event-stream" in content_type or response.headers.get(
        "transfer-encoding"
    ) == "chunked"

    try:
        body_bytes = response.read()
    except Exception:
        body_bytes = b""

    chunks: list[str] | None = None
    if is_streaming and body_bytes:
        chunks = _split_sse_chunks(body_bytes.decode("utf-8", errors="replace"))

    decoded, raw = decode_body(body_bytes)
    return RecordedResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        body=decoded,
        body_raw=raw,
        body_stream=chunks,
        is_streaming=is_streaming,
    )


async def _capture_response_async(response: httpx.Response) -> RecordedResponse:
    content_type = response.headers.get("content-type", "").lower()
    is_streaming = "event-stream" in content_type or response.headers.get(
        "transfer-encoding"
    ) == "chunked"

    try:
        body_bytes = await response.aread()
    except Exception:
        body_bytes = b""

    chunks: list[str] | None = None
    if is_streaming and body_bytes:
        chunks = _split_sse_chunks(body_bytes.decode("utf-8", errors="replace"))

    decoded, raw = decode_body(body_bytes)
    return RecordedResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        body=decoded,
        body_raw=raw,
        body_stream=chunks,
        is_streaming=is_streaming,
    )


def _split_sse_chunks(text: str) -> list[str]:
    parts = text.split("\n\n")
    result: list[str] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i < len(parts) - 1:
            result.append(part + "\n\n")
        else:
            result.append(part)
    return result


# ──────────────────────────────────────────────────────────────────────
# Response synthesis (replay) + rehydration (during record)
# ──────────────────────────────────────────────────────────────────────


def _synthesize_response(interaction: Interaction) -> httpx.Response:
    """Build an httpx.Response from a cassette entry. Used for replay."""
    resp = interaction.response

    # Drop hop-by-hop headers that confuse httpx on replay
    headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in {"transfer-encoding", "content-encoding", "content-length"}
    }

    if resp.is_streaming and resp.body_stream:
        chunks = resp.body_stream

        def stream_bytes() -> Iterator[bytes]:
            for chunk in chunks:
                yield chunk.encode("utf-8")

        return httpx.Response(
            status_code=resp.status_code,
            headers=headers,
            stream=_SyncBytesIter(stream_bytes()),
        )

    body = encode_body(resp.body, resp.body_raw)
    return httpx.Response(
        status_code=resp.status_code,
        headers=headers,
        content=body,
    )


def _rehydrate_response(
    original: httpx.Response, recorded: RecordedResponse
) -> httpx.Response:
    """After capturing a response during recording, hand back something the
    caller can re-read. httpx.Response.read() caches into `_content`, so the
    `original` is already re-readable - we can just return it.
    """
    return original


class _SyncBytesIter(httpx.SyncByteStream):
    def __init__(self, source: Iterator[bytes]) -> None:
        self._source = source

    def __iter__(self) -> Iterator[bytes]:
        yield from self._source


class _AsyncBytesIter(httpx.AsyncByteStream):
    def __init__(self, source: AsyncIterator[bytes]) -> None:
        self._source = source

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._source:
            yield chunk
