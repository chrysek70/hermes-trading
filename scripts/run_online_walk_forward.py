#!/usr/bin/env python3
"""Online walk-forward adaptive learning simulator (Issue #32).

Treats each historical 4h closed bar as if it were live at that
moment. No future leakage. Adaptive risk rules update only from
closed-trade memory. Research only — does NOT modify live config or
strategy logic.

For every bar in chronological order, per asset:

  1. The simulator sees only bars up to and including the CLOSED bar
     at index i-1 (signal_row). Intra-bar stop checks use the
     in-progress bar at index i (display_row). This matches the
     Issue #24 closed-bar semantics the live worker uses.
  2. If a position is open, evaluate the appropriate exit using
     `signals.long_exit` / `signals.short_exit` unmodified.
  3. If flat, evaluate `signals.long_entry` / `signals.short_entry`,
     then the funding hard gate, then compute the active adaptive
     sizing multiplier from a CLOSED-TRADE memory whose every
     element has `exit_ts < bar_ts`. Open the trade with the locked
     base × adaptive multiplier; never resize an open trade.
  4. Record one decision row per bar per asset and one trade row per
     close.

Adaptive rules (all locked, no parameter tuning):

  - `none`                  — multiplier always 1.0; reproduces the
                              current adopted live behaviour.
  - `rolling_decay_size`    — rolling profit factor over the last 10
                              closed trades. PF < 0.7 → 0.25;
                              PF < 1.0 → 0.5; PF ≥ 1.2 → 1.0;
                              fewer than 10 trades → 1.0.
  - `consecutive_loss_size` — 3 consecutive losses → 0.5;
                              4 consecutive losses → 0.25; reset on
                              any winning trade.
  - `stop_cluster_size`     — last 5 closed trades: 5 stops → 0.25;
                              4 stops → 0.5; otherwise 1.0.
  - `vol_sizing`            — 24-bar rolling std of log returns;
                              quartile thresholds from the prior
                              `refit_train_months` of data, refit
                              every `refit_every_bars` bars to
                              avoid look-ahead. Low → 1.0, mid →
                              0.5, high → 0.25.
  - `ensemble`              — MIN(rolling_decay_size,
                              stop_cluster_size, vol_sizing).

Hard rules respected:
  - No live config changes; no strategy yaml edits.
  - No tuning of SuperTrend / funding thresholds.
  - No future leakage. At bar T the rules see only closed-trade
    history whose `exit_ts < T`, and (vol_sizing) only quartiles
    fitted on bars before the active refit window.
  - Multipliers MIN out at 0.25; the simulator never sets a 0
    multiplier, so adaptive rules never gate signals — they only
    resize.

Outputs:
  results/online_walk_forward_decisions_<ts>.csv
  results/online_walk_forward_trades_<ts>.csv
  results/online_walk_forward_comparison_<ts>.csv  (--compare-all-rules)
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import STATE_DIR, log
from hermes_trading import data as data_mod
from hermes_trading import signals
from hermes_trading.multi_loop import (
    LiveFundingOverlay,
    RESEARCH_FEE_PER_SIDE,
    RESEARCH_SLIPPAGE,
    evaluate_funding_gate,
)

# ---- Locked adaptive-rule constants (Issue #32 spec — DO NOT TUNE) ---------

ROLLING_WINDOW = 10
ROLLING_PF_BLOCK_LOW = 0.7   # PF < this -> 0.25
ROLLING_PF_BLOCK_MID = 1.0   # PF < this -> 0.5
ROLLING_PF_FULL = 1.2        # PF >= this -> 1.0

CONSEC_HALF = 3              # 3 in a row -> 0.5
CONSEC_QUARTER = 4           # 4 in a row -> 0.25

STOP_WINDOW = 5
STOP_HALF = 4                # 4 of last 5 stops -> 0.5
STOP_QUARTER = 5             # all 5 stops -> 0.25

VOL_WINDOW_BARS = 24         # Issue #27 rolling-vol window (4 days at 4h)
SIZE_FULL = 1.00
SIZE_HALF = 0.50
SIZE_QUARTER = 0.25

# Vol-sizing refit cadence. Quartile thresholds are fitted on the
# `refit_train_months` of data ending just before the active window,
# then locked for `refit_every_bars` bars. This is the closest online
# analogue to Issue #27's per-fold train-window quartiles.
DEFAULT_REFIT_TRAIN_MONTHS = 12
DEFAULT_REFIT_EVERY_BARS = 30 * 6   # ~30 days at 4h (180 bars)

ADAPTIVE_RULES = (
    "none",
    "rolling_decay_size",
    "consecutive_loss_size",
    "stop_cluster_size",
    "vol_sizing",
    "ensemble",
)


# ===========================================================================
# Closed-trade memory + adaptive-rule evaluators
# ===========================================================================

class ClosedTradeMemory:
    """Per-asset and portfolio-wide rolling memory of closed trades.

    Every adaptive rule reads from this object. The only mutation
    point is `record_close`, which is called when a trade closes.
    Every read at bar T uses entries whose `exit_ts < T` — the
    invariant the no-future-leakage tests rely on.
    """

    def __init__(self) -> None:
        self.per_asset: dict[str, list[dict]] = {}
        self.global_trades: list[dict] = []

    def record_close(self, asset: str, trade: dict) -> None:
        self.per_asset.setdefault(asset, []).append(trade)
        self.global_trades.append(trade)

    def closed_before(self, asset: str, ts: pd.Timestamp) -> list[dict]:
        """Return closed trades for ``asset`` whose ``exit_ts`` is strictly
        before ``ts``. The strict inequality is the no-leakage guard."""
        ts_pd = pd.Timestamp(ts)
        trades = self.per_asset.get(asset, [])
        # Trades are appended in chronological order so we can just
        # walk from the tail backwards until exit_ts < ts.
        out: list[dict] = []
        for t in trades:
            if pd.Timestamp(t["exit_ts"]) < ts_pd:
                out.append(t)
            else:
                break
        # Maintain chronological order
        return out

    def realized_pnl_before(self, ts: pd.Timestamp) -> float:
        """Sum of net_return_pct across ALL assets before ``ts``."""
        ts_pd = pd.Timestamp(ts)
        return sum(t["net_return_pct"] for t in self.global_trades
                   if pd.Timestamp(t["exit_ts"]) < ts_pd)


def rolling_decay_multiplier(trades: list[dict]) -> tuple[float, float | None]:
    """Compute the rolling_decay multiplier from the most recent
    `ROLLING_WINDOW` closed trades. Returns (multiplier, pf_or_None).

    Per spec: fewer than 10 trades → 1.0; PF < 0.7 → 0.25;
    PF < 1.0 → 0.5; PF ≥ 1.2 → 1.0; 1.0 ≤ PF < 1.2 → 0.5 (neutral
    band)."""
    if len(trades) < ROLLING_WINDOW:
        return SIZE_FULL, None
    window = trades[-ROLLING_WINDOW:]
    rets = [t["net_return_pct"] for t in window]
    wins = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r < 0)
    if losses == 0:
        pf = float("inf")
    else:
        pf = wins / losses
    if pf < ROLLING_PF_BLOCK_LOW:
        return SIZE_QUARTER, pf
    if pf < ROLLING_PF_BLOCK_MID:
        return SIZE_HALF, pf
    if pf >= ROLLING_PF_FULL:
        return SIZE_FULL, pf
    # 1.0 <= pf < 1.2 — neutral band; halve (consistent with the spec's
    # silence on this band: PF below "fully healthy" but above the
    # explicit cut should not be at full size).
    return SIZE_HALF, pf


def consecutive_loss_multiplier(trades: list[dict]) -> tuple[float, int]:
    """Walk back from the tail until a win is found. Returns
    (multiplier, consecutive_loss_count). After a win the counter
    resets to 0 → 1.0."""
    if not trades:
        return SIZE_FULL, 0
    streak = 0
    for t in reversed(trades):
        if t["net_return_pct"] <= 0:
            streak += 1
        else:
            break
    if streak >= CONSEC_QUARTER:
        return SIZE_QUARTER, streak
    if streak >= CONSEC_HALF:
        return SIZE_HALF, streak
    return SIZE_FULL, streak


def stop_cluster_multiplier(trades: list[dict]) -> tuple[float, int]:
    """Among the last `STOP_WINDOW` closed trades, count exits with
    reason starting with ``stop``. Returns (multiplier, count_in_window).
    Fewer than `STOP_WINDOW` trades → 1.0."""
    if len(trades) < STOP_WINDOW:
        return SIZE_FULL, 0
    window = trades[-STOP_WINDOW:]
    n_stops = sum(1 for t in window
                  if str(t.get("exit_reason", "")).startswith("stop"))
    if n_stops >= STOP_QUARTER:
        return SIZE_QUARTER, n_stops
    if n_stops >= STOP_HALF:
        return SIZE_HALF, n_stops
    return SIZE_FULL, n_stops


# ===========================================================================
# Vol-sizing — Issue #27 logic, online refit
# ===========================================================================

class VolSizingState:
    """Rolling realised-vol sizing using PRIOR-WINDOW quartiles.

    Each asset gets its own state. The quartile thresholds q25/q75
    are refit on a trailing `refit_train_months` slab of vol values
    ending at the most recent refit timestamp; once fitted they are
    locked until the next refit. This is the online equivalent of
    Issue #27's per-fold train-window quartiles.

    No future leakage: at bar T the bands used are derived from
    bars < T (the refit window) and the active vol value at T is
    the rolling std of log returns over bars (T-VOL_WINDOW_BARS .. T).
    """

    def __init__(
        self,
        asset_close: pd.Series,
        timestamps: pd.DatetimeIndex,
        refit_train_months: int = DEFAULT_REFIT_TRAIN_MONTHS,
        refit_every_bars: int = DEFAULT_REFIT_EVERY_BARS,
        vol_window: int = VOL_WINDOW_BARS,
    ) -> None:
        log_ret = np.log(asset_close / asset_close.shift(1))
        self.realized_vol = log_ret.rolling(vol_window, min_periods=vol_window).std()
        self.realized_vol = self.realized_vol.reindex(timestamps)
        self.refit_train_bars = int(refit_train_months * 30 * 6)  # 30d * 6 bars/d at 4h
        self.refit_every = int(refit_every_bars)
        self.q25: float | None = None
        self.q75: float | None = None
        self.last_refit_i: int = -10 ** 9  # never refitted

    def update(self, i: int) -> None:
        """Refit the quartile bounds using the last `refit_train_bars`
        of vol values STRICTLY BEFORE bar i. Called once per bar; the
        actual refit only fires every `refit_every` bars."""
        if i - self.last_refit_i < self.refit_every and self.q25 is not None:
            return
        lo = max(0, i - self.refit_train_bars)
        hi = i  # exclusive — only bars STRICTLY before i contribute
        train_slice = self.realized_vol.iloc[lo:hi].dropna()
        if len(train_slice) < 8:
            return  # not enough history yet — keep prior bands (or None)
        self.q25 = float(train_slice.quantile(0.25))
        self.q75 = float(train_slice.quantile(0.75))
        self.last_refit_i = i

    def multiplier(self, i: int) -> tuple[float, float | None]:
        """Return (multiplier, realized_vol_at_i_or_None) at bar i.

        Warmup (no quartile bounds yet OR vol is NaN) → fail open
        with 1.0, matching Issue #27."""
        v = self.realized_vol.iloc[i] if i < len(self.realized_vol) else np.nan
        if pd.isna(v) or self.q25 is None or self.q75 is None:
            return SIZE_FULL, (None if pd.isna(v) else float(v))
        vv = float(v)
        if vv <= self.q25:
            return SIZE_FULL, vv
        if vv >= self.q75:
            return SIZE_QUARTER, vv
        return SIZE_HALF, vv


