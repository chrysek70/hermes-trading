"""BTC/ETH relative-strength decisions module.

Computes RS features from BTC and a context asset (ETH) and emits a
decisions DataFrame compatible with ``backtest._attach_decisions_df``:
columns ``long_allowed``, ``size_multiplier``, ``raw_state``,
``stable_state``, ``regime_score``, ``allowed_setups``.

Causality: all features at bar ``i`` use closes through bar ``i`` only.
The 30-bar return is ``close[i] / close[i-30] - 1`` (no future info).
The ratio EMA is computed as a standard recursive EMA over the ratio
series, which uses past values only.

ETH is not traded; it is market context that gates / sizes BTC entries.
"""
from __future__ import annotations

import pandas as pd


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def compute_features(
    btc_df: pd.DataFrame,
    eth_df: pd.DataFrame,
    lookback_bars: int = 30,
    ratio_ema: int = 30,
) -> pd.DataFrame:
    idx = btc_df.index.intersection(eth_df.index)
    btc = btc_df.loc[idx, "close"]
    eth = eth_df.loc[idx, "close"]

    btc_return_n = btc / btc.shift(lookback_bars) - 1.0
    eth_return_n = eth / eth.shift(lookback_bars) - 1.0
    btc_minus_eth_return_n = btc_return_n - eth_return_n
    btc_eth_ratio = btc / eth
    btc_eth_ratio_ema = _ema(btc_eth_ratio, ratio_ema)
    btc_eth_ratio_slope = btc_eth_ratio_ema - btc_eth_ratio_ema.shift(1)

    return pd.DataFrame({
        "btc_return_n": btc_return_n,
        "eth_return_n": eth_return_n,
        "btc_minus_eth_return_n": btc_minus_eth_return_n,
        "btc_eth_ratio": btc_eth_ratio,
        "btc_eth_ratio_ema": btc_eth_ratio_ema,
        "btc_eth_ratio_slope": btc_eth_ratio_slope,
    }, index=idx)


def compute_multi_asset_features(
    btc_df: pd.DataFrame,
    eth_df: pd.DataFrame,
    lookback_bars: int = 30,
    ratio_ema: int = 30,
) -> pd.DataFrame:
    """Like ``compute_features`` but also returns the ETH-perspective
    ratio and its EMA, so symmetric per-asset decisions can be built.

    EMA(1/x) != 1/EMA(x), so the ETH/BTC ratio EMA must be computed
    independently from the BTC/ETH ratio EMA — not derived as 1/btc_ema.
    """
    idx = btc_df.index.intersection(eth_df.index)
    btc = btc_df.loc[idx, "close"]
    eth = eth_df.loc[idx, "close"]

    btc_return_n = btc / btc.shift(lookback_bars) - 1.0
    eth_return_n = eth / eth.shift(lookback_bars) - 1.0
    btc_minus_eth_return_n = btc_return_n - eth_return_n

    btc_eth_ratio = btc / eth
    btc_eth_ratio_ema = _ema(btc_eth_ratio, ratio_ema)
    eth_btc_ratio = eth / btc
    eth_btc_ratio_ema = _ema(eth_btc_ratio, ratio_ema)

    return pd.DataFrame({
        "btc_return_n": btc_return_n,
        "eth_return_n": eth_return_n,
        "btc_minus_eth_return_n": btc_minus_eth_return_n,
        "btc_eth_ratio": btc_eth_ratio,
        "btc_eth_ratio_ema": btc_eth_ratio_ema,
        "eth_btc_ratio": eth_btc_ratio,
        "eth_btc_ratio_ema": eth_btc_ratio_ema,
    }, index=idx)


