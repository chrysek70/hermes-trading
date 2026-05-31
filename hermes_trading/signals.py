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


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> tuple[pd.Series, pd.Series]:
    """SuperTrend indicator — causal.

    Returns (line, direction) where direction is +1 for uptrend, -1 for
    downtrend. At bar i the line and direction are determined from the
    close at i and the *prior bar's* finalised bands — no lookahead.
    Entry decisions should compare current direction against the
    ``direction.shift(1)`` to identify flip bars.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    hl2 = (high + low) / 2.0

    # Wilder ATR (same convention as signals.atr)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1.0 / period, adjust=False).mean()

    basic_upper = (hl2 + multiplier * atr_).to_numpy()
    basic_lower = (hl2 - multiplier * atr_).to_numpy()
    closes = close.to_numpy()
    n = len(df)

    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)
    direction = np.zeros(n, dtype=np.int8)
    line = np.full(n, np.nan)

    if n == 0:
        return pd.Series(line, index=df.index), pd.Series(direction, index=df.index, dtype=np.int8)

    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    direction[0] = 1
    line[0] = basic_lower[0]

    for i in range(1, n):
        # final_upper ratchets DOWN unless the prior close pierced it
        if basic_upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]
        # final_lower ratchets UP unless the prior close broke it
        if basic_lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]
        # direction is set by close vs PRIOR bar's finalised bands
        if closes[i] > final_upper[i - 1]:
            direction[i] = 1
        elif closes[i] < final_lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        # the line is the band on the opposite side of the trend
        line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return pd.Series(line, index=df.index), pd.Series(direction, index=df.index, dtype=np.int8)


def donchian_channels(df: pd.DataFrame, period: int = 20) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Donchian channels using PRIOR completed bars only — causal.

    At row i, ``donchian_high`` is ``max(high[i-period .. i-1])`` (shifted by 1
    so the current bar's high never participates). Comparing today's close
    against today's donchian_high is therefore lookahead-safe — the channel
    you're breaking out *of* was finalised yesterday.
    """
    high = df["high"].rolling(period).max().shift(1)
    low = df["low"].rolling(period).min().shift(1)
    mid = (high + low) / 2.0
    return high, low, mid


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
    # Donchian channels — only computed if the donchian setup is wired in.
    donch_period = int(strategy.get("setups", {}).get("donchian", {}).get("period", 20))
    out["donchian_high"], out["donchian_low"], out["donchian_mid"] = donchian_channels(out, donch_period)
    # SuperTrend — only computed if the supertrend setup is wired in.
    st_cfg = strategy.get("setups", {}).get("supertrend", {})
    st_period = int(st_cfg.get("period", 10))
    st_mult = float(st_cfg.get("multiplier", 3.0))
    out["supertrend_line"], out["supertrend_direction"] = supertrend(out, st_period, st_mult)
    out["supertrend_direction_prev"] = out["supertrend_direction"].shift(1)
    return out


# ---------------------------------------------------------------------------
# Decisions — pure functions of a single row (+ open position for exits)
# ---------------------------------------------------------------------------


def _bullish_regime(row: pd.Series) -> bool:
    return bool(row["ema_fast"] > row["ema_slow"])


