"""Entrypoint. Resolves the asset from goal.yaml (override with --asset)
and starts the 24/7 loop.
"""
from __future__ import annotations

import argparse
import asyncio

from . import STATE_DIR, load_yaml, log
from . import loop as worker_loop


def main() -> None:
    goal = load_yaml(STATE_DIR / "goal.yaml")
    parser = argparse.ArgumentParser(description="Run the hermes-trading worker.")
    parser.add_argument(
        "--asset",
        default=goal.get("asset", "BTC/USDT"),
        help="ccxt ticker to trade (default: from goal.yaml)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(worker_loop.run(args.asset))
    except KeyboardInterrupt:
        log("shutting down (keyboard interrupt)")


if __name__ == "__main__":
    main()
