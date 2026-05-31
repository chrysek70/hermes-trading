# Execution-layer fill realism audit (Issue #28)

Read-only audit of how fills are currently modelled and where live
trading would differ. **No code changes. No live behaviour modified.**

Scope: the three execution paths in the project — research backtest,
replay, and live paper worker — measured against the adopted BTC/ETH
long-short + funding candidate (Issues #20 / #21).

The audit produces:

- A precise map of where slippage / fees are applied (and where they
  are not) in each path.
- Quantitative impact projections for the adopted candidate under
  plausible alternative slippage and fee assumptions.
- A ranked list of which assumptions matter, which can be ignored,
  and what the smallest improvement worth implementing is.

This is the prerequisite for the `vol_sizing` adoption decision left
open at the end of Issue #27.

## 1. Audit areas

### 1.1 Entry fills

**Research backtest** (`hermes_trading/backtest.py:_run_state_machine`):

```python
# long  (backtest.py:154)
position["entry"] = row["close"] * (1 + slippage)
# short (backtest.py:173)
position["entry"] = row["close"] * (1 - slippage)
```

Entries are filled at the **signal bar's close** plus an adverse
slippage (5 bp by default). Slippage is embedded in the fill price;
no separate slippage field is deducted later.

**Replay** (`scripts/replay_live.py`, both modes):

Same convention as the backtest — `entry_fill = last * (1 ± slippage)`
where `last` is the display-bar close. The `--slippage` and `--fee`
flags default to 5 bp / 10 bp respectively.

**Live worker** (`hermes_trading/multi_loop.py:~497-560`):

```python
new_pos = {
    "entry_price": last_price,   # = display_row["close"]
    ...
}
```

Entries are filled at the **display-bar close** with no slippage
adjustment.

**Difference from realistic exchange behaviour**: a market order
placed on a 4h-bar close in reality fills at:
- best-of-book ask (long) or bid (short), plus
- any walk-through-the-book cost for the order size, plus
- any micro-burst slippage during the fill window (a few ms).

For BTC/ETH at $1k notional ($500/asset under the per-asset weight)
on liquid venues (Binance, Kraken, Coinbase) the top-of-book bid-ask
spread is ~1-3 bp; walk-through cost at this size is zero. **5 bp is
a conservative-but-reasonable point estimate** for the research
default. **0 bp (the live model) is unrealistic** even at this size,
because it implies fills at the mid of the close bar rather than the
ask / bid.

### 1.2 Exit fills

**Research backtest** (`backtest.py:187-201`):

```python
# long exit (stop)
exit_fill = position["stop"] * (1 - slippage)
# long exit (non-stop)
exit_fill = row["close"] * (1 - slippage)
# short exit (stop)
exit_fill = position["stop"] * (1 + slippage)
# short exit (non-stop)
exit_fill = row["close"] * (1 + slippage)
```

- **Stop exits** fill at the **stop price** ± slippage.
- **SuperTrend flip / time / regime exits** fill at the **close** ±
  slippage.

The stop detection in `signals.long_exit` triggers when `row["low"]
<= position["stop"]` (the bar's worst intra-bar price), but the
**exit fill assumes the stop price itself, not the bar low**. This
is the single most optimistic assumption in the research model. In
reality, a stop-market order during a gap-down would fill below the
stop, not at it. The audit numbers below assume this asymmetry
washes out on average; for high-vol assets it can be material.

**Replay** (`scripts/replay_live.py`): same convention as backtest.

**Live worker** (`hermes_trading/multi_loop.py:589`):

```python
trade = build_trade_row(asset, position, last_price, reason, ...)
```

```python
# multi_loop.py:200-203 (build_trade_row)
ret_pct = (exit_price - entry_price) / entry_price   # long
net = ret_pct * size
```

- **All exits** (stop, SuperTrend flip, time, regime) fill at the
  **display-bar close** with no slippage adjustment.
- **No fee deducted** — `net = ret_pct * size`, not `net = (ret_pct
  - 2*fee) * size`.

**Difference**: live worker's stop exit at the display-bar close can
be *better* than backtest's stop-price fill when the bar closes
above the stop (a wick), and *worse* when the bar closes well below
the stop (a gap-through that runs further). On average over many
trades, live's "close fill" is more pessimistic than backtest's
"stop fill" for stop exits, but more optimistic than backtest's
"close ± slippage" for non-stop exits.

**Funding-filter interaction**: the funding filter is a **hard gate
applied before entry sizing**. It does not affect fills — when
blocked, no position is opened. Funding has no exit-side
interaction. This is correct and not a source of fill realism gap.

**Optimism summary**:

| path | entry side | non-stop exit | stop exit | net |
|---|---|---|---|---|
| research backtest | adverse 5 bp | adverse 5 bp | at stop, adverse 5 bp | conservative on close fills; optimistic on stop fills |
| replay | same as backtest | same | same | same |
| live worker | none | none | at close (not stop) | optimistic on every fill except stop-through gaps |

### 1.3 Fee assumptions

**Research backtest**: `fee = 0.001` (10 bp) per side, applied as
`(gross - 2*fee) * effective_size` (return-space deduction,
multiplicative on effective_size). Default in every
`scripts/run_*.py` runner.

**Replay**: same default as research (10 bp/side, 5 bp slippage).

**Live worker**: **no fee deducted anywhere**. `multi_loop.py:186`
sets `net = ret_pct * size`.

**Maker / taker assumption**: implicit **taker**. The signal-bar
close is a snapshot moment; a market order at the close is the
natural execution model. Maker fills would require posting limit
orders ahead of the bar close, which neither the research model nor
the live worker simulates.

**Exchange-specific context** (informational, as of audit date):

| venue (spot) | taker bp / side | maker bp / side |
|---|---:|---:|
| Binance spot (BNB discount, VIP 0) | 7.5 | 7.5 |
| Binance futures USDS-M (VIP 0) | 4.0 | 2.0 |
| Coinbase Advanced ($10k-$50k vol) | 35 | 25 |
| Coinbase Advanced ($1M+ vol) | 18 | 8 |
| Kraken Pro ($50k-$100k vol) | 26 | 16 |
| Kraken Pro ($1M+ vol) | 16 | 6 |
| OKX (regular) | 10 | 8 |

The research 10 bp/side assumption sits between Binance futures
taker (4 bp) and Kraken-Pro retail taker (26 bp). It is a
**reasonable default for a "Binance spot or near"** user. It would
**overstate edge for a Binance futures user** (the strategy would
do better in reality) and **understate cost for a Coinbase Advanced
retail user** (the strategy would do considerably worse in reality).

### 1.4 Missing execution effects

| effect | currently modelled? | matters for this strategy at this size? | notes |
|---|---|---|---|
| Slippage | ✓ research / replay; ✗ live | YES — main live↔research gap | 5 bp is the working assumption |
| Bid-ask spread | partially via slippage | low | At BTC/ETH $1k notional on a liquid book, spread is 1-3 bp and is *embedded in* what we call "slippage". A separate spread field would double-count. |
| Partial fills | ✗ none of the paths | NO at $1k notional | Top-of-book depth on BTC/USDT is typically $50k+ |
| Liquidity constraints | ✗ none of the paths | NO at this size | Same reason |
| Latency | ✗ none of the paths | NO at 4h decision bars | ~100 ms order placement is irrelevant against a 14400 s bar |
| Order queue position | ✗ none of the paths | NO for taker orders | Taker fills are instantaneous |
| Funding payment | ✗ none of the paths | LOW–MED for perpetuals | The funding filter avoids the worst extremes; remaining funding can be a small drag |
| Stop-through gaps | optimistic in backtest | LOW on 4h BTC/ETH (rare) | Stop fills at the stop price; a gap-through would fill below the stop |
| Maker rebate (if applicable) | ✗ none of the paths | only if strategy converts to maker | Not relevant under the current taker model |
| Tax / withholding | ✗ none of the paths | out of scope | Operator concern |

## 2. Impact analysis — adopted candidate

Baseline (from Issue #20, replicated byte-for-byte by Issue #27's
runner):

- 48-month walk-forward OOS on BTC/USDT + ETH/USDT parallel
- 123 trades
- +139.71% return, 4.64% max drawdown, PF 3.35
- per-trade effective size = base 0.5 × per-asset 0.5 × overlay 1.0 = **0.25**

Projection method: each scenario adjusts the per-trade net by the
delta in slippage + fee, then compounds across 123 trades. Pure
arithmetic — no re-run; assumes per-trade returns are independent
multiplicative draws of similar magnitude.

| scenario | projected return | Δ vs baseline |
|---|---:|---:|
| **baseline (5 bp slippage, 10 bp fee/side)** | **+139.71%** | — |
| +5 bp slippage (5 → 10 bp/side) | +132.45% | −7.26% |
| +10 bp slippage (5 → 15 bp/side) | +125.41% | −14.30% |
| Binance futures taker (10 → 5 bp/side fee) — CREDIT | +147.19% | +7.48% |
| Binance futures taker low (10 → 4 bp/side fee) — CREDIT | +148.72% | +9.01% |
| Kraken Pro spot taker (10 → 26 bp/side fee) | +117.24% | −22.47% |
| Coinbase Advanced retail taker (10 → 60 bp/side fee) | +76.19% | −63.52% |
| Conservative venue: +5 bp slippage + +5 bp/side fee | +125.41% | −14.30% |

**Reading the table**:

- A reasonable "Binance futures" user actually *gains* ~7-9 pp from
  the research assumption being conservative on fees. The strategy
  is more profitable for them than the headline suggests.
- A "Kraken Pro retail" user *loses* ~22 pp from the research
  assumption being too generous on fees. The strategy still passes
  adoption, but only marginally.
- A "Coinbase Advanced retail" user *loses* ~64 pp from the same
  asymmetry — the strategy would no longer pass the adoption gate
  (PF would drop below the threshold; DD remains low but absolute
  return is destroyed).
- A 5 bp slippage shift moves the headline by ~7 pp. Slippage
  matters about half as much as fee.

DD impact: slippage smooths returns slightly, marginally lowering
DD and lowering PF in parallel. The DD/PF *ratios* are roughly
preserved across the slippage range; the level shifts. Projection
does not model DD directly because per-bar drawdowns require the
full trade trajectory; the linear-aggregate approximation is for
total return only.

### Live-paper-vs-research arithmetic asymmetry

The live worker does not deduct fees or slippage. The research
backtest deducts both. The cumulative gap for the adopted candidate
over the 48mo window:

```
per-trade cost embedded in research = (2 × 5 bp slippage + 2 × 10 bp fee) × effective_size 0.25 = ~7.5 bp net/trade
cumulative over 123 trades                                                                     = ~9.22% of starting capital
```

**Live paper PnL will tend to overstate net edge by ~9.2% vs the
research baseline at the adopted candidate's trade count and size.**

The decay monitor (Issue #15) compares live paper performance vs
research-time baselines. The 9.2% inherent drift adds noise to
those comparisons. It is the single most impactful realism gap in
the project.

## 3. Audit answers

### 3.1 What assumptions are currently made?

- **Research / replay**: 5 bp slippage embedded in entry and exit
  fills; 10 bp fee per side deducted in return space; stop exits
  fill at the stop price; close-bar fills for all non-stop exits;
  no spread / partial fill / latency / liquidity modelling.
- **Live worker**: zero slippage; zero fees; all fills at the
  display-bar close (entries, exits, stops); no spread / partial
  fill / latency / liquidity modelling.
- **Implicit taker** at signal-bar close in all paths.

### 3.2 Which assumptions are unrealistic?

In order of severity:

1. **Live worker's "no slippage, no fee" model** — unrealistic on
   every venue. Single largest realism gap.
2. **Backtest's "stop fills at stop price"** — optimistic for
   gap-through stops. Mild on 4h BTC/ETH; more relevant in altcoins
   and around news events.
3. **Single fixed slippage constant (5 bp) across all bars** — real
   slippage scales with volatility and notional. At $1k notional on
   BTC/ETH it is a fair point estimate; at higher notional it
   under-states.
4. **Same fee for every trade across all venues** — operator-venue
   dependent (~5× spread between Binance futures and Coinbase
   retail).

### 3.3 Which assumptions matter most?

The live-research asymmetry. It contaminates every comparison the
decay monitor makes and every adoption decision that compares live
paper performance to a research baseline. At ~9.2% per 48mo on the
adopted candidate, it dwarfs the per-bp slippage and fee
sensitivities individually.

Within the per-bp sensitivities, **fees dominate slippage by ~2×**.
A 5 bp fee change moves the headline ~7.5 pp; a 5 bp slippage
change moves it ~7 pp; but fees have a much wider operator-venue
spread (4 bp ↔ 60 bp range) than slippage (3 bp ↔ 15 bp range).

### 3.4 Which assumptions can safely be ignored?

- **Partial fills, liquidity constraints, order queue position,
  latency** at the current $1k-per-asset notional and 4h decision
  cadence. The combined realism gap from all four is < 1 bp/trade.
- **Maker / rebate models** while the strategy stays taker. The
  alpha layer fires on the signal-bar close, which is structurally
  a taker model.
- **Stop-through gap modelling** as a first-order concern on 4h
  BTC/ETH. Worth revisiting only when the universe grows beyond
  large-cap pairs or moves to lower timeframes.
- **Bid-ask spread** as a separate field. Spread is currently
  embedded in the slippage assumption; making it explicit would
  either double-count or require a corresponding cut to slippage.
- **Tax, withholding, regulatory routing** — operator concerns,
  not framework concerns.

### 3.5 What is the smallest execution-model improvement worth implementing?

**Apply the existing research convention (10 bp fee/side, 5 bp
slippage embedded in fills) to the live paper worker's
`build_trade_row` and entry/exit price recording**, so live paper
PnL becomes directly comparable to the research baseline.

Concretely, in `hermes_trading/multi_loop.py`:

- At entry: record `entry_price = display_row close × (1 ± slippage)`
  instead of `display_row close`.
- At exit: record `exit_price = display_row close × (1 ∓ slippage)`
  (or for stop exits, `position["stop"] × (1 ∓ slippage)`).
- In `build_trade_row`: change `net = ret_pct * size` to
  `net = (ret_pct − 2 * fee) * size`.
- Pass `fee` and `slippage` through the live multi-asset config so
  they can be tuned per operator-venue without strategy yaml
  changes.

This is ~15-20 lines of code, no new dependencies, no new tests
beyond verifying:

1. live trade rows now deduct fee + slippage matching the research
   constants;
2. the decay monitor's baseline comparison shows a ~9 pp narrower
   live-vs-research gap;
3. `monitor_strategy_decay.py --self-test` and
   `test_multiasset_worker.py` continue to pass (they read the
   trade row schema, which gains nothing new — only the net field
   values change).

It does NOT require:

- A volatility-scaled slippage model (a separate, larger experiment).
- Partial-fill / liquidity modelling.
- Maker logic.
- Order routing / venue abstraction.

Those are larger projects with their own adoption criteria; the
"smallest worth doing" is making live and research speak the same
language.

### 3.6 Should execution modelling be prioritised ahead of `vol_sizing` live testing?

**Yes.** Two independent reasons:

1. **Compounding drift.** `vol_sizing` produced its strongest
   research result (PF 3.35 → 4.63, DD 4.64% → 2.10%) on the
   research model. Adopting it live without first closing the ~9.2%
   live-research gap means the live paper numbers will not be
   directly comparable to the research backtest, and any
   "vol_sizing live is matching research" verification becomes
   noisy. With the smallest fix above in place, the gap closes to
   the irreducible level (latency, queue position, real-time book
   walk — all sub-bp at this size).

2. **Adoption sequencing.** Issue #27's research report explicitly
   conditioned `vol_sizing` adoption on this audit and the
   subsequent execution-layer fix. The conditional was not
   procedural — it was a substantive risk-management requirement.
   Adopting `vol_sizing` first would invert the dependency order
   the report set.

Recommended order:

1. (next) Implement the smallest fix from §3.5 — apply research
   fee + slippage constants in the live worker. Verify with
   `test_multiasset_worker.py` and `monitor_strategy_decay.py
   --self-test`. Expect the decay monitor's live-vs-research gap
   to narrow by ~9 pp at the adopted candidate's trade count.
2. Forward paper-test the current adopted candidate for one
   research-baseline trade-count window (say 30 trades, ~4-6
   months in calendar time given the strategy's frequency) with
   the new fill model.
3. Only then revisit `vol_sizing` adoption.

Out of scope for that sequence:

- Real-money execution.
- Volatility-scaled slippage (a larger experiment).
- Maker logic / order routing.
- Venue abstraction.

## 4. Out of scope

- Real-money path of any kind.
- Any new slippage or fee model.
- Any changes to live worker, research backtest, or replay
  behaviour.
- Any changes to the adopted strategy parameters.
- HFT / sub-minute strategies (out of scope per `ROADMAP.md`).

## 5. Files

- This document: `research/execution_audit.md`.

Reference reading (unmodified by this audit):

- `hermes_trading/backtest.py:_run_state_machine` — research fill
  model.
- `scripts/replay_live.py:_run_strategy_replay` /
  `_run_config_replay` — replay fill model.
- `hermes_trading/multi_loop.py:build_trade_row` — live trade row
  construction.
- `hermes_trading/signals.py:long_exit` /
  `short_exit` — stop detection semantics.
- `ARCHITECTURE.md` — "Modeling asymmetry currently in the
  codebase" section.
- Issue #15 (`scripts/monitor_strategy_decay.py`) — the consumer
  of the live-vs-research comparison most affected by the
  asymmetry.
- Issue #20 / #21 — adopted candidate provenance.
- Issue #25 — execution backlog top item ("Live paper-fill quality
  audit") — this audit closes.
- Issue #27 — `vol_sizing` adoption gated on this audit.
