"""Contract tests: lifecycle (add → get → update → delete → list)."""

from __future__ import annotations

import pytest

from openmem.errors import NotFoundError
from openmem.types import MemoryInput, MemoryUpdate


def _input(content: str, **kw) -> MemoryInput:
    return MemoryInput(content=content, user_id="u1", **kw)


def test_add_then_get_roundtrip(adapter):
    m = adapter.add(_input("hello world", scope="greetings", tags=["t1"]))
    assert m.id.startswith("mem_")
    fetched = adapter.get(m.id)
    assert fetched.id == m.id
    assert fetched.content == "hello world"
    assert fetched.user_id == "u1"
    assert fetched.scope == "greetings"
    assert fetched.tags == ["t1"]
    assert fetched.created_at is not None


def test_update_supersedes_appends_to_history(adapter):
    a = adapter.add(_input("v1"))
    updated = adapter.update(a.id, MemoryUpdate(content="v2", supersedes=[a.id]))
    assert updated.content == "v2"
    assert a.id in (updated.supersedes or [])
    assert updated.updated_at is not None


def test_delete_then_get_raises_not_found(adapter):
    m = adapter.add(_input("ephemeral"))
    adapter.delete(m.id)
    with pytest.raises(NotFoundError):
        adapter.get(m.id)


def test_list_filters_by_scope_glob_and_tag(adapter):
    adapter.add(_input("c1", scope="coding/preferences", tags=["nodejs"]))
    adapter.add(_input("c2", scope="coding/tools", tags=["python"]))
    adapter.add(_input("h1", scope="health/sleep", tags=["nodejs"]))

    coding = adapter.list("u1", scope="coding/*")
    assert len(coding.items) == 2
    assert {m.scope for m in coding.items} == {
        "coding/preferences",
        "coding/tools",
    }

    nodejs = adapter.list("u1", tag="nodejs")
    assert len(nodejs.items) == 2
    assert {m.scope for m in nodejs.items} == {
        "coding/preferences",
        "health/sleep",
    }


def test_list_pagination_returns_next_cursor_and_terminates(adapter):
    """FR-005 + EC-007: keyset pagination yields exactly the right pages."""
    for i in range(75):
        adapter.add(_input(f"m{i:02d}"))

    page1 = adapter.list("u1", limit=50)
    assert len(page1.items) == 50
    assert page1.next_cursor is not None

    page2 = adapter.list("u1", limit=50, cursor=page1.next_cursor)
    assert len(page2.items) == 25
    assert page2.next_cursor is None

    # No id appears on both pages
    seen = {m.id for m in page1.items}
    assert not seen.intersection({m.id for m in page2.items})


def test_list_on_empty_db_returns_empty_page(adapter):
    """EC-001: list on empty DB returns empty page with next_cursor=None."""
    page = adapter.list("nobody")
    assert page.items == []
    assert page.next_cursor is None
