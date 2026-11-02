from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

REDACTED = "<redacted>"

#: Environment variable names whose values are treated as secrets even when the
#: caller forgets to name them explicitly.
SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def redact_secrets(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of each secret with a placeholder.

    Longer secrets are replaced first so a secret that contains another secret
    does not leave a partial value behind.
    """
    ordered = sorted({value for value in secrets if value}, key=len, reverse=True)
    for secret in ordered:
        text = text.replace(secret, REDACTED)
    return text


def redact_url(url: str) -> str:
    """Strip credentials and query parameters from a URL before logging it."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def collect_secret_values(environment: dict[str, str]) -> list[str]:
    """Best-effort discovery of secret values in an environment mapping."""
    values: list[str] = []
    for name, value in environment.items():
        if not value:
            continue
        upper = name.upper()
        if any(marker in upper for marker in SENSITIVE_ENV_MARKERS):
            values.append(value)
    return values
