"""3-year monthly walk-forward of funding contrarian alpha on BTCUSDT-PERP.

Grid: pos_thr x neg_thr x hold_h x tp x sl (much richer than the 6y quarterly run).
Window: 2023-04-29 .. 2026-04-29 (36 calendar months).

For each combo we report per-month (trades, ret, pf, L/S, dd) plus aggregate
metrics, and count how many combos pass several "stable" acceptance bars.

Acceptance tiers (all evaluated on aggregate compounded equity across the 36
months and the per-month series):
  T1  agg_ret > 0
  T2  T1 AND agg_pf >= 1.1 AND agg_trades >= 30 AND agg_dd <= 0.30
  T3  T2 AND positive_months >= 22 (>=60%)
  T4  T3 AND positive_months >= 25 (>=70%)
"""
from __future__ import annotations

import asyncio
import itertools
import json
import sys
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
KLINES_CACHE = PROJECT_ROOT / "data" / "perp_meta" / "BTCUSDT_15m_klines.parquet"
RESULTS = PROJECT_ROOT / "scripts" / "_funding_alpha_3y_monthly_results.jsonl"
SUMMARY = PROJECT_ROOT / "scripts" / "_funding_alpha_3y_monthly_summary.md"
COMMISSION = 0.0002

START = datetime(2023, 4, 29, tzinfo=timezone.utc)
END = datetime(2026, 4, 29, tzinfo=timezone.utc)


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


def load_klines():
    if KLINES_CACHE.exists():
        df = pd.read_parquet(KLINES_CACHE)
        ts = df["ts"].to_numpy(dtype="int64")
        return ts, df["o"].to_numpy(), df["h"].to_numpy(), df["l"].to_numpy(), df["c"].to_numpy()
    klines = asyncio.run(fetch_klines(datetime(2020, 9, 1, tzinfo=timezone.utc), END, "15m"))
    ts = np.array([int(k[0]) for k in klines], dtype="int64")
    o = np.array([float(k[1]) for k in klines], dtype="float64")
    h = np.array([float(k[2]) for k in klines], dtype="float64")
    l = np.array([float(k[3]) for k in klines], dtype="float64")
    c = np.array([float(k[4]) for k in klines], dtype="float64")
    pd.DataFrame({"ts": ts, "o": o, "h": h, "l": l, "c": c}).to_parquet(KLINES_CACHE, index=False)
    return ts, o, h, l, c


def load_funding() -> pd.DataFrame:
    df = pd.read_parquet(FUNDING_PARQUET).rename(
        columns={"funding_time": "fundingTime", "funding_rate": "fundingRate"}
    )
    df["fundingTime"] = df["fundingTime"].astype("int64")
    df["fundingRate"] = df["fundingRate"].astype("float64")
    return df.sort_values("fundingTime").reset_index(drop=True)


def simulate(ts, o, h, l, c, fwt, fwr, pos_thr, neg_thr, hold_bars, tp_pct, sl_pct):
    entries = np.searchsorted(ts, fwt, side="left")
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
        rate = fwr[k_idx]
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
            "ret": equity - 1.0, "pf": pf, "dd": max_dd,
            "gp": gross_profit, "gl": gross_loss}


def months(start: datetime, end: datetime):
    cur = start.replace(day=1)
    # snap to first month containing `start`
    out = []
    while cur < end:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        s = max(cur, start)
        e = min(nxt, end)
        if s < e:
            out.append((f"{cur:%Y-%m}", s, e))
        cur = nxt
    return out


