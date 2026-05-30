# Research Log

Chronological record of experiments that were actually run and the
honest result. All numbers below are out-of-sample stitched walk-forward
on BTC/USDT 4h, 24 months, fees 10 bps/side + 5 bps slippage, 8 folds
(train 1440 / test 360 / embargo 6) unless noted otherwise.

---

## v1: RSI mean-reversion (decommissioned)

- 1-minute polling, hard-coded `RSI < threshold` long entries, flat % stop.
- Failed on costs: at 1m, ATR is ~0.025% of price; a 1.5×ATR stop is
  ~5× narrower than a 0.2% round-trip fee. Every trade opened underwater.
- In-sample 24-month: -75% total return, 7% win rate, PF 0.08.
- **Decommissioned** in favor of v2 long-short on 4h.

## v2 long-short (current baseline)

- 4h decision bars; long + short setups with EMA50/200 regime gate.
- Setups: pullback (RSI<32 + 21-EMA touch), breakout (3-bar play + VWAP).
- Walk-forward OOS: **+8.97% return, 4.43% max DD, PF 1.69, 33 trades**.
- Buy-and-hold over the same window: ~+27% return, ~50% DD. Strategy is
  risk-managed underperformer in absolute terms.
- Currently the floor every new variant must beat.

## Markov regime layer — first iteration (rejected)

- 6-state hand-defined alphabet (up/sideways/down × low/high vol).
- First-order transition matrix fit per walk-forward fold.
- Used as a binary long-entry filter.
- In-sample: PF jumped 1.69 → 0.86 (looked great).
- OOS: PF dropped to 0.60 (worse than baseline). Textbook overfit
  signature; first iteration rejected.

## Markov regime — multi-mode rebuild

A discipline-focused rebuild evaluated five modes:

| mode | OOS PF | OOS return | verdict |
|---|---:|---:|---|
| `hard_filter` | 1.68 | +7.70% | rejected — no improvement |
| `soft_sizing` | 1.86 | +7.48% | risk-adjusted win, lower return |
| `bad_regime_avoidance` | 1.69 | +8.97% | inert; per-fold sample too small to trigger |
| `multi_timeframe_soft_sizing` | 1.86 | +7.48% | degenerates to single-TF (1d slice < min_training_bars) |
| `strategy_routing` (MTF on) | 2.17 | +9.23% | initial "winner" — later recalibrated |
| `strategy_routing` (MTF off) | 1.54 | +3.94% | honest baseline; MTF path was inflating |
| `routing_sizing_combined` | 1.37 | +2.55% | combination worse than either component |

The MTF recalibration came from the code-review pass: the multi-timeframe
code path forward-fills missing `long_allowed` rows as True at fold
boundaries (see `BUGS.md` #4). With that disabled, the `strategy_routing`
result lands at PF 1.54 — below baseline. **The honest current best
remains the no-Markov baseline (PF 1.69).**

Confirmed quant-literature finding: regime models add value via exposure
control (`soft_sizing` does cut drawdown ~46% at modest return cost), not
direction prediction (`hard_filter` is OOS-neutral).

## Trading-logic audit (Phase 1–7)

A per-trade detailed CSV of the 57 baseline trades surfaced concrete
edge signals:

- **State filter works**: PF 2.16 / 1.65 in `up_*` states vs PF 0.17 /
  0.20 in `down_*` states. Strong evidence that exposure control on
  regime is the only Markov role with real signal.
- **Holding past 16 bars** is where the money is: 19 of 46 such
  trades won at **80% win rate / +2.13%/trade compounded**. The
  current exits kick the strategy out of trends.
- **RSI<32 long pullback** went 0/6 wins at PF 0.39. The classic
  "buy oversold dip in uptrend" thesis is measurably negative on 4h BTC.
- **21-EMA trail exit on breakouts** is PF 0.08 — the worst exit
  reason. Wider trail or different exit logic is needed.
- **Entries far below VWAP** (bottom quartile) went 0/15. A
  vwap-distance gate is the strongest single filter visible in the data.
- **Short pullback** (RSI > 68, bearish regime, near 21-EMA) won 50%
  at PF 3.04 on 6 trades. Small sample, worth keeping.

## Donchian-20 trend-following experiment

Direct attempt to capture the "16+ bar holds win 80%" finding via a
trend-continuation entry instead of mean-reversion.

| variant | OOS PF | OOS return | verdict |
|---|---:|---:|---|
| Donchian-20 only | 0.90 | -2.16% | rejected — fails both adoption criteria |
| Donchian-20 + strategy_routing | 0.84 | -2.97% | rejected — routing slightly hurts |

The trend-capture mechanism worked: 19 of 46 Donchian trades held ≥16
bars and won 89.5% at +1.53%/trade. The problem: the other 27 trades
stopped out short with average -1.07% per ATR-stop hit, more than
overwhelming the long-hold winners. Donchian-20 fires too often in
chop relative to confluence-triggered setups.

Per the hard rule "do not tune endlessly", this experiment is closed.

## Recommended next experiment

**SuperTrend(10, 3) trend-following.** Same trend-capture thesis as
Donchian but a more selective entry (ATR-based directional flips,
fewer false breakouts in chop). Two TA-conventional hyperparameters,
low overfit surface, same walk-forward harness and adoption criteria.

If SuperTrend fails the criteria, the queue continues per `ROADMAP.md`:

1. SuperTrend(10, 3)
2. BTC/ETH relative-strength rotation
3. HMM 2-state regime overlay (optional `hmmlearn` dep)
4. Funding-rate stress filter
