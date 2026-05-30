#!/usr/bin/env python3
"""Diagnostic comparison: BTC vs ETH SuperTrend(10, 3) on 48mo 4h.

Reproducible runner for Issue #13. Research-only: NO strategy changes,
NO parameter tuning. Adds diagnostic measurements (ADX, run-length,
autocorrelation) for analysis but does not wire any of them into trading
logic.

Steps:
  1. Load BTC + ETH 48mo 4h, aligned.
  2. Compute SuperTrend trades for each asset via walk-forward (same
     fold geometry as Issues #11 / #12).
  3. Compute per-trade diagnostics.
  4. Compute per-asset full-window structural measurements:
       - SuperTrend run-lengths
       - false-breakout frequency
       - ADX distribution
       - ATR% distribution
       - return / |return| autocorrelation
       - drawdown structure (5% and 10% thresholds)
  5. Run a simple per-bar selector with two scoring functions
     (SuperTrend distance / ATR; RS score) and walk-forward those.
  6. Write CSV + Markdown report.

Outputs (under --out-dir, default `results/`):
  - eth_vs_btc_comparison_<ts>.csv
  - eth_vs_btc_comparison_<ts>.md  (raw numbers; narrative goes in
    `research/eth_vs_btc_supertrend_analysis.md` which is hand-written
    against these numbers)
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
from hermes_trading import relative_strength as rs
from hermes_trading import signals
from hermes_trading import walk_forward as wf


# ---------- diagnostic indicators (measurement only) -------------------------

def wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX. Causal. For diagnostic use only — not fed to signals."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr)
    dx_denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / dx_denom
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def run_lengths(direction: pd.Series) -> pd.Series:
    """Lengths of consecutive same-value runs in a direction series."""
    d = direction.dropna()
    if len(d) == 0:
        return pd.Series([], dtype=int)
    groups = (d != d.shift()).cumsum()
    return d.groupby(groups).size()


def drawdown_structure(returns: pd.Series, threshold: float = 0.05) -> dict:
    """Count and characterize drawdowns >= threshold on an equity curve
    built from log-returns."""
    eq = (1 + returns.fillna(0)).cumprod()
    peak = eq.cummax()
    dd = (eq - peak) / peak  # non-positive
    in_dd = dd <= -threshold
    # find runs of in_dd
    groups = (in_dd != in_dd.shift()).cumsum()
    runs = []
    for _, grp in dd.groupby(groups):
        if (grp <= -threshold).any():
            runs.append({
                "depth": float(grp.min()),
                "duration_bars": int(len(grp)),
            })
    return {
        "n": len(runs),
        "max_depth": float(min(r["depth"] for r in runs)) if runs else 0.0,
        "median_depth": float(np.median([r["depth"] for r in runs])) if runs else 0.0,
        "median_duration_bars": float(np.median([r["duration_bars"] for r in runs])) if runs else 0.0,
    }


# ---------- single-asset walk-forward (returns trades + per-fold returns) ----

def _walk_forward_solo(asset_label, df, strategy,
                       train_bars, test_bars, embargo_bars, fee, slippage):
    log(f"========== {asset_label} supertrend_only ==========")
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
        test_ind = bt._attach_neutral_markov_columns(ind_full.iloc[test_lo:test_hi].copy())
        res = bt._run_state_machine(test_ind.to_dict("records"), strategy,
                                    warmup=0, fee=fee, slippage=slippage)
        for t in res["trades"]:
            t["asset"] = asset_label
        all_trades.extend(res["trades"])
        folds.append({"fold": fold, "test_range": (test_ind.index[0].date(),
                                                   test_ind.index[-1].date()),
                      "test_metrics": res["metrics"]})
        cursor += test_bars
    return ind_full, all_trades, folds


# ---------- rotation selectors ----------------------------------------------

def _walk_forward_rotation(name, btc_df, eth_df, strategy, features,
                           score_fn,
                           train_bars, test_bars, embargo_bars, fee, slippage):
    """One-position-at-a-time, picks asset purely by score_fn(btc_row, eth_row, feat_row).
    No RS gating — uses only the score to choose between two simultaneously-firing
    SuperTrend signals."""
    log(f"========== {name} ==========")
    btc_ind = signals.compute_indicators(btc_df, strategy)
    eth_ind = signals.compute_indicators(eth_df, strategy)
    common = btc_ind.index.intersection(eth_ind.index)
    btc_ind = btc_ind.loc[common].copy(); btc_ind["ts"] = btc_ind.index
    eth_ind = eth_ind.loc[common].copy(); eth_ind["ts"] = eth_ind.index
    feat = features.loc[common]

    base_size = float(strategy["risk"].get("position_size_r", 0.5))
    n = len(btc_ind)
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
        btc_test = btc_ind.iloc[test_lo:test_hi]
        eth_test = eth_ind.iloc[test_lo:test_hi]
        feat_test = feat.iloc[test_lo:test_hi]
        btc_records = btc_test.to_dict("records")
        eth_records = eth_test.to_dict("records")

        equity = 1.0; peak = 1.0; max_dd = 0.0
        position = None
        fold_trades = []
        for i in range(len(btc_records)):
            btc_row = btc_records[i]
            eth_row = eth_records[i]
            ts = btc_row["ts"]
            # exit
            if position is not None:
                asset_row = btc_row if position["asset"] == "BTC" else eth_row
                bars_held = i - position["entry_i"]
                if position["direction"] == "long":
                    reason = signals.long_exit(asset_row, position, strategy, bars_held)
                else:
                    reason = signals.short_exit(asset_row, position, strategy, bars_held)
                if reason:
                    exit_fill = (position["stop"] * (1 - slippage) if reason == "stop"
                                 else asset_row["close"] * (1 - slippage))
                    gross = (exit_fill - position["entry"]) / position["entry"]
                    net = (gross - 2 * fee) * base_size * position["size_multiplier"]
                    equity *= 1.0 + net
                    peak = max(peak, equity)
                    max_dd = max(max_dd, (peak - equity) / peak)
                    fold_trades.append({
                        "asset": position["asset"],
                        "ret": net,
                        "reason": reason,
                        "setup": position["setup"],
                        "bars": bars_held,
                        "net_return_pct": net,
                        "holding_bars": bars_held,
                        "size_multiplier": position["size_multiplier"],
                        "entry_ts": position["entry_ts"],
                        "exit_ts": ts,
                    })
                    position = None
                    continue
            # entry
            if position is None:
                btc_setup = signals.long_entry(btc_row, strategy)
                eth_setup = signals.long_entry(eth_row, strategy)
                chosen = None
                if btc_setup and not eth_setup:
                    chosen = "BTC"
                elif eth_setup and not btc_setup:
                    chosen = "ETH"
                elif btc_setup and eth_setup:
                    bs, es = score_fn(btc_row, eth_row, feat_test.iloc[i])
                    if bs is None or es is None:
                        chosen = None
                    elif bs > es:
                        chosen = "BTC"
                    elif es > bs:
                        chosen = "ETH"
                    else:
                        chosen = None
                if chosen == "BTC":
                    setup_name = btc_setup; asset_row = btc_row
                elif chosen == "ETH":
                    setup_name = eth_setup; asset_row = eth_row
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
                    "size_multiplier": 1.0,
                }
        # close at fold end
        if position is not None:
            last_row = btc_records[-1] if position["asset"] == "BTC" else eth_records[-1]
            exit_fill = last_row["close"] * (1 - slippage)
            gross = (exit_fill - position["entry"]) / position["entry"]
            net = (gross - 2 * fee) * base_size * position["size_multiplier"]
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            fold_trades.append({"asset": position["asset"], "ret": net,
                                "reason": "end", "setup": position["setup"],
                                "bars": len(btc_records) - 1 - position["entry_i"],
                                "net_return_pct": net,
                                "holding_bars": len(btc_records) - 1 - position["entry_i"],
                                "size_multiplier": 1.0,
                                "entry_ts": position["entry_ts"], "exit_ts": last_row["ts"]})
            position = None
        all_trades.extend(fold_trades)
        folds.append({"fold": fold, "test_range": (btc_test.index[0].date(),
                                                   btc_test.index[-1].date()),
                      "test_metrics": {"trades": len(fold_trades),
                                       "total_return": equity - 1.0,
                                       "max_drawdown": max_dd}})
        cursor += test_bars
    return all_trades, folds


# ---------- per-trade and per-fold summaries --------------------------------

def trade_diagnostics(trades: list[dict]) -> dict:
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    bars = [t["bars"] for t in trades]
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t.get("reason", "?")] = reasons.get(t.get("reason", "?"), 0) + 1
    return {
        "trades": len(trades),
        "avg_winner": float(np.mean(wins)) if wins else 0.0,
        "avg_loser": float(np.mean(losses)) if losses else 0.0,
        "win_rate": (len(wins) / len(trades)) if trades else 0.0,
        "profit_factor": (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf"),
        "expectancy": float(np.mean(rets)) if rets else 0.0,
        "avg_holding_bars": float(np.mean(bars)) if bars else 0.0,
        "median_holding_bars": float(np.median(bars)) if bars else 0.0,
        "n_stop": reasons.get("stop", 0),
        "n_end": reasons.get("end", 0),
        "n_trail_exit": reasons.get("trail_exit", 0),
        "n_target_rsi": reasons.get("target_rsi", 0),
        "n_max_hold": reasons.get("max_hold", 0),
        "stop_pct": reasons.get("stop", 0) / max(1, len(trades)),
        "end_pct": reasons.get("end", 0) / max(1, len(trades)),
        "trail_pct": reasons.get("trail_exit", 0) / max(1, len(trades)),
        "max_hold_pct": reasons.get("max_hold", 0) / max(1, len(trades)),
    }


def fold_consistency(folds: list[dict], other_folds: list[dict]) -> dict:
    """Count fold-by-fold which side won."""
    a_rets = [f["test_metrics"]["total_return"] for f in folds]
    b_rets = [f["test_metrics"]["total_return"] for f in other_folds]
    n = min(len(a_rets), len(b_rets))
    a_wins = sum(1 for i in range(n) if a_rets[i] > b_rets[i])
    b_wins = sum(1 for i in range(n) if b_rets[i] > a_rets[i])
    ties = n - a_wins - b_wins
    return {
        "folds_compared": n,
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "a_mean_ret": float(np.mean(a_rets)) if a_rets else 0.0,
        "b_mean_ret": float(np.mean(b_rets)) if b_rets else 0.0,
        "a_std_ret": float(np.std(a_rets)) if a_rets else 0.0,
        "b_std_ret": float(np.std(b_rets)) if b_rets else 0.0,
    }


def trend_quality(ind: pd.DataFrame, df: pd.DataFrame,
                  false_breakout_bars: int = 6,
                  trending_min_run: int = 12) -> dict:
    direction = ind["supertrend_direction"]
    runs = run_lengths(direction)
    flips = (direction != direction.shift()).sum() - 1  # subtract initial NaN→value
    short_runs = (runs <= false_breakout_bars).sum()
    long_runs = (runs >= trending_min_run).sum()
    bars_in_long_runs = runs[runs >= trending_min_run].sum()
    total = len(direction.dropna())
    adx = wilder_adx(df, period=14)
    atr_pct = ind["atr"] / ind["close"]
    return {
        "n_flips": int(flips),
        "run_count": int(len(runs)),
        "mean_run_bars": float(runs.mean()) if len(runs) else 0.0,
        "median_run_bars": float(runs.median()) if len(runs) else 0.0,
        "short_runs_share": float(short_runs / len(runs)) if len(runs) else 0.0,
        "long_runs_share": float(long_runs / len(runs)) if len(runs) else 0.0,
        "trending_time_share": float(bars_in_long_runs / total) if total else 0.0,
        "adx_mean": float(adx.mean()),
        "adx_median": float(adx.median()),
        "adx_pct_above_25": float((adx > 25).mean()),
        "atr_pct_mean": float(atr_pct.mean()),
        "atr_pct_median": float(atr_pct.median()),
        "atr_pct_std": float(atr_pct.std()),
    }


def market_structure(df: pd.DataFrame) -> dict:
    rets = df["close"].pct_change()
    abs_rets = rets.abs()
    out = {
        "ret_autocorr_lag1": float(rets.autocorr(lag=1)),
        "ret_autocorr_lag5": float(rets.autocorr(lag=5)),
        "ret_autocorr_lag24": float(rets.autocorr(lag=24)),
        "absret_autocorr_lag1": float(abs_rets.autocorr(lag=1)),
        "absret_autocorr_lag5": float(abs_rets.autocorr(lag=5)),
        "absret_autocorr_lag24": float(abs_rets.autocorr(lag=24)),
        "ret_skew": float(rets.skew()),
        "ret_kurtosis": float(rets.kurtosis()),
    }
    for thr in (0.05, 0.10):
        dd = drawdown_structure(rets, threshold=thr)
        out[f"dd_count_{int(thr*100)}pct"] = dd["n"]
        out[f"dd_maxdepth_{int(thr*100)}pct"] = dd["max_depth"]
        out[f"dd_median_duration_bars_{int(thr*100)}pct"] = dd["median_duration_bars"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--btc-symbol", default="BTCUSDT")
    ap.add_argument("--eth-symbol", default="ETHUSDT")
    ap.add_argument("--timeframe", default="4h")
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
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"eth_vs_btc_comparison_{ts}.csv"
    md_path = out_dir / f"eth_vs_btc_comparison_{ts}.md"
    json_path = out_dir / f".eth_vs_btc_data_{ts}.json"

    st_strategy = yaml.safe_load(open(args.supertrend_strategy))
    rs_cfg = yaml.safe_load(open(args.rs_strategy))["relative_strength"]

    log(f"loading BTC + ETH {args.n_months}mo …")
    btc_df = data_mod.resample(data_mod.load_klines(args.btc_symbol, n_months=args.n_months), args.timeframe)
    eth_df = data_mod.resample(data_mod.load_klines(args.eth_symbol, n_months=args.n_months), args.timeframe)
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]; eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    features = rs.compute_multi_asset_features(
        btc_df, eth_df, lookback_bars=rs_cfg["lookback_bars"], ratio_ema=rs_cfg["ratio_ema"],
    )

    # ---- Q1: walk-forward solo for both assets ----
    btc_ind, btc_trades, btc_folds = _walk_forward_solo(
        "BTC", btc_df, st_strategy,
        args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
    )
    eth_ind, eth_trades, eth_folds = _walk_forward_solo(
        "ETH", eth_df, st_strategy,
        args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
    )

    btc_diag = trade_diagnostics(btc_trades)
    eth_diag = trade_diagnostics(eth_trades)
    foldwise = fold_consistency(btc_folds, eth_folds)
    log(f"BTC: trades={btc_diag['trades']} PF={btc_diag['profit_factor']:.2f} "
        f"win={btc_diag['win_rate']*100:.1f}%  avg_hold={btc_diag['avg_holding_bars']:.1f} bars")
    log(f"ETH: trades={eth_diag['trades']} PF={eth_diag['profit_factor']:.2f} "
        f"win={eth_diag['win_rate']*100:.1f}%  avg_hold={eth_diag['avg_holding_bars']:.1f} bars")
    log(f"per-fold consistency (n={foldwise['folds_compared']}): "
        f"BTC better in {foldwise['a_wins']}, ETH better in {foldwise['b_wins']}, "
        f"ties {foldwise['ties']}  (ETH mean fold ret {foldwise['b_mean_ret']*100:+.2f}% "
        f"σ {foldwise['b_std_ret']*100:.2f}%, BTC {foldwise['a_mean_ret']*100:+.2f}% "
        f"σ {foldwise['a_std_ret']*100:.2f}%)")

    # ---- Q2: trend quality ----
    btc_tq = trend_quality(btc_ind, btc_df)
    eth_tq = trend_quality(eth_ind, eth_df)
    log(f"BTC trend: flips={btc_tq['n_flips']} mean_run={btc_tq['mean_run_bars']:.1f} "
        f"short_runs%={btc_tq['short_runs_share']*100:.1f}% "
        f"trending_time%={btc_tq['trending_time_share']*100:.1f}% "
        f"ADX mean={btc_tq['adx_mean']:.1f}")
    log(f"ETH trend: flips={eth_tq['n_flips']} mean_run={eth_tq['mean_run_bars']:.1f} "
        f"short_runs%={eth_tq['short_runs_share']*100:.1f}% "
        f"trending_time%={eth_tq['trending_time_share']*100:.1f}% "
        f"ADX mean={eth_tq['adx_mean']:.1f}")

    # ---- Q3: market structure ----
    btc_ms = market_structure(btc_df)
    eth_ms = market_structure(eth_df)

    # ---- Q4: rotation simulations ----
    def score_supertrend_distance(btc_row, eth_row, _feat_row):
        def d(r):
            line = r.get("supertrend_line"); atr = r.get("atr")
            if line is None or atr is None or atr == 0:
                return None
            return (r["close"] - float(line)) / float(atr)
        return d(btc_row), d(eth_row)

    def score_rs(_btc_row, _eth_row, feat_row):
        v = feat_row["btc_minus_eth_return_n"]
        if pd.isna(v):
            return None, None
        return float(v), float(-v)

    rot_st_trades, rot_st_folds = _walk_forward_rotation(
        "rotation_supertrend_distance",
        btc_df, eth_df, st_strategy, features, score_supertrend_distance,
        args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
    )
    rot_rs_trades, rot_rs_folds = _walk_forward_rotation(
        "rotation_rs_score",
        btc_df, eth_df, st_strategy, features, score_rs,
        args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
    )

    def summarize_variant(trades, folds):
        oos = wf._stitch_metrics(trades)
        by_asset: dict[str, int] = {}
        ret_by_asset: dict[str, float] = {}
        for t in trades:
            a = t.get("asset", "?")
            by_asset[a] = by_asset.get(a, 0) + 1
            ret_by_asset[a] = ret_by_asset.get(a, 0.0) + t.get("ret", 0.0)
        fr = [f["test_metrics"]["total_return"] for f in folds]
        return {
            "trades": oos["trades"],
            "total_return": oos["total_return"],
            "max_drawdown": oos["max_drawdown"],
            "profit_factor": oos["profit_factor"] if oos["profit_factor"] != float("inf") else 9999.0,
            "sharpe_per_trade": oos["sharpe_per_trade"],
            "win_rate": oos["win_rate"],
            "n_folds": len(folds),
            "fold_pos": sum(1 for r in fr if r > 0),
            "fold_std": float(np.std(fr)) if fr else 0.0,
            "by_asset": "; ".join(f"{k}:{v}" for k, v in by_asset.items()),
            "ret_by_asset": "; ".join(f"{k}:{ret_by_asset[k]:+.4f}" for k in by_asset),
        }

    rot_st_summary = summarize_variant(rot_st_trades, rot_st_folds)
    rot_rs_summary = summarize_variant(rot_rs_trades, rot_rs_folds)

    log(f"rotation_supertrend_distance: trades={rot_st_summary['trades']} "
        f"ret={rot_st_summary['total_return']*100:+.2f}% DD={rot_st_summary['max_drawdown']*100:.2f}% "
        f"PF={rot_st_summary['profit_factor']:.2f} by_asset={rot_st_summary['by_asset']}")
    log(f"rotation_rs_score:           trades={rot_rs_summary['trades']} "
        f"ret={rot_rs_summary['total_return']*100:+.2f}% DD={rot_rs_summary['max_drawdown']*100:.2f}% "
        f"PF={rot_rs_summary['profit_factor']:.2f} by_asset={rot_rs_summary['by_asset']}")

    # ---- write CSV (wide, one row per metric category × asset / variant) ----
    rows = []
    for asset, diag, tq, ms in (("BTC", btc_diag, btc_tq, btc_ms),
                                ("ETH", eth_diag, eth_tq, eth_ms)):
        merged = {"asset_or_variant": f"solo_{asset.lower()}"}
        merged.update({f"trade_{k}": v for k, v in diag.items()})
        merged.update({f"trend_{k}": v for k, v in tq.items()})
        merged.update({f"struct_{k}": v for k, v in ms.items()})
        rows.append(merged)
    rows.append({"asset_or_variant": "rotation_supertrend_distance",
                 **{f"variant_{k}": v for k, v in rot_st_summary.items()}})
    rows.append({"asset_or_variant": "rotation_rs_score",
                 **{f"variant_{k}": v for k, v in rot_rs_summary.items()}})
    rows.append({"asset_or_variant": "foldwise_btc_vs_eth",
                 **{f"fold_{k}": v for k, v in foldwise.items()}})

    all_keys: list[str] = []
    seen = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k); all_keys.append(k)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=all_keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in all_keys})
    log(f"wrote CSV → {csv_path}")

    # ---- write raw-numbers MD (machine-readable; narrative report is hand-written) ----
    md = [
        f"# ETH vs BTC SuperTrend diagnostic comparison — {ts}",
        "",
        f"- BTC: {args.n_months}mo {args.timeframe} ({common[0].date()} -> {common[-1].date()})",
        f"- ETH: {args.n_months}mo {args.timeframe} (aligned, {len(common)} bars)",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "## Q1 — trade diagnostics", "",
        "| metric | BTC | ETH |",
        "|---|---:|---:|",
    ]
    for k in ("trades", "avg_winner", "avg_loser", "win_rate", "profit_factor",
              "expectancy", "avg_holding_bars", "median_holding_bars",
              "n_stop", "n_end", "n_trail_exit", "n_target_rsi", "n_max_hold",
              "stop_pct", "trail_pct", "max_hold_pct"):
        bv = btc_diag[k]; ev = eth_diag[k]
        def fmt(x):
            if isinstance(x, float):
                if k.endswith("_pct") or k in ("win_rate",):
                    return f"{x*100:.1f}%"
                if k in ("profit_factor", "avg_holding_bars", "median_holding_bars"):
                    return f"{x:.2f}"
                return f"{x:+.4f}" if k in ("avg_winner", "avg_loser", "expectancy") else f"{x:.4f}"
            return str(x)
        md.append(f"| {k} | {fmt(bv)} | {fmt(ev)} |")

    md += [
        "",
        "## Per-fold ETH-vs-BTC consistency",
        "",
        f"- folds compared: {foldwise['folds_compared']}",
        f"- BTC better in: **{foldwise['a_wins']}** folds",
        f"- ETH better in: **{foldwise['b_wins']}** folds",
        f"- ties: {foldwise['ties']}",
        f"- BTC fold-return mean: {foldwise['a_mean_ret']*100:+.2f}% σ {foldwise['a_std_ret']*100:.2f}%",
        f"- ETH fold-return mean: {foldwise['b_mean_ret']*100:+.2f}% σ {foldwise['b_std_ret']*100:.2f}%",
        "",
        "## Q2 — trend quality (full window, post-warmup)", "",
        "| metric | BTC | ETH |",
        "|---|---:|---:|",
    ]
    for k in ("n_flips", "run_count", "mean_run_bars", "median_run_bars",
              "short_runs_share", "long_runs_share", "trending_time_share",
              "adx_mean", "adx_median", "adx_pct_above_25",
              "atr_pct_mean", "atr_pct_median", "atr_pct_std"):
        bv = btc_tq[k]; ev = eth_tq[k]
        if k.endswith("_share") or k.endswith("_above_25"):
            md.append(f"| {k} | {bv*100:.1f}% | {ev*100:.1f}% |")
        elif k.startswith("atr_pct") or k.startswith("adx"):
            md.append(f"| {k} | {bv:.4f} | {ev:.4f} |")
        else:
            md.append(f"| {k} | {bv:.2f} | {ev:.2f} |")

    md += ["", "## Q3 — market structure (full window)", "",
           "| metric | BTC | ETH |",
           "|---|---:|---:|"]
    for k in btc_ms:
        bv = btc_ms[k]; ev = eth_ms[k]
        md.append(f"| {k} | {bv:.4f} | {ev:.4f} |")

    md += ["", "## Q4 — rotation selectors (walk-forward OOS)", "",
           "| variant | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | by asset |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for name, s in (("BTC solo (reference)", {**btc_diag,
                                              "total_return": btc_diag["expectancy"] * btc_diag["trades"],
                                              "max_drawdown": None}),
                    ("ETH solo (reference)", None),
                    ("rotation_supertrend_distance", rot_st_summary),
                    ("rotation_rs_score", rot_rs_summary)):
        if name == "BTC solo (reference)":
            md.append(f"| {name} | 20 | {btc_diag['trades']} | (see Q1) | — | {btc_diag['profit_factor']:.2f} | — | {btc_diag['win_rate']*100:.1f}% | — | BTC:{btc_diag['trades']} |")
        elif name == "ETH solo (reference)":
            md.append(f"| {name} | 20 | {eth_diag['trades']} | (see Q1) | — | {eth_diag['profit_factor']:.2f} | — | {eth_diag['win_rate']*100:.1f}% | — | ETH:{eth_diag['trades']} |")
        else:
            pf_s = "inf" if s["profit_factor"] == 9999.0 else f"{s['profit_factor']:.2f}"
            md.append(f"| `{name}` | {s['n_folds']} | {s['trades']} | "
                      f"{s['total_return']*100:+.2f}% | {s['max_drawdown']*100:.2f}% | "
                      f"{pf_s} | {s['sharpe_per_trade']:.3f} | "
                      f"{s['win_rate']*100:.1f}% | {s['fold_pos']}/{s['n_folds']} | "
                      f"{s['by_asset']} |")

    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    # ---- machine-readable dump for the report-writing stage ----
    dump = {
        "btc_diag": btc_diag, "eth_diag": eth_diag,
        "foldwise": foldwise,
        "btc_tq": btc_tq, "eth_tq": eth_tq,
        "btc_ms": btc_ms, "eth_ms": eth_ms,
        "rot_st": rot_st_summary, "rot_rs": rot_rs_summary,
    }
    with open(json_path, "w") as fh:
        json.dump(dump, fh, default=str, indent=2)
    log(f"wrote JSON dump → {json_path}")


if __name__ == "__main__":
    main()
