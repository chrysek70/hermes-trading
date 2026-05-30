"""Data adapters: price, on-chain, news, macro.

Each adapter exposes ``async def fetch(...) -> dict`` and stamps a
``schema_version`` field. The loop validates that field; a mismatch
raises ``SchemaError`` and halts the loop so we never trade on data we
no longer understand.
"""
from __future__ import annotations


class SchemaError(Exception):
    """Raised when an adapter returns an unexpected schema_version."""


EXPECTED_SCHEMA = {
    "price": "price.v2",
    "onchain": "onchain.v1",
    "news": "news.v1",
    "macro": "macro.v1",
}


def validate(name: str, payload: dict) -> dict:
    expected = EXPECTED_SCHEMA[name]
    got = payload.get("schema_version")
    if got != expected:
        raise SchemaError(f"{name}: expected schema {expected!r}, got {got!r}")
    return payload
