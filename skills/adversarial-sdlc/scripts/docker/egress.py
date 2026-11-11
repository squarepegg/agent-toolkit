#!/usr/bin/env python3
"""sdlc-egress: the only way out of a role container (spec docker-backend.md, INV-4 and A-4).

Role containers sit on an internal Docker network whose only other member is this proxy. The
proxy also sits on the default bridge, so it can reach the model gateway.

- Origin-form requests (`GET /v1/models HTTP/1.1`) are forwarded to SDLC_EGRESS_UPSTREAM
  (host:port, default host.docker.internal:8080). That is what
  SDLC_GATEWAY_URL=http://sdlc-egress:8080 uses.
- `CONNECT host:port` and absolute-URI requests (`GET http://host:port/x`) are allowed only to
  pairs listed in SDLC_EGRESS_ALLOW (comma or space separated; default: the upstream).
- Anything else gets a 403 and a REFUSED line on stderr.

The security property is about the TCP destination, not HTTP semantics: every upstream
connection is opened to an allowlisted host:port that is fixed before any client byte is
forwarded. After that the proxy pipes raw bytes both ways, so streaming responses (SSE) and
keep-alive work. A later request on the same connection can only reach the same allowlisted
destination. Stdlib only.
"""
import asyncio
import os
import re
import sys
import time

MAX_HEAD = 64 * 1024
HEAD_TIMEOUT = 30
CONNECT_TIMEOUT = 10
LISTEN_PORT = int(os.environ.get("SDLC_EGRESS_PORT", "8080"))
# All interfaces is intended: the proxy runs in its own container, and role containers reach it
# over the internal network. The host never publishes this port.
LISTEN_HOST = os.environ.get("SDLC_EGRESS_BIND", "0.0.0.0")  # noqa: S104


def norm(hp: str) -> str:
    host, _, port = hp.strip().rpartition(":")
    if not host or not port.isdigit():
        raise ValueError(f"bad host:port {hp!r}")
    return f"{host.lower().strip('[]')}:{int(port)}"


UPSTREAM = norm(os.environ.get("SDLC_EGRESS_UPSTREAM", "host.docker.internal:8080"))
ALLOW = {norm(x) for x in re.split(r"[,\s]+", os.environ.get("SDLC_EGRESS_ALLOW", "")) if x} or {UPSTREAM}
if UPSTREAM not in ALLOW:
    ALLOW.add(UPSTREAM)  # the reverse-proxy target is implicitly allowed


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} egress: {msg}", file=sys.stderr, flush=True)


async def refuse(w: asyncio.StreamWriter, peer: str, why: str, code: int = 403) -> None:
    log(f"REFUSED {peer}: {why}")
    reason = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}.get(code, "Error")
    body = f"sdlc-egress: {why}\n".encode()
    w.write(f"HTTP/1.1 {code} {reason}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    try:
        await w.drain()
    finally:
        w.close()


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError) as e:
        del e  # the peer went away: stop piping this direction
    finally:
        try:
            w.write_eof()
        except (OSError, RuntimeError):
            w.close()


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
    pa = cw.get_extra_info("peername") or ("?", "?")
    peer = f"{pa[0]}:{pa[1]}"
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
    except asyncio.LimitOverrunError:
        return await refuse(cw, peer, "request head too large", 400)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
        cw.close()
        return
    try:
        line, _, rest = head.partition(b"\r\n")
        method, target, version = line.decode("latin-1").split(" ")
    except ValueError:
        return await refuse(cw, peer, "malformed request line", 400)

    if method.upper() == "CONNECT":
        try:
            dest = norm(target)
        except ValueError:
            return await refuse(cw, peer, f"bad CONNECT target {target!r}", 400)
        if dest not in ALLOW:
            return await refuse(cw, peer, f"CONNECT {dest} not in allowlist")
        forward = None  # tunnel: reply 200 ourselves, then pipe
    elif m := re.match(r"(?i)^http://([^/?#]+)(.*)$", target):
        hostport = m.group(1).rpartition("@")[2]  # drop userinfo
        try:
            dest = norm(hostport if re.search(r":\d+$", hostport) else hostport + ":80")
        except ValueError:
            return await refuse(cw, peer, f"bad URL {target!r}", 400)
        if dest not in ALLOW:
            return await refuse(cw, peer, f"{method} {dest} not in allowlist")
        forward = f"{method} {m.group(2) or '/'} {version}\r\n".encode("latin-1") + rest
    elif target.startswith("/"):
        dest = UPSTREAM
        forward = head
    else:
        return await refuse(cw, peer, f"unsupported request target {target[:80]!r}", 400)

    host, _, port = dest.rpartition(":")
    try:
        ur, uw = await asyncio.wait_for(asyncio.open_connection(host, int(port)), CONNECT_TIMEOUT)
    except (OSError, asyncio.TimeoutError) as e:
        return await refuse(cw, peer, f"upstream {dest} unreachable: {e}", 502)
    if forward is None:
        cw.write(f"{version} 200 Connection established\r\n\r\n".encode("latin-1"))
        await cw.drain()
    else:
        uw.write(forward)
        await uw.drain()
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))
    for w in (cw, uw):
        w.close()


async def main() -> None:
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT, limit=MAX_HEAD)
    log(f"ready on :{LISTEN_PORT}; upstream {UPSTREAM}; allow {sorted(ALLOW)}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
