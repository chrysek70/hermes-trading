#!/usr/bin/env python3
"""Funding-rate filter / sizing overlay experiment.

Reproducible runner for Issue #7 Phase 3. Tests the spec's locked
filter rule: block new longs when the rolling 30-day funding
percentile is >= 95 (filter mode), or scale size (full / half / zero
at percentile 90 / 95 thresholds in sizing mode). No parameter
sweeps. No live wiring.

Five variants per spec:
  1. eth_supertrend_baseline               (Issue #12 adopted ETH-solo)
  2. eth_supertrend_funding_filter
  3. btc_eth_parallel_baseline             (Issue #14 adopted)
  4. btc_eth_parallel_funding_filter
  5. btc_eth_parallel_funding_sizing

Variants 1 and 3 are the baselines that variants 2/4/5 must beat
(PF improvement OR DD reduction without destroying trade count).

Hard rules — do NOT change in this script:
  - SuperTrend (10, 3) unchanged.
  - Filter percentiles fixed at p95 (block) / p90 (half-size).
  - Rolling percentile window fixed at 180 bars (~30 days at 4h).
  - Funding aligned via forward-fill — no future funding ever applied
    to a past bar.
  - Same fees / slippage / fold geometry as every other experiment.

Outputs (under --out-dir, default `results/`):
  - funding_rate_comparison_<ts>.csv
  - funding_rate_comparison_<ts>.md
  - trades_funding_<ts>.csv
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
from hermes_trading import funding as funding_mod
from hermes_trading import signals
from hermes_trading import walk_forward as wf


def build_funding_decisions(funding_pct: pd.Series, mode: str,
                            block_above: float = 95.0,
                            half_above: float = 90.0) -> pd.DataFrame:
    """Build a decisions_df from funding percentile rank.

    ``filter`` mode: long_allowed = False when percentile >= block_above
    (size_mult always = 1.0).
    ``sizing`` mode: full / half / zero at the configured thresholds.
    During warmup (percentile is NaN), default to allowed + full size.
    """
    idx = funding_pct.index
    warmup = funding_pct.isna()
    if mode == "filter":
        long_allowed = funding_pct < block_above
        size_mult = pd.Series(1.0, index=idx, dtype=float)
        long_allowed = long_allowed.where(~warmup, True)
    elif mode == "sizing":
        size_mult = pd.Series(1.0, index=idx, dtype=float)
        size_mult = size_mult.mask(funding_pct >= half_above, 0.5)
        size_mult = size_mult.mask(funding_pct >= block_above, 0.0)
        long_allowed = size_mult > 0
        size_mult = size_mult.where(~warmup, 1.0)
        long_allowed = long_allowed.where(~warmup, True)
    else:
        raise ValueError(f"unknown mode: {mode!r}")
    raw_state = pd.Series("funding_normal", index=idx, dtype=object)
    raw_state = raw_state.mask(funding_pct >= half_above, "funding_overheated")
    raw_state = raw_state.mask(funding_pct >= block_above, "funding_extreme")
    raw_state = raw_state.where(~warmup, "funding_warmup")
    return pd.DataFrame({
        "long_allowed": long_allowed.astype(bool),
        "size_multiplier": size_mult.astype(float),
        "raw_state": raw_state,
        "stable_state": raw_state,
        "regime_score": (100.0 - funding_pct.fillna(0)) / 100.0,
        "allowed_setups": pd.Series([None] * len(idx), index=idx, dtype=object),
        "funding_percentile": funding_pct,
    }, index=idx)


# ---- single-asset solo (variants 1, 2) -------------------------------------

def _run_solo(name, df, strategy, decisions_full,
              train_bars, test_bars, embargo_bars, fee, slippage, asset_label):
    log(f"========== {name} ==========")
    ind_full = signals.compute_indicators(df, strategy)
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
                test_ind, decisions_full.loc[test_ind.index][[
                    "long_allowed", "size_multiplier", "raw_state",
                    "stable_state", "regime_score", "allowed_setups",
                ]],
            )
            test_ind["funding_percentile"] = decisions_full["funding_percentile"].reindex(test_ind.index).values
        else:
            test_ind = bt._attach_neutral_markov_columns(test_ind)
            test_ind["funding_percentile"] = np.nan
        res = bt._run_state_machine(test_ind.to_dict("records"), strategy,
                                    warmup=0, fee=fee, slippage=slippage)
        for t in res["trades"]:
            t["_variant"] = name
            t["asset"] = asset_label
            etx = t.get("entry_ts")
            if etx is not None and etx in test_ind.index:
                fp = test_ind.loc[etx, "funding_percentile"]
                t["funding_percentile"] = float(fp) if pd.notna(fp) else None
                rs = test_ind.loc[etx].get("markov_state")
                t["funding_state"] = str(rs) if rs is not None else None
            else:
                t["funding_percentile"] = None
                t["funding_state"] = None
        all_trades.extend(res["trades"])
        folds.append({"fold": fold, "test_metrics": res["metrics"]})
        cursor += test_bars
    return _summarize(name, all_trades, folds)


# ---- parallel portfolio (variants 3, 4, 5) ---------------------------------

def _run_parallel(name, asset_data, strategy, decisions_by_asset,
                  train_bars, test_bars, embargo_bars, fee, slippage):
    log(f"========== {name} ==========")
    n_assets = len(asset_data)
    if n_assets == 0:
        return None
    size_per_asset = 1.0 / n_assets
    base_size = float(strategy["risk"].get("position_size_r", 0.5))

    any_asset = next(iter(asset_data.values()))
    full_index = any_asset["df"].index
    n_bars = len(full_index)

    folds = []
    all_trades = []
    fold_returns = []
    fold = 0
    cursor = 0
    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo_bars
        test_hi = test_lo + test_bars
        if test_hi > n_bars:
            break
        fold += 1
        test_index_slice = full_index[test_lo:test_hi]

        asset_records = {}
        for asset, d in asset_data.items():
            test_ind = d["ind"].iloc[test_lo:test_hi].copy()
            test_ind["ts"] = test_ind.index
            decisions = decisions_by_asset.get(asset)
            if decisions is not None:
                test_ind = bt._attach_decisions_df(
                    test_ind, decisions.loc[test_ind.index][[
                        "long_allowed", "size_multiplier", "raw_state",
                        "stable_state", "regime_score", "allowed_setups",
                    ]],
                )
                test_ind["funding_percentile"] = decisions["funding_percentile"].reindex(test_ind.index).values
            else:
                test_ind = bt._attach_neutral_markov_columns(test_ind)
                test_ind["funding_percentile"] = np.nan
            asset_records[asset] = test_ind.to_dict("records")

        equity = 1.0; peak = 1.0; max_dd = 0.0
        positions = {a: None for a in asset_data}
        fold_trades = []
        max_concurrent_in_fold = 0
        concurrent_log = []

        for i in range(len(test_index_slice)):
            ts = test_index_slice[i]
            for asset in asset_data:
                row = asset_records[asset][i]
                position = positions[asset]
                if position is not None:
                    bars_held = i - position["entry_i"]
                    reason = signals.long_exit(row, position, strategy, bars_held)
                    if reason:
                        exit_fill = (position["stop"] * (1 - slippage) if reason == "stop"
                                     else row["close"] * (1 - slippage))
                        gross = (exit_fill - position["entry"]) / position["entry"]
                        overlay = float(position.get("overlay_size_multiplier", 1.0))
                        eff = base_size * size_per_asset * overlay
                        net = (gross - 2 * fee) * eff
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        fold_trades.append({
                            "asset": asset,
                            "_variant": name,
                            "entry_ts": position["entry_ts"], "exit_ts": ts,
                            "setup": position["setup"], "setup_name": position["setup"],
                            "side": "long", "direction": "long",
                            "entry_price": position["entry"], "exit_price": exit_fill,
                            "gross_return_pct": gross, "net_return_pct": net,
                            "ret": net, "reason": reason, "exit_reason": reason,
                            "bars": bars_held, "holding_bars": bars_held,
                            "size_multiplier": overlay, "size_per_asset": size_per_asset,
                            "position_size_effective": eff,
                            "funding_percentile": position.get("funding_percentile"),
                            "funding_state": position.get("funding_state"),
                        })
                        positions[asset] = None
                        continue
                if positions[asset] is None:
                    if not bool(row.get("markov_long_allowed", True)):
                        continue
                    overlay = float(row.get("markov_size_multiplier", 1.0) or 0.0)
                    if overlay <= 0:
                        continue
                    setup = signals.long_entry(row, strategy)
                    if not setup:
                        continue
                    init_stop = signals.initial_stop(row, setup, strategy)
                    positions[asset] = {
                        "entry": row["close"] * (1 + slippage),
                        "setup": setup, "direction": "long",
                        "entry_i": i, "entry_ts": ts,
                        "stop": init_stop, "initial_stop": init_stop,
                        "overlay_size_multiplier": overlay,
                        "funding_percentile": row.get("funding_percentile"),
                        "funding_state": row.get("markov_state"),
                    }
            cur = sum(1 for p in positions.values() if p is not None)
            concurrent_log.append(cur)
            max_concurrent_in_fold = max(max_concurrent_in_fold, cur)

        for asset, position in list(positions.items()):
            if position is None:
                continue
            last_row = asset_records[asset][-1]
            bars_held = len(test_index_slice) - 1 - position["entry_i"]
            exit_fill = last_row["close"] * (1 - slippage)
            gross = (exit_fill - position["entry"]) / position["entry"]
            overlay = float(position.get("overlay_size_multiplier", 1.0))
            eff = base_size * size_per_asset * overlay
            net = (gross - 2 * fee) * eff
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            fold_trades.append({
                "asset": asset, "_variant": name,
                "entry_ts": position["entry_ts"], "exit_ts": test_index_slice[-1],
                "setup": position["setup"], "setup_name": position["setup"],
                "side": "long", "direction": "long",
                "entry_price": position["entry"], "exit_price": exit_fill,
                "gross_return_pct": gross, "net_return_pct": net,
                "ret": net, "reason": "end", "exit_reason": "end",
                "bars": bars_held, "holding_bars": bars_held,
                "size_multiplier": overlay, "size_per_asset": size_per_asset,
                "position_size_effective": eff,
                "funding_percentile": position.get("funding_percentile"),
                "funding_state": position.get("funding_state"),
            })
            positions[asset] = None

        all_trades.extend(fold_trades)
        fold_ret = equity - 1.0
        fold_returns.append(fold_ret)
        folds.append({"fold": fold,
                      "test_metrics": {"trades": len(fold_trades),
                                       "total_return": fold_ret,
                                       "max_drawdown": max_dd,
                                       "max_concurrent": max_concurrent_in_fold}})
        cursor += test_bars

    return _summarize(name, all_trades, folds, fold_returns=fold_returns)


def _summarize(name, all_trades, folds, fold_returns=None):
    if fold_returns is None:
        fold_returns = [f["test_metrics"]["total_return"] for f in folds]
    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for r in fold_returns if r > 0)
    pf = oos["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"Sharpe={oos['sharpe_per_trade']:.3f}  win={oos['win_rate']*100:.1f}%  "
        f"folds+={fold_pos}/{len(folds)}")
    # By funding state at entry
    by_state = {}
    for t in all_trades:
        s = t.get("funding_state") or "no_filter"
        by_state[s] = by_state.get(s, 0) + 1
    if by_state:
        log(f"     by funding state: " + ", ".join(f"{k}:{v}" for k, v in by_state.items()))
    return {"name": name, "oos": oos, "folds": folds, "trades": all_trades,
            "fold_pos": fold_pos, "fold_returns": fold_returns,
            "by_funding_state": by_state}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--supertrend-strategy",
                    default=str(STATE_DIR / "strategy_supertrend.yaml"))
    ap.add_argument("--funding-window-bars", type=int, default=180,
                    help="rolling percentile window (default 180 = 30 days @ 4h)")
    ap.add_argument("--block-pct", type=float, default=95.0)
    ap.add_argument("--half-pct", type=float, default=90.0)
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"funding_rate_comparison_{ts}.csv"
    md_path = out_dir / f"funding_rate_comparison_{ts}.md"
    trades_path = out_dir / f"trades_funding_{ts}.csv"

    st_strategy = yaml.safe_load(open(args.supertrend_strategy))

    log(f"loading BTC and ETH price + funding ({args.n_months}mo) …")
    btc_df = data_mod.resample(data_mod.load_klines("BTCUSDT", n_months=args.n_months), args.timeframe)
    eth_df = data_mod.resample(data_mod.load_klines("ETHUSDT", n_months=args.n_months), args.timeframe)
    btc_funding = funding_mod.load_funding("BTCUSDT", n_months=args.n_months)
    eth_funding = funding_mod.load_funding("ETHUSDT", n_months=args.n_months)
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]; eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    btc_f_aligned = funding_mod.align_to_index(btc_funding, btc_df.index)
    eth_f_aligned = funding_mod.align_to_index(eth_funding, eth_df.index)
    btc_pct = funding_mod.rolling_percentile(btc_f_aligned, window=args.funding_window_bars)
    eth_pct = funding_mod.rolling_percentile(eth_f_aligned, window=args.funding_window_bars)

    btc_filter_dec = build_funding_decisions(btc_pct, mode="filter",
                                             block_above=args.block_pct, half_above=args.half_pct)
    eth_filter_dec = build_funding_decisions(eth_pct, mode="filter",
                                             block_above=args.block_pct, half_above=args.half_pct)
    btc_sizing_dec = build_funding_decisions(btc_pct, mode="sizing",
                                             block_above=args.block_pct, half_above=args.half_pct)
    eth_sizing_dec = build_funding_decisions(eth_pct, mode="sizing",
                                             block_above=args.block_pct, half_above=args.half_pct)

    btc_ind = signals.compute_indicators(btc_df, st_strategy)
    eth_ind = signals.compute_indicators(eth_df, st_strategy)
    asset_data = {
        "BTCUSDT": {"df": btc_df, "ind": btc_ind},
        "ETHUSDT": {"df": eth_df, "ind": eth_ind},
    }

    results = []
    # variant 1
    results.append(_run_solo("eth_supertrend_baseline",
                             eth_df, st_strategy, None,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage, "ETH"))
    # variant 2
    results.append(_run_solo("eth_supertrend_funding_filter",
                             eth_df, st_strategy, eth_filter_dec,
                             args.train_bars, args.test_bars, args.embargo_bars,
                             args.fee, args.slippage, "ETH"))
    # variant 3
    results.append(_run_parallel("btc_eth_parallel_baseline",
                                 asset_data, st_strategy, {"BTCUSDT": None, "ETHUSDT": None},
                                 args.train_bars, args.test_bars, args.embargo_bars,
                                 args.fee, args.slippage))
    # variant 4
    results.append(_run_parallel("btc_eth_parallel_funding_filter",
                                 asset_data, st_strategy,
                                 {"BTCUSDT": btc_filter_dec, "ETHUSDT": eth_filter_dec},
                                 args.train_bars, args.test_bars, args.embargo_bars,
                                 args.fee, args.slippage))
    # variant 5
    results.append(_run_parallel("btc_eth_parallel_funding_sizing",
                                 asset_data, st_strategy,
                                 {"BTCUSDT": btc_sizing_dec, "ETHUSDT": eth_sizing_dec},
                                 args.train_bars, args.test_bars, args.embargo_bars,
                                 args.fee, args.slippage))

    rows = []
    all_trades = []
    for r in results:
        if r is None:
            continue
        o = r["oos"]
        for t in r["trades"]:
            all_trades.append(t)
        by_asset = {}
        for t in r["trades"]:
            a = t.get("asset", "?")
            by_asset[a] = by_asset.get(a, 0) + 1
        fr = r["fold_returns"]
        fold_std = float(np.std(fr)) if fr else 0.0
        rows.append({
            "variant": r["name"],
            "trades": o["trades"],
            "total_return": o["total_return"],
            "max_drawdown": o["max_drawdown"],
            "profit_factor": (o["profit_factor"] if o["profit_factor"] != float("inf") else 9999.0),
            "sharpe_per_trade": o["sharpe_per_trade"],
            "win_rate": o["win_rate"],
            "n_folds": len(r["folds"]),
            "fold_positive": f"{r['fold_pos']}/{len(r['folds'])}",
            "fold_return_std": fold_std,
            "by_asset": "; ".join(f"{k}:{v}" for k, v in by_asset.items()),
            "by_funding_state": "; ".join(f"{k}:{v}" for k, v in r["by_funding_state"].items()),
        })

    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# Funding-rate filter experiment — {ts}",
        "",
        f"- supertrend strategy: `{args.supertrend_strategy}`",
        f"- universe: BTCUSDT, ETHUSDT",
        f"- BTC + ETH: 48mo ({common[0].date()} -> {common[-1].date()}, {len(common)} bars)",
        f"- funding source: Binance Vision (futures/um/monthly/fundingRate)",
        f"- rolling percentile window: {args.funding_window_bars} bars (~30 days @ 4h)",
        f"- thresholds: block at p{args.block_pct}, half-size at p{args.half_pct}",
        f"- decision TF: {args.timeframe}",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "Adoption: must improve PF or DD without destroying trade count.",
        "",
        "| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | by asset | by funding state |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == 9999.0 else f"{pf:.2f}"
        md.append(f"| `{r['variant']}` | {r['n_folds']} | {r['trades']} | "
                  f"{r['total_return']*100:+.2f}% | {r['max_drawdown']*100:.2f}% | "
                  f"{pf_s} | {r['sharpe_per_trade']:.3f} | "
                  f"{r['win_rate']*100:.1f}% | {r['fold_positive']} | "
                  f"{r['by_asset']} | {r['by_funding_state']} |")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    base_cols = [
        "_variant", "asset", "entry_ts", "exit_ts", "setup_name",
        "entry_price", "exit_price", "gross_return_pct", "net_return_pct",
        "size_multiplier", "position_size_effective",
        "funding_percentile", "funding_state",
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
    log(f"wrote trades CSV ({len(all_trades)} rows) → {trades_path}")


if __name__ == "__main__":
    main()
