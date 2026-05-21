"""Secret scrubbing.

Cassettes get committed to repos. We do not want API keys / tokens / PII in
those files. Apply a list of regex patterns to recorded requests + responses
before writing.

Defaults are conservative on purpose: every match is replaced with
`<REDACTED>`. Users can extend the list with `encore.add_scrubber(r"...")`.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

# Default redaction patterns. Tuned to catch the common villains without
# eating innocent text. Add more via add_scrubber().
_BUILTIN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),                   # Anthropic
    re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),                  # OpenAI project keys
    re.compile(r"sk-[A-Za-z0-9_-]{32,}"),                       # OpenAI legacy
    re.compile(r"re_[A-Za-z0-9]{20,}"),                         # Resend
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),                      # Google
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),                        # GitHub
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),                    # GitLab
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),   # Email
    re.compile(r"Bearer\s+[A-Za-z0-9._\-+/]{20,}", re.IGNORECASE),       # Bearer token
]


_user_patterns: list[re.Pattern[str]] = []


def add_scrubber(pattern: str | re.Pattern[str]) -> None:
    """Register an additional regex to redact from cassettes.

    Patterns are applied in addition to the built-in list, NEVER replacing it.
    Use a raw string. Each match becomes `<REDACTED>`.
    """
    if isinstance(pattern, str):
        pattern = re.compile(pattern)
    _user_patterns.append(pattern)


def reset_scrubbers() -> None:
    """Drop user-added patterns. Useful in tests."""
    _user_patterns.clear()


def all_patterns() -> list[re.Pattern[str]]:
    return _BUILTIN_PATTERNS + _user_patterns


def scrub_string(value: str) -> str:
    out = value
    for pat in all_patterns():
        out = pat.sub("<REDACTED>", out)
    return out


def scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Drop or redact common auth headers entirely; redact pattern-matched
    values in the rest."""
    sensitive = {"authorization", "x-api-key", "anthropic-api-key", "openai-api-key"}
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in sensitive:
            out[k] = "<REDACTED>"
        else:
            out[k] = scrub_string(v)
    return out


def scrub_obj(value: Any) -> Any:
    """Walk a JSON-like structure, scrubbing strings recursively."""
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, dict):
        return {k: scrub_obj(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_obj(v) for v in value]
    return value


def iter_builtin_patterns() -> Iterator[re.Pattern[str]]:
    return iter(_BUILTIN_PATTERNS)


def iter_user_patterns() -> Iterator[re.Pattern[str]]:
    return iter(_user_patterns)


def set_patterns(patterns: Iterable[str | re.Pattern[str]]) -> None:
    """Override the user-added list. Built-ins are not affected."""
    _user_patterns.clear()
    for p in patterns:
        add_scrubber(p)
