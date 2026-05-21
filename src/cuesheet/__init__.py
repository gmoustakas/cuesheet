"""cuesheet - replay LLM API calls in tests. Zero cost. Zero flakes.

  >>> import cuesheet
  >>> @cuesheet.cassette("test_my_agent.yaml")
  ... def test_my_agent():
  ...     # First run: hits the real LLM, saves the response.
  ...     # Subsequent runs: no network, same response.
  ...     pass

See README for matchers, modes, scrubbers, and the pytest plugin.
"""
from cuesheet._version import __version__
from cuesheet.api import cassette, current_session, disable
from cuesheet.matchers import Matcher, default_matcher
from cuesheet.modes import Mode
from cuesheet.scrubbers import add_scrubber, scrub_string

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