def build_asset_decisions(
    features: pd.DataFrame,
    asset: str,
    mode: str = "sizing",
    min_return_advantage: float = 0.0,
) -> pd.DataFrame:
    """Build a single-asset decisions_df from the multi-asset features.

    ``asset`` is ``"btc"`` or ``"eth"``; the gates are symmetric:
      - BTC long: btc_minus_eth_return >= 0 AND btc_eth_ratio > btc_eth_ema
      - ETH long: eth_minus_btc_return >= 0 AND eth_btc_ratio > eth_btc_ema
    """
    if asset == "btc":
        return_diff = features["btc_minus_eth_return_n"]
        ratio_gate = features["btc_eth_ratio"] > features["btc_eth_ratio_ema"]
        warmup = (features["btc_minus_eth_return_n"].isna()
                  | features["btc_eth_ratio_ema"].isna())
    elif asset == "eth":
        return_diff = -features["btc_minus_eth_return_n"]
        ratio_gate = features["eth_btc_ratio"] > features["eth_btc_ratio_ema"]
        warmup = (features["btc_minus_eth_return_n"].isna()
                  | features["eth_btc_ratio_ema"].isna())
    else:
        raise ValueError(f"unknown asset: {asset!r}")

    return_gate = return_diff >= min_return_advantage

    if mode == "filter":
        long_allowed = (return_gate & ratio_gate)
        size_mult = pd.Series(1.0, index=features.index)
        long_allowed = long_allowed.where(~warmup, True)
    elif mode == "sizing":
        pass_count = return_gate.astype(int) + ratio_gate.astype(int)
        size_mult = pass_count.map({2: 1.0, 1: 0.5, 0: 0.0}).astype(float)
        long_allowed = size_mult > 0.0
        size_mult = size_mult.where(~warmup, 1.0)
        long_allowed = long_allowed.where(~warmup, True)
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    def _label(rg: bool, rp: bool, w: bool) -> str:
        if w:
            return "rs_warmup"
        if rg and rp:
            return "rs_strong"
        if rg or rp:
            return "rs_partial"
        return "rs_weak"

    raw_state = pd.Series(
        [_label(bool(r), bool(rp), bool(w))
         for r, rp, w in zip(return_gate.values, ratio_gate.values, warmup.values)],
        index=features.index,
    )

    return pd.DataFrame({
        "long_allowed": long_allowed.astype(bool),
        "size_multiplier": size_mult.astype(float),
        "raw_state": raw_state,
        "stable_state": raw_state,
        "regime_score": size_mult.astype(float),
        "allowed_setups": pd.Series([None] * len(features), index=features.index, dtype=object),
    }, index=features.index)


def build_decisions(
    btc_df: pd.DataFrame,
    eth_df: pd.DataFrame,
    mode: str,
    lookback_bars: int = 30,
    ratio_ema: int = 30,
    min_btc_minus_eth_return: float = 0.0,
    require_ratio_above_ema: bool = True,
) -> pd.DataFrame:
    """Build a decisions DataFrame for the BTC index.

    ``mode``:
      - ``"filter"``: long_allowed = (BTC stronger) AND (ratio > EMA);
        size_multiplier = 1.0 when allowed.
      - ``"sizing"``: long_allowed = True; size_multiplier =
        1.0 if both pass / 0.5 if one passes / 0.0 if neither.
    """
    feats = compute_features(btc_df, eth_df, lookback_bars, ratio_ema)

    return_gate = feats["btc_minus_eth_return_n"] >= min_btc_minus_eth_return
    ratio_gate = feats["btc_eth_ratio"] > feats["btc_eth_ratio_ema"]

    # Until both windows have produced finite values, treat the bar as
    # "unknown" — long_allowed defaults to True (no info, no veto) but
    # size_multiplier is 1.0 (no size cut from missing context). This
    # matches the convention used by the neutral Markov attacher.
    warmup_mask = feats["btc_minus_eth_return_n"].isna() | feats["btc_eth_ratio_ema"].isna()

    if mode == "filter":
        long_allowed = (return_gate & ratio_gate)
        size_mult = pd.Series(1.0, index=feats.index)
        # Warmup: leave long_allowed True (no info → no veto). This is
        # the same convention as a neutral / disabled regime layer.
        long_allowed = long_allowed.where(~warmup_mask, True)
    elif mode == "sizing":
        # Sizing rule from the experiment spec:
        # both pass → 1.0; one passes → 0.5; neither → 0.0
        pass_count = return_gate.astype(int) + ratio_gate.astype(int)
        size_mult = pass_count.map({2: 1.0, 1: 0.5, 0: 0.0}).astype(float)
        long_allowed = size_mult > 0.0
        # Warmup: full size, allowed (no info → no penalty).
        size_mult = size_mult.where(~warmup_mask, 1.0)
        long_allowed = long_allowed.where(~warmup_mask, True)
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    def _label(rg: bool, ratio_pass: bool, warm: bool) -> str:
        if warm:
            return "rs_warmup"
        if rg and ratio_pass:
            return "rs_strong"
        if rg or ratio_pass:
            return "rs_partial"
        return "rs_weak"

    raw_state = pd.Series(
        [_label(bool(rg), bool(rp), bool(wm))
         for rg, rp, wm in zip(return_gate.values, ratio_gate.values, warmup_mask.values)],
        index=feats.index,
    )

    decisions = pd.DataFrame({
        "long_allowed": long_allowed.astype(bool),
        "size_multiplier": size_mult.astype(float),
        "raw_state": raw_state,
        "stable_state": raw_state,
        "regime_score": size_mult.astype(float),
        "allowed_setups": pd.Series([None] * len(feats), index=feats.index, dtype=object),
    }, index=feats.index)
    return decisions
