"""Turn an HTTP request into the key a limit is counted against.

Framework-free: anything with ``headers``, ``client`` and ``state`` attributes works,
including Starlette/FastAPI ``Request`` objects.
"""

import hashlib
import ipaddress
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol


class ClientLike(Protocol):
    """The connected peer."""

    @property
    def host(self) -> str:
        """Peer IP address."""
        ...


class RequestLike(Protocol):
    """The parts of a request that key extractors read."""

    @property
    def headers(self) -> Mapping[str, str]:
        """Request headers (case-insensitive lookup expected)."""
        ...

    @property
    def client(self) -> ClientLike | None:
        """The TCP peer, if known."""
        ...

    @property
    def state(self) -> Any:
        """Per-request state set by earlier middleware (e.g. the authenticated user)."""
        ...


KeyExtractor = Callable[[RequestLike], str | None]
"""Returns a key such as ``"ip:203.0.113.7"``, or ``None`` if the request has none."""

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class ClientIP:
    """Key by client IP address.

    ``X-Forwarded-For`` is ignored unless the TCP peer is one of ``trusted_proxies``,
    because any client can send that header. When the peer is trusted, the header is read
    right to left and the first address that is not itself a trusted proxy is the client:
    entries to its left were written by the client and could be forged.

    Args:
        trusted_proxies: IPs or CIDR ranges of your load balancers, e.g. ``["10.0.0.0/8"]``.
    """

    def __init__(self, trusted_proxies: Iterable[str] = ()) -> None:
        self._trusted: tuple[_Network, ...] = tuple(
            ipaddress.ip_network(p, strict=False) for p in trusted_proxies
        )

    def __call__(self, request: RequestLike) -> str | None:
        """Return ``"ip:<address>"`` or ``None`` if the peer is unknown."""
        if request.client is None:
            return None
        peer = request.client.host
        forwarded = request.headers.get("x-forwarded-for")
        if not forwarded or not self._is_trusted(peer):
            return f"ip:{peer}"
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        for hop in reversed(hops):
            if not _is_ip(hop):
                return f"ip:{peer}"  # a malformed entry means the chain cannot be trusted
            if not self._is_trusted(hop):
                return f"ip:{hop}"
        return f"ip:{hops[0]}" if hops else f"ip:{peer}"

    def _is_trusted(self, address: str) -> bool:
        if not _is_ip(address):
            return False
        ip = ipaddress.ip_address(address)
        return any(ip in network for network in self._trusted)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def api_key(header: str = "X-API-Key") -> KeyExtractor:
    """Key by API key header. The key is hashed so secrets never reach Redis or logs."""

    def extract(request: RequestLike) -> str | None:
        value = request.headers.get(header)
        if not value:
            return None
        return f"key:{hashlib.sha256(value.encode()).hexdigest()[:32]}"

    return extract


def user_id(attribute: str = "user_id") -> KeyExtractor:
    """Key by an authenticated user id stored on ``request.state`` by auth middleware.

    Reading the id from request state (not from a header) means only your own auth code can
    set it.
    """

    def extract(request: RequestLike) -> str | None:
        value = getattr(request.state, attribute, None)
        return None if value is None else f"user:{value}"

    return extract
