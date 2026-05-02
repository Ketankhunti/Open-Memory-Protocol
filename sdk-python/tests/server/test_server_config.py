"""Tests for OmpServerConfig invariants (CFG-INV-1..4 + sanity)."""

from __future__ import annotations

import pytest

from openmem.server.config import OmpServerConfig


# --------------------------------------------------------------- happy paths

def test_postgres_minimal_ok() -> None:
    cfg = OmpServerConfig(
        provider="postgres",
        postgres_url="postgresql://u:p@h:5432/db",
    )
    assert cfg.provider == "postgres"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8080
    assert cfg.max_request_bytes == 1024 * 1024
    assert cfg.cors_origins == ()


def test_passthrough_minimal_ok() -> None:
    cfg = OmpServerConfig(
        provider="passthrough",
        passthrough_base_url="http://localhost:9000",
    )
    assert cfg.adapter_kwargs() == {"base_url": "http://localhost:9000"}


@pytest.mark.parametrize(
    "provider,key_attr",
    [
        ("mem0", "mem0_api_key"),
        ("supermemory", "supermemory_api_key"),
        ("letta", "letta_api_key"),
    ],
)
def test_provider_with_api_key_ok(provider: str, key_attr: str) -> None:
    cfg = OmpServerConfig(provider=provider, **{key_attr: "secret-123"})
    assert cfg.adapter_kwargs() == {"api_key": "secret-123"}


# --------------------------------------------------------------- invariants

def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError, match="provider must be"):
        OmpServerConfig(provider="bogus")  # type: ignore[arg-type]


@pytest.mark.parametrize("port", [0, -1, 65536, 100_000])
def test_invalid_port_rejected(port: int) -> None:
    with pytest.raises(ValueError, match="port must be"):
        OmpServerConfig(
            provider="postgres",
            postgres_url="postgresql://x",
            port=port,
        )


@pytest.mark.parametrize(
    "size", [0, 1023, 100 * 1024 * 1024 + 1, 500 * 1024 * 1024]
)
def test_invalid_max_request_bytes_rejected(size: int) -> None:
    with pytest.raises(ValueError, match="max_request_bytes must be"):
        OmpServerConfig(
            provider="postgres",
            postgres_url="postgresql://x",
            max_request_bytes=size,
        )


def test_postgres_requires_url() -> None:
    with pytest.raises(ValueError, match="postgres_url"):
        OmpServerConfig(provider="postgres")


def test_postgres_blank_url_rejected() -> None:
    with pytest.raises(ValueError, match="postgres_url"):
        OmpServerConfig(provider="postgres", postgres_url="   ")


def test_passthrough_requires_base_url() -> None:
    with pytest.raises(ValueError, match="passthrough_base_url"):
        OmpServerConfig(provider="passthrough")


@pytest.mark.parametrize(
    "provider,key_attr",
    [
        ("mem0", "mem0_api_key"),
        ("supermemory", "supermemory_api_key"),
        ("letta", "letta_api_key"),
    ],
)
def test_managed_provider_requires_api_key(
    provider: str, key_attr: str
) -> None:
    with pytest.raises(ValueError, match=f"{key_attr}"):
        OmpServerConfig(provider=provider)
    with pytest.raises(ValueError, match=f"{key_attr}"):
        OmpServerConfig(provider=provider, **{key_attr: "  "})


def test_blank_cors_origin_rejected() -> None:
    with pytest.raises(ValueError, match="cors_origins"):
        OmpServerConfig(
            provider="postgres",
            postgres_url="postgresql://x",
            cors_origins=("https://ok",  ""),
        )


def test_frozen() -> None:
    import dataclasses

    cfg = OmpServerConfig(provider="postgres", postgres_url="postgresql://x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.port = 9999  # type: ignore[misc]


def test_cors_list_normalized_to_tuple() -> None:
    cfg = OmpServerConfig(
        provider="postgres",
        postgres_url="postgresql://x",
        cors_origins=("https://a", "https://b"),
    )
    assert isinstance(cfg.cors_origins, tuple)
    assert cfg.cors_origins == ("https://a", "https://b")
