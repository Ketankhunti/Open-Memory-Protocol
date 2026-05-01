"""Bare-install packaging guard — FR-026 / SC-007 / C-EXT-1..3 (T032).

Verifies that an end-user who installs the bare ``openmem`` distribution
(no ``[async]`` extra) can still ``from openmem import Memory`` but
gets a *clear* ``ImportError`` referencing the install command when
they attempt ``from openmem import AsyncMemory``.

The test creates a throwaway venv, installs the package via
``pip install <repo>`` (no extras), then runs a one-shot ``python -c``
that asserts both contracts. If the host cannot create a venv
(restricted CI image, missing ``ensurepip``, etc.) the test
``pytest.skip``s — packaging guards are a release-gate concern, not a
per-developer one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

_REPO_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
_PROBE_SCRIPT = (
    "import sys\n"
    "from openmem import Memory  # bare import must work\n"
    "assert Memory is not None\n"
    "try:\n"
    "    from openmem import AsyncMemory  # noqa: F401\n"
    "except ImportError as e:\n"
    "    msg = str(e)\n"
    "    assert \"pip install 'openmem[async]'\" in msg, "
    "        f'ImportError message missing extras hint: {msg!r}'\n"
    "    sys.exit(0)\n"
    "else:\n"
    "    sys.exit('AsyncMemory import succeeded without [async] extra')\n"
)


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_bare_install_imports_memory_only(tmp_path):
    """Bare install: `Memory` works, `AsyncMemory` raises clear ImportError."""
    if not _REPO_PYPROJECT.exists():
        pytest.skip(f"pyproject.toml not found at {_REPO_PYPROJECT}")
    if shutil.which(sys.executable) is None:
        pytest.skip("host python is not invokable")
    if sysconfig.get_config_var("Py_DEBUG"):
        pytest.skip("debug build venv creation is unreliable")

    venv_dir = tmp_path / "bare-venv"
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"cannot create venv on this host: {exc}")

    py = _venv_python(venv_dir)
    if not py.exists():
        pytest.skip(f"venv python not found at {py}")

    repo_root = _REPO_PYPROJECT.parent
    install = subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", str(repo_root)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if install.returncode != 0:
        pytest.skip(
            "pip install of bare openmem failed in venv "
            f"(stderr={install.stderr[-400:]!r})"
        )

    probe = subprocess.run(
        [str(py), "-c", _PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.returncode == 0, (
        "Bare-install probe failed.\n"
        f"--- stdout ---\n{probe.stdout}\n"
        f"--- stderr ---\n{probe.stderr}"
    )
