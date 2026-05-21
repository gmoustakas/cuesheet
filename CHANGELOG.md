# Changelog

## [0.1.0] - 2026-05-21

Initial release. End-to-end working system.

### Added
- `encore.cassette()` as both decorator and context manager
- HTTP-level interception of `httpx.Client` and `httpx.AsyncClient`
- Cassette IO: YAML round-trip via `ruamel.yaml`, git-friendly format
- Five recording modes: `record_new`, `record_once`, `record_always`, `replay_only`, `bypass`
- Default + custom matchers: method, url, model, messages, tools, temperature, max_tokens
- Streaming support: SSE chunks recorded + replayed faithfully
- Secret scrubbing: built-in patterns for Anthropic / OpenAI / GitHub / Google / Resend keys, JWTs, emails, bearer tokens
- `encore.add_scrubber()` to extend
- pytest plugin: `encore_cassette` fixture auto-discovering `tests/cassettes/<test_name>.yaml`
- `@pytest.mark.encore(path=..., mode=...)` per-test override
- CLI: `encore list`, `inspect`, `stats`, `scrub`
- Provider auto-detection: Anthropic, OpenAI, Mistral, Google, Groq, Cohere, DeepSeek, Together, Azure OpenAI
- Async tests: full `httpx.AsyncClient` support
- `ENCORE_DEFAULT_MODE` env var (e.g. `ENCORE_DEFAULT_MODE=replay_only pytest` in CI)
- `encore.disable()` context manager for one-off real-network passes inside a cassette block

### Tests
- pytest suite covering cassette IO, matchers, modes, scrubbers, transport (sync+async), CLI
- Ruff + mypy --strict (with per-module relaxations) both clean

### Notes
- 0.x line; public API exported from `encore.__init__` is the stable contract
- Anthropic + OpenAI verified working via respx-mocked end-to-end tests
