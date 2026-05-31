#!/usr/bin/env python3
"""Phase 2 — rolling decay diagnostic for the adopted live candidate.

Generates the full per-trade sequence over 48 months for the adopted
``state/live_multiasset_long_short_funding.yaml`` candidate, using
the SAME live-replay engine (closed-bar entries; same costs; same
funding gate). Then computes rolling-window metrics by trade count
and by calendar days, saves them to CSV, and answers:

  - How often historically does a 90-day window go below -5%?
  - How often below -9.61%? (matching the user's 3mo replay)
  - Is the recent 3mo within the historical distribution?
  - Would the existing monitor_strategy_decay defaults have caught
    this period?
  - What threshold catches the recent period but does not produce
    false positives elsewhere?

Outputs:
  - results/rolling_decay_trades_<ts>.csv     (raw closed-trade seq)
  - results/rolling_decay_metrics_<ts>.csv    (rolling metrics rows)
  - parent agent writes research/rolling_decay_report.md

Hard rules respected:
  - No live config / strategy changes.
  - Same SuperTrend(10,3), funding gate (block long >= 95, short <= 5).
  - In-sample replay over 48mo: we are NOT tuning anything, just
    computing the empirical distribution of windowed metrics on the
    live candidate as-is.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import STATE_DIR, log
from hermes_trading import data as data_mod
from hermes_trading import funding as funding_mod
from hermes_trading import signals

ASSETS = ("BTCUSDT", "ETHUSDT")
ASSET_LABEL = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}


def _build_funding(price_index, symbol, n_months, block_long=95.0,
                   block_short=5.0, window=180):
    f = funding_mod.load_funding(symbol, n_months=n_months)
    aligned = funding_mod.align_to_index(f, price_index)
    pct = funding_mod.rolling_percentile(aligned, window=window)
    long_allowed = (pct < block_long) | pct.isna()
    short_allowed = (pct > block_short) | pct.isna()
    return pd.DataFrame({
        "funding_rate": aligned.astype(float),
        "funding_percentile": pct.astype(float),
        "long_allowed": long_allowed.astype(bool),
        "short_allowed": short_allowed.astype(bool),
    }, index=price_index)


def _replay_full(asset_dfs, strategy, fund_by_asset, fee, slippage,
                 size_per_asset, max_open):
    ind_by_asset = {a: signals.compute_indicators(df, strategy)
                    for a, df in asset_dfs.items()}
    common = ind_by_asset[ASSETS[0]].index
    for a in ASSETS[1:]:
        common = common.intersection(ind_by_asset[a].index)
    for a in ASSETS:
        ind_by_asset[a] = ind_by_asset[a].loc[common]
    pos_idx = {a: {ts: i for i, ts in enumerate(ind_by_asset[a].index)}
               for a in ASSETS}
    positions = {a: None for a in ASSETS}
    trades = []
    base_size = float(strategy["risk"].get("position_size_r", 0.5))
    for ts in common:
        for asset in ASSETS:
            ind = ind_by_asset[asset]
            i = pos_idx[asset].get(ts)
            if i is None or i < 1:
                continue
            display_row = ind.iloc[i].to_dict()
            signal_row = ind.iloc[i - 1].to_dict()
            last = float(display_row["close"])
            position = positions[asset]
            if position is not None:
                bars_held = i - position["entry_i"]
                if position["direction"] == "long":
                    reason = signals.long_exit(signal_row, position, strategy, bars_held)
                else:
                    reason = signals.short_exit(signal_row, position, strategy, bars_held)
                if reason is None:
                    if position["direction"] == "long":
                        dlow = display_row.get("low")
                        if dlow is not None and not pd.isna(dlow) and float(dlow) <= position["stop"]:
                            reason = "stop"
                    else:
                        dhigh = display_row.get("high")
                        if dhigh is not None and not pd.isna(dhigh) and float(dhigh) >= position["stop"]:
                            reason = "stop"
                if reason:
                    if position["direction"] == "long":
                        exit_fill = (position["stop"] * (1 - slippage)
                                     if reason == "stop"
                                     else last * (1 - slippage))
                        gross = (exit_fill - position["entry"]) / position["entry"]
                    else:
                        exit_fill = (position["stop"] * (1 + slippage)
                                     if reason == "stop"
                                     else last * (1 + slippage))
                        gross = (position["entry"] - exit_fill) / position["entry"]
                    eff = base_size * size_per_asset
                    net = (gross - 2 * fee) * eff
                    trades.append({
                        "asset": ASSET_LABEL[asset],
                        "direction": position["direction"],
                        "entry_time": position["entry_ts"],
                        "exit_time": ts,
                        "entry_price": position["entry"],
                        "exit_price": exit_fill,
                        "gross_return_pct": gross,
                        "net_return_pct": net,
                        "exit_reason": reason,
                        "bars_held": bars_held,
                        "setup": position["setup"],
                    })
                    positions[asset] = None
                    continue
            if positions[asset] is None:
                open_count = sum(1 for p in positions.values() if p is not None)
                if open_count >= max_open:
                    continue
                setup_l = signals.long_entry(signal_row, strategy)
                opened_long = False
                if setup_l:
                    funding_row = (fund_by_asset[asset].loc[ts]
                                   if ts in fund_by_asset[asset].index else None)
                    if funding_row is None or bool(funding_row["long_allowed"]):
                        entry_fill = last * (1 + slippage)
                        stop_val = float(signals.initial_stop(signal_row, setup_l, strategy))
                        positions[asset] = {
                            "asset": asset, "entry": entry_fill,
                            "direction": "long", "setup": setup_l,
                            "stop": stop_val, "entry_i": i, "entry_ts": ts,
                        }
                        opened_long = True
                if (not opened_long) and strategy.get("shorts", {}).get("enabled"):
                    setup_s = signals.short_entry(signal_row, strategy)
                    if setup_s:
                        funding_row = (fund_by_asset[asset].loc[ts]
                                       if ts in fund_by_asset[asset].index else None)
                        if funding_row is None or bool(funding_row["short_allowed"]):
                            entry_fill = last * (1 - slippage)
                            stop_val = float(signals.initial_stop_short(signal_row, setup_s, strategy))
                            positions[asset] = {
                                "asset": asset, "entry": entry_fill,
                                "direction": "short", "setup": setup_s,
                                "stop": stop_val, "entry_i": i, "entry_ts": ts,
                            }
    # Close open
    for asset in ASSETS:
        position = positions[asset]
        if position is None:
            continue
        ind = ind_by_asset[asset]
        last_ts = ind.index[-1]
        last_row = ind.iloc[-1]
        bars_held = len(ind) - 1 - position["entry_i"]
        last = float(last_row["close"])
        if position["direction"] == "long":
            exit_fill = last * (1 - slippage)
            gross = (exit_fill - position["entry"]) / position["entry"]
        else:
            exit_fill = last * (1 + slippage)
            gross = (position["entry"] - exit_fill) / position["entry"]
        eff = base_size * size_per_asset
        net = (gross - 2 * fee) * eff
        trades.append({
            "asset": ASSET_LABEL[asset],
            "direction": position["direction"],
            "entry_time": position["entry_ts"],
            "exit_time": last_ts,
            "entry_price": position["entry"],
            "exit_price": exit_fill,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "exit_reason": "end",
            "bars_held": bars_held,
            "setup": position["setup"],
        })
    return trades


# ---------- rolling-window metrics ------------------------------------------

def _trade_windowed_metrics(trades_sorted, window_size):
    rows = []
    for i in range(len(trades_sorted) - window_size + 1):
        win = trades_sorted[i:i + window_size]
        rets = [t["net_return_pct"] for t in win]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        win_rate = len(wins) / len(win)
        pf = (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf")
        equity = 1.0; peak = 1.0; max_dd = 0.0
        for r in rets:
            equity *= 1.0 + r
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak)
        total_ret = equity - 1.0
        # trailing consecutive losses
        cl = 0
        for r in reversed(rets):
            if r <= 0:
                cl += 1
            else:
                break
        # max consecutive losses in window
        max_cl = 0; cur = 0
        for r in rets:
            if r <= 0:
                cur += 1
                max_cl = max(max_cl, cur)
            else:
                cur = 0
        # stop-exit frequency
        stop_count = sum(1 for t in win if t["exit_reason"] == "stop")
        stop_freq = stop_count / len(win)
        rows.append({
            "window_type": f"trade_{window_size}",
            "anchor_time": win[-1]["exit_time"],
            "window_start": win[0]["entry_time"],
            "window_end": win[-1]["exit_time"],
            "trades": len(win),
            "total_return_pct": total_ret,
            "max_drawdown_pct": max_dd,
            "profit_factor": (pf if pf != float("inf") else 9999.0),
            "win_rate": win_rate,
            "trailing_consec_losses": cl,
            "max_consec_losses_in_window": max_cl,
            "stop_exit_freq": stop_freq,
            "avg_trade_return": float(np.mean(rets)),
        })
    return rows


def _day_windowed_metrics(trades_sorted, window_days, step_days=7):
    rows = []
    if not trades_sorted:
        return rows
    start = trades_sorted[0]["entry_time"]
    end = trades_sorted[-1]["exit_time"]
    cur = start
    while cur <= end:
        win_lo = cur
        win_hi = cur + pd.Timedelta(days=window_days)
        win = [t for t in trades_sorted
               if t["exit_time"] >= win_lo and t["exit_time"] <= win_hi]
        if win:
            rets = [t["net_return_pct"] for t in win]
            wins = [r for r in rets if r > 0]
            losses = [r for r in rets if r <= 0]
            win_rate = len(wins) / len(win)
            pf = (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf")
            equity = 1.0; peak = 1.0; max_dd = 0.0
            for r in rets:
                equity *= 1.0 + r
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
            total_ret = equity - 1.0
            cl = 0
            for r in reversed(rets):
                if r <= 0:
                    cl += 1
                else:
                    break
            max_cl = 0; cur_run = 0
            for r in rets:
                if r <= 0:
                    cur_run += 1
                    max_cl = max(max_cl, cur_run)
                else:
                    cur_run = 0
            stop_count = sum(1 for t in win if t["exit_reason"] == "stop")
            stop_freq = stop_count / len(win)
            rows.append({
                "window_type": f"day_{window_days}",
                "anchor_time": win_hi,
                "window_start": win_lo,
                "window_end": win_hi,
                "trades": len(win),
                "total_return_pct": total_ret,
                "max_drawdown_pct": max_dd,
                "profit_factor": (pf if pf != float("inf") else 9999.0),
                "win_rate": win_rate,
                "trailing_consec_losses": cl,
                "max_consec_losses_in_window": max_cl,
                "stop_exit_freq": stop_freq,
                "avg_trade_return": float(np.mean(rets)),
            })
        cur = cur + pd.Timedelta(days=step_days)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config",
                    default=str(STATE_DIR / "live_multiasset_long_short_funding.yaml"))
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    strategy_path = ROOT / cfg["strategy"]
    strategy = yaml.safe_load(open(strategy_path))
    max_open = int(cfg.get("max_open_positions", 2))
    size_per_asset = float(cfg.get("size_per_asset", 0.5))

    log(f"loading BTC + ETH {args.n_months}mo @ 4h …")
    btc = data_mod.resample(data_mod.load_klines("BTCUSDT", n_months=args.n_months), "4h")
    eth = data_mod.resample(data_mod.load_klines("ETHUSDT", n_months=args.n_months), "4h")
    common = btc.index.intersection(eth.index)
    btc = btc.loc[common]; eth = eth.loc[common]
    log(f"bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")
    log("loading funding …")
    fund = {"BTCUSDT": _build_funding(common, "BTCUSDT", args.n_months),
            "ETHUSDT": _build_funding(common, "ETHUSDT", args.n_months)}

    log("replaying live engine over full window …")
    trades = _replay_full({"BTCUSDT": btc, "ETHUSDT": eth}, strategy, fund,
                          args.fee, args.slippage, size_per_asset, max_open)
    trades_sorted = sorted(trades, key=lambda t: t["exit_time"])
    log(f"trades closed: {len(trades_sorted)}")

    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    trades_path = out_dir / f"rolling_decay_trades_{ts_str}.csv"
    metrics_path = out_dir / f"rolling_decay_metrics_{ts_str}.csv"

    # Write trades CSV (sorted by exit_time, for downstream rolling analysis)
    with open(trades_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "asset", "direction", "setup",
            "entry_time", "exit_time", "bars_held",
            "entry_price", "exit_price",
            "gross_return_pct", "net_return_pct", "exit_reason",
        ])
        w.writeheader()
        for t in trades_sorted:
            row = dict(t)
            row["entry_time"] = row["entry_time"].isoformat() if hasattr(row["entry_time"], "isoformat") else str(row["entry_time"])
            row["exit_time"] = row["exit_time"].isoformat() if hasattr(row["exit_time"], "isoformat") else str(row["exit_time"])
            w.writerow({k: row.get(k) for k in [
                "asset", "direction", "setup",
                "entry_time", "exit_time", "bars_held",
                "entry_price", "exit_price",
                "gross_return_pct", "net_return_pct", "exit_reason",
            ]})
    log(f"wrote trades -> {trades_path}")

    # Compute rolling windows
    all_rows = []
    for n in (10, 25, 50):
        all_rows.extend(_trade_windowed_metrics(trades_sorted, n))
    for days in (30, 90, 180):
        all_rows.extend(_day_windowed_metrics(trades_sorted, days))
    # Sort by window_type then anchor_time
    all_rows.sort(key=lambda r: (r["window_type"], r["anchor_time"]))

    with open(metrics_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "window_type", "anchor_time", "window_start", "window_end",
            "trades", "total_return_pct", "max_drawdown_pct",
            "profit_factor", "win_rate",
            "trailing_consec_losses", "max_consec_losses_in_window",
            "stop_exit_freq", "avg_trade_return",
        ])
        w.writeheader()
        for r in all_rows:
            row = dict(r)
            row["anchor_time"] = row["anchor_time"].isoformat() if hasattr(row["anchor_time"], "isoformat") else str(row["anchor_time"])
            row["window_start"] = row["window_start"].isoformat() if hasattr(row["window_start"], "isoformat") else str(row["window_start"])
            row["window_end"] = row["window_end"].isoformat() if hasattr(row["window_end"], "isoformat") else str(row["window_end"])
            w.writerow(row)
    log(f"wrote rolling metrics ({len(all_rows)} rows) -> {metrics_path}")

    # ---- Aggregate analysis / thresholds ----
    log("------------------------------------------------------------")
    # Overall 48mo metrics
    rets = [t["net_return_pct"] for t in trades_sorted]
    equity = 1.0; peak = 1.0; max_dd = 0.0
    for r in rets:
        equity *= 1.0 + r
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pf_total = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    log(f"48mo TOTAL: trades={len(trades_sorted)}  ret={(equity-1)*100:+.2f}%  "
        f"DD={max_dd*100:.2f}%  PF={pf_total:.2f}  win={len(wins)/len(rets)*100:.1f}%")

    # 90-day windows: distribution
    day90 = [r for r in all_rows if r["window_type"] == "day_90"]
    if day90:
        rets_arr = np.array([r["total_return_pct"] for r in day90])
        pct_below_neg5 = (rets_arr < -0.05).mean()
        pct_below_neg9_61 = (rets_arr < -0.0961).mean()
        log(f"90-day windows (n={len(day90)}):")
        log(f"  mean ret = {rets_arr.mean()*100:+.2f}%   median = {np.median(rets_arr)*100:+.2f}%")
        log(f"  p10 = {np.percentile(rets_arr, 10)*100:+.2f}%  p25 = {np.percentile(rets_arr, 25)*100:+.2f}%  p75 = {np.percentile(rets_arr, 75)*100:+.2f}%  p90 = {np.percentile(rets_arr, 90)*100:+.2f}%")
        log(f"  fraction < -5%:    {pct_below_neg5*100:.1f}%")
        log(f"  fraction < -9.61%: {pct_below_neg9_61*100:.1f}%")

    # 10-trade windows: PF
    t10 = [r for r in all_rows if r["window_type"] == "trade_10"]
    if t10:
        pf_arr = np.array([r["profit_factor"] for r in t10])
        pct_pf_lt_1 = (pf_arr < 1.0).mean()
        pct_pf_lt_12 = (pf_arr < 1.2).mean()
        log(f"10-trade windows (n={len(t10)}):")
        log(f"  PF p10/p25/p50/p75/p90 = "
            f"{np.percentile(pf_arr, 10):.2f}/{np.percentile(pf_arr, 25):.2f}/"
            f"{np.percentile(pf_arr, 50):.2f}/{np.percentile(pf_arr, 75):.2f}/"
            f"{np.percentile(pf_arr, 90):.2f}")
        log(f"  fraction PF < 1.0: {pct_pf_lt_1*100:.1f}%")
        log(f"  fraction PF < 1.2: {pct_pf_lt_12*100:.1f}%")

    # Find recent-3mo span: the last 90 days at the tail
    last_exit = trades_sorted[-1]["exit_time"]
    recent_lo = last_exit - pd.Timedelta(days=90)
    recent = [t for t in trades_sorted if t["exit_time"] >= recent_lo]
    rets_r = [t["net_return_pct"] for t in recent]
    if rets_r:
        equity = 1.0; peak = 1.0; max_dd_r = 0.0
        for r in rets_r:
            equity *= 1.0 + r
            peak = max(peak, equity)
            max_dd_r = max(max_dd_r, (peak - equity) / peak)
        wins_r = [r for r in rets_r if r > 0]
        losses_r = [r for r in rets_r if r <= 0]
        pf_r = (sum(wins_r) / abs(sum(losses_r))) if losses_r else float("inf")
        log(f"recent ~90 days (n={len(rets_r)}): ret={(equity-1)*100:+.2f}%  "
            f"DD={max_dd_r*100:.2f}%  PF={pf_r:.2f}  win={len(wins_r)/len(rets_r)*100:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
