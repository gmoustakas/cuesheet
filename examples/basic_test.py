"""Smallest possible example.

Run once with ANTHROPIC_API_KEY set: hits the real API, records the response.
Run again with no API key: replays from the cassette.

    pip install -e ".[dev]"
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/basic_test.py            # records
    unset ANTHROPIC_API_KEY
    python examples/basic_test.py            # replays
"""
from anthropic import Anthropic

import cuesheet


@cuesheet.cassette("examples/cassettes/basic.yaml")
def main() -> None:
    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": "Say hello in exactly five words."}],
    )
    print("model:", response.model)
    print("text:", response.content[0].text)


if __name__ == "__main__":
    main()
