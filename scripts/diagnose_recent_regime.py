#!/usr/bin/env python3
"""Diagnose the recent 3-month failure of the adopted live candidate
(``state/live_multiasset_long_short_funding.yaml``).

Reuses ``hermes_trading.signals`` and ``hermes_trading.data`` /
``hermes_trading.funding`` to replay the SAME logic the live worker
runs, in a single-pass synthesis that also produces rich per-trade
context:

  * funding rate + rolling percentile at entry
  * 1h and 1d SuperTrend direction at entry (agreement check)
  * realised volatility band (quartile thresholds from prior 6 months,
    computed before the 3mo replay window starts — strictly out of
    sample)
  * distance from entry close to SuperTrend line at entry
  * bar-after entry overshoot (whether the next bar traded outside
    the slippage band against the position)

Outputs:
  * results/recent_regime_failure_trades_<ts>.csv
  * (parent agent writes research/recent_regime_failure_report.md)

Hard rules respected:
  * No live config changes.
  * Same SuperTrend(10, 3), same funding rules (block long >= 95,
    block short <= 5), same costs (fee 10 bps/side, slippage 5 bps).
  * Strict OOS context: vol quartile thresholds are fit on the
    6 months BEFORE the 3mo replay window.
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
from hermes_trading import data as data_mod
from hermes_trading import funding as funding_mod
from hermes_trading import signals

ASSETS = ("BTCUSDT", "ETHUSDT")
ASSET_LABEL = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT"}
ASSET_FROM_LABEL = {"BTC/USDT": "BTCUSDT", "ETH/USDT": "ETHUSDT"}


def _build_funding_decision(price_index: pd.DatetimeIndex, symbol: str,
                            n_months: int,
                            block_long: float = 95.0,
                            block_short: float = 5.0,
                            window: int = 180) -> pd.DataFrame:
    """Returns DataFrame(funding_rate, funding_percentile, long_allowed,
    short_allowed) for every bar in ``price_index``. Causal — same
    rolling-percentile semantics as the live overlay."""
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


def _replay_window(asset_dfs: dict[str, pd.DataFrame],
                   strategy: dict,
                   funding_by_asset: dict[str, pd.DataFrame],
                   start: pd.Timestamp,
                   end: pd.Timestamp,
                   fee: float, slippage: float,
                   size_per_asset: float, max_open: int) -> list[dict]:
    """Run the live state machine across the [start, end] window
    using the most-recently-CLOSED-bar entry semantics (Issue #24
    parity). Returns the list of closed-trade dicts. Trades that
    open inside the window count as window trades regardless of
    where they exit."""
    ind_by_asset = {a: signals.compute_indicators(df, strategy)
                    for a, df in asset_dfs.items()}
    common = ind_by_asset[ASSETS[0]].index
    for a in ASSETS[1:]:
        common = common.intersection(ind_by_asset[a].index)
    # We process every bar in [start, end] but indicators must already
    # have warmed up. Slice indicators to common.
    for a in ASSETS:
        ind_by_asset[a] = ind_by_asset[a].loc[common]

    # Walk every timestamp; only act on bars within [start, end].
    timeline = common
    positions: dict[str, dict | None] = {a: None for a in ASSETS}
    trades: list[dict] = []
    base_size = float(strategy["risk"].get("position_size_r", 0.5))

    pos_idx = {a: {ts: i for i, ts in enumerate(ind_by_asset[a].index)}
               for a in ASSETS}

    for ts in timeline:
        in_window = (ts >= start) and (ts <= end)
        for asset in ASSETS:
            ind = ind_by_asset[asset]
            i = pos_idx[asset].get(ts)
            if i is None or i < 1:
                continue
            display_row = ind.iloc[i].to_dict()
            display_row["ts"] = ind.index[i]
            signal_row = ind.iloc[i - 1].to_dict()
            signal_row["ts"] = ind.index[i - 1]
            position = positions[asset]
            last = float(display_row["close"])

            # ---- exit ----
            if position is not None:
                bars_held = i - position["entry_i"]
                if position["direction"] == "long":
                    reason = signals.long_exit(signal_row, position, strategy, bars_held)
                else:
                    reason = signals.short_exit(signal_row, position, strategy, bars_held)
                if reason is None:
                    # intra-bar stop
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
                    effective_size = base_size * size_per_asset
                    net = (gross - 2 * fee) * effective_size
                    trade = {
                        "asset": ASSET_LABEL[asset],
                        "direction": position["direction"],
                        "entry_time": position["entry_ts"],
                        "exit_time": ts,
                        "entry_price": position["entry"],
                        "exit_price": exit_fill,
                        "stop_price": position["initial_stop"],
                        "final_stop_price": position["stop"],
                        "setup": position["setup"],
                        "exit_reason": reason,
                        "bars_held": bars_held,
                        "gross_return_pct": gross,
                        "net_return_pct": net,
                        "context": position.get("context", {}),
                        "in_window": position.get("in_window", False),
                    }
                    trades.append(trade)
                    positions[asset] = None
                    continue

            # ---- entry ----
            if positions[asset] is None and in_window:
                open_count = sum(1 for p in positions.values() if p is not None)
                if open_count >= max_open:
                    continue

                setup_l = signals.long_entry(signal_row, strategy)
                opened_long = False
                if setup_l:
                    funding_row = funding_by_asset[asset].loc[ts] if ts in funding_by_asset[asset].index else None
                    if funding_row is None or bool(funding_row["long_allowed"]):
                        entry_fill = last * (1 + slippage)
                        stop_val = float(signals.initial_stop(signal_row, setup_l, strategy))
                        ctx = _build_entry_context(asset, signal_row, display_row,
                                                    funding_row)
                        positions[asset] = {
                            "asset": asset,
                            "entry": entry_fill,
                            "direction": "long",
                            "setup": setup_l,
                            "stop": stop_val,
                            "initial_stop": stop_val,
                            "entry_i": i,
                            "entry_ts": ts,
                            "context": ctx,
                            "in_window": True,
                        }
                        opened_long = True
                if (not opened_long) and strategy.get("shorts", {}).get("enabled"):
                    setup_s = signals.short_entry(signal_row, strategy)
                    if setup_s:
                        funding_row = funding_by_asset[asset].loc[ts] if ts in funding_by_asset[asset].index else None
                        if funding_row is None or bool(funding_row["short_allowed"]):
                            entry_fill = last * (1 - slippage)
                            stop_val = float(signals.initial_stop_short(signal_row, setup_s, strategy))
                            ctx = _build_entry_context(asset, signal_row, display_row,
                                                        funding_row)
                            positions[asset] = {
                                "asset": asset,
                                "entry": entry_fill,
                                "direction": "short",
                                "setup": setup_s,
                                "stop": stop_val,
                                "initial_stop": stop_val,
                                "entry_i": i,
                                "entry_ts": ts,
                                "context": ctx,
                                "in_window": True,
                            }
    # Close any open positions at end of window.
    for asset in ASSETS:
        position = positions[asset]
        if position is None or not position.get("in_window"):
            continue
        # Use the last bar of the window
        ind = ind_by_asset[asset]
        # find last bar <= end
        in_w = ind.index[ind.index <= end]
        if len(in_w) == 0:
            continue
        last_ts = in_w[-1]
        last_row = ind.loc[last_ts]
        bars_held = pos_idx[asset][last_ts] - position["entry_i"]
        last = float(last_row["close"])
        if position["direction"] == "long":
            exit_fill = last * (1 - slippage)
            gross = (exit_fill - position["entry"]) / position["entry"]
        else:
            exit_fill = last * (1 + slippage)
            gross = (position["entry"] - exit_fill) / position["entry"]
        effective_size = base_size * size_per_asset
        net = (gross - 2 * fee) * effective_size
        trades.append({
            "asset": ASSET_LABEL[asset],
            "direction": position["direction"],
            "entry_time": position["entry_ts"],
            "exit_time": last_ts,
            "entry_price": position["entry"],
            "exit_price": exit_fill,
            "stop_price": position["initial_stop"],
            "final_stop_price": position["stop"],
            "setup": position["setup"],
            "exit_reason": "end_of_window",
            "bars_held": bars_held,
            "gross_return_pct": gross,
            "net_return_pct": net,
            "context": position.get("context", {}),
            "in_window": True,
        })
    return trades


def _build_entry_context(asset: str, signal_row: dict, display_row: dict,
                         funding_row) -> dict:
    """Snapshot SuperTrend + funding state at entry (signal-row driven,
    matching live worker semantics). Higher-timeframe and vol context
    is added in a separate enrichment pass after the replay completes."""
    st_dir = signal_row.get("supertrend_direction")
    st_line = signal_row.get("supertrend_line")
    close_prev = signal_row.get("close")
    dist_pct = None
    if (st_line is not None and pd.notna(st_line) and close_prev is not None and
            pd.notna(close_prev) and float(close_prev) > 0):
        dist_pct = (float(close_prev) - float(st_line)) / float(close_prev)
    return {
        "supertrend_direction_4h": (int(st_dir) if st_dir is not None and pd.notna(st_dir) else None),
        "supertrend_line_4h": float(st_line) if st_line is not None and pd.notna(st_line) else None,
        "close_prev_4h": float(close_prev) if close_prev is not None and pd.notna(close_prev) else None,
        "supertrend_distance_pct_4h": dist_pct,
        "funding_rate": (None if funding_row is None
                         else (None if pd.isna(funding_row["funding_rate"])
                               else float(funding_row["funding_rate"]))),
        "funding_percentile": (None if funding_row is None
                               else (None if pd.isna(funding_row["funding_percentile"])
                                     else float(funding_row["funding_percentile"]))),
    }


def _enrich_with_higher_tf(trades: list[dict],
                           hourly_dirs: dict[str, pd.Series],
                           daily_dirs: dict[str, pd.Series],
                           rvol_test: dict[str, pd.Series],
                           q25_by_asset: dict[str, float],
                           q75_by_asset: dict[str, float],
                           btc_4h: pd.DataFrame, eth_4h: pd.DataFrame) -> None:
    """In-place: enrich each trade dict with multi-TF agreement and vol
    band tags."""
    asset_4h = {"BTCUSDT": btc_4h, "ETHUSDT": eth_4h}
    for t in trades:
        a = ASSET_FROM_LABEL[t["asset"]]
        ts = t["entry_time"]
        # Most recent close prior to ts.
        h_idx = hourly_dirs[a].index.asof(ts)
        h_dir = (int(hourly_dirs[a].loc[h_idx])
                 if h_idx is not pd.NaT and pd.notna(hourly_dirs[a].loc[h_idx])
                 else None)
        d_idx = daily_dirs[a].index.asof(ts)
        d_dir = (int(daily_dirs[a].loc[d_idx])
                 if d_idx is not pd.NaT and pd.notna(daily_dirs[a].loc[d_idx])
                 else None)
        # vol band
        vol = rvol_test[a].get(ts) if ts in rvol_test[a].index else None
        q25 = q25_by_asset[a]; q75 = q75_by_asset[a]
        if vol is None or pd.isna(vol):
            band = "n/a"
            vol_val = None
        else:
            vol_val = float(vol)
            if vol_val <= q25:
                band = "low"
            elif vol_val >= q75:
                band = "high"
            else:
                band = "mid"
        # next-bar overshoot vs slippage band
        nbar_overshoot_pct = None
        df4h = asset_4h[a]
        try:
            i = df4h.index.get_loc(ts)
            if i + 1 < len(df4h):
                next_row = df4h.iloc[i + 1]
                entry_p = t["entry_price"]
                if t["direction"] == "long":
                    # Adverse = price drops below entry × (1 - slippage)
                    overshoot = (entry_p - float(next_row["low"])) / entry_p
                else:
                    overshoot = (float(next_row["high"]) - entry_p) / entry_p
                nbar_overshoot_pct = float(overshoot)
        except KeyError:
            pass

        ctx = t.setdefault("context", {})
        ctx["supertrend_direction_1h"] = h_dir
        ctx["supertrend_direction_1d"] = d_dir
        ctx["realized_vol_24"] = vol_val
        ctx["realized_vol_band"] = band
        ctx["vol_q25"] = q25
        ctx["vol_q75"] = q75
        # Agreement: does 1h agree with 4h direction at entry?
        cur_4h = ctx.get("supertrend_direction_4h")
        if h_dir is not None and cur_4h is not None:
            ctx["agree_1h_4h"] = (h_dir == cur_4h)
        else:
            ctx["agree_1h_4h"] = None
        if d_dir is not None and cur_4h is not None:
            ctx["agree_1d_4h"] = (d_dir == cur_4h)
        else:
            ctx["agree_1d_4h"] = None
        ctx["next_bar_adverse_overshoot_pct"] = nbar_overshoot_pct


def _supertrend_direction_series(df: pd.DataFrame, period: int = 10,
                                  mult: float = 3.0) -> pd.Series:
    _, d = signals.supertrend(df, period=period, multiplier=mult)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config",
                    default=str(STATE_DIR / "live_multiasset_long_short_funding.yaml"))
    ap.add_argument("--n-months", type=int, default=4,
                    help="months of 4h history to load (need 3mo of replay "
                         "+ warmup). Defaults to 4 — most recent 3 act as "
                         "the diagnostic window; the 1mo before is warmup.")
    ap.add_argument("--window-months", type=int, default=3,
                    help="size of the diagnostic window in months.")
    ap.add_argument("--vol-baseline-months", type=int, default=6,
                    help="months of pre-window history to fit vol "
                         "quartile thresholds.")
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    strategy_path = ROOT / cfg["strategy"]
    strategy = yaml.safe_load(open(strategy_path))
    max_open = int(cfg.get("max_open_positions", 2))
    size_per_asset = float(cfg.get("size_per_asset", 0.5))

    n_load = args.n_months + args.vol_baseline_months + 2
    log(f"loading BTC + ETH {n_load}mo (4h, 1h, 1d) …")

    btc_1m = data_mod.load_klines("BTCUSDT", n_months=n_load)
    eth_1m = data_mod.load_klines("ETHUSDT", n_months=n_load)
    btc_4h = data_mod.resample(btc_1m, "4h")
    eth_4h = data_mod.resample(eth_1m, "4h")
    btc_1h = data_mod.resample(btc_1m, "1h")
    eth_1h = data_mod.resample(eth_1m, "1h")
    btc_1d = data_mod.resample(btc_1m, "1d")
    eth_1d = data_mod.resample(eth_1m, "1d")
    # Align to common 4h timeline.
    common_4h = btc_4h.index.intersection(eth_4h.index)
    btc_4h = btc_4h.loc[common_4h]; eth_4h = eth_4h.loc[common_4h]
    log(f"4h bars: {len(common_4h)}  span: {common_4h[0].date()} -> {common_4h[-1].date()}")

    # Build SuperTrend direction series at 1h and 1d (used for agreement check)
    h_dirs = {"BTCUSDT": _supertrend_direction_series(btc_1h),
              "ETHUSDT": _supertrend_direction_series(eth_1h)}
    d_dirs = {"BTCUSDT": _supertrend_direction_series(btc_1d),
              "ETHUSDT": _supertrend_direction_series(eth_1d)}

    # Funding decisions for the entire 4h timeline.
    log("loading funding …")
    fund_by_asset = {
        "BTCUSDT": _build_funding_decision(common_4h, "BTCUSDT", n_load),
        "ETHUSDT": _build_funding_decision(common_4h, "ETHUSDT", n_load),
    }

    # Diagnostic window: last 3 months of the 4h timeline.
    window_end = common_4h[-1]
    months_back = pd.DateOffset(months=args.window_months)
    window_start = (window_end - months_back).floor("D")
    log(f"diagnostic window: {window_start} -> {window_end}")

    # Vol quartile thresholds: fit on the 6 months BEFORE the window.
    vol_baseline_start = window_start - pd.DateOffset(months=args.vol_baseline_months)
    vol_q25 = {}; vol_q75 = {}
    rvol_test = {}
    for a, df in (("BTCUSDT", btc_4h), ("ETHUSDT", eth_4h)):
        log_ret = np.log(df["close"] / df["close"].shift(1))
        rvol = log_ret.rolling(24, min_periods=24).std()
        baseline_rvol = rvol.loc[(rvol.index >= vol_baseline_start) & (rvol.index < window_start)].dropna()
        if len(baseline_rvol) < 8:
            vol_q25[a] = float("nan"); vol_q75[a] = float("nan")
        else:
            vol_q25[a] = float(baseline_rvol.quantile(0.25))
            vol_q75[a] = float(baseline_rvol.quantile(0.75))
        rvol_test[a] = rvol.loc[(rvol.index >= window_start) & (rvol.index <= window_end)]
        log(f"vol quartiles for {a}: q25={vol_q25[a]:.6f}  q75={vol_q75[a]:.6f}  "
            f"({len(baseline_rvol)} baseline bars)")

    # Replay
    trades = _replay_window(
        {"BTCUSDT": btc_4h, "ETHUSDT": eth_4h},
        strategy, fund_by_asset, window_start, window_end,
        args.fee, args.slippage, size_per_asset, max_open,
    )
    log(f"trades opened in window: {sum(1 for t in trades if t.get('in_window'))}")

    # Enrich with higher-TF + vol bands
    _enrich_with_higher_tf(trades, h_dirs, d_dirs, rvol_test, vol_q25, vol_q75,
                           btc_4h, eth_4h)

    # CSV output
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"recent_regime_failure_trades_{ts_str}.csv"
    cols = ["asset", "direction", "setup", "entry_time", "exit_time", "bars_held",
            "entry_price", "stop_price", "final_stop_price", "exit_price",
            "gross_return_pct", "net_return_pct", "exit_reason",
            "supertrend_direction_4h", "supertrend_line_4h", "close_prev_4h",
            "supertrend_distance_pct_4h",
            "supertrend_direction_1h", "agree_1h_4h",
            "supertrend_direction_1d", "agree_1d_4h",
            "realized_vol_24", "realized_vol_band", "vol_q25", "vol_q75",
            "funding_rate", "funding_percentile",
            "next_bar_adverse_overshoot_pct",
            ]
    rows = []
    for t in trades:
        if not t.get("in_window"):
            continue
        ctx = t.get("context", {})
        row = {
            "asset": t["asset"],
            "direction": t["direction"],
            "setup": t["setup"],
            "entry_time": t["entry_time"].isoformat() if hasattr(t["entry_time"], "isoformat") else str(t["entry_time"]),
            "exit_time": t["exit_time"].isoformat() if hasattr(t["exit_time"], "isoformat") else str(t["exit_time"]),
            "bars_held": t["bars_held"],
            "entry_price": t["entry_price"],
            "stop_price": t["stop_price"],
            "final_stop_price": t["final_stop_price"],
            "exit_price": t["exit_price"],
            "gross_return_pct": t["gross_return_pct"],
            "net_return_pct": t["net_return_pct"],
            "exit_reason": t["exit_reason"],
            "supertrend_direction_4h": ctx.get("supertrend_direction_4h"),
            "supertrend_line_4h": ctx.get("supertrend_line_4h"),
            "close_prev_4h": ctx.get("close_prev_4h"),
            "supertrend_distance_pct_4h": ctx.get("supertrend_distance_pct_4h"),
            "supertrend_direction_1h": ctx.get("supertrend_direction_1h"),
            "agree_1h_4h": ctx.get("agree_1h_4h"),
            "supertrend_direction_1d": ctx.get("supertrend_direction_1d"),
            "agree_1d_4h": ctx.get("agree_1d_4h"),
            "realized_vol_24": ctx.get("realized_vol_24"),
            "realized_vol_band": ctx.get("realized_vol_band"),
            "vol_q25": ctx.get("vol_q25"),
            "vol_q75": ctx.get("vol_q75"),
            "funding_rate": ctx.get("funding_rate"),
            "funding_percentile": ctx.get("funding_percentile"),
            "next_bar_adverse_overshoot_pct": ctx.get("next_bar_adverse_overshoot_pct"),
        }
        rows.append(row)
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    log(f"wrote {len(rows)} window-trade rows -> {csv_path}")

    # Aggregate summary to stdout
    total_net = sum(r["net_return_pct"] for r in rows)
    n_long = sum(1 for r in rows if r["direction"] == "long")
    n_short = sum(1 for r in rows if r["direction"] == "short")
    wins = [r for r in rows if r["net_return_pct"] > 0]
    losses = [r for r in rows if r["net_return_pct"] <= 0]
    pf = (sum(r["net_return_pct"] for r in wins) /
          abs(sum(r["net_return_pct"] for r in losses))) if losses else float("inf")
    log("------------------------------------------------------------")
    log(f"window net return:   {total_net*100:+.3f}%")
    log(f"trade count:         {len(rows)}  (L={n_long}, S={n_short})")
    log(f"wins / losses:       {len(wins)} / {len(losses)}")
    log(f"PF:                  {pf if pf == float('inf') else round(pf, 3)}")
    by_reason = {}
    for r in rows:
        by_reason[r["exit_reason"]] = by_reason.get(r["exit_reason"], 0) + 1
    log(f"by exit reason:      {by_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
