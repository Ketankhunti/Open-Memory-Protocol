"""Principle I: pydantic types in `openmem.types` mirror the OpenAPI schema.

Loads `spec/omp-0.1.openapi.yaml` and walks every entry in
``components/schemas``; for each one it asserts a corresponding pydantic
model exists and every required OpenAPI property maps to a field that is
also required (no default) on the pydantic side.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_SPEC_PATH = (
    Path(__file__).resolve().parents[2] / "spec" / "omp-0.1.openapi.yaml"
)

# OpenAPI schema name → pydantic class name in `openmem.types`
_NAME_OVERRIDES = {
    "MemoryInput": "MemoryInput",
    "MemoryUpdate": "MemoryUpdate",
    "Memory": "Memory",
    "MemoryPage": "MemoryPage",
    "MemorySource": "MemorySource",
    "SearchResult": "SearchResult",
    "ContextBlock": "ContextBlock",
    "Capabilities": "Capabilities",
    "CapabilityFeatures": "CapabilityFeatures",
    "CapabilityLimits": "CapabilityLimits",
    "AuditEntry": "AuditEntry",
}


def _load_schemas() -> dict:
    if not _SPEC_PATH.exists():
        pytest.skip(f"spec not found at {_SPEC_PATH}")
    spec = yaml.safe_load(_SPEC_PATH.read_text(encoding="utf-8"))
    return spec.get("components", {}).get("schemas", {}) or {}


@pytest.mark.parametrize("schema_name", sorted(_load_schemas().keys()))
def test_pydantic_model_exists_for_schema(schema_name):
    types = importlib.import_module("openmem.types")
    py_name = _NAME_OVERRIDES.get(schema_name, schema_name)
    assert hasattr(types, py_name), (
        f"OpenAPI schema {schema_name!r} has no pydantic model "
        f"openmem.types.{py_name}"
    )


@pytest.mark.parametrize("schema_name", sorted(_load_schemas().keys()))
def test_required_fields_match_openapi(schema_name):
    schemas = _load_schemas()
    schema = schemas[schema_name]
    py_name = _NAME_OVERRIDES.get(schema_name, schema_name)
    types = importlib.import_module("openmem.types")
    if not hasattr(types, py_name):
        pytest.skip(f"covered by existence test")
    model = getattr(types, py_name)
    required = set(schema.get("required", []) or [])
    for prop in required:
        assert prop in model.model_fields, (
            f"{py_name}.{prop} required by OpenAPI but missing"
        )
        field = model.model_fields[prop]
        assert field.is_required(), (
            f"{py_name}.{prop} required by OpenAPI but optional in pydantic"
        )


# ---------------------------------------------------------------------------
# M2.1: Memory.status enum round-trip (FR-122)
# ---------------------------------------------------------------------------


def test_memory_status_enum_present_in_openapi():
    schemas = _load_schemas()
    mem = schemas.get("Memory", {})
    props = mem.get("properties", {})
    status = props.get("status")
    assert status, "Memory.status missing from OpenAPI (FR-122)"
    assert status.get("enum") == [
        "queued",
        "indexing",
        "done",
        "failed",
    ], "Memory.status enum drift vs spec"
    assert "status" not in mem.get("required", []), (
        "Memory.status MUST be optional (additive, back-compat per Principle III)"
    )


def test_memory_status_round_trips_through_pydantic():
    from openmem.types import Memory

    raw = {
        "id": "mem_x",
        "content": "hello",
        "user_id": "u1",
        "created_at": "2026-04-28T00:00:00Z",
        "status": "queued",
    }
    m = Memory.model_validate(raw)
    assert m.status == "queued"
    # Round-trip back to JSON-serialisable dict
    dumped = m.model_dump(mode="json", exclude_none=True)
    assert dumped["status"] == "queued"

    # Absent status → None (legacy clients unaffected)
    m2 = Memory.model_validate({**raw, "status": None})
    assert m2.status is None


def test_error_code_enum_includes_ingestion_timeout():
    schemas = _load_schemas()
    err = schemas.get("Error", {})
    code_enum = (
        err.get("properties", {})
        .get("error", {})
        .get("properties", {})
        .get("code", {})
        .get("enum", [])
    )
    assert "ingestion_timeout" in code_enum, (
        "Error.code.ingestion_timeout missing from OpenAPI (M2.1 / FR-105 / EC-101)"
    )


def test_ingestion_timeout_resolves_to_provider_error():
    from openmem.errors import OMPError, ProviderError

    err = OMPError.from_response_dict(
        {
            "error": {
                "code": "ingestion_timeout",
                "message": "budget elapsed",
                "type": "provider_error",
                "provider": "mem0",
            }
        }
    )
    assert isinstance(err, ProviderError)
    assert err.code == "ingestion_timeout"
    assert err.provider == "mem0"
