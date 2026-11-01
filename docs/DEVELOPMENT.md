# Development guide

## Requirements

- Python 3.13 (`>=3.13,<3.14`)
- [`uv`](https://docs.astral.sh/uv/)
- No API credentials for ordinary development

## Setup

```bash
uv sync --locked
uv run pytest
uv run ruff check .
```

## Offline workflows

```bash
uv run endpoint-bench registry validate
uv run endpoint-bench run --target offline-mock-smoke --profile smoke --dry-run
```

The mock endpoint is intentionally unusable and is never contacted.

## Repository map

- `src/endpoint_bench`: provider-neutral implementation.
- `configs/`: checked-in non-secret example configuration.
- `docs/`: methodology, schema, decisions, and development documentation.
- `examples/synthetic/`: clearly synthetic dry-run artifacts.
- `tests/`: offline tests.

## Change discipline

Do not add a provider branch to core code. Add or extend configuration first. Do not implement live traffic, cache, long-context, or AA features without updating the methodology/schema contracts and fail-closed tests. Do not represent dry-run output as a result.
