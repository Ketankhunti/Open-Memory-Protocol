"""HTTP routes — mirrors `spec/omp-0.1.openapi.yaml` 1:1.

Paths follow the spec (no `/v1/` prefix). Every handler is `async def`
and obtains the `AsyncMemory` via `Depends(get_memory)`. Body validation
uses the existing `MemoryInput` / `MemoryUpdate` Pydantic models so
JSON Schema stays in lock-step with the spec.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse, Response

from openmem.async_memory import AsyncMemory
from openmem.errors import InvalidRequestError, ProviderError
from openmem.server.deps import (
    extract_user_id_from_header,
    get_memory,
)
from openmem.server.errors import ProviderUnavailable
from openmem.types import MemoryInput, MemoryUpdate

__all__ = ["router", "health_router"]

router = APIRouter()
health_router = APIRouter()


# ---------------------------------------------------------- helpers

def _to_jsonable(obj: Any) -> Any:
    """Pydantic v2 model → dict (JSON-mode for datetimes etc.)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", exclude_none=True)
    return obj


def _check_user_id_in_body(payload: dict[str, Any]) -> str:
    user_id = payload.get("user_id")
    if user_id is None or not str(user_id).strip():
        raise InvalidRequestError("user_id must be a non-empty string")
    return str(user_id)


# ---------------------------------------------------------- /capabilities

@router.get("/capabilities", tags=["meta"])
async def get_capabilities(
    mem: Annotated[AsyncMemory, Depends(get_memory)],
) -> JSONResponse:
    caps = await mem.capabilities()
    return JSONResponse(status_code=200, content=_to_jsonable(caps))


# ---------------------------------------------------------- /memories (POST/GET)

@router.post("/memories", tags=["memories"], status_code=status.HTTP_201_CREATED)
async def add_memory(
    request: Request,
    mem: Annotated[AsyncMemory, Depends(get_memory)],
) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise InvalidRequestError("request body must be a JSON object")
    _check_user_id_in_body(payload)
    # Pydantic validates schema; re-raises as RequestValidationError → 400.
    body = MemoryInput.model_validate(payload)
    record = await mem.add(**body.model_dump(exclude_none=True))
    return JSONResponse(status_code=201, content=_to_jsonable(record))


