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
