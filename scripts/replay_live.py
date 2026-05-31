#!/usr/bin/env python3
"""Replay-mode worker — feed historical bars through the live engine.

Same ``signals`` module the live worker uses; same entry/exit logic;
same strategy yaml. Difference: instead of polling a live price feed
every 10 seconds, this script walks historical bars at a configurable
speed.

Two modes:

  1) Legacy ``--strategy`` mode (single asset, single strategy yaml).
     Unchanged behaviour. Example:

         uv run python scripts/replay_live.py \\
             --strategy state/strategy_supertrend_long_short.yaml \\
             --n-months 3 --bars-per-second 20 --quiet-flat

  2) ``--config`` mode (Issue #26) — replay the same multi-asset live
     config the live worker reads, including funding filter:

         uv run python scripts/replay_live.py \\
             --config state/live_multiasset_long_short_funding.yaml \\
             --n-months 24 --bars-per-second 20 --quiet-flat \\
             --trades-out results/replay_trades_<ts>.csv

This is research / educational tooling. It does NOT trade, does NOT
write to ``state/``, and is independent of the live worker.

Live-semantics match (Issue #24): in both modes, entries and
SuperTrend flip / time exits evaluate on the most recently CLOSED
bar; stops can still fire intra-bar on the current bar's low / high.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import STATE_DIR
from hermes_trading import data as data_mod
from hermes_trading import display as display_mod
from hermes_trading import signals
from hermes_trading.multi_loop import (
    LiveFundingOverlay,
    LiveVolSizingOverlay,
    VOL_SIZING_WINDOW_BARS_DEFAULT,
    VOL_SIZING_TRAIN_MONTHS_DEFAULT,
    VOL_SIZING_MULT_LOW_DEFAULT,
    VOL_SIZING_MULT_MID_DEFAULT,
    VOL_SIZING_MULT_HIGH_DEFAULT,
    evaluate_funding_gate,
    can_enter,
)

GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GRAY = "\033[90m"
RESET = "\033[0m"


# ---------- shared formatting --------------------------------------------------

def _fmt_pos(position: dict | None, last: float) -> str:
    """Legacy single-asset position string (kept for --strategy mode)."""
    if position is None:
        return "flat"
    if position["direction"] == "long":
        chg = (last - position["entry"]) / position["entry"]
        arrow = "↑"
    else:
        chg = (position["entry"] - last) / position["entry"]
        arrow = "↓"
    pnl = chg * position["size_multiplier"]
    color = GREEN if pnl >= 0 else RED
    return (f"{arrow}{position['direction']} @ {position['entry']:.2f} "
            f"{color}{pnl * 100:+.3f}%{RESET}")


def _asset_label_from_symbol(symbol: str) -> str:
    """Turn ``BTCUSDT`` into ``BTC/USDT`` (live-style asset label)."""
    if len(symbol) >= 6:
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def _symbol_from_asset(asset: str) -> str:
    """Turn ``BTC/USDT`` into ``BTCUSDT`` (Binance Vision symbol)."""
    return asset.replace("/", "")


# ---------- legacy --strategy mode (unchanged behaviour) ----------------------

def _run_strategy_replay(args: argparse.Namespace) -> int:
    """Original single-asset / single-strategy replay loop. Behaviour
    preserved byte-for-byte from the pre-Issue-#26 script so existing
    invocations and screenshots reproduce."""

    strategy = yaml.safe_load(open(args.strategy))
    version = strategy.get("version", "?")

    print(f"{CYAN}Loading {args.symbol} {args.n_months}mo {args.timeframe} …{RESET}")
    df = data_mod.resample(
        data_mod.load_klines(args.symbol, n_months=args.n_months),
        args.timeframe,
    )
    print(f"{CYAN}{len(df)} bars  "
          f"span: {df.index[0].date()} -> {df.index[-1].date()}{RESET}")
    print(f"{CYAN}Strategy: {Path(args.strategy).name}  v{version}  "
          f"warmup={args.warmup_bars} bars  "
          f"speed={args.bars_per_second} bars/sec{RESET}")
    print()

    ind = signals.compute_indicators(df, strategy)
    base_size = float(strategy["risk"].get("position_size_r", 0.5))

    position: dict | None = None
    trades: list[dict] = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    last_state = None

    sleep_per_bar = 1.0 / max(args.bars_per_second, 0.001)
    asset_label = _asset_label_from_symbol(args.symbol)

    records = ind.to_dict("records")
    for i, row in enumerate(records):
        if i < args.warmup_bars:
            continue

        ts = ind.index[i]
        last = float(row["close"])
        rsi = row.get("rsi")
        rsi_str = f"{rsi:.1f}" if pd.notna(rsi) else "n/a"
        ts_str = ts.strftime("%Y-%m-%d %H:%M")

        # ---- exit check ----
        exit_event = None
        if position is not None:
            bars_held = i - position["entry_i"]
            if position["direction"] == "long":
                reason = signals.long_exit(row, position, strategy, bars_held)
            else:
                reason = signals.short_exit(row, position, strategy, bars_held)
            if reason:
                if position["direction"] == "long":
                    exit_fill = (position["stop"] * (1 - args.slippage)
                                 if reason == "stop"
                                 else last * (1 - args.slippage))
                    gross = (exit_fill - position["entry"]) / position["entry"]
                else:
                    exit_fill = (position["stop"] * (1 + args.slippage)
                                 if reason == "stop"
                                 else last * (1 + args.slippage))
                    gross = (position["entry"] - exit_fill) / position["entry"]
                effective_size = base_size * position["size_multiplier"]
                net = (gross - 2 * args.fee) * effective_size
                equity *= 1.0 + net
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
                exit_event = {
                    "reason": reason,
                    "net": net,
                    "bars": bars_held,
                    "exit_price": exit_fill,
                    "direction": position["direction"],
                    "setup": position["setup"],
                }
                trades.append({
                    "entry_ts": position["entry_ts"],
                    "exit_ts": ts,
                    "direction": position["direction"],
                    "setup": position["setup"],
                    "net": net,
                    "reason": reason,
                    "bars": bars_held,
                })
                position = None

        # ---- entry check ----
        enter_event = None
        if position is None:
            setup_l = signals.long_entry(row, strategy)
            if setup_l:
                init_stop = signals.initial_stop(row, setup_l, strategy)
                position = {
                    "asset": asset_label,
                    "entry": last * (1 + args.slippage),
                    "setup": setup_l,
                    "direction": "long",
                    "entry_i": i,
                    "entry_ts": ts,
                    "stop": init_stop,
                    "size_multiplier": 1.0,
                }
                enter_event = position
            else:
                setup_s = (signals.short_entry(row, strategy)
                           if strategy.get("shorts", {}).get("enabled") else None)
                if setup_s:
                    init_stop = signals.initial_stop_short(row, setup_s, strategy)
                    position = {
                        "asset": asset_label,
                        "entry": last * (1 - args.slippage),
                        "setup": setup_s,
                        "direction": "short",
                        "entry_i": i,
                        "entry_ts": ts,
                        "stop": init_stop,
                        "size_multiplier": 1.0,
                    }
                    enter_event = position

        # ---- output ----
        cur_state = "flat" if position is None else position["direction"]
        is_state_change = exit_event is not None or enter_event is not None
        if args.quiet_flat and not is_state_change and cur_state == "flat" == last_state:
            time.sleep(sleep_per_bar)
            continue

        prefix = f"{GRAY}[{ts_str}]{RESET}"
        header = f"{prefix} {asset_label} {last:.2f} rsi={rsi_str} v{version}"
        if exit_event:
            color = GREEN if exit_event["net"] >= 0 else RED
            print(f"{header} {color}EXIT {exit_event['direction']} "
                  f"{exit_event['setup']} @ {exit_event['exit_price']:.2f} "
                  f"net={exit_event['net'] * 100:+.3f}% "
                  f"reason={exit_event['reason']} "
                  f"bars={exit_event['bars']}{RESET}")
        if enter_event:
            color = GREEN if enter_event["direction"] == "long" else RED
            print(f"{header} {color}ENTER {enter_event['direction']} "
                  f"{enter_event['setup']} @ {enter_event['entry']:.2f} "
                  f"stop={enter_event['stop']:.2f}{RESET}")
        if not exit_event and not enter_event:
            print(f"{header} pos={_fmt_pos(position, last)}")

        last_state = cur_state
        time.sleep(sleep_per_bar)

    # close any open position at end-of-data
    if position is not None:
        last = float(records[-1]["close"])
        ts = ind.index[-1]
        if position["direction"] == "long":
            exit_fill = last * (1 - args.slippage)
            gross = (exit_fill - position["entry"]) / position["entry"]
        else:
            exit_fill = last * (1 + args.slippage)
            gross = (position["entry"] - exit_fill) / position["entry"]
        effective_size = base_size * position["size_multiplier"]
        net = (gross - 2 * args.fee) * effective_size
        equity *= 1.0 + net
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        print(f"{GRAY}[{ts.strftime('%Y-%m-%d %H:%M')}]{RESET} "
              f"{asset_label} {last:.2f} {YELLOW}EXIT (end of data) "
              f"{position['direction']} net={net*100:+.3f}%{RESET}")
        trades.append({"entry_ts": position["entry_ts"], "exit_ts": ts,
                       "direction": position["direction"],
                       "setup": position["setup"], "net": net,
                       "reason": "end",
                       "bars": len(records) - 1 - position["entry_i"]})

    # ---- summary ----
    print()
    print(f"{CYAN}{'='*70}{RESET}")
    print(f"{CYAN}REPLAY SUMMARY  {asset_label}  "
          f"{ind.index[args.warmup_bars].date()} -> {ind.index[-1].date()}{RESET}")
    print(f"{CYAN}{'='*70}{RESET}")
    rets = [t["net"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    total = equity - 1.0
    win_rate = len(wins) / len(trades) if trades else 0.0
    pf = sum(wins) / abs(sum(losses)) if sum(losses) != 0 else float("inf")
    print(f"  trades            {len(trades)}")
    color_total = GREEN if total >= 0 else RED
    print(f"  total return      {color_total}{total * 100:+.2f}%{RESET}")
    print(f"  max drawdown      {max_dd * 100:.2f}%")
    print(f"  win rate          {win_rate * 100:.1f}%")
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    print(f"  profit factor     {pf_str}")
    by_setup: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for t in trades:
        by_setup[t["setup"]] = by_setup.get(t["setup"], 0) + 1
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    if by_setup:
        print(f"  by setup          {by_setup}")
    if by_reason:
        print(f"  by reason         {by_reason}")
    print()
    print(f"{GRAY}Note: replay is in-sample on the full window. For OOS "
          f"adoption-quality numbers, run scripts/run_*.py (walk-forward).{RESET}")
    return 0


# ---------- new --config mode (Issue #26) -------------------------------------

# Trade-CSV columns: the original Issue #26 spec PLUS the Issue #34
# vol_sizing fields appended at the end so existing consumers reading the
# first 12 columns remain compatible. DO NOT rename existing columns.
TRADE_CSV_COLUMNS = [
    # Issue #26 (original) columns:
    "asset", "direction", "entry_time", "exit_time",
    "entry_price", "exit_price", "return_pct", "net_return_pct",
    "setup", "exit_reason", "bars_held", "funding_decision",
    # Issue #34 vol_sizing additions (None when vol_sizing disabled):
    "base_size", "vol_multiplier", "final_size",
    "realized_vol_24", "vol_bucket",
    "vol_q1", "vol_q2", "vol_q3",
]


def _resolve_path(cfg_value: str, cfg_dir: Path) -> Path:
    """Strategy paths in the live config are written relative to the
    repo root (e.g. ``state/strategy_supertrend.yaml``). Accept both
    that form and absolute paths."""
    p = Path(cfg_value)
    if p.is_absolute():
        return p
    if str(p).startswith("state/"):
        return ROOT / p
    return cfg_dir / p


def _load_assets_indicators(
    assets: list[str], n_months: int, timeframe: str, strategy: dict
) -> dict[str, pd.DataFrame]:
    """Per-asset indicator DataFrames, keyed by asset label
    (``BTC/USDT``). Errors on per-asset load are fatal — replay can't
    proceed without prices for a configured asset."""
    out: dict[str, pd.DataFrame] = {}
    for asset in assets:
        sym = _symbol_from_asset(asset)
        print(f"{CYAN}Loading {sym} {n_months}mo {timeframe} …{RESET}")
        df = data_mod.resample(
            data_mod.load_klines(sym, n_months=n_months), timeframe,
        )
        ind = signals.compute_indicators(df, strategy)
        print(f"{CYAN}  {len(ind)} bars  "
              f"span: {ind.index[0].date()} -> {ind.index[-1].date()}{RESET}")
        out[asset] = ind
    return out


def _funding_decision_for_heartbeat(
    funding_state: dict | None,
    block_long: float,
    block_short: float,
) -> tuple[str, str]:
    """Direction-agnostic "what would block right now" decision used for
    the per-bar funding display line. Returns ``(decision, label)``."""
    if not funding_state or not funding_state.get("available"):
        return "missing_data", "missing_data"
    pct = funding_state.get("percentile")
    if pct is None:
        return "missing_data", "missing_data"
    if pct >= block_long:
        return "block_long", "block_long"
    if pct <= block_short:
        return "block_short", "block_short"
    return "allow", "allow"


def _run_config_replay(args: argparse.Namespace) -> int:
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    cfg = yaml.safe_load(open(cfg_path))
    cfg_dir = cfg_path.parent

    assets: list[str] = list(cfg["assets"])
    timeframe: str = cfg.get("timeframe", "4h")
    max_open = int(cfg.get("max_open_positions", len(assets)))
    size_per_asset = float(cfg.get("size_per_asset", 1.0 / max(len(assets), 1)))
    strategy_path = _resolve_path(cfg["strategy"], cfg_dir)
    strategy = yaml.safe_load(open(strategy_path))
    version = strategy.get("version", "?")

    funding_cfg = cfg.get("funding_filter") or {}
    funding_enabled = bool(funding_cfg.get("enabled"))
    funding_block_long = float(funding_cfg.get("block_long_above_pct", 95.0))
    funding_block_short = float(funding_cfg.get("block_short_below_pct", 5.0))
    funding_window_bars = int(funding_cfg.get("percentile_window_bars", 180))
    funding_missing_policy = str(funding_cfg.get("on_missing_data", "fail_open"))

    # Issue #34 — vol_sizing replay parity. Reads the SAME ``vol_sizing:``
    # config block Issue #33 added to the live yaml. Disabled by default;
    # the default live yaml (``state/live_multiasset_long_short_funding.yaml``)
    # has no vol_sizing block, so replay behaviour for that yaml is
    # byte-for-byte unchanged.
    vol_cfg = cfg.get("vol_sizing") or {}
    vol_sizing_enabled = bool(vol_cfg.get("enabled"))
    vol_window_bars = int(vol_cfg.get("window_bars",
                                       VOL_SIZING_WINDOW_BARS_DEFAULT))
    vol_train_months = int(vol_cfg.get("train_months",
                                        VOL_SIZING_TRAIN_MONTHS_DEFAULT))
    vol_mult_low = float(vol_cfg.get("mult_low",
                                      VOL_SIZING_MULT_LOW_DEFAULT))
    vol_mult_mid = float(vol_cfg.get("mult_mid",
                                      VOL_SIZING_MULT_MID_DEFAULT))
    vol_mult_high = float(vol_cfg.get("mult_high",
                                       VOL_SIZING_MULT_HIGH_DEFAULT))

    print(f"{CYAN}{'='*70}{RESET}")
    print(f"{CYAN}REPLAY (multi-asset config mode) — {cfg_path.name}{RESET}")
    print(f"{CYAN}  assets={assets}  timeframe={timeframe}  "
          f"max_open={max_open}  size_per_asset={size_per_asset}{RESET}")
    print(f"{CYAN}  strategy={strategy_path.name}  v{version}  "
          f"warmup={args.warmup_bars} bars  "
          f"speed={args.bars_per_second} bars/sec{RESET}")
    print(f"{CYAN}  funding_filter={'ENABLED' if funding_enabled else 'disabled'}"
          + (f"  long_block>={funding_block_long}  "
             f"short_block<={funding_block_short}  "
             f"window={funding_window_bars}" if funding_enabled else "")
          + f"{RESET}")
    print(f"{CYAN}  vol_sizing={'ENABLED' if vol_sizing_enabled else 'disabled'}"
          + (f"  window_bars={vol_window_bars}  "
             f"train_months={vol_train_months}  "
             f"mult=({vol_mult_low}/{vol_mult_mid}/{vol_mult_high})"
             if vol_sizing_enabled else "")
          + f"{RESET}")
    print()

    # ---- load per-asset OHLCV + indicators ----
    ind_by_asset = _load_assets_indicators(assets, args.n_months, timeframe, strategy)

    # ---- funding overlay (uses live worker's loader; no replay-specific code) ----
    funding_overlay: LiveFundingOverlay | None = None
    if funding_enabled:
        funding_overlay = LiveFundingOverlay(
            assets, percentile_window_bars=funding_window_bars,
            n_months_history=max(args.n_months + 6, 36),
            timeframe=timeframe,
        )

    # ---- vol_sizing overlay (Issue #34) — reuses the exact LiveVolSizingOverlay
    # ---- class the live worker uses, including no-future-leak train-window
    # ---- quartile thresholds. No replay-specific math.
    vol_overlay: LiveVolSizingOverlay | None = None
    if vol_sizing_enabled:
        vol_overlay = LiveVolSizingOverlay(
            assets, timeframe=timeframe,
            window_bars=vol_window_bars,
            train_months=vol_train_months,
            mult_low=vol_mult_low,
            mult_mid=vol_mult_mid,
            mult_high=vol_mult_high,
            # Load enough history that even the earliest replay bar has a
            # populated train window.
            load_history_months=max(args.n_months + vol_train_months + 3,
                                     vol_train_months + 3),
        )

    # ---- timeline union (so BTC and ETH advance in lockstep) ----
    timeline = sorted(set().union(
        *[set(ind.index) for ind in ind_by_asset.values()]
    ))

    # Pre-compute per-asset positional index of each timestamp so we can
    # cheaply look up signal_row = iloc[i-1] / display_row = iloc[i].
    pos_index: dict[str, dict[pd.Timestamp, int]] = {
        a: {ts: i for i, ts in enumerate(ind.index)}
        for a, ind in ind_by_asset.items()
    }

    positions_by_asset: dict[str, dict | None] = {a: None for a in assets}
    closed_trades: list[dict] = []
    realized_pnl_pct = 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    max_concurrent = 0
    sleep_per_bar = 1.0 / max(args.bars_per_second, 0.001)
    last_state_changed_ts: pd.Timestamp | None = None
    any_state_change_ever = False

    # ---- main loop: one timestamp at a time, every asset processed in turn ----
    for ts in timeline:
        ts_str = ts.strftime("%Y-%m-%d %H:%M")
        any_state_change_this_ts = False
        per_asset_lines: list[str] = []

        for asset in assets:
            ind = ind_by_asset[asset]
            i = pos_index[asset].get(ts)
            if i is None or i < args.warmup_bars:
                continue
            # Need at least one closed bar to drive entry/flip decisions.
            if i < 1:
                continue

            display_row = ind.iloc[i].to_dict()
            display_row["ts"] = ind.index[i]
            signal_row = ind.iloc[i - 1].to_dict()
            signal_row["ts"] = ind.index[i - 1]
            last = float(display_row["close"])

            position = positions_by_asset[asset]
            funding_state = None
            if funding_overlay is not None:
                funding_state = funding_overlay.state_at(asset, signal_row["ts"])

            # Issue #34 — vol_sizing per-bar lookup. Same signal-bar timestamp
            # the funding overlay uses; same semantics as multi_loop.run.
            vol_state = None
            if vol_overlay is not None:
                vol_state = vol_overlay.state_at(asset, signal_row["ts"])
            current_vol_mult = (float(vol_state["multiplier"])
                                if vol_state is not None
                                else 1.0)

            exit_event: dict | None = None
            enter_event: dict | None = None
            funding_block_msg: str | None = None

            # ----- exit logic (signal_row drives flip / time; display_row low/high
            #       drives intra-bar stop) -----
            if position is not None:
                bars_held = i - position["entry_i"]
                direction = position["direction"]
                if direction == "long":
                    reason = signals.long_exit(signal_row, position, strategy, bars_held)
                else:
                    reason = signals.short_exit(signal_row, position, strategy, bars_held)
                if reason is None:
                    if direction == "long":
                        dlow = display_row.get("low")
                        if (dlow is not None and not pd.isna(dlow)
                                and float(dlow) <= position["stop"]):
                            reason = "stop"
                    else:
                        dhigh = display_row.get("high")
                        if (dhigh is not None and not pd.isna(dhigh)
                                and float(dhigh) >= position["stop"]):
                            reason = "stop"
                if reason:
                    if direction == "long":
                        exit_fill = (position["stop"] * (1 - args.slippage)
                                     if reason == "stop"
                                     else last * (1 - args.slippage))
                        gross = (exit_fill - position["entry"]) / position["entry"]
                    else:
                        exit_fill = (position["stop"] * (1 + args.slippage)
                                     if reason == "stop"
                                     else last * (1 + args.slippage))
                        gross = (position["entry"] - exit_fill) / position["entry"]
                    net = (gross - 2 * args.fee) * position["size"]
                    equity *= 1.0 + net
                    peak = max(peak, equity)
                    max_dd = max(max_dd, (peak - equity) / peak)
                    realized_pnl_pct += net * 100.0
                    closed_trades.append({
                        "asset": asset,
                        "direction": direction,
                        "entry_time": position["entry_ts"].isoformat(),
                        "exit_time": ts.isoformat(),
                        "entry_price": position["entry"],
                        "exit_price": exit_fill,
                        "return_pct": gross,
                        "net_return_pct": net,
                        "setup": position["setup"],
                        "exit_reason": reason,
                        "bars_held": bars_held,
                        "funding_decision": position.get("funding_decision_at_entry",
                                                         "n/a"),
                        # Issue #34 — vol_sizing context locked at entry
                        "base_size": position.get("base_size_at_entry"),
                        "vol_multiplier": position.get("vol_multiplier_at_entry"),
                        "final_size": float(position["size"]),
                        "realized_vol_24": position.get("realized_vol_24_at_entry"),
                        "vol_bucket": position.get("vol_bucket_at_entry"),
                        "vol_q1": position.get("vol_q1_at_entry"),
                        "vol_q2": position.get("vol_q2_at_entry"),
                        "vol_q3": position.get("vol_q3_at_entry"),
                    })
                    exit_event = {
                        "direction": direction,
                        "setup": position["setup"],
                        "exit_price": exit_fill,
                        "net": net,
                        "reason": reason,
                        "bars": bars_held,
                    }
                    positions_by_asset[asset] = None
                    position = None

            # ----- entry logic (signal_row only) -----
            if position is None:
                allowed, gate_reason = can_enter(asset, positions_by_asset, max_open)
                if allowed:
                    setup_l = signals.long_entry(signal_row, strategy)
                    opened = False
                    funding_decision = "n/a"
                    if setup_l:
                        if funding_overlay is not None:
                            gate = evaluate_funding_gate(
                                "long",
                                funding_state.get("percentile") if funding_state else None,
                                block_long_above_pct=funding_block_long,
                                block_short_below_pct=funding_block_short,
                                on_missing_data=funding_missing_policy,
                            )
                            funding_decision = gate["decision"]
                            if gate["allow"]:
                                opened = True
                            else:
                                funding_block_msg = (f"funding_filter {gate['decision']} "
                                                     f"({gate['reason']})")
                        else:
                            opened = True
                        if opened:
                            entry_fill = last * (1 + args.slippage)
                            stop_val = float(
                                signals.initial_stop(signal_row, setup_l, strategy)
                            )
                            # Issue #34 — vol_sizing multiplier locks at entry.
                            # final_size = size_per_asset * vol_mult; funding_allow
                            # is already 1 here.
                            final_size = size_per_asset * current_vol_mult
                            new_pos = {
                                "asset": asset,
                                "entry": entry_fill,
                                "direction": "long",
                                "setup": setup_l,
                                "stop": stop_val,
                                "entry_i": i,
                                "entry_ts": ts,
                                "size": final_size,
                                "base_size_at_entry": size_per_asset,
                                "vol_multiplier_at_entry": current_vol_mult,
                                "realized_vol_24_at_entry": (
                                    vol_state.get("realized_vol") if vol_state else None),
                                "vol_bucket_at_entry": (
                                    vol_state.get("bucket") if vol_state else None),
                                "vol_q1_at_entry": (
                                    vol_state.get("q25") if vol_state else None),
                                "vol_q2_at_entry": (
                                    vol_state.get("q50") if vol_state else None),
                                "vol_q3_at_entry": (
                                    vol_state.get("q75") if vol_state else None),
                                "funding_decision_at_entry": funding_decision,
                                "funding_rate_at_entry": (
                                    funding_state.get("rate") if funding_state else None),
                                "funding_percentile_at_entry": (
                                    funding_state.get("percentile") if funding_state else None),
                            }
                            positions_by_asset[asset] = new_pos
                            enter_event = {
                                "direction": "long",
                                "setup": setup_l,
                                "entry": entry_fill,
                                "stop": stop_val,
                                "funding_decision": funding_decision,
                                "vol_mult": current_vol_mult,
                                "final_size": final_size,
                            }
                    if (not opened and positions_by_asset[asset] is None
                            and strategy.get("shorts", {}).get("enabled")):
                        setup_s = signals.short_entry(signal_row, strategy)
                        if setup_s:
                            short_ok = True
                            funding_decision_s = "n/a"
                            if funding_overlay is not None:
                                gate = evaluate_funding_gate(
                                    "short",
                                    funding_state.get("percentile") if funding_state else None,
                                    block_long_above_pct=funding_block_long,
                                    block_short_below_pct=funding_block_short,
                                    on_missing_data=funding_missing_policy,
                                )
                                funding_decision_s = gate["decision"]
                                if not gate["allow"]:
                                    short_ok = False
                                    funding_block_msg = (
                                        f"funding_filter {gate['decision']} "
                                        f"({gate['reason']})")
                            if short_ok:
                                entry_fill = last * (1 - args.slippage)
                                stop_val = float(
                                    signals.initial_stop_short(signal_row, setup_s, strategy)
                                )
                                # Issue #34 — vol_sizing multiplier locks at entry.
                                final_size = size_per_asset * current_vol_mult
                                new_pos = {
                                    "asset": asset,
                                    "entry": entry_fill,
                                    "direction": "short",
                                    "setup": setup_s,
                                    "stop": stop_val,
                                    "entry_i": i,
                                    "entry_ts": ts,
                                    "size": final_size,
                                    "base_size_at_entry": size_per_asset,
                                    "vol_multiplier_at_entry": current_vol_mult,
                                    "realized_vol_24_at_entry": (
                                        vol_state.get("realized_vol") if vol_state else None),
                                    "vol_bucket_at_entry": (
                                        vol_state.get("bucket") if vol_state else None),
                                    "vol_q1_at_entry": (
                                        vol_state.get("q25") if vol_state else None),
                                    "vol_q2_at_entry": (
                                        vol_state.get("q50") if vol_state else None),
                                    "vol_q3_at_entry": (
                                        vol_state.get("q75") if vol_state else None),
                                    "funding_decision_at_entry": funding_decision_s,
                                    "funding_rate_at_entry": (
                                        funding_state.get("rate") if funding_state else None),
                                    "funding_percentile_at_entry": (
                                        funding_state.get("percentile") if funding_state else None),
                                }
                                positions_by_asset[asset] = new_pos
                                enter_event = {
                                    "direction": "short",
                                    "setup": setup_s,
                                    "entry": entry_fill,
                                    "stop": stop_val,
                                    "funding_decision": funding_decision_s,
                                    "vol_mult": current_vol_mult,
                                    "final_size": final_size,
                                }

            # ----- per-asset display line -----
            cur_pos = positions_by_asset[asset]
            state_changed = exit_event is not None or enter_event is not None
            if state_changed:
                any_state_change_this_ts = True
                any_state_change_ever = True

            if args.quiet_flat and not state_changed and cur_pos is None:
                # silent flat bar — no per-asset line emitted, but funding
                # display still suppressed (matches user's example output).
                continue

            tick_line = display_mod.format_supertrend_tick(
                asset=asset,
                close=last,
                supertrend_direction=display_row.get("supertrend_direction"),
                supertrend_line=display_row.get("supertrend_line"),
                strategy_version=version,
                position=(
                    {
                        "entry_price": cur_pos["entry"],
                        "size": cur_pos["size"],
                        "direction": cur_pos["direction"],
                        "setup": cur_pos["setup"],
                    } if cur_pos else None
                ),
                rsi=(float(display_row["rsi"])
                     if pd.notna(display_row.get("rsi")) else None),
                verbose=False,
            )
            per_asset_lines.append(f"{GRAY}[{ts_str}]{RESET} {tick_line}")

            if exit_event:
                color = GREEN if exit_event["net"] >= 0 else RED
                per_asset_lines.append(
                    f"{GRAY}[{ts_str}]{RESET} {asset} {color}EXIT {exit_event['direction']} "
                    f"{exit_event['setup']} @ {exit_event['exit_price']:.2f} "
                    f"net={exit_event['net'] * 100:+.3f}% "
                    f"reason={exit_event['reason']} bars={exit_event['bars']}{RESET}"
                )
            if enter_event:
                color = GREEN if enter_event["direction"] == "long" else RED
                # Issue #34: include final size + vol multiplier when vol_sizing
                # is on, matching the spec's example ENTER line.
                vol_suffix = ""
                if vol_overlay is not None:
                    vol_suffix = (f" size={enter_event['final_size']:.4f} "
                                  f"vol_mult={enter_event['vol_mult']:.2f}")
                per_asset_lines.append(
                    f"{GRAY}[{ts_str}]{RESET} {asset} {color}ENTER {enter_event['direction']} "
                    f"{enter_event['setup']} @ {enter_event['entry']:.2f} "
                    f"stop={enter_event['stop']:.2f}{vol_suffix}{RESET}"
                )
            if funding_overlay is not None and (state_changed or not args.quiet_flat):
                fs = funding_state or {"available": False}
                if fs.get("available"):
                    rate = fs.get("rate")
                    pct = fs.get("percentile")
                    decision, _ = _funding_decision_for_heartbeat(
                        fs, funding_block_long, funding_block_short
                    )
                    per_asset_lines.append(
                        f"  funding {asset} rate={rate*100:+.4f}% "
                        f"pct={pct:.1f} decision={decision}"
                    )
                else:
                    per_asset_lines.append(
                        f"  funding {asset} unavailable (fail_open)"
                    )
            # Issue #34 — vol_sizing verbose line on state-change bars, same
            # format the live worker uses.
            if vol_overlay is not None and (state_changed or not args.quiet_flat):
                vs = vol_state or {"available": False}
                if vs.get("available"):
                    rv = vs.get("realized_vol")
                    q1 = vs.get("q25"); q2 = vs.get("q50"); q3 = vs.get("q75")
                    per_asset_lines.append(
                        f"  vol {asset} rv24={rv*100:.2f}% "
                        f"bucket={vs.get('bucket')} mult={vs.get('multiplier'):.2f} "
                        f"q=[{q1*100:.2f}%,{q2*100:.2f}%,{q3*100:.2f}%]"
                    )
                else:
                    per_asset_lines.append(
                        f"  vol {asset} warmup / insufficient history; "
                        f"fail-open mult=1.00"
                    )
            if funding_block_msg:
                per_asset_lines.append(f"  blocked_by: {funding_block_msg}")

        # ---- portfolio status line (printed only when something happened) ----
        open_count = sum(1 for v in positions_by_asset.values() if v is not None)
        max_concurrent = max(max_concurrent, open_count)
        unrl_portfolio = 0.0
        for asset in assets:
            cur_pos = positions_by_asset[asset]
            if cur_pos is None:
                continue
            ind = ind_by_asset[asset]
            i = pos_index[asset].get(ts)
            if i is None:
                continue
            last = float(ind.iloc[i]["close"])
            entry = cur_pos["entry"]
            if cur_pos["direction"] == "long":
                chg = (last - entry) / entry
            else:
                chg = (entry - last) / entry
            unrl_portfolio += chg * cur_pos["size"]

        if per_asset_lines:
            for line in per_asset_lines:
                print(line)
            if not args.quiet_flat or any_state_change_this_ts:
                print(f"portfolio open={open_count}/{max_open}  "
                      f"realized={realized_pnl_pct:+.3f}%  "
                      f"unrealized={unrl_portfolio*100:+.3f}%")
                print()
            last_state_changed_ts = ts

        time.sleep(sleep_per_bar)

    # ---- end-of-data: close any open positions ----
    if any(v is not None for v in positions_by_asset.values()):
        for asset in assets:
            cur_pos = positions_by_asset[asset]
            if cur_pos is None:
                continue
            ind = ind_by_asset[asset]
            last_ts = ind.index[-1]
            last = float(ind.iloc[-1]["close"])
            direction = cur_pos["direction"]
            if direction == "long":
                exit_fill = last * (1 - args.slippage)
                gross = (exit_fill - cur_pos["entry"]) / cur_pos["entry"]
            else:
                exit_fill = last * (1 + args.slippage)
                gross = (cur_pos["entry"] - exit_fill) / cur_pos["entry"]
            net = (gross - 2 * args.fee) * cur_pos["size"]
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            realized_pnl_pct += net * 100.0
            bars_held = (len(ind) - 1) - cur_pos["entry_i"]
            closed_trades.append({
                "asset": asset,
                "direction": direction,
                "entry_time": cur_pos["entry_ts"].isoformat(),
                "exit_time": last_ts.isoformat(),
                "entry_price": cur_pos["entry"],
                "exit_price": exit_fill,
                "return_pct": gross,
                "net_return_pct": net,
                "setup": cur_pos["setup"],
                "exit_reason": "end_of_data",
                "bars_held": bars_held,
                "funding_decision": cur_pos.get("funding_decision_at_entry", "n/a"),
                # Issue #34 — vol_sizing context locked at entry
                "base_size": cur_pos.get("base_size_at_entry"),
                "vol_multiplier": cur_pos.get("vol_multiplier_at_entry"),
                "final_size": float(cur_pos["size"]),
                "realized_vol_24": cur_pos.get("realized_vol_24_at_entry"),
                "vol_bucket": cur_pos.get("vol_bucket_at_entry"),
                "vol_q1": cur_pos.get("vol_q1_at_entry"),
                "vol_q2": cur_pos.get("vol_q2_at_entry"),
                "vol_q3": cur_pos.get("vol_q3_at_entry"),
            })
            print(f"{GRAY}[{last_ts.strftime('%Y-%m-%d %H:%M')}]{RESET} "
                  f"{asset} {YELLOW}EXIT (end of data) "
                  f"{direction} net={net*100:+.3f}%{RESET}")
            positions_by_asset[asset] = None

    # ---- CSV output (Issue #26 spec) ----
    if args.trades_out:
        out_path = Path(args.trades_out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=TRADE_CSV_COLUMNS)
            writer.writeheader()
            for t in closed_trades:
                writer.writerow({k: t.get(k) for k in TRADE_CSV_COLUMNS})
        print(f"{CYAN}wrote {len(closed_trades)} trade rows -> {out_path}{RESET}")

    # ---- summary ----
    print()
    print(f"{CYAN}{'='*70}{RESET}")
    start = timeline[args.warmup_bars] if len(timeline) > args.warmup_bars else timeline[0]
    print(f"{CYAN}REPLAY SUMMARY  config={cfg_path.name}  "
          f"{start.date()} -> {timeline[-1].date()}{RESET}")
    print(f"{CYAN}{'='*70}{RESET}")

    if not closed_trades:
        print(f"  {YELLOW}no trades closed{RESET}")
        return 0

    rets = [t["net_return_pct"] for t in closed_trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    total = equity - 1.0
    win_rate = len(wins) / len(closed_trades) if closed_trades else 0.0
    pf = sum(wins) / abs(sum(losses)) if sum(losses) != 0 else float("inf")
    print(f"  trades                 {len(closed_trades)}")
    color_total = GREEN if total >= 0 else RED
    print(f"  total return           {color_total}{total * 100:+.2f}%{RESET}")
    print(f"  portfolio realized     {realized_pnl_pct:+.3f}%")
    print(f"  max drawdown           {max_dd * 100:.2f}%")
    print(f"  win rate               {win_rate * 100:.1f}%")
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    print(f"  profit factor          {pf_str}")
    print(f"  max concurrent open    {max_concurrent}/{max_open}")
    by_asset: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for t in closed_trades:
        by_asset[t["asset"]] = by_asset.get(t["asset"], 0) + 1
        by_direction[t["direction"]] = by_direction.get(t["direction"], 0) + 1
        by_reason[t["exit_reason"]] = by_reason.get(t["exit_reason"], 0) + 1
    print(f"  trades by asset        {by_asset}")
    print(f"  trades by direction    {by_direction}")
    print(f"  trades by exit reason  {by_reason}")
    # Issue #34 — vol_sizing summary (when enabled in this config)
    if vol_overlay is not None:
        vol_mults = [t.get("vol_multiplier") for t in closed_trades
                     if t.get("vol_multiplier") is not None]
        final_sizes = [t.get("final_size") for t in closed_trades
                       if t.get("final_size") is not None]
        if vol_mults:
            mean_mult = sum(vol_mults) / len(vol_mults)
            mean_final = sum(final_sizes) / len(final_sizes)
            buckets: dict[str, int] = {}
            for t in closed_trades:
                b = t.get("vol_bucket")
                if b is not None:
                    buckets[b] = buckets.get(b, 0) + 1
            print(f"  vol_sizing mean mult   {mean_mult:.3f}")
            print(f"  vol_sizing mean size   {mean_final:.4f}")
            print(f"  vol_sizing by bucket   {buckets}")
    print()
    print(f"{GRAY}Note: replay is in-sample on the full window. For OOS "
          f"adoption-quality numbers, run scripts/run_*.py (walk-forward).{RESET}")
    if not any_state_change_ever:
        print(f"{YELLOW}Note: no entry signals fired in the replay window.{RESET}")
    return 0


# ---------- entry point --------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None,
                    help="multi-asset live config yaml (e.g. "
                         "state/live_multiasset_long_short_funding.yaml). "
                         "Mutually exclusive with --strategy.")
    ap.add_argument("--symbol", default="BTCUSDT",
                    help="(--strategy mode only) ccxt symbol without slash")
    ap.add_argument("--n-months", type=int, default=24,
                    help="months of history to replay (default 24)")
    ap.add_argument("--timeframe", default="4h",
                    help="(--strategy mode only; --config mode takes timeframe "
                         "from the config file)")
    ap.add_argument("--strategy", default=None,
                    help="strategy yaml for single-asset replay. Mutually "
                         "exclusive with --config.")
    ap.add_argument("--bars-per-second", type=float, default=20.0,
                    help="replay speed (default 20 = 50ms per bar)")
    ap.add_argument("--warmup-bars", type=int, default=210,
                    help="bars to silently process before showing output "
                         "(needed to seed indicators)")
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--quiet-flat", action="store_true",
                    help="only print bars where state changes "
                         "(enter / exit / first flat after exit)")
    ap.add_argument("--trades-out", default=None,
                    help="(--config mode) write closed trades to CSV at "
                         "this path. Columns: asset, direction, entry_time, "
                         "exit_time, entry_price, exit_price, return_pct, "
                         "net_return_pct, setup, exit_reason, bars_held, "
                         "funding_decision.")
    args = ap.parse_args()

    if args.config and args.strategy:
        ap.error("--config and --strategy are mutually exclusive")
    if not args.config and not args.strategy:
        # default to the legacy v2 long-short strategy for backward
        # compatibility with prior invocations.
        args.strategy = str(STATE_DIR / "strategy_v2_long_short.yaml")

    if args.config:
        return _run_config_replay(args)
    return _run_strategy_replay(args)


if __name__ == "__main__":
    sys.exit(main())
