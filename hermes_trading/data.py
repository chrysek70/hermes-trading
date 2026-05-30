"""Historical OHLCV loader for backtesting.

Primary source: Binance Vision public data dumps (https://data.binance.vision)
— free, no API key, and reachable where the Binance trading API is geo-blocked,
because it's a static file CDN. Monthly 1-minute klines, cached locally so we
download each month only once.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

from . import STATE_DIR, log

_BASE = "https://data.binance.vision/data/spot/monthly/klines"
_CACHE = STATE_DIR / "data"


def _month_url(symbol: str, month: str) -> str:
    return f"{_BASE}/{symbol}/1m/{symbol}-1m-{month}.zip"


def recent_months(n: int, end: datetime | None = None) -> list[str]:
    """The n most recent *complete* months as 'YYYY-MM', oldest first."""
    end = end or datetime.now(timezone.utc)
    first_of_this_month = end.replace(day=1)
    months = []
    cursor = first_of_this_month - timedelta(days=1)  # step into previous month
    for _ in range(n):
        months.append(cursor.strftime("%Y-%m"))
        cursor = cursor.replace(day=1) - timedelta(days=1)
    return list(reversed(months))


def _load_month(symbol: str, month: str) -> pd.DataFrame:
    _CACHE.mkdir(parents=True, exist_ok=True)
    zpath = _CACHE / f"{symbol}-1m-{month}.zip"
    if not zpath.exists():
        url = _month_url(symbol, month)
        log(f"downloading {symbol} 1m {month} …")
        resp = httpx.get(url, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        zpath.write_bytes(resp.content)

    with zipfile.ZipFile(zpath) as zf:
        with zf.open(zf.namelist()[0]) as fh:
            raw = pd.read_csv(
                io.BytesIO(fh.read()),
                header=None,
                usecols=[0, 1, 2, 3, 4, 5],
                names=["ts", "open", "high", "low", "close", "volume"],
            )
    # Newer dumps sometimes carry a header row — drop any non-numeric ts.
    raw = raw[pd.to_numeric(raw["ts"], errors="coerce").notna()].copy()
    raw["ts"] = raw["ts"].astype("int64")
    return raw


def load_klines(symbol: str = "BTCUSDT", months: list[str] | None = None, n_months: int = 3) -> pd.DataFrame:
    """Load 1m OHLCV for the given months (or the n most recent complete ones).

    Returns a DataFrame indexed by UTC datetime with float columns
    open/high/low/close/volume, de-duplicated and sorted.
    """
    months = months or recent_months(n_months)
    frames = []
    for m in months:
        try:
            frames.append(_load_month(symbol, m))
        except Exception as exc:  # noqa: BLE001 — skip months Binance doesn't have
            log(f"[yellow]skip {symbol} {m}: {exc}[/yellow]")
    if not frames:
        raise RuntimeError(f"no data loaded for {symbol} in {months}")
    data = pd.concat(frames, ignore_index=True)
    # Binance dumps mix epoch units across months (ms in older files, µs in
    # newer ones). Normalise EACH timestamp to milliseconds individually — a
    # single global guess mis-parses the other half (year-58299 dates).
    ts = data["ts"].astype("int64")
    ts = ts.mask(ts > 10**16, ts // 1_000_000)  # nanoseconds  -> ms
    ts = ts.mask(ts > 10**13, ts // 1_000)       # microseconds -> ms
    data["ts"] = ts
    data = data.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        data[col] = data[col].astype(float)
    data.index = pd.to_datetime(data["ts"], unit="ms", utc=True)
    return data[["open", "high", "low", "close", "volume"]]


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample 1m OHLCV up to a higher timeframe (e.g. '15m', '1h', '4h')."""
    if timeframe in ("1m", "1min"):
        return df
    rule = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D"}.get(
        timeframe, timeframe
    )
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule).agg(agg).dropna()
