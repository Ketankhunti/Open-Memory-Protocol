"""Logging redaction + X-Request-Id echo (T038 / contracts §7)."""

from __future__ import annotations

import logging
import re

import httpx
import pytest


pytestmark = pytest.mark.asyncio


_FORBIDDEN_RE = re.compile(
    r"(super_secret_password|sk-leak-token|u-alice-uid|api_key)",
    re.IGNORECASE,
)
_LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z INFO "
    r"(GET|POST|PATCH|DELETE) \S+ \d{3} \d+ms req=[A-Za-z0-9._\-]{1,64}$"
)


async def test_log_line_format(caplog, client_passthrough):
    caplog.set_level(logging.INFO, logger="openmem.server.access")
    r = await client_passthrough.get("/capabilities")
    assert r.status_code == 200
    lines = [
        record.getMessage()
        for record in caplog.records
        if record.name == "openmem.server.access"
    ]
    assert lines, "no access log line emitted"
    assert any(_LOG_LINE_RE.match(line) for line in lines), lines


async def test_log_redacts_user_id_and_secrets(caplog, client_passthrough):
    """C-LOG-2: user_id, password, token, api_key MUST NOT appear in logs."""
    caplog.set_level(logging.INFO)  # capture all loggers
    r = await client_passthrough.post(
        "/memories",
        json={
            "content": "secret payload super_secret_password",
            "user_id": "u-alice-uid",
        },
        headers={
            "Authorization": "Bearer sk-leak-token",
            "X-Api-Key": "sk-leak-token",
        },
    )
    assert r.status_code in (201, 400)  # body validation either passes or fails
    blob = "\n".join(record.getMessage() for record in caplog.records)
    assert not _FORBIDDEN_RE.search(blob), f"leaked sensitive content: {blob!r}"


async def test_x_request_id_echoed_when_provided(client_passthrough):
    rid = "test-req-id-12345"
    r = await client_passthrough.get(
        "/capabilities", headers={"X-Request-Id": rid}
    )
    assert r.headers.get("x-request-id") == rid


async def test_x_request_id_generated_when_absent(client_passthrough):
    r = await client_passthrough.get("/capabilities")
    rid = r.headers.get("x-request-id")
    assert rid and re.match(r"^[A-Za-z0-9._\-]{1,64}$", rid)


async def test_x_request_id_replaced_when_invalid(client_passthrough):
    """Provided X-Request-Id failing the regex MUST be replaced (not echoed)."""
    bad = "x" * 200  # too long
    r = await client_passthrough.get(
        "/capabilities", headers={"X-Request-Id": bad}
    )
    rid = r.headers.get("x-request-id")
    assert rid and rid != bad
    assert len(rid) <= 64
