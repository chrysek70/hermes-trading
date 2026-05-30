"""Markov-style regime detection — research framework v2.

EXTENDS the original first-order discrete Markov chain; does not remove it.
All v1 methods (``classify_states``, ``fit``, ``transition_matrix``,
``current_state``, ``next_state_probabilities``, ``long_permission_score``)
remain intact for back-compat. The new additions implement the modes,
hysteresis, soft sizing, multi-timeframe scoring, train-only bad-regime
avoidance, and strategy routing specified in the v2 yaml schema.

Modes (yaml ``mode:``):
  - disabled                  : classifier runs only for diagnostics
  - hard_filter               : v1 binary long permission (kept for comparison)
  - soft_sizing               : continuous size_multiplier in [min_score, max_score]
  - bad_regime_avoidance      : reduce/block in states bad on TRAIN
  - regime_features_only      : tag bars/trades; never sizes or gates
  - strategy_routing          : per-state allowed_setups + size_multiplier

Pandas + numpy only. ``hmmlearn`` is intentionally NOT a dependency here;
optional HMM lives in ``hmm_regime.py``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ALL_STATES: list[str] = [
    "up_low_vol",
    "up_high_vol",
    "sideways_low_vol",
    "sideways_high_vol",
    "down_low_vol",
    "down_high_vol",
]


# ===========================================================================
# v1 model (back-compat, unchanged behaviour)
# ===========================================================================


class MarkovRegimeModel:
    """First-order Markov chain over a fixed 6-state alphabet."""

    def __init__(self, config: dict):
        self.cfg = dict(config)
        self.transition_: pd.DataFrame | None = None
        self.states_: list[str] = list(ALL_STATES)

    # ------------------------------------------------------------------ states
    def _state_cfg(self) -> dict:
        # v2 yaml nests state thresholds under ``state:``; v1 yaml had them
        # at the top level. Accept either so existing tests still pass.
        s = self.cfg.get("state") or {}
        return {
            "return_window": int(s.get("return_window", self.cfg.get("return_window", 12))),
            "vol_window": int(s.get("vol_window", self.cfg.get("vol_window", 14))),
            "up_return_threshold": float(s.get("up_return_threshold", self.cfg.get("up_return_threshold", 0.003))),
            "down_return_threshold": float(s.get("down_return_threshold", self.cfg.get("down_return_threshold", -0.003))),
            "high_vol_threshold": float(s.get("high_vol_threshold", self.cfg.get("high_vol_threshold", 0.008))),
            "hysteresis_bars": int(s.get("hysteresis_bars", 1)),
        }

    def classify_states(self, df: pd.DataFrame) -> pd.Series:
        sc = self._state_cfg()
        close = df["close"].astype(float)
        ret = close.pct_change(sc["return_window"])
        log_ret = np.log(close / close.shift(1))
        vol = log_ret.rolling(sc["vol_window"]).std()

        direction = np.where(
            ret > sc["up_return_threshold"], "up",
            np.where(ret < sc["down_return_threshold"], "down", "sideways"),
        )
        vol_class = np.where(vol > sc["high_vol_threshold"], "high_vol", "low_vol")
        combo = pd.Series(direction, index=df.index, dtype=object) + "_" + pd.Series(
            vol_class, index=df.index, dtype=object
        )
        valid = ret.notna() & vol.notna()
        combo = combo.where(valid, other=np.nan)
        return combo

    # ------------------------------------------------------------------- fit
    def fit(self, df: pd.DataFrame) -> None:
        states = self.classify_states(df).dropna()
        min_train = int(self.cfg.get("min_training_bars", 500))
        if len(states) < min_train:
            raise ValueError(
                f"insufficient bars for markov fit: have {len(states)}, need {min_train}"
            )
        lookback = int(self.cfg.get("lookback_bars", 0) or 0)
        if lookback and len(states) > lookback:
            states = states.iloc[-lookback:]

        alpha = float(self.cfg.get("model", {}).get("smoothing_alpha", 0.0))
        counts = pd.DataFrame(alpha, index=ALL_STATES, columns=ALL_STATES, dtype=float)
        s = states.values
        for prev, nxt in zip(s[:-1], s[1:]):
            counts.loc[prev, nxt] += 1.0
        row_sums = counts.sum(axis=1)
        trans = counts.div(row_sums.replace(0.0, np.nan), axis=0).fillna(1.0 / len(ALL_STATES))
        self.transition_ = trans

    # ---------------------------------------------------------------- queries
    def transition_matrix(self) -> pd.DataFrame:
        if self.transition_ is None:
            raise RuntimeError("model not fitted — call fit(df) first")
        return self.transition_

    def current_state(self, df: pd.DataFrame) -> str | None:
        states = self.classify_states(df).dropna()
        return None if states.empty else str(states.iloc[-1])

    def next_state_probabilities(self, df: pd.DataFrame) -> dict:
        if self.transition_ is None:
            return {}
        cs = self.current_state(df)
        if cs is None or cs not in self.transition_.index:
            return {}
        return {k: float(v) for k, v in self.transition_.loc[cs].to_dict().items()}

    # ----------------------------------------- v1 hard-filter permission API
    def long_permission_score(self, df: pd.DataFrame) -> dict:
        """Legacy v1 hard-filter output. Kept verbatim for back-compat."""
        if not self.cfg.get("enabled", False):
            return {
                "enabled": False, "current_state": None, "next_probs": {},
                "long_allowed": True, "score": 1.0,
                "reason": "markov filter disabled",
            }
        cs = self.current_state(df)
        if cs is None:
            return {
                "enabled": True, "current_state": None, "next_probs": {},
                "long_allowed": False, "score": 0.0,
                "reason": "insufficient data to classify regime",
            }
        if self.transition_ is None:
            return {
                "enabled": True, "current_state": cs, "next_probs": {},
                "long_allowed": False, "score": 0.0,
                "reason": "model not fitted",
            }
        next_probs = self.next_state_probabilities(df)
        allowed_set = set(self.cfg.get("allowed_long_states", []))
        score = float(sum(p for s, p in next_probs.items() if s in allowed_set))
        min_prob = float(self.cfg.get("min_prob_same_or_up", 0.5))
        long_allowed = (cs in allowed_set) and (score >= min_prob)
        return {
            "enabled": True, "current_state": cs, "next_probs": next_probs,
            "long_allowed": long_allowed, "score": score,
            "reason": (
                f"Current state {cs!r} is " + ("allowed" if cs in allowed_set else "NOT allowed")
                + f"; P(allowed next states) = {score:.2f} (min={min_prob:.2f})"
            ),
        }


# ===========================================================================
# v2 additions — hysteresis, soft sizing, multi-TF, routing, bad-state set
# ===========================================================================


def apply_hysteresis(raw_states: pd.Series, n_bars: int) -> pd.Series:
    """Smooth a raw state series so a new label must persist N consecutive
    bars before it becomes the stable state. NaN raw rows keep the previous
    stable label (or NaN before bootstrapping)."""
    if n_bars <= 1:
        return raw_states.copy()
    stable_out = [None] * len(raw_states)
    last_stable: str | None = None
    run_state: str | None = None
    run_len = 0
    for i, s in enumerate(raw_states.values):
        if isinstance(s, float) and pd.isna(s):
            stable_out[i] = last_stable
            run_state = None
            run_len = 0
            continue
        s = str(s)
        if s == run_state:
            run_len += 1
        else:
            run_state = s
            run_len = 1
        if last_stable is None:
            last_stable = s              # bootstrap
        elif s != last_stable and run_len >= n_bars:
            last_stable = s
        stable_out[i] = last_stable
    return pd.Series(stable_out, index=raw_states.index, dtype=object)


def _favorable_set(cfg: dict) -> set[str]:
    return set(cfg.get("sizing", {}).get("favorable_states", []))


def _unfavorable_set(cfg: dict) -> set[str]:
    return set(cfg.get("sizing", {}).get("unfavorable_states", []))


def regime_score_raw(stable_state: str | None, next_probs: dict, cfg: dict) -> float:
    """Raw (unclamped) score in roughly [0, 1] for ``soft_sizing`` math:
      score = current_state_weight * 1{stable in favorable}
            + transition_weight    * Σ P(next) into favorable_states
    If stable_state is in the unfavorable set, returns 0 outright.
    """
    fav = _favorable_set(cfg)
    unfav = _unfavorable_set(cfg)
    if stable_state is None:
        return float(cfg.get("sizing", {}).get("neutral_score", 1.0))
    if stable_state in unfav:
        return 0.0
    sizing = cfg.get("sizing", {})
    w_cs = float(sizing.get("current_state_weight", 0.30))
    w_tr = float(sizing.get("transition_weight", 0.70))
    cs_term = 1.0 if stable_state in fav else 0.0
    tr_term = float(sum(p for s, p in (next_probs or {}).items() if s in fav))
    return w_cs * cs_term + w_tr * tr_term


def compute_decisions(
    df: pd.DataFrame,
    model: MarkovRegimeModel,
    cfg: dict,
    bad_state_set: set[str] | None = None,
) -> pd.DataFrame:
    """Produce per-bar regime decisions for the given dataframe.

    Returns a DataFrame indexed like ``df`` with columns:
      raw_state, stable_state, transition_score, regime_score,
      size_multiplier, long_allowed, allowed_setups
    """
    sc = model._state_cfg()
    raw = model.classify_states(df)
    stable = apply_hysteresis(raw, sc["hysteresis_bars"])

    # Per-bar transition score (into favorable next states) lookup
    fav = _favorable_set(cfg)
    if model.transition_ is not None:
        cols = [c for c in model.transition_.columns if c in fav]
        per_state_trans = (
            model.transition_[cols].sum(axis=1) if cols else pd.Series(0.0, index=model.transition_.index)
        )
    else:
        per_state_trans = pd.Series(dtype=float)

    mode = str(cfg.get("mode", "disabled"))
    sizing = cfg.get("sizing", {})
    min_s = float(sizing.get("min_score", 0.25))
    max_s = float(sizing.get("max_score", 1.0))
    neutral = float(sizing.get("neutral_score", 1.0))
    bad_state_set = bad_state_set or set()

    raw_states = raw.tolist()
    stable_states = stable.tolist()
    regime_score = np.full(len(df), neutral, dtype=float)
    size_multiplier = np.full(len(df), 1.0, dtype=float)
    long_allowed = np.ones(len(df), dtype=bool)
    allowed_setups: list[list[str] | None] = [None] * len(df)
    trans_score = np.full(len(df), 0.0, dtype=float)

    bra_cfg = cfg.get("bad_regime_avoidance", {})
    bra_reduce = float(bra_cfg.get("reduce_size_to", 0.25))

    routing_cfg = cfg.get("strategy_routing", {})
    routing_routes = routing_cfg.get("routes", {})

    legacy_allowed = set(cfg.get("allowed_long_states", []))
    hard_min_prob = float(cfg.get("min_prob_same_or_up", 0.55))

    for i in range(len(df)):
        ss = stable_states[i]
        if ss is None or (isinstance(ss, float) and pd.isna(ss)):
            ss = None
        # transition score from this state
        if ss is not None and ss in per_state_trans.index:
            t = float(per_state_trans.loc[ss])
        else:
            t = 0.0
        trans_score[i] = t

        if mode == "disabled":
            regime_score[i] = neutral
            size_multiplier[i] = 1.0
            long_allowed[i] = True

        elif mode == "hard_filter":
            score = t
            in_allowed = (ss is not None) and (ss in legacy_allowed)
            ok = in_allowed and (score >= hard_min_prob)
            regime_score[i] = float(in_allowed) * score
            size_multiplier[i] = 1.0
            long_allowed[i] = bool(ok)

        elif mode == "soft_sizing":
            raw_score = (
                cfg["sizing"]["current_state_weight"] * (1.0 if ss in _favorable_set(cfg) else 0.0)
                + cfg["sizing"]["transition_weight"] * t
            ) if ss is not None else neutral
            if ss is not None and ss in _unfavorable_set(cfg):
                raw_score = 0.0
            clamped = max(min_s, min(max_s, raw_score))
            regime_score[i] = clamped
            size_multiplier[i] = clamped
            long_allowed[i] = True  # size handles it

        elif mode == "bad_regime_avoidance":
            if ss in bad_state_set:
                size_multiplier[i] = bra_reduce
                long_allowed[i] = bra_reduce > 0.0
            else:
                size_multiplier[i] = 1.0
                long_allowed[i] = True
            regime_score[i] = size_multiplier[i]

        elif mode == "regime_features_only":
            size_multiplier[i] = 1.0
            long_allowed[i] = True
            regime_score[i] = (
                regime_score_raw(ss, model.next_state_probabilities(df.iloc[: i + 1]) if False else {}, cfg)
                if ss is not None else neutral
            )
            # cheap version: compute raw score from t directly
            if ss is not None:
                cs_term = 1.0 if ss in _favorable_set(cfg) else 0.0
                regime_score[i] = (
                    cfg["sizing"]["current_state_weight"] * cs_term
                    + cfg["sizing"]["transition_weight"] * t
                )

        elif mode == "routing_sizing":
            # Combined: routes pick which setups may fire; soft-sizing math
            # picks how big. A route with size_multiplier=0.0 still hard-blocks
            # (long_allowed False, size=0).
            route = routing_routes.get(ss) if ss is not None else None
            allowed_setups[i] = list(route.get("allowed_setups", []) or []) if route else None
            if route is not None and float(route.get("size_multiplier", 1.0)) == 0.0:
                size_multiplier[i] = 0.0
                long_allowed[i] = False
                regime_score[i] = 0.0
            else:
                if ss is not None and ss in _unfavorable_set(cfg):
                    raw_score = 0.0
                elif ss is not None:
                    raw_score = (
                        cfg["sizing"]["current_state_weight"] * (1.0 if ss in _favorable_set(cfg) else 0.0)
                        + cfg["sizing"]["transition_weight"] * t
                    )
                else:
                    raw_score = neutral
                clamped = max(min_s, min(max_s, raw_score))
                regime_score[i] = clamped
                size_multiplier[i] = clamped
                long_allowed[i] = clamped > 0.0

        elif mode == "strategy_routing":
            route = routing_routes.get(ss) if ss is not None else None
            if route is None:
                size_multiplier[i] = 1.0
                long_allowed[i] = True
                allowed_setups[i] = None
            else:
                m = float(route.get("size_multiplier", 1.0))
                size_multiplier[i] = m
                long_allowed[i] = m > 0.0
                allowed_setups[i] = list(route.get("allowed_setups", []) or [])
            regime_score[i] = size_multiplier[i]

        else:
            # unknown mode → safe default
            size_multiplier[i] = 1.0
            long_allowed[i] = True
            regime_score[i] = neutral

    return pd.DataFrame(
        {
            "raw_state": raw_states,
            "stable_state": stable_states,
            "transition_score": trans_score,
            "regime_score": regime_score,
            "size_multiplier": size_multiplier,
            "long_allowed": long_allowed,
            "allowed_setups": allowed_setups,
        },
        index=df.index,
    )


# ===========================================================================
# Multi-timeframe scoring
# ===========================================================================


def multi_timeframe_score(
    decisions_by_tf: dict[str, pd.DataFrame],
    weights: dict[str, float],
    decision_index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Combine per-TF decision frames into a single decision frame at the
    decision timeframe's index. Each TF's series is forward-filled onto the
    decision index so a 4h bar inherits the most recent 1d state."""
    combined_score = pd.Series(0.0, index=decision_index)
    combined_size = pd.Series(0.0, index=decision_index)
    combined_long_allowed = pd.Series(True, index=decision_index)
    total_w = 0.0
    snapshot_cols = {}
    for tf, w in weights.items():
        if tf not in decisions_by_tf:
            continue
        d = decisions_by_tf[tf]
        d_aligned_score = d["regime_score"].reindex(decision_index, method="ffill")
        d_aligned_size = d["size_multiplier"].reindex(decision_index, method="ffill")
        d_aligned_allowed = d["long_allowed"].reindex(decision_index, method="ffill").fillna(True)
        d_aligned_state = d["stable_state"].reindex(decision_index, method="ffill")
        combined_score = combined_score + w * d_aligned_score.fillna(0.0)
        combined_size = combined_size + w * d_aligned_size.fillna(0.0)
        combined_long_allowed = combined_long_allowed & d_aligned_allowed.astype(bool)
        total_w += w
        snapshot_cols[f"state_{tf}"] = d_aligned_state
        snapshot_cols[f"score_{tf}"] = d_aligned_score
    if total_w > 0:
        combined_score = combined_score / total_w
        combined_size = combined_size / total_w
    out = pd.DataFrame(
        {
            "regime_score": combined_score,
            "size_multiplier": combined_size,
            "long_allowed": combined_long_allowed,
            "allowed_setups": [None] * len(decision_index),
            "raw_state": snapshot_cols.get(f"state_{list(weights)[0]}", pd.Series(index=decision_index)),
            "stable_state": snapshot_cols.get(f"state_{list(weights)[0]}", pd.Series(index=decision_index)),
            "transition_score": combined_score,
        },
        index=decision_index,
    )
    for k, v in snapshot_cols.items():
        out[k] = v
    return out


