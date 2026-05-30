"""Indicator + signal engine — pure functions over an OHLCV DataFrame.

This module is the single source of truth for trading logic. The live loop and
the backtester both call it, so a strategy that backtests one way behaves the
same way live. Nothing here does I/O or holds state beyond what's passed in.

Indicator columns are computed vectorised once; the entry/exit *decisions* are
pure functions of a single row (+ the open position for exits), so the same
code drives a vectorised backtest and a one-bar-at-a-time live loop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.fillna(100.0)  # no losses yet → maximally strong


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False).mean()


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, reset each UTC day."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    day = df.index.floor("D")
    pv = (typical * df["volume"]).groupby(day).cumsum()
    vol = df["volume"].groupby(day).cumsum().replace(0.0, np.nan)
    return (pv / vol).ffill()


def three_bar_play(df: pd.DataFrame, ignition_atr_mult: float = 1.0) -> pd.Series:
    """Detect a bullish 3-bar play firing on the current bar.

    bar -2: ignition — bullish, wide range (>= mult*ATR), closes in the top 40%.
    bar -1: inside bar — contained within the ignition bar's range.
    bar  0: trigger — breaks above the inside bar's high.
    """
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0.0, np.nan)
    a = atr(df)
    ignition = (
        (c.shift(2) > o.shift(2))
        & ((h.shift(2) - l.shift(2)) >= ignition_atr_mult * a.shift(2))
        & ((c.shift(2) - l.shift(2)) >= 0.6 * (h.shift(2) - l.shift(2)))
    )
    inside = (h.shift(1) <= h.shift(2)) & (l.shift(1) >= l.shift(2))
    trigger = h > h.shift(1)
    return (ignition & inside & trigger).fillna(False)


def compute_indicators(df: pd.DataFrame, strategy: dict) -> pd.DataFrame:
    """Return a copy of ``df`` with all indicator columns the strategy needs."""
    regime = strategy["regime"]
    pull = strategy["setups"]["pullback"]
    risk = strategy["risk"]

    out = df.copy()
    out["ema_fast"] = ema(out["close"], int(regime["trend_ema_fast"]))
    out["ema_slow"] = ema(out["close"], int(regime["trend_ema_slow"]))
    out["ema_pull"] = ema(out["close"], int(pull["pullback_ema"]))
    out["rsi"] = rsi(out["close"], int(strategy.get("rsi_period", 14)))
    out["atr"] = atr(out, int(risk["atr_period"]))
    out["vwap"] = session_vwap(out)
    out["three_bar"] = three_bar_play(out, float(strategy["setups"]["breakout"].get("ignition_atr_mult", 1.0)))
    out["three_bar_short"] = three_bar_play_short(
        out, float(strategy.get("shorts", {}).get("breakout", {}).get("ignition_atr_mult", 1.0))
    )
    return out


# ---------------------------------------------------------------------------
# Decisions — pure functions of a single row (+ open position for exits)
# ---------------------------------------------------------------------------


def _bullish_regime(row: pd.Series) -> bool:
    return bool(row["ema_fast"] > row["ema_slow"])


def long_entry(row: pd.Series, strategy: dict) -> str | None:
    """Return the setup name that fires a long here, or None. Regime-gated."""
    if not _bullish_regime(row):
        return None  # never trade against the 50/200 trend

    setups = strategy["setups"]

    pull = setups["pullback"]
    if pull.get("enabled", True):
        near_pull_ema = row["close"] <= row["ema_pull"] * (1.0 + float(pull.get("ema_tol", 0.002)))
        above_slow = row["close"] >= row["ema_slow"]
        if row["rsi"] < float(pull["rsi_threshold"]) and near_pull_ema and above_slow:
            return "pullback"

    brk = setups["breakout"]
    if brk.get("enabled", True):
        above_vwap = (not brk.get("require_above_vwap", True)) or (row["close"] > row["vwap"])
        if bool(row["three_bar"]) and above_vwap:
            return "breakout"

    return None


def initial_stop(row: pd.Series, setup: str, strategy: dict) -> float:
    mult = float(strategy["setups"][setup]["exit"].get("stop_atr_mult", 1.5))
    return float(row["close"] - mult * row["atr"])


def _bearish_regime(row: pd.Series) -> bool:
    return bool(row["ema_fast"] < row["ema_slow"])


def three_bar_play_short(df: pd.DataFrame, ignition_atr_mult: float = 1.0) -> pd.Series:
    """Bearish 3-bar play: down ignition + inside bar + breakdown of inside bar's low."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    a = atr(df)
    ignition = (
        (c.shift(2) < o.shift(2))
        & ((h.shift(2) - l.shift(2)) >= ignition_atr_mult * a.shift(2))
        & ((h.shift(2) - c.shift(2)) >= 0.6 * (h.shift(2) - l.shift(2)))
    )
    inside = (h.shift(1) <= h.shift(2)) & (l.shift(1) >= l.shift(2))
    trigger = l < l.shift(1)
    return (ignition & inside & trigger).fillna(False)