def long_entry(row: pd.Series, strategy: dict) -> str | None:
    """Return the setup name that fires a long here, or None. Regime-gated.

    Check order: donchian_breakout → pullback → breakout. Donchian takes
    priority when both donchian and breakout conditions fire on the same bar,
    because the Donchian experiment is the one being measured.
    """
    if not _bullish_regime(row):
        return None  # never trade against the 50/200 trend

    setups = strategy["setups"]

    # SuperTrend trend-following — fires only on bullish direction FLIPS
    # (prior bar was -1, current bar is +1). This avoids re-entering every
    # bar of a sustained uptrend.
    st = setups.get("supertrend", {})
    if st.get("enabled", False):
        cur = row.get("supertrend_direction")
        prev = row.get("supertrend_direction_prev")
        if pd.notna(cur) and pd.notna(prev):
            if int(cur) == 1 and int(prev) == -1:
                return "supertrend"

    # Donchian-20 trend-following breakout (uses prior-window high — causal)
    donch = setups.get("donchian", {})
    if donch.get("enabled", False):
        dh = row.get("donchian_high")
        if pd.notna(dh) and row["close"] > float(dh):
            max_neg_vwap = float(donch.get("max_negative_vwap_distance_pct", -0.015))
            vwap_val = row.get("vwap")
            if pd.notna(vwap_val) and float(vwap_val) > 0:
                vwap_dist = (row["close"] - float(vwap_val)) / float(vwap_val)
                vwap_ok = vwap_dist >= max_neg_vwap
            else:
                vwap_ok = False
            if vwap_ok:
                return "donchian_breakout"

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
    if setup == "supertrend":
        # The SuperTrend line IS the dynamic stop. At entry that level is the
        # current band on the opposite side of the trend.
        line = row.get("supertrend_line")
        if pd.notna(line):
            return float(line)
        # Fall back to a wide ATR stop if the line is somehow NaN at entry.
        return float(row["close"] - 3.0 * row["atr"])
    if setup == "donchian_breakout":
        cfg = strategy["setups"]["donchian"]
        mult = float(cfg.get("atr_stop_mult", 2.5))
    else:
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

    # SuperTrend short (Issue #19) — mirror of the long path. Fires only
    # on bearish direction FLIPS (prev=+1, current=-1) and only when both
    # the base SuperTrend setup is enabled AND the shorts.supertrend
    # toggle is on. Keeps the legacy pullback / breakout shorts intact.
    setups_st = (strategy.get("setups") or {}).get("supertrend") or {}
    shorts_st = shorts_cfg.get("supertrend") or {}
    if shorts_st.get("enabled", False) and setups_st.get("enabled", False):
        cur = row.get("supertrend_direction")
        prev = row.get("supertrend_direction_prev")
        if pd.notna(cur) and pd.notna(prev):
            if int(cur) == -1 and int(prev) == 1:
                return "supertrend_short"

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
    if setup == "supertrend_short":
        # The SuperTrend line at entry IS the stop. In a fresh DOWN regime
        # the line is the upper band, sitting above price. Falls back to a
        # wide ATR stop if the indicator is somehow NaN at entry.
        line = row.get("supertrend_line")
        if pd.notna(line):
            return float(line)
        return float(row["close"] + 3.0 * row["atr"])
    key = setup.replace("_short", "")  # 'breakout_short' -> 'breakout'
    mult = float(strategy["shorts"][key]["exit"].get("stop_atr_mult", 1.5))
    return float(row["close"] + mult * row["atr"])


def short_exit(row: pd.Series, position: dict, strategy: dict, bars_held: int) -> str | None:
    """Mirror of long_exit, with inverted comparisons (stop above, trail above)."""
    risk = strategy["risk"]

    # SuperTrend short setup (Issue #19): the indicator's line IS the
    # exit. Ratchet stop DOWN as the line falls, exit on bullish flip.
    if position["setup"] == "supertrend_short":
        st_cfg = strategy["setups"]["supertrend"]
        line = row.get("supertrend_line")
        if pd.notna(line):
            line_val = float(line)
            if line_val < position["stop"]:
                position["stop"] = line_val
        if row["high"] >= position["stop"]:
            return "stop"
        cur = row.get("supertrend_direction")
        if pd.notna(cur) and int(cur) == 1:
            return "supertrend_flip"
        max_hold = int(st_cfg.get("max_holding_bars", 0) or 0)
        if max_hold > 0 and bars_held >= max_hold:
            return "supertrend_time_stop"
        return None

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

    # SuperTrend setup: the indicator itself is the exit. Exit on bearish
    # flip; the SuperTrend line ratchets the stop as the trend extends.
    if position["setup"] == "supertrend":
        st_cfg = strategy["setups"]["supertrend"]
        line = row.get("supertrend_line")
        if pd.notna(line):
            line_val = float(line)
            if line_val > position["stop"]:
                position["stop"] = line_val
        if row["low"] <= position["stop"]:
            return "stop"
        cur = row.get("supertrend_direction")
        if pd.notna(cur) and int(cur) == -1:
            return "supertrend_flip"
        max_hold = int(st_cfg.get("max_holding_bars", 0) or 0)
        if max_hold > 0 and bars_held >= max_hold:
            return "supertrend_time_stop"
        return None

    # Donchian setup: distinct exit ladder — ATR trail + midline + bad-state + time stop.
    # Note: the EMA50/200 regime-flip exit is INTENTIONALLY skipped here; the
    # bad_markov_state check replaces it with a return-based filter that is
    # less noisy on 4h BTC (per Phase-3 finding).
    if position["setup"] == "donchian_breakout":
        donch_cfg = strategy["setups"]["donchian"]
        trail_mult = float(donch_cfg.get("atr_trail_mult", 3.0))
        if pd.notna(row.get("atr")):
            trail_level = float(row["close"]) - trail_mult * float(row["atr"])
            if trail_level > position["stop"]:
                position["stop"] = trail_level
        if row["low"] <= position["stop"]:
            return "stop"
        if donch_cfg.get("exit_on_midline_break", True):
            mid = row.get("donchian_mid")
            if pd.notna(mid) and row["close"] < float(mid):
                return "midline_break"
        if donch_cfg.get("exit_on_bad_markov_state", True):
            ss = row.get("markov_stable_state")
            if ss in ("down_low_vol", "down_high_vol"):
                return "bad_markov_state"
        max_hold = int(donch_cfg.get("max_holding_bars", 96))
        if max_hold > 0 and bars_held >= max_hold:
            return "donchian_time_stop"
        return None

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
