"""encore - replay LLM API calls in tests. Zero cost. Zero flakes.

  >>> import encore
  >>> @encore.cassette("test_my_agent.yaml")
  ... def test_my_agent():
  ...     # First run: hits the real LLM, saves the response.
  ...     # Subsequent runs: no network, same response.
  ...     pass

See README for matchers, modes, scrubbers, and the pytest plugin.
"""
from encore._version import __version__
from encore.api import cassette, current_session, disable
from encore.matchers import Matcher, default_matcher
from encore.modes import Mode
from encore.scrubbers import add_scrubber, scrub_string

__all__ = [
    "Matcher",
    "Mode",
    "__version__",
    "add_scrubber",
    "cassette",
    "current_session",
    "default_matcher",
    "disable",
    "scrub_string",
]