def short_entry(row: pd.Series, strategy: dict) -> str | None:
    """Returns short setup name or None. Inverse-regime gated (EMA50<EMA200)."""
    shorts_cfg = strategy.get("shorts")
    if not shorts_cfg or not shorts_cfg.get("enabled", False):
        return None
    if not _bearish_regime(row):
        return None

    pull = shorts_cfg.get("pullback", {})
    if pull.get("enabled", True):
        near_pull_ema = row["close"] >= row["ema_pull"] * (1.0 - float(pull.get("ema_tol", 0.002)))
        below_slow = row["close"] <= row["ema_slow"]
        if row["rsi"] > float(pull.get("rsi_threshold", 68)) and near_pull_ema and below_slow:
            return "pullback_short"

    brk = shorts_cfg.get("breakout", {})
    if brk.get("enabled", True):
        below_vwap = (not brk.get("require_below_vwap", True)) or (row["close"] < row["vwap"])
        if bool(row.get("three_bar_short", False)) and below_vwap:
            return "breakout_short"

    return None


def initial_stop_short(row: pd.Series, setup: str, strategy: dict) -> float:
    """Stop sits ABOVE entry for a short."""
    key = setup.replace("_short", "")  # 'breakout_short' -> 'breakout'
    mult = float(strategy["shorts"][key]["exit"].get("stop_atr_mult", 1.5))
    return float(row["close"] + mult * row["atr"])


def short_exit(row: pd.Series, position: dict, strategy: dict, bars_held: int) -> str | None:
    """Mirror of long_exit, with inverted comparisons (stop above, trail above)."""
    risk = strategy["risk"]

    if row["high"] >= position["stop"]:
        return "stop"

    if risk.get("regime_flip_exit", True) and row["ema_fast"] > row["ema_slow"]:
        return "regime_flip"

    max_hold = int(risk.get("max_hold_bars", 0) or 0)
    if max_hold and bars_held >= max_hold:
        return "time_stop"

    key = position["setup"].replace("_short", "")
    exit_cfg = strategy["shorts"][key]["exit"]

    if exit_cfg["type"] == "mean_revert":
        if row["rsi"] <= float(exit_cfg.get("target_rsi", 45)):
            return "target_rsi"

    elif exit_cfg["type"] == "trail":
        trail_level = float(row["ema_pull"])
        if trail_level < position["stop"]:
            position["stop"] = trail_level
        if row["close"] > trail_level:
            return "trail_exit"

    return None


def long_exit(row: pd.Series, position: dict, strategy: dict, bars_held: int) -> str | None:
    """Return an exit reason or None. Mutates position['stop'] for trailing."""
    risk = strategy["risk"]

    # Global hard stop (intrabar low pierces the stop).
    if row["low"] <= position["stop"]:
        return "stop"

    # Regime flip — bull trend gone.
    if risk.get("regime_flip_exit", True) and row["ema_fast"] < row["ema_slow"]:
        return "regime_flip"

    # Time stop.
    max_hold = int(risk.get("max_hold_bars", 0) or 0)
    if max_hold and bars_held >= max_hold:
        return "time_stop"

    exit_cfg = strategy["setups"][position["setup"]]["exit"]

    if exit_cfg["type"] == "mean_revert":
        if row["rsi"] >= float(exit_cfg.get("target_rsi", 55)):
            return "target_rsi"

    elif exit_cfg["type"] == "trail":
        # Ratchet the stop up under the trail EMA (never down).
        trail_level = float(row["ema_pull"])
        if trail_level > position["stop"]:
            position["stop"] = trail_level
        if row["close"] < trail_level:
            return "trail_exit"

    return None
