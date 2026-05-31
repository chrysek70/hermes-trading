#!/usr/bin/env python3
"""Adaptive regime-based position sizing on the adopted BTC/ETH
long-short SuperTrend(10, 3) + funding-filter candidate (Issue #27).

The currently adopted live candidate (Issues #20 / #21) only adapts
via SuperTrend ATR behaviour, the funding filter, ratcheting stops,
and the decay monitor. It does NOT change position size based on the
prevailing regime.

Issue #6 tested HMM as a hard FILTER and it dropped trade count
below the 100 gate on the long-short variant (Issue #20). This
runner tests the hypothesis that the same HMM is more useful as a
SIZING multiplier — keep every signal the alpha layer fires, but
take it with less risk in unfavourable regimes.

Variants (all share the funding filter — direction-aware, block long
at p>=95, block short at p<=5 — the current adopted hard gate):

  1. baseline                — funding filter, no sizing overlay
  2. hmm_sizing              — funding + HMM-prob -> {1.0, 0.5, 0.25}
  3. vol_sizing              — funding + vol-quartile -> {1.0, 0.5, 0.25}
  4. hmm_plus_vol_sizing     — funding + min(HMM mult, vol mult)

Sizing concept (per Issue #27 spec):
  - favourable      → 1.00 size multiplier
  - neutral         → 0.50 size multiplier
  - adverse/high-vol→ 0.25 size multiplier
  - Multiplier multiplies against the existing per-asset base
    (``size_per_asset = 0.5``). Never increases above base.

HMM mapping (from per-fold per-asset 2-state HMM fit on causal
features):
  - P(favourable) >= 0.70                          -> 1.00
  - 0.55 <= P(favourable) < 0.70                   -> 0.50
  - P(favourable) <  0.55 OR P(adverse) >= 0.70    -> 0.25

Volatility mapping (rolling 24-bar realised vol of log returns;
quartile bands fitted on TRAIN bars of each fold, applied causally
to test bars):
  - Q1 (lowest vol)    -> 1.00
  - Q2 / Q3 (mid)      -> 0.50
  - Q4 (highest vol)   -> 0.25

Stacking (variant 4): the combined multiplier is the MIN of the two
overlays, ensuring stacked overlays only ever *reduce* exposure
versus either component alone.

Hard rules respected (Issue #27):
  - No live trading changes.
  - No parameter tuning of any adopted parameter.
  - BTC + ETH only; SuperTrend(10, 3) unchanged; 4h timeframe.
  - Walk-forward OOS only; causal features only.
  - Baseline is the current adopted candidate.

Adoption criterion: reduce DD or improve PF without materially
reducing trade count (within ~5% of baseline).

Outputs (under --out-dir, default ``results/``):
  - adaptive_sizing_comparison_<ts>.csv
  - adaptive_sizing_comparison_<ts>.md
  - trades_adaptive_sizing_<ts>.csv
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

# Locked per Issue #27 spec. Editing these would change the experiment.
SIZE_FAVOURABLE = 1.00
SIZE_NEUTRAL = 0.50
SIZE_ADVERSE = 0.25

HMM_P_FAV_FULL = 0.70
HMM_P_FAV_HALF = 0.55
HMM_P_ADV_BLOCK = 0.70


# ---------- funding hard gate (direction-aware, FIXED across variants) ------

def _build_funding_gate(price_index: pd.DatetimeIndex, symbol: str,
                        n_months: int, side: str,
                        block_long_above_pct: float = 95.0,
                        block_short_below_pct: float = 5.0,
                        window: int = 180) -> pd.DataFrame:
    """Returns ``DataFrame(long_allowed: bool, size_multiplier: 1.0)`` —
    the funding filter is a hard gate only. Sizing overlays come from
    separate per-fold per-asset modules below."""
    f = funding_mod.load_funding(symbol, n_months=n_months)
    aligned = funding_mod.align_to_index(f, price_index)
    pct = funding_mod.rolling_percentile(aligned, window=window)
    warmup = pct.isna()

    if side == "long":
        allowed = pct < block_long_above_pct
    elif side == "short":
        allowed = pct > block_short_below_pct
    else:
        raise ValueError(side)
    allowed = allowed.where(~warmup, True)  # fail-open during warmup

    return pd.DataFrame({
        "long_allowed": allowed.astype(bool),
        "size_multiplier": pd.Series(1.0, index=price_index, dtype=float),
        "funding_percentile": pct.astype(float),
    }, index=price_index)


# ---------- HMM sizing (per-fold per-asset fit, Issue #27 mapping) ----------

def _build_hmm_sizing(asset, train_df, train_ind, test_df, test_ind,
                      hmm_cfg, strategy, fee, slippage):
    """Fit HMM on train; build test-window sizing decisions with the
    Issue #27 mapping (favourable=1.0 / neutral=0.5 / adverse=0.25).

    Returns a DataFrame indexed by ``test_ind.index`` with:
        size_multiplier    : float in {1.00, 0.50, 0.25}
        regime_label       : "favourable" / "neutral" / "adverse" / "warmup"
        p_favorable        : raw probability for diagnostics
    """
    try:
        det = hmmr.HMMRegimeDetector(hmm_cfg)
        det.fit(train_df, indicators=train_ind)
        # train-window: build the same trade-history-based label map the
        # existing run_long_short_overlays.py uses, so favourable / adverse
        # mapping mirrors the established pattern. Falls back to vol-based
        # mapping when trade count is sparse.
        train_proba = det.predict_proba(train_df, indicators=train_ind)
        prob_cols = [c for c in train_proba.columns if c.startswith("p_state_")]
        tp_clean = train_proba[prob_cols].dropna()
        if tp_clean.empty:
            return None
        train_states = (tp_clean.idxmax(axis=1)
                        .str.replace("p_state_", "").astype(float)
                        .reindex(train_proba.index))
        train_ind_tagged = bt._attach_neutral_markov_columns(train_ind.copy())
        train_ind_tagged["hmm_state_at_entry"] = train_states.reindex(
            train_ind.index).values
        train_records = train_ind_tagged.to_dict("records")
        train_res = bt._run_state_machine(train_records, strategy,
                                          warmup=0, fee=fee, slippage=slippage)
        for t in train_res["trades"]:
            idx = t.get("entry_ts")
            if idx is not None and idx in train_states.index:
                v = train_states.loc[idx]
                t["hmm_state_at_entry"] = int(v) if not pd.isna(v) else None
        det.map_states(train_trades=train_res["trades"])

        # test-window probabilities → Issue #27 sizing
        test_proba = det.predict_proba(test_df, indicators=test_ind)
        fav_state = next(s for s, lbl in det.state_labels_.items()
                         if lbl == "favorable")
        adv_state = next(s for s, lbl in det.state_labels_.items()
                         if lbl == "adverse")
        p_fav = test_proba[f"p_state_{fav_state}"]
        p_adv = test_proba[f"p_state_{adv_state}"]
        warmup = p_fav.isna() | p_adv.isna()

        size_mult = pd.Series(SIZE_ADVERSE, index=test_ind.index, dtype=float)
        regime_label = pd.Series("adverse", index=test_ind.index, dtype=object)
        # neutral band: P_fav >= 0.55
        nb_mask = p_fav >= HMM_P_FAV_HALF
        size_mult = size_mult.mask(nb_mask, SIZE_NEUTRAL)
        regime_label = regime_label.mask(nb_mask, "neutral")
        # favourable band: P_fav >= 0.70
        fb_mask = p_fav >= HMM_P_FAV_FULL
        size_mult = size_mult.mask(fb_mask, SIZE_FAVOURABLE)
        regime_label = regime_label.mask(fb_mask, "favourable")
        # adverse override: P_adv >= 0.70 stamps the floor regardless
        ab_mask = p_adv >= HMM_P_ADV_BLOCK
        size_mult = size_mult.mask(ab_mask, SIZE_ADVERSE)
        regime_label = regime_label.mask(ab_mask, "adverse")
        # warmup: fail open (full size, labelled separately)
        size_mult = size_mult.where(~warmup, SIZE_FAVOURABLE)
        regime_label = regime_label.where(~warmup, "warmup")

        return pd.DataFrame({
            "size_multiplier": size_mult,
            "regime_label": regime_label,
            "p_favorable": p_fav,
        }, index=test_ind.index)
    except Exception as exc:  # noqa: BLE001
        log(f"    [yellow]HMM fit failed for {asset}: {exc}; neutral fallback[/yellow]")
        return None


# ---------- volatility sizing (per-fold quartile thresholds, train-only) ----

def _build_vol_sizing(train_df: pd.DataFrame, test_df: pd.DataFrame,
                      window: int = 24) -> pd.DataFrame:
    """Compute realised volatility on train+test (causal — rolling on log
    returns); fit quartile bounds on TRAIN ONLY; apply to TEST bars
    causally.

    Mapping:
      Q1 (low vol)  -> 1.00
      Q2 / Q3 (mid) -> 0.50
      Q4 (high vol) -> 0.25
    """
    train_close = train_df["close"]
    test_close = test_df["close"]
    full_close = pd.concat([train_close, test_close])
    log_ret = np.log(full_close / full_close.shift(1))
    rvol_full = log_ret.rolling(window, min_periods=window).std()
    rvol_train = rvol_full.loc[train_df.index]
    rvol_test = rvol_full.loc[test_df.index]

    # Quartile bounds from train data only — causal.
    train_clean = rvol_train.dropna()
    if len(train_clean) < 8:
        # Not enough train history — fall back to neutral (1.0).
        return pd.DataFrame({
            "size_multiplier": pd.Series(SIZE_FAVOURABLE,
                                         index=test_df.index, dtype=float),
            "regime_label": pd.Series("warmup", index=test_df.index,
                                      dtype=object),
            "realized_vol": rvol_test,
        }, index=test_df.index)
    q25 = float(train_clean.quantile(0.25))
    q75 = float(train_clean.quantile(0.75))

    warmup = rvol_test.isna()
    # Default to neutral middle band (Q2/Q3)
    size_mult = pd.Series(SIZE_NEUTRAL, index=test_df.index, dtype=float)
    regime_label = pd.Series("neutral", index=test_df.index, dtype=object)
    low_mask = rvol_test <= q25
    size_mult = size_mult.mask(low_mask, SIZE_FAVOURABLE)
    regime_label = regime_label.mask(low_mask, "favourable")
    high_mask = rvol_test >= q75
    size_mult = size_mult.mask(high_mask, SIZE_ADVERSE)
    regime_label = regime_label.mask(high_mask, "adverse")
    # warmup → fail open
    size_mult = size_mult.where(~warmup, SIZE_FAVOURABLE)
    regime_label = regime_label.where(~warmup, "warmup")

    return pd.DataFrame({
        "size_multiplier": size_mult,
        "regime_label": regime_label,
        "realized_vol": rvol_test,
    }, index=test_df.index)


# ---------- per-bar overlay resolution --------------------------------------

def _funding_allowed(decision_df: pd.DataFrame, ts) -> bool:
    if decision_df is None or ts not in decision_df.index:
        return True
    return bool(decision_df.loc[ts]["long_allowed"])


def _sizing_lookup(decision_df: pd.DataFrame | None,
                   ts) -> tuple[float, str | None]:
    if decision_df is None or ts not in decision_df.index:
        return SIZE_FAVOURABLE, None
    row = decision_df.loc[ts]
    mult = float(row["size_multiplier"])
    label = row.get("regime_label")
    return mult, (str(label) if pd.notna(label) else None)


def _combined_multiplier(hmm_dec, vol_dec, ts) -> tuple[float, dict]:
    """Return ``(multiplier, tags)``. ``tags`` carries the per-overlay
    regime labels so the trade can be tagged for the by-regime
    breakdown."""
    h_mult, h_lbl = _sizing_lookup(hmm_dec, ts)
    v_mult, v_lbl = _sizing_lookup(vol_dec, ts)
    actives = [m for m in (h_mult, v_mult) if m is not None]
    combined = min(actives) if actives else SIZE_FAVOURABLE
    return combined, {
        "hmm_regime_label": h_lbl,
        "vol_regime_label": v_lbl,
        "hmm_size_multiplier": h_mult,
        "vol_size_multiplier": v_mult,
    }


# ---------- trade book-keeping ----------------------------------------------

def _open_position(asset, row, setup, direction, base_size, size_per_asset,
                   overlay_mult, slippage, strategy, i, ts, tags: dict):
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
        "hmm_regime_label_at_entry": tags.get("hmm_regime_label"),
        "vol_regime_label_at_entry": tags.get("vol_regime_label"),
        "hmm_size_multiplier_at_entry": tags.get("hmm_size_multiplier"),
        "vol_size_multiplier_at_entry": tags.get("vol_size_multiplier"),
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
        "hmm_regime_label_at_entry": position.get("hmm_regime_label_at_entry"),
        "vol_regime_label_at_entry": position.get("vol_regime_label_at_entry"),
        "hmm_size_multiplier_at_entry": position.get("hmm_size_multiplier_at_entry"),
        "vol_size_multiplier_at_entry": position.get("vol_size_multiplier_at_entry"),
    }
    return trade, net


# ---------- per-variant walk-forward coordinator ----------------------------

def _run_variant(name, btc_df, eth_df, strategy,
                 long_funding, short_funding,
                 use_hmm: bool, use_vol: bool,
                 hmm_cfg, train_bars, test_bars, embargo_bars,
                 fee, slippage, vol_window=24, max_open=2):
    """Walk-forward parallel coordinator for one Issue #27 variant.

    ``long_funding`` / ``short_funding`` are per-asset dicts of
    decision DataFrames spanning the full common index. They encode
    the FIXED funding hard gate (direction-aware) that all variants
    share.

    ``use_hmm`` / ``use_vol`` toggle the per-fold sizing overlays.
    The combined multiplier is the MIN of the active overlay
    multipliers (so stacked overlays only reduce exposure).
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

        # ---- per-fold per-asset overlay decisions ----
        hmm_dec = {a: None for a in ASSETS}
        vol_dec = {a: None for a in ASSETS}
        for a in ASSETS:
            tr_df = asset_df[a].iloc[cursor:train_hi]
            tr_ind = asset_ind[a].iloc[cursor:train_hi]
            te_df = asset_df[a].iloc[test_lo:test_hi]
            te_ind = asset_ind[a].iloc[test_lo:test_hi]
            if use_hmm:
                hmm_dec[a] = _build_hmm_sizing(
                    a, tr_df, tr_ind, te_df, te_ind,
                    hmm_cfg, strategy, fee, slippage,
                )
            if use_vol:
                vol_dec[a] = _build_vol_sizing(tr_df, te_df, window=vol_window)

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

                    # 1) Try LONG; gate by funding (hard).
                    setup_l = signals.long_entry(row, strategy)
                    if setup_l:
                        if _funding_allowed(long_funding[asset], ts):
                            mult, tags = _combined_multiplier(
                                hmm_dec[asset] if use_hmm else None,
                                vol_dec[asset] if use_vol else None,
                                ts,
                            )
                            if mult > 0:
                                positions[asset] = _open_position(
                                    asset, row, setup_l, "long",
                                    base_size, size_per_asset, mult, slippage,
                                    strategy, i, ts, tags,
                                )
                        continue

                    # 2) Try SHORT; gate by funding (hard, direction-aware).
                    setup_s = signals.short_entry(row, strategy)
                    if setup_s:
                        if _funding_allowed(short_funding[asset], ts):
                            mult, tags = _combined_multiplier(
                                hmm_dec[asset] if use_hmm else None,
                                vol_dec[asset] if use_vol else None,
                                ts,
                            )
                            if mult > 0:
                                positions[asset] = _open_position(
                                    asset, row, setup_s, "short",
                                    base_size, size_per_asset, mult, slippage,
                                    strategy, i, ts, tags,
                                )

            cur_open = sum(1 for p in positions.values() if p is not None)
            fold_max_concurrent = max(fold_max_concurrent, cur_open)

        # Close any open positions at fold end.
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
    mean_overlay_mult = (np.mean([t["overlay_size_multiplier"] for t in all_trades])
                         if all_trades else 1.0)
    return_per_exposure = oos["total_return"] / mean_overlay_mult if mean_overlay_mult > 0 else 0.0
    dd_per_exposure = oos["max_drawdown"] / mean_overlay_mult if mean_overlay_mult > 0 else 0.0
    log(f"  -> trades={oos['trades']} (L={n_long}, S={n_short})  "
        f"ret={oos['total_return']*100:+.2f}%  DD={oos['max_drawdown']*100:.2f}%  "
        f"PF={pf_s}  win={oos['win_rate']*100:.1f}%  "
        f"meanMult={mean_overlay_mult:.3f}  "
        f"ret/exp={return_per_exposure*100:+.2f}%  "
        f"DD/exp={dd_per_exposure*100:.2f}%  "
        f"folds+={fold_pos}/{len(folds)}  max_open={max_concurrent}")

    by_regime = _regime_breakdown(all_trades)

    return {
        "name": name, "oos": oos, "folds": folds, "trades": all_trades,
        "fold_pos": fold_pos, "fold_returns": fold_returns,
        "max_concurrent": max_concurrent, "n_long": n_long, "n_short": n_short,
        "mean_overlay_mult": mean_overlay_mult,
        "return_per_exposure": return_per_exposure,
        "dd_per_exposure": dd_per_exposure,
        "by_regime": by_regime,
    }