# ===========================================================================
# Core simulator
# ===========================================================================

def _symbol_from_asset(asset: str) -> str:
    return asset.replace("/", "")


def _resolve_path(cfg_value: str, cfg_dir: Path) -> Path:
    p = Path(cfg_value)
    if p.is_absolute():
        return p
    if str(p).startswith("state/"):
        return ROOT / p
    return cfg_dir / p


def _load_assets(
    assets: list[str], n_months: int, timeframe: str
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for asset in assets:
        sym = _symbol_from_asset(asset)
        log(f"  loading {sym} {n_months}mo {timeframe} …")
        df = data_mod.resample(
            data_mod.load_klines(sym, n_months=n_months), timeframe,
        )
        out[asset] = df
    return out


def _funding_decision_at(
    overlay: LiveFundingOverlay | None,
    asset: str,
    ts: pd.Timestamp,
    direction: str,
    block_long: float,
    block_short: float,
    missing_policy: str,
) -> tuple[dict, dict]:
    """Returns (funding_state_dict, gate_dict). Both empty-ish if the
    overlay is None."""
    if overlay is None:
        return ({"available": False, "rate": None, "percentile": None},
                {"allow": True, "decision": "disabled", "reason": "overlay off"})
    state = overlay.state_at(asset, ts)
    pct = state.get("percentile")
    gate = evaluate_funding_gate(
        direction, pct,
        block_long_above_pct=block_long,
        block_short_below_pct=block_short,
        on_missing_data=missing_policy,
    )
    return state, gate


def _compute_multipliers(
    rule: str,
    asset_trades: list[dict],
    vol_state: VolSizingState | None,
    bar_i: int,
) -> dict[str, float | None]:
    """Compute every sizing multiplier needed for the decision log,
    and pick the active one for ``rule``. Returns a dict with keys:
        rolling_mult, rolling_pf
        consec_mult, consec_streak
        stop_mult, stop_count
        vol_mult, realized_vol
        active   (the multiplier actually applied)
    """
    rolling_mult, rolling_pf = rolling_decay_multiplier(asset_trades)
    consec_mult, consec_streak = consecutive_loss_multiplier(asset_trades)
    stop_mult, stop_count = stop_cluster_multiplier(asset_trades)
    if vol_state is not None:
        vol_mult, rvol = vol_state.multiplier(bar_i)
    else:
        vol_mult, rvol = SIZE_FULL, None

    if rule == "none":
        active = SIZE_FULL
    elif rule == "rolling_decay_size":
        active = rolling_mult
    elif rule == "consecutive_loss_size":
        active = consec_mult
    elif rule == "stop_cluster_size":
        active = stop_mult
    elif rule == "vol_sizing":
        active = vol_mult
    elif rule == "ensemble":
        active = min(rolling_mult, stop_mult, vol_mult)
    else:
        raise ValueError(f"unknown adaptive rule: {rule!r}")

    return {
        "rolling_mult": rolling_mult,
        "rolling_pf": rolling_pf,
        "consec_mult": consec_mult,
        "consec_streak": consec_streak,
        "stop_mult": stop_mult,
        "stop_count": stop_count,
        "vol_mult": vol_mult,
        "realized_vol": rvol,
        "active": active,
    }


def run_simulation(
    *,
    rule: str,
    cfg: dict,
    cfg_dir: Path,
    n_months: int,
    fee: float = RESEARCH_FEE_PER_SIDE,
    slippage: float = RESEARCH_SLIPPAGE,
    refit_train_months: int = DEFAULT_REFIT_TRAIN_MONTHS,
    refit_every_bars: int = DEFAULT_REFIT_EVERY_BARS,
    cached_indicators: dict[str, pd.DataFrame] | None = None,
    cached_overlay: LiveFundingOverlay | None = None,
) -> dict:
    """Run the online walk-forward simulator for a single adaptive rule.

    Returns a dict with keys:
        decisions: list[dict]   — one per bar per asset
        trades:    list[dict]   — one per closed trade
        meta:      dict         — metadata: assets, span, etc.
    """
    if rule not in ADAPTIVE_RULES:
        raise ValueError(f"unknown adaptive rule: {rule!r}")

    assets: list[str] = list(cfg["assets"])
    timeframe: str = cfg.get("timeframe", "4h")
    max_open = int(cfg.get("max_open_positions", len(assets)))
    size_per_asset = float(cfg.get("size_per_asset", 1.0 / max(len(assets), 1)))
    base_size = size_per_asset  # the live worker's "base" — per-asset weight
    strategy_path = _resolve_path(cfg["strategy"], cfg_dir)
    strategy = yaml.safe_load(open(strategy_path))

    funding_cfg = cfg.get("funding_filter") or {}
    funding_enabled = bool(funding_cfg.get("enabled"))
    block_long = float(funding_cfg.get("block_long_above_pct", 95.0))
    block_short = float(funding_cfg.get("block_short_below_pct", 5.0))
    funding_window = int(funding_cfg.get("percentile_window_bars", 180))
    missing_policy = str(funding_cfg.get("on_missing_data", "fail_open"))

    if cached_indicators is None:
        log(f"[{rule}] loading + computing indicators …")
        raw = _load_assets(assets, n_months, timeframe)
        ind_by_asset: dict[str, pd.DataFrame] = {
            a: signals.compute_indicators(df, strategy) for a, df in raw.items()
        }
    else:
        ind_by_asset = {a: df.copy() for a, df in cached_indicators.items()}

    # Align to the common index across assets (lockstep replay).
    common_index = None
    for ind in ind_by_asset.values():
        idx = ind.index
        common_index = idx if common_index is None else common_index.intersection(idx)
    ind_by_asset = {a: ind.loc[common_index].copy()
                    for a, ind in ind_by_asset.items()}

    # Funding overlay (shared across rules if cached).
    overlay: LiveFundingOverlay | None
    if funding_enabled and cached_overlay is None:
        overlay = LiveFundingOverlay(
            assets,
            percentile_window_bars=funding_window,
            n_months_history=max(n_months + 6, 36),
            timeframe=timeframe,
        )
    else:
        overlay = cached_overlay

    # Per-asset vol-sizing state (only built for rules that need it).
    vol_states: dict[str, VolSizingState | None] = {}
    for a in assets:
        if rule in ("vol_sizing", "ensemble"):
            vol_states[a] = VolSizingState(
                ind_by_asset[a]["close"],
                ind_by_asset[a].index,
                refit_train_months=refit_train_months,
                refit_every_bars=refit_every_bars,
            )
        else:
            vol_states[a] = None

    memory = ClosedTradeMemory()
    positions: dict[str, dict | None] = {a: None for a in assets}
    decisions: list[dict] = []
    trades: list[dict] = []

    n_bars = len(common_index)
    log(f"[{rule}] running {n_bars} bars × {len(assets)} assets "
        f"(span {common_index[0].date()} -> {common_index[-1].date()})")

    for i in range(1, n_bars):
        bar_ts = common_index[i]
        signal_ts = common_index[i - 1]
        for asset in assets:
            ind = ind_by_asset[asset]
            display_row = ind.iloc[i].to_dict()
            display_row["ts"] = bar_ts
            signal_row = ind.iloc[i - 1].to_dict()
            signal_row["ts"] = signal_ts
            close_now = float(display_row["close"])

            # --- update vol-sizing refit state (uses STRICTLY < i) ---
            vs = vol_states.get(asset)
            if vs is not None:
                vs.update(i)

            position = positions[asset]
            action = "hold"
            direction = "n/a"
            setup = "n/a"
            signal_state = "in_position" if position is not None else "flat"
            base_size_logged = base_size
            adaptive_mult = SIZE_FULL
            final_size = base_size
            reason = ""
            funding_decision = "n/a"

            # Compute the rule-driven multiplier from the memory at THIS
            # timestamp. This is recorded into every row even when the
            # current decision is hold / exit, so the decision log is a
            # complete causal record of what the rules saw bar-by-bar.
            asset_trades = memory.closed_before(asset, bar_ts)
            mults = _compute_multipliers(rule, asset_trades, vs, i)

            # ===== EXIT FIRST (if in position) =====
            if position is not None:
                direction = position["direction"]
                setup = position["setup"]
                bars_held = i - position["entry_i"]
                if direction == "long":
                    exit_reason = signals.long_exit(signal_row, position,
                                                    strategy, bars_held)
                else:
                    exit_reason = signals.short_exit(signal_row, position,
                                                     strategy, bars_held)
                # Intra-bar stop reactivity (matches multi_loop.run)
                if exit_reason is None:
                    if direction == "long":
                        dlow = display_row.get("low")
                        if (dlow is not None and not pd.isna(dlow)
                                and float(dlow) <= position["stop"]):
                            exit_reason = "stop"
                    else:
                        dhigh = display_row.get("high")
                        if (dhigh is not None and not pd.isna(dhigh)
                                and float(dhigh) >= position["stop"]):
                            exit_reason = "stop"
                if exit_reason:
                    if direction == "long":
                        base_exit = (position["stop"] if exit_reason == "stop"
                                     else close_now)
                        exit_fill = base_exit * (1.0 - slippage)
                        gross = (exit_fill - position["entry"]) / position["entry"]
                    else:
                        base_exit = (position["stop"] if exit_reason == "stop"
                                     else close_now)
                        exit_fill = base_exit * (1.0 + slippage)
                        gross = (position["entry"] - exit_fill) / position["entry"]
                    # The locked size at entry is base × adaptive_at_entry.
                    effective = position["base_size"] * position["adaptive_at_entry"]
                    net = (gross - 2 * fee) * effective
                    trade = {
                        "asset": asset,
                        "direction": direction,
                        "entry_time": position["entry_ts"].isoformat(),
                        "exit_time": bar_ts.isoformat(),
                        "exit_ts": bar_ts,  # internal — pruned at CSV write
                        "entry_price": position["entry"],
                        "exit_price": exit_fill,
                        "gross_return_pct": gross,
                        "net_return_pct": net,
                        "base_size": position["base_size"],
                        "adaptive_multiplier": position["adaptive_at_entry"],
                        "final_size": effective,
                        "exit_reason": exit_reason,
                        "setup": position["setup"],
                        "bars_held": bars_held,
                    }
                    trades.append(trade)
                    memory.record_close(asset, trade)
                    positions[asset] = None
                    action = "exit"
                    final_size = effective
                    adaptive_mult = position["adaptive_at_entry"]
                    base_size_logged = position["base_size"]
                    reason = exit_reason
                    signal_state = "flat"
            # ===== ENTRY (if still flat) =====
            elif position is None:
                # portfolio cap
                open_count = sum(1 for p in positions.values() if p is not None)
                if open_count >= max_open:
                    signal_state = "flat"
                    reason = "portfolio_cap_reached"
                else:
                    setup_l = signals.long_entry(signal_row, strategy)
                    opened = False
                    if setup_l:
                        f_state, gate = _funding_decision_at(
                            overlay, asset, signal_ts, "long",
                            block_long, block_short, missing_policy,
                        )
                        funding_decision = gate["decision"]
                        signal_state = "long_signal"
                        if gate["allow"]:
                            adaptive_mult = mults["active"]
                            if adaptive_mult <= 0.0:
                                action = "skip"
                                reason = (f"adaptive_multiplier_zero "
                                          f"(rule={rule})")
                            else:
                                # Lock size for the life of the position.
                                entry_fill = close_now * (1.0 + slippage)
                                stop_val = float(
                                    signals.initial_stop(signal_row,
                                                          setup_l, strategy))
                                positions[asset] = {
                                    "asset": asset,
                                    "entry": entry_fill,
                                    "stop": stop_val,
                                    "direction": "long",
                                    "setup": setup_l,
                                    "entry_i": i,
                                    "entry_ts": bar_ts,
                                    "base_size": base_size,
                                    "adaptive_at_entry": adaptive_mult,
                                }
                                opened = True
                                action = "enter"
                                direction = "long"
                                setup = setup_l
                                final_size = base_size * adaptive_mult
                                reason = (f"long {setup_l} adaptive={adaptive_mult:.2f}")
                        else:
                            action = "skip"
                            reason = f"funding_block: {gate['reason']}"
                    if not opened and positions[asset] is None:
                        setup_s = (signals.short_entry(signal_row, strategy)
                                   if strategy.get("shorts", {}).get("enabled")
                                   else None)
                        if setup_s:
                            f_state, gate = _funding_decision_at(
                                overlay, asset, signal_ts, "short",
                                block_long, block_short, missing_policy,
                            )
                            funding_decision = gate["decision"]
                            signal_state = "short_signal"
                            if gate["allow"]:
                                adaptive_mult = mults["active"]
                                if adaptive_mult <= 0.0:
                                    action = "skip"
                                    reason = (f"adaptive_multiplier_zero "
                                              f"(rule={rule})")
                                else:
                                    entry_fill = close_now * (1.0 - slippage)
                                    stop_val = float(
                                        signals.initial_stop_short(
                                            signal_row, setup_s, strategy))
                                    positions[asset] = {
                                        "asset": asset,
                                        "entry": entry_fill,
                                        "stop": stop_val,
                                        "direction": "short",
                                        "setup": setup_s,
                                        "entry_i": i,
                                        "entry_ts": bar_ts,
                                        "base_size": base_size,
                                        "adaptive_at_entry": adaptive_mult,
                                    }
                                    action = "enter"
                                    direction = "short"
                                    setup = setup_s
                                    final_size = base_size * adaptive_mult
                                    reason = (f"short {setup_s} "
                                              f"adaptive={adaptive_mult:.2f}")
                            else:
                                action = "skip"
                                reason = f"funding_block: {gate['reason']}"

            # Realised PnL up to (but not including) this bar.
            rpnl = memory.realized_pnl_before(bar_ts)
            cur_pos = positions[asset]
            pos_state = ("flat" if cur_pos is None
                         else f"open_{cur_pos['direction']}")
            decisions.append({
                "timestamp": bar_ts.isoformat(),
                "asset": asset,
                "action": action,
                "direction": direction,
                "setup": setup,
                "signal_state": signal_state,
                "base_size": base_size_logged,
                "adaptive_multiplier": adaptive_mult,
                "final_size": final_size,
                "reason": reason,
                "rolling_pf_10": (mults["rolling_pf"]
                                   if mults["rolling_pf"] is not None else ""),
                "consecutive_losses": mults["consec_streak"],
                "stop_cluster_count": mults["stop_count"],
                "vol_sizing_multiplier": mults["vol_mult"],
                "funding_decision": funding_decision,
                "position_state": pos_state,
                "realized_pnl_to_date": rpnl,
            })

    # End-of-data — close any open positions at the last bar's close.
    last_ts = common_index[-1]
    for asset, position in list(positions.items()):
        if position is None:
            continue
        ind = ind_by_asset[asset]
        last_close = float(ind.iloc[-1]["close"])
        direction = position["direction"]
        if direction == "long":
            exit_fill = last_close * (1.0 - slippage)
            gross = (exit_fill - position["entry"]) / position["entry"]
        else:
            exit_fill = last_close * (1.0 + slippage)
            gross = (position["entry"] - exit_fill) / position["entry"]
        effective = position["base_size"] * position["adaptive_at_entry"]
        net = (gross - 2 * fee) * effective
        bars_held = (len(common_index) - 1) - position["entry_i"]
        trade = {
            "asset": asset,
            "direction": direction,
            "entry_time": position["entry_ts"].isoformat(),
            "exit_time": last_ts.isoformat(),
            "exit_ts": last_ts,
            "entry_price": position["entry"],
            "exit_price": exit_fill,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "base_size": position["base_size"],
            "adaptive_multiplier": position["adaptive_at_entry"],
            "final_size": effective,
            "exit_reason": "end_of_data",
            "setup": position["setup"],
            "bars_held": bars_held,
        }
        trades.append(trade)
        memory.record_close(asset, trade)
        positions[asset] = None

    meta = {
        "rule": rule,
        "assets": assets,
        "n_bars": n_bars,
        "span_start": common_index[0].isoformat(),
        "span_end": common_index[-1].isoformat(),
        "n_trades": len(trades),
    }
    log(f"[{rule}] done. {len(trades)} closed trades, "
        f"{len(decisions)} decision rows.")
    return {
        "decisions": decisions,
        "trades": trades,
        "meta": meta,
        "indicators": ind_by_asset,
        "overlay": overlay,
    }


# ===========================================================================
# Metrics for the window comparison
# ===========================================================================

def _filter_trades_window(
    trades: list[dict], cutoff: pd.Timestamp,
) -> list[dict]:
    """Return trades whose ``exit_time`` >= cutoff (inclusive)."""
    out = []
    for t in trades:
        exit_ts = pd.Timestamp(t["exit_time"])
        if exit_ts >= cutoff:
            out.append(t)
    return out


def _compute_metrics(trades: list[dict]) -> dict:
    """Compute the spec's metric set on a trade list."""
    if not trades:
        return {
            "trade_count": 0, "total_return": 0.0, "max_dd": 0.0,
            "profit_factor": 0.0, "win_rate": 0.0,
            "average_size_multiplier": 0.0, "return_per_exposure": 0.0,
            "worst_3mo": 0.0, "latest_3mo": 0.0,
        }
    # Time-ordered equity curve
    ordered = sorted(trades, key=lambda t: pd.Timestamp(t["exit_time"]))
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for t in ordered:
        equity *= 1.0 + t["net_return_pct"]
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    total_return = equity - 1.0
    wins = [t["net_return_pct"] for t in ordered if t["net_return_pct"] > 0]
    losses = [t["net_return_pct"] for t in ordered if t["net_return_pct"] <= 0]
    pf = (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf")
    win_rate = len(wins) / len(ordered)
    mults = [t["adaptive_multiplier"] for t in ordered]
    avg_mult = float(np.mean(mults)) if mults else 1.0
    ret_per_exp = total_return / avg_mult if avg_mult > 0 else 0.0
    # Worst / latest 3-month windows
    df = pd.DataFrame({
        "exit_time": [pd.Timestamp(t["exit_time"]) for t in ordered],
        "net_return_pct": [t["net_return_pct"] for t in ordered],
    })
    df = df.set_index("exit_time").sort_index()
    if not df.empty:
        # rolling 90-day net cumulative return
        rolling = df["net_return_pct"].resample("D").sum().rolling(90,
                                                                   min_periods=1).sum()
        worst_3mo = float(rolling.min())
        # latest 3 months by exit_time
        last = df.index.max()
        cutoff = last - pd.Timedelta(days=90)
        latest = df.loc[df.index >= cutoff, "net_return_pct"].sum()
    else:
        worst_3mo = 0.0
        latest = 0.0
    return {
        "trade_count": len(ordered),
        "total_return": total_return,
        "max_dd": max_dd,
        "profit_factor": (pf if pf != float("inf") else 9999.0),
        "win_rate": win_rate,
        "average_size_multiplier": avg_mult,
        "return_per_exposure": ret_per_exp,
        "worst_3mo": worst_3mo,
        "latest_3mo": float(latest),
    }


def _slice_by_window(
    trades: list[dict], span_end: pd.Timestamp, months: int,
) -> list[dict]:
    cutoff = span_end - pd.DateOffset(months=months)
    return _filter_trades_window(trades, cutoff)


# ===========================================================================
# CSV writers
# ===========================================================================

DECISION_COLUMNS = [
    "timestamp", "asset", "action", "direction", "setup", "signal_state",
    "base_size", "adaptive_multiplier", "final_size", "reason",
    "rolling_pf_10", "consecutive_losses", "stop_cluster_count",
    "vol_sizing_multiplier", "funding_decision", "position_state",
    "realized_pnl_to_date",
]

TRADE_COLUMNS = [
    "asset", "direction", "entry_time", "exit_time", "entry_price",
    "exit_price", "gross_return_pct", "net_return_pct", "base_size",
    "adaptive_multiplier", "final_size", "exit_reason",
]


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})


