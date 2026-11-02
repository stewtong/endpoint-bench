from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class ProfileError(ValueError):
    """Raised when a benchmark profile is invalid."""


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    schema_version: str
    source_path: Path
    source_sha256: str
    request_count: int
    input_tokens: int
    max_output_tokens: int
    concurrency: int
    streaming: bool
    timeout_s: float = 60.0
    token_tolerance_pct: float = 10.0
    qualification: dict[str, object] | None = None


def load_profile(path: Path | str, expected_name: str | None = None) -> Profile:
    source_path = Path(path)
    if not source_path.is_file():
        raise ProfileError(f"profile file does not exist: {source_path}")
    try:
        document = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileError(f"could not read profile `{source_path}`: {error}") from error
    if not isinstance(document, dict):
        raise ProfileError("profile root must be a JSON object")

    def positive(key: str) -> int:
        value = document.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ProfileError(f"profile `{key}` must be a positive integer")
        return value

    request_count = positive("request_count")
    input_tokens = positive("input_tokens")
    max_output_tokens = positive("max_output_tokens")
    concurrency = positive("concurrency")
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise ProfileError("profile `schema_version` must be a non-empty string")
    name = document.get("name")
    description = document.get("description")
    if not isinstance(name, str) or not name:
        raise ProfileError("profile `name` must be a non-empty string")
    if not isinstance(description, str) or not description:
        raise ProfileError("profile `description` must be a non-empty string")
    if expected_name is not None and name != expected_name:
        raise ProfileError(
            f"profile name `{name}` does not match requested profile `{expected_name}`"
        )
    if concurrency > request_count:
        raise ProfileError("profile `concurrency` cannot exceed `request_count`")

    timeout_s = document.get("timeout_s", 60.0)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise ProfileError("profile `timeout_s` must be a positive number")

    tolerance = document.get("token_tolerance_pct", 10.0)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or tolerance < 0:
        raise ProfileError("profile `token_tolerance_pct` must be a non-negative number")

    qualification = document.get("qualification")
    if qualification is not None and not isinstance(qualification, dict):
        raise ProfileError("profile `qualification` must be an object or absent")

    return Profile(
        name=name,
        description=description,
        schema_version=str(document["schema_version"]),
        source_path=source_path,
        source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
        request_count=request_count,
        input_tokens=input_tokens,
        max_output_tokens=max_output_tokens,
        concurrency=concurrency,
        streaming=bool(document.get("streaming", False)),
        timeout_s=float(timeout_s),
        token_tolerance_pct=float(tolerance),
        qualification=dict(qualification) if qualification else None,
    )
