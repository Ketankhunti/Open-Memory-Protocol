"""Allow `python -m openmem.eval` invocation."""

from openmem.eval.cli import main

if __name__ == "__main__":  # pragma: no cover - thin shim
    raise SystemExit(main())
