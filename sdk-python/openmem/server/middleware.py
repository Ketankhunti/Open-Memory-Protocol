"""Server middlewares (PR-B / contracts §4 + §7).

Two middlewares:

* :class:`MaxRequestSizeMiddleware` — C-SIZ-1/2: rejects oversized bodies
  with 413 BEFORE Pydantic validation runs.
* :class:`LoggingMiddleware` — C-LOG-1..4: emits one INFO line per request
  with a fixed format, redacts user_id/secrets, and echoes X-Request-Id.

Both middlewares are pure ASGI (no Starlette `BaseHTTPMiddleware`) so
they cooperate cleanly with `http.disconnect` cancellation (FR-018).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openmem.server.errors import make_envelope

__all__ = ["MaxRequestSizeMiddleware", "LoggingMiddleware"]

_LOG = logging.getLogger("openmem.server.access")

# C-LOG-3: validate provided X-Request-Id; reject anything funky.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

# C-LOG-2: forbidden header names (case-insensitive contains check).
_FORBIDDEN_KEY_RE = re.compile(
    r"(?i)password|secret|token|api[_-]?key|authorization"
)


class MaxRequestSizeMiddleware:
    """Pure-ASGI middleware that enforces request body size limits."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # C-SIZ-1: fast path — trust Content-Length if present.
        headers = dict(scope.get("headers") or [])
        cl_raw = headers.get(b"content-length")
        if cl_raw is not None:
            try:
                if int(cl_raw) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass  # malformed; fall through to bounded read

        # C-SIZ-2: bounded chunked read (handles chunked-transfer or
        # missing Content-Length).
        seen = 0
        buffered: list[Message] = []
        more = True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":
                # http.disconnect or anything else — pass through verbatim.
                buffered.append(msg)
                more = False
                break
            body = msg.get("body") or b""
            seen += len(body)
            if seen > self.max_bytes:
                await self._reject(send)
                return
            buffered.append(msg)
            more = bool(msg.get("more_body"))

        # Replay receive() with the buffered messages.
        iter_msgs = iter(buffered)

        async def replay() -> Message:
            try:
                return next(iter_msgs)
            except StopIteration:
                return await receive()

        await self.app(scope, replay, send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(
            make_envelope(
                "payload_too_large",
                f"request body exceeds {self.max_bytes} bytes",
                type_="invalid",
            )
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


class LoggingMiddleware:
    """Pure-ASGI access logger.

    Emits exactly one INFO line per HTTP request, format:

        <iso8601> <level> <method> <path> <status> <latency_ms>ms req=<request_id>

    Echoes `X-Request-Id` on every response (newly generated if absent /
    invalid).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = self._resolve_request_id(scope)
        start = time.perf_counter()
        status_holder = {"code": 500}

        async def send_with_id(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                status_holder["code"] = int(msg.get("status", 500))
                # Inject/overwrite X-Request-Id on the response.
                headers = [
                    (k, v)
                    for (k, v) in (msg.get("headers") or [])
                    if k.lower() != b"x-request-id"
                ]
                headers.append(
                    (b"x-request-id", request_id.encode("ascii"))
                )
                msg = {**msg, "headers": headers}
            await send(msg)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            method = self._safe(scope.get("method", ""))
            path = self._safe(scope.get("path", ""))
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            # C-LOG-1/2: fixed-format line; no body, no user_id, no headers.
            _LOG.info(
                "%s INFO %s %s %d %dms req=%s",
                ts,
                method,
                path,
                status_holder["code"],
                latency_ms,
                request_id,
            )

    @staticmethod
    def _safe(value: str | bytes) -> str:
        if isinstance(value, bytes):
            value = value.decode("latin-1", errors="replace")
        # C-LOG-2: strip anything that smells like a secret key.
        if _FORBIDDEN_KEY_RE.search(value):
            return "<redacted>"
        return value

    @staticmethod
    def _resolve_request_id(scope: Scope) -> str:
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-request-id":
                candidate = value.decode("latin-1", errors="replace")
                if _REQUEST_ID_RE.match(candidate):
                    return candidate
                break
        return uuid.uuid4().hex
