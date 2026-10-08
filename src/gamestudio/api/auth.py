"""The token that protects the local API, and the host it answers.

The server only listens on loopback, but "loopback" does not mean "private":
any process on the machine can reach it, and any page open in a browser can
send it requests. The terminal routes launch arbitrary programs, so implicit
trust is not enough to cover them.

Two locks, for two different purposes:

The **token** is the real door. It lives in `<data>/run/api-token` with mode
0600 and travels in a header (`Authorization: Bearer`, or
`X-Gamestudio-Token`) or a URL parameter -- `<img>` tags and `EventSource`
cannot set headers. It is drawn once per installation and then **reused**: a
studio restart disconnects nobody, and the Rust shell does not have to chase a
value that changes under it. To renew it, delete the file.

The **host** is the second, against DNS rebinding: a domain name resolving to
127.0.0.1 would pass a remote page off as loopback. The server therefore only
answers a `Host` that really is loopback.

What stays open is the static front: the page must load before it knows the
token, and it triggers no action. All of `/api/` is guarded.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from ..config import settings

logger = logging.getLogger("gamestudio.api")

# A token set by the environment wins over the file: it keeps the tests
# hermetic, and lets a front-end developer fix one without reading the disk. It
# is never written: the Rust shell reads the variable itself, environment then
# `.env`, in the same order (`read_token` in `app/src-tauri/src/server.rs`).
TOKEN_ENV = "GAMESTUDIO_TOKEN"
TOKEN_HEADER = "x-gamestudio-token"
TOKEN_QUERY = "token"

# Only `/api/` is guarded. The front mounted at the root must stay reachable:
# the page loads before it knows the token.
PROTECTED_PREFIX = "/api/"

# A host name outside this list betrays a DNS rebinding.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_cached: str | None = None


def token_path() -> Path:
    return settings().run_dir / "api-token"


def expected() -> str:
    """The expected token, written on first need."""
    global _cached
    forced = os.environ.get(TOKEN_ENV, "").strip()
    if forced:
        return forced
    if _cached is None:
        _cached = _read() or _mint()
    return _cached


def _read() -> str:
    try:
        return token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _mint() -> str:
    """Draw a token and write it readable by its owner only.

    The file is created with its permissions and written through the same
    descriptor: creating it first and restricting it afterwards would leave a
    window during which a permissive `umask` made it world-readable.
    """
    value = secrets.token_urlsafe(32)
    path = token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
            "w", encoding="utf-8")
        with handle:
            handle.write(value)
    except OSError as error:
        # A read-only disk must not stop the server: the token holds for this
        # session. The shell only reads it from disk (or `GAMESTUDIO_TOKEN`):
        # without the file, it refuses to start and says why.
        logger.warning("token not written to %s (%s): it holds for this "
                       "session only", path, error)
    return value


def reset() -> None:
    """Forget the computed token: tests change `data_dir` along the way."""
    global _cached
    _cached = None


def publish() -> str:
    """Write the token before the server opens its port.

    The Rust shell reads it from disk as soon as the port answers: the file
    must exist before, or it would read a missing file or a previous run's.
    """
    return expected()


# ---------------------------------------------------------------------- read


def _header(scope: dict[str, Any], name: str) -> str:
    for key, value in scope.get("headers") or ():
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return ""


def _host_allowed(host: str) -> bool:
    if not host:
        # A request without `Host` (HTTP/1.0, a local non-browser client): DNS
        # rebinding goes through this very header, so its absence is not
        # suspicious.
        return True
    name = host.strip().lower()
    if name.startswith("["):
        name = name.partition("]")[0].lstrip("[")
    else:
        name = name.rpartition(":")[0] or name
    return name in LOOPBACK_HOSTS


def _presented(scope: dict[str, Any]) -> str:
    """The presented token, however it is presented."""
    direct = _header(scope, TOKEN_HEADER)
    if direct.strip():
        return direct.strip()
    scheme, _, value = _header(scope, "authorization").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    for candidate in query.get(TOKEN_QUERY, []):
        if candidate.strip():
            return candidate.strip()
    return ""


def _authorized(scope: dict[str, Any]) -> bool:
    presented = _presented(scope)
    return bool(presented) and hmac.compare_digest(presented, expected())


# ---------------------------------------------------------------------- guard


class Guard:
    """The wall in front of the application.

    Written as raw ASGI rather than `BaseHTTPMiddleware`: the latter only sees
    HTTP requests, and the studio's most sensitive door -- a terminal's stream
    -- is a WebSocket. One wall for both, so one place to check that the rule
    holds.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket") or not self._guarded(scope):
            await self.app(scope, receive, send)
            return
        if not _host_allowed(_header(scope, "host")):
            await self._refuse(scope, send, kind, "non-local host refused")
            return
        if not _authorized(scope):
            await self._refuse(scope, send, kind, "API token missing or invalid")
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _guarded(scope: dict[str, Any]) -> bool:
        if scope.get("method") == "OPTIONS":
            # A CORS preflight never carries an `Authorization` header:
            # requiring it would close the whole API in development, where the
            # front comes from Vite and the API from elsewhere. An `OPTIONS`
            # request triggers no action.
            return False
        return str(scope.get("path", "")).startswith(PROTECTED_PREFIX)

    @staticmethod
    async def _refuse(scope: dict[str, Any], send: Any, kind: str,
                      detail: str) -> None:
        if kind == "websocket":
            # Refuse before `accept`: the handshake fails, and the client sees a
            # refusal rather than a stream that cut off by itself. The reason
            # says which of the two checks failed, otherwise "the stream does
            # not open" stays unexplained in the field.
            await send({"type": "websocket.close", "code": 1008, "reason": detail})
            return
        await send({
            "type": "http.response.start",
            "status": 403,
            # 403 and not 401: `401` triggers the browser's authentication
            # dialog, which has no business here.
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": json.dumps({"detail": detail}).encode("utf-8"),
        })
