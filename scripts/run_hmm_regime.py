#!/usr/bin/env python3
"""HMM 2-state regime overlay walk-forward experiment.

Reproducible runner for Issue #6. Tests whether a Gaussian HMM fitted
on causal market features per train fold improves SuperTrend(10, 3)
risk-adjusted performance on BTC and ETH separately.

Six variants:
  1. supertrend_only_btc           — baseline reference
  2. supertrend_hmm_filter_btc     — binary long_allowed gate
  3. supertrend_hmm_sizing_btc     — full / half / 0 sizing
  4. supertrend_only_eth           — baseline reference (ETH)
  5. supertrend_hmm_filter_eth     — binary long_allowed gate
  6. supertrend_hmm_sizing_eth     — full / half / 0 sizing

Adoption criteria (from Issue #6 spec — different per asset):

  BTC:  PF > 2.24 AND trades >= 30 AND max DD <= 9.63%
  ETH:  PF > 2.92 AND trades >= 30 AND max DD <= 5.30%

Hard rules — do NOT change these in this script:
  - SuperTrend (10, 3) unchanged.
  - HMM config from yaml; NO sweeps.
  - Per-fold HMM fitting on train only; map_states on train only; test
    uses the trained model without any test-data leakage.
  - Same fees / slippage / fold geometry as every other experiment.
  - If hmmlearn is missing, print install hint and exit cleanly.

Outputs (under --out-dir, default `results/`):
  - hmm_regime_comparison_<ts>.csv
  - hmm_regime_comparison_<ts>.md
  - trades_hmm_regime_btc_<ts>.csv
  - trades_hmm_regime_eth_<ts>.csv
"""
from __future__ import annotations

import argparse
import csv
import json
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
from hermes_trading import hmm_regime as hmmr
from hermes_trading import signals
from hermes_trading import walk_forward as wf


DEFAULT_HMM_CFG = hmmr.DEFAULT_CONFIG


def _run_solo(name, df, strategy,
              train_bars, test_bars, embargo_bars, fee, slippage):
    log(f"========== {name} ==========")
    ind_full = signals.compute_indicators(df, strategy)
    n = len(ind_full)
    folds = []
    all_trades: list[dict] = []
    fold = 0
    cursor = 0
    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo_bars
        test_hi = test_lo + test_bars
        if test_hi > n:
            break
        fold += 1
        test_ind = bt._attach_neutral_markov_columns(
            ind_full.iloc[test_lo:test_hi].copy())
        res = bt._run_state_machine(test_ind.to_dict("records"), strategy,
                                    warmup=0, fee=fee, slippage=slippage)
        for t in res["trades"]:
            t["asset"] = name.split("_")[-1].upper()
            t["_variant"] = name
            t["hmm_state"] = None
            t["hmm_p_favorable"] = None
            t["hmm_p_adverse"] = None
            t["hmm_size_multiplier"] = 1.0
            t["hmm_decision_reason"] = "no_hmm"
        all_trades.extend(res["trades"])
        folds.append({"fold": fold, "test_range": (test_ind.index[0].date(),
                                                   test_ind.index[-1].date()),
                      "test_metrics": res["metrics"]})
        cursor += test_bars
    return _summarize(name, all_trades, folds, fold_mappings=[])


