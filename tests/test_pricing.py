"""Token + cost extraction across provider response shapes."""
from __future__ import annotations

from cuesheet.cassette import Interaction, RecordedRequest, RecordedResponse
from cuesheet.pricing import (
    aggregate,
    cost_estimate,
    extract_usage,
    lookup_rate,
)


def _interaction(model: str, response_body: dict) -> Interaction:
    return Interaction(
        id="x",
        request=RecordedRequest(
            method="POST", url="https://api.anthropic.com/v1/messages",
            body={"model": model, "messages": []},
        ),
        response=RecordedResponse(status_code=200, body=response_body),
    )


def test_extract_usage_anthropic_shape() -> None:
    i = _interaction("claude-sonnet-4-5", {"usage": {"input_tokens": 120, "output_tokens": 40}})
    assert extract_usage(i) == (120, 40)


def test_extract_usage_openai_shape() -> None:
    i = _interaction("gpt-4o-mini", {"usage": {"prompt_tokens": 200, "completion_tokens": 80}})
    assert extract_usage(i) == (200, 80)


def test_extract_usage_google_shape() -> None:
    i = _interaction("gemini-2.5-flash", {"usageMetadata": {
        "promptTokenCount": 50, "candidatesTokenCount": 10,
    }})
    assert extract_usage(i) == (50, 10)


def test_extract_usage_missing_returns_zeros() -> None:
    assert extract_usage(_interaction("claude-sonnet-4-5", {})) == (0, 0)


def test_lookup_rate_prefix_match() -> None:
    """Versioned names should still hit their family rate."""
    rate_v = lookup_rate("claude-sonnet-4-5-20251022")
    rate_fam = lookup_rate("claude-sonnet-4-5")
    assert rate_v is not None
    assert rate_v == rate_fam


def test_lookup_rate_unknown_returns_none() -> None:
    assert lookup_rate("never-heard-of-this-model") is None
    assert lookup_rate(None) is None


def test_cost_estimate_anthropic() -> None:
    # claude-sonnet-4-5: $3 in / $15 out per million
    i = _interaction("claude-sonnet-4-5", {"usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}})
    assert cost_estimate(i) == 18.0


def test_cost_estimate_zero_for_unknown_model() -> None:
    i = _interaction("custom-model", {"usage": {"input_tokens": 1000, "output_tokens": 1000}})
    assert cost_estimate(i) == 0.0


def test_aggregate_rolls_up_across_models() -> None:
    interactions = [
        _interaction("claude-sonnet-4-5", {"usage": {"input_tokens": 100, "output_tokens": 50}}),
        _interaction("claude-sonnet-4-5", {"usage": {"input_tokens": 200, "output_tokens": 100}}),
        _interaction("gpt-4o-mini",       {"usage": {"prompt_tokens": 500, "completion_tokens": 100}}),
        _interaction("not-priced",        {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}),
    ]
    agg = aggregate(interactions)
    assert agg["input_tokens"] == 100 + 200 + 500 + 10
    assert agg["output_tokens"] == 50 + 100 + 100 + 5
    assert agg["cost_usd"] > 0
    assert "claude-sonnet-4-5" in agg["by_model"]
    assert agg["by_model"]["claude-sonnet-4-5"]["count"] == 2
    assert agg["by_model"]["not-priced"]["priced"] is False
    assert "not-priced" in agg["unpriced_models"]
