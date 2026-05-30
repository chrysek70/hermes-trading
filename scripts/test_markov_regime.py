#!/usr/bin/env python3
"""Quick validation of the Markov regime model.

Loads cached BTC, resamples to 1h, fits the model, prints the transition
matrix, the current state, next-state probabilities, and the long-permission
score. Forces ``enabled: true`` so the permission output is informative even
when the YAML default is off.

Usage:
    cd ~/hermes-trading && uv run python scripts/test_markov_regime.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hermes_trading import data, STATE_DIR
from hermes_trading.markov_regime import MarkovRegimeModel


def main() -> None:
    cfg_path = STATE_DIR / "markov_regime.yaml"
    with open(cfg_path) as fh:
        cfg = yaml.safe_load(fh)
    cfg["enabled"] = True  # force on for the validation run

    print(
        f"config: return_window={cfg['return_window']} vol_window={cfg['vol_window']} "
        f"up_thr={cfg['up_return_threshold']} dn_thr={cfg['down_return_threshold']} "
        f"hv_thr={cfg['high_vol_threshold']}"
    )

    df = data.load_klines("BTCUSDT", n_months=24)
    df_1h = data.resample(df, "1h")
    print(f"loaded {len(df_1h)} 1h bars  ({df_1h.index[0].date()} -> {df_1h.index[-1].date()})")

    model = MarkovRegimeModel(cfg)

    states = model.classify_states(df_1h).dropna()
    print(f"\nstate distribution over {len(states)} classified bars:")
    counts = states.value_counts()
    total = len(states)
    for s, n in counts.items():
        print(f"  {s:<22} {n:>6}  ({100*n/total:5.1f}%)")

    model.fit(df_1h)
    print(f"\ntransition matrix (rows: current state, columns: next state):")
    print(model.transition_matrix().round(3).to_string())

    cs = model.current_state(df_1h)
    print(f"\ncurrent state: {cs}")
    next_probs = model.next_state_probabilities(df_1h)
    print("next-state probabilities:")
    for s, p in sorted(next_probs.items(), key=lambda kv: -kv[1]):
        print(f"  {s:<22} {p:.3f}")

    perm = model.long_permission_score(df_1h)
    print("\nlong permission score:")
    for k, v in perm.items():
        if k == "next_probs":
            continue
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
