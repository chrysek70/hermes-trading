"""Shared walk-forward coordinator for the Issues #35 / #36 / #37
entry-filter experiments.

This module is a SUPPORT MODULE for three runner scripts:

  - ``scripts/run_volume_confirmation.py``   (Issue #35)
  - ``scripts/run_adx_gate.py``              (Issue #36)
  - ``scripts/run_body_range_confirmation.py`` (Issue #37)

The leading underscore in the filename signals "support, not a
runnable experiment". The runners pass a per-asset
``entry_filter_fn(signal_row, direction) -> bool`` and this module
handles the walk-forward parallel coordinator, the vol_sizing
overlay (Issue #27 / #33 locked), the funding hard gate (Issue
#20 / #21), and the report rendering.

Hard rules respected (locked across all three experiments):

  - signals.py is UNMODIFIED — entry filters wrap ``long_entry`` /
    ``short_entry`` at the runner level.
  - No live worker changes; no yaml writes; no parameter sweeps.
  - Funding hard gate: long blocked at p >= 95, short blocked at
    p <= 5 (Issue #20 / #21 default).
  - Vol_sizing: 24-bar realised vol, train_months=12 (causal,
    trailing-12mo quartile lookup matching ``multi_loop.LiveVolSizingOverlay``).
  - Fold geometry: train=1440, test=360, embargo=6.
  - Fees: 0.001/side, slippage: 0.0005. Issue #29 fill model.
  - 48-month walk-forward; recent windows sliced AFTER the run by
    trade exit timestamp.

The vol_sizing overlay used here matches the LIVE worker's
trailing-12-month quartile lookup (``LiveVolSizingOverlay``) rather
than the per-fold-train-slice variant in
``scripts/run_adaptive_sizing.py``, because the live candidate is
the comparison target and we want the baseline numbers to be
directly comparable to the operator's live config (Issue #33
``state/live_multiasset_long_short_funding_vol.yaml``).
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from hermes_trading import log
from hermes_trading import data as data_mod
from hermes_trading import funding as funding_mod
from hermes_trading import signals
from hermes_trading import walk_forward as wf
from hermes_trading.multi_loop import (
    RESEARCH_FEE_PER_SIDE,
    RESEARCH_SLIPPAGE,
    VOL_SIZING_WINDOW_BARS_DEFAULT,
    VOL_SIZING_TRAIN_MONTHS_DEFAULT,
    VOL_SIZING_MULT_LOW_DEFAULT,
    VOL_SIZING_MULT_MID_DEFAULT,
    VOL_SIZING_MULT_HIGH_DEFAULT,
    vol_bucket_from_thresholds,
    vol_multiplier_from_bucket,
)

ASSETS = ("BTCUSDT", "ETHUSDT")

# Recent-window slicing for the report (months back from the last
# trade's exit time). 48mo is the full window so we don't include it
# here — it's implicit (the unsliced data set).
RECENT_WINDOWS = (3, 6, 12, 24)

# ---- locked geometry / fees / slippage / overlay parameters ----
TRAIN_BARS_DEFAULT = 1440
TEST_BARS_DEFAULT = 360
EMBARGO_BARS_DEFAULT = 6
FEE_DEFAULT = RESEARCH_FEE_PER_SIDE
SLIPPAGE_DEFAULT = RESEARCH_SLIPPAGE
VOL_WINDOW_BARS = VOL_SIZING_WINDOW_BARS_DEFAULT       # 24 (Issue #27 / #33)
VOL_TRAIN_MONTHS = VOL_SIZING_TRAIN_MONTHS_DEFAULT     # 12 (live overlay match)
VOL_MULT_LOW = VOL_SIZING_MULT_LOW_DEFAULT             # Q1 = 1.00
VOL_MULT_MID = VOL_SIZING_MULT_MID_DEFAULT             # Q2/Q3 = 0.50
VOL_MULT_HIGH = VOL_SIZING_MULT_HIGH_DEFAULT           # Q4 = 0.25
FUNDING_BLOCK_LONG_ABOVE_PCT = 95.0
FUNDING_BLOCK_SHORT_BELOW_PCT = 5.0
FUNDING_PERCENTILE_WINDOW_BARS = 180


# ---------- funding hard gate (Issue #20 / #21 default) ---------------------


def build_funding_gate(price_index: pd.DatetimeIndex, symbol: str,
                       n_months: int, side: str) -> pd.DataFrame:
    """Per-direction funding hard gate — direction-aware, no sizing.

    Long: blocked when rolling percentile >= 95.
    Short: blocked when rolling percentile <= 5.
    Warmup fails open (allowed).
    """
    f = funding_mod.load_funding(symbol, n_months=n_months)
    aligned = funding_mod.align_to_index(f, price_index)
    pct = funding_mod.rolling_percentile(
        aligned, window=FUNDING_PERCENTILE_WINDOW_BARS,
    )
    warmup = pct.isna()
    if side == "long":
        allowed = pct < FUNDING_BLOCK_LONG_ABOVE_PCT
    elif side == "short":
        allowed = pct > FUNDING_BLOCK_SHORT_BELOW_PCT
    else:
        raise ValueError(side)
    allowed = allowed.where(~warmup, True)
    return pd.DataFrame({
        "long_allowed": allowed.astype(bool),
        "funding_percentile": pct.astype(float),
    }, index=price_index)


# ---------- vol_sizing overlay (Issue #27 / #33 locked, live-matching) ------


def build_vol_sizing_overlay(asset_df: pd.DataFrame,
                             window_bars: int = VOL_WINDOW_BARS,
                             train_months: int = VOL_TRAIN_MONTHS,
                             ) -> pd.DataFrame:
    """Compute per-bar vol-bucket + multiplier matching the live
    ``LiveVolSizingOverlay`` behaviour: 24-bar rolling realised vol
    of log returns, trailing 12-month train-window quartile
    thresholds applied causally (strictly-before lookup, so no
    future leak).

    Returns a DataFrame indexed by ``asset_df.index`` with columns:
        realised_vol  (float)
        q25, q50, q75 (float; trailing 12mo train-window quartiles)
        bucket        ("Q1" / "Q2_Q3" / "Q4" / "warmup")
        size_multiplier (1.00 / 0.50 / 0.25; warmup fails open to 1.00)
    """
    closes = asset_df["close"].astype(float)
    logr = np.log(closes / closes.shift(1))
    rv = logr.rolling(window_bars, min_periods=window_bars).std()

    # Trailing-12mo quartiles, evaluated strictly BEFORE each bar.
    # Implemented with a vectorised approach: for each timestamp ts,
    # use rv.loc[ts - train_months months : ts) (exclusive).
    idx = asset_df.index
    q25 = np.full(len(idx), np.nan)
    q50 = np.full(len(idx), np.nan)
    q75 = np.full(len(idx), np.nan)
    rv_arr = rv.values
    # Convert to numpy datetime64 for offset arithmetic via pandas
    for i, ts in enumerate(idx):
        train_start = ts - pd.DateOffset(months=train_months)
        # strictly before ts -> use < ts
        mask = (idx >= train_start) & (idx < ts)
        if not mask.any():
            continue
        slc = rv_arr[mask]
        slc = slc[~np.isnan(slc)]
        if len(slc) < 50:  # stability floor (matches LiveVolSizingOverlay)
            continue
        q25[i] = float(np.quantile(slc, 0.25))
        q50[i] = float(np.quantile(slc, 0.50))
        q75[i] = float(np.quantile(slc, 0.75))

    bucket = []
    mult = np.empty(len(idx), dtype=float)
    rv_vals = rv.values
    for i in range(len(idx)):
        rv_v = None if np.isnan(rv_vals[i]) else float(rv_vals[i])
        q25_v = None if np.isnan(q25[i]) else float(q25[i])
        q75_v = None if np.isnan(q75[i]) else float(q75[i])
        b = vol_bucket_from_thresholds(rv_v, q25_v, q75_v)
        bucket.append(b)
        mult[i] = vol_multiplier_from_bucket(
            b,
            mult_low=VOL_MULT_LOW,
            mult_mid=VOL_MULT_MID,
            mult_high=VOL_MULT_HIGH,
        )
    return pd.DataFrame({
        "realised_vol": rv,
        "q25": pd.Series(q25, index=idx),
        "q50": pd.Series(q50, index=idx),
        "q75": pd.Series(q75, index=idx),
        "bucket": pd.Series(bucket, index=idx),
        "size_multiplier": pd.Series(mult, index=idx),
    }, index=idx)


# ---------- trade book-keeping ---------------------------------------------


def _open_position(asset, row, setup, direction, base_size, size_per_asset,
                   overlay_mult, slippage, strategy, i, ts, vol_tags):
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
        "overlay_size_multiplier": float(overlay_mult),
        "size_per_asset": size_per_asset,
        "base_size": base_size,
        "entry_rsi": float(row["rsi"]) if pd.notna(row.get("rsi")) else None,
        "entry_atr": float(row["atr"]) if pd.notna(row.get("atr")) else None,
        "vol_bucket_at_entry": vol_tags.get("bucket"),
        "vol_multiplier_at_entry": vol_tags.get("size_multiplier"),
        "realised_vol_at_entry": vol_tags.get("realised_vol"),
    }


def _close_position(position, exit_fill, reason, ts, fee, asset, bars_held):
    if position["direction"] == "long":
        gross = (exit_fill - position["entry"]) / position["entry"]
    else:
        gross = (position["entry"] - exit_fill) / position["entry"]
    effective = (position["base_size"] * position["size_per_asset"]
                 * position["overlay_size_multiplier"])
    net = (gross - 2 * fee) * effective
    return {
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
        "vol_bucket_at_entry": position.get("vol_bucket_at_entry"),
        "vol_multiplier_at_entry": position.get("vol_multiplier_at_entry"),
        "realised_vol_at_entry": position.get("realised_vol_at_entry"),
    }, net


# ---------- per-variant walk-forward coordinator ----------------------------


def _vol_lookup(vol_df: pd.DataFrame | None, ts) -> dict:
    if vol_df is None or ts not in vol_df.index:
        return {"size_multiplier": 1.0, "bucket": "warmup",
                "realised_vol": None}
    row = vol_df.loc[ts]
    return {
        "size_multiplier": float(row["size_multiplier"]),
        "bucket": str(row["bucket"]) if pd.notna(row["bucket"]) else "warmup",
        "realised_vol": (float(row["realised_vol"])
                         if pd.notna(row["realised_vol"]) else None),
    }


def _funding_allowed(decision_df: pd.DataFrame, ts) -> bool:
    if decision_df is None or ts not in decision_df.index:
        return True
    return bool(decision_df.loc[ts]["long_allowed"])


def run_walk_forward(
    name: str,
    btc_df: pd.DataFrame,
    eth_df: pd.DataFrame,
    strategy: dict,
    long_funding: dict,
    short_funding: dict,
    vol_overlay: dict[str, pd.DataFrame] | None,
    entry_filter_fn: Callable[[pd.Series, str, str], bool] | None,
    train_bars: int = TRAIN_BARS_DEFAULT,
    test_bars: int = TEST_BARS_DEFAULT,
    embargo_bars: int = EMBARGO_BARS_DEFAULT,
    fee: float = FEE_DEFAULT,
    slippage: float = SLIPPAGE_DEFAULT,
    max_open: int = 2,
) -> dict:
    """Walk-forward parallel coordinator for ONE variant.

    Args:
      name             : variant name for logging / tagging.
      btc_df / eth_df  : aligned per-asset OHLCV (full span).
      strategy         : long-short SuperTrend strategy dict.
      long_funding     : {asset: DataFrame(long_allowed: bool, ...)}.
      short_funding    : {asset: DataFrame(long_allowed: bool, ...)}.
      vol_overlay      : {asset: vol DataFrame} or None (baseline w/o vol).
                         When provided, each trade is sized by the
                         per-bar size_multiplier at the signal bar.
      entry_filter_fn  : optional function (signal_row, direction, asset)
                         -> bool. False blocks the entry. None = passthrough.

    Returns a dict with name, oos metrics, folds, trades, fold-returns,
    counts, and derived metrics for report rendering.
    """
    log(f"========== {name} ==========")
    btc_ind = signals.compute_indicators(btc_df, strategy)
    eth_ind = signals.compute_indicators(eth_df, strategy)
    common = btc_ind.index.intersection(eth_ind.index)
    btc_ind = btc_ind.loc[common].copy()
    eth_ind = eth_ind.loc[common].copy()

    asset_df = {"BTCUSDT": btc_df.loc[common], "ETHUSDT": eth_df.loc[common]}
    asset_ind = {"BTCUSDT": btc_ind, "ETHUSDT": eth_ind}
    size_per_asset = 1.0 / len(ASSETS)
    base_size = float(strategy["risk"].get("position_size_r", 0.5))

    n = len(common)
    folds = []
    all_trades: list[dict] = []
    fold_returns: list[float] = []
    fold = 0
    cursor = 0
    max_concurrent = 0
    stops_filtered_out = 0  # filtered-out signal counter for diagnostics
    longs_filtered_out = 0
    shorts_filtered_out = 0

    while True:
        train_hi = cursor + train_bars
        test_lo = train_hi + embargo_bars
        test_hi = test_lo + test_bars
        if test_hi > n:
            break
        fold += 1

        btc_test = btc_ind.iloc[test_lo:test_hi]
        eth_test = eth_ind.iloc[test_lo:test_hi]
        per_asset_records = {
            "BTCUSDT": btc_test.to_dict("records"),
            "ETHUSDT": eth_test.to_dict("records"),
        }

        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        positions: dict[str, dict | None] = {a: None for a in ASSETS}
        fold_trades = []
        fold_max_concurrent = 0

        for i in range(len(btc_test)):
            ts = btc_test.index[i]
            for asset in ASSETS:
                row = per_asset_records[asset][i]
                position = positions[asset]

                # ---- exit ----
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

                # ---- entry ----
                if positions[asset] is None:
                    open_count = sum(1 for p in positions.values() if p is not None)
                    if open_count >= max_open:
                        continue

                    # 1) LONG
                    setup_l = signals.long_entry(row, strategy)
                    if setup_l:
                        if not _funding_allowed(long_funding[asset], ts):
                            continue
                        # entry-filter gate (the experiment)
                        if entry_filter_fn is not None:
                            row_series = btc_test.iloc[i] if asset == "BTCUSDT" \
                                else eth_test.iloc[i]
                            if not entry_filter_fn(row_series, "long", asset):
                                longs_filtered_out += 1
                                continue
                        vol_tags = _vol_lookup(
                            vol_overlay[asset] if vol_overlay else None, ts,
                        )
                        mult = vol_tags["size_multiplier"]
                        if mult > 0:
                            positions[asset] = _open_position(
                                asset, row, setup_l, "long",
                                base_size, size_per_asset, mult, slippage,
                                strategy, i, ts, vol_tags,
                            )
                        continue

                    # 2) SHORT
                    setup_s = signals.short_entry(row, strategy)
                    if setup_s:
                        if not _funding_allowed(short_funding[asset], ts):
                            continue
                        if entry_filter_fn is not None:
                            row_series = btc_test.iloc[i] if asset == "BTCUSDT" \
                                else eth_test.iloc[i]
                            if not entry_filter_fn(row_series, "short", asset):
                                shorts_filtered_out += 1
                                continue
                        vol_tags = _vol_lookup(
                            vol_overlay[asset] if vol_overlay else None, ts,
                        )
                        mult = vol_tags["size_multiplier"]
                        if mult > 0:
                            positions[asset] = _open_position(
                                asset, row, setup_s, "short",
                                base_size, size_per_asset, mult, slippage,
                                strategy, i, ts, vol_tags,
                            )

            cur_open = sum(1 for p in positions.values() if p is not None)
            fold_max_concurrent = max(fold_max_concurrent, cur_open)

        # close any open positions at fold end
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
    n_stop = sum(1 for t in all_trades if t.get("reason") == "stop")
    mean_overlay_mult = (
        float(np.mean([t["overlay_size_multiplier"] for t in all_trades]))
        if all_trades else 1.0
    )
    return_per_exposure = (oos["total_return"] / mean_overlay_mult
                           if mean_overlay_mult > 0 else 0.0)
    stop_freq = n_stop / oos["trades"] if oos["trades"] else 0.0
    log(f"  -> trades={oos['trades']} (L={n_long}, S={n_short})  "
        f"ret={oos['total_return']*100:+.2f}%  DD={oos['max_drawdown']*100:.2f}%  "
        f"PF={pf_s}  win={oos['win_rate']*100:.1f}%  "
        f"meanMult={mean_overlay_mult:.3f}  "
        f"ret/exp={return_per_exposure*100:+.2f}%  "
        f"stop%={stop_freq*100:.1f}%  "
        f"filtered L={longs_filtered_out}/S={shorts_filtered_out}  "
        f"folds+={fold_pos}/{len(folds)}  max_open={max_concurrent}")

    return {
        "name": name, "oos": oos, "folds": folds, "trades": all_trades,
        "fold_pos": fold_pos, "fold_returns": fold_returns,
        "max_concurrent": max_concurrent,
        "n_long": n_long, "n_short": n_short, "n_stop": n_stop,
        "mean_overlay_mult": mean_overlay_mult,
        "return_per_exposure": return_per_exposure,
        "stop_freq": stop_freq,
        "longs_filtered_out": longs_filtered_out,
        "shorts_filtered_out": shorts_filtered_out,
    }


# ---------- recent-window slicing -------------------------------------------


def slice_trades_recent_window(trades: list[dict],
                               window_months: int,
                               anchor_ts: pd.Timestamp) -> list[dict]:
    """Return the subset of ``trades`` whose ``exit_ts`` falls in the
    last ``window_months`` months ending at ``anchor_ts``."""
    cutoff = anchor_ts - pd.DateOffset(months=window_months)
    out = []
    for t in trades:
        et = t.get("exit_ts")
        if et is None:
            continue
        et_pd = pd.Timestamp(et)
        if et_pd.tzinfo is None and anchor_ts.tzinfo is not None:
            et_pd = et_pd.tz_localize("UTC")
        if et_pd >= cutoff:
            out.append(t)
    return out


def metrics_for_trades(trades: list[dict]) -> dict:
    """Compute the report metrics dict for an arbitrary trade list.

    Includes: trades, total_return, max_drawdown, profit_factor,
    win_rate, return_per_exposure, stop_freq, mean_overlay_mult.
    """
    base = wf._stitch_metrics(trades)
    n_stop = sum(1 for t in trades if t.get("reason") == "stop")
    mean_mult = (float(np.mean([t["overlay_size_multiplier"] for t in trades]))
                 if trades else 1.0)
    return_per_exposure = (base["total_return"] / mean_mult
                           if mean_mult > 0 else 0.0)
    stop_freq = n_stop / base["trades"] if base["trades"] else 0.0
    return {
        "trades": base["trades"],
        "total_return": base["total_return"],
        "max_drawdown": base["max_drawdown"],
        "profit_factor": base["profit_factor"],
        "win_rate": base["win_rate"],
        "mean_overlay_mult": mean_mult,
        "return_per_exposure": return_per_exposure,
        "stop_freq": stop_freq,
    }


# ---------- report rendering ------------------------------------------------


def _fmt_pf(pf: float) -> str:
    if pf == float("inf"):
        return "inf"
    if pf >= 9999.0:
        return "inf"
    return f"{pf:.2f}"


def render_csv(out_path: Path, variant_results: list[dict],
               recent_metrics: dict[str, dict[int, dict]],
               full_window_months: int = 48) -> None:
    """Write a comparison CSV with one row per (variant, window) pair.

    ``recent_metrics[variant_name][months] = metrics dict``. The full
    window is keyed by ``full_window_months``.
    """
    cols = [
        "variant", "window_months",
        "trades", "total_return", "max_drawdown", "profit_factor",
        "win_rate", "return_per_exposure", "stop_freq",
        "mean_overlay_multiplier",
    ]
    windows = (full_window_months,) + tuple(
        m for m in RECENT_WINDOWS if m < full_window_months
    )
    rows = []
    for r in variant_results:
        for months in windows:
            m = recent_metrics[r["name"]][months]
            pf = m["profit_factor"]
            rows.append({
                "variant": r["name"],
                "window_months": months,
                "trades": m["trades"],
                "total_return": m["total_return"],
                "max_drawdown": m["max_drawdown"],
                "profit_factor": (pf if pf != float("inf") else 9999.0),
                "win_rate": m["win_rate"],
                "return_per_exposure": m["return_per_exposure"],
                "stop_freq": m["stop_freq"],
                "mean_overlay_multiplier": m["mean_overlay_mult"],
            })
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def render_md_comparison(
    out_path: Path,
    title: str,
    experiment_blurb: str,
    variant_names: tuple[str, str],
    variant_results: list[dict],
    recent_metrics: dict[str, dict[int, dict]],
    span_lo: pd.Timestamp,
    span_hi: pd.Timestamp,
    n_bars: int,
    n_months: int,
    train_bars: int,
    test_bars: int,
    embargo_bars: int,
    fee: float,
    slippage: float,
    adoption_verdict: str,
    adoption_question: str,
) -> None:
    """Render the comparison markdown table for one experiment."""
    md = [
        f"# {title}",
        "",
        experiment_blurb,
        "",
        f"- universe: BTC/USDT + ETH/USDT (parallel; max 2 concurrent positions)",
        f"- strategy: SuperTrend(10,3) long-short — `state/strategy_supertrend_long_short.yaml`",
        f"- funding hard gate: long-block at p>=95, short-block at p<=5 (Issue #20 / #21)",
        f"- vol_sizing: 24-bar rv, trailing 12mo train window, Q1=1.00 / Q2_Q3=0.50 / Q4=0.25 (Issue #27 / #33)",
        f"- {n_months}mo span: {span_lo.date()} -> {span_hi.date()} ({n_bars} bars)",
        f"- walk-forward: train={train_bars} / test={test_bars} / embargo={embargo_bars}",
        f"- costs: fee={fee}/side, slippage={slippage} (Issue #29 fill model)",
        "",
        f"## Full {n_months}-month walk-forward OOS",
        "",
        "| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in variant_results:
        m = recent_metrics[r["name"]][n_months]
        md.append(
            f"| `{r['name']}` | {m['trades']} | "
            f"{m['total_return']*100:+.2f}% | "
            f"{m['max_drawdown']*100:.2f}% | "
            f"{_fmt_pf(m['profit_factor'])} | "
            f"{m['win_rate']*100:.1f}% | "
            f"{m['return_per_exposure']*100:+.2f}% | "
            f"{m['stop_freq']*100:.1f}% | "
            f"{m['mean_overlay_mult']:.3f} |"
        )

    # head-to-head delta row
    md.append("")
    md.append("### Head-to-head (baseline → +filter)")
    md.append("")
    md.append("| window | Δ trades | Δ return | Δ max DD | Δ PF | Δ win% | Δ ret/exp | Δ stop% |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    base_name, filt_name = variant_names
    windows = (n_months,) + tuple(m for m in RECENT_WINDOWS if m < n_months)
    for months in windows:
        b = recent_metrics[base_name][months]
        f_ = recent_metrics[filt_name][months]
        d_pf = (f_["profit_factor"] - b["profit_factor"]
                if (b["profit_factor"] != float("inf")
                    and f_["profit_factor"] != float("inf"))
                else float("nan"))
        d_pf_s = "—" if not np.isfinite(d_pf) else f"{d_pf:+.2f}"
        md.append(
            f"| {months}mo | "
            f"{f_['trades'] - b['trades']:+d} | "
            f"{(f_['total_return'] - b['total_return'])*100:+.2f}pp | "
            f"{(f_['max_drawdown'] - b['max_drawdown'])*100:+.2f}pp | "
            f"{d_pf_s} | "
            f"{(f_['win_rate'] - b['win_rate'])*100:+.2f}pp | "
            f"{(f_['return_per_exposure'] - b['return_per_exposure'])*100:+.2f}pp | "
            f"{(f_['stop_freq'] - b['stop_freq'])*100:+.2f}pp |"
        )

    # Per-window tables
    md.append("")
    md.append("## Recent-window subsets (sliced by exit timestamp)")
    md.append("")
    for months in [m for m in RECENT_WINDOWS if m < n_months]:
        md.append(f"### Last {months} months")
        md.append("")
        md.append(
            "| variant | trades | total return | max DD | PF | win% | ret/exp | stop% | mean mult |"
        )
        md.append(
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
        )
        for r in variant_results:
            m = recent_metrics[r["name"]][months]
            md.append(
                f"| `{r['name']}` | {m['trades']} | "
                f"{m['total_return']*100:+.2f}% | "
                f"{m['max_drawdown']*100:.2f}% | "
                f"{_fmt_pf(m['profit_factor'])} | "
                f"{m['win_rate']*100:.1f}% | "
                f"{m['return_per_exposure']*100:+.2f}% | "
                f"{m['stop_freq']*100:.1f}% | "
                f"{m['mean_overlay_mult']:.3f} |"
            )
        md.append("")

    md.append("## Adoption verdict")
    md.append("")
    md.append(f"**Question**: {adoption_question}")
    md.append("")
    md.append(adoption_verdict)
    md.append("")
    with open(out_path, "w") as fh:
        fh.write("\n".join(md))


def load_universe(n_months: int, timeframe: str = "4h"):
    """Load and align BTC + ETH on the chosen timeframe."""
    log(f"loading BTC + ETH {n_months}mo …")
    btc_df = data_mod.resample(
        data_mod.load_klines("BTCUSDT", n_months=n_months), timeframe,
    )
    eth_df = data_mod.resample(
        data_mod.load_klines("ETHUSDT", n_months=n_months), timeframe,
    )
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]
    eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  "
        f"span: {common[0].date()} -> {common[-1].date()}")
    return btc_df, eth_df, common


def build_funding_gates(price_index, n_months: int):
    """Build per-asset per-direction funding gate dicts."""
    log("building funding hard gate (long: block at p>=95; short: block at p<=5) …")
    long_funding = {
        a: build_funding_gate(price_index, a, n_months, "long")
        for a in ASSETS
    }
    short_funding = {
        a: build_funding_gate(price_index, a, n_months, "short")
        for a in ASSETS
    }
    return long_funding, short_funding


def build_vol_overlays(asset_df: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Build per-asset vol_sizing overlay DataFrames (24-bar rv,
    trailing 12mo quartile thresholds — Issue #27 / #33 lock)."""
    log("building vol_sizing overlay (24-bar rv, trailing 12mo quartiles, "
        "Q1=1.00 / Q2_Q3=0.50 / Q4=0.25) …")
    out = {}
    for a, df in asset_df.items():
        out[a] = build_vol_sizing_overlay(df,
                                          window_bars=VOL_WINDOW_BARS,
                                          train_months=VOL_TRAIN_MONTHS)
        bk_counts = out[a]["bucket"].value_counts().to_dict()
        log(f"  {a}: bucket counts = {bk_counts}")
    return out


