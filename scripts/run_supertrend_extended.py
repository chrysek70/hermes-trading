#!/usr/bin/env python3
"""SuperTrend(10, 3) extended-history walk-forward.

Reproducible runner for the experiment run for Issue #4 (24 months) and
Issue #11 (48 months). Runs three variants on the same BTC 4h data with
identical walk-forward geometry, costs, and parameters:

  1. baseline                  — state/strategy_v2_long_short.yaml, no Markov
  2. supertrend_only           — state/strategy_supertrend.yaml, no Markov
  3. supertrend_plus_routing   — state/strategy_supertrend.yaml + Markov
                                 strategy_routing, with `supertrend` added
                                 to the up_low_vol and up_high_vol routes

Adoption criteria are locked at the project level (`ROADMAP.md`):
    OOS profit factor > 1.69 AND OOS trade count >= 30.

Hard rules — do NOT change these in this script:
  - SuperTrend (period, multiplier) stays at (10, 3.0).
  - No parameter sweeps. To test a different SuperTrend config, write a
    new experiment with a new issue number, do not modify this one.
  - Walk-forward only. No in-sample reporting.
  - Same fees/slippage as every other experiment in this repo.

Outputs (under --out-dir, default `results/`):
  - supertrend_<n>mo_comparison_<ts>.csv
  - supertrend_<n>mo_comparison_<ts>.md
  - trades_supertrend_<n>mo_detailed_<ts>.csv  (the two SuperTrend variants)
"""
from __future__ import annotations

import argparse
import copy
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
from hermes_trading import walk_forward as wf


def _pf_str(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def _build_routing_cfg(base_markov: dict) -> dict:
    """Take the standard Markov yaml and switch it into strategy_routing
    mode, with `supertrend` appended to the up_* state routes."""
    cfg = copy.deepcopy(base_markov)
    cfg["enabled"] = True
    cfg["mode"] = "strategy_routing"
    cfg["multi_timeframe"] = {"enabled": False}
    routes = cfg.setdefault("strategy_routing", {}).setdefault("routes", {})
    for state in ("up_low_vol", "up_high_vol"):
        if state in routes:
            allowed = list(routes[state].get("allowed_setups", []))
            if "supertrend" not in allowed:
                allowed.append("supertrend")
                routes[state]["allowed_setups"] = allowed
    return cfg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48,
                    help="months of history (default 48 — Issue #11 setup)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--baseline-strategy",
                    default=str(STATE_DIR / "strategy_v2_long_short.yaml"))
    ap.add_argument("--supertrend-strategy",
                    default=str(STATE_DIR / "strategy_supertrend.yaml"))
    ap.add_argument("--markov-base",
                    default=str(STATE_DIR / "markov_regime.yaml"))
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
    csv_path = out_dir / f"supertrend_{args.n_months}mo_comparison_{ts}.csv"
    md_path = out_dir / f"supertrend_{args.n_months}mo_comparison_{ts}.md"
    trades_path = out_dir / f"trades_supertrend_{args.n_months}mo_detailed_{ts}.csv"

    base_strategy = yaml.safe_load(open(args.baseline_strategy))
    st_strategy = yaml.safe_load(open(args.supertrend_strategy))
    base_markov = yaml.safe_load(open(args.markov_base))
    routing_cfg = _build_routing_cfg(base_markov)

    df = data_mod.resample(
        data_mod.load_klines(args.symbol, n_months=args.n_months), args.timeframe,
    )
    log(f"loaded {len(df)} {args.timeframe} bars  "
        f"span: {df.index[0].date()} -> {df.index[-1].date()}")

    variants = [
        ("baseline", base_strategy, None),
        ("supertrend_only", st_strategy, None),
        ("supertrend_plus_routing", st_strategy, routing_cfg),
    ]

    rows = []
    all_st_trades: list[dict] = []
    for name, strategy, markov_cfg in variants:
        print()
        log(f"========== {name} ==========")
        res = wf.walk_forward(
            df, strategy, markov_cfg=markov_cfg,
            train_bars=args.train_bars, test_bars=args.test_bars,
            embargo_bars=args.embargo_bars,
            fee=args.fee, slippage=args.slippage,
        )
        m = res["oos_metrics"]
        fold_pos = sum(1 for f in res["folds"]
                       if f["test_metrics"]["total_return"] > 0)
        if "supertrend" in name:
            for t in res["trades"]:
                t["_variant"] = name
                all_st_trades.append(t)
        by_setup: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for t in res["trades"]:
            by_setup[t.get("setup", "?")] = by_setup.get(t.get("setup", "?"), 0) + 1
            by_reason[t.get("reason", "?")] = by_reason.get(t.get("reason", "?"), 0) + 1
        rows.append({
            "variant": name,
            "trades": m["trades"],
            "total_return": m["total_return"],
            "max_drawdown": m["max_drawdown"],
            "profit_factor": (m["profit_factor"]
                              if m["profit_factor"] != float("inf") else 9999.0),
            "sharpe_per_trade": m["sharpe_per_trade"],
            "win_rate": m["win_rate"],
            "avg_win": m["avg_win"],
            "avg_loss": m["avg_loss"],
            "avg_size_multiplier": m["avg_size_multiplier"],
            "n_folds": len(res["folds"]),
            "fold_positive": f"{fold_pos}/{len(res['folds'])}",
            "by_setup": "; ".join(f"{k}:{v}" for k, v in by_setup.items()),
            "by_reason": "; ".join(f"{k}:{v}" for k, v in by_reason.items()),
        })
        wf._report(res, (df.index[0].date(), df.index[-1].date()))

    cols = ["variant", "trades", "total_return", "max_drawdown",
            "profit_factor", "sharpe_per_trade", "win_rate",
            "avg_win", "avg_loss", "avg_size_multiplier",
            "n_folds", "fold_positive", "by_setup", "by_reason"]
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# SuperTrend(10, 3) on {args.n_months}-month history — {ts}",
        "",
        f"- baseline strategy: `{args.baseline_strategy}`",
        f"- supertrend strategy: `{args.supertrend_strategy}`",
        f"- history: {args.n_months} months {args.symbol} "
        f"({df.index[0].date()} -> {df.index[-1].date()})",
        f"- decision TF: {args.timeframe}",
        f"- walk-forward: train_bars={args.train_bars} / test_bars={args.test_bars} "
        f"/ embargo_bars={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
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
    md += ["", "### By setup", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_setup']}")
    md += ["", "### By exit reason", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_reason']}")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    if all_st_trades:
        bt.write_trades_detailed_csv(all_st_trades, str(trades_path))
        log(f"wrote detailed SuperTrend trades CSV "
            f"({len(all_st_trades)} rows) → {trades_path}")


if __name__ == "__main__":
    main()
