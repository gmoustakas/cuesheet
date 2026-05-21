"""Token and cost helpers.

Cassette responses from LLM providers usually carry a usage / usage_metadata
object. This module pulls those numbers out and multiplies by a small built-in
pricing table so we can answer "how much would this test suite cost if it
weren't replayed?"

The pricing table is best-effort and may drift as providers change prices.
Treat the numbers as an estimate, not a quote. Override per model by
mutating PRICING at runtime, or pass your own table into `cost_estimate`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cuesheet.cassette import Interaction


@dataclass(frozen=True)
class ModelRate:
    """Per-million-token pricing in USD. Most providers bill at this scale."""

    input_per_million: float
    output_per_million: float


# Best-effort pricing snapshot, USD per million tokens.
# Keys are matched as a prefix against the recorded model string, so
# "claude-sonnet-4-5-20251022" still hits "claude-sonnet-4-5".
PRICING: dict[str, ModelRate] = {
    # Anthropic
    "claude-opus-4-7":      ModelRate(15.00, 75.00),
    "claude-opus-4":        ModelRate(15.00, 75.00),
    "claude-sonnet-4-5":    ModelRate(3.00, 15.00),
    "claude-sonnet-4":      ModelRate(3.00, 15.00),
    "claude-haiku-4-5":     ModelRate(1.00, 5.00),
    "claude-3-5-sonnet":    ModelRate(3.00, 15.00),
    "claude-3-5-haiku":     ModelRate(0.80, 4.00),
    "claude-3-opus":        ModelRate(15.00, 75.00),
    # OpenAI
    "gpt-5":                ModelRate(1.25, 10.00),
    "gpt-4o-mini":          ModelRate(0.15, 0.60),
    "gpt-4o":               ModelRate(2.50, 10.00),
    "gpt-4-turbo":          ModelRate(10.00, 30.00),
    "gpt-4":                ModelRate(30.00, 60.00),
    "gpt-3.5-turbo":        ModelRate(0.50, 1.50),
    "o1-mini":              ModelRate(3.00, 12.00),
    "o1":                   ModelRate(15.00, 60.00),
    # Google
    "gemini-2.5-pro":       ModelRate(1.25, 10.00),
    "gemini-2.5-flash":     ModelRate(0.30, 2.50),
    "gemini-2.0-flash":     ModelRate(0.10, 0.40),
    "gemini-1.5-pro":       ModelRate(1.25, 5.00),
    "gemini-1.5-flash":     ModelRate(0.075, 0.30),
    # Mistral
    "mistral-large":        ModelRate(2.00, 6.00),
    "mistral-small":        ModelRate(0.20, 0.60),
    # Groq (hosted)
    "llama-3.3-70b":        ModelRate(0.59, 0.79),
    "llama-3.1-70b":        ModelRate(0.59, 0.79),
    # DeepSeek
    "deepseek-chat":        ModelRate(0.27, 1.10),
    "deepseek-reasoner":    ModelRate(0.55, 2.19),
}


def lookup_rate(model: str | None) -> ModelRate | None:
    """Find a pricing entry for a model name. Prefix-matches so versioned
    names ("claude-sonnet-4-5-20251022") match their family."""
    if not model:
        return None
    candidate = None
    longest = -1
    for key, rate in PRICING.items():
        if model.startswith(key) and len(key) > longest:
            candidate = rate
            longest = len(key)
    return candidate


def extract_usage(interaction: Interaction) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) for one interaction.

    Handles the three common shapes we see in the wild:
      - OpenAI:    response.body["usage"]["prompt_tokens" | "completion_tokens"]
      - Anthropic: response.body["usage"]["input_tokens" | "output_tokens"]
      - Google:    response.body["usageMetadata"]["promptTokenCount" | "candidatesTokenCount"]
    """
    body = interaction.response.body
    if not isinstance(body, dict):
        return (0, 0)

    usage = body.get("usage")
    if isinstance(usage, dict):
        # Anthropic naming wins if both shapes appear, since it's the more
        # explicit one
        inp = _coerce_int(usage.get("input_tokens", usage.get("prompt_tokens")))
        out = _coerce_int(usage.get("output_tokens", usage.get("completion_tokens")))
        if inp or out:
            return (inp, out)

    meta = body.get("usageMetadata")
    if isinstance(meta, dict):
        inp = _coerce_int(meta.get("promptTokenCount"))
        out = _coerce_int(meta.get("candidatesTokenCount"))
        if inp or out:
            return (inp, out)

    return (0, 0)


def cost_estimate(interaction: Interaction) -> float:
    """USD cost for one interaction. 0.0 when we don't recognise the model
    or the usage object is missing."""
    body = interaction.request.body
    model = body.get("model") if isinstance(body, dict) else None
    rate = lookup_rate(model)
    if rate is None:
        return 0.0
    inp, out = extract_usage(interaction)
    return (inp / 1_000_000.0) * rate.input_per_million + (out / 1_000_000.0) * rate.output_per_million


def aggregate(interactions: list[Interaction]) -> dict[str, Any]:
    """Roll up usage and cost across a list of interactions.

    Returns:
      {
        "input_tokens":  int,
        "output_tokens": int,
        "total_tokens":  int,
        "cost_usd":      float,
        "by_model": {
          "<model>": { input, output, total, cost, count, priced: bool }
        },
        "unpriced_models": [<model>, ...],
      }
    """
    total_in = total_out = 0
    total_cost = 0.0
    by_model: dict[str, dict[str, Any]] = {}
    unpriced: set[str] = set()

    for interaction in interactions:
        body = interaction.request.body
        model = (body.get("model") if isinstance(body, dict) else None) or "(unknown)"
        inp, out = extract_usage(interaction)
        cost = cost_estimate(interaction)
        rate = lookup_rate(model if model != "(unknown)" else None)

        total_in += inp
        total_out += out
        total_cost += cost

        bucket = by_model.setdefault(model, {
            "input": 0, "output": 0, "total": 0,
            "cost": 0.0, "count": 0, "priced": rate is not None,
        })
        bucket["input"] += inp
        bucket["output"] += out
        bucket["total"] += inp + out
        bucket["cost"] += cost
        bucket["count"] += 1

        if rate is None and (inp or out):
            unpriced.add(model)

    return {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "cost_usd": total_cost,
        "by_model": by_model,
        "unpriced_models": sorted(unpriced),
    }


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0
