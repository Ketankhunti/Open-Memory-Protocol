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

    Tokenizes the input on non-alphanumeric boundaries (lowercased), maps
    each unique token to a deterministic SHA-256-derived unit vector of
    size ``dim``, sums them, and L2-normalizes. This makes cosine
    similarity reflect **token overlap**: identical strings score 1.0,
    strings sharing relevant keywords (e.g. ``"user prefers"`` ↔
    ``"the user prefer"``) score noticeably higher than unrelated ones.

    The previous implementation hashed the whole string and produced
    semantically blind vectors (every distinct text was effectively
    orthogonal), which made relevance-style contract tests probabilistic.

    Default ``dim=64`` keeps test DDL fast while staying high-dim enough
    that random hash collisions between unrelated tokens are negligible.
    """

    def __init__(self, dim: int = 64, model: str = "fake-sha256-64") -> None:
        if dim < 8 or dim > 1024:
            raise ValueError("FakeEmbedder dim must be between 8 and 1024")
        self.dim = dim
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        # Tiny stopword list so common function words don't drown out
        # content tokens in the bag-of-words cosine. Kept intentionally
        # small — this is an offline test embedder, not a production NLP
        # pipeline.
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "but", "by", "do",
            "does", "for", "from", "had", "has", "have", "i", "if", "in",
            "is", "it", "its", "my", "of", "on", "or", "so", "that", "the",
            "this", "to", "was", "were", "which", "with",
        }
        out: list[str] = []
        buf: list[str] = []
        for ch in text.lower():
            if ch.isalnum():
                buf.append(ch)
            elif buf:
                tok = "".join(buf)
                if tok not in stopwords:
                    out.append(tok)
                buf = []
        if buf:
            tok = "".join(buf)
            if tok not in stopwords:
                out.append(tok)
        return out

    def _token_vector(self, token: str) -> list[float]:
        # Generate ``dim`` floats in [-1, 1] from SHA-256(token), then
        # L2-normalize so each token contributes a unit vector.
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            h = hashlib.sha256(f"{counter}:{token}".encode("utf-8")).digest()
            for i in range(0, len(h), 8):
                if len(out) >= self.dim:
                    break
                n = int.from_bytes(h[i : i + 8], "big", signed=False)
                out.append((n / (2**64 - 1)) * 2 - 1)
            counter += 1
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]

    def _embed_one(self, text: str) -> list[float]:
        tokens = self._tokenize(text)
        if not tokens:
            # Fall back to hashing the empty/symbol-only string itself so
            # the embedder remains total.
            return self._token_vector(text)
        acc = [0.0] * self.dim
        for tok in tokens:
            v = self._token_vector(tok)
            for i in range(self.dim):
                acc[i] += v[i]
        norm = math.sqrt(sum(x * x for x in acc)) or 1.0
        return [x / norm for x in acc]


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
