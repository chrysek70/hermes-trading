"""Price adapter — OHLCV via ccxt. Free public endpoint by default.

Set EXCHANGE_API_KEY / EXCHANGE_API_SECRET in .env to use an
authenticated exchange instead of the public feed.
"""
from __future__ import annotations

import asyncio
import os

import ccxt

_API_KEY = os.getenv("EXCHANGE_API_KEY") or None
_API_SECRET = os.getenv("EXCHANGE_API_SECRET") or None

# Explicit override via EXCHANGE_ID; otherwise probe a chain of public
# exchanges and use the first reachable one. Binance geo-blocks many
# regions with HTTP 451, so it sits LAST, not first.
_PREFERRED = os.getenv("EXCHANGE_ID") or None
_FALLBACK_CHAIN = ["kraken", "coinbase", "bitstamp", "kucoin", "binance"]

_working_exchange: str | None = None


def _candidates() -> list[str]:
    return [_PREFERRED] if _PREFERRED else list(_FALLBACK_CHAIN)


def _build(exchange_id: str):
    klass = getattr(ccxt, exchange_id)
    return klass({"apiKey": _API_KEY, "secret": _API_SECRET, "enableRateLimit": True})


def _sync_fetch(symbol: str, timeframe: str, limit: int):
    global _working_exchange
    order = ([_working_exchange] if _working_exchange else []) + [
        e for e in _candidates() if e != _working_exchange
    ]
    errors = []
    for exchange_id in order:
        try:
            ohlcv = _build(exchange_id).fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if ohlcv:
                _working_exchange = exchange_id
                return exchange_id, ohlcv
        except Exception as exc:  # noqa: BLE001 — try the next venue
            errors.append(f"{exchange_id}: {str(exc).splitlines()[0][:100]}")
    _working_exchange = None
    raise RuntimeError(f"no public exchange returned {symbol} data :: " + " | ".join(errors))


async def fetch(symbol: str = "BTC/USDT", timeframe: str = "1m", limit: int = 100) -> dict:
    exchange_id, ohlcv = await asyncio.to_thread(_sync_fetch, symbol, timeframe, limit)
    closes = [row[4] for row in ohlcv]
    return {
        "schema_version": "price.v2",
        "symbol": symbol,
        "timeframe": timeframe,
        "exchange": exchange_id,
        "closes": closes,
        "ohlcv": ohlcv,
        "last": closes[-1] if closes else None,
    }
