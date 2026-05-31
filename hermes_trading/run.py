"""Entrypoint.

Two modes:

- single-asset (default, backward compatible): reads ``state/goal.yaml``
  for asset + timeframe, runs ``loop.run(asset)``.
- multi-asset (Issue #16): invoked via ``--config <path-to-yaml>``;
  reads the multi-asset config and runs ``multi_loop.run(cfg_path)``.

Examples:

    # Single-asset (existing behaviour — unchanged)
    uv run python -m hermes_trading.run
    uv run python -m hermes_trading.run --asset ETH/USDT

    # Multi-asset paper worker
    uv run python -m hermes_trading.run --config state/live_multiasset.yaml
"""
from __future__ import annotations

import argparse
import asyncio

from . import STATE_DIR, load_yaml, log
from . import loop as worker_loop
from . import multi_loop as multi_loop_mod


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the hermes-trading paper worker.")
    parser.add_argument(
        "--config",
        default=None,
        help="multi-asset config yaml (e.g. state/live_multiasset.yaml). "
             "If omitted, runs single-asset mode from state/goal.yaml.",
    )
    parser.add_argument(
        "--asset",
        default=None,
        help="single-asset override (ignored when --config is given).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="extra debug fields in per-tick output (e.g. RSI in SuperTrend mode).",
    )
    args = parser.parse_args()

    if args.config:
        log(f"starting multi-asset worker from {args.config}")
        try:
            asyncio.run(multi_loop_mod.run(args.config, verbose=args.verbose))
        except KeyboardInterrupt:
            log("shutting down (keyboard interrupt)")
        return

    goal = load_yaml(STATE_DIR / "goal.yaml")
    asset = args.asset or goal.get("asset", "BTC/USDT")
    try:
        asyncio.run(worker_loop.run(asset, verbose=args.verbose))
    except KeyboardInterrupt:
        log("shutting down (keyboard interrupt)")


if __name__ == "__main__":
    main()
