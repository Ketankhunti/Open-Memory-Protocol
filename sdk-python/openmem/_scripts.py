"""Console-script entry points declared in `pyproject.toml`."""

from __future__ import annotations

import sys
from pathlib import Path


def validate_spec() -> int:
    """Validate `spec/omp-0.1.openapi.yaml` against the OpenAPI 3.x meta-schema.

    Used in CI (`.github/workflows/ci.yml`) and locally as
    ``omp-validate-spec``.
    """
    try:
        import yaml  # type: ignore[import-not-found]
        from openapi_spec_validator import validate as validate_openapi  # type: ignore[import-not-found]
    except ImportError as e:
        print(
            f"missing dev deps ({e}); install with: pip install openmem[dev]",
            file=sys.stderr,
        )
        return 2

    spec_path = Path(__file__).resolve().parents[2] / "spec" / "omp-0.1.openapi.yaml"
    if not spec_path.exists():
        print(f"spec not found at {spec_path}", file=sys.stderr)
        return 2
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    validate_openapi(spec)
    print(f"OK: {spec_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(validate_spec())
