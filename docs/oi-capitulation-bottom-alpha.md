# Winner Alpha — OI Capitulation Bottom (BTCUSDT-PERP)

**Discovered**: Iter4 robustness sweep, commit `263a9de` (4-iteration alpha discovery).
**Status**: Live-tradable spec; pending OI data-feed integration for production runner.

---

## 1. Specification

| Param | Value | Description |
|-------|-------|-------------|
| Symbol | BTCUSDT-PERP | Binance USDT-margined perpetual |
| Bar | 15m kline | Entry/exit on 15m bars |
| Feature | sum_oi (5m, FF to 15m) | Aggregate Open Interest in BTC |
| Side | LONG only | |
| Entry signal | `OI.pct_change(24h) < -2.0%` AND `close.pct_change(24h) < -0.5%` | Capitulation bottom: leveraged longs liquidating into a falling price |
| Hold | 48 bars (12h max) | |
| Take-profit | +2.0% | |
| Stop-loss | -1.2% | |
| Cooldown | No overlapping positions (busy_until = exit bar) | |
| Commission | 4 bps round-trip | Assumed in backtest |

Equivalent in code variables (`micro_alpha_lib.build_signal_oi_price`):
```
mode      = "oi_down_p_down_long"
win       = 96 bars (24h)
k_oi      = 0.020   # 2% OI drop over 24h
k_p       = 0.005   # 0.5% price drop over 24h
hold_h    = 48
tp        = 0.020
sl        = 0.012
```

---

## 2. Performance — full 5.7 years (2020-09-01 → 2026-04-29)

| Metric | Value |
|--------|------:|
| Total return | **+214.0%** |
| Profit factor | 1.34 |
| Max drawdown | 22.4% |
| Trades | 544 |
| Positive months | 42 / 68 (62%) |
| Avg trade frequency | ~8 / month |

### Train / Test (Iter3 OOS picks)
| Window | Period | Ret | PF | DD | +M | Trades |
|--------|--------|----:|----:|----:|----:|------:|
| TRAIN  | 2023-04..2025-04 (24m) | +50.4% | 1.32 | 7.5% | 15/25 | 208 |
| TEST   | 2025-04..2026-04 (12m) | **+34.9%** | 1.56 | 4.9% | 9/13 | 94 |

### Robustness (Iter4 — full sample slicing)
| Slice | Detail |
|-------|--------|
| Quarters (24, ≥5 trades) | **18 / 24 positive (75%)**. Worst quarter: **−22.4%**. |
| Rolling 6m (11 windows) | **10 / 11 positive (91%)**. Worst 6m: **−5.6%**. Worst 6m DD: 13.8%. |

This is the **only** candidate (out of 10 hand-picked from iter1+iter23 OOS survivors) that
remained robust on the pre-2023 history (2020 launch through 2022 bear). All LSR-based and
oi_up_p_down_long candidates collapsed (DD > 60%, multiple quarters < −40%) when tested
on the 2020–2022 range — confirming they were 2023+ regime artifacts, **not** robust alphas.

---

## 3. Economic Rationale

OI falling concurrently with price falling = **leveraged longs being liquidated/closed**.
That liquidation pressure is the marginal seller. Once it ends, the supply imbalance reverses
and price tends to mean-revert higher. Empirically the bottoming process averages 12–48h on
BTC perp.

This is a **capitulation-bottom mean-reversion alpha**, not a trend-follow.

---

## 4. Live-deploy Checklist (follow-up engineering — not yet done)

The runner today does **not** ingest OI data. Before going live, the following must ship:

1. **OI ingest job**: pull `/futures/data/openInterestHist` (5m granularity) on a 5m schedule
   into the live store, with backfill from at least the last 25h on startup.
2. **Custom indicator** registered via `ctx.register_indicator("oi_pct_change_24h", fn)`
   that reads from the live OI store and returns the latest 24h % change.
3. **Strategy file** `scripts/strategies/oi_capitulation_bottom_strategy.py` (skeleton TBD)
   that consumes both the OI custom indicator AND the price 24h-pct-change.
4. **Min-trade-size guard**: position sizing based on TP=2% / SL=1.2% (R/R ≈ 1.67).
5. **Smoke test**: paper-trade for 1 month with the live OI feed; confirm trade rate ≈ 8/m
   and trade outcomes match backtest distribution.

---

## 5. Caveats

- 2022 bear and 2021 mid-cycle had stretches with consecutive negative quarters (−22.4% worst);
  drawdowns of 15–25% should be expected and budgeted for in sizing.
- Trade rate is moderate (~8/m). Does **not** meet the original "≥5 trades per day" target.
  Higher-frequency variants (win=4, hold=8h) showed strong recent OOS but failed the full-sample
  robustness check — they are regime-fits.
- Full sample uses Vision-archived OI; live OI feed accuracy must be validated before prod.

---

## 6. Repro

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -u scripts/sweep_micro_iter4_robust.py
# Reads data/perp_meta/*.parquet, writes _micro_iter4_robust_summary.md
```

Source files:
- [scripts/micro_alpha_lib.py](scripts/micro_alpha_lib.py)
- [scripts/sweep_micro_iter23.py](scripts/sweep_micro_iter23.py)
- [scripts/sweep_micro_iter4_robust.py](scripts/sweep_micro_iter4_robust.py)
- [scripts/_micro_iter4_robust_summary.md](scripts/_micro_iter4_robust_summary.md)