def _regime_breakdown(trades: list[dict]) -> dict:
    """Return per-tag {label: {trades, win_rate, total_return}} dicts for
    the HMM regime label at entry and the volatility band at entry."""
    by_hmm: dict[str, dict] = {}
    by_vol: dict[str, dict] = {}
    for t in trades:
        for tag_field, target in (("hmm_regime_label_at_entry", by_hmm),
                                  ("vol_regime_label_at_entry", by_vol)):
            label = t.get(tag_field) or "n/a"
            slot = target.setdefault(label, {"trades": 0, "wins": 0,
                                              "total_return": 0.0})
            slot["trades"] += 1
            slot["total_return"] += t["ret"]
            if t["ret"] > 0:
                slot["wins"] += 1
    for d in (by_hmm, by_vol):
        for label, slot in d.items():
            slot["win_rate"] = slot["wins"] / slot["trades"] if slot["trades"] else 0.0
    return {"by_hmm_regime": by_hmm, "by_vol_regime": by_vol}


# ---------- main -----------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-months", type=int, default=48)
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--long-short-strategy",
                    default=str(STATE_DIR / "strategy_supertrend_long_short.yaml"))
    ap.add_argument("--hmm-config",
                    default=str(STATE_DIR / "hmm_regime.yaml"))
    ap.add_argument("--train-bars", type=int, default=1440)
    ap.add_argument("--test-bars", type=int, default=360)
    ap.add_argument("--embargo-bars", type=int, default=6)
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--vol-window", type=int, default=24,
                    help="bars for realised vol (24 = 4 days at 4h)")
    ap.add_argument("--out-dir", default="results")
    args = ap.parse_args()

    if not hmmr.available():
        log(f"[red]{hmmr.INSTALL_HINT}[/red]")
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"adaptive_sizing_comparison_{ts}.csv"
    md_path = out_dir / f"adaptive_sizing_comparison_{ts}.md"
    trades_path = out_dir / f"trades_adaptive_sizing_{ts}.csv"

    ls_strategy = yaml.safe_load(open(args.long_short_strategy))
    hmm_cfg = yaml.safe_load(open(args.hmm_config))["hmm_regime"]

    log(f"loading BTC + ETH {args.n_months}mo …")
    btc_df = data_mod.resample(
        data_mod.load_klines("BTCUSDT", n_months=args.n_months), args.timeframe)
    eth_df = data_mod.resample(
        data_mod.load_klines("ETHUSDT", n_months=args.n_months), args.timeframe)
    common = btc_df.index.intersection(eth_df.index)
    btc_df = btc_df.loc[common]
    eth_df = eth_df.loc[common]
    log(f"aligned bars: {len(common)}  span: {common[0].date()} -> {common[-1].date()}")

    # ---- Build the fixed funding hard gate (direction-aware) ----
    log("building funding hard gate (long: block at p>=95; short: block at p<=5) …")
    long_funding = {
        "BTCUSDT": _build_funding_gate(common, "BTCUSDT", args.n_months, "long"),
        "ETHUSDT": _build_funding_gate(common, "ETHUSDT", args.n_months, "long"),
    }
    short_funding = {
        "BTCUSDT": _build_funding_gate(common, "BTCUSDT", args.n_months, "short"),
        "ETHUSDT": _build_funding_gate(common, "ETHUSDT", args.n_months, "short"),
    }

    results = []
    common_args = dict(
        btc_df=btc_df, eth_df=eth_df, strategy=ls_strategy,
        long_funding=long_funding, short_funding=short_funding,
        hmm_cfg=hmm_cfg,
        train_bars=args.train_bars, test_bars=args.test_bars,
        embargo_bars=args.embargo_bars,
        fee=args.fee, slippage=args.slippage, vol_window=args.vol_window,
    )

    results.append(_run_variant("baseline_funding_only",
                                use_hmm=False, use_vol=False, **common_args))
    results.append(_run_variant("hmm_sizing",
                                use_hmm=True, use_vol=False, **common_args))
    results.append(_run_variant("vol_sizing",
                                use_hmm=False, use_vol=True, **common_args))
    results.append(_run_variant("hmm_plus_vol_sizing",
                                use_hmm=True, use_vol=True, **common_args))

    # ---- artifacts ----
    rows = []
    all_trades = []
    baseline = results[0]
    for r in results:
        o = r["oos"]
        all_trades.extend(r["trades"])
        fr = r["fold_returns"]
        fold_std = float(np.std(fr)) if fr else 0.0
        rows.append({
            "variant": r["name"],
            "trades": o["trades"],
            "n_long": r["n_long"],
            "n_short": r["n_short"],
            "total_return": o["total_return"],
            "max_drawdown": o["max_drawdown"],
            "profit_factor": (o["profit_factor"]
                              if o["profit_factor"] != float("inf") else 9999.0),
            "win_rate": o["win_rate"],
            "mean_overlay_multiplier": r["mean_overlay_mult"],
            "return_per_exposure": r["return_per_exposure"],
            "dd_per_exposure": r["dd_per_exposure"],
            "n_folds": len(r["folds"]),
            "fold_positive": f"{r['fold_pos']}/{len(r['folds'])}",
            "fold_return_std": fold_std,
            "max_concurrent": r["max_concurrent"],
            "trade_count_delta_vs_baseline": (
                (o["trades"] - baseline["oos"]["trades"]) / baseline["oos"]["trades"]
                if baseline["oos"]["trades"] else 0.0),
        })

    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log(f"wrote CSV → {csv_path}")

    md = [
        f"# Adaptive sizing comparison (Issue #27) — {ts}",
        "",
        f"- long-short strategy: `{args.long_short_strategy}`",
        f"- HMM config: `{args.hmm_config}` (per-asset per-fold fit; train-only mapping; this script uses its own Issue #27 sizing mapping)",
        f"- vol window: {args.vol_window} bars (4 days at 4h)",
        f"- funding hard gate: long-block at p>=95, short-block at p<=5 (Issue #20 adopted) — applied to ALL variants",
        f"- universe: BTC/USDT + ETH/USDT (parallel)",
        f"- {args.n_months}mo span: {common[0].date()} -> {common[-1].date()} ({len(common)} bars)",
        f"- walk-forward: train={args.train_bars} / test={args.test_bars} / embargo={args.embargo_bars}",
        f"- costs: fee={args.fee}/side, slippage={args.slippage}",
        "",
        "**Sizing concept** (locked, Issue #27): favourable=1.00, neutral=0.50, adverse/high-vol=0.25.",
        "",
        "**Adoption criterion** (Issue #27): reduce DD or improve PF without materially reducing trade count.",
        "",
        "| variant | folds | n | L | S | OOS return | max DD | PF | win% | mean mult | ret/exp | DD/exp | trade Δ vs base | folds+ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        pf = r["profit_factor"]
        pf_s = "inf" if pf == 9999.0 else f"{pf:.2f}"
        md.append(
            f"| `{r['variant']}` | {r['n_folds']} | {r['trades']} | "
            f"{r['n_long']} | {r['n_short']} | "
            f"{r['total_return']*100:+.2f}% | "
            f"{r['max_drawdown']*100:.2f}% | {pf_s} | "
            f"{r['win_rate']*100:.1f}% | "
            f"{r['mean_overlay_multiplier']:.3f} | "
            f"{r['return_per_exposure']*100:+.2f}% | "
            f"{r['dd_per_exposure']*100:.2f}% | "
            f"{r['trade_count_delta_vs_baseline']*100:+.1f}% | "
            f"{r['fold_positive']} |"
        )

    # By-regime breakdown
    md.append("")
    md.append("## Performance by HMM regime label at entry")
    md.append("")
    md.append("| variant | regime | trades | win% | total return |")
    md.append("|---|---|---:|---:|---:|")
    for r in results:
        for label, slot in r["by_regime"]["by_hmm_regime"].items():
            md.append(f"| `{r['name']}` | {label} | {slot['trades']} | "
                      f"{slot['win_rate']*100:.1f}% | "
                      f"{slot['total_return']*100:+.2f}% |")
    md.append("")
    md.append("## Performance by volatility band at entry")
    md.append("")
    md.append("| variant | band | trades | win% | total return |")
    md.append("|---|---|---:|---:|---:|")
    for r in results:
        for label, slot in r["by_regime"]["by_vol_regime"].items():
            md.append(f"| `{r['name']}` | {label} | {slot['trades']} | "
                      f"{slot['win_rate']*100:.1f}% | "
                      f"{slot['total_return']*100:+.2f}% |")

    with open(md_path, "w") as fh:
        fh.write("\n".join(md))
    log(f"wrote MD → {md_path}")

    base_cols = [
        "_variant", "asset", "entry_ts", "exit_ts", "setup_name",
        "direction", "entry_price", "exit_price",
        "gross_return_pct", "net_return_pct",
        "size_per_asset", "overlay_size_multiplier",
        "position_size_effective", "exit_reason", "holding_bars",
        "hmm_regime_label_at_entry", "vol_regime_label_at_entry",
        "hmm_size_multiplier_at_entry", "vol_size_multiplier_at_entry",
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
    log("done. No live config modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
