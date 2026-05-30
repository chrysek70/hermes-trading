#!/usr/bin/env python3
"""Phase 10 — Markov-mode research sweep.

Runs the walk-forward harness across six variants and writes a summary
CSV + Markdown report. **In-sample is never reported as success.**

Variants:
  1. baseline_no_markov         — strategy alone (no regime layer)
  2. hard_filter                — v1 binary long permission
  3. soft_sizing                — continuous size_multiplier
  4. bad_regime_avoidance       — train-only PF-per-state, block bad ones in test
  5. multi_timeframe_soft_sizing — soft_sizing combined across 4h + 1d
  6. strategy_routing           — per-state allowed_setups + sizing
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import STATE_DIR, log
from hermes_trading import data as data_mod
from hermes_trading import walk_forward as wf

VARIANTS = [
    ("baseline_no_markov",          None),
    ("hard_filter",                 {"mode": "hard_filter"}),
    ("soft_sizing",                 {"mode": "soft_sizing"}),
    ("bad_regime_avoidance",        {"mode": "bad_regime_avoidance"}),
    ("multi_timeframe_soft_sizing", {"mode": "soft_sizing",
                                     "multi_timeframe": {"enabled": True,
                                                         "weights": {"4h": 0.65, "1d": 0.35}}}),
    ("strategy_routing",            {"mode": "strategy_routing",
                                     "strategy_routing": {"enabled": True}}),
]


def _apply_overrides(base: dict, overrides: dict) -> dict:
    out = copy.deepcopy(base)
    out["enabled"] = True
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
            # second-level merge
            for sk, sv in v.items():
                if isinstance(sv, dict) and isinstance(out[k].get(sk), dict):
                    out[k][sk] = {**out[k][sk], **sv}
        else:
            out[k] = v
    return out


def _pf_str(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=24)
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--strategy", default=str(STATE_DIR / "strategy_v2_long_short.yaml"))
    ap.add_argument("--markov-base", default=str(STATE_DIR / "markov_regime.yaml"))
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
    csv_path = out_dir / f"markov_research_summary_{ts}.csv"
    md_path = out_dir / f"markov_research_summary_{ts}.md"

    with open(args.strategy) as fh:
        strategy = yaml.safe_load(fh)
    with open(args.markov_base) as fh:
        base_markov_cfg = yaml.safe_load(fh)

    df = data_mod.load_klines(args.symbol, n_months=args.n_months)
    df = data_mod.resample(df, args.timeframe)
    log(f"loaded {len(df)} {args.timeframe} bars for the research sweep "
        f"(train={args.train_bars} test={args.test_bars} embargo={args.embargo_bars})")

    rows = []
    for name, overrides in VARIANTS:
        print()
        log(f"========== VARIANT: {name} ==========")
        markov_cfg = None if overrides is None else _apply_overrides(base_markov_cfg, overrides)
        try:
            res = wf.walk_forward(
                df, strategy, markov_cfg=markov_cfg,
                train_bars=args.train_bars, test_bars=args.test_bars,
                embargo_bars=args.embargo_bars,
                fee=args.fee, slippage=args.slippage,
            )
            m = res["oos_metrics"]
            fold_rets = [f["test_metrics"]["total_return"] for f in res["folds"]]
            fold_pos = sum(1 for r in fold_rets if r > 0)
            by_state_summary = {
                s: {
                    "n": v["trades"],
                    "PF": v["profit_factor"] if v["profit_factor"] != float("inf") else "inf",
                    "exp": round(v["expectancy"], 5),
                }
                for s, v in res["by_state"].items()
            }
            rows.append({
                "variant": name,
                "trades": m["trades"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "profit_factor": m["profit_factor"],
                "sharpe_per_trade": m["sharpe_per_trade"],
                "win_rate": m["win_rate"],
                "avg_win": m["avg_win"],
                "avg_loss": m["avg_loss"],
                "avg_size_multiplier": m["avg_size_multiplier"],
                "folds_pos_over_total": f"{fold_pos}/{len(res['folds'])}",
                "by_state_json": json.dumps(by_state_summary),
            })
            wf._report(res, (df.index[0].date(), df.index[-1].date()))
        except Exception as exc:  # noqa: BLE001
            log(f"[red]variant {name} failed: {exc}[/red]")
            rows.append({"variant": name, "error": str(exc)})

    # CSV
    if rows:
        cols = sorted({k for r in rows for k in r})
        with open(csv_path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        log(f"\nwrote CSV → {csv_path}")

    # Markdown
    md = [
        f"# Markov research summary — {ts}",
        "",
        f"- strategy: `{args.strategy}`",
        f"- decision timeframe: `{args.timeframe}`",
        f"- history: `{args.n_months}` months",
        f"- walk-forward: train_bars=`{args.train_bars}` test_bars=`{args.test_bars}` "
        f"embargo=`{args.embargo_bars}`",
        f"- costs: fee=`{args.fee}`/side, slippage=`{args.slippage}`",
        "",
        "| variant | n | OOS return | max DD | PF | Sharpe | win % | avg_size | folds+ |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if "error" in r:
            md.append(f"| `{r['variant']}` | — | (failed: {r['error']}) |  |  |  |  |  |  |")
            continue
        md.append(
            f"| `{r['variant']}` | {r['trades']} | "
            f"{r['total_return']*100:+.2f}% | {r['max_drawdown']*100:.2f}% | "
            f"{_pf_str(r['profit_factor'])} | {r['sharpe_per_trade']:.3f} | "
            f"{r['win_rate']*100:.1f}% | {r['avg_size_multiplier']:.2f} | "
            f"{r['folds_pos_over_total']} |"
        )
    md += [
        "",
        "### How to read",
        "",
        "All numbers above are stitched out-of-sample across walk-forward folds.",
        "No variant saw the data it was evaluated on at fit time. The bad-regime",
        "avoidance variant fits its 'bad state' set on the TRAIN slice of each",
        "fold only.",
        "",
        "`folds+` is the count of folds with positive OOS return; a useful",
        "consistency check independent of the aggregate.",
    ]
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")


if __name__ == "__main__":
    main()