# ===========================================================================
# Train-only "bad state" detection
# ===========================================================================


def identify_bad_states_from_train(
    train_trades: list[dict], cfg: dict
) -> tuple[set[str], dict[str, dict]]:
    """Compute per-state PF / expectancy on TRAIN-slice trades, return the
    set of states deemed "bad" by the YAML thresholds. Test slice is never
    consulted.

    Returns (bad_state_set, per_state_metrics).
    """
    bra = cfg.get("bad_regime_avoidance", {})
    min_n = int(bra.get("min_trades_per_state", 20))
    min_pf = float(bra.get("min_profit_factor", 1.0))
    min_exp = float(bra.get("min_expectancy", 0.0))
    block_pf = float(bra.get("block_if_pf_below", 0.7))

    per_state: dict[str, list[float]] = {}
    for t in train_trades:
        s = t.get("markov_stable_state") or t.get("markov_state") or "unknown"
        per_state.setdefault(str(s), []).append(float(t["ret"]))

    bad: set[str] = set()
    metrics: dict[str, dict] = {}
    for s, rets in per_state.items():
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        pf = (sum(wins) / abs(sum(losses))) if sum(losses) != 0 else float("inf")
        exp = float(np.mean(rets)) if rets else 0.0
        metrics[s] = {
            "trades": len(rets),
            "profit_factor": pf,
            "expectancy": exp,
            "win_rate": (len(wins) / len(rets)) if rets else 0.0,
        }
        if len(rets) >= min_n and (pf < min_pf or exp < min_exp):
            bad.add(s)
        if len(rets) >= min_n and pf < block_pf:
            bad.add(s)
    return bad, metrics
