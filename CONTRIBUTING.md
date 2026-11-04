# Contributing

Thanks for considering a contribution. This project favors small, verified changes over speculative framework work.

## Setup

1. Fork or branch from the latest default branch.
2. Install Python 3.13 and [uv](https://docs.astral.sh/uv/).
3. Run:

```bash
uv sync --locked
uv run pytest
uv run ruff check .
```

All ordinary development and CI tests must work offline without credentials.

## License

Contributions are accepted under the repository's [MIT License](LICENSE).

## Proposing a provider

Open a GitHub issue describing:

- endpoint URL and API protocol;
- exact served model ID and revision, if known;
- region/deployment identifier;
- authentication environment-variable name (never a value);
- advertised context/output limits and pricing source/date;
- protocol features such as streaming, tools, JSON/schema output, reasoning, and cache controls.

Start with a non-secret registry configuration. Do not hard-code provider-specific behavior in `src/endpoint_bench`. A provider adapter belongs in a narrowly scoped module only after a validated configuration contract proves it is necessary.

## Adding a profile

Profiles must be declarative, versioned, format-neutral, and hashable. Include request count, target input size, maximum output size, concurrency, streaming mode, timeout/retry policy when implemented, and a description that distinguishes screening from qualification. Update methodology and schema documentation when the profile introduces a new observable behavior.

## Adding a probe

Probes should be deterministic, provider-neutral, fail visibly, and classify protocol failure separately from model choice. Include mocked response fixtures for success, malformed output, truncation, interruption, missing usage, timeout, and HTTP 429/5xx paths. Never execute text returned by an endpoint.

## Fixtures

Use small synthetic fixtures under `tests/fixtures/` or `examples/synthetic/`. Label every synthetic value. Do not commit real credentials, private traces, restricted AA records, provider account identifiers, or unredacted prompts/responses.

## Result schemas

Update `docs/RESULTS_SCHEMA.md` for any normalized field. Use additive nullable fields when possible, bump the schema version for semantic changes, and explain migration/compatibility in the documentation. Unknown values must be `null` or explicit `unknown`; not zero.

## Issues and pull requests

Use the GitHub issue templates. A pull request should include:

- what changed and why;
- test evidence with exact commands;
- any schema/methodology impact;
- security/privacy impact;
- remaining limitations.

Do not include benchmark claims unless the benchmark actually ran. Clearly label dry-run, synthetic, and provisional evidence.
