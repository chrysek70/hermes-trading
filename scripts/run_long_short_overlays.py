#!/usr/bin/env python3
"""Overlay sweep on the BTC/ETH SuperTrend long-short variant (Issue #20).

Issue #19 produced a long-short variant that beat the live floor on PF,
return and trades but missed the DD gate by 0.22 pp (5.76% vs 5.54%).
This script tests whether any *already-implemented* overlay can reduce
the long-short DD below the 5.54% gate without losing return/PF.

Variants:
  1. btc_eth_long_short_baseline
  2. btc_eth_long_short_hmm_filter
  3. btc_eth_long_short_hmm_sizing
  4. btc_eth_long_short_funding_filter
  5. btc_eth_long_short_funding_sizing
  6. btc_eth_long_short_rs_sizing

Overlay direction-mapping conventions (per Issue #20 spec):

  HMM: the per-asset Gaussian HMM identifies high-vol / regime-shift
       bars. High vol hurts EITHER direction (chop, fakeouts), so the
       same per-asset HMM decision is applied to both long and short
       entries. Volatility-based regime mapping; no "down = adverse"
       assumption.

  Funding: symmetric inversion. Long entries blocked / down-sized when
       funding percentile is HIGH (overheated). Short entries blocked
       / down-sized when funding percentile is LOW (extreme negative).

  RS: direction-aware. Each asset's existing `build_asset_decisions`
       already encodes "this asset is stronger". For the LONG side of
       asset X we use X's own decision. For the SHORT side of X we use
       the OTHER asset's decision (asset X is the weaker one when the
       other asset is stronger). No new RS rule designed.

Adoption gates (from Issue #20):

  Primary:    DD <= 5.54% AND PF >= 3.26 AND return >= +139.47% AND trades >= 100
  Secondary:  DD <= 5.54% AND PF >= 3.00 AND return >= +120%    AND trades >= 100

Hard rules respected:
  - SuperTrend (10, 3) unchanged.
  - No new parameters / thresholds. HMM yaml = state/hmm_regime.yaml.
    Funding yaml = state/strategy_supertrend_rs.yaml.relative_strength
    (lookback/ratio_ema only) + Issue #7 percentiles (block at 95,
    half at 90).
  - Same fees / slippage / fold geometry as every other experiment.
  - No live wiring. state/live_multiasset.yaml is not read or modified.

Outputs (under --out-dir, default ``results/``):
  - long_short_overlay_comparison_<ts>.csv
  - long_short_overlay_comparison_<ts>.md
  - trades_long_short_overlay_<ts>.csv (all variants tagged)
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
from hermes_trading import funding as funding_mod
from hermes_trading import hmm_regime as hmmr
from hermes_trading import relative_strength as rs
from hermes_trading import signals
from hermes_trading import walk_forward as wf


# ----- per-asset overlay decision builders ---------------------------------

def _build_funding_decisions(price_index: pd.DatetimeIndex, symbol: str,
                             n_months: int, mode: str, side: str,
                             window: int = 180,
                             block_above: float = 95.0,
                             half_above: float = 90.0) -> pd.DataFrame:
    """Per-direction funding decisions.

    ``side='long'``  : block / half-size when funding percentile is HIGH.
                       (Original Issue #7 behaviour.)
    ``side='short'`` : block / half-size when funding percentile is LOW
                       (extreme negative funding → bad for shorts).
    """
    f = funding_mod.load_funding(symbol, n_months=n_months)
    aligned = funding_mod.align_to_index(f, price_index)
    pct = funding_mod.rolling_percentile(aligned, window=window)

    if side == "long":
        warmup = pct.isna()
        if mode == "filter":
            long_allowed = pct < block_above
            size_mult = pd.Series(1.0, index=price_index, dtype=float)
            long_allowed = long_allowed.where(~warmup, True)
        elif mode == "sizing":
            size_mult = pd.Series(1.0, index=price_index, dtype=float)
            size_mult = size_mult.mask(pct >= half_above, 0.5)
            size_mult = size_mult.mask(pct >= block_above, 0.0)
            long_allowed = size_mult > 0
            size_mult = size_mult.where(~warmup, 1.0)
            long_allowed = long_allowed.where(~warmup, True)
        else:
            raise ValueError(mode)
    elif side == "short":
        # Symmetric inversion: short_block threshold at 100 - block_above,
        # short_half threshold at 100 - half_above.
        warmup = pct.isna()
        short_block_below = 100.0 - block_above   # e.g. 5.0
        short_half_below = 100.0 - half_above     # e.g. 10.0
        if mode == "filter":
            long_allowed = pct > short_block_below   # named long_allowed for
                                                     # plumbing reuse; actually
                                                     # gates the short here
            size_mult = pd.Series(1.0, index=price_index, dtype=float)
            long_allowed = long_allowed.where(~warmup, True)
        elif mode == "sizing":
            size_mult = pd.Series(1.0, index=price_index, dtype=float)
            size_mult = size_mult.mask(pct <= short_half_below, 0.5)
            size_mult = size_mult.mask(pct <= short_block_below, 0.0)
            long_allowed = size_mult > 0
            size_mult = size_mult.where(~warmup, 1.0)
            long_allowed = long_allowed.where(~warmup, True)
        else:
            raise ValueError(mode)
    else:
        raise ValueError(side)

    return pd.DataFrame({
        "long_allowed": long_allowed.astype(bool),
        "size_multiplier": size_mult.astype(float),
    }, index=price_index)


def _build_hmm_decisions_for_fold(asset, train_df, train_ind, test_df, test_ind,
                                  hmm_cfg, mode, strategy, fee, slippage):
    """Fit a per-asset HMM on train, map states (vol-based fallback when
    train trade counts are sparse), and return the test-window decisions
    DataFrame. Returns None on fit failure (caller falls back to neutral)."""
    try:
        det = hmmr.HMMRegimeDetector(hmm_cfg)
        det.fit(train_df, indicators=train_ind)
        train_proba = det.predict_proba(train_df, indicators=train_ind)
        prob_cols = [c for c in train_proba.columns if c.startswith("p_state_")]
        tp_clean = train_proba[prob_cols].dropna()
        if tp_clean.empty:
            return None
        train_states = (tp_clean.idxmax(axis=1)
                        .str.replace("p_state_", "").astype(float)
                        .reindex(train_proba.index))
        train_ind_tagged = bt._attach_neutral_markov_columns(train_ind.copy())
        train_ind_tagged["hmm_state_at_entry"] = train_states.reindex(train_ind.index).values
        train_records = train_ind_tagged.to_dict("records")
        train_res = bt._run_state_machine(train_records, strategy,
                                          warmup=0, fee=fee, slippage=slippage)
        for t in train_res["trades"]:
            idx = t.get("entry_ts")
            if idx is not None and idx in train_states.index:
                v = train_states.loc[idx]
                t["hmm_state_at_entry"] = int(v) if not pd.isna(v) else None
        det.map_states(train_trades=train_res["trades"])
        return det.decisions(test_df, indicators=test_ind, mode=mode)
    except Exception as exc:  # noqa: BLE001
        log(f"    [yellow]HMM fit failed for {asset}: {exc}; neutral fallback[/yellow]")
        return None


# ----- parallel coordinator with per-direction overlay -----------------------

def _open_position(asset, row, setup, direction, base_size, size_per_asset,
                   overlay_mult, slippage, strategy, i, ts):
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
        "overlay_size_multiplier": float(overlay_mult),
        "size_per_asset": size_per_asset,
        "base_size": base_size,
        "entry_rsi": float(row["rsi"]) if pd.notna(row.get("rsi")) else None,
        "entry_atr": float(row["atr"]) if pd.notna(row.get("atr")) else None,
    }


def _close_position(position, exit_fill, reason, ts, fee, asset, bars_held):
    if position["direction"] == "long":
        gross = (exit_fill - position["entry"]) / position["entry"]
    else:
        gross = (position["entry"] - exit_fill) / position["entry"]
    effective = (position["base_size"] * position["size_per_asset"]
                 * position["overlay_size_multiplier"])
    net = (gross - 2 * fee) * effective
    trade = {
        "asset": asset,
        "ret": net,
        "reason": reason,
        "setup": position["setup"],
        "direction": position["direction"],
        "bars": bars_held,
        "size_per_asset": position["size_per_asset"],
        "overlay_size_multiplier": position["overlay_size_multiplier"],
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


def _overlay_for(decision_df: pd.DataFrame | None, ts) -> tuple[bool, float]:
    """Look up (allowed, size_multiplier) for an asset+direction at ts.
    A None decision_df means "neutral" — always allowed at full size."""
    if decision_df is None or ts not in decision_df.index:
        return True, 1.0
    row = decision_df.loc[ts]
    allowed = bool(row.get("long_allowed", True))
    mult = float(row.get("size_multiplier", 1.0) or 0.0)
    return allowed, mult


def _run_parallel_overlay(name, btc_df, eth_df, strategy,
                          btc_long_dec, eth_long_dec,
                          btc_short_dec, eth_short_dec,
                          train_bars, test_bars, embargo_bars,
                          fee, slippage, max_open=2,
                          per_fold_hmm: dict | None = None):
    """Walk-forward parallel coordinator that supports per-asset
    per-direction overlay decision DataFrames.

    ``per_fold_hmm`` is an optional dict of dicts:
        {asset: {"mode": "filter"|"sizing", "hmm_cfg": cfg}}
    When provided, the coordinator builds the per-asset HMM
    decisions PER FOLD (fit on train only). Otherwise the four
    decision DataFrames above are used as fixed precomputed
    overlays.
    """
    log(f"========== {name} ==========")
    btc_ind = signals.compute_indicators(btc_df, strategy)
    eth_ind = signals.compute_indicators(eth_df, strategy)
    common = btc_ind.index.intersection(eth_ind.index)
    btc_ind = btc_ind.loc[common].copy(); btc_ind["ts"] = btc_ind.index
    eth_ind = eth_ind.loc[common].copy(); eth_ind["ts"] = eth_ind.index

    assets = ("BTCUSDT", "ETHUSDT")
    asset_df = {"BTCUSDT": btc_df.loc[common], "ETHUSDT": eth_df.loc[common]}
    asset_ind = {"BTCUSDT": btc_ind, "ETHUSDT": eth_ind}
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

        # Build per-fold HMM decisions if requested
        per_fold_dec = {a: None for a in assets}
        if per_fold_hmm is not None:
            for a in assets:
                cfg = per_fold_hmm[a]
                tr_df = asset_df[a].iloc[cursor:train_hi]
                tr_ind = asset_ind[a].iloc[cursor:train_hi]
                te_df = asset_df[a].iloc[test_lo:test_hi]
                te_ind = asset_ind[a].iloc[test_lo:test_hi]
                per_fold_dec[a] = _build_hmm_decisions_for_fold(
                    a, tr_df, tr_ind, te_df, te_ind,
                    cfg["hmm_cfg"], cfg["mode"], strategy, fee, slippage,
                )

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
                            position, exit_fill, reason, ts, fee, asset, bars_held,
                        )
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        fold_trades.append(trade)
                        positions[asset] = None
                        continue

                if positions[asset] is None:
                    open_count = sum(1 for p in positions.values() if p is not None)
                    if open_count >= max_open:
                        continue

                    setup_l = signals.long_entry(row, strategy)
                    if setup_l:
                        # decide overlay for this asset+direction
                        if per_fold_hmm is not None:
                            allowed, mult = _overlay_for(per_fold_dec[asset], ts)
                        elif asset == "BTCUSDT":
                            allowed, mult = _overlay_for(btc_long_dec, ts)
                        else:
                            allowed, mult = _overlay_for(eth_long_dec, ts)
                        if allowed and mult > 0:
                            positions[asset] = _open_position(
                                asset, row, setup_l, "long",
                                base_size, size_per_asset, mult, slippage, strategy, i, ts,
                            )
                        continue

                    setup_s = signals.short_entry(row, strategy)
                    if setup_s:
                        if per_fold_hmm is not None:
                            allowed, mult = _overlay_for(per_fold_dec[asset], ts)
                        elif asset == "BTCUSDT":
                            allowed, mult = _overlay_for(btc_short_dec, ts)
                        else:
                            allowed, mult = _overlay_for(eth_short_dec, ts)
                        if allowed and mult > 0:
                            positions[asset] = _open_position(
                                asset, row, setup_s, "short",
                                base_size, size_per_asset, mult, slippage, strategy, i, ts,
                            )

            cur_open = sum(1 for p in positions.values() if p is not None)
            fold_max_concurrent = max(fold_max_concurrent, cur_open)

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
                fee, asset, bars_held,
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
    long_ret_sum = sum(t["ret"] for t in all_trades if t.get("direction") == "long")
    short_ret_sum = sum(t["ret"] for t in all_trades if t.get("direction") == "short")
    log(f"  -> trades={oos['trades']} (L={n_long}, S={n_short})  "
        f"ret={oos['total_return']*100:+.2f}%  DD={oos['max_drawdown']*100:.2f}%  "
        f"PF={pf_s}  Sharpe={oos['sharpe_per_trade']:.3f}  "
        f"win={oos['win_rate']*100:.1f}%  folds+={fold_pos}/{len(folds)}  "
        f"max_concurrent={max_concurrent}")
    return {
        "name": name, "oos": oos, "folds": folds, "trades": all_trades,
        "fold_pos": fold_pos, "fold_returns": fold_returns,
        "max_concurrent": max_concurrent, "n_long": n_long, "n_short": n_short,
        "long_ret_sum": long_ret_sum, "short_ret_sum": short_ret_sum,
    }


# ---------- main -------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--long-short-strategy",
                    default=str(STATE_DIR / "strategy_supertrend_long_short.yaml"))
    ap.add_argument("--hmm-config",
                    default=str(STATE_DIR / "hmm_regime.yaml"))
    ap.add_argument("--rs-strategy",
                    default=str(STATE_DIR / "strategy_supertrend_multiasset_rs.yaml"))
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    if not hmmr.available():
        log(f"[red]{hmmr.INSTALL_HINT}[/red]")
        sys.exit(2)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"long_short_overlay_comparison_{ts}.csv"
    md_path = out_dir / f"long_short_overlay_comparison_{ts}.md"
    trades_path = out_dir / f"trades_long_short_overlay_{ts}.csv"

    ls_strategy = yaml.safe_load(open(args.long_short_strategy))
    hmm_cfg = yaml.safe_load(open(args.hmm_config))["hmm_regime"]
    rs_cfg = yaml.safe_load(open(args.rs_strategy))["relative_strength"]

    log(f"loading BTC + ETH {args.n_months}mo …")
    btc_df = data_mod.resample(data_mod.load_klines("BTCUSDT", n_months=args.n_months),
                               args.timeframe)
    eth_df = data_mod.resample(data_mod.load_klines("ETHUSDT", n_months=args.n_months),
                               args.timeframe)
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]; eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    # ---- Pre-compute fixed (non-fold-fitted) overlay decisions ----
    log("building funding overlays (long-side: block at p95; short-side: block at p5) …")
    btc_long_fund_filter = _build_funding_decisions(
        common, "BTCUSDT", args.n_months, mode="filter", side="long",
    )
    btc_short_fund_filter = _build_funding_decisions(
        common, "BTCUSDT", args.n_months, mode="filter", side="short",
    )
    eth_long_fund_filter = _build_funding_decisions(
        common, "ETHUSDT", args.n_months, mode="filter", side="long",
    )
    eth_short_fund_filter = _build_funding_decisions(
        common, "ETHUSDT", args.n_months, mode="filter", side="short",
    )
    btc_long_fund_sizing = _build_funding_decisions(
        common, "BTCUSDT", args.n_months, mode="sizing", side="long",
    )
    btc_short_fund_sizing = _build_funding_decisions(
        common, "BTCUSDT", args.n_months, mode="sizing", side="short",
    )
    eth_long_fund_sizing = _build_funding_decisions(
        common, "ETHUSDT", args.n_months, mode="sizing", side="long",
    )
    eth_short_fund_sizing = _build_funding_decisions(
        common, "ETHUSDT", args.n_months, mode="sizing", side="short",
    )

    log("building RS decisions (direction-aware: long uses own decision; short uses the other asset's) …")
    rs_features = rs.compute_multi_asset_features(
        btc_df, eth_df,
        lookback_bars=rs_cfg["lookback_bars"], ratio_ema=rs_cfg["ratio_ema"],
    )
    btc_rs = rs.build_asset_decisions(rs_features, asset="btc", mode="sizing",
                                      min_return_advantage=rs_cfg["min_return_advantage"])
    eth_rs = rs.build_asset_decisions(rs_features, asset="eth", mode="sizing",
                                      min_return_advantage=rs_cfg["min_return_advantage"])
    # Long BTC uses BTC's decision (BTC stronger).
    # Short BTC uses ETH's decision (ETH stronger ↔ BTC weaker → short BTC favored).
    btc_long_rs = btc_rs
    btc_short_rs = eth_rs
    eth_long_rs = eth_rs
    eth_short_rs = btc_rs

    results = []

    # 1. baseline long-short
    results.append(_run_parallel_overlay(
        "btc_eth_long_short_baseline",
        btc_df, eth_df, ls_strategy,
        None, None, None, None,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))

    # 2. HMM filter (per-fold per-asset fit)
    results.append(_run_parallel_overlay(
        "btc_eth_long_short_hmm_filter",
        btc_df, eth_df, ls_strategy,
        None, None, None, None,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
        per_fold_hmm={
            "BTCUSDT": {"mode": "filter", "hmm_cfg": hmm_cfg},
            "ETHUSDT": {"mode": "filter", "hmm_cfg": hmm_cfg},
        },
    ))

    # 3. HMM sizing
    results.append(_run_parallel_overlay(
        "btc_eth_long_short_hmm_sizing",
        btc_df, eth_df, ls_strategy,
        None, None, None, None,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
        per_fold_hmm={
            "BTCUSDT": {"mode": "sizing", "hmm_cfg": hmm_cfg},
            "ETHUSDT": {"mode": "sizing", "hmm_cfg": hmm_cfg},
        },
    ))

    # 4. Funding filter (direction-aware)
    results.append(_run_parallel_overlay(
        "btc_eth_long_short_funding_filter",
        btc_df, eth_df, ls_strategy,
        btc_long_fund_filter, eth_long_fund_filter,
        btc_short_fund_filter, eth_short_fund_filter,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))

    # 5. Funding sizing (direction-aware)
    results.append(_run_parallel_overlay(
        "btc_eth_long_short_funding_sizing",
        btc_df, eth_df, ls_strategy,
        btc_long_fund_sizing, eth_long_fund_sizing,
        btc_short_fund_sizing, eth_short_fund_sizing,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))

    # 6. RS sizing (direction-aware)
    results.append(_run_parallel_overlay(
        "btc_eth_long_short_rs_sizing",
        btc_df, eth_df, ls_strategy,
        btc_long_rs, eth_long_rs,
        btc_short_rs, eth_short_rs,
        args.train_bars, args.test_bars, args.embargo_bars,
        args.fee, args.slippage,
    ))

    # ---- artifacts ----
    rows = []
    all_trades = []
    for r in results:
        o = r["oos"]
        for t in r["trades"]:
            all_trades.append(t)
        fr = r["fold_returns"]
        fold_std = float(np.std(fr)) if fr else 0.0
        rows.append({
            "variant": r["name"],
            "trades": o["trades"],
            "n_long": r["n_long"],
            "n_short": r["n_short"],
            "total_return": o["total_return"],
            "long_return_contrib": r["long_ret_sum"],
            "short_return_contrib": r["short_ret_sum"],
            "max_drawdown": o["max_drawdown"],
            "profit_factor": (o["profit_factor"]
                              if o["profit_factor"] != float("inf") else 9999.0),
            "sharpe_per_trade": o["sharpe_per_trade"],
            "win_rate": o["win_rate"],
            "n_folds": len(r["folds"]),
            "fold_positive": f"{r['fold_pos']}/{len(r['folds'])}",
            "fold_return_std": fold_std,
            "max_concurrent": r["max_concurrent"],
        })

    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# BTC/ETH long-short + overlays — {ts}",
        "",
        f"- long-short strategy: `{args.long_short_strategy}`",
        f"- HMM config: `{args.hmm_config}` (per-asset per-fold fit; train-only mapping)",
        f"- RS config: lookback={rs_cfg['lookback_bars']}, ratio_ema={rs_cfg['ratio_ema']} "
        f"(direction-aware: long uses own decision, short uses other asset's)",
        "- funding overlay: long-side blocks at p95 (Issue #7); short-side mirrors at p5",
        f"- universe: BTC/USDT + ETH/USDT (parallel)",
        f"- 48mo span: {common[0].date()} -> {common[-1].date()} ({len(common)} bars)",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "Adoption (primary): DD <= 5.54% AND PF >= 3.26 AND return >= +139.47% AND trades >= 100.",
        "Adoption (secondary): DD <= 5.54% AND PF >= 3.00 AND return >= +120% AND trades >= 100.",
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
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    base_cols = [
        "_variant", "asset", "entry_ts", "exit_ts", "setup_name",
        "direction", "entry_price", "exit_price",
        "gross_return_pct", "net_return_pct",
        "size_per_asset", "overlay_size_multiplier", "position_size_effective",
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
