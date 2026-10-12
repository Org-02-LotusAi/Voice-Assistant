"""Client IP for throttling and logs: the socket peer, or X-Forwarded-For only when the peer is a trusted proxy."""

from __future__ import annotations

import ipaddress
from typing import Any

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(raw: str) -> tuple[Network, ...]:
    """"10.0.0.0/8, 172.18.0.2" -> networks; raise ValueError on a bad entry."""
    return tuple(ipaddress.ip_network(p.strip(), strict=False) for p in raw.split(",") if p.strip())


def _addr(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted(value: str, nets: tuple[Network, ...]) -> bool:
    a = _addr(value)
    return a is not None and any(a in n for n in nets)


def client_ip(request: Any, trusted_proxies: tuple[Network, ...] = ()) -> str:
    """`request.client.host`, unless that peer is in `trusted_proxies`: then the rightmost X-Forwarded-For entry that is not
    itself a trusted proxy (entries further left can be forged by the client, so they are never preferred)."""
    peer = request.client.host if request.client else "unknown"
    if not trusted_proxies or not _trusted(peer, trusted_proxies):
        return peer
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    for hop in reversed(forwarded):
        if _addr(hop) is None:
            return peer  # malformed header: do not guess
        if not _trusted(hop, trusted_proxies):
            return hop
    return peer
