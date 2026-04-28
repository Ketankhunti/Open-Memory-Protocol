"""Embedder protocol + reference implementations.

Per Constitution Principle IV (Provider Neutrality), the SDK ships a
deterministic ``FakeEmbedder`` so the contract suite and the offline demo
run with no third-party accounts. ``OpenAIEmbedder`` is provided as the
production default but lazy-imports ``openai`` so the package installs
without it.

The ``model`` attribute is consumed by the Postgres adapter to enforce
cross-embedding-model search safety (FR-014: hard-fail when the query
embedder's model differs from the indexed memories' model).
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Protocol every embedder must satisfy."""

    dim: int
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text. Each vector has length ``dim``."""
        ...


# ---------------------------------------------------------------------------
# FakeEmbedder
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic, offline embedder for tests + demos.

    Hashes the input with SHA-256 expanded to ``dim`` floats in [-1, 1],
    then L2-normalizes. Same text always produces the same vector, so
    cosine similarity is meaningful: identical strings score 1.0,
    unrelated strings score near 0.

    Default ``dim=64`` keeps test DDL fast while staying high-dim enough
    that random collisions are negligible.
    """

    def __init__(self, dim: int = 64, model: str = "fake-sha256-64") -> None:
        if dim < 8 or dim > 1024:
            raise ValueError("FakeEmbedder dim must be between 8 and 1024")
        self.dim = dim
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        # Generate enough bytes by hashing repeatedly with a counter
        out: list[float] = []
        counter = 0
        # 8 bytes per float
        bytes_needed = self.dim * 8
        while len(out) * 8 < bytes_needed:
            h = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
            for i in range(0, len(h), 8):
                if len(out) >= self.dim:
                    break
                chunk = h[i : i + 8]
                # Map 8 bytes to a signed int → float in [-1, 1]
                n = int.from_bytes(chunk, "big", signed=False)
                out.append((n / (2**64 - 1)) * 2 - 1)
            counter += 1
        # L2 normalize so cosine similarity makes sense
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]


# ---------------------------------------------------------------------------
# OpenAIEmbedder (lazy)
# ---------------------------------------------------------------------------


class OpenAIEmbedder:
    """Production embedder backed by the OpenAI embeddings API.

    Requires the ``openai`` extra: ``pip install openmem[openai]``.
    Lazy-imports so the core package installs without ``openai``.
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        # text-embedding-3-small => 1536; -3-large => 3072
        self.dim = 3072 if "large" in model else 1536
        if client is not None:
            self._client = client
            return
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as e:  # pragma: no cover - environment-specific
            raise ImportError(
                "OpenAIEmbedder requires the 'openai' extra. "
                "Install with: pip install openmem[openai]"
            ) from e
        self._client = OpenAI(api_key=api_key) if api_key else OpenAI()

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


__all__ = ["Embedder", "FakeEmbedder", "OpenAIEmbedder"]
