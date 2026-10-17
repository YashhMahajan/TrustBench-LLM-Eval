"""Protections at the edge of the API: body size, security headers and the address of the client.

These are plain ASGI middleware. They act on the raw request, before any route code runs.
"""

import ipaddress
import json

MAX_BODY_BYTES = 5 * 1024 * 1024


class _TooLarge(Exception):
    pass


class BodyLimit:
    """Refuse a body above the limit. It counts the bytes while they arrive, so a client that sends no
    Content-Length (chunked upload) cannot fill our memory. A check on the header alone is not enough."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.max_bytes:
            return await self._refuse(send)
        received, started = 0, False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._refuse(send)

    async def _refuse(self, send):
        body = json.dumps({"error": f"The request is larger than {self.max_bytes // 1024 // 1024} MB. Send smaller batches."}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


class SecurityHeaders:
    """Headers that make a browser safer when it shows an API answer. The JSON API never needs scripts,
    frames or caching, so we forbid them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        https = dict(scope["headers"]).get(b"x-forwarded-proto", b"") == b"https"

        async def with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                add = [(b"x-content-type-options", b"nosniff"), (b"x-frame-options", b"DENY"), (b"referrer-policy", b"no-referrer")]
                if path.startswith("/v1/"):                      # The /docs page loads scripts, so it keeps its own rules.
                    add += [(b"cache-control", b"no-store"), (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")]
                if https:
                    add.append((b"strict-transport-security", b"max-age=31536000"))
                have = {k.lower() for k, _ in headers}
                message = {**message, "headers": headers + [(k, v) for k, v in add if k not in have]}
            await send(message)

        await self.app(scope, receive, with_headers)


def parse_trusted(value: str | None) -> list:
    """TRUSTBENCH_TRUSTED_PROXIES: addresses or networks, separated by commas. Empty: trust nobody."""
    nets = []
    for part in (value or "").split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


def _in(ip: str, nets: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


def client_ip(peer: str | None, forwarded_for: str | None, trusted: list) -> str:
    """The address of the real client.

    X-Forwarded-For is a header that anyone can write. We believe it only when the connection comes from a proxy
    that we trust, and then we read it from the right: each trusted proxy adds the address it saw to the end,
    so the first address from the right that is not a proxy is the client. Entries to its left are the client's own claims."""
    peer = peer or "unknown"
    if not trusted or not forwarded_for or not _in(peer, trusted):
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _in(hop, trusted):
            return hop
    return hops[0] if hops else peer
