# Donchian Breakout Triple-Symbol Alpha (clean fapi data)

Discovered by `scripts/_alpha_lab/multi_symbol_sweep.py` after rebuilding
all klines parquets from the authoritative Binance USDT-M Futures fapi
(see commit `ca33163` — the previous `BTCUSDT_15m_klines.parquet` was
corrupted, with only 0.6% bar match to fapi, and ALL prior alpha-lab
"winners" turned out to be artifacts of bad data).

## TL;DR

Three Donchian channel breakout configs, one per (symbol, timeframe),
deployed in parallel with 1/3 capital each. **Same strategy code, three
preset constants** (see `scripts/strategies/donchian_breakout_strategy.py`).

## Vectorized OOS metrics (2025-05-01 ~ 2026-04-29, 6bp commission + 2bp slippage)

| Preset      | Symbol  | Interval | dc | TP    | SL    | hold | Ret    | Trades | PF    | DD     | tpd   |
| ----------- | ------- | -------- | -- | ----- | ----- | ---- | ------ | ------ | ----- | ------ | ----- |
| `BTC-2h`    | BTCUSDT | 2h       | 25 | 2.0%  | 2.0%  | 48h  | +20.7% | 174    | 1.15  | 14.8%  | 0.48  |
| `ETH-4h`    | ETHUSDT | 4h       | 40 | 5.0%  | 1.0%  | 48h  | +23.1% | 83     | 1.33  | 11.7%  | 0.23  |
| `ETH-30m`   | ETHUSDT | 30m      | 60 | 5.0%  | 1.0%  | 48h  | +29.1% | 307    | 1.11  | 20.7%  | 0.83  |
| **portfolio (1/3 each)** |  |  |  |  |  |  | **+24.30%** | **564** | — | — | **1.55** |

## Friction sensitivity (OOS year, portfolio average)

| Setting  | BTC    | ETH-4h | ETH-30m | Portfolio |
| -------- | ------ | ------ | ------- | --------- |
| 4bp/0bp  | +28.4% | +26.6% | +47.9%  | +34.3%    |
| 6bp/2bp  | +20.7% | +23.1% | +29.1%  | +24.3%    |
| 8bp/2bp  | +13.8% | +19.8% | +16.8%  | +16.8%    |
| 10bp/3bp | +2.2%  | +13.6% | +4.3%   | +6.7%     |

`ETH-4h` is the most friction-resilient single config.

## Annual flow (clean fapi data, 6bp/2bp friction)

| Year | BTC    | ETH-4h | ETH-30m | Portfolio |
| ---- | ------ | ------ | ------- | --------- |
| 2021 | -78.1% | -30.7% | **+25.0%** | -27.9%    |
| 2022 | -4.2%  | -27.1% | -18.1%  | -16.4%    |
| 2023 | -0.7%  | +16.5% | -17.7%  | -0.7%     |
| 2024 | +38.0% | +35.8% | +17.3%  | +30.4%    |
| 2025 | +44.8% | +22.0% | +35.9%  | +34.2%    |

Walk-forward 6-month windows: 13/20 (65%) positive, mean +8.45%, median +8.98%.

## Production BacktestEngine validation (BTC-2h preset)

Run: `python scripts/_alpha_lab/engine_donchian_check.py --symbol BTCUSDT --interval 2h --preset BTC-2h --start 2025-05-01 --end 2026-04-29 --max-position 1.0 --slippage-bps 2 --commission-bps 4`

Engine result vs vectorized lab:
- Engine: +33.52% / 137 trades (compound, full-position)
- Vectorized: +27.69% / 174 trades (additive, full-position)
- Trade count gap: ~21% (vectorized has access to pre-OOS history for
  the first Donchian window; engine warm-up starts at slice boundary)
- Return gap: vectorized is additive equity, engine is compounding —
  hence engine > vectorized after a profitable year.
- Exit pattern matches (TP > SL > TIME), win rate matches (~29%).

## Caveats / NOT alpha for these regimes

1. **2021 BTC -78.1%**: BTC violent chop with constant false breakouts.
   Adding SMA200 trend filter cuts BTC 2021 loss to -46% but also halves
   OOS year (+20.7% → +9.3%). Trade-off; portfolio diversification is
   currently the chosen defense.
2. **2022 -16.4% portfolio**: ETH downtrend cap'd by SL/short-side, all
   three configs lost money in this year.
3. **PF ~ 1.1-1.3**: weak edge that depends on regime; not a get-rich
   strategy. Realistic year is +20-30% with -15-25% drawdown.
4. **Frequency 1.55 trades/day** is below the original 2-3 trades/day
   target. Adding more symbols (BNB, AVAX, LINK) would be the next
   step to push frequency up.

## Implementation files

- `scripts/strategies/donchian_breakout_strategy.py` — base class +
  three preset wrappers (`DonchianBreakoutBtc2hStrategy`, `Eth4hStrategy`,
  `Eth30mStrategy`).
- `scripts/_alpha_lab/multi_symbol_sweep.py` — the 26,706-task sweep that
  found the three winners (klines-only, BTC+ETH+SOL, 4 families).
- `scripts/_alpha_lab/final_candidates.py` — stress test + portfolio sim.
- `scripts/_alpha_lab/engine_donchian_check.py` — production engine
  validation harness for any (symbol, interval, preset) tuple.
- `scripts/_alpha_lab/dataset_klines.py` — minimal klines-only loader for
  symbols without OI/funding/lsr/taker data.
