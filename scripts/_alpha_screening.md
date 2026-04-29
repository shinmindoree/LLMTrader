# Alpha screening notes — BTCUSDT-PERP

## Data foundation
- `data/perp_meta/BTCUSDT_funding.parquet`: 1y, 1096 funding events (every 8h), 2025-04-29..2026-04-29.
- `data/perp_meta/BTCUSDT_oi_5m.parquet`: 30d only (Binance public hist limit), 8716 rows.
- `data/perp_meta/BTCUSDT_lsr_5m.parquet`: 30d only, 8716 rows.
- `data/perp_meta/BTCUSDT_taker_5m.parquet`: 30d only, 8716 rows.
- Note: openInterestHist / topLongShortAccountRatio / takerlongshortRatio are capped at 30 days of history on Binance fapi public.

## Funding contrarian alpha (sweep_funding_alpha.py)
Hypothesis: Extreme funding rates indicate one-sided positioning that mean-reverts.
- pos_thr > 0 → SHORT (longs paying premium)
- neg_thr < 0 → LONG  (shorts paying premium)
- Exit on % TP/SL or after N hours.
- Fees: 0.02% per side.

Funding rate distribution over the year:
- mean=0.00003, std=0.00005, min=-0.00015, max=0.00010 (bps-scale)
- pcts: 5%=-5.4bp, 25%=0.5bp, 75%=7.2bp, 95%=10bp.

### Top stable combos (positive in 1m/3m/6m/1y)
| pos_thr | neg_thr | hold_h | tp | sl | 1y ret | 6m ret | 3m ret | 1m ret | 1y trades | 1y PF | 1y DD |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| +3e-5 | -3e-5 | 48 | 0.006 | 0.012 | +17.2% | +13.6% | +1.6% | +3.2% | 150 | 1.35 | 7.4% |
| +7e-5 | -2e-5 | 24 | 0.006 | 0.012 | +15.3% | +7.2% | +3.6% | +1.8% | 183 | 1.25 | 8.7% |
| +3e-5 | -2e-5 | 8  | 0.006 | 0.012 | +17.7% | +2.6% | +3.1% | +1.6% | 491 | 1.12 | 10.9% |
| +7e-5 | -7e-5 | 24 | 0.006 | 0.012 | +12.9% | +9.0% | +5.3% | +2.1% | 142 | 1.28 | 9.0% |

Best raw 1y: pos=+3e-5 neg=-7e-5 hold=24h tp=0.006 sl=0.012 → +22.7%, 228 trades, PF 1.30, DD 6.9%
(but 3m return is −0.9%, so excluded from "stable").

### Acceptance check (≥0% 6m, PF≥1.1, ≥30 trades, DD≤30%)
**PASSES** for 41 combos. Funding alpha is real.

### Caveats
- Frequency: ~0.4–1.4 trades/day (limited by 3 funding events/day). Cannot meet "≥5/day" alone.
- Heavily short-biased (rate distribution skews positive in this period). Need to verify on a different regime before trusting longs.
- Funding rates extremely benign in this 1y window (max 1bp). Could have been very different in 2021/2022.

## Next alphas (to test on 30d windows)
- LSR contrarian (top-trader long/short ratio extremes).
- Taker buy/sell flow imbalance.
- OI surge with price rejection.
