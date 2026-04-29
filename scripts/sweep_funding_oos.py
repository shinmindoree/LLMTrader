"""Train/test OOS validation for funding contrarian alpha (BTCUSDT-PERP).

Reuses the same simulator as sweep_funding_3y_monthly.py but splits the 3y
window into:
  TRAIN: 2023-04-29 .. 2025-04-28  (24 months)
  TEST : 2025-04-29 .. 2026-04-29  (12 months)

For each combo we compute monthly stats on both halves, then:
  1) Pick combos that pass acceptance on TRAIN only.
  2) Evaluate those same combos on TEST (no parameter tuning).
  3) Report how many survive on TEST and the median/mean OOS performance.
"""
from __future__ import annotations

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

FUNDING_PARQUET = PROJECT_ROOT / "data" / "perp_meta" / "BTCUSDT_funding.parquet"
KLINES_CACHE = PROJECT_ROOT / "data" / "perp_meta" / "BTCUSDT_15m_klines.parquet"
RESULTS = PROJECT_ROOT / "scripts" / "_funding_alpha_oos_results.jsonl"
SUMMARY = PROJECT_ROOT / "scripts" / "_funding_alpha_oos_summary.md"
COMMISSION = 0.0002

TRAIN_START = datetime(2023, 4, 29, tzinfo=timezone.utc)
TRAIN_END = datetime(2025, 4, 29, tzinfo=timezone.utc)
TEST_START = TRAIN_END
TEST_END = datetime(2026, 4, 29, tzinfo=timezone.utc)


def load_klines():
    df = pd.read_parquet(KLINES_CACHE)
    return (df["ts"].to_numpy(dtype="int64"),
            df["o"].to_numpy(), df["h"].to_numpy(),
            df["l"].to_numpy(), df["c"].to_numpy())


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
    gp = 0.0
    gl = 0.0
    trades = longs = shorts = 0
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
        if net > 0: gp += net
        else: gl += -net
        trades += 1
        if side == 1: longs += 1
        else: shorts += 1
        busy_until = end_bar

    pf = (gp / gl) if gl > 1e-12 else (float("inf") if gp > 0 else 0.0)
    return {"trades": trades, "longs": longs, "shorts": shorts,
            "ret": equity - 1.0, "pf": pf, "dd": max_dd, "gp": gp, "gl": gl}


def months(start: datetime, end: datetime):
    cur = start.replace(day=1)
    out = []
    while cur < end:
        nxt = cur.replace(year=cur.year + (1 if cur.month == 12 else 0),
                          month=1 if cur.month == 12 else cur.month + 1)
        s = max(cur, start)
        e = min(nxt, end)
        if s < e:
            out.append((f"{cur:%Y-%m}", s, e))
        cur = nxt
    return out


def split_eval(ts, o, h, l, c, m_data, pos_thr, neg_thr, hold_bars, tp, sl):
    """Run per-month sim across all months in m_data and return aggregate stats."""
    agg_eq = 1.0
    agg_peak = 1.0
    agg_dd = 0.0
    trades = 0
    gp = 0.0
    gl = 0.0
    pos_m = neg_m = 0
    longs = shorts = 0
    for _label, fwt, fwr in m_data:
        st = simulate(ts, o, h, l, c, fwt, fwr, pos_thr, neg_thr, hold_bars, tp, sl)
        agg_eq *= (1.0 + st["ret"])
        if agg_eq > agg_peak: agg_peak = agg_eq
        d = (agg_peak - agg_eq) / agg_peak
        if d > agg_dd: agg_dd = d
        trades += st["trades"]
        gp += st["gp"]; gl += st["gl"]
        longs += st["longs"]; shorts += st["shorts"]
        if st["ret"] > 0: pos_m += 1
        elif st["ret"] < 0: neg_m += 1
    pf = (gp / gl) if gl > 1e-12 else (float("inf") if gp > 0 else 0.0)
    return {
        "agg_ret": agg_eq - 1.0,
        "agg_trades": trades,
        "agg_pf": pf,
        "agg_dd": agg_dd,
        "pos_months": pos_m,
        "neg_months": neg_m,
        "longs": longs, "shorts": shorts,
    }


def slice_funding(funding, start, end):
    f_t = funding["fundingTime"].to_numpy(dtype="int64")
    f_r = funding["fundingRate"].to_numpy(dtype="float64")
    out = []
    for label, ms_dt, me_dt in months(start, end):
        s_ms = int(ms_dt.timestamp() * 1000)
        e_ms = int(me_dt.timestamp() * 1000)
        mask = (f_t >= s_ms) & (f_t < e_ms)
        out.append((label, f_t[mask], f_r[mask]))
    return out