def write_trades_csv(out_path: Path, variant_results: list[dict]) -> None:
    """Combine trade lists from all variants into one CSV."""
    base_cols = [
        "_variant", "asset", "entry_ts", "exit_ts", "setup_name",
        "direction", "entry_price", "exit_price",
        "gross_return_pct", "net_return_pct",
        "size_per_asset", "overlay_size_multiplier",
        "position_size_effective", "exit_reason", "holding_bars",
        "vol_bucket_at_entry", "vol_multiplier_at_entry",
        "realised_vol_at_entry",
    ]
    rows = []
    for r in variant_results:
        for t in r["trades"]:
            row = []
            for c in base_cols:
                v = t.get(c)
                if hasattr(v, "isoformat"):
                    v = v.isoformat()
                if v is None:
                    v = ""
                row.append(v)
            rows.append(row)
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(base_cols)
        w.writerows(rows)


def compute_window_metrics(
    variant_results: list[dict],
    span_hi: pd.Timestamp,
    full_window_months: int = 48,
) -> dict[str, dict[int, dict]]:
    """For each variant, compute the metrics dict for the full-span
    window AND each of the RECENT_WINDOWS. Returns nested dict
    indexed [variant_name][months]. The full-span window is keyed
    by ``full_window_months`` (default 48)."""
    out: dict[str, dict[int, dict]] = {}
    for r in variant_results:
        full_metrics = metrics_for_trades(r["trades"])
        out[r["name"]] = {full_window_months: full_metrics}
        for months in RECENT_WINDOWS:
            if months >= full_window_months:
                # Skip recent windows that meet or exceed the full
                # span — they would just duplicate the full-window
                # row.
                continue
            subset = slice_trades_recent_window(
                r["trades"], months, span_hi,
            )
            out[r["name"]][months] = metrics_for_trades(subset)
    return out
