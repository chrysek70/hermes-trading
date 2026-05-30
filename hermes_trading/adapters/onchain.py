"""On-chain adapter — BTC network fees via mempool.space (free, public).

Set GLASSNODE_API_KEY in .env to pull richer on-chain metrics instead.
"""
from __future__ import annotations

import os

import httpx

_GLASSNODE_KEY = os.getenv("GLASSNODE_API_KEY") or None


async def fetch() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        if _GLASSNODE_KEY:
            r = await client.get(
                "https://api.glassnode.com/v1/metrics/fees/fee_ratio_multiple",
                params={"a": "BTC", "api_key": _GLASSNODE_KEY},
            )
            r.raise_for_status()
            return {"schema_version": "onchain.v1", "source": "glassnode", "data": r.json()}

        r = await client.get("https://mempool.space/api/v1/fees/recommended")
        r.raise_for_status()
        fees = r.json()
    return {
        "schema_version": "onchain.v1",
        "source": "mempool.space",
        "fast_fee": fees.get("fastestFee"),
        "data": fees,
    }
