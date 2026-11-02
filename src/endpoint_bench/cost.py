from __future__ import annotations

from typing import Any

from .profiles import Profile
from .registry import Deployment

#: Multiplier applied to the nominal token exposure. Covers chat-template
#: wrapping, tokenizer disagreement, and reasoning tokens that the nominal output
#: limit may not capture. It is a bound, not a prediction.
DEFAULT_SAFETY_MULTIPLIER = 1.5


def estimate_cost_usd(
    deployment: Deployment,
    profile: Profile,
    *,
    safety_multiplier: float = DEFAULT_SAFETY_MULTIPLIER,
) -> float | None:
    """Conservative upper-bound cost estimate, or None when pricing is unknown.

    Unknown pricing is `None`, never zero. A caller that needs a bound must treat
    `None` as blocking, not as free.
    """
    prompt_rate = deployment.pricing.get("prompt_per_million_tokens")
    completion_rate = deployment.pricing.get("completion_per_million_tokens")
    if not isinstance(prompt_rate, (int, float)) or isinstance(prompt_rate, bool):
        return None
    if not isinstance(completion_rate, (int, float)) or isinstance(completion_rate, bool):
        return None

    input_tokens = profile.request_count * profile.input_tokens
    output_tokens = profile.request_count * profile.max_output_tokens
    nominal = (
        input_tokens / 1_000_000 * prompt_rate + output_tokens / 1_000_000 * completion_rate
    )
    return nominal * safety_multiplier


def cost_preflight(
    deployment: Deployment,
    profile: Profile,
    *,
    ceiling_usd: float | None = None,
) -> dict[str, Any]:
    """Record the cost bound and its provenance for a run manifest."""
    estimate = estimate_cost_usd(deployment, profile)
    return {
        "estimate_usd": estimate,
        "ceiling_usd": ceiling_usd,
        "within_ceiling": (
            None if estimate is None or ceiling_usd is None else estimate <= ceiling_usd
        ),
        "safety_multiplier": DEFAULT_SAFETY_MULTIPLIER,
        "pricing_verified": deployment.pricing.get("verified") is True,
        "pricing_source": deployment.pricing.get("source"),
        "pricing_effective_date": deployment.pricing.get("effective_date"),
        "prompt_per_million_tokens": deployment.pricing.get("prompt_per_million_tokens"),
        "completion_per_million_tokens": deployment.pricing.get("completion_per_million_tokens"),
        "planned_input_tokens": profile.request_count * profile.input_tokens,
        "planned_output_tokens": profile.request_count * profile.max_output_tokens,
        "note": (
            "A client-side ceiling is not a hard provider billing limit. "
            "Provider-side enforcement is required before claiming one."
        ),
    }
