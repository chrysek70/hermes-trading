#!/usr/bin/env python3
"""Phase 3 — test the SAME long-short funding strategy on different
decision timeframes.

Hard rules respected:
  - SuperTrend(10, 3) unchanged.
  - BTC/USDT + ETH/USDT only.
  - Direction-aware funding filter: block long >= p95, block short <= p5.
  - fee=0.001/side, slippage=0.0005.
  - Walk-forward only (no full-history retune).

Timeframes tested: 1h, 2h, 4h (baseline), 1d.

Per timeframe, train/test/embargo bar scaling:
  - 1h:  train=1440 (60d),  test=360 (15d),  embargo=6
  - 2h:  train=1440 (120d), test=360 (30d),  embargo=6
  - 4h:  train=1440 (240d), test=360 (60d),  embargo=6
  - 1d:  train=240  (240d), test=60  (60d),  embargo=1

Outputs (under --out-dir):
  - timeframe_comparison_<ts>.csv
  - timeframe_comparison_<ts>.md
  - timeframe_trades_<ts>.csv
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

# Locked TF geometry from spec.
TF_GEOMETRY = {
    "1h":  dict(train_bars=1440, test_bars=360, embargo=6),
    "2h":  dict(train_bars=1440, test_bars=360, embargo=6),
    "4h":  dict(train_bars=1440, test_bars=360, embargo=6),
    "1d":  dict(train_bars=240,  test_bars=60,  embargo=1),
}


def _build_funding(price_index, symbol, n_months, side,
                   block_long=95.0, block_short=5.0, window_bars=180):
    f = funding_mod.load_funding(symbol, n_months=n_months)
    aligned = funding_mod.align_to_index(f, price_index)
    pct = funding_mod.rolling_percentile(aligned, window=window_bars)
    warmup = pct.isna()
    if side == "long":
        allowed = pct < block_long
    else:
        allowed = pct > block_short
    allowed = allowed.where(~warmup, True)
    return pd.DataFrame({"long_allowed": allowed.astype(bool)}, index=price_index)


def _run_walk_forward(btc_df, eth_df, strategy, long_fund, short_fund,
                     train_bars, test_bars, embargo, fee, slippage, max_open=2):
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
                        if position["direction"] == "long":
                            gross = (exit_fill - position["entry"]) / position["entry"]
                        else:
                            gross = (position["entry"] - exit_fill) / position["entry"]
                        eff = base_size * size_per_asset
                        net = (gross - 2 * fee) * eff
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        fold_trades.append({
                            "asset": asset,
                            "ret": net,
                            "direction": position["direction"],
                            "gross_return_pct": gross,
                            "net_return_pct": net,
                            "exit_reason": reason,
                            "bars": bars_held,
                            "entry_ts": position["entry_ts"],
                            "exit_ts": ts,
                            "entry_price": position["entry"],
                            "exit_price": exit_fill,
                        })
                        positions[asset] = None
                        continue
                if positions[asset] is None:
                    open_count = sum(1 for p in positions.values() if p is not None)
                    if open_count >= max_open:
                        continue
                    setup_l = signals.long_entry(row, strategy)
                    opened_long = False
                    if setup_l:
                        allowed = True
                        df_l = long_fund[asset]
                        if ts in df_l.index:
                            allowed = bool(df_l.loc[ts]["long_allowed"])
                        if allowed:
                            entry_fill = float(row["close"]) * (1 + slippage)
                            stop_val = float(signals.initial_stop(row, setup_l, strategy))
                            positions[asset] = {
                                "asset": asset, "entry": entry_fill,
                                "direction": "long", "setup": setup_l,
                                "stop": stop_val, "entry_i": i, "entry_ts": ts,
                            }
                            opened_long = True
                    if (not opened_long) and strategy.get("shorts", {}).get("enabled"):
                        setup_s = signals.short_entry(row, strategy)
                        if setup_s:
                            allowed = True
                            df_s = short_fund[asset]
                            if ts in df_s.index:
                                allowed = bool(df_s.loc[ts]["long_allowed"])
                            if allowed:
                                entry_fill = float(row["close"]) * (1 - slippage)
                                stop_val = float(signals.initial_stop_short(row, setup_s, strategy))
                                positions[asset] = {
                                    "asset": asset, "entry": entry_fill,
                                    "direction": "short", "setup": setup_s,
                                    "stop": stop_val, "entry_i": i, "entry_ts": ts,
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
            eff = base_size * size_per_asset
            net = (gross - 2 * fee) * eff
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            fold_trades.append({
                "asset": asset,
                "ret": net,
                "direction": position["direction"],
                "gross_return_pct": gross,
                "net_return_pct": net,
                "exit_reason": "end",
                "bars": bars_held,
                "entry_ts": position["entry_ts"],
                "exit_ts": btc_test.index[-1],
                "entry_price": position["entry"],
                "exit_price": exit_fill,
            })
            positions[asset] = None
        all_trades.extend(fold_trades)
        fold_ret = equity - 1.0
        fold_returns.append(fold_ret)
        folds.append({"fold": fold, "ret": fold_ret, "trades": len(fold_trades)})
        cursor += test_bars
    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for r in fold_returns if r > 0)
    return {
        "oos": oos, "folds": folds, "fold_returns": fold_returns,
        "fold_pos": fold_pos, "trades": all_trades,
    }


def _subset_metrics(trades, lo, hi):
    """Compute stitched OOS metrics on trades whose exit_ts is in
    [lo, hi]. Uses the same continuous-compounding rule as
    ``wf._stitch_metrics``."""
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
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--timeframes", default="1h,2h,4h,1d")
    args = ap.parse_args()

    strategy = yaml.safe_load(open(args.strategy))

    log(f"loading BTC + ETH {args.n_months}mo (1m) …")
    btc_1m = data_mod.load_klines("BTCUSDT", n_months=args.n_months)
    eth_1m = data_mod.load_klines("ETHUSDT", n_months=args.n_months)
    log("loaded base 1m bars; will resample per TF")

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"timeframe_comparison_{ts_str}.csv"
    md_path = out_dir / f"timeframe_comparison_{ts_str}.md"
    trades_path = out_dir / f"timeframe_trades_{ts_str}.csv"

    tfs = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    results_by_tf = {}
    all_trades_tagged = []

    for tf in tfs:
        if tf not in TF_GEOMETRY:
            log(f"  skipping unknown TF: {tf}")
            continue
        geom = TF_GEOMETRY[tf]
        btc = data_mod.resample(btc_1m, tf)
        eth = data_mod.resample(eth_1m, tf)
        common = btc.index.intersection(eth.index)
        btc = btc.loc[common]; eth = eth.loc[common]
        log(f"========== TF={tf}  bars={len(common)}  "
            f"train={geom['train_bars']}  test={geom['test_bars']}  "
            f"embargo={geom['embargo']} ==========")
        # Funding window in bars must roughly equal 30 days in this TF.
        bars_per_day = {"1h": 24, "2h": 12, "4h": 6, "1d": 1}[tf]
        fund_window = max(20, int(round(30 * bars_per_day)))
        long_fund = {
            "BTCUSDT": _build_funding(common, "BTCUSDT", args.n_months, "long",
                                       window_bars=fund_window),
            "ETHUSDT": _build_funding(common, "ETHUSDT", args.n_months, "long",
                                       window_bars=fund_window),
        }
        short_fund = {
            "BTCUSDT": _build_funding(common, "BTCUSDT", args.n_months, "short",
                                       window_bars=fund_window),
            "ETHUSDT": _build_funding(common, "ETHUSDT", args.n_months, "short",
                                       window_bars=fund_window),
        }
        res = _run_walk_forward(btc, eth, strategy, long_fund, short_fund,
                                geom["train_bars"], geom["test_bars"],
                                geom["embargo"], args.fee, args.slippage)
        oos = res["oos"]
        log(f"  OOS: trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
            f"DD={oos['max_drawdown']*100:.2f}%  "
            f"PF={oos['profit_factor'] if oos['profit_factor'] != float('inf') else 'inf'}")
        # subset windows
        if res["trades"]:
            last_exit = res["trades"][-1]["exit_ts"]
            subsets = {}
            for months_back in (24, 12, 6, 3):
                lo = last_exit - pd.DateOffset(months=months_back)
                m = _subset_metrics(res["trades"], lo, last_exit)
                subsets[months_back] = m
            results_by_tf[tf] = {"oos": oos, "subsets": subsets,
                                  "trades_count": oos["trades"],
                                  "folds": len(res["folds"]),
                                  "fold_pos": res["fold_pos"]}
            for t in res["trades"]:
                t2 = dict(t); t2["_tf"] = tf
                all_trades_tagged.append(t2)

    # Write CSV
    rows = []
    for tf in tfs:
        if tf not in results_by_tf:
            continue
        r = results_by_tf[tf]
        oos = r["oos"]
        rows.append({
            "timeframe": tf,
            "scope": "48mo_wf",
            "trades": oos["trades"],
            "total_return": oos["total_return"],
            "max_drawdown": oos["max_drawdown"],
            "profit_factor": (oos["profit_factor"]
                               if oos["profit_factor"] != float("inf") else 9999.0),
            "win_rate": oos["win_rate"],
            "folds": r["folds"],
            "folds_positive": r["fold_pos"],
        })
        for m_back, m in r["subsets"].items():
            if m is None:
                continue
            rows.append({
                "timeframe": tf,
                "scope": f"last_{m_back}mo",
                "trades": m["trades"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "profit_factor": (m["profit_factor"]
                                   if m["profit_factor"] != float("inf") else 9999.0),
                "win_rate": m["win_rate"],
                "folds": "",
                "folds_positive": "",
            })

    if rows:
        cols = list(rows[0].keys())
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        log(f"wrote {csv_path}")

    # Markdown summary
    md = [
        f"# Timeframe comparison — {ts_str}",
        "",
        f"- strategy: `{args.strategy}` (long-short SuperTrend(10,3) + direction-aware funding gate)",
        f"- universe: BTC/USDT + ETH/USDT (parallel)",
        f"- n_months: {args.n_months}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "TF geometry:",
        "| TF | train | test | embargo | bars/day | funding window (bars) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for tf in tfs:
        if tf not in TF_GEOMETRY:
            continue
        g = TF_GEOMETRY[tf]
        bpd = {"1h": 24, "2h": 12, "4h": 6, "1d": 1}[tf]
        md.append(f"| {tf} | {g['train_bars']} | {g['test_bars']} | "
                  f"{g['embargo']} | {bpd} | {max(20, int(round(30*bpd)))} |")
    md.extend(["", "## Walk-forward OOS by timeframe", "",
              "| TF | n | ret | DD | PF | win | folds+ |",
              "|---|---:|---:|---:|---:|---:|---:|"])
    for tf in tfs:
        if tf not in results_by_tf:
            continue
        oos = results_by_tf[tf]["oos"]
        r = results_by_tf[tf]
        pf = oos["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        md.append(f"| {tf} | {oos['trades']} | "
                  f"{oos['total_return']*100:+.2f}% | "
                  f"{oos['max_drawdown']*100:.2f}% | {pf_s} | "
                  f"{oos['win_rate']*100:.1f}% | "
                  f"{r['fold_pos']}/{r['folds']} |")
    md.extend(["", "## Trailing-window slices (in-sample on WF trades)", "",
              "| TF | scope | n | ret | DD | PF | win |",
              "|---|---|---:|---:|---:|---:|---:|"])
    for tf in tfs:
        if tf not in results_by_tf:
            continue
        for mb in (24, 12, 6, 3):
            m = results_by_tf[tf]["subsets"].get(mb)
            if m is None:
                continue
            pf = m["profit_factor"]
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            md.append(f"| {tf} | last_{mb}mo | {m['trades']} | "
                      f"{m['total_return']*100:+.2f}% | "
                      f"{m['max_drawdown']*100:.2f}% | {pf_s} | "
                      f"{m['win_rate']*100:.1f}% |")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote {md_path}")

    # Per-trade CSV
    cols = ["_tf", "asset", "direction", "entry_ts", "exit_ts",
            "entry_price", "exit_price", "gross_return_pct", "net_return_pct",
            "exit_reason", "bars"]
    with open(trades_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for t in all_trades_tagged:
            row = []
            for c in cols:
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
