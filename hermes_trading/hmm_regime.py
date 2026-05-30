"""Optional 2-state Hidden Markov regime detector.

Soft alternative to the hand-defined 6-state Markov model. EM-fits a
Gaussian HMM on causal features (log-return, realised vol, ATR%,
EMA slope, SuperTrend distance) per walk-forward train fold; maps the
raw HMM states to {favorable, adverse} using train-only statistics;
emits a per-bar P(favorable) / P(adverse) that the engine uses as a
decisions_df overlay (size_multiplier + long_allowed).

Why "optional":
  - `hmmlearn` is a heavy dependency (scikit-learn, scipy).
  - Backtests, walk-forward, and the live worker must keep running if
    `hmmlearn` is not installed.
  - `HMMRegimeDetector.available()` returns False when the import
    failed; callers that hit this branch should fall back to neutral
    decisions and log a clear install hint.

Install: `uv add hmmlearn` (already added to pyproject.toml).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:  # pragma: no cover - import-time guard
    from hmmlearn.hmm import GaussianHMM  # type: ignore
    _HMMLEARN_AVAILABLE = True
    _HMMLEARN_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001
    GaussianHMM = None  # type: ignore
    _HMMLEARN_AVAILABLE = False
    _HMMLEARN_IMPORT_ERROR = exc


INSTALL_HINT = (
    "hmmlearn is not installed. Run `uv add hmmlearn` (or `pip install hmmlearn`) "
    "to enable the optional HMM regime detector. Existing backtests / live "
    "worker do not need this dependency."
)


def available() -> bool:
    """True if hmmlearn imported successfully and HMMRegimeDetector can be built."""
    return _HMMLEARN_AVAILABLE


def import_error() -> Exception | None:
    return _HMMLEARN_IMPORT_ERROR


DEFAULT_CONFIG = {
    "enabled": True,
    "n_states": 2,
    "covariance_type": "full",
    "n_iter": 200,
    "random_state": 42,
    "features": [
        "log_return",
        "realized_vol_24",
        "atr_pct",
        "ema50_slope",
        "supertrend_distance_pct",
    ],
    "sizing": {
        "favorable_prob_full": 0.70,
        "favorable_prob_half": 0.55,
        "adverse_prob_block": 0.70,
        "min_size": 0.0,
        "half_size": 0.5,
        "full_size": 1.0,
    },
    "min_trades_for_setup_mapping": 5,
}


class HMMRegimeDetector:
    """Two-state Gaussian HMM regime detector.

    Causal feature engineering: every column at bar t uses only data
    through bar t. ATR% and SuperTrend distance require the indicator
    DataFrame produced by `signals.compute_indicators` (so the same
    SuperTrend(10, 3) the strategy uses).
    """

    def __init__(self, config: dict | None = None):
        if not _HMMLEARN_AVAILABLE:
            raise RuntimeError(INSTALL_HINT)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(config or {})
        sizing = dict(DEFAULT_CONFIG["sizing"])
        sizing.update((config or {}).get("sizing", {}) or {})
        cfg["sizing"] = sizing
        self.cfg = cfg
        self.model: Any = None
        self.state_labels_: dict[int, str] = {}
        self.state_stats_: dict[int, dict] = {}
        self.feature_means_: dict[str, float] = {}
        self.feature_stds_: dict[str, float] = {}

    # ---- features --------------------------------------------------------

    def build_features(self, df: pd.DataFrame,
                       indicators: pd.DataFrame | None = None) -> pd.DataFrame:
        """Build the feature matrix used by fit / predict_proba.

        ``df`` is OHLCV. ``indicators`` is the strategy indicator frame
        from ``signals.compute_indicators`` and must already contain
        ``ema_fast`` (50 by default), ``atr``, ``supertrend_line``.
        Strictly causal — no future bars used at row t.
        """
        out = pd.DataFrame(index=df.index)
        close = df["close"]
        log_ret = np.log(close / close.shift(1))
        out["log_return"] = log_ret
        out["realized_vol_24"] = log_ret.rolling(24, min_periods=24).std()
        if indicators is not None and "atr" in indicators.columns:
            out["atr_pct"] = indicators["atr"] / close
        else:
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - close.shift(1)).abs(),
                (df["low"] - close.shift(1)).abs(),
            ], axis=1).max(axis=1)
            out["atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False,
                                    min_periods=14).mean() / close
        if indicators is not None and "ema_fast" in indicators.columns:
            ema50 = indicators["ema_fast"]
        else:
            ema50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
        out["ema50_slope"] = (ema50 - ema50.shift(1)) / ema50.shift(1)
        if indicators is not None and "supertrend_line" in indicators.columns:
            st_line = indicators["supertrend_line"]
        else:
            st_line = pd.Series(np.nan, index=df.index)
        out["supertrend_distance_pct"] = (close - st_line) / close
        return out

    def _select_features(self, features: pd.DataFrame) -> np.ndarray:
        cols = [c for c in self.cfg["features"] if c in features.columns]
        if not cols:
            raise RuntimeError(f"none of the configured HMM features are present in the frame: {self.cfg['features']}")
        sub = features[cols].dropna()
        return sub.to_numpy(), sub.index, cols

    # ---- fit / predict ---------------------------------------------------

    def fit(self, train_df: pd.DataFrame,
            indicators: pd.DataFrame | None = None,
            train_features: pd.DataFrame | None = None) -> "HMMRegimeDetector":
        feats = train_features if train_features is not None else self.build_features(train_df, indicators)
        X, idx, used_cols = self._select_features(feats)
        # standardise — store mean/std from train only, apply to test later
        mu = X.mean(axis=0)
        sd = X.std(axis=0)
        sd_safe = np.where(sd < 1e-12, 1.0, sd)
        Xn = (X - mu) / sd_safe
        self.feature_means_ = dict(zip(used_cols, mu.tolist()))
        self.feature_stds_ = dict(zip(used_cols, sd.tolist()))

        self.model = GaussianHMM(
            n_components=int(self.cfg["n_states"]),
            covariance_type=str(self.cfg["covariance_type"]),
            n_iter=int(self.cfg["n_iter"]),
            random_state=int(self.cfg["random_state"]),
        )
        self.model.fit(Xn)
        # train-time per-state stats for mapping (run after fit)
        train_states = self.model.predict(Xn)
        self._compute_state_stats(train_df.loc[idx], train_states, feats.loc[idx])
        return self

    def predict_proba(self, df: pd.DataFrame,
                      indicators: pd.DataFrame | None = None,
                      features: pd.DataFrame | None = None) -> pd.DataFrame:
        if self.model is None:
            raise RuntimeError("HMMRegimeDetector.predict_proba called before fit")
        feats = features if features is not None else self.build_features(df, indicators)
        cols = list(self.feature_means_.keys())
        sub = feats[cols].dropna()
        mu = np.array([self.feature_means_[c] for c in cols])
        sd = np.array([self.feature_stds_[c] for c in cols])
        sd_safe = np.where(sd < 1e-12, 1.0, sd)
        Xn = (sub.to_numpy() - mu) / sd_safe
        proba = self.model.predict_proba(Xn)  # (n, n_states)
        out = pd.DataFrame(proba, index=sub.index,
                           columns=[f"p_state_{i}" for i in range(proba.shape[1])])
        out = out.reindex(df.index)
        return out

    # ---- state mapping ---------------------------------------------------

    def _compute_state_stats(self, train_df: pd.DataFrame,
                             train_states: np.ndarray,
                             train_features: pd.DataFrame) -> None:
        stats: dict[int, dict] = {}
        log_ret = train_features["log_return"]
        vol = train_features["realized_vol_24"]
        atrp = train_features["atr_pct"]
        for s in range(int(self.cfg["n_states"])):
            mask = (pd.Series(train_states, index=train_features.dropna().index) == s).reindex(train_features.index).fillna(False)
            ret_s = log_ret[mask]
            vol_s = vol[mask]
            atr_s = atrp[mask]
            stats[s] = {
                "n_bars": int(mask.sum()),
                "mean_log_return": float(ret_s.mean()) if len(ret_s) else 0.0,
                "mean_realized_vol": float(vol_s.mean()) if len(vol_s) else 0.0,
                "mean_atr_pct": float(atr_s.mean()) if len(atr_s) else 0.0,
            }
        self.state_stats_ = stats

    def map_states(self, train_df: pd.DataFrame | None = None,
                   train_trades: list[dict] | None = None) -> dict[int, str]:
        """Decide which HMM state is "favorable" and which is "adverse".

        Primary criterion (when ``train_trades`` is provided and has
        enough samples per state): SuperTrend training expectancy per
        state — favorable = higher expectancy.

        Fallback (no trades or too few): lower realised volatility =
        favorable.
        """
        n = int(self.cfg["n_states"])
        if n != 2:
            # generic mapping: rank by mean return - vol penalty
            ranks = sorted(
                self.state_stats_.items(),
                key=lambda kv: (kv[1]["mean_log_return"] - 0.5 * kv[1]["mean_realized_vol"]),
                reverse=True,
            )
            labels = {}
            for i, (s, _) in enumerate(ranks):
                labels[s] = "favorable" if i == 0 else "adverse"
            self.state_labels_ = labels
            return labels

        min_n = int(self.cfg.get("min_trades_for_setup_mapping", 5))
        used_setup_mapping = False
        if train_trades:
            per_state_rets: dict[int, list[float]] = {s: [] for s in range(n)}
            for t in train_trades:
                s = t.get("hmm_state_at_entry")
                if s is None:
                    continue
                per_state_rets[int(s)].append(float(t.get("ret", 0.0)))
            if all(len(v) >= min_n for v in per_state_rets.values()):
                # use train expectancy as primary
                exp_by_state = {s: float(np.mean(v)) for s, v in per_state_rets.items()}
                favorable = max(exp_by_state, key=exp_by_state.get)
                used_setup_mapping = True
            else:
                favorable = None  # type: ignore
        else:
            favorable = None  # type: ignore

        if not used_setup_mapping:
            # fall back to vol-based mapping: lower realised vol => favorable
            vol_by_state = {s: v["mean_realized_vol"] for s, v in self.state_stats_.items()}
            favorable = min(vol_by_state, key=vol_by_state.get)
        labels = {}
        for s in range(n):
            labels[s] = "favorable" if s == favorable else "adverse"
        self.state_labels_ = labels
        return labels

    # ---- decisions -------------------------------------------------------

    def decisions(self, df: pd.DataFrame,
                  indicators: pd.DataFrame | None = None,
                  mode: str = "sizing") -> pd.DataFrame:
        """Build a decisions_df compatible with
        ``backtest._attach_decisions_df``.

        ``mode``:
          - "filter": long_allowed gated on P(favorable) >= half thr,
            blocked when P(adverse) >= adverse_prob_block; size 1.0
          - "sizing": full / half / 0 by spec thresholds.
        """
        if self.model is None or not self.state_labels_:
            raise RuntimeError("decisions called before fit + map_states")
        proba = self.predict_proba(df, indicators)
        sizing = self.cfg["sizing"]
        full_thr = float(sizing["favorable_prob_full"])
        half_thr = float(sizing["favorable_prob_half"])
        adverse_block = float(sizing["adverse_prob_block"])
        full_size = float(sizing["full_size"])
        half_size = float(sizing["half_size"])
        min_size = float(sizing["min_size"])

        favorable_state = next(s for s, lbl in self.state_labels_.items()
                               if lbl == "favorable")
        adverse_state = next(s for s, lbl in self.state_labels_.items()
                             if lbl == "adverse")
        p_fav = proba[f"p_state_{favorable_state}"]
        p_adv = proba[f"p_state_{adverse_state}"]

        warmup = p_fav.isna() | p_adv.isna()

        if mode == "filter":
            long_allowed = (p_fav >= half_thr) & ~(p_adv >= adverse_block)
            size_mult = pd.Series(full_size, index=df.index)
            long_allowed = long_allowed.where(~warmup, True)
        elif mode == "sizing":
            size_mult = pd.Series(min_size, index=df.index, dtype=float)
            size_mult = size_mult.mask(p_fav >= half_thr, half_size)
            size_mult = size_mult.mask(p_fav >= full_thr, full_size)
            size_mult = size_mult.mask(p_adv >= adverse_block, min_size)
            long_allowed = size_mult > 0
            size_mult = size_mult.where(~warmup, full_size)
            long_allowed = long_allowed.where(~warmup, True)
        else:
            raise ValueError(f"unknown mode: {mode!r}")

        raw_state = pd.Series("hmm_warmup", index=df.index, dtype=object)
        raw_state = raw_state.where(warmup,
                                    np.where(p_fav >= p_adv, "favorable", "adverse"))

        return pd.DataFrame({
            "long_allowed": long_allowed.astype(bool),
            "size_multiplier": size_mult.astype(float),
            "raw_state": raw_state,
            "stable_state": raw_state,
            "regime_score": p_fav.astype(float),
            "allowed_setups": pd.Series([None] * len(df), index=df.index, dtype=object),
            "p_favorable": p_fav.astype(float),
            "p_adverse": p_adv.astype(float),
        }, index=df.index)