def _run_hmm_variant(name, df, strategy, hmm_cfg, mode,
                     train_bars, test_bars, embargo_bars, fee, slippage):
    log(f"========== {name} ==========")
    ind_full = signals.compute_indicators(df, strategy)
    n = len(ind_full)
    folds = []
    all_trades: list[dict] = []
    fold_mappings: list[dict] = []
    fold = 0
    cursor = 0
    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo_bars
        test_hi = test_lo + test_bars
        if test_hi > n:
            break
        fold += 1
        train_df = df.iloc[cursor:train_hi]
        train_ind = ind_full.iloc[cursor:train_hi]
        test_df = df.iloc[test_lo:test_hi]
        test_ind = ind_full.iloc[test_lo:test_hi]

        try:
            det = hmmr.HMMRegimeDetector(hmm_cfg)
            det.fit(train_df, indicators=train_ind)
            # State mapping uses train-only stats + (best-effort)
            # train SuperTrend trade expectancy by state. Run a quick
            # in-sample SuperTrend backtest on train with HMM state tags
            # attached so the mapper sees per-state expectancy. This is
            # train-only — no test leakage.
            train_proba = det.predict_proba(train_df, indicators=train_ind)
            prob_cols = [c for c in train_proba.columns if c.startswith("p_state_")]
            # pandas 2.x idxmax errors on all-NaN rows; drop them first and reindex back.
            tp_clean = train_proba[prob_cols].dropna()
            train_states = tp_clean.idxmax(axis=1).str.replace("p_state_", "").astype(float).reindex(train_proba.index)
            train_ind_tagged = bt._attach_neutral_markov_columns(train_ind.copy())
            train_ind_tagged["hmm_state_at_entry"] = train_states.reindex(train_ind.index).values
            train_records = train_ind_tagged.to_dict("records")
            train_res = bt._run_state_machine(train_records, strategy,
                                              warmup=0, fee=fee, slippage=slippage)
            for t in train_res["trades"]:
                # tag each train trade with its HMM state at entry index
                idx = t.get("entry_ts")
                if idx is not None and idx in train_states.index:
                    t["hmm_state_at_entry"] = int(train_states.loc[idx]) if not pd.isna(train_states.loc[idx]) else None
            mapping = det.map_states(train_trades=train_res["trades"])
        except Exception as exc:  # noqa: BLE001
            log(f"  fold {fold}: HMM fit failed ({exc}); using neutral decisions")
            test_ind_neutral = bt._attach_neutral_markov_columns(test_ind.copy())
            res = bt._run_state_machine(test_ind_neutral.to_dict("records"),
                                        strategy, warmup=0, fee=fee, slippage=slippage)
            for t in res["trades"]:
                t["hmm_state"] = None; t["hmm_p_favorable"] = None
                t["hmm_p_adverse"] = None; t["hmm_size_multiplier"] = 1.0
                t["hmm_decision_reason"] = "hmm_fit_failed"
                t["asset"] = name.split("_")[-1].upper(); t["_variant"] = name
            all_trades.extend(res["trades"])
            folds.append({"fold": fold, "test_metrics": res["metrics"],
                          "test_range": (test_ind.index[0].date(),
                                         test_ind.index[-1].date())})
            cursor += test_bars
            continue

        # apply HMM decisions to test
        decisions = det.decisions(test_df, indicators=test_ind, mode=mode)
        fold_mappings.append({
            "fold": fold,
            "mapping": mapping,
            "state_stats": det.state_stats_,
            "mean_p_favorable_test": float(decisions["p_favorable"].dropna().mean()) if decisions["p_favorable"].dropna().size else None,
            "test_range": (test_ind.index[0].date(), test_ind.index[-1].date()),
        })
        test_ind_decided = bt._attach_decisions_df(test_ind.copy(), decisions[[
            "long_allowed", "size_multiplier", "raw_state", "stable_state",
            "regime_score", "allowed_setups",
        ]])
        res = bt._run_state_machine(test_ind_decided.to_dict("records"),
                                    strategy, warmup=0, fee=fee, slippage=slippage)
        for t in res["trades"]:
            t["asset"] = name.split("_")[-1].upper(); t["_variant"] = name
            etx = t.get("entry_ts")
            if etx is not None and etx in decisions.index and not pd.isna(decisions.loc[etx, "p_favorable"]):
                t["hmm_state"] = str(decisions.loc[etx, "raw_state"])
                t["hmm_p_favorable"] = float(decisions.loc[etx, "p_favorable"])
                t["hmm_p_adverse"] = float(decisions.loc[etx, "p_adverse"])
                t["hmm_size_multiplier"] = float(decisions.loc[etx, "size_multiplier"])
                t["hmm_decision_reason"] = f"{mode}_p_fav={t['hmm_p_favorable']:.2f}"
            else:
                t["hmm_state"] = None; t["hmm_p_favorable"] = None
                t["hmm_p_adverse"] = None; t["hmm_size_multiplier"] = 1.0
                t["hmm_decision_reason"] = "no_decision_row"
        all_trades.extend(res["trades"])
        folds.append({"fold": fold, "test_metrics": res["metrics"],
                      "test_range": (test_ind.index[0].date(),
                                     test_ind.index[-1].date())})
        cursor += test_bars

    return _summarize(name, all_trades, folds, fold_mappings)


