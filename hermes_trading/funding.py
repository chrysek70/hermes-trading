"""Binance Vision perpetual-futures funding-rate loader.

Mirrors the ``data.py`` pattern (zipped monthly CSVs, local cache,
multi-month concatenation). Funding rate data is published every 8h
on Binance USDS-M perpetuals. We forward-fill to the strategy's
decision timeframe so each bar carries the most recent settled
funding rate.

CDN: data.binance.vision/data/futures/um/monthly/fundingRate/<SYMBOL>/
File format: csv with columns calc_time (ms), funding_interval_hours,
last_funding_rate.

Coverage as of audit (2026-05-30): BTCUSDT and ETHUSDT both have
monthly archives from 2020-01 through current month. Fully covers
the 48mo research window (2022-05 → 2026-04).
"""
from __future__ import annotations

import io
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import STATE_DIR, log

_BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
_CACHE = STATE_DIR / "data" / "funding"


def _url(symbol: str, month: str) -> str:
    return f"{_BASE}/{symbol}/{symbol}-fundingRate-{month}.zip"


def _months(n_months: int, end: pd.Timestamp | None = None) -> list[str]:
    end = end or pd.Timestamp.utcnow().normalize().replace(day=1)
    months = []
    cur = end
    for _ in range(n_months):
        months.append(cur.strftime("%Y-%m"))
        cur = (cur - pd.Timedelta(days=1)).replace(day=1)
    return list(reversed(months))


def _load_month(symbol: str, month: str) -> pd.DataFrame:
    _CACHE.mkdir(parents=True, exist_ok=True)
    zpath = _CACHE / f"{symbol}-fundingRate-{month}.zip"
    if not zpath.exists():
        url = _url(symbol, month)
        log(f"downloading {symbol} funding {month} …")
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:  # noqa: BLE001
            raise RuntimeError(f"funding archive not available for {symbol} {month}: HTTP {exc.code}") from exc
        zpath.write_bytes(data)
    with zipfile.ZipFile(zpath) as z:
        name = z.namelist()[0]
        with z.open(name) as fh:
            df = pd.read_csv(fh)
    # parse ts
    df["ts"] = pd.to_datetime(df["calc_time"], unit="ms", utc=True)
    df = df.set_index("ts").sort_index()
    df = df[["funding_interval_hours", "last_funding_rate"]]
    df = df.rename(columns={"last_funding_rate": "funding_rate"})
    return df


def load_funding(symbol: str = "BTCUSDT", n_months: int = 48,
                 end: pd.Timestamp | None = None,
                 months: list[str] | None = None) -> pd.DataFrame:
    months = months or _months(n_months, end)
    # The current calendar month's archive is published by Binance Vision
    # only AFTER the month closes. Until then a 404 is the expected
    # absence of data, not an error. Downgrade those specific 404s from
    # yellow ("warning") to gray ("informational") so the operator can
    # tell them apart from real failures (HTTP 5xx, network errors,
    # historical archives missing, etc.).
    current_month_str = pd.Timestamp.utcnow().strftime("%Y-%m")
    frames = []
    for m in months:
        try:
            frames.append(_load_month(symbol, m))
        except RuntimeError as exc:  # noqa: BLE001
            msg = str(exc)
            is_current_month_404 = (
                m == current_month_str
                and "HTTP 404" in msg
            )
            if is_current_month_404:
                log(f"[gray]Funding archive for {symbol} {m} not yet "
                    f"published (expected for current month).[/gray]")
            else:
                log(f"[yellow]skip {symbol} funding {m}: {exc}[/yellow]")
    if not frames:
        raise RuntimeError(f"no funding loaded for {symbol} in {months}")
    df = pd.concat(frames).sort_index()
    # de-duplicate any timestamp overlaps at month boundaries
    df = df[~df.index.duplicated(keep="last")]
    return df


def align_to_index(funding_df: pd.DataFrame, target_index: pd.DatetimeIndex,
                   column: str = "funding_rate") -> pd.Series:
    """Forward-fill the funding rate (announced every 8h) onto a finer-grained
    target index (e.g. 4h bars). Each target bar carries the most recently
    settled funding rate. Causal — no future funding is ever assigned to a
    past bar."""
    s = funding_df[column].copy()
    s.index = s.index.tz_convert("UTC") if s.index.tzinfo else s.index.tz_localize("UTC")
    target_index = target_index.tz_convert("UTC") if target_index.tzinfo else target_index.tz_localize("UTC")
    # combine indices, forward-fill, then slice to target
    combined = s.reindex(s.index.union(target_index)).sort_index().ffill()
    return combined.reindex(target_index)


def rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Causal rolling percentile rank in [0, 100]. The value at bar t is the
    percentile of ``series[t]`` within the trailing ``window`` observations
    (inclusive of t). No future bars used.

    Implementation note: pandas' ``rolling.rank(pct=True)`` is fast and
    correct for this purpose.
    """
    return series.rolling(window, min_periods=max(20, window // 4)).rank(pct=True) * 100.0
