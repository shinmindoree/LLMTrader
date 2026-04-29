"""Monthly breakdown of the winning OI capitulation-bottom alpha — last 36 months."""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from micro_alpha_lib import (
    load_klines_15m, load_micro, align_to_klines,
    build_signal_oi_price, simulate_signal,
)

kl = load_klines_15m()
ts = kl["ts"].to_numpy(dtype="int64")
o = kl["o"].to_numpy(); h = kl["h"].to_numpy()
l = kl["l"].to_numpy(); c = kl["c"].to_numpy()
months_arr = pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m").to_numpy()

oi = align_to_klines(ts, load_micro("oi"))
sig = build_signal_oi_price(oi, c, win=96, k_oi=0.020, k_p=0.005, mode="oi_down_p_down_long")
res = simulate_signal(ts, o, h, l, c, sig, hold_bars=48*4, tp_pct=0.020, sl_pct=0.012, months_arr=months_arr)

# Last 36 months ending 2026-04
end = datetime(2026, 5, 1, tzinfo=timezone.utc)
months = []
cur = datetime(2023, 5, 1, tzinfo=timezone.utc)
while cur < end:
    months.append(cur.strftime("%Y-%m"))
    y, m = cur.year, cur.month
    cur = datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)

print(f"{'Month':<8} {'Trades':>7} {'L':>3} {'S':>3} {'Ret%':>8} {'PF':>6} {'GP%':>7} {'GL%':>7} {'CumEq':>8}")
print("-" * 70)
eq = 1.0
total_t = 0; total_pos = 0; total_neg = 0; total_zero = 0
gps = []; gls = []
for ym in months:
    m = res["monthly"].get(ym)
    if not m or m["trades"] == 0:
        total_zero += 1
        print(f"{ym:<8} {'-':>7} {'-':>3} {'-':>3} {'0.00':>8} {'-':>6} {'-':>7} {'-':>7} {eq:>8.4f}")
        continue
    ret = m["eq"] - 1.0
    pf = (m["gp"]/m["gl"]) if m["gl"] > 1e-12 else (float("inf") if m["gp"]>0 else 0.0)
    eq *= (1 + ret)
    total_t += m["trades"]
    if ret > 0: total_pos += 1
    elif ret < 0: total_neg += 1
    else: total_zero += 1
    print(f"{ym:<8} {m['trades']:>7d} {m['L']:>3d} {m['S']:>3d} {ret*100:>+7.2f}% {pf:>6.2f} {m['gp']*100:>6.2f}% {m['gl']*100:>6.2f}% {eq:>8.4f}")

print("-" * 70)
print(f"36m: trades={total_t}  +M={total_pos}  -M={total_neg}  0M={total_zero}  cumret={(eq-1)*100:+.2f}%")

# DD on monthly equity curve
eq_curve = []
cur_eq = 1.0
for ym in months:
    m = res["monthly"].get(ym)
    r = (m["eq"]-1.0) if (m and m["trades"]>0) else 0.0
    cur_eq *= (1+r)
    eq_curve.append(cur_eq)
arr = np.array(eq_curve)
peak = np.maximum.accumulate(arr)
dd = (peak - arr) / peak
print(f"36m max DD (monthly): {dd.max()*100:.2f}%")

# yearly
print("\n=== Yearly ===")
yrs = {}
for ym in months:
    m = res["monthly"].get(ym)
    if not m or m["trades"]==0: continue
    yr = ym[:4]
    yrs.setdefault(yr, {"t":0,"eq":1.0,"pos":0,"neg":0})
    yrs[yr]["t"] += m["trades"]
    yrs[yr]["eq"] *= m["eq"]
    if m["eq"]>1: yrs[yr]["pos"] += 1
    elif m["eq"]<1: yrs[yr]["neg"] += 1
for yr in sorted(yrs):
    y = yrs[yr]
    print(f"  {yr}: trades={y['t']:>4} ret={(y['eq']-1)*100:+7.2f}% +M={y['pos']} -M={y['neg']}")
