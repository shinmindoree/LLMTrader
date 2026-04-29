"""6-year rolling 3-month walk of funding contrarian alpha on BTCUSDT-PERP.

For each 3-month window from 2020-09 onwards, evaluate every parameter combo and
report per-quarter return / trades / PF. Helps detect regime-dependence.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
from datetime import datetime, timezone, timedelta
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
KLINES_CACHE = PROJECT_ROOT / "data" / "perp_meta" / "BTCUSDT_15m_klines.parquet"
RESULTS = PROJECT_ROOT / "scripts" / "_funding_alpha_6y_results.jsonl"
SUMMARY = PROJECT_ROOT / "scripts" / "_funding_alpha_6y_summary.md"
COMMISSION = 0.0002


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


def load_klines() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Load 15m klines for 2020-09..2026-04, caching as parquet."""
    if KLINES_CACHE.exists():
        df = pd.read_parquet(KLINES_CACHE)
        ts = df["ts"].to_numpy(dtype="int64")
        return ts, df["o"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    start = datetime(2020, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 29, 23, 59, 59, tzinfo=timezone.utc)
    print(f"[fetch] 15m klines {start:%Y-%m-%d}..{end:%Y-%m-%d}")
    klines = asyncio.run(fetch_klines(start, end, "15m"))
    ts = np.array([int(k[0]) for k in klines], dtype="int64")
    o = np.array([float(k[1]) for k in klines], dtype="float64")
    h = np.array([float(k[2]) for k in klines], dtype="float64")
    l = np.array([float(k[3]) for k in klines], dtype="float64")
    c = np.array([float(k[4]) for k in klines], dtype="float64")
    pd.DataFrame({"ts": ts, "o": o, "h": h, "l": l, "c": c}).to_parquet(KLINES_CACHE, index=False)
    print(f"  cached -> {KLINES_CACHE}  ({len(ts)} bars)")
    return ts, o, h, l, c


def load_funding() -> pd.DataFrame:
    df = pd.read_parquet(FUNDING_PARQUET).rename(
        columns={"funding_time": "fundingTime", "funding_rate": "fundingRate"}
    )
    df["fundingTime"] = df["fundingTime"].astype("int64")
    df["fundingRate"] = df["fundingRate"].astype("float64")
    return df.sort_values("fundingTime").reset_index(drop=True)


def simulate(ts, o, h, l, c, fwin_times, fwin_rates,
             pos_thr, neg_thr, hold_bars, tp_pct, sl_pct):
    entries = np.searchsorted(ts, fwin_times, side="left")
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    trades = wins = losses = longs = shorts = 0
    busy_until = -1

    for k_idx in range(len(entries)):
        bar_i = entries[k_idx]
        if bar_i >= len(c) - 1 or bar_i <= busy_until:
            continue
        rate = fwin_rates[k_idx]
        if rate > pos_thr:
            side = -1
        elif rate < neg_thr:
            side = 1
        else:
            continue

        entry_p = o[bar_i]
        if side == 1:
            tp_p = entry_p * (1.0 + tp_pct); sl_p = entry_p * (1.0 - sl_pct)
        else:
            tp_p = entry_p * (1.0 - tp_pct); sl_p = entry_p * (1.0 + sl_pct)

        end_bar = min(bar_i + hold_bars, len(c) - 1)
        exit_p = None
        for j in range(bar_i, end_bar + 1):
            if side == 1:
                if l[j] <= sl_p: exit_p = sl_p; break
                if h[j] >= tp_p: exit_p = tp_p; break
            else:
                if h[j] >= sl_p: exit_p = sl_p; break
                if l[j] <= tp_p: exit_p = tp_p; break
        if exit_p is None:
            exit_p = c[end_bar]

        if side == 1:
            ret = (exit_p / entry_p) - 1.0
        else:
            ret = (entry_p / exit_p) - 1.0
        net = ret - 2 * COMMISSION
        equity *= (1.0 + net)
        if equity > peak: peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd: max_dd = dd
        if net > 0: wins += 1; gross_profit += net
        else: losses += 1; gross_loss += -net
        trades += 1
        if side == 1: longs += 1
        else: shorts += 1
        busy_until = end_bar

    pf = (gross_profit / gross_loss) if gross_loss > 1e-12 else (float("inf") if gross_profit > 0 else 0.0)
    return {"trades": trades, "wins": wins, "losses": losses, "longs": longs, "shorts": shorts,
            "ret": equity - 1.0, "pf": pf, "dd": max_dd}


def quarters(start: datetime, end: datetime):
    """Yield (label, start_dt, end_dt) for non-overlapping 3-month buckets."""
    cur = start
    while cur < end:
        if cur.month <= 9:
            nxt_m = cur.month + 3
            nxt = cur.replace(month=nxt_m)
        else:
            nxt = cur.replace(year=cur.year + 1, month=cur.month + 3 - 12)
        if nxt > end: nxt = end
        label = f"{cur:%Y}Q{(cur.month - 1)//3 + 1}"
        yield label, cur, nxt
        cur = nxt


def main():
    print("[load] funding")
    funding = load_funding()
    f0 = datetime.fromtimestamp(funding["fundingTime"].iloc[0]/1000, tz=timezone.utc)
    f1 = datetime.fromtimestamp(funding["fundingTime"].iloc[-1]/1000, tz=timezone.utc)
    print(f"  {len(funding)} rows {f0:%Y-%m-%d}..{f1:%Y-%m-%d}")
    print(f"  rate stats: mean={funding['fundingRate'].mean():.5f} std={funding['fundingRate'].std():.5f} "
          f"min={funding['fundingRate'].min():.5f} max={funding['fundingRate'].max():.5f}")

    print("[load] klines")
    ts, o, h, l, c = load_klines()
    print(f"  {len(ts)} bars  {datetime.fromtimestamp(ts[0]/1000, tz=timezone.utc):%Y-%m-%d}.."
          f"{datetime.fromtimestamp(ts[-1]/1000, tz=timezone.utc):%Y-%m-%d}")

    # Quarterly windows from 2020-09-01
    q_start = datetime(2020, 9, 1, tzinfo=timezone.utc)
    q_end = datetime(2026, 4, 29, tzinfo=timezone.utc)
    qs = list(quarters(q_start, q_end))
    print(f"[quarters] {len(qs)} buckets: {qs[0][0]}..{qs[-1][0]}")

    # Use the funding alpha sweep grid (top-tier: focus on stable region)
    pos_thr_grid = [0.00002, 0.00003, 0.00005, 0.00007, 0.00010]
    neg_thr_grid = [-0.00002, -0.00003, -0.00005, -0.00010]
    hold_h_grid = [8, 16, 24, 48]
    tp_grid = [0.006, 0.012]
    sl_grid = [0.006, 0.012]
    bars_per_h = 4

    n_combos = len(pos_thr_grid)*len(neg_thr_grid)*len(hold_h_grid)*len(tp_grid)*len(sl_grid)
    print(f"[sweep] {n_combos} combos x {len(qs)} quarters = {n_combos*len(qs)} sims")

    # Pre-extract numpy arrays for funding in each quarter
    f_t = funding["fundingTime"].to_numpy(dtype="int64")
    f_r = funding["fundingRate"].to_numpy(dtype="float64")
    q_data = []
    for label, qs_dt, qe_dt in qs:
        s_ms = int(qs_dt.timestamp() * 1000)
        e_ms = int(qe_dt.timestamp() * 1000)
        mask = (f_t >= s_ms) & (f_t < e_ms)
        q_data.append((label, qs_dt, qe_dt, f_t[mask], f_r[mask]))

    if RESULTS.exists():
        RESULTS.unlink()
    written = 0
    with RESULTS.open("a", encoding="utf-8") as fout:
        for pos_thr, neg_thr, hold_h, tp, sl in itertools.product(
                pos_thr_grid, neg_thr_grid, hold_h_grid, tp_grid, sl_grid):
            hold_bars = hold_h * bars_per_h
            row = {"pos_thr": pos_thr, "neg_thr": neg_thr, "hold_h": hold_h, "tp": tp, "sl": sl,
                   "quarters": {}}
            agg_eq = 1.0
            agg_trades = 0
            for label, _qs, _qe, fwt, fwr in q_data:
                stats = simulate(ts, o, h, l, c, fwt, fwr, pos_thr, neg_thr, hold_bars, tp, sl)
                row["quarters"][label] = {
                    "trades": stats["trades"], "ret": round(stats["ret"], 4),
                    "pf": round(stats["pf"], 3) if stats["pf"] != float("inf") else None,
                    "L": stats["longs"], "S": stats["shorts"], "dd": round(stats["dd"], 4),
                }
                agg_eq *= (1.0 + stats["ret"])
                agg_trades += stats["trades"]
            row["agg_ret"] = round(agg_eq - 1.0, 4)
            row["agg_trades"] = agg_trades
            row["pos_quarters"] = sum(1 for q in row["quarters"].values() if q["ret"] > 0)
            row["neg_quarters"] = sum(1 for q in row["quarters"].values() if q["ret"] < 0)
            fout.write(json.dumps(row) + "\n")
            written += 1
            if written % 50 == 0:
                print(f"  ...{written}/{n_combos}")

    print(f"[done] wrote {written} -> {RESULTS}")

    # Summarize: top-10 by aggregate compounded return with min trades
    rows = [json.loads(l) for l in RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows.sort(key=lambda r: r["agg_ret"], reverse=True)
    top = rows[:10]
    by_pos_q = sorted(rows, key=lambda r: (r["pos_quarters"], r["agg_ret"]), reverse=True)[:10]

    lines = []
    lines.append(f"# Funding contrarian alpha — 6y quarterly walk\n")
    lines.append(f"- Period: {qs[0][0]}..{qs[-1][0]}  ({len(qs)} quarters)")
    lines.append(f"- Combos: {n_combos}")
    lines.append(f"- Commission: {COMMISSION*100:.2f}% per side\n")

    lines.append("## Top-10 by aggregate compounded return\n")
    lines.append("| pos_thr | neg_thr | hold_h | tp | sl | agg ret | trades | +Q | −Q |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in top:
        lines.append(f"| {r['pos_thr']:+.5f} | {r['neg_thr']:+.5f} | {r['hold_h']} | {r['tp']:.3f} | {r['sl']:.3f} "
                     f"| {r['agg_ret']*100:+.1f}% | {r['agg_trades']} | {r['pos_quarters']} | {r['neg_quarters']} |")

    lines.append("\n## Top-10 by positive-quarter count (then return)\n")
    lines.append("| pos_thr | neg_thr | hold_h | tp | sl | agg ret | trades | +Q | −Q |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in by_pos_q:
        lines.append(f"| {r['pos_thr']:+.5f} | {r['neg_thr']:+.5f} | {r['hold_h']} | {r['tp']:.3f} | {r['sl']:.3f} "
                     f"| {r['agg_ret']*100:+.1f}% | {r['agg_trades']} | {r['pos_quarters']} | {r['neg_quarters']} |")

    # Per-quarter trace of the best agg-ret combo
    best = top[0]
    lines.append(f"\n## Best combo per-quarter trace\n")
    lines.append(f"`pos={best['pos_thr']:+.5f} neg={best['neg_thr']:+.5f} hold={best['hold_h']}h tp={best['tp']:.3f} sl={best['sl']:.3f}`\n")
    lines.append("| quarter | ret | trades | L/S | PF | DD |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label, _qs, _qe, _t, _r in q_data:
        q = best["quarters"][label]
        pf = q["pf"] if q["pf"] is not None else "—"
        lines.append(f"| {label} | {q['ret']*100:+.1f}% | {q['trades']} | {q['L']}/{q['S']} | {pf} | {q['dd']*100:.1f}% |")

    SUMMARY.write_text("\n".join(lines), encoding="utf-8")
    print(f"[summary] -> {SUMMARY}")
    print("\n=== Top 5 by agg return ===")
    for r in top[:5]:
        print(f"  pos={r['pos_thr']:+.5f} neg={r['neg_thr']:+.5f} hold={r['hold_h']}h tp={r['tp']:.3f} sl={r['sl']:.3f} "
              f"| agg={r['agg_ret']*100:+.1f}% trades={r['agg_trades']} +Q={r['pos_quarters']}/-Q={r['neg_quarters']}")
    print("\n=== Top 5 by +Q count ===")
    for r in by_pos_q[:5]:
        print(f"  pos={r['pos_thr']:+.5f} neg={r['neg_thr']:+.5f} hold={r['hold_h']}h tp={r['tp']:.3f} sl={r['sl']:.3f} "
              f"| agg={r['agg_ret']*100:+.1f}% trades={r['agg_trades']} +Q={r['pos_quarters']}/-Q={r['neg_quarters']}")


if __name__ == "__main__":
    main()
