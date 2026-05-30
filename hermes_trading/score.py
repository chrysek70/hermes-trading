"""Score realised trade outcomes against the locked-in goal.

``score(trades, goal) -> float`` in [-1, +1]. Composite of three terms:
realised return vs target, drawdown vs max, Sharpe vs min. Returns below
``failure_below`` are clamped steeply negative.
"""
from __future__ import annotations

import math


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _closed_returns(trades: list[dict]) -> list[float]:
    return [t["return"] for t in trades if t.get("status") == "closed" and "return" in t]


def realised_return(trades: list[dict]) -> float:
    """Compounded return across closed trades, as a fraction."""
    equity = 1.0
    for r in _closed_returns(trades):
        equity *= (1.0 + r)
    return equity - 1.0


def max_drawdown(trades: list[dict]) -> float:
    """Peak-to-trough drawdown of the equity curve, as a positive fraction."""
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in _closed_returns(trades):
        equity *= (1.0 + r)
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak)
    return worst


def sharpe(trades: list[dict]) -> float:
    rets = _closed_returns(trades)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return mean / std


def score(trades: list[dict], goal: dict) -> float:
    target = goal.get("target_return_30d", 0.05)
    max_dd = goal.get("max_drawdown", 0.08)
    min_s = goal.get("min_sharpe", 1.2)
    floor = goal.get("failure_below", -0.04)

    rr = realised_return(trades)
    dd = max_drawdown(trades)
    sh = sharpe(trades)

    ret_score = _clamp(rr / target) if target else 0.0
    dd_score = _clamp(1.0 - dd / max_dd) if max_dd else 0.0
    sharpe_score = _clamp(sh / min_s) if min_s else 0.0

    composite = 0.5 * ret_score + 0.3 * dd_score + 0.2 * sharpe_score

    if rr < floor:
        composite = min(composite, -1.0 + (rr - floor))  # steeply negative

    return _clamp(composite)
