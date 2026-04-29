"""Iter 1 — LSR contrarian alpha sweeps + train/test OOS.
Tests three LSR feature variants with z-score thresholds, contrarian direction.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from micro_alpha_lib import (  # noqa: E402
    load_klines_15m, load_micro, align_to_klines,
    run_z_sweep, passes_train, survives_test, fmt_row,
    TRAIN_START, TRAIN_END, TEST_START, TEST_END,
)

OUT_RESULTS = Path(__file__).parent / "_micro_iter1_lsr_results.jsonl"
OUT_SUMMARY = Path(__file__).parent / "_micro_iter1_lsr_summary.md"


def main():
    print("[load] klines 15m")
    kl = load_klines_15m()
    print(f"  {len(kl)} bars  {kl['dt'].iloc[0]}..{kl['dt'].iloc[-1]}")

    feats = {}
    for fk in ("lsr_top_pos", "lsr_top_acc", "lsr_acc"):
        m = load_micro(fk)
        feats[fk] = align_to_klines(kl["ts"].to_numpy(dtype="int64"), m)
        v = feats[fk]
        v_finite = v[np.isfinite(v)]
        print(f"  {fk}: n={len(v):,}  nan={np.isnan(v).sum():,}  "
              f"mean={v_finite.mean():.3f} std={v_finite.std():.3f}")

    grids = {
        "win": [96, 192, 384],          # 24h, 48h, 96h
        "k": [1.0, 1.5, 2.0, 2.5],
        "hold": [8, 24, 48],            # hours
        "tp": [0.006, 0.012, 0.020],
        "sl": [0.012, 0.020],
    }

    all_rows = []
    for fk in ("lsr_top_pos", "lsr_top_acc", "lsr_acc"):
        rows = run_z_sweep(kl, feats[fk], fk, "contra", grids, label="iter1")
        all_rows.extend(rows)

    print(f"[done] {len(all_rows)} rows")

    # write
    if OUT_RESULTS.exists(): OUT_RESULTS.unlink()
    with OUT_RESULTS.open("w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r) + "\n")

    # acceptance
    train_ok = [r for r in all_rows if passes_train(r)]
    surv = [r for r in train_ok if survives_test(r)]
    print()
    print(f"=== Iter1 LSR contrarian acceptance (out of {len(all_rows)}) ===")
    print(f"  TRAIN passes : {len(train_ok)}")
    print(f"  TEST survives: {len(surv)}")
    if train_ok:
        med = float(np.median([r["test"]["agg_ret"] for r in train_ok]))
        mean = float(np.mean([r["test"]["agg_ret"] for r in train_ok]))
        print(f"  OOS test_ret median/mean across train_ok: {med*100:+.2f}% / {mean*100:+.2f}%")

    print()
    print("=== Top 10 OOS survivors (by test agg_ret) ===")
    for r in sorted(surv, key=lambda x: -x["test"]["agg_ret"])[:10]:
        print("  " + fmt_row(r))

    print()
    print("=== Top 10 train passes (by train agg_ret), with their OOS ===")
    for r in sorted(train_ok, key=lambda x: -x["train"]["agg_ret"])[:10]:
        print("  " + fmt_row(r))

    with OUT_SUMMARY.open("w", encoding="utf-8") as f:
        f.write("# Iter1 — LSR contrarian (z-score thresholds)\n\n")
        f.write(f"Total combos: {len(all_rows)} (3 features × 4 wins × 5 k × 5 hold × 4 tp × 4 sl)\n")
        f.write(f"TRAIN: {TRAIN_START:%Y-%m-%d}..{TRAIN_END:%Y-%m-%d}  TEST: {TEST_START:%Y-%m-%d}..{TEST_END:%Y-%m-%d}\n\n")
        f.write(f"## Acceptance counts\n")
        f.write(f"- TRAIN T2 pass (PF>=1.1, trades>=20, DD<=30%, +M>=14/24): **{len(train_ok)}**\n")
        f.write(f"- OOS survivors (PF>=1.05, trades>=15, DD<=20%, +M>=6/12, ret>0): **{len(surv)}**\n\n")
        f.write("## Top 20 OOS survivors\n")
        for r in sorted(surv, key=lambda x: -x["test"]["agg_ret"])[:20]:
            f.write(f"- {fmt_row(r)}\n")
        f.write("\n## Top 20 TRAIN passes (any OOS)\n")
        for r in sorted(train_ok, key=lambda x: -x["train"]["agg_ret"])[:20]:
            f.write(f"- {fmt_row(r)}\n")

    print(f"[summary] -> {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
