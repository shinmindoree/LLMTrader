"""Funding-rate contrarian alpha screening for BTCUSDT-PERP.

Hypothesis: Extreme funding rates indicate crowded one-sided positioning that
mean-reverts. We enter against the crowd at funding-event timestamps:
  - funding > pos_thr  -> SHORT (longs paying too much)
  - funding < neg_thr  -> LONG  (shorts paying too much)
Exit on fixed % TP/SL or after N hours.

Vectorized sweep over thresholds, hold period, and TP/SL grids.
Evaluates each parameter set on 1m / 3m / 6m / 1y windows of BTCUSDT 15m bars.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backtest.data_fetcher import fetch_all_klines  # noqa: E402
from binance.client import BinanceHTTPClient, normalize_binance_base_url  # noqa: E402
from settings import get_settings  # noqa: E402

FUNDING_PARQUET = PROJECT_ROOT / "data" / "perp_meta" / "BTCUSDT_funding.parquet"
RESULTS = PROJECT_ROOT / "scripts" / "_funding_alpha_results.jsonl"
COMMISSION = 0.0002  # per side, taker-ish


async def fetch_klines(start: datetime, end: datetime, itv: str = "15m"):
    s = get_settings()
    base = normalize_binance_base_url(s.binance.base_url_backtest or s.binance.base_url)
    c = BinanceHTTPClient(api_key=s.binance.api_key or "", api_secret=s.binance.api_secret or "", base_url=base)
    try:
        return await fetch_all_klines(
            client=c, symbol="BTCUSDT", interval=itv,
            start_ts=int(start.timestamp() * 1000),
            end_ts=int(end.timestamp() * 1000),
        )
    finally:
        await c.aclose()


def klines_to_arrays(klines):
    ts = np.array([int(k[0]) for k in klines], dtype="int64")
    o = np.array([float(k[1]) for k in klines], dtype="float64")
    h = np.array([float(k[2]) for k in klines], dtype="float64")
    l = np.array([float(k[3]) for k in klines], dtype="float64")
    c = np.array([float(k[4]) for k in klines], dtype="float64")
    return ts, o, h, l, c


def load_funding() -> pd.DataFrame:
    df = pd.read_parquet(FUNDING_PARQUET)
    rename_map = {}
    if "funding_time" in df.columns: rename_map["funding_time"] = "fundingTime"
    if "funding_rate" in df.columns: rename_map["funding_rate"] = "fundingRate"
    if rename_map:
        df = df.rename(columns=rename_map)
    df["fundingTime"] = df["fundingTime"].astype("int64")
    df["fundingRate"] = df["fundingRate"].astype("float64")
    df = df.sort_values("fundingTime").reset_index(drop=True)
    return df


@dataclass
class TradeStats:
    trades: int
    wins: int
    losses: int
    total_return: float  # multiplicative (equity-1)
    pf: float
    max_dd: float
    longs: int
    shorts: int
    avg_pnl: float


def simulate(ts, o, h, l, c, funding_df: pd.DataFrame,
             pos_thr: float, neg_thr: float,
             hold_bars: int, tp_pct: float, sl_pct: float,
             window_start_ms: int, window_end_ms: int) -> TradeStats:
    # Filter funding events to window
    fwin = funding_df[(funding_df["fundingTime"] >= window_start_ms)
                      & (funding_df["fundingTime"] <= window_end_ms)]
    # For each funding event, find first bar with ts >= fundingTime (open of next bar)
    # We enter at that bar's open; SL/TP checked from intra-bar high/low.
    bar_times = ts
    entries = np.searchsorted(bar_times, fwin["fundingTime"].values, side="left")

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    trades = wins = losses = longs = shorts = 0
    busy_until = -1  # bar index until which we hold (exit bar inclusive)

    rates = fwin["fundingRate"].values

    for k_idx, bar_i in enumerate(entries):
        if bar_i >= len(c) - 1:
            continue
        if bar_i <= busy_until:
            continue  # already in trade
        rate = rates[k_idx]
        side = 0
        if rate > pos_thr:
            side = -1  # short
        elif rate < neg_thr:
            side = 1  # long
        else:
            continue

        entry_p = o[bar_i]  # open of next bar after funding
        if side == 1:
            tp_p = entry_p * (1.0 + tp_pct)
            sl_p = entry_p * (1.0 - sl_pct)
        else:
            tp_p = entry_p * (1.0 - tp_pct)
            sl_p = entry_p * (1.0 + sl_pct)

        exit_p = None
        end_bar = min(bar_i + hold_bars, len(c) - 1)
        for j in range(bar_i, end_bar + 1):
            hi = h[j]; lo = l[j]
            if side == 1:
                if lo <= sl_p:
                    exit_p = sl_p; break
                if hi >= tp_p:
                    exit_p = tp_p; break
            else:
                if hi >= sl_p:
                    exit_p = sl_p; break
                if lo <= tp_p:
                    exit_p = tp_p; break
        if exit_p is None:
            exit_p = c[end_bar]

        if side == 1:
            ret = (exit_p / entry_p) - 1.0
        else:
            ret = (entry_p / exit_p) - 1.0
        net = ret - 2 * COMMISSION
        equity *= (1.0 + net)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
        if net > 0:
            wins += 1; gross_profit += net
        else:
            losses += 1; gross_loss += -net
        trades += 1
        if side == 1: longs += 1
        else: shorts += 1
        busy_until = end_bar

    pf = (gross_profit / gross_loss) if gross_loss > 1e-12 else (float("inf") if gross_profit > 0 else 0.0)
    return TradeStats(
        trades=trades, wins=wins, losses=losses,
        total_return=equity - 1.0, pf=pf, max_dd=max_dd,
        longs=longs, shorts=shorts,
        avg_pnl=((equity - 1.0) / trades) if trades else 0.0,
    )


def main():
    print("[load] funding parquet")
    funding = load_funding()
    print(f"  funding rows: {len(funding)}  range: "
          f"{datetime.fromtimestamp(funding['fundingTime'].iloc[0]/1000, tz=timezone.utc):%Y-%m-%d} .. "
          f"{datetime.fromtimestamp(funding['fundingTime'].iloc[-1]/1000, tz=timezone.utc):%Y-%m-%d}")
    print(f"  funding stats: mean={funding['fundingRate'].mean():.5f} "
          f"std={funding['fundingRate'].std():.5f} "
          f"min={funding['fundingRate'].min():.5f} max={funding['fundingRate'].max():.5f}")
    pcts = funding['fundingRate'].quantile([0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]).to_dict()
    print(f"  pcts: {pcts}")

    # Fetch 1y of 15m candles
    end = datetime(2026, 4, 29, 23, 59, 59, tzinfo=timezone.utc)
    start = datetime(2025, 4, 29, tzinfo=timezone.utc)
    print(f"[fetch] 15m klines {start:%Y-%m-%d}..{end:%Y-%m-%d}")
    klines = asyncio.run(fetch_klines(start, end, "15m"))
    ts, o, h, l, c = klines_to_arrays(klines)
    print(f"  klines: {len(klines)}")

    windows = [
        ("1m", datetime(2026, 3, 30, tzinfo=timezone.utc), datetime(2026, 4, 29, 23, 59, 59, tzinfo=timezone.utc)),
        ("3m", datetime(2026, 1, 29, tzinfo=timezone.utc), datetime(2026, 4, 29, 23, 59, 59, tzinfo=timezone.utc)),
        ("6m", datetime(2025, 10, 29, tzinfo=timezone.utc), datetime(2026, 4, 29, 23, 59, 59, tzinfo=timezone.utc)),
        ("1y", datetime(2025, 4, 29, tzinfo=timezone.utc), datetime(2026, 4, 29, 23, 59, 59, tzinfo=timezone.utc)),
    ]

    # Sweep grid (~tractable). Funding rates here cap at ~1bp, so use sub-bp thresholds.
    pos_thr_grid = [0.00002, 0.00003, 0.00005, 0.00007, 0.00009]
    neg_thr_grid = [-0.00002, -0.00003, -0.00005, -0.00007]
    hold_h_grid = [4, 8, 16, 24, 48]   # hours
    tp_grid = [0.003, 0.006, 0.012]
    sl_grid = [0.003, 0.006, 0.012]

    bars_per_h = 4  # 15m bars
    if RESULTS.exists():
        RESULTS.unlink()
    n_combos = (len(pos_thr_grid) * len(neg_thr_grid) * len(hold_h_grid)
                * len(tp_grid) * len(sl_grid))
    print(f"[sweep] {n_combos} combos x {len(windows)} windows")

    best_by_window = {w[0]: None for w in windows}
    written = 0
    with RESULTS.open("a", encoding="utf-8") as fout:
        for pos_thr, neg_thr, hold_h, tp, sl in itertools.product(
                pos_thr_grid, neg_thr_grid, hold_h_grid, tp_grid, sl_grid):
            hold_bars = hold_h * bars_per_h
            row: dict = {
                "pos_thr": pos_thr, "neg_thr": neg_thr,
                "hold_h": hold_h, "tp": tp, "sl": sl,
            }
            for w_name, w_s, w_e in windows:
                stats = simulate(
                    ts, o, h, l, c, funding,
                    pos_thr, neg_thr, hold_bars, tp, sl,
                    int(w_s.timestamp() * 1000), int(w_e.timestamp() * 1000),
                )
                row[w_name] = {
                    "trades": stats.trades, "ret": round(stats.total_return, 4),
                    "pf": round(stats.pf, 3) if stats.pf != float("inf") else None,
                    "dd": round(stats.max_dd, 4),
                    "L": stats.longs, "S": stats.shorts,
                }
                # Track best 1y by return with min trades
                if w_name == "1y" and stats.trades >= 30:
                    bw = best_by_window["1y"]
                    if bw is None or stats.total_return > bw[0]:
                        best_by_window["1y"] = (stats.total_return, dict(row))
            fout.write(json.dumps(row) + "\n")
            written += 1
            if written % 50 == 0:
                print(f"  ...{written}/{n_combos}")

    print(f"[done] wrote {written} rows -> {RESULTS}")
    bw = best_by_window["1y"]
    if bw:
        print(f"\n[best 1y] return={bw[0]:.4f}")
        print(json.dumps(bw[1], indent=2))


if __name__ == "__main__":
    main()
