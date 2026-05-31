#!/usr/bin/env python3
"""SuperTrend long-only vs long-short comparison (Issue #19).

Six variants on the 48-month 4h BTC+ETH data, same walk-forward as
every other experiment (train 1440 / test 360 / embargo 6, 20 folds).
No parameter tuning. SuperTrend(10, 3) unchanged.

Variants:
  1. btc_supertrend_long_only
  2. btc_supertrend_long_short
  3. eth_supertrend_long_only
  4. eth_supertrend_long_short
  5. btc_eth_parallel_long_only
  6. btc_eth_parallel_long_short

The single-asset variants reuse ``walk_forward.walk_forward`` because
``backtest._run_state_machine`` already evaluates both long and short
sides — the long-short strategy yaml just turns the short side on.

The parallel variants use a small coordinator written inline because
the existing parallel coordinators in this repo are long-only.

Adoption criteria (from the issue spec):

    PF > 2.50  AND  DD <= 5.54%
    return > 39.72%  AND  trades >= 65

Hard rules:
  - SuperTrend (10, 3) unchanged.
  - Same fees / slippage / fold geometry as every other experiment.
  - No live wiring; no live config change.

Outputs (under --out-dir, default ``results/``):
  - supertrend_long_short_comparison_<ts>.csv
  - supertrend_long_short_comparison_<ts>.md
  - trades_supertrend_long_short_<ts>.csv  (all six variants tagged)
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import STATE_DIR, log
from hermes_trading import backtest as bt
from hermes_trading import data as data_mod
from hermes_trading import signals
from hermes_trading import walk_forward as wf


# ---------- single-asset variants -------------------------------------------

def _run_solo(name, df, strategy, train_bars, test_bars, embargo_bars,
              fee, slippage, asset_label):
    log(f"========== {name} ==========")
    res = wf.walk_forward(
        df, strategy, markov_cfg=None,
        train_bars=train_bars, test_bars=test_bars,
        embargo_bars=embargo_bars, fee=fee, slippage=slippage,
    )
    trades = res["trades"]
    for t in trades:
        t["_variant"] = name
        t["asset"] = asset_label
    fold_returns = [f["test_metrics"]["total_return"] for f in res["folds"]]
    fold_pos = sum(1 for r in fold_returns if r > 0)
    oos = res["oos_metrics"]
    pf_s = "inf" if oos["profit_factor"] == float("inf") else f"{oos['profit_factor']:.2f}"
    n_long = sum(1 for t in trades if t.get("direction") == "long")
    n_short = sum(1 for t in trades if t.get("direction") == "short")
    log(f"  -> trades={oos['trades']} (L={n_long}, S={n_short})  "
        f"ret={oos['total_return']*100:+.2f}%  DD={oos['max_drawdown']*100:.2f}%  "
        f"PF={pf_s}  Sharpe={oos['sharpe_per_trade']:.3f}  "
        f"win={oos['win_rate']*100:.1f}%  folds+={fold_pos}/{len(res['folds'])}")
    return {
        "name": name, "oos": oos, "folds": res["folds"], "trades": trades,
        "fold_pos": fold_pos, "fold_returns": fold_returns,
        "n_long": n_long, "n_short": n_short,
    }


# ---------- parallel coordinator (long + short) -----------------------------

def _open_position(asset, row, setup, direction, base_size, size_per_asset,
                   slippage, strategy, i, ts):
    """Construct a position dict matching the conventions
    _run_state_machine uses, so the existing long_exit/short_exit
    functions can be reused without modification."""
    entry_price = float(row["close"])
    if direction == "long":
        entry = entry_price * (1 + slippage)
        stop = float(signals.initial_stop(row, setup, strategy))
    else:
        entry = entry_price * (1 - slippage)
        stop = float(signals.initial_stop_short(row, setup, strategy))
    return {
        "asset": asset,
        "entry": entry,
        "setup": setup,
        "direction": direction,
        "entry_i": i,
        "entry_ts": ts,
        "stop": stop,
        "initial_stop": stop,
        "size_multiplier": 1.0,  # parallel coordinator doesn't use overlays
        "size_per_asset": size_per_asset,
        "base_size": base_size,
        "entry_rsi": float(row["rsi"]) if pd.notna(row.get("rsi")) else None,
        "entry_atr": float(row["atr"]) if pd.notna(row.get("atr")) else None,
    }


def _close_position(position, exit_fill, reason, ts, fee, slippage, asset, bars_held):
    """Build a trade record + return the equity-curve contribution."""
    if position["direction"] == "long":
        gross = (exit_fill - position["entry"]) / position["entry"]
    else:
        gross = (position["entry"] - exit_fill) / position["entry"]
    effective = position["base_size"] * position["size_per_asset"]
    net = (gross - 2 * fee) * effective
    trade = {
        "asset": asset,
        "ret": net,
        "reason": reason,
        "setup": position["setup"],
        "direction": position["direction"],
        "bars": bars_held,
        "size_multiplier": 1.0,
        "size_per_asset": position["size_per_asset"],
        "position_size_effective": effective,
        "entry_ts": position["entry_ts"],
        "exit_ts": ts,
        "setup_name": position["setup"],
        "side": position["direction"],
        "entry_price": position["entry"],
        "exit_price": exit_fill,
        "gross_return_pct": gross,
        "net_return_pct": net,
        "holding_bars": bars_held,
        "exit_reason": reason,
        "entry_rsi": position.get("entry_rsi"),
        "entry_atr": position.get("entry_atr"),
    }
    return trade, net


def _run_parallel(name, btc_df, eth_df, strategy,
                  train_bars, test_bars, embargo_bars, fee, slippage,
                  max_open=2):
    log(f"========== {name} ==========")
    btc_ind = signals.compute_indicators(btc_df, strategy)
    eth_ind = signals.compute_indicators(eth_df, strategy)
    common = btc_ind.index.intersection(eth_ind.index)
    btc_ind = btc_ind.loc[common].copy(); btc_ind["ts"] = btc_ind.index
    eth_ind = eth_ind.loc[common].copy(); eth_ind["ts"] = eth_ind.index

    assets = ("BTCUSDT", "ETHUSDT")
    size_per_asset = 1.0 / len(assets)
    base_size = float(strategy["risk"].get("position_size_r", 0.5))

    n = len(common)
    folds = []
    all_trades = []
    fold_returns = []
    fold = 0
    cursor = 0
    max_concurrent = 0

    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo_bars
        test_hi = test_lo + test_bars
        if test_hi > n:
            break
        fold += 1

        btc_test = btc_ind.iloc[test_lo:test_hi]
        eth_test = eth_ind.iloc[test_lo:test_hi]
        per_asset_records = {
            "BTCUSDT": btc_test.to_dict("records"),
            "ETHUSDT": eth_test.to_dict("records"),
        }

        equity = 1.0; peak = 1.0; max_dd = 0.0
        positions: dict[str, dict | None] = {a: None for a in assets}
        fold_trades = []
        fold_max_concurrent = 0

        for i in range(len(btc_test)):
            ts = btc_test.index[i]
            for asset in assets:
                row = per_asset_records[asset][i]
                position = positions[asset]

                # ---- exit branch ----
                if position is not None:
                    bars_held = i - position["entry_i"]
                    if position["direction"] == "long":
                        reason = signals.long_exit(row, position, strategy, bars_held)
                    else:
                        reason = signals.short_exit(row, position, strategy, bars_held)
                    if reason:
                        if position["direction"] == "long":
                            exit_fill = (position["stop"] * (1 - slippage)
                                         if reason == "stop"
                                         else row["close"] * (1 - slippage))
                        else:
                            exit_fill = (position["stop"] * (1 + slippage)
                                         if reason == "stop"
                                         else row["close"] * (1 + slippage))
                        trade, net = _close_position(
                            position, exit_fill, reason, ts, fee, slippage,
                            asset, bars_held,
                        )
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        fold_trades.append(trade)
                        positions[asset] = None
                        continue

                # ---- entry branch ----
                if positions[asset] is None:
                    open_count = sum(1 for p in positions.values() if p is not None)
                    if open_count >= max_open:
                        continue
                    setup_l = signals.long_entry(row, strategy)
                    if setup_l:
                        positions[asset] = _open_position(
                            asset, row, setup_l, "long",
                            base_size, size_per_asset, slippage, strategy, i, ts,
                        )
                        continue
                    setup_s = signals.short_entry(row, strategy)
                    if setup_s:
                        positions[asset] = _open_position(
                            asset, row, setup_s, "short",
                            base_size, size_per_asset, slippage, strategy, i, ts,
                        )
            cur_open = sum(1 for p in positions.values() if p is not None)
            fold_max_concurrent = max(fold_max_concurrent, cur_open)

        # close any leftover at fold end
        for asset, position in list(positions.items()):
            if position is None:
                continue
            last_row = per_asset_records[asset][-1]
            bars_held = len(btc_test) - 1 - position["entry_i"]
            if position["direction"] == "long":
                exit_fill = last_row["close"] * (1 - slippage)
            else:
                exit_fill = last_row["close"] * (1 + slippage)
            trade, net = _close_position(
                position, exit_fill, "end", btc_test.index[-1],
                fee, slippage, asset, bars_held,
            )
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            fold_trades.append(trade)
            positions[asset] = None

        for t in fold_trades:
            t["_variant"] = name
        all_trades.extend(fold_trades)
        fold_ret = equity - 1.0
        fold_returns.append(fold_ret)
        max_concurrent = max(max_concurrent, fold_max_concurrent)
        folds.append({
            "fold": fold,
            "test_range": (btc_test.index[0].date(), btc_test.index[-1].date()),
            "test_metrics": {
                "trades": len(fold_trades),
                "total_return": fold_ret,
                "max_drawdown": max_dd,
                "max_concurrent": fold_max_concurrent,
            },
        })
        cursor += test_bars

    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for r in fold_returns if r > 0)
    pf_s = "inf" if oos["profit_factor"] == float("inf") else f"{oos['profit_factor']:.2f}"
    n_long = sum(1 for t in all_trades if t.get("direction") == "long")
    n_short = sum(1 for t in all_trades if t.get("direction") == "short")
    log(f"  -> trades={oos['trades']} (L={n_long}, S={n_short})  "
        f"ret={oos['total_return']*100:+.2f}%  DD={oos['max_drawdown']*100:.2f}%  "
        f"PF={pf_s}  Sharpe={oos['sharpe_per_trade']:.3f}  "
        f"win={oos['win_rate']*100:.1f}%  folds+={fold_pos}/{len(folds)}  "
        f"max_concurrent={max_concurrent}")
    return {
        "name": name, "oos": oos, "folds": folds, "trades": all_trades,
        "fold_pos": fold_pos, "fold_returns": fold_returns,
        "max_concurrent": max_concurrent, "n_long": n_long, "n_short": n_short,
    }


# ---------- main -------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--btc-symbol", default="BTCUSDT")
    ap.add_argument("--eth-symbol", default="ETHUSDT")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--long-only-strategy",
                    default=str(STATE_DIR / "strategy_supertrend.yaml"))
    ap.add_argument("--long-short-strategy",
                    default=str(STATE_DIR / "strategy_supertrend_long_short.yaml"))
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"supertrend_long_short_comparison_{ts}.csv"
    md_path = out_dir / f"supertrend_long_short_comparison_{ts}.md"
    trades_path = out_dir / f"trades_supertrend_long_short_{ts}.csv"

    long_strategy = yaml.safe_load(open(args.long_only_strategy))
    ls_strategy = yaml.safe_load(open(args.long_short_strategy))

    log(f"loading BTC + ETH {args.n_months}mo …")
    btc_df = data_mod.resample(data_mod.load_klines(args.btc_symbol, n_months=args.n_months),
                               args.timeframe)
    eth_df = data_mod.resample(data_mod.load_klines(args.eth_symbol, n_months=args.n_months),
                               args.timeframe)
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]; eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    results = []
    results.append(_run_solo("btc_supertrend_long_only", btc_df, long_strategy,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage, "BTC"))
    results.append(_run_solo("btc_supertrend_long_short", btc_df, ls_strategy,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage, "BTC"))
    results.append(_run_solo("eth_supertrend_long_only", eth_df, long_strategy,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage, "ETH"))
    results.append(_run_solo("eth_supertrend_long_short", eth_df, ls_strategy,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage, "ETH"))
    results.append(_run_parallel("btc_eth_parallel_long_only",
                                 btc_df, eth_df, long_strategy,
                                 args.train_bars, args.test_bars, args.embargo_bars,
                                 args.fee, args.slippage))
    results.append(_run_parallel("btc_eth_parallel_long_short",
                                 btc_df, eth_df, ls_strategy,
                                 args.train_bars, args.test_bars, args.embargo_bars,
                                 args.fee, args.slippage))

    rows = []
    all_trades = []
    for r in results:
        o = r["oos"]
        for t in r["trades"]:
            all_trades.append(t)
        fr = r["fold_returns"]
        fold_std = float(np.std(fr)) if fr else 0.0
        by_reason: dict[str, int] = {}
        for t in r["trades"]:
            by_reason[t.get("reason", "?")] = by_reason.get(t.get("reason", "?"), 0) + 1
        long_ret = sum(t["ret"] for t in r["trades"] if t.get("direction") == "long")
        short_ret = sum(t["ret"] for t in r["trades"] if t.get("direction") == "short")
        rows.append({
            "variant": r["name"],
            "trades": o["trades"],
            "n_long": r["n_long"],
            "n_short": r["n_short"],
            "total_return": o["total_return"],
            "long_return_contrib": long_ret,
            "short_return_contrib": short_ret,
            "max_drawdown": o["max_drawdown"],
            "profit_factor": (o["profit_factor"]
                              if o["profit_factor"] != float("inf") else 9999.0),
            "sharpe_per_trade": o["sharpe_per_trade"],
            "win_rate": o["win_rate"],
            "avg_win": o["avg_win"],
            "avg_loss": o["avg_loss"],
            "n_folds": len(r["folds"]),
            "fold_positive": f"{r['fold_pos']}/{len(r['folds'])}",
            "fold_return_std": fold_std,
            "max_concurrent": r.get("max_concurrent", 1),
            "by_reason": "; ".join(f"{k}:{v}" for k, v in by_reason.items()),
        })

    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# SuperTrend long-only vs long-short — {ts}",
        "",
        f"- long-only strategy: `{args.long_only_strategy}`",
        f"- long-short strategy: `{args.long_short_strategy}`",
        f"- universe: BTC/USDT + ETH/USDT (parallel)",
        f"- BTC + ETH 48mo span: {common[0].date()} -> {common[-1].date()} ({len(common)} bars)",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "Adoption (long-short must beat the adopted BTC/ETH parallel long-only): "
        "PF > 2.50, DD <= 5.54%, return > 39.72%, trades >= 65.",
        "",
        "| variant | folds | n | L | S | OOS return | L ret | S ret | max DD | PF | Sharpe | win% | folds+ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == 9999.0 else f"{pf:.2f}"
        md.append(
            f"| `{r['variant']}` | {r['n_folds']} | {r['trades']} | "
            f"{r['n_long']} | {r['n_short']} | "
            f"{r['total_return']*100:+.2f}% | "
            f"{r['long_return_contrib']*100:+.2f}% | "
            f"{r['short_return_contrib']*100:+.2f}% | "
            f"{r['max_drawdown']*100:.2f}% | {pf_s} | "
            f"{r['sharpe_per_trade']:.3f} | "
            f"{r['win_rate']*100:.1f}% | {r['fold_positive']} |"
        )
    md += ["", "### By exit reason", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_reason']}")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    base_cols = [
        "_variant", "asset", "entry_ts", "exit_ts", "setup_name",
        "direction", "side", "entry_price", "exit_price",
        "gross_return_pct", "net_return_pct",
        "size_per_asset", "position_size_effective",
        "exit_reason", "holding_bars",
    ]
    with open(trades_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(base_cols)
        for t in all_trades:
            row = []
            for c in base_cols:
                v = t.get(c)
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                if v is None:
                    v = ""
                row.append(v)
            w.writerow(row)
    log(f"wrote detailed trades CSV ({len(all_trades)} rows) → {trades_path}")


if __name__ == "__main__":
    main()
