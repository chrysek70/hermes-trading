"""Reflection cycle — propose and apply exactly ONE strategy change.

Two modes:

  --fallback   Deterministic rule. Used before Hermes is installed to
               prove the mechanism end to end.
                 * realised return < target  -> loosen entry.threshold by 2
                 * drawdown > max            -> tighten stop_loss_pct by 0.2
               Exactly one variable changes per cycle.

  --hermes     Production mode. Feeds the last 25 trades + current
               strategy to the local ``hermes`` CLI, parses the returned
               hypothesis, and applies it.

Either way: bump version, archive the prior strategy to
state/history/v{NNNN}.yaml, append the hypothesis to hypotheses.jsonl.
"""
from __future__ import annotations

import argparse
import json
import subprocess

from . import (
    STATE_DIR,
    append_jsonl,
    load_yaml,
    log,
    now_iso,
    read_jsonl,
    save_yaml,
)
from . import score as scoring

# The ONLY variables a reflection may change, with sane bounds. The worker
# (loop.py) understands these numeric knobs; structural keys like
# entry.indicator / entry.direction are off-limits because the worker only
# implements RSI-long. An LLM proposal outside this set is rejected and we
# fall back to the deterministic rule.
ALLOWED_VARS: dict[str, tuple[float, float]] = {
    # Long setups (v2 schema)
    "setups.pullback.rsi_threshold": (10.0, 50.0),
    "setups.pullback.exit.target_rsi": (45.0, 90.0),
    "setups.pullback.exit.stop_atr_mult": (0.3, 5.0),
    "setups.breakout.ignition_atr_mult": (0.3, 3.0),
    "setups.breakout.exit.stop_atr_mult": (0.3, 5.0),
    # Short setups (mirror — RSI bounds inverted)
    "shorts.pullback.rsi_threshold": (50.0, 90.0),
    "shorts.pullback.exit.target_rsi": (10.0, 55.0),
    "shorts.pullback.exit.stop_atr_mult": (0.3, 5.0),
    "shorts.breakout.ignition_atr_mult": (0.3, 3.0),
    "shorts.breakout.exit.stop_atr_mult": (0.3, 5.0),
    # Risk
    "risk.position_size_r": (0.1, 2.0),
}


def _get(strategy: dict, dotted: str):
    node = strategy
    for part in dotted.split("."):
        node = node[part]
    return node


