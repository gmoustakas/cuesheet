<div align="center">

# encore

**Replay LLM API calls in tests. Zero cost. Zero flakes.**

Like [vcr.py](https://github.com/kevin1024/vcrpy) - but for the Anthropic, OpenAI, Mistral, and any other LLM SDK that uses `httpx`.

[![PyPI](https://img.shields.io/pypi/v/encore.svg)](https://pypi.org/project/encore/)
[![Python](https://img.shields.io/pypi/pyversions/encore.svg)](https://pypi.org/project/encore/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## The problem

You wrote a function that calls Claude. Now you want to test it.

- Hitting the real API in tests is **slow, flaky, and expensive**.
- Hand-rolled mocks **drift from reality** the moment the SDK changes.
- Existing solutions either lock you into a framework or don't understand LLM payloads.

## The fix

```python
import encore

@encore.cassette("tests/cassettes/test_summarizer.yaml")
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

**First run**: hits the real API, records the response to `test_summarizer.yaml`.
**Every run after**: zero network calls. Same response. Same assertions.

## How it works

encore intercepts the underlying `httpx` transport that Anthropic, OpenAI, Mistral, Gemini, LiteLLM (and any modern Python LLM SDK) all sit on top of. One library, every provider.

- ✅ Sync + async
- ✅ Streaming (chunks recorded + replayed at configurable speed)
- ✅ Multi-provider in a single cassette
- ✅ Git-friendly YAML format
- ✅ Auto-scrubs API keys, JWTs, emails before write
- ✅ pytest plugin (zero-config fixtures)
- ✅ Local web UI with live updates as tests record

## Install

```bash
pip install encore               # SDK + CLI
pip install "encore[web]"        # + local web UI
pip install "encore[all]"        # everything
```

## Common patterns

### Decorator (simplest)
```python
@encore.cassette("test_x.yaml")
def test_x():
    ...
```

### Context manager (for non-test code or partial fixtures)
```python
with encore.cassette("my_run.yaml"):
    response = client.messages.create(...)
```

### pytest fixture (zero-config)
```python
def test_my_agent(encore_cassette):
    # auto-uses tests/cassettes/test_my_agent.yaml
    ...
```

### CI: forbid recording (catches missing fixtures fast)
```python
@encore.cassette("test_x.yaml", mode="replay_only")
def test_x():
    ...
```
Or globally via env:
```bash
ENCORE_DEFAULT_MODE=replay_only pytest
```

## Recording modes

| Mode | Behavior | When to use |
|---|---|---|
| `record_new` *(default)* | Replay if cassette exists; record + save if missing | Local dev |
| `record_once` | Record only if file empty; never re-record | First-run fixtures |
| `record_always` | Always hit the real API; overwrite the cassette | Refresh after API changes |
| `replay_only` | Never call the network; fail if cassette missing | CI guarantee |
| `bypass` | Ignore cassette entirely | Disable in one place |

## Matchers

Two requests "match" if they're identical on:
- HTTP method + URL
- Model
- Messages list (semantic, order-preserving)
- Tools schema
- Temperature, max_tokens, etc.

Override any of these:
```python
@encore.cassette("x.yaml", match_on=["method", "url", "model", "messages"])
def test_x():
    ...
```

Or write a custom matcher:
```python
@encore.matcher
def ignore_user_id(req_a, req_b):
    a, b = req_a.body.copy(), req_b.body.copy()
    a.pop("user", None); b.pop("user", None)
    return a == b
```

## Secret scrubbing

Cassettes are committed to your repo. encore strips API keys, JWTs, and emails before write. Built-in patterns:

- Anthropic keys (`sk-ant-...`)
- OpenAI keys (`sk-...`)
- Generic bearer tokens
- JWTs
- Common email regex

Add your own:
```python
encore.add_scrubber(r"INTERNAL-[A-Z0-9]{16}")
```

## CLI

```bash
encore list                          # all cassettes in cwd
encore inspect tests/cassettes/x.yaml # pretty-print one cassette
encore stats                          # interaction + size totals
encore scrub tests/cassettes/        # re-apply scrubbers in place
encore web                           # open the local web UI
```

## Web UI

```bash
encore web                           # opens http://127.0.0.1:8095
```

Dark + ochre, mobile-responsive, zero auth. Pages:

- **Index**: a sortable table of every cassette with path, providers, interaction count, size, last modified.
- **Cassette detail**: click any row for a per-interaction inspector with request/response panes, syntax-highlighted JSON, expandable headers, and a stream-chunk view.
- **JSON API**: `/api/cassettes`, `/api/cassettes/_detail`, `/api/stats` for tooling.

**Live updates.** Keep the UI open in one tab and run your tests in another. The dashboard subscribes to filesystem events via SSE and refreshes the affected section the moment a cassette gets recorded, modified, or deleted. The pulsing `• live` pill in the header confirms the watcher is connected.

Designed to be useful in two workflows: code-review (browse what got recorded) and debugging (open one cassette, find the offending interaction, see the raw response). No daemon, no persistence. It just renders the files on disk.

### Screenshots

![Index page - every cassette with provider, count, modified time](docs/screenshots/index.png)
![Cassette detail - request/response panes, expandable headers](docs/screenshots/cassette-detail.png)
![Live updates - the dashboard refreshes the moment tests record a new cassette](docs/screenshots/live-update.png)

### Custom logo

The default brand mark is the Unicode music repeat-sign `𝄇` (semantic match for "play again"). If you want a graphical logo, here's an image-generation prompt that fits the brand:

> A minimalist square logo for a Python library called `encore`. The mark sits on a near-black background (#07080c). The composition is a single ochre-yellow (#f4b942) glyph: a stylised music repeat-sign, two vertical lines with two dots, where the dots are subtly reinterpreted as tiny LED indicators. Flat, geometric, no gradients, no shadows. Confident, technical, intentional, in the visual language of a JetBrains plugin icon or a Vercel mark, not a children's app. 1024x1024 PNG, transparent edges acceptable.

## Comparison

| | vcr.py | pytest-vcr | RESPX | **encore** |
|---|---|---|---|---|
| HTTP-level | ✅ | ✅ | ✅ | ✅ |
| LLM-payload aware | ❌ | ❌ | ❌ | ✅ |
| Streaming response replay | partial | partial | ❌ | ✅ |
| Provider-agnostic (Anthropic + OpenAI + ...) | ✅ | ✅ | ✅ | ✅ |
| Auto API-key scrubbing | manual | manual | ❌ | ✅ |
| pytest plugin | manual | ✅ | ❌ | ✅ |

## Status

🟢 **Beta** - used by the maintainer, tests green, real LLM SDK roundtrip verified. 0.x public API is `encore.cassette` + `encore.matcher` + `encore.add_scrubber`. Internals may change between 0.x minors.

## License

MIT. Built by [Giorgos Moustakas](https://georgemou.gr) in Greece.
