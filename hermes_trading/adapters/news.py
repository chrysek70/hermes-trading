"""News / sentiment adapter — Crypto Fear & Greed Index (free, public).

Set NEWS_API_KEY in .env to layer a headline feed on top.
"""
from __future__ import annotations

import os

import httpx

_NEWS_KEY = os.getenv("NEWS_API_KEY") or None


async def fetch() -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get("https://api.alternative.me/fng/", params={"limit": 1})
        r.raise_for_status()
        payload = r.json()
    latest = (payload.get("data") or [{}])[0]
    return {
        "schema_version": "news.v1",
        "source": "alternative.me",
        "fear_greed_value": int(latest["value"]) if latest.get("value") else None,
        "fear_greed_label": latest.get("value_classification"),
    }