def _set(strategy: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    node = strategy
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def _bump_version(old: str) -> str:
    return f"{int(old) + 1:02d}"


def _archive(strategy: dict) -> None:
    old = str(strategy.get("version", "00"))
    dest = STATE_DIR / "history" / f"v{int(old):04d}.yaml"
    save_yaml(dest, strategy)
    log(f"archived prior strategy -> {dest.name}")


def _deterministic_hypothesis(strategy: dict, goal: dict, trades: list[dict]) -> dict:
    rr = scoring.realised_return(trades)
    dd = scoring.max_drawdown(trades)
    target = goal.get("target_return_30d", 0.05)
    max_dd = goal.get("max_drawdown", 0.08)

    if dd > max_dd:
        # Tighten the long breakout stop (the bigger-exposure setup).
        var = "setups.breakout.exit.stop_atr_mult"
        old = _get(strategy, var)
        new = round(max(0.3, float(old) - 0.2), 4)
        reason = (
            f"drawdown {dd:.4f} above max {max_dd:.4f}: tighten breakout stop "
            f"{old} -> {new}"
        )
        predict = "drawdown_down"
    elif rr < target:
        # Loosen long pullback RSI (take more pullback longs).
        var = "setups.pullback.rsi_threshold"
        old = _get(strategy, var)
        new = round(min(50.0, float(old) + 2), 4)
        reason = (
            f"realised return {rr:+.4f} below target {target:+.4f}: loosen "
            f"pullback RSI threshold {old} -> {new}"
        )
        predict = "score_up"
    else:
        var = "setups.pullback.rsi_threshold"
        old = _get(strategy, var)
        new = round(min(50.0, float(old) + 2), 4)
        reason = (
            f"on target (return {rr:+.4f}, drawdown {dd:.4f}): nudge "
            f"pullback RSI threshold {old} -> {new}"
        )
        predict = "score_up"

    return {"variable": var, "from": old, "to": new, "reason": reason, "predict": predict}


def _validate_hypothesis(hyp: dict, strategy: dict) -> dict:
    """Coerce an LLM hypothesis to a safe, applicable change or raise."""
    var = str(hyp.get("variable", "")).strip()
    if var.startswith("strategy."):  # models love to invent this prefix
        var = var[len("strategy.") :]
    if var not in ALLOWED_VARS:
        raise ValueError(f"disallowed variable {var!r} (allowed: {sorted(ALLOWED_VARS)})")
    lo, hi = ALLOWED_VARS[var]
    try:
        new = float(hyp["to"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"'to' is not numeric: {hyp.get('to')!r}")
    if not (lo <= new <= hi):
        raise ValueError(f"{var}={new} out of bounds [{lo}, {hi}]")
    return {
        "variable": var,
        "from": _get(strategy, var),  # authoritative, ignore model's 'from'
        "to": round(new, 4),
        "reason": str(hyp.get("reason", ""))[:300],
        "predict": str(hyp.get("predict", "")),
    }


def _hermes_hypothesis(strategy: dict, trades: list[dict]) -> dict:
    recent = trades[-25:]
    allowed = ", ".join(sorted(ALLOWED_VARS))
    current = {v: _get(strategy, v) for v in ALLOWED_VARS}
    bounds_str = "; ".join(f"{k} {lo}-{hi}" for k, (lo, hi) in sorted(ALLOWED_VARS.items()))
    returns = [round(float(t.get("return", 0.0)), 4) for t in recent]
    prompt = (
        "You tune a paper-trading strategy by changing EXACTLY ONE numeric variable.\n"
        f"You may ONLY change one of: {allowed}.\n"
        "Do NOT change indicators, directions, or schema. Do NOT invent variables. "
        "Do NOT prefix names with 'strategy.'.\n"
        f"Current values: {json.dumps(current)}.\n"
        f"Bounds: {bounds_str}.\n"
        f"Last {len(recent)} trade returns (fraction): {json.dumps(returns)}.\n"
        'Reply with ONLY this JSON, nothing else: '
        '{"variable":"<one allowed name>","to":<number>,'
        '"reason":"<short why>","predict":"score_up|drawdown_down"}.'
    )
    proc = subprocess.run(["hermes", "-z", prompt], capture_output=True, text=True, timeout=300)
    out = proc.stdout.strip()
    start, end = out.find("{"), out.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON in hermes output: {out[:200]!r}")
    return _validate_hypothesis(json.loads(out[start : end + 1]), strategy)


def reflect(mode: str) -> dict | None:
    goal = load_yaml(STATE_DIR / "goal.yaml")
    strategy = load_yaml(STATE_DIR / "strategy.yaml")
    trades = read_jsonl(STATE_DIR / "trades.jsonl")

    if mode == "hermes":
        try:
            hyp = _hermes_hypothesis(strategy, trades)
        except Exception as exc:  # noqa: BLE001 — any LLM/parse/validation failure
            log(f"[yellow]hermes proposal rejected ({exc}); using deterministic rule[/yellow]")
            mode = "fallback"
            hyp = _deterministic_hypothesis(strategy, goal, trades)
    else:
        hyp = _deterministic_hypothesis(strategy, goal, trades)

    _archive(strategy)

    old_version = str(strategy.get("version", "00"))
    _set(strategy, hyp["variable"], hyp["to"])
    strategy["version"] = _bump_version(old_version)
    save_yaml(STATE_DIR / "strategy.yaml", strategy)

    record = {
        "ts": now_iso(),
        "mode": mode,
        "from_version": old_version,
        "to_version": strategy["version"],
        **hyp,
        "n_trades": len(trades),
    }
    append_jsonl(STATE_DIR / "hypotheses.jsonl", record)

    log(
        f"[green]v{old_version} -> v{strategy['version']}[/green] "
        f"changed {hyp['variable']}: {hyp['from']} -> {hyp['to']}"
    )
    log(f"reason: {hyp['reason']}")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one reflection cycle.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--fallback", action="store_true", help="deterministic rule (default)")
    group.add_argument("--hermes", action="store_true", help="call the hermes CLI")
    args = parser.parse_args()

    mode = "hermes" if args.hermes else "fallback"
    reflect(mode)


if __name__ == "__main__":
    main()
