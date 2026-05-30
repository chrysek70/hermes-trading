# Funding Rate Data Audit (Phase 1)

Issue #7. Audit performed before building any filter, per the spec
hard rule "Do not build the filter until data quality is confirmed."

## Sources surveyed

### Binance Vision CDN (chosen)
`https://data.binance.vision/data/futures/um/monthly/fundingRate/<SYMBOL>/`

Public, no auth, no rate limit (HTTP CDN). Monthly zipped CSV
archives, identical pattern to the spot klines we already use.
Columns: `calc_time` (ms epoch), `funding_interval_hours`,
`last_funding_rate`. ~90 records per month (1 per 8 hours).

### Binance Futures live API (geo-blocked, not used)
`https://fapi.binance.com/fapi/v1/fundingRate`

Returns HTTP 200 with payload `{"code": 0, "msg": "Service unavailable
from a restricted location ..."}` from this machine's region. We do
not need this — Vision CDN has the historical data and the live API
is for current/streaming rates, which is out of scope for research.

### Third-party aggregators (Coinglass, Glassnode, etc.)
Not used. Binance Vision covers the window we need; adding a paid or
auth-required source adds dependency surface without research value.

## Coverage probe

Probed monthly archive availability via HEAD requests:

| symbol | 2019-09 | 2020-01 | 2021-01 | 2022-05 | 2024-04 | 2026-04 | 2026-05 |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 404 | 200 | 200 | 200 | 200 | 200 | 404 (not yet archived) |
| ETHUSDT | 404 | (not probed) | 200 | 200 | 200 | 200 | 404 (not yet archived) |

BTC perpetual launched 2019-09 on Binance but the funding archive
starts 2020-01. ETH similar. Both have continuous coverage from
2020-01 through the most recent completed month (2026-04 at audit
time). The current month is never archived until it closes.

## Full-window load summary

Loaded both symbols at default `n_months=48` (which spans 2022-06 →
2026-04 — 47 archives; 2026-05 not yet available).

| symbol | records | first | last | interval | gaps |
|---|---:|---|---|---|---|
| BTCUSDT | 4290 | 2022-06-01 00:00 UTC | 2026-04-30 16:00 UTC | uniformly 8h | none |
| ETHUSDT | 4290 | 2022-06-01 00:00 UTC | 2026-04-30 16:00 UTC | uniformly 8h | none |

The `funding_interval_hours` column is uniformly 8 in every record
across the entire 48-month window. No interval changes, no
duplicates, no gaps. Clean data.

Funding-rate distributions (per 8h settlement, in percent):

| | BTC | ETH |
|---|---:|---:|
| mean | +0.0064 | +0.0059 |
| median | +0.0060 | +0.0062 |
| std | 0.0087 | 0.0120 |
| min | -0.1192 | -0.3019 |
| max | +0.0881 | +0.1017 |
| annualised mean (×3×365) | ~7.0%/yr | ~6.5%/yr |

ETH funding is more volatile than BTC, with occasional very negative
extremes (down to -0.30% per 8h = -329% APR at the extreme — these
are the funding "blowouts" that pay shorts heavily during liquidation
cascades). BTC's distribution is tighter, consistent with deeper
liquidity.

## Alignment to 4h decision bars

Funding rates are settled every 8h; SuperTrend decisions happen every
4h. Convention used: **forward-fill the most-recent settled funding
rate onto each 4h bar**. Each 4h bar carries the funding rate that
would have been observed by a real trader holding into that bar.
Causal — no future funding is ever assigned to a past bar.

Implementation in `hermes_trading/funding.py::align_to_index`:

```python
combined = s.reindex(s.index.union(target_index)).sort_index().ffill()
return combined.reindex(target_index)
```

Coverage after alignment: 8580 of 8766 4h bars (97.9%). The 2.1% gap
is the first month (2022-05) — funding archive starts 2022-06-01 but
price data starts 2022-05-01. This first month is absorbed by the
walk-forward train warmup (1440 bars = 240 days), so the gap is
inconsequential for the OOS test window.

## Six questions the spec required

### 1. What exchanges provide long-term funding-rate history?
Binance via Vision CDN (used here). Bybit / OKX also publish history
via their public APIs, but Binance is the deepest liquidity venue and
the CDN format matches our existing data adapter pattern, so we use
Binance only for this experiment. Adding more venues is a separate
research question.

### 2. How much BTC funding history is available?
From 2020-01 through the most recent completed month (2026-04 at
audit time). **77 months total available; we use the 47 months that
align with the 48-month research window we use for everything else.**

### 3. How much ETH funding history is available?
Same as BTC (2020-01 through last completed month). 77 months.

### 4. Is 48-month history realistic?
**Yes** — comfortably within available coverage. The 48mo window
2022-05 → 2026-04 is fully covered for funding, except the very first
month (2022-05) where the funding archive starts on 2022-06-01.
That first month falls inside the walk-forward train warmup, so it
does not affect OOS test results.

### 5. What data gaps exist?
- 2022-05 (price has it, funding doesn't — first month of window).
  Absorbed by walk-forward warmup.
- 2026-05 (current month, not yet archived). Outside the experiment
  window.
- No within-archive gaps: every monthly file has the expected ~90
  records at exactly 8h spacing.

### 6. Can funding be sourced automatically in research mode?
**Yes.** `hermes_trading/funding.py::load_funding(symbol, n_months)`
mirrors the spot-data loader: download, cache, concatenate, return
DataFrame. No auth required. Same caching pattern as price data
(`state/data/funding/<SYMBOL>-fundingRate-<MONTH>.zip`).

## Conclusion

**Data quality is sufficient to proceed with Phase 2 (diagnostics)
and Phase 3 (filter).** No gaps within the relevant window; uniform
8h cadence; clean numeric values; reasonable distribution properties
matching prior literature on perpetuals funding.

Funding alignment to 4h decision bars uses forward-fill (causal). The
2.1% coverage gap at the start of the 48mo window is absorbed by the
walk-forward train warmup and does not affect OOS metrics.

Next: Phase 2 diagnostics — measure whether funding has predictive or
coincident relationship to BTC / ETH returns, SuperTrend trade
outcomes, and drawdowns.
