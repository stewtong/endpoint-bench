from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

#: The only addresses permitted for unattended local execution. Numeric only:
#: a DNS name that resolves to loopback today can be repointed tomorrow, and
#: "localhost" is resolved by the system resolver, not by this guard.
_ALLOWED_LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})

#: Proxy variables stripped from any local execution environment.
_PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

_LOOPBACK_DISCOVERY_VARIABLES = ("no_proxy", "NO_PROXY")


class NetworkGuardError(ValueError):
    """Raised when a destination is not provably local and explicit."""


@dataclass(frozen=True)
class Destination:
    """A parsed destination that has passed local-safety validation."""

    scheme: str
    host: str
    port: int
    path: str

    @property
    def is_loopback(self) -> bool:
        return self.host in _ALLOWED_LOOPBACK_ADDRESSES

    def to_dict(self) -> dict[str, object]:
        return {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "path": self.path,
            "is_loopback": self.is_loopback,
        }


def parse_destination(url: str) -> Destination:
    """Parse a URL strictly, rejecting anything ambiguous or non-numeric.

    Rejected on purpose: credentials in the URL, missing scheme, unsupported
    scheme, missing host, hostname instead of a numeric address, missing port,
    and out-of-range ports. Each of these is a way a request could leave the
    machine or reach an unintended service.
    """
    if not isinstance(url, str) or not url.strip():
        raise NetworkGuardError("destination URL is empty")

    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise NetworkGuardError(f"unsupported scheme `{parts.scheme or '(none)'}` in `{url}`")
    if parts.username or parts.password:
        raise NetworkGuardError(f"destination `{url}` embeds credentials")
    if not parts.hostname:
        raise NetworkGuardError(f"destination `{url}` has no host")

    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError as error:
        raise NetworkGuardError(
            f"destination host `{parts.hostname}` is not a numeric address"
        ) from error
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        raise NetworkGuardError(f"destination `{url}` uses an IPv4-mapped IPv6 address")

    try:
        port = parts.port
    except ValueError as error:
        raise NetworkGuardError(f"destination `{url}` has an invalid port") from error
    if port is None:
        raise NetworkGuardError(f"destination `{url}` must declare an explicit port")
    if not 1 <= port <= 65535:
        raise NetworkGuardError(f"destination `{url}` has an out-of-range port")

    return Destination(
        scheme=parts.scheme, host=str(address), port=port, path=parts.path or "/"
    )


def assert_loopback(url: str) -> Destination:
    """Require a numeric loopback destination on an explicit port."""
    destination = parse_destination(url)
    if not destination.is_loopback:
        raise NetworkGuardError(
            f"destination `{url}` resolves to `{destination.host}`, which is not an allowed "
            f"loopback address ({', '.join(sorted(_ALLOWED_LOOPBACK_ADDRESSES))})"
        )
    return destination


def is_loopback(url: str) -> bool:
    """Non-raising convenience check used for reporting."""
    try:
        return assert_loopback(url).is_loopback
    except NetworkGuardError:
        return False


def local_environment(base: dict[str, str], *, discovery_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    """Build a subprocess environment that cannot route traffic externally.

    Proxy variables are removed rather than overridden, so a stale `ALL_PROXY`
    cannot be picked up by a transport that trusts the environment. `NO_PROXY`
    is then set so a transport that consults it skips proxying local hosts.
    """
    environment = {
        key: value for key, value in base.items() if key not in _PROXY_VARIABLES
    }
    loopback_list = ",".join(sorted(_ALLOWED_LOOPBACK_ADDRESSES))
    environment["NO_PROXY"] = loopback_list
    environment["no_proxy"] = loopback_list
    for prefix in discovery_prefixes:
        environment[prefix] = ",".join(sorted(_ALLOWED_LOOPBACK_ADDRESSES))
    return environment


def assert_no_redirect(status: int) -> None:
    """Reject redirects outright for local conformance work.

    A redirect is the simplest way for an ostensibly local target to move a
    request somewhere else, so it is treated as a protocol violation.
    """
    if 300 <= status < 400:
        raise NetworkGuardError(f"target issued a redirect (status {status}); redirects are not permitted")
