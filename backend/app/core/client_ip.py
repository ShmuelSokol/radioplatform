"""The real address of the caller, for rate limits, listener stats and the activity log.

Railway's proxy appends the address it saw to X-Forwarded-For, so the right end of
the header is the only part the proxy vouches for. Anything to the left was sent by
the caller and can be made up. The address is therefore the rightmost hop that is
not an internal one (Railway's own network adds private hops after the edge).
Without the header: X-Real-IP, then the socket address.
"""
import ipaddress

from starlette.requests import HTTPConnection

_INTERNAL_V4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")]
_INTERNAL_V6 = ipaddress.ip_network("fc00::/7")


def _internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return any(ip in n for n in _INTERNAL_V4)
    return ip in _INTERNAL_V6


def client_ip(request: HTTPConnection) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        for hop in reversed(hops):
            try:
                ip = ipaddress.ip_address(hop)
            except ValueError:
                continue
            if not _internal(ip):
                return hop[:50]
        if hops:
            return hops[-1][:50]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()[:50]
    return request.client.host if request.client else None


def rate_limit_key(request: HTTPConnection) -> str:
    """Key for slowapi limiters: one bucket per real caller, not per proxy."""
    return client_ip(request) or "unknown"
