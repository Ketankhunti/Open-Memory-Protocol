"""OpenAPI conformance (T036 / FR-015 / contracts §8).

For each successful response from the live in-process server, we
validate its JSON body against the matching response schema in
`spec/omp-0.1.openapi.yaml`. The spec is the canonical source of truth
(per the contract preamble), and any drift between server output and
spec MUST fail this test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
import yaml


pytestmark = pytest.mark.asyncio


_SPEC_PATH = (
    Path(__file__).resolve().parents[3] / "spec" / "omp-0.1.openapi.yaml"
)


@pytest.fixture(scope="session")
def openapi_spec() -> dict[str, Any]:
    with open(_SPEC_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _resolve_schema_for(
    spec: dict[str, Any], operation_id: str, status_code: str
) -> dict[str, Any]:
    """Walk the spec by operationId to find the schema for a status code."""
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            if op.get("operationId") != operation_id:
                continue
            resp = op["responses"].get(status_code)
            if resp is None:
                raise KeyError(
                    f"{operation_id}: no response for {status_code}"
                )
            content = resp.get("content", {}).get("application/json")
            if content is None or "schema" not in content:
                # 204 / empty response — no schema to validate.
                return {}
            return content["schema"]
    raise KeyError(f"operationId not found in spec: {operation_id}")


def _make_validator(spec: dict[str, Any], schema: dict[str, Any]):
    """Build a Draft 2020-12 validator with the spec's components resolvable."""
    base = {
        "$id": "https://omp.local/openapi#",
        "components": spec["components"],
    }
    # `$ref: "#/components/schemas/..."` will resolve via the registry.
    registry_uri = "https://omp.local/openapi"
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    resource = Resource(contents=base, specification=DRAFT202012)
    registry = Registry().with_resource(uri=registry_uri, resource=resource)
    # Rewrite the schema to absolute refs.
    rewritten = _rewrite_refs(schema, registry_uri)
    return jsonschema.Draft202012Validator(rewritten, registry=registry)


def _rewrite_refs(node: Any, base_uri: str) -> Any:
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str) and v.startswith("#/"):
                out[k] = f"{base_uri}{v}"
            else:
                out[k] = _rewrite_refs(v, base_uri)
        return out
    if isinstance(node, list):
        return [_rewrite_refs(x, base_uri) for x in node]
    return node


# ---------------------------------------------------------- per-route validators


async def _validate_route(
    client: httpx.AsyncClient,
    spec: dict[str, Any],
    *,
    method: str,
    url: str,
    operation_id: str,
    status_code: int,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
):
    r = await client.request(method, url, json=json_body, headers=headers)
    assert r.status_code == status_code, (operation_id, r.status_code, r.text)
    if r.status_code != 204:
        assert r.headers["content-type"].startswith("application/json"), (
            f"{operation_id} returned non-JSON content-type: "
            f"{r.headers.get('content-type')!r}"
        )
        # X-Request-Id always echoed.
        assert r.headers.get("x-request-id"), operation_id

        schema = _resolve_schema_for(spec, operation_id, str(status_code))
        if schema:
            validator = _make_validator(spec, schema)
            errors = sorted(
                validator.iter_errors(r.json()),
                key=lambda e: e.path,  # type: ignore[arg-type]
            )
            assert not errors, (
                f"{operation_id} response failed schema:\n"
                + "\n".join(f"  - {e.message}" for e in errors)
            )


async def test_openapi_conformance_all_routes(client_passthrough, openapi_spec):
    c = client_passthrough

    # --- getCapabilities (200)
    await _validate_route(
        c, openapi_spec,
        method="GET", url="/capabilities",
        operation_id="getCapabilities", status_code=200,
    )

    # --- addMemory (201) — establish an id we can reuse below.
    r = await c.post(
        "/memories",
        json={"content": "spec conformance probe", "user_id": "u-conformance"},
    )
    assert r.status_code == 201
    mid = r.json()["id"]
    schema = _resolve_schema_for(openapi_spec, "addMemory", "201")
    validator = _make_validator(openapi_spec, schema)
    errs = list(validator.iter_errors(r.json()))
    assert not errs, [e.message for e in errs]

    # --- getMemory (200)
    await _validate_route(
        c, openapi_spec,
        method="GET", url=f"/memories/{mid}",
        operation_id="getMemory", status_code=200,
        headers={"X-User-Id": "u-conformance"},
    )

    # --- listMemories (200)
    await _validate_route(
        c, openapi_spec,
        method="GET", url="/memories?user_id=u-conformance",
        operation_id="listMemories", status_code=200,
    )

    # --- updateMemory (200)
    r = await c.patch(f"/memories/{mid}", json={"content": "v2 content"})
    assert r.status_code == 200
    schema = _resolve_schema_for(openapi_spec, "updateMemory", "200")
    validator = _make_validator(openapi_spec, schema)
    errs = list(validator.iter_errors(r.json()))
    assert not errs, [e.message for e in errs]

    # --- searchMemories (200)
    await _validate_route(
        c, openapi_spec,
        method="GET",
        url="/memories/search?q=conformance&user_id=u-conformance",
        operation_id="searchMemories", status_code=200,
    )

    # --- getContext (200)
    await _validate_route(
        c, openapi_spec,
        method="POST", url="/context",
        json_body={"query": "conformance", "user_id": "u-conformance"},
        operation_id="getContext", status_code=200,
    )

    # --- deleteMemory (204)
    r = await c.delete(
        f"/memories/{mid}", headers={"X-User-Id": "u-conformance"}
    )
    assert r.status_code == 204

    # --- 404 NotFound envelope (use a definitely-missing id).
    r = await c.get("/memories/no-such-id-xyz", headers={"X-User-Id": "u-x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"

    # --- 400 InvalidRequest envelope.
    r = await c.post("/memories", json={"content": "x"})  # no user_id
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
