#!/usr/bin/env python3
"""Phase 4 — multi-timeframe confirmation variants on the adopted
long-short funding strategy.

Variants (locked spec):
  A: 4h entry only if 1d SuperTrend agrees
  B: 4h entry only if 1h SuperTrend agrees
  C: 4h entry size scaled by agreement (1.0 if 1h+4h+1d agree,
     0.5 if 2 of 3 agree, 0.25 if only 4h agrees)
  D: 4h entry; 1h early-warning exit (close position when 1h flips
     against position direction)

baseline = current adopted candidate (no MTF confirmation).

Hard rules respected:
  - SuperTrend(10, 3) unchanged.
  - Funding gate: block long >= p95, block short <= p5.
  - fee=0.001/side, slippage=0.0005.
  - Walk-forward (train=1440 / test=360 / embargo=6) — 4h geometry.
  - No threshold tuning. The confirmation rules above are the spec.

Outputs:
  - results/multitimeframe_confirmation_<ts>.csv
  - results/multitimeframe_confirmation_<ts>.md
  - results/multitimeframe_confirmation_trades_<ts>.csv
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
from hermes_trading import data as data_mod
from hermes_trading import funding as funding_mod
from hermes_trading import signals
from hermes_trading import walk_forward as wf

ASSETS = ("BTCUSDT", "ETHUSDT")

# Variant C size mapping (locked)
SIZE_3 = 1.00
SIZE_2 = 0.50
SIZE_1 = 0.25


def _build_funding(price_index, symbol, n_months, side,
                   block_long=95.0, block_short=5.0, window=180):
    f = funding_mod.load_funding(symbol, n_months=n_months)
    aligned = funding_mod.align_to_index(f, price_index)
    pct = funding_mod.rolling_percentile(aligned, window=window)
    warmup = pct.isna()
    if side == "long":
        allowed = pct < block_long
    else:
        allowed = pct > block_short
    allowed = allowed.where(~warmup, True)
    return pd.DataFrame({"long_allowed": allowed.astype(bool)}, index=price_index)


def _build_hourly_daily_dirs(btc_1m, eth_1m):
    """Returns per-asset 1h and 1d SuperTrend direction series, computed
    on the SAME timeframe data (1h/1d) the spec calls for. These series
    are then asof-aligned to each 4h bar."""
    btc_1h = data_mod.resample(btc_1m, "1h")
    eth_1h = data_mod.resample(eth_1m, "1h")
    btc_1d = data_mod.resample(btc_1m, "1d")
    eth_1d = data_mod.resample(eth_1m, "1d")
    _, btc_h_d = signals.supertrend(btc_1h)
    _, eth_h_d = signals.supertrend(eth_1h)
    _, btc_d_d = signals.supertrend(btc_1d)
    _, eth_d_d = signals.supertrend(eth_1d)
    return {
        "1h": {"BTCUSDT": btc_h_d, "ETHUSDT": eth_h_d},
        "1d": {"BTCUSDT": btc_d_d, "ETHUSDT": eth_d_d},
    }


def _asof_dir(series, ts):
    """Most recent CLOSED direction value at or before ts (1h / 1d)."""
    idx = series.index.asof(ts)
    if idx is pd.NaT:
        return None
    val = series.loc[idx]
    if pd.isna(val):
        return None
    return int(val)


def _run_variant(name, btc_df, eth_df, strategy,
                 long_fund, short_fund,
                 mtf_dirs, variant,
                 train_bars, test_bars, embargo,
                 fee, slippage, max_open=2):
    """variant is one of: 'baseline', 'A', 'B', 'C', 'D'."""
    log(f"========== {name}  (variant={variant}) ==========")
    btc_ind = signals.compute_indicators(btc_df, strategy)
    eth_ind = signals.compute_indicators(eth_df, strategy)
    common = btc_ind.index.intersection(eth_ind.index)
    btc_ind = btc_ind.loc[common]; eth_ind = eth_ind.loc[common]
    asset_ind = {"BTCUSDT": btc_ind, "ETHUSDT": eth_ind}
    size_per_asset = 0.5
    base_size = float(strategy["risk"].get("position_size_r", 0.5))
    n = len(common)
    folds = []
    all_trades = []
    fold_returns = []
    fold = 0
    cursor = 0
    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo
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
        positions = {a: None for a in ASSETS}
        fold_trades = []
        for i in range(len(btc_test)):
            ts = btc_test.index[i]
            for asset in ASSETS:
                row = per_asset_records[asset][i]
                position = positions[asset]
                cur_4h = row.get("supertrend_direction")
                # Look up 1h and 1d directions asof ts (causal)
                h_dir = _asof_dir(mtf_dirs["1h"][asset], ts)
                d_dir = _asof_dir(mtf_dirs["1d"][asset], ts)
                if position is not None:
                    bars_held = i - position["entry_i"]
                    if position["direction"] == "long":
                        reason = signals.long_exit(row, position, strategy, bars_held)
                    else:
                        reason = signals.short_exit(row, position, strategy, bars_held)
                    # Variant D early-warning exit: if 1h flipped against pos
                    if reason is None and variant == "D" and h_dir is not None:
                        pos_dir = 1 if position["direction"] == "long" else -1
                        if h_dir != pos_dir:
                            reason = "mtf_1h_warning"
                    if reason:
                        if position["direction"] == "long":
                            exit_fill = (position["stop"] * (1 - slippage)
                                         if reason == "stop"
                                         else float(row["close"]) * (1 - slippage))
                        else:
                            exit_fill = (position["stop"] * (1 + slippage)
                                         if reason == "stop"
                                         else float(row["close"]) * (1 + slippage))
                        if position["direction"] == "long":
                            gross = (exit_fill - position["entry"]) / position["entry"]
                        else:
                            gross = (position["entry"] - exit_fill) / position["entry"]
                        eff = base_size * size_per_asset * position.get("size_mult", 1.0)
                        net = (gross - 2 * fee) * eff
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        fold_trades.append({
                            "asset": asset,
                            "ret": net,
                            "direction": position["direction"],
                            "size_mult": position.get("size_mult", 1.0),
                            "gross_return_pct": gross,
                            "net_return_pct": net,
                            "exit_reason": reason,
                            "bars": bars_held,
                            "entry_ts": position["entry_ts"],
                            "exit_ts": ts,
                            "entry_price": position["entry"],
                            "exit_price": exit_fill,
                            "_variant": name,
                        })
                        positions[asset] = None
                        continue
                if positions[asset] is None:
                    open_count = sum(1 for p in positions.values() if p is not None)
                    if open_count >= max_open:
                        continue
                    # ---- Long ----
                    setup_l = signals.long_entry(row, strategy)
                    opened_long = False
                    if setup_l:
                        df_l = long_fund[asset]
                        allowed = (bool(df_l.loc[ts]["long_allowed"])
                                   if ts in df_l.index else True)
                        if allowed:
                            # Apply variant gate/sizing for LONG
                            size_mult = 1.0
                            do_open = True
                            if variant == "A":   # 1d must agree
                                if d_dir is None or d_dir != 1:
                                    do_open = False
                            elif variant == "B": # 1h must agree
                                if h_dir is None or h_dir != 1:
                                    do_open = False
                            elif variant == "C":
                                agree = sum(1 for v in (h_dir, d_dir) if v == 1)
                                # +4h is the direction we're entering (1 for long)
                                # 4h counts as agreement always (since it's the entry)
                                agree += 1
                                if agree == 3:
                                    size_mult = SIZE_3
                                elif agree == 2:
                                    size_mult = SIZE_2
                                else:
                                    size_mult = SIZE_1
                            if do_open:
                                entry_fill = float(row["close"]) * (1 + slippage)
                                stop_val = float(signals.initial_stop(row, setup_l, strategy))
                                positions[asset] = {
                                    "asset": asset, "entry": entry_fill,
                                    "direction": "long", "setup": setup_l,
                                    "stop": stop_val, "entry_i": i, "entry_ts": ts,
                                    "size_mult": size_mult,
                                }
                                opened_long = True
                    # ---- Short ----
                    if (not opened_long) and strategy.get("shorts", {}).get("enabled"):
                        setup_s = signals.short_entry(row, strategy)
                        if setup_s:
                            df_s = short_fund[asset]
                            allowed = (bool(df_s.loc[ts]["long_allowed"])
                                       if ts in df_s.index else True)
                            if allowed:
                                size_mult = 1.0
                                do_open = True
                                if variant == "A":   # 1d must agree (short = -1)
                                    if d_dir is None or d_dir != -1:
                                        do_open = False
                                elif variant == "B":
                                    if h_dir is None or h_dir != -1:
                                        do_open = False
                                elif variant == "C":
                                    agree = sum(1 for v in (h_dir, d_dir) if v == -1)
                                    agree += 1   # 4h is the entry direction
                                    if agree == 3:
                                        size_mult = SIZE_3
                                    elif agree == 2:
                                        size_mult = SIZE_2
                                    else:
                                        size_mult = SIZE_1
                                if do_open:
                                    entry_fill = float(row["close"]) * (1 - slippage)
                                    stop_val = float(signals.initial_stop_short(row, setup_s, strategy))
                                    positions[asset] = {
                                        "asset": asset, "entry": entry_fill,
                                        "direction": "short", "setup": setup_s,
                                        "stop": stop_val, "entry_i": i, "entry_ts": ts,
                                        "size_mult": size_mult,
                                    }
        # Close any open at fold end
        for asset, position in list(positions.items()):
            if position is None:
                continue
            last_row = per_asset_records[asset][-1]
            bars_held = len(btc_test) - 1 - position["entry_i"]
            if position["direction"] == "long":
                exit_fill = float(last_row["close"]) * (1 - slippage)
                gross = (exit_fill - position["entry"]) / position["entry"]
            else:
                exit_fill = float(last_row["close"]) * (1 + slippage)
                gross = (position["entry"] - exit_fill) / position["entry"]
            eff = base_size * size_per_asset * position.get("size_mult", 1.0)
            net = (gross - 2 * fee) * eff
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            fold_trades.append({
                "asset": asset, "ret": net, "direction": position["direction"],
                "size_mult": position.get("size_mult", 1.0),
                "gross_return_pct": gross, "net_return_pct": net,
                "exit_reason": "end", "bars": bars_held,
                "entry_ts": position["entry_ts"], "exit_ts": btc_test.index[-1],
                "entry_price": position["entry"], "exit_price": exit_fill,
                "_variant": name,
            })
            positions[asset] = None
        all_trades.extend(fold_trades)
        fold_ret = equity - 1.0
        fold_returns.append(fold_ret)
        folds.append({"fold": fold, "ret": fold_ret})
        cursor += test_bars
    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for r in fold_returns if r > 0)
    pf_s = "inf" if oos["profit_factor"] == float("inf") else f"{oos['profit_factor']:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"win={oos['win_rate']*100:.1f}%  folds+={fold_pos}/{len(folds)}")
    return {
        "name": name, "oos": oos, "folds": folds, "trades": all_trades,
        "fold_pos": fold_pos,
    }


def _subset_metrics(trades, lo, hi):
    in_w = [t for t in trades if t["exit_ts"] >= lo and t["exit_ts"] <= hi]
    if not in_w:
        return None
    rets = [t["ret"] for t in in_w]
    eq = 1.0; peak = 1.0; max_dd = 0.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    return {
        "trades": len(in_w), "total_return": eq - 1.0,
        "max_drawdown": max_dd,
        "profit_factor": pf if pf != float("inf") else 9999.0,
        "win_rate": len(wins) / len(in_w),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--strategy",
                    default=str(STATE_DIR / "strategy_supertrend_long_short.yaml"))
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    strategy = yaml.safe_load(open(args.strategy))
    log(f"loading BTC + ETH {args.n_months}mo (1m) …")
    btc_1m = data_mod.load_klines("BTCUSDT", n_months=args.n_months)
    eth_1m = data_mod.load_klines("ETHUSDT", n_months=args.n_months)
    btc_4h = data_mod.resample(btc_1m, "4h")
    eth_4h = data_mod.resample(eth_1m, "4h")
    common = btc_4h.index.intersection(eth_4h.index)
    btc_4h = btc_4h.loc[common]; eth_4h = eth_4h.loc[common]
    log(f"4h bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    log("building MTF SuperTrend direction series (1h, 1d) …")
    mtf_dirs = _build_hourly_daily_dirs(btc_1m, eth_1m)

    log("building funding gates …")
    long_fund = {
        "BTCUSDT": _build_funding(common, "BTCUSDT", args.n_months, "long"),
        "ETHUSDT": _build_funding(common, "ETHUSDT", args.n_months, "long"),
    }
    short_fund = {
        "BTCUSDT": _build_funding(common, "BTCUSDT", args.n_months, "short"),
        "ETHUSDT": _build_funding(common, "ETHUSDT", args.n_months, "short"),
    }

    results = []
    for name, v in (("baseline",  "baseline"),
                    ("A_1d_agree", "A"),
                    ("B_1h_agree", "B"),
                    ("C_size_scale", "C"),
                    ("D_1h_early_exit", "D")):
        r = _run_variant(name, btc_4h, eth_4h, strategy,
                         long_fund, short_fund, mtf_dirs, v,
                         args.train_bars, args.test_bars, args.embargo_bars,
                         args.fee, args.slippage)
        results.append(r)

    # Subset windows per variant
    for r in results:
        if not r["trades"]:
            r["subsets"] = {}
            continue
        last_exit = r["trades"][-1]["exit_ts"]
        subsets = {}
        for mb in (24, 12, 6, 3):
            lo = last_exit - pd.DateOffset(months=mb)
            m = _subset_metrics(r["trades"], lo, last_exit)
            subsets[mb] = m
        r["subsets"] = subsets

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"multitimeframe_confirmation_{ts_str}.csv"
    md_path = out_dir / f"multitimeframe_confirmation_{ts_str}.md"
    trades_path = out_dir / f"multitimeframe_confirmation_trades_{ts_str}.csv"

    rows = []
    for r in results:
        oos = r["oos"]
        rows.append({
            "variant": r["name"],
            "scope": "48mo_wf",
            "trades": oos["trades"],
            "total_return": oos["total_return"],
            "max_drawdown": oos["max_drawdown"],
            "profit_factor": (oos["profit_factor"]
                               if oos["profit_factor"] != float("inf") else 9999.0),
            "win_rate": oos["win_rate"],
            "folds_positive": r["fold_pos"],
        })
        for mb, m in r["subsets"].items():
            if m is None:
                continue
            rows.append({
                "variant": r["name"],
                "scope": f"last_{mb}mo",
                "trades": m["trades"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "profit_factor": m["profit_factor"],
                "win_rate": m["win_rate"],
                "folds_positive": "",
            })
    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote {csv_path}")

    md = [
        f"# MTF confirmation variants — {ts_str}",
        "",
        f"- universe: BTC/USDT + ETH/USDT (parallel, 4h decision)",
        f"- strategy: `{args.strategy}`",
        f"- n_months: {args.n_months}",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / "
        f"embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "Variants:",
        "- baseline = current adopted candidate (no MTF confirmation)",
        "- A: 4h entry only if 1d SuperTrend direction agrees with the trade",
        "- B: 4h entry only if 1h SuperTrend direction agrees with the trade",
        "- C: size scaled by agreement (3 agree -> 1.0, 2 -> 0.5, 1 -> 0.25)",
        "- D: 4h entry, 1h early-warning exit (close on 1h flip vs position)",
        "",
        "## 48mo OOS",
        "",
        "| variant | n | ret | DD | PF | win | folds+ |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        oos = r["oos"]
        pf = oos["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        md.append(f"| {r['name']} | {oos['trades']} | "
                  f"{oos['total_return']*100:+.2f}% | "
                  f"{oos['max_drawdown']*100:.2f}% | {pf_s} | "
                  f"{oos['win_rate']*100:.1f}% | "
                  f"{r['fold_pos']}/{len(r['folds'])} |")

    md.extend(["", "## Trailing-window slices (in-sample on WF trades)", "",
               "| variant | scope | n | ret | DD | PF | win |",
               "|---|---|---:|---:|---:|---:|---:|"])
    for r in results:
        for mb in (24, 12, 6, 3):
            m = r["subsets"].get(mb)
            if m is None:
                continue
            pf = m["profit_factor"]
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            md.append(f"| {r['name']} | last_{mb}mo | {m['trades']} | "
                      f"{m['total_return']*100:+.2f}% | "
                      f"{m['max_drawdown']*100:.2f}% | {pf_s} | "
                      f"{m['win_rate']*100:.1f}% |")

    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote {md_path}")

    base_cols = ["_variant", "asset", "direction", "entry_ts", "exit_ts",
                 "entry_price", "exit_price", "gross_return_pct", "net_return_pct",
                 "exit_reason", "bars", "size_mult"]
    with open(trades_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(base_cols)
        for r in results:
            for t in r["trades"]:
                row = []
                for c in base_cols:
                    v = t.get(c)
                    if hasattr(v, "isoformat"):
                        v = v.isoformat()
                    if v is None:
                        v = ""
                    row.append(v)
                w.writerow(row)
    log(f"wrote {trades_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
