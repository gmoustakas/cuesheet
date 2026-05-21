"""Public API: cassette() decorator + context manager + current_session()."""
from __future__ import annotations

import functools
import inspect
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import Token
from pathlib import Path
from typing import Any, TypeVar

from cuesheet.matchers import Matcher, default_matcher
from cuesheet.modes import Mode
from cuesheet.session import Session, _current, current
from cuesheet.transport import install as install_transport

F = TypeVar("F", bound=Callable[..., Any])


def _resolve_mode(explicit: str | Mode | None) -> Mode:
    if explicit is not None:
        return Mode(explicit) if isinstance(explicit, str) else explicit
    from_env = os.environ.get("CUESHEET_DEFAULT_MODE")
    if from_env:
        return Mode(from_env)
    return Mode.RECORD_NEW


def _resolve_matcher(match_on: list[str] | tuple[str, ...] | Matcher | None) -> Matcher:
    if match_on is None:
        return default_matcher()
    if callable(match_on):
        return match_on  # type: ignore[return-value]
    return default_matcher(*match_on)


def cassette(
    path: str | Path,
    *,
    mode: str | Mode | None = None,
    match_on: list[str] | tuple[str, ...] | Matcher | None = None,
    scrub: bool = True,
) -> _CassetteContext:
    """Open a cassette - usable as both a decorator and a context manager.

    >>> @cuesheet.cassette("test.yaml")
    ... def test_foo():
    ...     ...

    >>> with cuesheet.cassette("test.yaml"):
    ...     ...
    """
    return _CassetteContext(
        path=Path(path),
        mode=_resolve_mode(mode),
        matcher=_resolve_matcher(match_on),
        scrub=scrub,
    )


def current_session() -> Session | None:
    """Return the active session, if any."""
    return current()


# ──────────────────────────────────────────────────────────────────────


class _CassetteContext:
    """Dual-mode object: a context manager AND a decorator.

    We manage the contextvar directly rather than going through a
    @contextmanager-decorated generator, because in some Python configurations
    the wrapping generator object can be GC'd before __exit__ runs, which
    prematurely fires its finally block and resets our session.
    """

    def __init__(
        self,
        path: Path,
        mode: Mode,
        matcher: Matcher,
        scrub: bool,
    ) -> None:
        self.path = path
        self.mode = mode
        self.matcher = matcher
        self.scrub = scrub
        self._session: Session | None = None
        self._token: Token[Session | None] | None = None

    # ── context manager ───────────────────────────────────────────────

    def __enter__(self) -> Session:
        install_transport()
        self._session = Session(
            path=self.path,
            mode=self.mode,
            matcher=self.matcher,
            scrub=self.scrub,
        )
        self._token = _current.set(self._session)
        return self._session

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        session = self._session
        if session is not None and session.recorded_count > 0:
            try:
                session.flush()
            except Exception:
                # Don't shadow user exception; log via the session's logger
                import logging
                logging.getLogger("cuesheet").exception(
                    "failed to flush cassette %s on exit", session.path
                )
        if self._token is not None:
            try:
                _current.reset(self._token)
            except ValueError:
                # Token may have been invalidated if nested contexts misbehaved
                _current.set(None)
        self._session = None
        self._token = None

    # ── async context manager ─────────────────────────────────────────

    async def __aenter__(self) -> Session:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)

    # ── decorator ─────────────────────────────────────────────────────

    def __call__(self, func: F) -> F:
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with self:
                    return await func(*args, **kwargs)
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with self:
                return func(*args, **kwargs)
        return sync_wrapper  # type: ignore[return-value]


# ──────────────────────────────────────────────────────────────────────


@contextmanager
def disable() -> Iterator[None]:
    """Temporarily route around cuesheet in a section of code, even if a
    cassette is active. Useful when you want one specific call to hit the
    real network.
    """
    token = _current.set(None)
    try:
        yield
    finally:
        _current.reset(token)
