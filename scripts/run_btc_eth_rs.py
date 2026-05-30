#!/usr/bin/env python3
"""BTC/ETH relative-strength walk-forward experiment.

Reproducible runner for Issue #5. ETH is used as **market context only**
(not a traded asset). The script loads BTC and ETH at the same timeframe,
aligns timestamps, computes causal RS features (using prior closes only),
and applies them via the existing decisions_df plumbing as either:

  - filter: long entries blocked unless both gates pass
  - sizing: scale size 1.0 / 0.5 / 0.0 by how many gates pass

Four variants are walk-forwarded on identical folds and costs:

  1. baseline_v2                          — state/strategy_v2_long_short.yaml
  2. supertrend_only                      — state/strategy_supertrend.yaml
  3. supertrend_with_btc_eth_rs_filter    — supertrend + RS filter overlay
  4. supertrend_with_btc_eth_rs_sizing    — supertrend + RS sizing overlay

Adoption criteria for the RS variants (from Issue #5 spec):
  (a) beat supertrend_only PF, OR
  (b) meaningfully reduce max DD while keeping PF > supertrend_only AND
      trade count >= 30.

Hard rules — do NOT change these in this script:
  - RS windows (lookback_bars, ratio_ema) come from
    state/strategy_supertrend_rs.yaml. No parameter sweeps. To try a
    different RS config, write a new experiment with a new issue
    number, do not modify this one.
  - Walk-forward only.
  - Same fees/slippage as every other experiment in this repo.

Outputs (under --out-dir, default `results/`):
  - btc_eth_rs_comparison_<ts>.csv
  - btc_eth_rs_comparison_<ts>.md
  - trades_btc_eth_rs_detailed_<ts>.csv  (supertrend + RS variants only)
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import STATE_DIR, log
from hermes_trading import backtest as bt
from hermes_trading import data as data_mod
from hermes_trading import relative_strength as rs
from hermes_trading import signals
from hermes_trading import walk_forward as wf


def _run_variant(name, btc_df, strategy, decisions_full,
                 train_bars, test_bars, embargo_bars, fee, slippage):
    """Walk-forward driver that can take a precomputed decisions_full
    DataFrame and slice it per fold. Mirrors wf.walk_forward's fold loop."""
    log(f"========== {name} ==========")
    ind_full = signals.compute_indicators(btc_df, strategy)
    n = len(ind_full)
    folds = []
    all_trades = []
    fold = 0
    cursor = 0
    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo_bars
        test_hi = test_lo + test_bars
        if test_hi > n:
            break
        fold += 1
        test_ind = ind_full.iloc[test_lo:test_hi].copy()
        if decisions_full is not None:
            test_ind = bt._attach_decisions_df(
                test_ind, decisions_full.loc[test_ind.index],
            )
        else:
            test_ind = bt._attach_neutral_markov_columns(test_ind)
        res = bt._run_state_machine(
            test_ind.to_dict("records"), strategy,
            warmup=0, fee=fee, slippage=slippage,
        )
        all_trades.extend(res["trades"])
        folds.append({"fold": fold, "test_metrics": res["metrics"]})
        cursor += test_bars

    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for f in folds if f["test_metrics"]["total_return"] > 0)
    pf = oos["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"Sharpe={oos['sharpe_per_trade']:.3f}  win={oos['win_rate']*100:.1f}%  "
        f"folds+={fold_pos}/{len(folds)}")
    return {"name": name, "oos": oos, "folds": folds,
            "trades": all_trades, "fold_pos": fold_pos}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--btc-symbol", default="BTCUSDT")
    ap.add_argument("--eth-symbol", default="ETHUSDT")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--baseline-strategy",
                    default=str(STATE_DIR / "strategy_v2_long_short.yaml"))
    ap.add_argument("--supertrend-strategy",
                    default=str(STATE_DIR / "strategy_supertrend.yaml"))
    ap.add_argument("--rs-strategy",
                    default=str(STATE_DIR / "strategy_supertrend_rs.yaml"))
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"btc_eth_rs_comparison_{ts}.csv"
    md_path = out_dir / f"btc_eth_rs_comparison_{ts}.md"
    trades_path = out_dir / f"trades_btc_eth_rs_detailed_{ts}.csv"

    base_strategy = yaml.safe_load(open(args.baseline_strategy))
    st_strategy = yaml.safe_load(open(args.supertrend_strategy))
    rs_cfg = yaml.safe_load(open(args.rs_strategy))["relative_strength"]

    log(f"loading {args.btc_symbol} {args.n_months}mo …")
    btc_df = data_mod.resample(
        data_mod.load_klines(args.btc_symbol, n_months=args.n_months),
        args.timeframe,
    )
    log(f"loading {args.eth_symbol} {args.n_months}mo …")
    eth_df = data_mod.resample(
        data_mod.load_klines(args.eth_symbol, n_months=args.n_months),
        args.timeframe,
    )

    # Align on intersection — drop any unmatched bars.
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]
    eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  "
        f"span: {common[0].date()} -> {common[-1].date()}")

    # RS decisions are computed once on the full aligned index then sliced
    # per fold. OOS-safe because no parameter is fit on train.
    rs_filter = rs.build_decisions(
        btc_df, eth_df, mode="filter",
        lookback_bars=rs_cfg["lookback_bars"],
        ratio_ema=rs_cfg["ratio_ema"],
        min_btc_minus_eth_return=rs_cfg["min_btc_minus_eth_return"],
        require_ratio_above_ema=rs_cfg["require_ratio_above_ema"],
    )
    rs_sizing = rs.build_decisions(
        btc_df, eth_df, mode="sizing",
        lookback_bars=rs_cfg["lookback_bars"],
        ratio_ema=rs_cfg["ratio_ema"],
        min_btc_minus_eth_return=rs_cfg["min_btc_minus_eth_return"],
        require_ratio_above_ema=rs_cfg["require_ratio_above_ema"],
    )
    post_warm = rs_filter[rs_filter["raw_state"] != "rs_warmup"]
    dist = post_warm["raw_state"].value_counts().to_dict()
    log(f"RS state dist (post-warmup, {len(post_warm)} bars): "
        + ", ".join(f"{k}={v}" for k, v in dist.items()))

    variants = [
        ("baseline_v2",                       base_strategy, None),
        ("supertrend_only",                   st_strategy,   None),
        ("supertrend_with_btc_eth_rs_filter", st_strategy,   rs_filter),
        ("supertrend_with_btc_eth_rs_sizing", st_strategy,   rs_sizing),
    ]

    results = []
    for name, strategy, decisions_full in variants:
        results.append(_run_variant(
            name, btc_df, strategy, decisions_full,
            args.train_bars, args.test_bars, args.embargo_bars,
            args.fee, args.slippage,
        ))

    rows = []
    all_rs_trades = []
    for r in results:
        by_state: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for t in r["trades"]:
            s = (t.get("markov_stable_state") or t.get("markov_state")
                 or "unknown")
            by_state[s] = by_state.get(s, 0) + 1
            rsn = t.get("reason", "?")
            by_reason[rsn] = by_reason.get(rsn, 0) + 1
        if "supertrend" in r["name"]:
            for t in r["trades"]:
                t["_variant"] = r["name"]
                all_rs_trades.append(t)
        o = r["oos"]
        rows.append({
            "variant": r["name"],
            "trades": o["trades"],
            "total_return": o["total_return"],
            "max_drawdown": o["max_drawdown"],
            "profit_factor": (o["profit_factor"]
                              if o["profit_factor"] != float("inf") else 9999.0),
            "sharpe_per_trade": o["sharpe_per_trade"],
            "win_rate": o["win_rate"],
            "avg_win": o["avg_win"],
            "avg_loss": o["avg_loss"],
            "avg_size_multiplier": o["avg_size_multiplier"],
            "n_folds": len(r["folds"]),
            "fold_positive": f"{r['fold_pos']}/{len(r['folds'])}",
            "by_state": "; ".join(f"{k}:{v}" for k, v in by_state.items()),
            "by_reason": "; ".join(f"{k}:{v}" for k, v in by_reason.items()),
        })

    cols = ["variant", "trades", "total_return", "max_drawdown",
            "profit_factor", "sharpe_per_trade", "win_rate",
            "avg_win", "avg_loss", "avg_size_multiplier",
            "n_folds", "fold_positive", "by_state", "by_reason"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# BTC/ETH relative-strength experiment — {ts}",
        "",
        f"- baseline strategy: `{args.baseline_strategy}`",
        f"- supertrend strategy: `{args.supertrend_strategy}`",
        f"- RS-enabled strategy: `{args.rs_strategy}`",
        f"- BTC history: {args.n_months} months "
        f"({btc_df.index[0].date()} -> {btc_df.index[-1].date()})",
        f"- ETH history: {args.n_months} months (aligned, {len(common)} bars)",
        f"- decision TF: {args.timeframe}",
        f"- walk-forward: train_bars={args.train_bars} / "
        f"test_bars={args.test_bars} / embargo_bars={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        f"- RS config: lookback={rs_cfg['lookback_bars']}, "
        f"ratio_ema={rs_cfg['ratio_ema']}, "
        f"min_btc_minus_eth_return={rs_cfg['min_btc_minus_eth_return']}, "
        f"require_ratio_above_ema={rs_cfg['require_ratio_above_ema']}",
        "",
        "| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == 9999.0 else f"{pf:.2f}"
        md.append(f"| `{r['variant']}` | {r['n_folds']} | {r['trades']} | "
                  f"{r['total_return']*100:+.2f}% | {r['max_drawdown']*100:.2f}% | "
                  f"{pf_s} | {r['sharpe_per_trade']:.3f} | "
                  f"{r['win_rate']*100:.1f}% | {r['fold_positive']} |")
    md += ["", "### By RS / regime state", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_state']}")
    md += ["", "### By exit reason", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_reason']}")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    if all_rs_trades:
        bt.write_trades_detailed_csv(all_rs_trades, str(trades_path))
        log(f"wrote detailed trades CSV ({len(all_rs_trades)} rows) → "
            f"{trades_path}")


if __name__ == "__main__":
    main()