def main():
    print("[load] funding")
    funding = load_funding()
    print(f"  {len(funding)} rows")

    print("[load] klines")
    ts, o, h, l, c = load_klines()
    print(f"  {len(ts)} bars")

    ms = months(START, END)
    print(f"[months] {len(ms)} buckets: {ms[0][0]}..{ms[-1][0]}")

    pos_thr_grid = [1e-5, 2e-5, 3e-5, 5e-5, 7e-5, 1e-4]
    neg_thr_grid = [-1e-5, -2e-5, -3e-5, -5e-5, -7e-5, -1e-4]
    hold_h_grid = [8, 16, 24, 48, 72]
    tp_grid = [0.004, 0.006, 0.012, 0.020]
    sl_grid = [0.006, 0.012, 0.020]
    bars_per_h = 4

    n_combos = len(pos_thr_grid) * len(neg_thr_grid) * len(hold_h_grid) * len(tp_grid) * len(sl_grid)
    print(f"[sweep] {n_combos} combos x {len(ms)} months = {n_combos*len(ms)} sims")

    f_t = funding["fundingTime"].to_numpy(dtype="int64")
    f_r = funding["fundingRate"].to_numpy(dtype="float64")
    m_data = []
    for label, ms_dt, me_dt in ms:
        s_ms = int(ms_dt.timestamp() * 1000)
        e_ms = int(me_dt.timestamp() * 1000)
        mask = (f_t >= s_ms) & (f_t < e_ms)
        m_data.append((label, f_t[mask], f_r[mask]))

    if RESULTS.exists():
        RESULTS.unlink()

    written = 0
    with RESULTS.open("a", encoding="utf-8") as fout:
        for pos_thr, neg_thr, hold_h, tp, sl in itertools.product(
                pos_thr_grid, neg_thr_grid, hold_h_grid, tp_grid, sl_grid):
            hold_bars = hold_h * bars_per_h
            agg_eq = 1.0
            agg_peak = 1.0
            agg_dd = 0.0
            agg_trades = 0
            agg_gp = 0.0
            agg_gl = 0.0
            month_rets = []
            months_dict = {}
            for label, fwt, fwr in m_data:
                st = simulate(ts, o, h, l, c, fwt, fwr, pos_thr, neg_thr, hold_bars, tp, sl)
                months_dict[label] = {
                    "trades": st["trades"], "ret": round(st["ret"], 5),
                    "pf": round(st["pf"], 3) if st["pf"] != float("inf") else None,
                    "L": st["longs"], "S": st["shorts"], "dd": round(st["dd"], 4),
                }
                agg_eq *= (1.0 + st["ret"])
                if agg_eq > agg_peak: agg_peak = agg_eq
                d = (agg_peak - agg_eq) / agg_peak
                if d > agg_dd: agg_dd = d
                agg_trades += st["trades"]
                agg_gp += st["gp"]
                agg_gl += st["gl"]
                month_rets.append(st["ret"])

            agg_pf = (agg_gp / agg_gl) if agg_gl > 1e-12 else (float("inf") if agg_gp > 0 else 0.0)
            pos_m = sum(1 for r in month_rets if r > 0)
            neg_m = sum(1 for r in month_rets if r < 0)

            row = {
                "pos_thr": pos_thr, "neg_thr": neg_thr, "hold_h": hold_h, "tp": tp, "sl": sl,
                "agg_ret": round(agg_eq - 1.0, 5),
                "agg_trades": agg_trades,
                "agg_pf": round(agg_pf, 3) if agg_pf != float("inf") else None,
                "agg_dd": round(agg_dd, 4),
                "pos_months": pos_m, "neg_months": neg_m,
                "months": months_dict,
            }
            fout.write(json.dumps(row) + "\n")
            written += 1
            if written % 200 == 0:
                print(f"  ...{written}/{n_combos}")

    print(f"[done] wrote {written} -> {RESULTS}")

    # ---- summarize / acceptance counts ----
    rows = [json.loads(l) for l in RESULTS.open(encoding="utf-8")]

    def passes_t1(r): return r["agg_ret"] > 0
    def passes_t2(r):
        return passes_t1(r) and (r["agg_pf"] is not None and r["agg_pf"] >= 1.1) \
            and r["agg_trades"] >= 30 and r["agg_dd"] <= 0.30
    def passes_t3(r): return passes_t2(r) and r["pos_months"] >= 22  # >=60%
    def passes_t4(r): return passes_t3(r) and r["pos_months"] >= 25  # ~70%

    t1 = [r for r in rows if passes_t1(r)]
    t2 = [r for r in rows if passes_t2(r)]
    t3 = [r for r in rows if passes_t3(r)]
    t4 = [r for r in rows if passes_t4(r)]

    print()
    print(f"=== Acceptance counts (out of {len(rows)} combos, 36 months) ===")
    print(f"  T1  agg_ret > 0                                      : {len(t1)}")
    print(f"  T2  +PF>=1.1 +trades>=30 +DD<=30%                    : {len(t2)}")
    print(f"  T3  +>=22/36 positive months (>=60%)                 : {len(t3)}")
    print(f"  T4  +>=25/36 positive months (~70%)                  : {len(t4)}")

    def fmt(r):
        return (f"pos=+{r['pos_thr']:.0e} neg={r['neg_thr']:.0e} "
                f"hold={r['hold_h']}h tp={r['tp']:.3f} sl={r['sl']:.3f} | "
                f"agg={r['agg_ret']*100:+.1f}% trades={r['agg_trades']} "
                f"pf={r['agg_pf']} dd={r['agg_dd']*100:.1f}% +M={r['pos_months']}/{r['pos_months']+r['neg_months']}")

    with SUMMARY.open("w", encoding="utf-8") as f:
        f.write(f"# Funding contrarian alpha — 3y monthly walk-forward\n\n")
        f.write(f"Window: 2023-04-29 .. 2026-04-29 ({len(rows[0]['months'])} months)\n\n")
        f.write(f"Grid: pos {pos_thr_grid}, neg {neg_thr_grid}, hold {hold_h_grid}h, tp {tp_grid}, sl {sl_grid} = **{len(rows)} combos**\n\n")
        f.write(f"## Acceptance counts\n\n")
        f.write(f"| Tier | Criterion | Count |\n|---|---|---:|\n")
        f.write(f"| T1 | agg_ret > 0 | {len(t1)} |\n")
        f.write(f"| T2 | T1 + PF>=1.1 + trades>=30 + DD<=30% | {len(t2)} |\n")
        f.write(f"| T3 | T2 + positive months >= 22 (>=60%) | {len(t3)} |\n")
        f.write(f"| T4 | T2 + positive months >= 25 (~70%) | {len(t4)} |\n\n")

        for tier, label in [(t2, "T2 (basic acceptance)"), (t3, "T3 (>=60% +months)"), (t4, "T4 (~70% +months)")]:
            f.write(f"## {label} — top 10 by agg_ret\n\n")
            for r in sorted(tier, key=lambda x: -x["agg_ret"])[:10]:
                f.write(f"- {fmt(r)}\n")
            f.write("\n")

        # Also show top 10 by agg_ret overall and top 10 by pos_months
        f.write(f"## Top 10 by agg_ret (any tier)\n\n")
        for r in sorted(rows, key=lambda x: -x["agg_ret"])[:10]:
            f.write(f"- {fmt(r)}\n")
        f.write("\n")
        f.write(f"## Top 10 by positive months\n\n")
        for r in sorted(rows, key=lambda x: (-x["pos_months"], -x["agg_ret"]))[:10]:
            f.write(f"- {fmt(r)}\n")
        f.write("\n")

        # Per-month trace of top-1 by agg_ret
        if rows:
            top = max(rows, key=lambda x: x["agg_ret"])
            f.write(f"## Per-month trace — best agg_ret combo\n\n")
            f.write(f"`{fmt(top)}`\n\n")
            f.write(f"| Month | ret | trades | pf | L/S | dd |\n|---|---:|---:|---:|---|---:|\n")
            for label, d in top["months"].items():
                f.write(f"| {label} | {d['ret']*100:+.2f}% | {d['trades']} | {d['pf']} | {d['L']}/{d['S']} | {d['dd']*100:.1f}% |\n")

    print(f"[summary] -> {SUMMARY}")
    print()
    print("Top 5 by agg_ret:")
    for r in sorted(rows, key=lambda x: -x["agg_ret"])[:5]:
        print("  " + fmt(r))


if __name__ == "__main__":
    main()
