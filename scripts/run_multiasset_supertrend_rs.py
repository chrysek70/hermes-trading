#!/usr/bin/env python3
"""Multi-asset SuperTrend + BTC/ETH relative-strength walk-forward.

Reproducible runner for Issue #12. Same SuperTrend(10, 3) and same RS
config as Issue #5; only the universe expands (BTC and ETH instead of
BTC alone) and the engine is wrapped in a one-position-at-a-time
portfolio coordinator.

Five variants are walk-forwarded on identical folds and costs:

  1. btc_supertrend_only           — BTC alone, no RS overlay
  2. btc_supertrend_rs_sizing      — BTC alone + RS sizing overlay
  3. eth_supertrend_only           — ETH alone, no RS overlay
  4. eth_supertrend_rs_sizing      — ETH alone + RS sizing overlay
  5. multiasset_supertrend_rs_one_position
                                   — BTC + ETH portfolio with RS sizing
                                     per asset, single shared position

Adoption criteria (from Issue #12 spec):
  - trade count >= 30
  - PF > 2.24 (beat BTC supertrend_only)
  - max DD <= 9.63% (no worse than BTC supertrend_only)
  - fold consistency not worse than BTC supertrend_only

Hard rules — do NOT change these in this script:
  - SuperTrend (10, 3) unchanged.
  - RS windows unchanged from Issue #5 (lookback=30, ratio_ema=30).
  - Same fees / slippage / fold geometry as every other experiment.

Outputs (under --out-dir, default `results/`):
  - multiasset_supertrend_rs_comparison_<ts>.csv
  - multiasset_supertrend_rs_comparison_<ts>.md
  - trades_multiasset_supertrend_rs_<ts>.csv  (all 5 variants)
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
from hermes_trading import relative_strength as rs
from hermes_trading import signals
from hermes_trading import walk_forward as wf


# ---------- single-asset variant driver (variants 1-4) -----------------------

def _run_single_asset_variant(name, df, strategy, decisions_full,
                              train_bars, test_bars, embargo_bars,
                              fee, slippage):
    """Mirrors wf.walk_forward but accepts a precomputed decisions_full
    DataFrame for slicing per fold (no per-fold refit)."""
    log(f"========== {name} ==========")
    ind_full = signals.compute_indicators(df, strategy)
    n = len(ind_full)
    folds = []
    all_trades: list[dict] = []
    fold_returns: list[float] = []
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
        for t in res["trades"]:
            t["_variant"] = name
            t["asset"] = "BTC" if "btc" in name else "ETH"
        all_trades.extend(res["trades"])
        folds.append({"fold": fold,
                      "test_range": (test_ind.index[0].date(),
                                     test_ind.index[-1].date()),
                      "test_metrics": res["metrics"]})
        fold_returns.append(res["metrics"]["total_return"])
        cursor += test_bars

    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for r in fold_returns if r > 0)
    pf = oos["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"Sharpe={oos['sharpe_per_trade']:.3f}  win={oos['win_rate']*100:.1f}%  "
        f"folds+={fold_pos}/{len(folds)}")
    return {"name": name, "oos": oos, "folds": folds,
            "trades": all_trades, "fold_pos": fold_pos,
            "fold_returns": fold_returns}


# ---------- multi-asset state machine (variant 5) ----------------------------

def _close_trade_record(position, exit_fill, reason, exit_ts, base_size,
                        fee, slippage, asset, exit_close):
    """Mirror of backtest._run_state_machine's close_trade logic, but
    returns the trade dict instead of mutating closures."""
    if position["direction"] == "long":
        gross = (exit_fill - position["entry"]) / position["entry"]
    else:
        gross = (position["entry"] - exit_fill) / position["entry"]
    effective_size = base_size * float(position.get("size_multiplier", 1.0))
    fees_cost = 2.0 * fee * effective_size
    slippage_cost = 2.0 * slippage * effective_size
    net = (gross - 2 * fee) * effective_size
    trade = {
        "asset": asset,
        "ret": net,
        "reason": reason,
        "setup": position["setup"],
        "direction": position["direction"],
        "bars": position.get("bars_held_at_exit", 0),
        "size_multiplier": float(position.get("size_multiplier", 1.0)),
        "markov_state": position.get("markov_state"),
        "markov_stable_state": position.get("markov_stable_state"),
        "entry_ts": position.get("entry_ts"),
        "exit_ts": exit_ts,
        "setup_name": position["setup"],
        "side": position["direction"],
        "entry_price": position["entry"],
        "exit_price": exit_fill,
        "gross_return_pct": gross,
        "fees_total": fees_cost,
        "slippage_total": slippage_cost,
        "net_return_pct": net,
        "holding_bars": position.get("bars_held_at_exit", 0),
        "exit_reason": reason,
        "position_size_effective": effective_size,
    }
    for k in ("entry_rsi", "entry_ema_fast", "entry_ema_slow",
              "entry_ema_pull", "entry_atr", "entry_vwap",
              "entry_ema_slope", "entry_atr_pct",
              "entry_vwap_distance_pct", "entry_volume_zscore"):
        trade[k] = position.get(k)
    return trade


def _attach_rs_to_row(row: dict, decision_row: pd.Series) -> dict:
    """Inject RS gates into a single row dict the way
    _attach_decisions_df does on a DataFrame."""
    out = dict(row)
    out["markov_state"] = decision_row.get("raw_state")
    out["markov_stable_state"] = decision_row.get("stable_state")
    out["markov_long_allowed"] = bool(decision_row.get("long_allowed", True))
    out["markov_size_multiplier"] = float(decision_row.get("size_multiplier", 1.0))
    out["markov_regime_score"] = float(decision_row.get("regime_score", 1.0))
    out["markov_allowed_setups"] = decision_row.get("allowed_setups", None)
    return out


def _run_multi_asset(name, btc_df, eth_df, strategy, btc_decisions, eth_decisions,
                     features, train_bars, test_bars, embargo_bars,
                     fee, slippage):
    """One-position-at-a-time coordinator for two assets.

    Per bar:
      - if holding a position on asset X, evaluate X's exit; close if triggered.
      - if flat after exit handling, evaluate entry signals on BOTH assets:
          * single signal → enter that asset
          * both signals → selection rules:
              1. higher RS score wins
              2. tied → larger SuperTrend distance / ATR wins
              3. still tied → skip
    """
    log(f"========== {name} ==========")
    btc_ind_full = signals.compute_indicators(btc_df, strategy)
    eth_ind_full = signals.compute_indicators(eth_df, strategy)
    common = btc_ind_full.index.intersection(eth_ind_full.index)
    btc_ind_full = btc_ind_full.loc[common]
    eth_ind_full = eth_ind_full.loc[common]

    # Carry ts through to records
    btc_ind_full = btc_ind_full.copy(); btc_ind_full["ts"] = btc_ind_full.index
    eth_ind_full = eth_ind_full.copy(); eth_ind_full["ts"] = eth_ind_full.index

    n = len(btc_ind_full)
    base_size = float(strategy["risk"].get("position_size_r", 0.5))
    folds = []
    all_trades: list[dict] = []
    fold_returns: list[float] = []
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

        # Pre-attach RS to each per-asset row for the test slice.
        btc_test = btc_ind_full.iloc[test_lo:test_hi]
        eth_test = eth_ind_full.iloc[test_lo:test_hi]
        btc_dec_slice = btc_decisions.loc[btc_test.index]
        eth_dec_slice = eth_decisions.loc[eth_test.index]
        feat_slice = features.loc[btc_test.index]

        btc_records = [_attach_rs_to_row(r, btc_dec_slice.iloc[i])
                       for i, r in enumerate(btc_test.to_dict("records"))]
        eth_records = [_attach_rs_to_row(r, eth_dec_slice.iloc[i])
                       for i, r in enumerate(eth_test.to_dict("records"))]
        btc_minus_eth = feat_slice["btc_minus_eth_return_n"].values

        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        position = None  # dict with "asset" + standard fields
        fold_trades = []
        cur_concurrent = 0

        for i in range(len(btc_records)):
            btc_row = btc_records[i]
            eth_row = eth_records[i]
            ts = btc_row["ts"]

            # ---- exit branch (one position, one asset) ----
            if position is not None:
                asset_row = btc_row if position["asset"] == "BTC" else eth_row
                bars_held = i - position["entry_i"]
                if position["direction"] == "long":
                    reason = signals.long_exit(asset_row, position, strategy, bars_held)
                    if reason:
                        exit_fill = (position["stop"] * (1 - slippage)
                                     if reason == "stop"
                                     else asset_row["close"] * (1 - slippage))
                        position["bars_held_at_exit"] = bars_held
                        trade = _close_trade_record(
                            position, exit_fill, reason, ts, base_size,
                            fee, slippage, position["asset"],
                            asset_row["close"],
                        )
                        net = trade["ret"]
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        fold_trades.append(trade)
                        position = None
                        cur_concurrent = 0
                        # do not re-enter on the same bar
                        continue

            # ---- entry branch (only when flat) ----
            if position is None:
                btc_long_ok = bool(btc_row.get("markov_long_allowed", True))
                eth_long_ok = bool(eth_row.get("markov_long_allowed", True))
                btc_size = float(btc_row.get("markov_size_multiplier", 1.0) or 0.0)
                eth_size = float(eth_row.get("markov_size_multiplier", 1.0) or 0.0)

                btc_setup = signals.long_entry(btc_row, strategy) if (btc_long_ok and btc_size > 0) else None
                eth_setup = signals.long_entry(eth_row, strategy) if (eth_long_ok and eth_size > 0) else None

                chosen = None  # "BTC" / "ETH" / None
                if btc_setup and not eth_setup:
                    chosen = "BTC"
                elif eth_setup and not btc_setup:
                    chosen = "ETH"
                elif btc_setup and eth_setup:
                    # 1) RS score: BTC stronger means btc_minus_eth > 0.
                    score = btc_minus_eth[i]
                    if score is None or (isinstance(score, float) and np.isnan(score)):
                        score = 0.0
                    if score > 0:
                        chosen = "BTC"
                    elif score < 0:
                        chosen = "ETH"
                    else:
                        # 2) Tiebreak on SuperTrend distance / ATR (the
                        #    indicator already lives on each per-asset row).
                        def st_dist(r):
                            line = r.get("supertrend_line")
                            atr = r.get("atr")
                            if line is None or atr is None or atr == 0:
                                return None
                            return (r["close"] - float(line)) / float(atr)
                        bd = st_dist(btc_row)
                        ed = st_dist(eth_row)
                        if bd is None and ed is None:
                            chosen = None
                        elif bd is None:
                            chosen = "ETH"
                        elif ed is None:
                            chosen = "BTC"
                        elif bd > ed:
                            chosen = "BTC"
                        elif ed > bd:
                            chosen = "ETH"
                        else:
                            chosen = None   # 3) still tied → skip

                if chosen == "BTC":
                    setup_name = btc_setup
                    asset_row = btc_row
                    size_mult = btc_size
                elif chosen == "ETH":
                    setup_name = eth_setup
                    asset_row = eth_row
                    size_mult = eth_size
                else:
                    continue

                init_stop = signals.initial_stop(asset_row, setup_name, strategy)
                position = {
                    "asset": chosen,
                    "entry": asset_row["close"] * (1 + slippage),
                    "setup": setup_name,
                    "direction": "long",
                    "entry_i": i,
                    "entry_ts": ts,
                    "stop": init_stop,
                    "initial_stop": init_stop,
                    "size_multiplier": size_mult,
                    "markov_state": asset_row.get("markov_state"),
                    "markov_stable_state": asset_row.get("markov_stable_state"),
                    "entry_rsi": asset_row.get("rsi"),
                    "entry_ema_fast": asset_row.get("ema_fast"),
                    "entry_ema_slow": asset_row.get("ema_slow"),
                    "entry_ema_pull": asset_row.get("ema_pull"),
                    "entry_atr": asset_row.get("atr"),
                    "entry_vwap": asset_row.get("vwap"),
                    "entry_ema_slope": asset_row.get("ema_slope"),
                    "entry_atr_pct": asset_row.get("atr_pct"),
                    "entry_vwap_distance_pct": asset_row.get("vwap_distance_pct"),
                    "entry_volume_zscore": asset_row.get("volume_zscore"),
                }
                cur_concurrent = 1
                max_concurrent = max(max_concurrent, cur_concurrent)

        # close any open position at fold end
        if position is not None:
            asset_row = btc_records[-1] if position["asset"] == "BTC" else eth_records[-1]
            position["bars_held_at_exit"] = len(btc_records) - 1 - position["entry_i"]
            exit_fill = asset_row["close"] * (1 - slippage)
            trade = _close_trade_record(
                position, exit_fill, "end", asset_row["ts"], base_size,
                fee, slippage, position["asset"], asset_row["close"],
            )
            net = trade["ret"]
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            fold_trades.append(trade)
            position = None
            cur_concurrent = 0

        for t in fold_trades:
            t["_variant"] = name
        all_trades.extend(fold_trades)
        fold_ret = equity - 1.0
        fold_returns.append(fold_ret)
        folds.append({"fold": fold,
                      "test_range": (btc_test.index[0].date(),
                                     btc_test.index[-1].date()),
                      "test_metrics": {
                          "trades": len(fold_trades),
                          "total_return": fold_ret,
                          "max_drawdown": max_dd,
                      }})
        cursor += test_bars

    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for r in fold_returns if r > 0)
    pf = oos["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"Sharpe={oos['sharpe_per_trade']:.3f}  win={oos['win_rate']*100:.1f}%  "
        f"folds+={fold_pos}/{len(folds)}  max_concurrent={max_concurrent}")
    return {"name": name, "oos": oos, "folds": folds,
            "trades": all_trades, "fold_pos": fold_pos,
            "fold_returns": fold_returns,
            "max_concurrent": max_concurrent}


# ---------- main -------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--btc-symbol", default="BTCUSDT")
    ap.add_argument("--eth-symbol", default="ETHUSDT")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--supertrend-strategy",
                    default=str(STATE_DIR / "strategy_supertrend.yaml"))
    ap.add_argument("--multiasset-strategy",
                    default=str(STATE_DIR / "strategy_supertrend_multiasset_rs.yaml"))
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
    csv_path = out_dir / f"multiasset_supertrend_rs_comparison_{ts}.csv"
    md_path = out_dir / f"multiasset_supertrend_rs_comparison_{ts}.md"
    trades_path = out_dir / f"trades_multiasset_supertrend_rs_{ts}.csv"

    st_strategy = yaml.safe_load(open(args.supertrend_strategy))
    ma_strategy = yaml.safe_load(open(args.multiasset_strategy))
    rs_cfg = ma_strategy["relative_strength"]

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
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]
    eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  "
        f"span: {common[0].date()} -> {common[-1].date()}")

    features = rs.compute_multi_asset_features(
        btc_df, eth_df,
        lookback_bars=rs_cfg["lookback_bars"],
        ratio_ema=rs_cfg["ratio_ema"],
    )
    btc_decisions = rs.build_asset_decisions(
        features, asset="btc", mode="sizing",
        min_return_advantage=rs_cfg["min_return_advantage"],
    )
    eth_decisions = rs.build_asset_decisions(
        features, asset="eth", mode="sizing",
        min_return_advantage=rs_cfg["min_return_advantage"],
    )
    btc_post_warm = btc_decisions[btc_decisions["raw_state"] != "rs_warmup"]
    eth_post_warm = eth_decisions[eth_decisions["raw_state"] != "rs_warmup"]
    btc_dist = btc_post_warm["raw_state"].value_counts().to_dict()
    eth_dist = eth_post_warm["raw_state"].value_counts().to_dict()
    log(f"BTC RS dist (post-warmup, {len(btc_post_warm)} bars): "
        + ", ".join(f"{k}={v}" for k, v in btc_dist.items()))
    log(f"ETH RS dist (post-warmup, {len(eth_post_warm)} bars): "
        + ", ".join(f"{k}={v}" for k, v in eth_dist.items()))

    results = []
    results.append(_run_single_asset_variant(
        "btc_supertrend_only", btc_df, st_strategy, None,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))
    results.append(_run_single_asset_variant(
        "btc_supertrend_rs_sizing", btc_df, st_strategy, btc_decisions,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))
    results.append(_run_single_asset_variant(
        "eth_supertrend_only", eth_df, st_strategy, None,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))
    results.append(_run_single_asset_variant(
        "eth_supertrend_rs_sizing", eth_df, st_strategy, eth_decisions,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))
    results.append(_run_multi_asset(
        "multiasset_supertrend_rs_one_position",
        btc_df, eth_df, st_strategy, btc_decisions, eth_decisions, features,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))

    rows = []
    all_trades = []
    for r in results:
        o = r["oos"]
        by_asset: dict[str, int] = {}
        ret_by_asset: dict[str, float] = {}
        for t in r["trades"]:
            a = t.get("asset", "BTC")
            by_asset[a] = by_asset.get(a, 0) + 1
            ret_by_asset[a] = ret_by_asset.get(a, 0.0) + t.get("ret", 0.0)
        by_state: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        for t in r["trades"]:
            s = (t.get("markov_stable_state") or t.get("markov_state")
                 or "unknown")
            by_state[s] = by_state.get(s, 0) + 1
            rsn = t.get("reason", "?")
            by_reason[rsn] = by_reason.get(rsn, 0) + 1
            all_trades.append(t)

        fr = r["fold_returns"]
        fold_std = float(np.std(fr)) if fr else 0.0
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
            "fold_return_std": fold_std,
            "max_concurrent": r.get("max_concurrent", 1),
            "by_asset": "; ".join(f"{k}:{v}" for k, v in by_asset.items()),
            "ret_by_asset": "; ".join(
                f"{k}:{ret_by_asset[k]:+.4f}" for k in by_asset),
            "by_state": "; ".join(f"{k}:{v}" for k, v in by_state.items()),
            "by_reason": "; ".join(f"{k}:{v}" for k, v in by_reason.items()),
        })

    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# Multi-asset SuperTrend + BTC/ETH RS — {ts}",
        "",
        f"- supertrend strategy: `{args.supertrend_strategy}`",
        f"- multi-asset strategy: `{args.multiasset_strategy}`",
        f"- BTC history: {args.n_months} months "
        f"({btc_df.index[0].date()} -> {btc_df.index[-1].date()})",
        f"- ETH history: {args.n_months} months (aligned, {len(common)} bars)",
        f"- decision TF: {args.timeframe}",
        f"- walk-forward: train_bars={args.train_bars} / "
        f"test_bars={args.test_bars} / embargo_bars={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        f"- RS config: lookback={rs_cfg['lookback_bars']}, "
        f"ratio_ema={rs_cfg['ratio_ema']}, "
        f"min_return_advantage={rs_cfg['min_return_advantage']}, "
        f"require_ratio_above_ema={rs_cfg['require_ratio_above_ema']}",
        "",
        "| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | fold σ | concurrent | by asset |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == 9999.0 else f"{pf:.2f}"
        md.append(
            f"| `{r['variant']}` | {r['n_folds']} | {r['trades']} | "
            f"{r['total_return']*100:+.2f}% | {r['max_drawdown']*100:.2f}% | "
            f"{pf_s} | {r['sharpe_per_trade']:.3f} | "
            f"{r['win_rate']*100:.1f}% | {r['fold_positive']} | "
            f"{r['fold_return_std']*100:.2f}% | {r['max_concurrent']} | "
            f"{r['by_asset']} |"
        )
    md += ["", "### Return contribution by asset", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['ret_by_asset']}")
    md += ["", "### By RS / regime state", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_state']}")
    md += ["", "### By exit reason", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_reason']}")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    if all_trades:
        # Detailed trade CSV — same columns as bt.write_trades_detailed_csv
        # plus the new "asset" and "_variant" columns.
        extra_cols = ["asset", "_variant"]
        # Compose a one-off CSV row-by-row so unknown extra fields are tolerated.
        base_cols = [
            "asset", "_variant", "entry_ts", "exit_ts", "side", "setup_name",
            "entry_price", "exit_price", "gross_return_pct",
            "fees_total", "slippage_total", "net_return_pct",
            "holding_bars", "exit_reason",
            "entry_rsi", "entry_ema_fast", "entry_ema_slow", "entry_ema_pull",
            "entry_atr", "entry_vwap", "entry_ema_slope", "entry_atr_pct",
            "entry_vwap_distance_pct", "entry_volume_zscore",
            "markov_state", "markov_stable_state",
            "position_size_effective", "size_multiplier",
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
        log(f"wrote detailed trades CSV ({len(all_trades)} rows) → "
            f"{trades_path}")


if __name__ == "__main__":
    main()
