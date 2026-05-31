#!/usr/bin/env python3
"""Phase 5 — adaptive risk-layer rules on top of the adopted long-short
funding candidate.

These rules DO NOT change alpha. They only modulate exposure (size or
pause) based on diagnostics computed CAUSALLY from the recent trade
history or regime model.

Rules tested (locked spec):
  R1: Pause (size=0) when rolling 10-trade PF < 1.0       (consec pause until next win)
  R2: Half-size after 3 consecutive losses                (resumed full size after 1 win)
  R3: Half-size when rolling 30-day return < -3%
  R4: Half-size when stop-exit frequency > 80% over last 5 trades
  R5: Half-size when HMM adverse probability > 0.7 at entry (per-asset per-fold HMM)
  R6: Volatility-quartile sizing (same as Issue #27 vol_sizing)

Hard rules respected:
  - SuperTrend(10, 3) unchanged.
  - Funding gate: block long >= p95, block short <= p5.
  - fee=0.001/side, slippage=0.0005.
  - Walk-forward (train=1440 / test=360 / embargo=6, 4h).
  - No threshold tuning beyond the spec. Where I had to fix
    something to a sensible default (e.g. consecutive-loss cooldown
    after 1 win for R2), that choice is fixed and noted in the
    report.

Outputs:
  - results/adaptive_regime_response_<ts>.csv
  - results/adaptive_regime_response_<ts>.md
  - results/adaptive_regime_response_trades_<ts>.csv
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
from hermes_trading import hmm_regime as hmmr
from hermes_trading import signals
from hermes_trading import walk_forward as wf

ASSETS = ("BTCUSDT", "ETHUSDT")

HALF = 0.5
ZERO = 0.0
FULL = 1.0

ADAPTIVE_THRESHOLDS = {
    "R1_pf_window": 10,
    "R1_pf_threshold": 1.0,
    "R2_consec_losses": 3,
    "R3_window_days": 30,
    "R3_return_threshold": -0.03,
    "R4_stop_window": 5,
    "R4_stop_freq_threshold": 0.80,
    "R5_hmm_adverse_threshold": 0.70,
    "R6_vol_window_bars": 24,
}


def _build_funding(price_index, symbol, n_months, side,
                   block_long=95.0, block_short=5.0, window=180):
    f = funding_mod.load_funding(symbol, n_months=n_months)
    aligned = funding_mod.align_to_index(f, price_index)
    pct = funding_mod.rolling_percentile(aligned, window=window)
    warmup = pct.isna()
    if side == "long":
        allowed = pct < block_long
    else:
        allowed = pct > block_short
    allowed = allowed.where(~warmup, True)
    return pd.DataFrame({"long_allowed": allowed.astype(bool)}, index=price_index)


def _hmm_p_adverse(asset, train_df, train_ind, test_df, test_ind,
                   hmm_cfg, strategy, fee, slippage):
    """Per-fold per-asset: fit HMM on train, return P(adverse) series
    aligned to test bars. Returns None on fit failure."""
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
        test_proba = det.predict_proba(test_df, indicators=test_ind)
        adv_state = next(s for s, lbl in det.state_labels_.items()
                         if lbl == "adverse")
        return test_proba[f"p_state_{adv_state}"]
    except Exception as exc:  # noqa: BLE001
        log(f"    [yellow]HMM fit failed for {asset}: {exc}[/yellow]")
        return None


def _vol_quartile_thresholds(train_df, test_df, window=24):
    """Returns (rvol_test_series, q25, q75) for vol_sizing rule R6."""
    full = pd.concat([train_df["close"], test_df["close"]])
    log_ret = np.log(full / full.shift(1))
    rvol = log_ret.rolling(window, min_periods=window).std()
    rvol_train = rvol.loc[train_df.index].dropna()
    rvol_test = rvol.loc[test_df.index]
    if len(rvol_train) < 8:
        return rvol_test, None, None
    return rvol_test, float(rvol_train.quantile(0.25)), float(rvol_train.quantile(0.75))


def _run_rule(name, rule_code, btc_df, eth_df, strategy,
              long_fund, short_fund, train_bars, test_bars, embargo,
              fee, slippage, hmm_cfg=None, max_open=2):
    """rule_code ∈ {'baseline','R1','R2','R3','R4','R5','R6'}."""
    log(f"========== {name} (rule={rule_code}) ==========")
    btc_ind = signals.compute_indicators(btc_df, strategy)
    eth_ind = signals.compute_indicators(eth_df, strategy)
    common = btc_ind.index.intersection(eth_ind.index)
    btc_ind = btc_ind.loc[common]; eth_ind = eth_ind.loc[common]
    asset_df = {"BTCUSDT": btc_df.loc[common], "ETHUSDT": eth_df.loc[common]}
    asset_ind = {"BTCUSDT": btc_ind, "ETHUSDT": eth_ind}
    size_per_asset = 0.5
    base_size = float(strategy["risk"].get("position_size_r", 0.5))
    n = len(common)
    all_trades = []
    fold_returns = []
    fold = 0
    cursor = 0
    fold_pos = 0
    # Persistent across folds — rule history is causal & cumulative
    closed_trades_chrono: list[dict] = []
    fold_records = []
    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo
        test_hi = test_lo + test_bars
        if test_hi > n:
            break
        fold += 1
        # Per-rule per-fold preparation
        hmm_p_adv = {a: None for a in ASSETS}
        vol_info = {a: (None, None, None) for a in ASSETS}
        if rule_code == "R5":
            for a in ASSETS:
                tr_df = asset_df[a].iloc[cursor:train_hi]
                tr_ind = asset_ind[a].iloc[cursor:train_hi]
                te_df = asset_df[a].iloc[test_lo:test_hi]
                te_ind = asset_ind[a].iloc[test_lo:test_hi]
                hmm_p_adv[a] = _hmm_p_adverse(a, tr_df, tr_ind, te_df, te_ind,
                                              hmm_cfg, strategy, fee, slippage)
        if rule_code == "R6":
            for a in ASSETS:
                tr_df = asset_df[a].iloc[cursor:train_hi]
                te_df = asset_df[a].iloc[test_lo:test_hi]
                rvol, q25, q75 = _vol_quartile_thresholds(tr_df, te_df,
                                                          ADAPTIVE_THRESHOLDS["R6_vol_window_bars"])
                vol_info[a] = (rvol, q25, q75)
        btc_test = btc_ind.iloc[test_lo:test_hi]
        eth_test = eth_ind.iloc[test_lo:test_hi]
        per_asset_records = {
            "BTCUSDT": btc_test.to_dict("records"),
            "ETHUSDT": eth_test.to_dict("records"),
        }
        equity = 1.0; peak = 1.0; max_dd = 0.0
        positions = {a: None for a in ASSETS}
        fold_trades = []
        # rule triggers tracked at the fold level
        triggers_in_fold = 0
        bars_with_pause = 0

        for i in range(len(btc_test)):
            ts = btc_test.index[i]
            # ---- compute rule-based size_mult at this timestamp ----
            global_mult = FULL   # applied to any new entry on this bar
            rule_triggered = False
            if rule_code == "R1":
                W = ADAPTIVE_THRESHOLDS["R1_pf_window"]
                if len(closed_trades_chrono) >= W:
                    win = closed_trades_chrono[-W:]
                    wins = [t for t in win if t["net_return_pct"] > 0]
                    losses = [t for t in win if t["net_return_pct"] <= 0]
                    pf = (sum(t["net_return_pct"] for t in wins) /
                          abs(sum(t["net_return_pct"] for t in losses))
                          if losses else float("inf"))
                    if pf < ADAPTIVE_THRESHOLDS["R1_pf_threshold"]:
                        global_mult = ZERO
                        rule_triggered = True
            elif rule_code == "R2":
                N = ADAPTIVE_THRESHOLDS["R2_consec_losses"]
                trailing = closed_trades_chrono
                cl = 0
                for t in reversed(trailing):
                    if t["net_return_pct"] <= 0:
                        cl += 1
                    else:
                        break
                if cl >= N:
                    global_mult = HALF
                    rule_triggered = True
            elif rule_code == "R3":
                D = ADAPTIVE_THRESHOLDS["R3_window_days"]
                lo = ts - pd.Timedelta(days=D)
                win = [t for t in closed_trades_chrono
                       if t["exit_ts"] >= lo]
                if win:
                    eq = 1.0
                    for t in win:
                        eq *= 1.0 + t["net_return_pct"]
                    if (eq - 1.0) < ADAPTIVE_THRESHOLDS["R3_return_threshold"]:
                        global_mult = HALF
                        rule_triggered = True
            elif rule_code == "R4":
                W = ADAPTIVE_THRESHOLDS["R4_stop_window"]
                if len(closed_trades_chrono) >= W:
                    win = closed_trades_chrono[-W:]
                    stop_count = sum(1 for t in win if t["exit_reason"] == "stop")
                    if stop_count / len(win) > ADAPTIVE_THRESHOLDS["R4_stop_freq_threshold"]:
                        global_mult = HALF
                        rule_triggered = True
            if rule_triggered:
                triggers_in_fold += 1
                if global_mult == ZERO:
                    bars_with_pause += 1

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
                                         else float(row["close"]) * (1 - slippage))
                        else:
                            exit_fill = (position["stop"] * (1 + slippage)
                                         if reason == "stop"
                                         else float(row["close"]) * (1 + slippage))
                        if position["direction"] == "long":
                            gross = (exit_fill - position["entry"]) / position["entry"]
                        else:
                            gross = (position["entry"] - exit_fill) / position["entry"]
                        eff = base_size * size_per_asset * position.get("size_mult", 1.0)
                        net = (gross - 2 * fee) * eff
                        equity *= 1.0 + net
                        peak = max(peak, equity)
                        max_dd = max(max_dd, (peak - equity) / peak)
                        trade = {
                            "asset": asset, "ret": net,
                            "direction": position["direction"],
                            "gross_return_pct": gross, "net_return_pct": net,
                            "exit_reason": reason, "bars": bars_held,
                            "entry_ts": position["entry_ts"], "exit_ts": ts,
                            "entry_price": position["entry"], "exit_price": exit_fill,
                            "size_mult": position.get("size_mult", 1.0),
                            "_variant": name,
                        }
                        fold_trades.append(trade)
                        closed_trades_chrono.append(trade)
                        positions[asset] = None
                        continue
                if positions[asset] is None:
                    open_count = sum(1 for p in positions.values() if p is not None)
                    if open_count >= max_open:
                        continue
                    setup_l = signals.long_entry(row, strategy)
                    opened_long = False
                    if setup_l:
                        df_l = long_fund[asset]
                        allowed = (bool(df_l.loc[ts]["long_allowed"])
                                   if ts in df_l.index else True)
                        if allowed:
                            # ---- per-rule size_mult ----
                            size_mult = global_mult
                            if rule_code == "R5":
                                p_adv = hmm_p_adv[asset]
                                if p_adv is not None and ts in p_adv.index:
                                    val = p_adv.loc[ts]
                                    if pd.notna(val) and float(val) >= ADAPTIVE_THRESHOLDS["R5_hmm_adverse_threshold"]:
                                        size_mult = HALF
                                        triggers_in_fold += 1
                            elif rule_code == "R6":
                                rvol, q25, q75 = vol_info[asset]
                                if rvol is not None and ts in rvol.index:
                                    val = rvol.loc[ts]
                                    if pd.notna(val):
                                        if q25 is not None and float(val) <= q25:
                                            size_mult = FULL
                                        elif q75 is not None and float(val) >= q75:
                                            size_mult = 0.25
                                            triggers_in_fold += 1
                                        else:
                                            size_mult = HALF
                            if size_mult > 0:
                                entry_fill = float(row["close"]) * (1 + slippage)
                                stop_val = float(signals.initial_stop(row, setup_l, strategy))
                                positions[asset] = {
                                    "asset": asset, "entry": entry_fill,
                                    "direction": "long", "setup": setup_l,
                                    "stop": stop_val, "entry_i": i, "entry_ts": ts,
                                    "size_mult": size_mult,
                                }
                                opened_long = True
                    if (not opened_long) and strategy.get("shorts", {}).get("enabled"):
                        setup_s = signals.short_entry(row, strategy)
                        if setup_s:
                            df_s = short_fund[asset]
                            allowed = (bool(df_s.loc[ts]["long_allowed"])
                                       if ts in df_s.index else True)
                            if allowed:
                                size_mult = global_mult
                                if rule_code == "R5":
                                    p_adv = hmm_p_adv[asset]
                                    if p_adv is not None and ts in p_adv.index:
                                        val = p_adv.loc[ts]
                                        if pd.notna(val) and float(val) >= ADAPTIVE_THRESHOLDS["R5_hmm_adverse_threshold"]:
                                            size_mult = HALF
                                            triggers_in_fold += 1
                                elif rule_code == "R6":
                                    rvol, q25, q75 = vol_info[asset]
                                    if rvol is not None and ts in rvol.index:
                                        val = rvol.loc[ts]
                                        if pd.notna(val):
                                            if q25 is not None and float(val) <= q25:
                                                size_mult = FULL
                                            elif q75 is not None and float(val) >= q75:
                                                size_mult = 0.25
                                                triggers_in_fold += 1
                                            else:
                                                size_mult = HALF
                                if size_mult > 0:
                                    entry_fill = float(row["close"]) * (1 - slippage)
                                    stop_val = float(signals.initial_stop_short(row, setup_s, strategy))
                                    positions[asset] = {
                                        "asset": asset, "entry": entry_fill,
                                        "direction": "short", "setup": setup_s,
                                        "stop": stop_val, "entry_i": i, "entry_ts": ts,
                                        "size_mult": size_mult,
                                    }
        # Close at fold end
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
            eff = base_size * size_per_asset * position.get("size_mult", 1.0)
            net = (gross - 2 * fee) * eff
            equity *= 1.0 + net
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
            trade = {
                "asset": asset, "ret": net, "direction": position["direction"],
                "gross_return_pct": gross, "net_return_pct": net,
                "exit_reason": "end", "bars": bars_held,
                "entry_ts": position["entry_ts"], "exit_ts": btc_test.index[-1],
                "entry_price": position["entry"], "exit_price": exit_fill,
                "size_mult": position.get("size_mult", 1.0),
                "_variant": name,
            }
            fold_trades.append(trade)
            closed_trades_chrono.append(trade)
            positions[asset] = None
        all_trades.extend(fold_trades)
        fold_ret = equity - 1.0
        fold_returns.append(fold_ret)
        if fold_ret > 0:
            fold_pos += 1
        fold_records.append({"fold": fold, "triggers": triggers_in_fold,
                             "bars_with_pause": bars_with_pause})
        cursor += test_bars

    oos = wf._stitch_metrics(all_trades)
    n_trigger = sum(fr["triggers"] for fr in fold_records)
    # Count trades fired during recent ~90d
    last_exit = all_trades[-1]["exit_ts"] if all_trades else None
    recent_trades = []
    if last_exit:
        recent_lo = last_exit - pd.Timedelta(days=90)
        recent_trades = [t for t in all_trades if t["exit_ts"] >= recent_lo]
    rule_active_in_recent = sum(1 for t in recent_trades
                                if abs(t.get("size_mult", 1.0) - 1.0) > 1e-6)
    pf_s = "inf" if oos["profit_factor"] == float("inf") else f"{oos['profit_factor']:.2f}"
    log(f"  -> trades={oos['trades']}  ret={oos['total_return']*100:+.2f}%  "
        f"DD={oos['max_drawdown']*100:.2f}%  PF={pf_s}  "
        f"folds+={fold_pos}/{len(fold_records)}  "
        f"triggers={n_trigger}  recent_modified={rule_active_in_recent}")
    return {
        "name": name, "rule": rule_code, "oos": oos,
        "trades": all_trades, "fold_records": fold_records,
        "fold_pos": fold_pos, "n_folds": len(fold_records),
        "triggers": n_trigger, "recent_modified": rule_active_in_recent,
    }


def _subset_metrics(trades, lo, hi):
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
    ap.add_argument("--hmm-config",
                    default=str(STATE_DIR / "hmm_regime.yaml"))
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    if not hmmr.available():
        log(f"[red]{hmmr.INSTALL_HINT}[/red]")
        return 2

    strategy = yaml.safe_load(open(args.strategy))
    hmm_cfg = yaml.safe_load(open(args.hmm_config))["hmm_regime"]

    log(f"loading BTC + ETH {args.n_months}mo @ 4h …")
    btc = data_mod.resample(data_mod.load_klines("BTCUSDT", n_months=args.n_months), "4h")
    eth = data_mod.resample(data_mod.load_klines("ETHUSDT", n_months=args.n_months), "4h")
    common = btc.index.intersection(eth.index)
    btc = btc.loc[common]; eth = eth.loc[common]
    log(f"4h bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    log("building funding gates …")
    long_fund = {
        "BTCUSDT": _build_funding(common, "BTCUSDT", args.n_months, "long"),
        "ETHUSDT": _build_funding(common, "ETHUSDT", args.n_months, "long"),
    }
    short_fund = {
        "BTCUSDT": _build_funding(common, "BTCUSDT", args.n_months, "short"),
        "ETHUSDT": _build_funding(common, "ETHUSDT", args.n_months, "short"),
    }

    rule_defs = [
        ("baseline",        "baseline"),
        ("R1_pf_pause",     "R1"),
        ("R2_consec_loss",  "R2"),
        ("R3_30d_return",   "R3"),
        ("R4_stop_freq",    "R4"),
        ("R5_hmm_adverse",  "R5"),
        ("R6_vol_quartile", "R6"),
    ]
    results = []
    for name, code in rule_defs:
        r = _run_rule(name, code, btc, eth, strategy, long_fund, short_fund,
                      args.train_bars, args.test_bars, args.embargo_bars,
                      args.fee, args.slippage, hmm_cfg=hmm_cfg)
        results.append(r)

    # Subset windows
    for r in results:
        if not r["trades"]:
            r["subsets"] = {}; continue
        last_exit = r["trades"][-1]["exit_ts"]
        r["subsets"] = {mb: _subset_metrics(r["trades"],
                                             last_exit - pd.DateOffset(months=mb),
                                             last_exit)
                        for mb in (24, 12, 6, 3)}

    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"adaptive_regime_response_{ts_str}.csv"
    md_path = out_dir / f"adaptive_regime_response_{ts_str}.md"
    trades_path = out_dir / f"adaptive_regime_response_trades_{ts_str}.csv"

    rows = []
    for r in results:
        oos = r["oos"]
        rows.append({
            "variant": r["name"],
            "rule": r["rule"],
            "scope": "48mo_wf",
            "trades": oos["trades"],
            "total_return": oos["total_return"],
            "max_drawdown": oos["max_drawdown"],
            "profit_factor": (oos["profit_factor"]
                               if oos["profit_factor"] != float("inf") else 9999.0),
            "win_rate": oos["win_rate"],
            "folds_positive": f"{r['fold_pos']}/{r['n_folds']}",
            "triggers": r["triggers"],
            "recent_modified": r["recent_modified"],
        })
        for mb, m in r["subsets"].items():
            if m is None:
                continue
            rows.append({
                "variant": r["name"],
                "rule": r["rule"],
                "scope": f"last_{mb}mo",
                "trades": m["trades"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "profit_factor": m["profit_factor"],
                "win_rate": m["win_rate"],
                "folds_positive": "",
                "triggers": "",
                "recent_modified": "",
            })
    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote {csv_path}")

    md = [
        f"# Adaptive risk-layer rules — {ts_str}",
        "",
        f"- universe: BTC/USDT + ETH/USDT (parallel, 4h decision)",
        f"- strategy: `{args.strategy}`",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / "
        f"embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "Rules tested:",
        "- R1: pause (size=0) when rolling 10-trade PF < 1.0",
        "- R2: half-size after 3 consecutive losses",
        "- R3: half-size when rolling 30-day return < -3%",
        "- R4: half-size when stop-exit frequency > 80% over last 5 trades",
        "- R5: half-size when HMM adverse probability > 0.7 at entry",
        "- R6: volatility-quartile sizing (low=1.0, mid=0.5, high=0.25)",
        "",
        "## 48mo OOS",
        "",
        "| variant | n | ret | DD | PF | win | folds+ | triggers | recent_modified |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        oos = r["oos"]
        pf = oos["profit_factor"]
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        md.append(f"| {r['name']} | {oos['trades']} | "
                  f"{oos['total_return']*100:+.2f}% | "
                  f"{oos['max_drawdown']*100:.2f}% | {pf_s} | "
                  f"{oos['win_rate']*100:.1f}% | "
                  f"{r['fold_pos']}/{r['n_folds']} | "
                  f"{r['triggers']} | {r['recent_modified']} |")

    md.extend(["", "## Trailing windows", "",
               "| variant | scope | n | ret | DD | PF | win |",
               "|---|---|---:|---:|---:|---:|---:|"])
    for r in results:
        for mb in (24, 12, 6, 3):
            m = r["subsets"].get(mb)
            if m is None:
                continue
            pf = m["profit_factor"]
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            md.append(f"| {r['name']} | last_{mb}mo | {m['trades']} | "
                      f"{m['total_return']*100:+.2f}% | "
                      f"{m['max_drawdown']*100:.2f}% | {pf_s} | "
                      f"{m['win_rate']*100:.1f}% |")
    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote {md_path}")

    base_cols = ["_variant", "asset", "direction", "entry_ts", "exit_ts",
                 "entry_price", "exit_price", "gross_return_pct", "net_return_pct",
                 "exit_reason", "bars", "size_mult"]
    with open(trades_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(base_cols)
        for r in results:
            for t in r["trades"]:
                row = []
                for c in base_cols:
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
