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


# ---------- bar-completeness helper (Issue #24) -----------------------------

def split_display_and_signal_rows(ind_df) -> tuple[dict, dict]:
    """Return ``(display_row, signal_row)`` from an indicator DataFrame.

    ``display_row`` is the **current in-progress** bar (``iloc[-1]``).
    It is used for the tick line, the heartbeat live-price field, and
    intra-bar stop monitoring so paper stops stay reactive.

    ``signal_row`` is the **most recently closed** bar (``iloc[-2]``).
    It is used for entry decisions and SuperTrend flip / time-stop
    exits so live behaviour matches what every backtest measured.

    If only one bar is present (e.g. the very first poll on startup),
    both rows fall back to ``iloc[-1]`` — there is no closed bar yet
    to use.

    This split is the entire fix for Issues #23 / #24 — every other
    audit finding flowed from feeding the in-progress bar into signal
    evaluation. ``signals.py`` is unchanged.
    """
    if len(ind_df) == 0:
        empty: dict = {}
        return empty, empty
    display_row = ind_df.iloc[-1].to_dict()
    if len(ind_df) >= 2:
        signal_row = ind_df.iloc[-2].to_dict()
    else:
        signal_row = display_row
    return display_row, signal_row


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

# ---------- "why no trade" diagnostics (Issue #18) --------------------------

#: Default distance below the SuperTrend line at which the asset is considered
#: "near a potential entry". Configurable per call. -1.0 = within 1% below the
#: line.
DEFAULT_NEAR_ENTRY_THRESHOLD_PCT = -1.0


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def diagnose_entry_blockers(
    row: dict,
    strategy: dict,
    position: dict | None,
    portfolio_open: int = 0,
    max_open: int = 1,
    near_entry_threshold_pct: float = DEFAULT_NEAR_ENTRY_THRESHOLD_PCT,
) -> dict:
    """Pure-function diagnostic. Returns a dict describing why an entry is
    or isn't blocked on the current bar.

    Keys returned:
        in_position        : bool — asset already holds
        bullish_regime_ok  : bool — EMA50 > EMA200
        st_dir             : 'UP' / 'DOWN' / '?'
        st_dir_prev        : 'UP' / 'DOWN' / '?'
        st_flip_ok         : bool — prev=DOWN, current=UP (a fresh UP flip)
        distance_pct       : float — (close - line) / line * 100 (signed)
        portfolio_cap_reached : bool
        blockers           : list[str] — short labels for what's blocking
        waiting_for        : str — human-readable description of entry rule
        near_entry         : str | None — present when close is near the band
        blocked_by         : str | None — populated when ALL signal conditions
                             pass but the portfolio cap stops the entry
    """
    out: dict = {
        "in_position": position is not None,
        "blockers": [],
        "waiting_for": None,
        "near_entry": None,
        "blocked_by": None,
    }

    close = _safe_float(row.get("close"))
    ema_fast = _safe_float(row.get("ema_fast"))
    ema_slow = _safe_float(row.get("ema_slow"))
    st_line = _safe_float(row.get("supertrend_line"))
    cur_dir = row.get("supertrend_direction")
    prev_dir = row.get("supertrend_direction_prev")

    out["st_dir"] = _direction_str(cur_dir)
    out["st_dir_prev"] = _direction_str(prev_dir)
    bullish_regime_ok = ema_fast is not None and ema_slow is not None and ema_fast > ema_slow
    out["bullish_regime_ok"] = bool(bullish_regime_ok)

    # SuperTrend "flip up" requires the previous bar to have been DOWN and
    # the current bar to be UP. We test both.
    try:
        st_flip_ok = (
            cur_dir is not None and prev_dir is not None
            and int(cur_dir) == 1 and int(prev_dir) == -1
        )
    except (TypeError, ValueError):
        st_flip_ok = False
    out["st_flip_ok"] = bool(st_flip_ok)

    dist_pct = None
    if close is not None and st_line is not None and st_line > 0:
        dist_pct = (close - st_line) / st_line * 100.0
    out["distance_pct"] = dist_pct

    portfolio_cap_reached = portfolio_open >= max_open
    out["portfolio_cap_reached"] = bool(portfolio_cap_reached)

    setups = strategy.get("setups") or {}
    supertrend_enabled = bool((setups.get("supertrend") or {}).get("enabled"))
    out["supertrend_enabled"] = supertrend_enabled

    if out["in_position"]:
        # An existing position is not "blocked from entry"; describe what
        # the worker is now waiting for instead.
        if supertrend_enabled:
            out["waiting_for"] = "SuperTrend flip DOWN (exit) or stop hit"
        else:
            out["waiting_for"] = "strategy-defined exit rule"
        return out

    # Flat — work out which entry-rule conditions are blocking.
    if supertrend_enabled:
        out["waiting_for"] = "SuperTrend flip UP + EMA50 > EMA200"

        if out["st_dir"] != "UP":
            out["blockers"].append(f"supertrend_direction={out['st_dir']}")
        if dist_pct is not None and dist_pct < 0:
            out["blockers"].append(
                f"close below supertrend_line by {abs(dist_pct):.2f}%"
            )
        if not st_flip_ok and out["st_dir"] == "UP":
            # Already in UP regime; we don't fire again until the next
            # DOWN→UP transition.
            out["blockers"].append("no fresh DOWN→UP flip this bar")
        if not bullish_regime_ok:
            out["blockers"].append("ema50_below_ema200")

        # "Near entry": close is within the configured pct of the band,
        # currently below it (typical setup before a flip).
        if (dist_pct is not None
                and dist_pct < 0
                and dist_pct >= near_entry_threshold_pct):
            out["near_entry"] = (
                f"needs +{abs(dist_pct):.2f}% close above SuperTrend line on "
                f"completed 4h bar"
            )

        # "blocked_by portfolio cap" only fires when entry conditions are
        # actually met — otherwise the cap isn't the relevant blocker.
        if st_flip_ok and bullish_regime_ok and portfolio_cap_reached:
            out["blocked_by"] = "portfolio max_open_positions reached"

    else:
        # Legacy v2 long-short: produce a useful waiting_for + blocker list
        # so the verbose mode is informative there too. Brief because the
        # primary target of this feature is the SuperTrend live deployment.
        out["waiting_for"] = "RSI / breakout / pullback signal"
        if not bullish_regime_ok:
            out["blockers"].append("ema50_below_ema200")
        if portfolio_cap_reached:
            out["blockers"].append("portfolio max_open_positions reached")

    return out


def format_entry_diagnostic_lines(diag: dict) -> list[str]:
    """Render the diagnostic dict into 1–3 indented lines suitable for the
    log. Returns an empty list if there is nothing to show (e.g. asset is
    already in a position and has no waiting_for)."""
    lines: list[str] = []
    if diag.get("waiting_for"):
        lines.append(f"  waiting_for: {diag['waiting_for']}")
    blockers = diag.get("blockers") or []
    if blockers:
        lines.append("  blockers: " + ", ".join(blockers))
    if diag.get("near_entry"):
        lines.append(f"  near_entry: {diag['near_entry']}")
    if diag.get("blocked_by"):
        lines.append(f"  blocked_by: {diag['blocked_by']}")
    return lines


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
