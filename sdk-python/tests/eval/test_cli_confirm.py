"""T021 — cost confirmation helper."""

from __future__ import annotations

import pytest

from openmem.eval.confirm import confirm_or_exit


def test_proceeds_silently_below_threshold() -> None:
    confirm_or_exit(0.10, threshold=1.00, yes=False, isatty=True)
    confirm_or_exit(1.00, threshold=1.00, yes=False, isatty=False)


def test_yes_flag_bypasses_prompt_above_threshold() -> None:
    confirm_or_exit(5.00, threshold=1.00, yes=True, isatty=False)


def test_above_threshold_no_tty_no_yes_exits_3() -> None:
    with pytest.raises(SystemExit) as exc:
        confirm_or_exit(5.00, threshold=1.00, yes=False, isatty=False)
    assert exc.value.code == 3


def test_above_threshold_tty_y_proceeds() -> None:
    confirm_or_exit(
        5.00, threshold=1.00, yes=False, isatty=True, prompt=lambda _msg: "y"
    )


def test_above_threshold_tty_n_exits_3() -> None:
    with pytest.raises(SystemExit) as exc:
        confirm_or_exit(
            5.00, threshold=1.00, yes=False, isatty=True, prompt=lambda _msg: "n"
        )
    assert exc.value.code == 3


def test_above_threshold_tty_eof_exits_3() -> None:
    def _eof(_msg: str) -> str:
        raise EOFError

    with pytest.raises(SystemExit) as exc:
        confirm_or_exit(5.00, threshold=1.00, yes=False, isatty=True, prompt=_eof)
    assert exc.value.code == 3
