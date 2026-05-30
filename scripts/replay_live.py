#!/usr/bin/env python3
"""Replay-mode worker — feed historical bars through the live engine.

Same `signals` module the live worker uses; same entry/exit logic; same
strategy yaml. Difference: instead of polling a live price feed every 10
seconds, this script walks historical bars at a configurable speed.

Useful for:
  - Watching, in compressed time, what the bot WOULD have done over a
    24- or 48-month window.
  - Building intuition for entry/exit timing on 4h decision bars (i.e.
    "what does '1 trade every 3 weeks' actually look like").
  - Sanity-checking a strategy yaml without waiting for a live signal
    to fire.

This is research / educational tooling. It does NOT trade, does NOT
write to state/, and is independent of the live worker.

Output format mirrors the live worker so the experience is familiar:

  [2024-07-15 12:00] BTC/USDT 64321.10 rsi=41.6 v10 pos=flat
  [2024-07-15 16:00] BTC/USDT 64580.50 rsi=43.2 v10 pos=flat
  [2024-07-17 04:00] BTC/USDT 67120.00 rsi=68.4 v10 ENTER long supertrend @ 67120.00 stop=64980.00 size=0.5
  [2024-07-17 08:00] BTC/USDT 67890.00 rsi=70.1 v10 ↑long @ 67120.00 +1.15%
  ...
  [2024-07-22 20:00] BTC/USDT 71200.00 rsi=75.0 v10 EXIT long supertrend @ 71200.00 net=+3.05% reason=stop bars=33
"""
from __future__ import annotations

import argparse
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
from hermes_trading import signals

GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GRAY = "\033[90m"
RESET = "\033[0m"


def _fmt_pos(position: dict | None, last: float) -> str:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT",
                    help="ccxt symbol without slash (default BTCUSDT)")
    ap.add_argument("--n-months", type=int, default=24,
                    help="months of history to replay (default 24)")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--strategy",
                    default=str(STATE_DIR / "strategy_v2_long_short.yaml"))
    ap.add_argument("--bars-per-second", type=float, default=20.0,
                    help="replay speed (default 20 = 200ms per 4h bar; "
                         "lower this to slow down)")
    ap.add_argument("--warmup-bars", type=int, default=210,
                    help="bars to silently process before showing output "
                         "(needed to seed indicators)")
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--quiet-flat", action="store_true",
                    help="only print bars where state changes "
                         "(enter / exit / first flat after exit)")
    args = ap.parse_args()

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
    last_state = None       # "flat" / "long" / "short" — for quiet-flat mode

    sleep_per_bar = 1.0 / max(args.bars_per_second, 0.001)
    asset_label = f"{args.symbol[:3]}/{args.symbol[3:]}" if len(args.symbol) >= 6 else args.symbol

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

        # ---- entry check (only if flat after exit handling) ----
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
                setup_s = signals.short_entry(row, strategy) if strategy.get("shorts", {}).get("enabled") else None
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


if __name__ == "__main__":
    main()
