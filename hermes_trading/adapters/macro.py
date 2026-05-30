"""Macro adapter — US Dollar Index (DXY) via yfinance (free, public)."""
from __future__ import annotations

import asyncio

import yfinance as yf

_TICKER = "DX-Y.NYB"


def _sync_fetch():
    hist = yf.Ticker(_TICKER).history(period="5d", interval="1d")
    return hist["Close"].dropna().tolist()


async def fetch() -> dict:
    closes = await asyncio.to_thread(_sync_fetch)
    return {
        "schema_version": "macro.v1",
        "source": "yfinance",
        "ticker": _TICKER,
        "dxy_closes": closes,
        "dxy_last": closes[-1] if closes else None,
    }
