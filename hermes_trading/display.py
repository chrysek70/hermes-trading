"""Live-worker tick-display helpers (Issue #17).

Pure presentation. No trading logic, no I/O. Used by both the
single-asset loop (`loop.py`) and the multi-asset loop
(`multi_loop.py`) to render tick lines and heartbeat fields that
match whichever strategy is currently active.

The auto-detect rule: if ``strategy.setups.supertrend.enabled`` is
truthy, we are in "SuperTrend mode" and show SuperTrend-relevant
fields (direction, line, distance). Otherwise we keep the legacy
RSI-style tick line so the v2 long-short strategy renders exactly as
it did before.

RSI is never *removed* from the system — it is still computed by
``signals.compute_indicators`` and still written to the heartbeat
JSON and the closed-trade rows. The SuperTrend tick line only
demotes it from the headline so the screen stops misrepresenting
which signal is driving entries.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


# ---------- mode detection --------------------------------------------------

def is_supertrend_active(strategy: dict) -> bool:
    """True when the strategy yaml's SuperTrend setup is enabled.

    Defensive against missing keys — a legacy v2 yaml with no
    ``supertrend`` block returns False without raising.
    """
    setups = strategy.get("setups") or {}
    st = setups.get("supertrend") or {}
    return bool(st.get("enabled"))


# ---------- helpers ---------------------------------------------------------

def _direction_str(value: Any) -> str:
    """Map the raw supertrend_direction column (+1 / -1 / NaN) to a label."""
    if value is None:
        return "?"
    try:
        if pd.isna(value):
            return "?"
    except (TypeError, ValueError):
        pass
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "?"
    if v == 1:
        return "UP"
    if v == -1:
        return "DOWN"
    return "?"


def _format_distance_pct(close: float | None, line: Any) -> tuple[str, float | None]:
    """Return (display_str, raw_pct). raw_pct is None when unavailable."""
    if close is None or line is None:
        return "?", None
    try:
        if pd.isna(line):
            return "?", None
    except (TypeError, ValueError):
        pass
    try:
        line_f = float(line)
    except (TypeError, ValueError):
        return "?", None
    if line_f == 0:
        return "?", None
    pct = (float(close) - line_f) / line_f * 100.0
    return f"{pct:+.2f}%", pct


# ---------- tick-line formatters --------------------------------------------

def format_supertrend_tick(
    asset: str,
    close: float,
    supertrend_direction: Any,
    supertrend_line: Any,
    strategy_version: str | None,
    position: dict | None = None,
    rsi: float | None = None,
    verbose: bool = False,
) -> str:
    """SuperTrend-mode per-asset tick line.

    Example (flat):
        tick BTC/USDT close=73890.80 st=UP line=72150.22 dist=+2.41% v=v3-supertrend-01 pos=flat

    Example (long):
        tick ETH/USDT close=3901.20 st=UP line=3720.00 dist=+4.87% v=v3-supertrend-01 pos=long setup=supertrend uPnL=+1.22%
    """
    st_label = _direction_str(supertrend_direction)
    line_str = "?"
    if supertrend_line is not None:
        try:
            if not pd.isna(supertrend_line):
                line_str = f"{float(supertrend_line):.2f}"
        except (TypeError, ValueError):
            pass
    dist_str, _ = _format_distance_pct(close, supertrend_line)
    version = strategy_version or "?"

    if position is None:
        pos_str = "pos=flat"
    else:
        entry = float(position.get("entry_price", 0.0))
        size = float(position.get("size", 1.0))
        if entry > 0:
            direction = position.get("direction", "long")
            if direction == "long":
                chg = (float(close) - entry) / entry
            else:
                chg = (entry - float(close)) / entry
            upnl_pct = chg * size * 100.0
            upnl_str = f"uPnL={upnl_pct:+.2f}%"
        else:
            upnl_str = "uPnL=?"
        setup = position.get("setup") or "?"
        direction_label = position.get("direction") or "long"
        pos_str = f"pos={direction_label} setup={setup} {upnl_str}"

    base = (
        f"tick {asset} close={float(close):.2f} st={st_label} line={line_str} "
        f"dist={dist_str} v={version} {pos_str}"
    )
    if verbose:
        rsi_str = f"{rsi:.1f}" if (rsi is not None and not pd.isna(rsi)) else "n/a"
        base += f" rsi={rsi_str}"
    return base


def format_rsi_tick(
    asset: str,
    close: float,
    rsi: float | None,
    strategy_version: str | None,
    position: dict | None = None,
    regime_str: str = "regime=off",
    paper_notional_usd: float | None = None,
) -> str:
    """Legacy v2-style tick line. Format preserved byte-for-byte so any
    existing tooling that greps the single-asset log keeps working."""
    last_str = f"{float(close):.2f}"
    rsi_str = f"{rsi:.1f}" if (rsi is not None and not pd.isna(rsi)) else "n/a"
    version = strategy_version or "?"
    if position is None:
        pos_str = "flat"
    else:
        entry = float(position.get("entry_price", 0.0))
        size = float(position.get("size", 1.0))
        direction = position.get("direction", "long")
        if entry > 0:
            if direction == "long":
                chg = (float(close) - entry) / entry
                arrow = "↑"
            else:
                chg = (entry - float(close)) / entry
                arrow = "↓"
            pnl_ret = chg * size
            usd_str = ""
            if paper_notional_usd is not None:
                pnl_usd = pnl_ret * paper_notional_usd
                usd_str = f"  ${pnl_usd:+.2f}"
            color = "green" if pnl_ret >= 0 else "red"
            pos_str = (
                f"{arrow}{direction} @ {entry:.2f} "
                f"[{color}]{pnl_ret * 100:+.3f}%{usd_str}[/{color}]"
            )
        else:
            pos_str = direction
    return f"tick {asset} {last_str} rsi={rsi_str} v{version} pos={pos_str} {regime_str}"


# ---------- heartbeat fields ------------------------------------------------

def supertrend_heartbeat_fields(
    close: float | None,
    supertrend_direction: Any,
    supertrend_line: Any,
) -> dict:
    """Three SuperTrend-related fields for the heartbeat. Values are
    ``None`` when the indicator is in warmup or the band is undefined.

    The keys are:
        supertrend_direction : "UP" / "DOWN" / None
        supertrend_line      : float / None
        supertrend_distance_pct : float (percent) / None
    """
    direction_label = _direction_str(supertrend_direction)
    direction_out = direction_label if direction_label in ("UP", "DOWN") else None

    line_out: float | None = None
    if supertrend_line is not None:
        try:
            if not pd.isna(supertrend_line):
                line_out = float(supertrend_line)
        except (TypeError, ValueError):
            pass

    _, dist_pct = _format_distance_pct(close, supertrend_line)
    return {
        "supertrend_direction": direction_out,
        "supertrend_line": line_out,
        "supertrend_distance_pct": dist_pct,
    }