# ===========================================================================
# CLI
# ===========================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(STATE_DIR /
                    "live_multiasset_long_short_funding.yaml"))
    ap.add_argument("--months", type=int, default=24)
    ap.add_argument("--adaptive-rule", default="none", choices=ADAPTIVE_RULES)
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--compare-all-rules", action="store_true",
                    help="Run every rule and emit a per-rule per-window "
                         "metrics CSV in addition to the per-rule artifacts.")
    ap.add_argument("--refit-train-months", type=int,
                    default=DEFAULT_REFIT_TRAIN_MONTHS,
                    help="vol_sizing: months of prior data used to fit "
                         "quartile bands.")
    ap.add_argument("--refit-every-bars", type=int,
                    default=DEFAULT_REFIT_EVERY_BARS,
                    help="vol_sizing: bars between quartile-band refits.")
    ap.add_argument("--fee", type=float, default=RESEARCH_FEE_PER_SIDE)
    ap.add_argument("--slippage", type=float, default=RESEARCH_SLIPPAGE)
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    cfg = yaml.safe_load(open(cfg_path))
    cfg_dir = cfg_path.parent

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.compare_all_rules:
        # Load and compute indicators + overlay ONCE; reuse across rules.
        log("compare-all-rules: loading shared indicators + funding overlay …")
        assets = list(cfg["assets"])
        timeframe = cfg.get("timeframe", "4h")
        strategy_path = _resolve_path(cfg["strategy"], cfg_dir)
        strategy = yaml.safe_load(open(strategy_path))
        raw = _load_assets(assets, args.months, timeframe)
        cached_indicators = {
            a: signals.compute_indicators(df, strategy) for a, df in raw.items()
        }
        funding_cfg = cfg.get("funding_filter") or {}
        if funding_cfg.get("enabled"):
            cached_overlay = LiveFundingOverlay(
                assets,
                percentile_window_bars=int(funding_cfg.get(
                    "percentile_window_bars", 180)),
                n_months_history=max(args.months + 6, 36),
                timeframe=timeframe,
            )
        else:
            cached_overlay = None

        all_results: dict[str, dict] = {}
        for rule in ADAPTIVE_RULES:
            res = run_simulation(
                rule=rule, cfg=cfg, cfg_dir=cfg_dir,
                n_months=args.months,
                fee=args.fee, slippage=args.slippage,
                refit_train_months=args.refit_train_months,
                refit_every_bars=args.refit_every_bars,
                cached_indicators=cached_indicators,
                cached_overlay=cached_overlay,
            )
            all_results[rule] = res
            dec_path = out_dir / f"online_walk_forward_decisions_{rule}_{ts}.csv"
            trd_path = out_dir / f"online_walk_forward_trades_{rule}_{ts}.csv"
            _write_csv(dec_path, res["decisions"], DECISION_COLUMNS)
            _write_csv(trd_path, res["trades"], TRADE_COLUMNS)
            log(f"  -> {dec_path}")
            log(f"  -> {trd_path}")

        # Window comparison
        windows = [3, 6, 12, 24]
        cmp_path = out_dir / f"online_walk_forward_comparison_{ts}.csv"
        cmp_rows: list[dict] = []
        for rule in ADAPTIVE_RULES:
            res = all_results[rule]
            span_end = pd.Timestamp(res["meta"]["span_end"])
            for w in windows:
                if w == max(windows):
                    sliced = res["trades"]
                else:
                    sliced = _slice_by_window(res["trades"], span_end, w)
                m = _compute_metrics(sliced)
                cmp_rows.append({
                    "rule": rule, "window_months": w,
                    **m,
                })
        _write_csv(
            cmp_path, cmp_rows,
            ["rule", "window_months", "trade_count", "total_return",
             "max_dd", "profit_factor", "win_rate",
             "average_size_multiplier", "return_per_exposure",
             "worst_3mo", "latest_3mo"],
        )
        log(f"wrote comparison CSV -> {cmp_path}")
        # Print a compact summary table to stdout.
        log("")
        log("=== comparison summary (windows in months) ===")
        for w in windows:
            log(f"  window={w}mo")
            for rule in ADAPTIVE_RULES:
                rows = [r for r in cmp_rows
                        if r["rule"] == rule and r["window_months"] == w]
                if not rows:
                    continue
                r = rows[0]
                log(f"    {rule:<24}  n={r['trade_count']:3d}  "
                    f"ret={r['total_return']*100:+7.2f}%  "
                    f"DD={r['max_dd']*100:5.2f}%  "
                    f"PF={r['profit_factor']:.2f}  "
                    f"mult={r['average_size_multiplier']:.3f}")
        return 0

    # Single-rule mode
    res = run_simulation(
        rule=args.adaptive_rule, cfg=cfg, cfg_dir=cfg_dir,
        n_months=args.months, fee=args.fee, slippage=args.slippage,
        refit_train_months=args.refit_train_months,
        refit_every_bars=args.refit_every_bars,
    )
    dec_path = out_dir / f"online_walk_forward_decisions_{ts}.csv"
    trd_path = out_dir / f"online_walk_forward_trades_{ts}.csv"
    _write_csv(dec_path, res["decisions"], DECISION_COLUMNS)
    _write_csv(trd_path, res["trades"], TRADE_COLUMNS)
    log(f"wrote decisions CSV -> {dec_path}")
    log(f"wrote trades CSV    -> {trd_path}")
    metrics = _compute_metrics(res["trades"])
    log(f"summary: n={metrics['trade_count']}  "
        f"ret={metrics['total_return']*100:+.2f}%  "
        f"DD={metrics['max_dd']*100:.2f}%  "
        f"PF={metrics['profit_factor']:.2f}  "
        f"win={metrics['win_rate']*100:.1f}%  "
        f"meanMult={metrics['average_size_multiplier']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
