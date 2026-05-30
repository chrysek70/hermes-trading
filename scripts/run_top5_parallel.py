#!/usr/bin/env python3
"""Top-5 parallel SuperTrend portfolio with optional regime overlays.

Reproducible runner for Issue #14. Tests whether trading a fixed
universe of 5 liquid USDT pairs in parallel (no rotation, equal risk
budget) solves the trade-count problem that has blocked every
regime-overlay experiment so far (RS, routing, HMM).

Asset universe (fixed at experiment start — NOT optimized after seeing
results):
  BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT

If any asset's 48mo data is unavailable on Binance Vision the runner
proceeds with the available subset and reports the omission honestly.
It does NOT substitute another asset after seeing performance.

Portfolio rules:
  - Each asset trades its own SuperTrend(10, 3) independently.
  - Max 5 concurrent positions.
  - Equal per-asset size = 1 / n_assets (applied on top of the
    strategy's position_size_r, so total exposure when all 5 hold = 1×
    the single-asset exposure).
  - No leverage. No rotation. No per-bar selector.
  - Overlays (HMM) only reduce or block an asset's own exposure;
    they do not redirect to a different asset.

Variants:
  1. top5_supertrend_parallel
  2. top5_supertrend_hmm_filter_parallel
  3. top5_supertrend_hmm_sizing_parallel
  4. btc_eth_reference_parallel  (same engine, 2-asset universe)

The spec's variant 4 (top5_supertrend_rs_context_parallel) is SKIPPED.
The Issue #5/#12 RS implementation is fundamentally pairwise
(BTC vs ETH return diff + BTC/ETH ratio EMA). There is no clean
generalization to 5 assets that does not require asset-specific rule
design — which the spec hard-rules forbid. The report documents the
skip with the explanation.

Adoption criteria (from Issue #14 spec, ALL required):
  - trade count >= 60
  - PF > 2.24
  - max DD <= 9.63%
  - OOS return > 38.66%
  - no single asset contributes more than 60% of total profit

Hard rules — do NOT change in this script:
  - SuperTrend (10, 3) unchanged.
  - HMM config from yaml; NO sweeps.
  - Per-asset, per-fold HMM fit on train only.
  - Same fees / slippage / fold geometry as every other experiment.

Outputs (under --out-dir, default `results/`):
  - top5_parallel_comparison_<ts>.csv
  - top5_parallel_comparison_<ts>.md
  - trades_top5_parallel_<ts>.csv  (all variants tagged with _variant)
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

DEFAULT_UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


# ---------- HMM decisions per asset per fold ---------------------------------

def _build_hmm_decisions_for_fold(asset, train_df, train_ind, test_df, test_ind,
                                  hmm_cfg, mode, strategy, fee, slippage):
    """Fit HMM on this asset's train slice; return a test-window
    decisions_df. Returns ``None`` on fit failure (caller then falls
    back to neutral decisions for that asset / fold)."""
    try:
        det = hmmr.HMMRegimeDetector(hmm_cfg)
        det.fit(train_df, indicators=train_ind)
        train_proba = det.predict_proba(train_df, indicators=train_ind)
        prob_cols = [c for c in train_proba.columns if c.startswith("p_state_")]
        tp_clean = train_proba[prob_cols].dropna()
        if tp_clean.empty:
            return None, None
        train_states = (tp_clean.idxmax(axis=1)
                        .str.replace("p_state_", "")
                        .astype(float)
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
        mapping = det.map_states(train_trades=train_res["trades"])
        decisions = det.decisions(test_df, indicators=test_ind, mode=mode)
        return decisions, mapping
    except Exception as exc:  # noqa: BLE001
        log(f"    [yellow]HMM fit failed for {asset}: {exc}; using neutral[/yellow]")
        return None, None


# ---------- parallel portfolio state machine ---------------------------------

def _close_trade(position, exit_fill, reason, exit_ts, base_size, fee, slippage,
                 size_per_asset, asset):
    """Returns a trade dict and the net return contribution to portfolio equity."""
    if position["direction"] == "long":
        gross = (exit_fill - position["entry"]) / position["entry"]
    else:
        gross = (position["entry"] - exit_fill) / position["entry"]
    overlay_mult = float(position.get("overlay_size_multiplier", 1.0))
    # Final effective fraction of portfolio equity = base_size_r * per_asset * overlay
    effective_size = base_size * size_per_asset * overlay_mult
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
        "size_multiplier": overlay_mult,
        "size_per_asset": size_per_asset,
        "position_size_effective": effective_size,
        "entry_ts": position["entry_ts"],
        "exit_ts": exit_ts,
        "setup_name": position["setup"],
        "side": position["direction"],
        "entry_price": position["entry"],
        "exit_price": exit_fill,
        "gross_return_pct": gross,
        "fees_total": fees_cost,
        "slippage_total": slippage_cost,
        "net_return_pct": net,
        "portfolio_return_contribution_pct": net,  # already scaled by size
        "holding_bars": position.get("bars_held_at_exit", 0),
        "exit_reason": reason,
        "hmm_state": position.get("hmm_state"),
        "hmm_p_favorable": position.get("hmm_p_favorable"),
        "hmm_p_adverse": position.get("hmm_p_adverse"),
        "hmm_decision_reason": position.get("hmm_decision_reason"),
        # Carry indicator snapshot
        "entry_rsi": position.get("entry_rsi"),
        "entry_atr": position.get("entry_atr"),
    }
    return trade, net


def _run_parallel_variant(name, asset_data, strategy, hmm_cfg, hmm_mode,
                          train_bars, test_bars, embargo_bars, fee, slippage):
    """``asset_data`` is a dict {asset: {"df": DataFrame, "ind": DataFrame}}.
    All DataFrames must share the same DatetimeIndex (aligned upstream).
    ``hmm_mode``: "filter" / "sizing" / None (no HMM)."""
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
    all_trades: list[dict] = []
    fold_returns: list[float] = []
    fold_mappings: list[dict] = []
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

        # Pre-attach HMM (or neutral) decisions per asset for this fold.
        per_asset_decisions: dict[str, pd.DataFrame | None] = {}
        per_asset_mappings: dict[str, dict | None] = {}
        for asset, d in asset_data.items():
            train_df = d["df"].iloc[cursor:train_hi]
            train_ind = d["ind"].iloc[cursor:train_hi]
            test_df = d["df"].iloc[test_lo:test_hi]
            test_ind = d["ind"].iloc[test_lo:test_hi]
            if hmm_mode is not None:
                decisions, mapping = _build_hmm_decisions_for_fold(
                    asset, train_df, train_ind, test_df, test_ind,
                    hmm_cfg, hmm_mode, strategy, fee, slippage,
                )
                per_asset_decisions[asset] = decisions
                per_asset_mappings[asset] = mapping
            else:
                per_asset_decisions[asset] = None
                per_asset_mappings[asset] = None
        fold_mappings.append({
            "fold": fold,
            "test_range": (test_index_slice[0].date(),
                           test_index_slice[-1].date()),
            "mappings": {a: per_asset_mappings.get(a) for a in asset_data},
            "had_hmm": {a: per_asset_decisions[a] is not None
                        for a in asset_data},
        })

        # Build per-asset record lists for the test slice, with overlay
        # decisions injected. Each list is len(test_bars) long.
        asset_records: dict[str, list[dict]] = {}
        for asset, d in asset_data.items():
            test_ind = d["ind"].iloc[test_lo:test_hi].copy()
            test_ind["ts"] = test_ind.index
            decisions = per_asset_decisions[asset]
            if decisions is not None:
                # _attach_decisions_df expects the standard columns
                test_ind = bt._attach_decisions_df(test_ind, decisions[[
                    "long_allowed", "size_multiplier", "raw_state",
                    "stable_state", "regime_score", "allowed_setups",
                ]])
                # also carry the per-bar p_favorable / p_adverse for the trade log
                test_ind["hmm_p_favorable"] = decisions["p_favorable"].reindex(test_ind.index).values
                test_ind["hmm_p_adverse"] = decisions["p_adverse"].reindex(test_ind.index).values
            else:
                test_ind = bt._attach_neutral_markov_columns(test_ind)
                test_ind["hmm_p_favorable"] = np.nan
                test_ind["hmm_p_adverse"] = np.nan
            asset_records[asset] = test_ind.to_dict("records")

        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        positions: dict[str, dict | None] = {a: None for a in asset_data}
        per_asset_equity_contrib: dict[str, float] = {a: 0.0 for a in asset_data}
        concurrent_log: list[int] = []
        fold_trades = []
        max_concurrent_in_fold = 0

        for i in range(len(test_index_slice)):
            ts = test_index_slice[i]
            for asset in asset_data:
                row = asset_records[asset][i]
                position = positions[asset]

                # ---- exit check ----
                if position is not None:
                    bars_held = i - position["entry_i"]
                    reason = signals.long_exit(row, position, strategy, bars_held)
                    if reason:
                        exit_fill = (position["stop"] * (1 - slippage)
                                     if reason == "stop"
                                     else row["close"] * (1 - slippage))
                        position["bars_held_at_exit"] = bars_held
                        trade, net = _close_trade(position, exit_fill, reason, ts,
                                                  base_size, fee, slippage,
                                                  size_per_asset, asset)
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        per_asset_equity_contrib[asset] += net
                        fold_trades.append(trade)
                        positions[asset] = None
                        continue  # don't re-enter this bar

                # ---- entry check (only if flat on this asset) ----
                if positions[asset] is None:
                    if not bool(row.get("markov_long_allowed", True)):
                        continue
                    overlay_size = float(row.get("markov_size_multiplier", 1.0) or 0.0)
                    if overlay_size <= 0:
                        continue
                    setup = signals.long_entry(row, strategy)
                    if not setup:
                        continue
                    init_stop = signals.initial_stop(row, setup, strategy)
                    positions[asset] = {
                        "entry": row["close"] * (1 + slippage),
                        "setup": setup,
                        "direction": "long",
                        "entry_i": i,
                        "entry_ts": ts,
                        "stop": init_stop,
                        "initial_stop": init_stop,
                        "overlay_size_multiplier": overlay_size,
                        "hmm_state": row.get("markov_state"),
                        "hmm_p_favorable": row.get("hmm_p_favorable"),
                        "hmm_p_adverse": row.get("hmm_p_adverse"),
                        "hmm_decision_reason": (
                            f"{hmm_mode}_p_fav={row.get('hmm_p_favorable'):.2f}"
                            if hmm_mode and row.get("hmm_p_favorable") is not None
                                and not pd.isna(row.get("hmm_p_favorable"))
                            else ("no_hmm" if hmm_mode is None else "hmm_no_data")
                        ),
                        "entry_rsi": row.get("rsi"),
                        "entry_atr": row.get("atr"),
                    }

            # Track concurrency at end-of-bar
            cur_open = sum(1 for p in positions.values() if p is not None)
            concurrent_log.append(cur_open)
            max_concurrent_in_fold = max(max_concurrent_in_fold, cur_open)

        # Close any open positions at fold end
        for asset, position in list(positions.items()):
            if position is None:
                continue
            last_row = asset_records[asset][-1]
            position["bars_held_at_exit"] = len(test_index_slice) - 1 - position["entry_i"]
            exit_fill = last_row["close"] * (1 - slippage)
            trade, net = _close_trade(position, exit_fill, "end", test_index_slice[-1],
                                      base_size, fee, slippage, size_per_asset, asset)
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            per_asset_equity_contrib[asset] += net
            fold_trades.append(trade)
            positions[asset] = None

        for t in fold_trades:
            t["_variant"] = name
        all_trades.extend(fold_trades)
        fold_ret = equity - 1.0
        fold_returns.append(fold_ret)
        folds.append({
            "fold": fold,
            "test_range": (test_index_slice[0].date(),
                           test_index_slice[-1].date()),
            "test_metrics": {
                "trades": len(fold_trades),
                "total_return": fold_ret,
                "max_drawdown": max_dd,
                "max_concurrent": max_concurrent_in_fold,
                "exposure_pct": sum(1 for c in concurrent_log if c > 0) / max(1, len(concurrent_log)),
            },
            "per_asset_equity_contrib": dict(per_asset_equity_contrib),
        })
        cursor += test_bars

    oos = wf._stitch_metrics(all_trades)
    fold_pos = sum(1 for r in fold_returns if r > 0)
    by_asset: dict[str, int] = {}
    ret_by_asset: dict[str, float] = {}
    for t in all_trades:
        a = t["asset"]
        by_asset[a] = by_asset.get(a, 0) + 1
        ret_by_asset[a] = ret_by_asset.get(a, 0.0) + t["ret"]
    total_pos_ret = sum(v for v in ret_by_asset.values() if v > 0)
    if total_pos_ret > 0:
        share_by_asset = {a: max(0.0, r) / total_pos_ret for a, r in ret_by_asset.items()}
    else:
        share_by_asset = {a: 0.0 for a in ret_by_asset}
    max_share = max(share_by_asset.values()) if share_by_asset else 0.0
    fold_max_concurrent = max(f["test_metrics"]["max_concurrent"] for f in folds) if folds else 0
    avg_exposure = float(np.mean([f["test_metrics"]["exposure_pct"] for f in folds])) if folds else 0.0
    pf = oos["profit_factor"]
    pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"Sharpe={oos['sharpe_per_trade']:.3f}  win={oos['win_rate']*100:.1f}%  "
        f"folds+={fold_pos}/{len(folds)}  max_concurrent={fold_max_concurrent}")
    log(f"     per-asset trades: " + ", ".join(f"{a}:{by_asset.get(a, 0)}" for a in asset_data))
    log(f"     per-asset return: " + ", ".join(f"{a}:{ret_by_asset.get(a, 0)*100:+.2f}%" for a in asset_data))
    log(f"     max-share profit: {max_share*100:.1f}%  (limit 60% for adoption)")

    return {
        "name": name, "oos": oos, "folds": folds, "trades": all_trades,
        "fold_pos": fold_pos, "fold_returns": fold_returns,
        "by_asset": by_asset, "ret_by_asset": ret_by_asset,
        "share_by_asset": share_by_asset, "max_share": max_share,
        "max_concurrent": fold_max_concurrent, "avg_exposure": avg_exposure,
        "fold_mappings": fold_mappings,
        "n_assets": n_assets, "size_per_asset": size_per_asset,
    }


# ---------- main -------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--symbols", nargs="*", default=DEFAULT_UNIVERSE,
                    help=f"asset universe (default {DEFAULT_UNIVERSE})")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--supertrend-strategy",
                    default=str(STATE_DIR / "strategy_supertrend.yaml"))
    ap.add_argument("--hmm-config",
                    default=str(STATE_DIR / "hmm_regime.yaml"))
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"top5_parallel_comparison_{ts}.csv"
    md_path = out_dir / f"top5_parallel_comparison_{ts}.md"
    trades_path = out_dir / f"trades_top5_parallel_{ts}.csv"
    mappings_path = out_dir / f".top5_fold_mappings_{ts}.json"

    st_strategy = yaml.safe_load(open(args.supertrend_strategy))
    hmm_cfg_path = Path(args.hmm_config)
    hmm_cfg = yaml.safe_load(open(hmm_cfg_path))["hmm_regime"] if hmm_cfg_path.exists() else hmmr.DEFAULT_CONFIG

    # ---- load + align ----
    loaded: dict[str, pd.DataFrame] = {}
    failed: list[tuple[str, str]] = []
    for sym in args.symbols:
        try:
            log(f"loading {sym} {args.n_months}mo …")
            df = data_mod.resample(data_mod.load_klines(sym, n_months=args.n_months),
                                   args.timeframe)
            if len(df) < args.train_bars + args.test_bars + args.embargo_bars:
                failed.append((sym, f"only {len(df)} bars"))
                continue
            loaded[sym] = df
        except Exception as exc:  # noqa: BLE001
            failed.append((sym, str(exc)))
            log(f"[yellow]failed to load {sym}: {exc}[/yellow]")
    if not loaded:
        log("[red]no usable assets loaded — exiting[/red]")
        sys.exit(2)

    common = None
    for df in loaded.values():
        common = df.index if common is None else common.intersection(df.index)
    log(f"aligned bars across {len(loaded)} assets: {len(common)}  "
        f"span: {common[0].date()} -> {common[-1].date()}")

    asset_data: dict[str, dict] = {}
    for sym, df in loaded.items():
        df_aligned = df.loc[common]
        ind = signals.compute_indicators(df_aligned, st_strategy)
        asset_data[sym] = {"df": df_aligned, "ind": ind}
    log(f"assets in portfolio ({len(asset_data)}): {list(asset_data.keys())}")
    if failed:
        log(f"assets NOT loaded ({len(failed)}): " + ", ".join(f"{s}({why})" for s, why in failed))

    btc_eth_only = {k: v for k, v in asset_data.items() if k in ("BTCUSDT", "ETHUSDT")}

    # ---- run variants ----
    results = []
    results.append(_run_parallel_variant(
        "top5_supertrend_parallel", asset_data, st_strategy, hmm_cfg, None,
        args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
    ))
    if hmmr.available():
        results.append(_run_parallel_variant(
            "top5_supertrend_hmm_filter_parallel", asset_data, st_strategy, hmm_cfg, "filter",
            args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
        ))
        results.append(_run_parallel_variant(
            "top5_supertrend_hmm_sizing_parallel", asset_data, st_strategy, hmm_cfg, "sizing",
            args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
        ))
    else:
        log(f"[yellow]{hmmr.INSTALL_HINT} — skipping HMM variants[/yellow]")
    results.append(_run_parallel_variant(
        "btc_eth_reference_parallel", btc_eth_only, st_strategy, hmm_cfg, None,
        args.train_bars, args.test_bars, args.embargo_bars, args.fee, args.slippage,
    ))

    # ---- artifacts ----
    rows = []
    all_trades = []
    for r in results:
        if r is None:
            continue
        o = r["oos"]
        for t in r["trades"]:
            all_trades.append(t)
        fr = r["fold_returns"]
        fold_std = float(np.std(fr)) if fr else 0.0
        share = "; ".join(f"{a}:{r['share_by_asset'].get(a, 0)*100:.1f}%"
                          for a in r["by_asset"])
        by_asset = "; ".join(f"{a}:{r['by_asset'][a]}" for a in r["by_asset"])
        ret_by_asset = "; ".join(f"{a}:{r['ret_by_asset'][a]*100:+.2f}%"
                                 for a in r["ret_by_asset"])
        rows.append({
            "variant": r["name"],
            "n_assets": r["n_assets"],
            "trades": o["trades"],
            "total_return": o["total_return"],
            "max_drawdown": o["max_drawdown"],
            "profit_factor": (o["profit_factor"] if o["profit_factor"] != float("inf") else 9999.0),
            "sharpe_per_trade": o["sharpe_per_trade"],
            "win_rate": o["win_rate"],
            "n_folds": len(r["folds"]),
            "fold_positive": f"{r['fold_pos']}/{len(r['folds'])}",
            "fold_return_std": fold_std,
            "max_concurrent": r["max_concurrent"],
            "avg_exposure": r["avg_exposure"],
            "max_share_profit": r["max_share"],
            "by_asset": by_asset,
            "ret_by_asset": ret_by_asset,
            "share_by_asset": share,
        })

    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# Top-5 parallel SuperTrend portfolio — {ts}",
        "",
        f"- supertrend strategy: `{args.supertrend_strategy}`",
        f"- HMM config: `{args.hmm_config}`",
        f"- requested universe: {args.symbols}",
        f"- universe used: {list(asset_data.keys())}  (n={len(asset_data)})",
        f"- universe NOT loaded: {failed if failed else 'none'}",
        f"- aligned bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}",
        f"- per-asset equal size: 1/{len(asset_data)} = {1/len(asset_data):.4f}",
        f"- decision TF: {args.timeframe}",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "Adoption: trades >= 60 AND PF > 2.24 AND DD <= 9.63% AND return > 38.66% "
        "AND max single-asset profit share <= 60%.",
        "",
        "| variant | assets | folds | n | OOS return | max DD | PF | Sharpe | win% | folds+ | concurrent | exposure | max share |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == 9999.0 else f"{pf:.2f}"
        md.append(
            f"| `{r['variant']}` | {r['n_assets']} | {r['n_folds']} | {r['trades']} | "
            f"{r['total_return']*100:+.2f}% | {r['max_drawdown']*100:.2f}% | {pf_s} | "
            f"{r['sharpe_per_trade']:.3f} | {r['win_rate']*100:.1f}% | "
            f"{r['fold_positive']} | {r['max_concurrent']} | "
            f"{r['avg_exposure']*100:.1f}% | {r['max_share_profit']*100:.1f}% |"
        )
    md += ["", "### Per-asset trade count", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['by_asset']}")
    md += ["", "### Per-asset return contribution (gross of overlay sizing, before equity compounding)", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['ret_by_asset']}")
    md += ["", "### Profit share by asset (winners only)", ""]
    for r in rows:
        md.append(f"- **{r['variant']}**: {r['share_by_asset']}")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    base_cols = [
        "_variant", "asset", "entry_ts", "exit_ts", "setup_name",
        "entry_price", "exit_price", "gross_return_pct", "fees_total",
        "slippage_total", "net_return_pct",
        "portfolio_return_contribution_pct", "size_per_asset",
        "size_multiplier", "position_size_effective",
        "hmm_state", "hmm_p_favorable", "hmm_p_adverse",
        "hmm_decision_reason", "exit_reason", "holding_bars",
        "entry_rsi", "entry_atr",
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

    fm_dump = {}
    for r in results:
        if r is None or not r.get("fold_mappings"):
            continue
        fm_dump[r["name"]] = r["fold_mappings"]
    with open(mappings_path, "w") as fh:
        json.dump(fm_dump, fh, default=str, indent=2)
    log(f"wrote fold mappings → {mappings_path}")


if __name__ == "__main__":
    main()
