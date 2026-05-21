<div align="center">

<img src="https://www.georgemou.gr/_next/image?url=%2Fprojects%2Fcuesheet%2Flogo-256.png&w=1080&q=75" alt="cuesheet" width="180" height="180">

# cuesheet

**Record once. Replay forever. Test LLM-calling code without burning the API.**

[![PyPI](https://img.shields.io/pypi/v/cuesheet.svg)](https://pypi.org/project/cuesheet/)
[![Python](https://img.shields.io/pypi/pyversions/cuesheet.svg)](https://pypi.org/project/cuesheet/)
[![CI](https://github.com/gmoustakas/cuesheet/actions/workflows/ci.yml/badge.svg)](https://github.com/gmoustakas/cuesheet/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## The problem

If you've ever tried to write tests for code that calls an LLM, you know the drill.

- Hitting Anthropic or OpenAI in CI is **slow** (multi-second per call), **flaky** (rate limits, transient errors, sampling drift), and **expensive** (every PR run bleeds tokens).
- Hand-rolled mocks **rot the moment the SDK ships a breaking change**, and they never quite match what the real API returns.
- Existing HTTP fixtures (vcr.py, respx, pytest-vcr) work, but they don't understand LLM payloads, don't replay streamed responses faithfully, and don't scrub API keys for you.

So most teams settle for one of three bad options: skip the tests, mark them slow and skip them in CI, or write a brittle mock and pray.

## What cuesheet does

`cuesheet` is a test-fixture library for any Python LLM SDK that uses `httpx` under the hood (Anthropic, OpenAI, Mistral, Gemini, Cohere, Groq, DeepSeek, Together, LiteLLM, and anything else built on the standard transport).

You wrap your test in `@cuesheet.cassette(...)`. The first time it runs, cuesheet hits the real API and saves the request/response pair to a YAML file you commit to your repo. Every run after that replays from the file. Same response, byte-for-byte. No network calls. No flakes. No cost.

```python
import cuesheet

@cuesheet.cassette("tests/cassettes/test_summarizer.yaml")
def test_summarizer():
    from anthropic import Anthropic
    client = Anthropic()

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": "Summarize: ..."}],
    )

    assert "key point" in response.content[0].text
```

That's the whole API. One decorator. One YAML file per test. Drop it in.

## Features

- Sync and async clients, both supported.
- Streaming responses recorded as raw SSE chunks and replayed in order at configurable speed.
- One cassette can hold multiple interactions across multiple providers.
- YAML format chosen for **git-friendly diffs** during code review.
- Auto-scrubs Anthropic and OpenAI keys, JWTs, bearer tokens, and common email regexes before writing to disk.
- Composable matchers (method, URL, model, messages, tools, temperature, ...) overridable per-cassette or globally.
- pytest plugin: zero-config fixture auto-discovers `tests/cassettes/<test_name>.yaml`.
- Local web UI with live updates as tests record (FastAPI, HTMX, SSE).
- Strictly typed: `mypy --strict` clean on the public surface.

## Install

```bash
pip install cuesheet               # SDK + CLI
pip install "cuesheet[web]"        # also installs the web UI
pip install "cuesheet[all]"        # everything
```

Python 3.10+.

## Common patterns

### Decorator (simplest)

```python
@cuesheet.cassette("test_x.yaml")
def test_x():
    ...
```

### Context manager

```python
with cuesheet.cassette("my_run.yaml"):
    response = client.messages.create(...)
```

### pytest fixture (zero-config)

```python
def test_my_agent(cuesheet_cassette):
    # auto-uses tests/cassettes/test_my_agent.yaml
    ...
```

### CI: forbid recording, fail on missing cassettes

```python
@cuesheet.cassette("test_x.yaml", mode="replay_only")
def test_x():
    ...
```

Or globally:

```bash
CUESHEET_DEFAULT_MODE=replay_only pytest
```

This is the mode you want in CI. It guarantees no test ever silently records a new cassette against the real API on the build server.

## Recording modes

| Mode | Behavior | When to use |
|---|---|---|
| `record_new` *(default)* | Replay if cassette exists; record and save if missing | Local dev |
| `record_once` | Record only if file empty; never re-record | First-run fixtures |
| `record_always` | Always hit the real API; overwrite the cassette | Refresh after API changes |
| `replay_only` | Never call the network; fail if cassette missing | CI |
| `bypass` | Ignore cassette entirely | Disable in one place |

## Matchers

Two requests match if they're identical on:

- HTTP method and URL
- Model
- Messages list (semantic, order-preserving)
- Tools schema
- Temperature, max_tokens, and other generation params

Override per cassette:

```python
@cuesheet.cassette("x.yaml", match_on=["method", "url", "model", "messages"])
def test_x():
    ...
```

Or write a custom matcher:

```python
@cuesheet.matcher
def ignore_user_id(req_a, req_b):
    a, b = req_a.body.copy(), req_b.body.copy()
    a.pop("user", None); b.pop("user", None)
    return a == b
```

## Secret scrubbing

Cassettes get committed to your repo. Anything you didn't redact will end up on GitHub. `cuesheet` strips API keys, JWTs, and emails before write. Built-in patterns:

- Anthropic keys (`sk-ant-...`)
- OpenAI keys (`sk-...`, `sk-proj-...`)
- Generic bearer tokens
- JWTs (`eyJ...` triplets)
- Common email regex

Add your own:

```python
cuesheet.add_scrubber(r"INTERNAL-[A-Z0-9]{16}")
```

If you find a secret pattern that should be in the default set, please open a PR.

## CLI

```bash
cuesheet list                              # all cassettes in cwd
cuesheet inspect tests/cassettes/x.yaml    # pretty-print one cassette
cuesheet stats                             # interaction + size totals
cuesheet scrub tests/cassettes/            # re-apply scrubbers in place
cuesheet web                               # open the local web UI
```

## Web UI

```bash
cuesheet web                               # opens http://127.0.0.1:8095
```

Dark plus ochre, mobile-responsive, zero auth. The dashboard watches the filesystem and pushes change events over SSE, so the index and cassette detail pages update in real time as your tests run in another terminal. The pulsing `live` pill in the header confirms the watcher is connected. No daemon, no persistence; it just renders the files on disk.

## Maturity

cuesheet is at v0.1.0. The public API (`cuesheet.cassette`, `cuesheet.matcher`, `cuesheet.add_scrubber`) is stable; internals may shift between 0.x minors. The interception logic hooks at the `httpx` transport layer, not at the SDK layer, so it's provider-agnostic by construction. Each SDK has quirks though, so if yours misbehaves, please [file an issue](https://github.com/gmoustakas/cuesheet/issues) with a minimal repro.

## Supported providers

Any Python SDK that calls an LLM provider over `httpx` works. The providers below are explicitly detected and tagged in the web UI:

- Anthropic
- OpenAI
- Google (Gemini)
- Mistral
- Cohere
- Groq
- DeepSeek
- Together
- LiteLLM (passes through to the underlying provider URL)

If your provider isn't in the list, cuesheet still records and replays it; you just won't get the coloured provider pill in the UI.

## Comparison

|                                | vcr.py | pytest-vcr | RESPX | **cuesheet** |
|---|:---:|:---:|:---:|:---:|
| HTTP-level                     | ✅ | ✅ | ✅ | ✅ |
| LLM-payload aware              | ❌ | ❌ | ❌ | ✅ |
| Streaming response replay      | ⚠️ partial | ⚠️ partial | ❌ | ✅ |
| Provider-agnostic              | ✅ | ✅ | ✅ | ✅ |
| Auto API-key scrubbing         | ⚠️ manual  | ⚠️ manual  | ❌ | ✅ |
| pytest plugin                  | ⚠️ manual  | ✅ | ❌ | ✅ |
| Web UI with live updates       | ❌ | ❌ | ❌ | ✅ |

## License

MIT. Built by [George Moustakas](https://www.georgemou.gr/) in Greece.