@router.get("/memories", tags=["memories"])
async def list_memories(
    mem: Annotated[AsyncMemory, Depends(get_memory)],
    user_id: Annotated[str, Query(min_length=1)],
    scope: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    if not user_id.strip():
        raise InvalidRequestError("user_id must be a non-empty string")
    page = await mem.list(
        user_id,
        scope=scope,
        tag=tag,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )
    return JSONResponse(status_code=200, content=_to_jsonable(page))


# ---------------------------------------------------------- /memories/search
# Declared BEFORE /memories/{id} so FastAPI doesn't match `id="search"`.

@router.get("/memories/search", tags=["search"])
async def search_memories(
    mem: Annotated[AsyncMemory, Depends(get_memory)],
    q: Annotated[str, Query(min_length=1)],
    user_id: Annotated[str, Query(min_length=1)],
    scope: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    min_score: Annotated[float | None, Query()] = None,
) -> JSONResponse:
    if not user_id.strip() or not q.strip():
        raise InvalidRequestError("q and user_id must be non-empty")
    results = await mem.search(
        q, user_id, scope=scope, limit=limit, min_score=min_score
    )
    return JSONResponse(
        status_code=200,
        content={"results": [_to_jsonable(r) for r in results]},
    )


# ---------------------------------------------------------- /memories/{id}

@router.get("/memories/{id}", tags=["memories"])
async def get_memory_by_id(
    id: str,
    mem: Annotated[AsyncMemory, Depends(get_memory)],
    _user: Annotated[str, Depends(extract_user_id_from_header)],
) -> JSONResponse:
    record = await mem.get(id)
    return JSONResponse(status_code=200, content=_to_jsonable(record))


@router.patch("/memories/{id}", tags=["memories"])
async def update_memory(
    id: str,
    request: Request,
    mem: Annotated[AsyncMemory, Depends(get_memory)],
) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise InvalidRequestError("request body must be a JSON object")
    # PATCH body may omit user_id (the memory id already scopes the row);
    # but if present, it must be non-empty.
    if "user_id" in payload:
        _check_user_id_in_body(payload)
    body = MemoryUpdate.model_validate(payload)
    record = await mem.update(id, **body.model_dump(exclude_none=True))
    return JSONResponse(status_code=200, content=_to_jsonable(record))


@router.delete("/memories/{id}", tags=["memories"], status_code=204)
async def delete_memory(
    id: str,
    mem: Annotated[AsyncMemory, Depends(get_memory)],
    _user: Annotated[str, Depends(extract_user_id_from_header)],
) -> Response:
    await mem.delete(id)
    return Response(status_code=204)


# ---------------------------------------------------------- /context

@router.post("/context", tags=["search"])
async def get_context(
    request: Request,
    mem: Annotated[AsyncMemory, Depends(get_memory)],
) -> JSONResponse:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise InvalidRequestError("request body must be a JSON object")
    _check_user_id_in_body(payload)
    query = payload.get("query")
    if query is None or not str(query).strip():
        raise InvalidRequestError("query must be a non-empty string")
    block = await mem.context(
        str(query),
        str(payload["user_id"]),
        scope=payload.get("scope"),
        token_budget=int(payload.get("token_budget", 500)),
    )
    return JSONResponse(status_code=200, content=_to_jsonable(block))


# ---------------------------------------------------------- /audit

@router.get("/audit", tags=["meta"])
async def get_audit(
    mem: Annotated[AsyncMemory, Depends(get_memory)],
    user_id: Annotated[str, Query(min_length=1)],
    app: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> JSONResponse:
    if not user_id.strip():
        raise InvalidRequestError("user_id must be a non-empty string")
    entries = await mem.audit(user_id, app=app, since=since, limit=limit)
    return JSONResponse(
        status_code=200,
        content={"entries": [_to_jsonable(e) for e in entries]},
    )


# ---------------------------------------------------------- /healthz

@health_router.get("/healthz", tags=["meta"], include_in_schema=False)
async def health(request: Request) -> JSONResponse:
    """Provider-aware readiness probe.

    * postgres   → acquire+release a pool conn within 1s; 503 on timeout.
    * passthrough → HEAD the upstream within 2s; 503 on non-2xx/3xx.
    * mem0/supermemory/letta → 200 unconditionally (paid endpoint protection).
    """
    import asyncio

    cfg = getattr(request.app.state, "config", None)
    mem: AsyncMemory | None = getattr(request.app.state, "memory", None)
    provider = getattr(cfg, "provider", None) if cfg else None

    if mem is None or cfg is None:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "provider_unavailable",
                                "message": "server not initialized",
                                "type": "provider_error"}},
        )

    try:
        if provider == "postgres":
            adapter = mem._adapter  # noqa: SLF001
            ensure = getattr(adapter, "_ensure_pool", None)
            if ensure is not None:
                pool = await asyncio.wait_for(ensure(), timeout=1.0)
                async with asyncio.timeout(1.0):
                    async with pool.acquire():
                        pass
        elif provider == "passthrough":
            adapter = mem._adapter  # noqa: SLF001
            client = getattr(adapter, "_client", None)
            base_url = getattr(adapter, "base_url", None) or getattr(cfg, "passthrough_base_url", None)
            if client is not None and base_url:
                resp = await asyncio.wait_for(
                    client.head(str(base_url)), timeout=2.0
                )
                if not (200 <= resp.status_code < 400):
                    raise ProviderUnavailable(
                        f"upstream returned {resp.status_code}"
                    )
        # mem0 / supermemory / letta → unconditional 200.
    except (asyncio.TimeoutError, Exception) as exc:
        if isinstance(exc, ProviderUnavailable):
            raise
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "provider_unavailable",
                                "message": "health check failed",
                                "type": "provider_error"}},
        )

    return JSONResponse(status_code=200, content={"status": "ok"})
