"""T018a — cleanup deletes every memory written under the run user_id."""

from __future__ import annotations

from dataclasses import dataclass, field

from openmem.eval.cleanup import cleanup


@dataclass
class _Item:
    id: str


@dataclass
class _Page:
    items: list[_Item]
    next_cursor: str | None = None


class _Stub:
    def __init__(self, ids: list[str]) -> None:
        self._remaining = list(ids)
        self.deleted: list[str] = []

    def list(self, user_id, *, limit=200, cursor=None):
        # Single-page for simplicity
        items = [_Item(i) for i in self._remaining]
        self._remaining = []
        return _Page(items=items)

    def delete(self, mid: str) -> None:
        self.deleted.append(mid)


def test_cleanup_deletes_all_listed_ids() -> None:
    stub = _Stub(["m-1", "m-2", "m-3"])
    n = cleanup(stub, user_id="eval-abc")
    assert n == 3
    assert stub.deleted == ["m-1", "m-2", "m-3"]


def test_cleanup_empty_returns_zero() -> None:
    stub = _Stub([])
    assert cleanup(stub, user_id="eval-x") == 0


def test_cleanup_paginates() -> None:
    pages = [
        _Page(items=[_Item("a"), _Item("b")], next_cursor="c1"),
        _Page(items=[_Item("c")], next_cursor=None),
    ]

    class _Paged:
        def __init__(self):
            self.deleted = []
            self._iter = iter(pages)

        def list(self, *_a, **_k):
            return next(self._iter)

        def delete(self, mid):
            self.deleted.append(mid)

    s = _Paged()
    assert cleanup(s, user_id="eval-x") == 3
    assert s.deleted == ["a", "b", "c"]
