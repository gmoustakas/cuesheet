# Changelog

## [0.2.0] - 2026-05-21

Five small wins on top of 0.1.0. Backward-compatible.

### Added
- **Replay-miss diagnostics.** `CassetteMissingMatch` now carries the live request, the closest near-miss interaction, a per-criterion breakdown of what matched and what diverged, and a unified-diff of the diverging request bodies. Call `exc.diagnostic()` for a multi-line message that fits inside a pytest traceback.
- **`cuesheet diff <a.yaml> <b.yaml>`.** Semantic diff between two cassettes. Pairs interactions by method + url + model + messages, then reports response-body changes, added, and removed interactions with a colored unified diff.
- **Token and cost stats.** `cuesheet stats` now reads `usage` / `usageMetadata` from recorded responses, sums input + output tokens per model, and estimates cost using a built-in pricing table covering current Anthropic, OpenAI, Google, Mistral, Groq, and DeepSeek model families. Unpriced models are surfaced explicitly.
- **Pretty SSE in the web UI.** Stream chunks now have a `decoded` / `raw` toggle. Decoded mode parses each SSE frame into its event name and JSON-pretty-printed data payload.
- **`cuesheet init`.** Scaffolds `tests/cassettes/`, a starter `conftest.py`, and an example test into the current project. Idempotent; `--force` overwrites.

### Internal
- New module `cuesheet.pricing` with `extract_usage`, `cost_estimate`, and `aggregate` helpers.
- New `find_closest_miss` helper in `cuesheet.matchers` that scores interactions by how many criteria they match.
- New Jinja filter `sse_parse` registered on the web app.
- Test count: 76 (up from 48).

## [0.1.0] - 2026-05-21

Initial release. End-to-end working system.

### Added
- `cuesheet.cassette()` as both decorator and context manager
- HTTP-level interception of `httpx.Client` and `httpx.AsyncClient`
- Cassette IO: YAML round-trip via `ruamel.yaml`, git-friendly format
- Five recording modes: `record_new`, `record_once`, `record_always`, `replay_only`, `bypass`
- Default + custom matchers: method, url, model, messages, tools, temperature, max_tokens
- Streaming support: SSE chunks recorded + replayed faithfully
- Secret scrubbing: built-in patterns for Anthropic / OpenAI / GitHub / Google / Resend keys, JWTs, emails, bearer tokens
- `cuesheet.add_scrubber()` to extend
- pytest plugin: `cuesheet_cassette` fixture auto-discovering `tests/cassettes/<test_name>.yaml`
- `@pytest.mark.cuesheet(path=..., mode=...)` per-test override
- CLI: `cuesheet list`, `inspect`, `stats`, `scrub`
- Provider auto-detection: Anthropic, OpenAI, Mistral, Google, Groq, Cohere, DeepSeek, Together, Azure OpenAI
- Async tests: full `httpx.AsyncClient` support
- `CUESHEET_DEFAULT_MODE` env var (e.g. `CUESHEET_DEFAULT_MODE=replay_only pytest` in CI)
- `cuesheet.disable()` context manager for one-off real-network passes inside a cassette block

### Tests
- pytest suite covering cassette IO, matchers, modes, scrubbers, transport (sync+async), CLI
- Ruff + mypy --strict (with per-module relaxations) both clean

### Notes
- 0.x line; public API exported from `cuesheet.__init__` is the stable contract
- Anthropic + OpenAI verified working via respx-mocked end-to-end tests
