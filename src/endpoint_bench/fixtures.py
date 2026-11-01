from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass

#: Deliberately neutral vocabulary. Sentiment, topic, and style are irrelevant to
#: protocol conformance and must never be read as answer-quality signal.
_WORDS = (
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
)

#: Rough characters-per-token for the builtin tiktoken o200k_base encoding on
#: this vocabulary. Only used to size fixtures approximately; reported token
#: counts always come from an actual tokenizer or provider usage.
_APPROX_CHARS_PER_TOKEN = 4


def approximate_tokens(text: str) -> int:
    """Approximate token count for fixture sizing only.

    Never report this as an observed token count. AIPerf's own count, or the
    provider's `usage` field, is the authoritative number.
    """
    return max(1, len(text) // _APPROX_CHARS_PER_TOKEN)


@dataclass(frozen=True)
class FixturePrompt:
    index: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "text": self.text,
            "approx_tokens": approximate_tokens(self.text),
        }


@dataclass(frozen=True)
class PromptFixture:
    """A deterministic prompt set with stable identity.

    The same seed, version, and target size always produce the same texts and the
    same hash, so a manifest hash genuinely identifies the workload.
    """

    name: str
    version: str
    seed: int
    target_tokens: int
    prompts: tuple[FixturePrompt, ...]

    @property
    def sha256(self) -> str:
        material = json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "seed": self.seed,
                "target_tokens": self.target_tokens,
                "prompts": [prompt.text for prompt in self.prompts],
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()


def generate_fixture(
    *,
    count: int,
    target_tokens: int,
    seed: int = 0,
    name: str = "synthetic-conformance",
    version: str = "1.0",
) -> PromptFixture:
    """Build a deterministic prompt set of approximately `target_tokens` each.

    Each prompt is prefixed with a stable index marker so a trial can be traced
    back to its fixture entry without depending on request ordering.
    """
    if count <= 0:
        raise ValueError("fixture `count` must be positive")
    if target_tokens <= 0:
        raise ValueError("fixture `target_tokens` must be positive")

    generator = random.Random(seed)
    prompts: list[FixturePrompt] = []
    for index in range(count):
        marker = f"[fixture={name} v{version} index={index}] "
        body: list[str] = [marker]
        while approximate_tokens(" ".join(body)) < target_tokens:
            body.append(generator.choice(_WORDS))
        prompts.append(FixturePrompt(index=index, text=" ".join(body)))

    return PromptFixture(
        name=name,
        version=version,
        seed=seed,
        target_tokens=target_tokens,
        prompts=tuple(prompts),
    )
