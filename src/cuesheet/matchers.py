"""Request matching strategies.

When a producer makes an HTTP call, we need to decide whether any
already-recorded Interaction is a fit. Matchers split that into a list
of small, composable boolean checks.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cuesheet.cassette import Interaction, RecordedRequest

Matcher = Callable[[RecordedRequest, RecordedRequest], bool]


def match_method(a: RecordedRequest, b: RecordedRequest) -> bool:
    return a.method.upper() == b.method.upper()


def match_url(a: RecordedRequest, b: RecordedRequest) -> bool:
    # Strip query strings: most LLM APIs don't use them for identity
    return _strip_query(a.url) == _strip_query(b.url)


def match_url_full(a: RecordedRequest, b: RecordedRequest) -> bool:
    return a.url == b.url


def match_model(a: RecordedRequest, b: RecordedRequest) -> bool:
    return _get_field(a.body, "model") == _get_field(b.body, "model")


def match_messages(a: RecordedRequest, b: RecordedRequest) -> bool:
    """Strict message-list match. Order and content must agree."""
    return _normalize_messages(_get_field(a.body, "messages", [])) == _normalize_messages(
        _get_field(b.body, "messages", [])
    )


def match_tools(a: RecordedRequest, b: RecordedRequest) -> bool:
    return _get_field(a.body, "tools", []) == _get_field(b.body, "tools", [])


def match_max_tokens(a: RecordedRequest, b: RecordedRequest) -> bool:
    keys = ("max_tokens", "max_completion_tokens")
    return tuple(_get_field(a.body, k) for k in keys) == tuple(
        _get_field(b.body, k) for k in keys
    )


def match_temperature(a: RecordedRequest, b: RecordedRequest) -> bool:
    return _get_field(a.body, "temperature") == _get_field(b.body, "temperature")


def match_body_strict(a: RecordedRequest, b: RecordedRequest) -> bool:
    """Fallback for non-LLM endpoints: full body equality."""
    if a.body is not None or b.body is not None:
        return a.body == b.body
    return a.body_raw == b.body_raw


# ──────────────────────────────────────────────────────────────────────
# Composable default
# ──────────────────────────────────────────────────────────────────────


DEFAULT_MATCH_ON: tuple[str, ...] = (
    "method",
    "url",
    "model",
    "messages",
    "tools",
    "max_tokens",
    "temperature",
)

_NAMED_MATCHERS: dict[str, Matcher] = {
    "method": match_method,
    "url": match_url,
    "url_full": match_url_full,
    "model": match_model,
    "messages": match_messages,
    "tools": match_tools,
    "max_tokens": match_max_tokens,
    "temperature": match_temperature,
    "body": match_body_strict,
}


def default_matcher(*names: str) -> Matcher:
    """Build a composite matcher from a list of named primitives."""
    chosen_names = names or DEFAULT_MATCH_ON
    chosen = [_NAMED_MATCHERS[name] for name in chosen_names if name in _NAMED_MATCHERS]

    def composite(a: RecordedRequest, b: RecordedRequest) -> bool:
        return all(m(a, b) for m in chosen)

    composite.__name__ = f"matcher({','.join(chosen_names)})"
    return composite


def find_match(
    interactions: list[Interaction],
    request: RecordedRequest,
    matcher: Matcher,
) -> Interaction | None:
    """First interaction that matches, or None."""
    for interaction in interactions:
        if matcher(interaction.request, request):
            return interaction
    return None


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────


def _strip_query(url: str) -> str:
    return url.split("?", 1)[0]


def _get_field(body: Any, key: str, default: Any = None) -> Any:
    if isinstance(body, dict):
        return body.get(key, default)
    return default


def _normalize_messages(messages: Any) -> Any:
    """Trim trailing whitespace + drop fields that vary between SDKs (like
    `name`, `id`, server-side fields) to make matching less brittle."""
    if not isinstance(messages, list):
        return messages
    out: list[Any] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        content = m.get("content")
        if isinstance(content, str):
            content = content.strip()
        out.append({"role": m.get("role"), "content": content})
    return out
