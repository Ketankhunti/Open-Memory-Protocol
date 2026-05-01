"""M2.1 / Phase 6 — opaque cursor codec round-trip & rejection (T046c).

Covers data-model.md §2a: the cursor is base64-urlsafe(json({"page": N}))
with strict pre-flight validation. Defends against cursor-injection
attacks (e.g. crafted `page=99999999` to exhaust upstream quota).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from openmem.adapters._cursor import (
    MAX_PAGE_NUMBER,
    decode_cursor,
    encode_cursor,
)
from openmem.adapters.passthrough import PassthroughAdapter
from openmem.errors import InvalidRequestError
from openmem.types import Capabilities, CapabilityFeatures


@pytest.mark.parametrize("n", [1, 2, 100, 9999, MAX_PAGE_NUMBER])
def test_round_trip_for_legit_pages(n: int) -> None:
    assert decode_cursor(encode_cursor(n)) == n


def test_page_above_hard_cap_decode_rejected() -> None:
    """Encoded page > MAX_PAGE_NUMBER must be rejected at decode time."""
    crafted = encode_cursor(MAX_PAGE_NUMBER + 1)
    with pytest.raises(InvalidRequestError):
        decode_cursor(crafted)


def test_empty_cursor_decodes_to_page_one() -> None:
    assert decode_cursor(None) == 1
    assert decode_cursor("") == 1


@pytest.mark.parametrize(
    "bad",
    [
        "not-base64!!!",
        "<" * 100,  # garbage
        "A" * 257,  # over length cap
        # base64 of non-int page
        base64.urlsafe_b64encode(json.dumps({"page": "two"}).encode())
        .decode()
        .rstrip("="),
        # base64 of negative page
        base64.urlsafe_b64encode(json.dumps({"page": -1}).encode())
        .decode()
        .rstrip("="),
        # base64 of page over hard cap
        base64.urlsafe_b64encode(
            json.dumps({"page": MAX_PAGE_NUMBER + 1}).encode()
        )
        .decode()
        .rstrip("="),
        # base64 of wrong shape
        base64.urlsafe_b64encode(json.dumps({"foo": 1}).encode())
        .decode()
        .rstrip("="),
        # base64 of non-dict
        base64.urlsafe_b64encode(json.dumps([1, 2]).encode())
        .decode()
        .rstrip("="),
        # base64 of bool (bool-as-int trap)
        base64.urlsafe_b64encode(json.dumps({"page": True}).encode())
        .decode()
        .rstrip("="),
    ],
)
def test_decode_rejects_malformed(bad: str) -> None:
    with pytest.raises(InvalidRequestError):
        decode_cursor(bad)


def test_passthrough_list_rejects_oversized_cursor_before_http() -> None:
    """Boundary check: malformed cursor must NEVER reach the upstream."""
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    adapter = PassthroughAdapter(
        base_url="http://omp.test",
        capabilities=Capabilities(
            omp_version="0.1",
            provider="passthrough",
            verbs=["list"],
            features=CapabilityFeatures(),
        ),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(InvalidRequestError):
        adapter.list("u1", cursor="A" * 1000)
    assert calls == [], "passthrough must not issue HTTP for malformed cursor"