def main():
    print("[load]")
    funding = load_funding()
    ts, o, h, l, c = load_klines()
    print(f"  funding={len(funding)}  klines={len(ts)}")
    train_m = slice_funding(funding, TRAIN_START, TRAIN_END)
    test_m = slice_funding(funding, TEST_START, TEST_END)
    print(f"[split] train={len(train_m)} months ({train_m[0][0]}..{train_m[-1][0]}) "
          f"test={len(test_m)} months ({test_m[0][0]}..{test_m[-1][0]})")

    pos_thr_grid = [1e-5, 2e-5, 3e-5, 5e-5, 7e-5, 1e-4]
    neg_thr_grid = [-1e-5, -2e-5, -3e-5, -5e-5, -7e-5, -1e-4]
    hold_h_grid = [8, 16, 24, 48, 72]
    tp_grid = [0.004, 0.006, 0.012, 0.020]
    sl_grid = [0.006, 0.012, 0.020]
    bars_per_h = 4

    n = len(pos_thr_grid)*len(neg_thr_grid)*len(hold_h_grid)*len(tp_grid)*len(sl_grid)
    print(f"[sweep] {n} combos x (24+12) months")

    if RESULTS.exists():
        RESULTS.unlink()
    rows = []
    written = 0
    with RESULTS.open("a", encoding="utf-8") as fout:
        for pos_thr, neg_thr, hold_h, tp, sl in itertools.product(
                pos_thr_grid, neg_thr_grid, hold_h_grid, tp_grid, sl_grid):
            hb = hold_h * bars_per_h
            tr = split_eval(ts, o, h, l, c, train_m, pos_thr, neg_thr, hb, tp, sl)
            te = split_eval(ts, o, h, l, c, test_m, pos_thr, neg_thr, hb, tp, sl)
            row = {
                "pos_thr": pos_thr, "neg_thr": neg_thr, "hold_h": hold_h, "tp": tp, "sl": sl,
                "train": {k: (round(v, 5) if isinstance(v, float) else v) for k, v in tr.items()},
                "test":  {k: (round(v, 5) if isinstance(v, float) else v) for k, v in te.items()},
            }
            fout.write(json.dumps(row) + "\n")
            rows.append(row)
            written += 1
            if written % 200 == 0:
                print(f"  ...{written}/{n}")
    print(f"[done] {written} rows -> {RESULTS}")

    # ----- Acceptance on TRAIN only -----
    def passes_train_t2(r):
        t = r["train"]
        return t["agg_ret"] > 0 and (t["agg_pf"] is not None and t["agg_pf"] >= 1.1) \
            and t["agg_trades"] >= 20 and t["agg_dd"] <= 0.30

    def passes_train_t3(r):
        # T2 + >=60% positive months on train (24 months -> 14 positive)
        return passes_train_t2(r) and r["train"]["pos_months"] >= 14

    def passes_train_t4(r):
        # T2 + >=70% positive months on train
        return passes_train_t2(r) and r["train"]["pos_months"] >= 17

    def survives_test(r, min_pf=1.0):
        t = r["test"]
        return t["agg_ret"] > 0 and (t["agg_pf"] is not None and t["agg_pf"] >= min_pf)

    train_t2 = [r for r in rows if passes_train_t2(r)]
    train_t3 = [r for r in rows if passes_train_t3(r)]
    train_t4 = [r for r in rows if passes_train_t4(r)]

    def survival(tier):
        if not tier: return (0, 0, 0.0, 0.0)
        surv = [r for r in tier if survives_test(r)]
        ret_med = float(np.median([r["test"]["agg_ret"] for r in tier]))
        ret_mean = float(np.mean([r["test"]["agg_ret"] for r in tier]))
        return (len(tier), len(surv), ret_med, ret_mean)

    n_t2, s_t2, med_t2, mean_t2 = survival(train_t2)
    n_t3, s_t3, med_t3, mean_t3 = survival(train_t3)
    n_t4, s_t4, med_t4, mean_t4 = survival(train_t4)

    print()
    print(f"=== Train/Test OOS results (out of {len(rows)} combos) ===")
    print(f"  Train tier      | n_train | OOS positive | OOS test_ret median / mean")
    print(f"  T2 (PF>=1.1)    | {n_t2:>7} | {s_t2:>12} | {med_t2*100:+.2f}% / {mean_t2*100:+.2f}%")
    print(f"  T3 (>=60% +M)   | {n_t3:>7} | {s_t3:>12} | {med_t3*100:+.2f}% / {mean_t3*100:+.2f}%")
    print(f"  T4 (>=70% +M)   | {n_t4:>7} | {s_t4:>12} | {med_t4*100:+.2f}% / {mean_t4*100:+.2f}%")

    # Best train -> show test
    def fmt(r):
        return (f"pos=+{r['pos_thr']:.0e} neg={r['neg_thr']:.0e} "
                f"hold={r['hold_h']}h tp={r['tp']:.3f} sl={r['sl']:.3f}")

    def line(r):
        tr = r["train"]; te = r["test"]
        return (f"  {fmt(r)} | "
                f"TRAIN agg={tr['agg_ret']*100:+.1f}% pf={tr['agg_pf']:.2f} "
                f"dd={tr['agg_dd']*100:.1f}% +M={tr['pos_months']}/24 trades={tr['agg_trades']} | "
                f"TEST agg={te['agg_ret']*100:+.1f}% pf={te['agg_pf']:.2f} "
                f"dd={te['agg_dd']*100:.1f}% +M={te['pos_months']}/12 trades={te['agg_trades']}")

    # Top by train agg
    print()
    print("=== Top 10 by TRAIN agg_ret (passing T2 train) ===")
    for r in sorted(train_t2, key=lambda x: -x["train"]["agg_ret"])[:10]:
        print(line(r))
    print()
    print("=== Top 10 by TRAIN +months (passing T2 train) ===")
    for r in sorted(train_t2, key=lambda x: (-x["train"]["pos_months"], -x["train"]["agg_ret"]))[:10]:
        print(line(r))

    # Single best train pick: highest train agg with PF>=1.1 -> see test
    if train_t2:
        best = max(train_t2, key=lambda x: x["train"]["agg_ret"])
        print()
        print("=== Best by TRAIN agg ===")
        print(line(best))

    with SUMMARY.open("w", encoding="utf-8") as f:
        f.write(f"# Funding contrarian — Train/Test OOS\n\n")
        f.write(f"- TRAIN: {TRAIN_START:%Y-%m-%d} .. {TRAIN_END:%Y-%m-%d} (24 months)\n")
        f.write(f"- TEST : {TEST_START:%Y-%m-%d} .. {TEST_END:%Y-%m-%d} (12 months)\n")
        f.write(f"- Grid: {len(rows)} combos\n\n")
        f.write(f"## Train acceptance -> OOS survival\n\n")
        f.write(f"| Train tier | Combos pass | OOS test_ret > 0 | OOS test_ret median | OOS test_ret mean |\n")
        f.write(f"|---|---:|---:|---:|---:|\n")
        f.write(f"| T2 (PF>=1.1, trades>=20, DD<=30%) | {n_t2} | {s_t2} ({(s_t2/max(n_t2,1))*100:.0f}%) | {med_t2*100:+.2f}% | {mean_t2*100:+.2f}% |\n")
        f.write(f"| T3 (T2 + >=60% +months) | {n_t3} | {s_t3} ({(s_t3/max(n_t3,1))*100:.0f}%) | {med_t3*100:+.2f}% | {mean_t3*100:+.2f}% |\n")
        f.write(f"| T4 (T2 + >=70% +months) | {n_t4} | {s_t4} ({(s_t4/max(n_t4,1))*100:.0f}%) | {med_t4*100:+.2f}% | {mean_t4*100:+.2f}% |\n\n")
        f.write(f"## Top 10 by TRAIN agg_ret (passing T2)\n\n")
        for r in sorted(train_t2, key=lambda x: -x["train"]["agg_ret"])[:10]:
            f.write(f"- {line(r).strip()}\n")
        f.write("\n## Top 10 by TRAIN +months (passing T2)\n\n")
        for r in sorted(train_t2, key=lambda x: (-x["train"]["pos_months"], -x["train"]["agg_ret"]))[:10]:
            f.write(f"- {line(r).strip()}\n")
        f.write("\n## All T4 train combos (their OOS test result)\n\n")
        for r in sorted(train_t4, key=lambda x: -x["train"]["agg_ret"]):
            f.write(f"- {line(r).strip()}\n")

    print(f"[summary] -> {SUMMARY}")


if __name__ == "__main__":
    main()
