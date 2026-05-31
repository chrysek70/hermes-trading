# Example configs (`state/examples/`)

**These files are examples only. The live worker does NOT load
anything from this directory.**

Each example yaml shows the shape of the config keys that govern
one layer of the architecture (`ARCHITECTURE.md`). They exist so
contributors can see "here's how I'd express a new alpha setup"
or "here's where the risk overlay lives" without grep-spelunking
the real configs.

| file | layer | purpose |
|---|---|---|
| `alpha_signal_example.yaml` | Alpha | shape of a strategy yaml (setups + regime + risk-base + shorts) |
| `risk_overlay_example.yaml` | Risk | shape of an overlay config (funding filter + HMM + RS) |
| `live_execution_example.yaml` | Execution | shape of a live multi-asset config (assets + timeframe + caps + overlay enable / disable) |

To actually run something, point `--config` at one of the real
configs:

```bash
# long-only fallback
uv run python -m hermes_trading.run \
    --config state/live_multiasset.yaml

# adopted long-short + funding-filter candidate
uv run python -m hermes_trading.run \
    --config state/live_multiasset_long_short_funding.yaml --verbose
```