def _summarize(name, all_trades, folds, fold_mappings):
    oos = wf._stitch_metrics(all_trades)
    fold_returns = [f["test_metrics"]["total_return"] for f in folds]
    fold_pos = sum(1 for r in fold_returns if r > 0)
    pf = oos["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"Sharpe={oos['sharpe_per_trade']:.3f}  win={oos['win_rate']*100:.1f}%  "
        f"folds+={fold_pos}/{len(folds)}")
    return {"name": name, "oos": oos, "folds": folds, "trades": all_trades,
            "fold_pos": fold_pos, "fold_returns": fold_returns,
            "fold_mappings": fold_mappings}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--btc-symbol", default="BTCUSDT")
    ap.add_argument("--eth-symbol", default="ETHUSDT")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--supertrend-strategy",
                    default=str(STATE_DIR / "strategy_supertrend.yaml"))
    ap.add_argument("--hmm-config",
                    default=str(STATE_DIR / "hmm_regime.yaml"),
                    help="HMM yaml; falls back to module defaults if missing")
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    if not hmmr.available():
        log(f"[red]{hmmr.INSTALL_HINT}[/red]")
        log(f"underlying import error: {hmmr.import_error()}")
        sys.exit(2)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"hmm_regime_comparison_{ts}.csv"
    md_path = out_dir / f"hmm_regime_comparison_{ts}.md"
    btc_trades_path = out_dir / f"trades_hmm_regime_btc_{ts}.csv"
    eth_trades_path = out_dir / f"trades_hmm_regime_eth_{ts}.csv"
    mappings_path = out_dir / f".hmm_fold_mappings_{ts}.json"

    st_strategy = yaml.safe_load(open(args.supertrend_strategy))
    hmm_cfg_path = Path(args.hmm_config)
    if hmm_cfg_path.exists():
        hmm_cfg = yaml.safe_load(open(hmm_cfg_path)).get("hmm_regime", DEFAULT_HMM_CFG)
    else:
        log(f"hmm config {hmm_cfg_path} not found; using module defaults")
        hmm_cfg = DEFAULT_HMM_CFG

    log(f"loading BTC + ETH {args.n_months}mo …")
    btc_df = data_mod.resample(data_mod.load_klines(args.btc_symbol, n_months=args.n_months), args.timeframe)
    eth_df = data_mod.resample(data_mod.load_klines(args.eth_symbol, n_months=args.n_months), args.timeframe)
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]; eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    results = []
    results.append(_run_solo("supertrend_only_btc", btc_df, st_strategy,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage))
    results.append(_run_hmm_variant("supertrend_hmm_filter_btc", btc_df, st_strategy,
                                    hmm_cfg, "filter",
                                    args.train_bars, args.test_bars, args.embargo_bars,
                                    args.fee, args.slippage))
    results.append(_run_hmm_variant("supertrend_hmm_sizing_btc", btc_df, st_strategy,
                                    hmm_cfg, "sizing",
                                    args.train_bars, args.test_bars, args.embargo_bars,
                                    args.fee, args.slippage))
    results.append(_run_solo("supertrend_only_eth", eth_df, st_strategy,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage))
    results.append(_run_hmm_variant("supertrend_hmm_filter_eth", eth_df, st_strategy,
                                    hmm_cfg, "filter",
                                    args.train_bars, args.test_bars, args.embargo_bars,
                                    args.fee, args.slippage))
    results.append(_run_hmm_variant("supertrend_hmm_sizing_eth", eth_df, st_strategy,
                                    hmm_cfg, "sizing",
                                    args.train_bars, args.test_bars, args.embargo_bars,
                                    args.fee, args.slippage))

    rows = []
    btc_trades = []
    eth_trades = []
    for r in results:
        o = r["oos"]
        for t in r["trades"]:
            if t.get("asset") == "BTC":
                btc_trades.append(t)
            elif t.get("asset") == "ETH":
                eth_trades.append(t)
        fr = r["fold_returns"]
        fold_std = float(np.std(fr)) if fr else 0.0
        by_state = {}
        for t in r["trades"]:
            s = t.get("hmm_state") or "no_hmm"
            by_state[s] = by_state.get(s, 0) + 1
        rows.append({
            "variant": r["name"],
            "trades": o["trades"],
            "total_return": o["total_return"],
            "max_drawdown": o["max_drawdown"],
            "profit_factor": (o["profit_factor"] if o["profit_factor"] != float("inf") else 9999.0),
            "sharpe_per_trade": o["sharpe_per_trade"],
            "win_rate": o["win_rate"],
            "avg_win": o["avg_win"],
            "avg_loss": o["avg_loss"],
            "avg_size_multiplier": o["avg_size_multiplier"],
            "n_folds": len(r["folds"]),
            "fold_positive": f"{r['fold_pos']}/{len(r['folds'])}",
            "fold_return_std": fold_std,
            "by_state": "; ".join(f"{k}:{v}" for k, v in by_state.items()),
        })

    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# HMM regime overlay — {ts}",
        "",
        f"- supertrend strategy: `{args.supertrend_strategy}`",
        f"- HMM config: `{args.hmm_config}` (or defaults if missing)",
        f"- BTC: {args.n_months}mo ({btc_df.index[0].date()} -> {btc_df.index[-1].date()})",
        f"- ETH: {args.n_months}mo (aligned, {len(common)} bars)",
        f"- decision TF: {args.timeframe}",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "Adoption criteria — BTC: PF > 2.24 AND trades >= 30 AND DD <= 9.63%. "
        "ETH: PF > 2.92 AND trades >= 30 AND DD <= 5.30%.",
        "",
        "| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | by state |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == 9999.0 else f"{pf:.2f}"
        md.append(f"| `{r['variant']}` | {r['n_folds']} | {r['trades']} | "
                  f"{r['total_return']*100:+.2f}% | {r['max_drawdown']*100:.2f}% | "
                  f"{pf_s} | {r['sharpe_per_trade']:.3f} | "
                  f"{r['win_rate']*100:.1f}% | {r['fold_positive']} | "
                  f"{r['by_state']} |")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    def write_trades_csv(path, trades):
        cols = ["asset", "_variant", "entry_ts", "exit_ts", "setup_name",
                "entry_price", "exit_price", "gross_return_pct",
                "net_return_pct", "position_size_effective",
                "hmm_state", "hmm_p_favorable", "hmm_p_adverse",
                "hmm_size_multiplier", "hmm_decision_reason",
                "exit_reason", "holding_bars"]
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for t in trades:
                row = []
                for c in cols:
                    v = t.get(c)
                    if hasattr(v, "isoformat"):
                        v = v.isoformat()
                    if v is None:
                        v = ""
                    row.append(v)
                w.writerow(row)
    write_trades_csv(btc_trades_path, btc_trades)
    write_trades_csv(eth_trades_path, eth_trades)
    log(f"wrote BTC trades CSV ({len(btc_trades)} rows) → {btc_trades_path}")
    log(f"wrote ETH trades CSV ({len(eth_trades)} rows) → {eth_trades_path}")

    # fold-level mappings JSON for the report
    fm_dump = {r["name"]: r["fold_mappings"] for r in results if r["fold_mappings"]}
    with open(mappings_path, "w") as fh:
        json.dump(fm_dump, fh, default=str, indent=2)
    log(f"wrote fold mappings → {mappings_path}")


if __name__ == "__main__":
    main()
