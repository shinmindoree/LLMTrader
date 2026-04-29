"""Iter 2 — Taker imbalance contrarian/follow + Iter 3 OI-price regimes."""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from micro_alpha_lib import (  # noqa: E402
    load_klines_15m, load_micro, align_to_klines,
    run_z_sweep, run_oi_sweep, passes_train, survives_test, fmt_row,
    TRAIN_START, TRAIN_END, TEST_START, TEST_END,
)

OUT_RES = Path(__file__).parent / "_micro_iter23_results.jsonl"
OUT_SUM = Path(__file__).parent / "_micro_iter23_summary.md"


def main():
    print("[load] klines 15m")
    kl = load_klines_15m()
    print(f"  {len(kl)} bars")

    taker_arr = align_to_klines(kl["ts"].to_numpy(dtype="int64"), load_micro("taker"))
    oi_arr = align_to_klines(kl["ts"].to_numpy(dtype="int64"), load_micro("oi"))

    grids_z = {
        "win": [96, 192, 384],
        "k": [1.0, 1.5, 2.0, 2.5],
        "hold": [8, 24, 48],
        "tp": [0.006, 0.012, 0.020],
        "sl": [0.012, 0.020],
    }
    grids_oi = {
        "win": [4, 16, 96],   # 1h, 4h, 24h delta
        "k_oi": [0.005, 0.01, 0.02],
        "k_p": [0.005, 0.01, 0.02],
        "hold": [8, 24, 48],
        "tp": [0.006, 0.012, 0.020],
        "sl": [0.012, 0.020],
    }

    rows = []
    # Iter2: taker (contra + follow)
    for direction in ("contra", "follow"):
        rows.extend(run_z_sweep(kl, taker_arr, "taker", direction, grids_z, label="iter2"))

    # Iter3: OI x price regimes (try shorts on oi-up+p-up = exhaustion long, longs on oi-up+p-down = squeeze, etc.)
    for mode in ("oi_up_p_down_long", "oi_up_p_up_long", "oi_down_p_up_short",
                 "oi_down_p_down_long", "oi_up_p_down_short", "oi_up_p_up_short"):
        rows.extend(run_oi_sweep(kl, oi_arr, mode, grids_oi, label="iter3"))

    print(f"[done] {len(rows)} rows")
    if OUT_RES.exists(): OUT_RES.unlink()
    with OUT_RES.open("w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

    train_ok = [r for r in rows if passes_train(r)]
    surv = [r for r in train_ok if survives_test(r)]
    print()
    print(f"=== Iter2+3 acceptance (out of {len(rows)}) ===")
    print(f"  TRAIN passes : {len(train_ok)}")
    print(f"  TEST survives: {len(surv)}")

    print("\n=== Top 15 OOS survivors (by test agg_ret) ===")
    for r in sorted(surv, key=lambda x: -x["test"]["agg_ret"])[:15]:
        print("  " + fmt_row(r))

    # Group survivors by feature/mode
    from collections import Counter
    cnt = Counter((r.get("feature","?"), r.get("mode","?")) for r in surv)
    print("\n=== Survivor distribution by (feature, mode) ===")
    for (f, m), c in cnt.most_common():
        print(f"  {f:12} {m:25} : {c}")

    with OUT_SUM.open("w", encoding="utf-8") as f:
        f.write("# Iter2+3 — Taker / OI sweeps with train/test OOS\n\n")
        f.write(f"Total combos: {len(rows)}\n")
        f.write(f"TRAIN passes: {len(train_ok)} | OOS survivors: {len(surv)}\n\n")
        f.write("## Top 30 OOS survivors\n")
        for r in sorted(surv, key=lambda x: -x["test"]["agg_ret"])[:30]:
            f.write(f"- {fmt_row(r)}\n")
        f.write("\n## Top 20 train passes (by train agg_ret)\n")
        for r in sorted(train_ok, key=lambda x: -x["train"]["agg_ret"])[:20]:
            f.write(f"- {fmt_row(r)}\n")
        f.write("\n## Survivor distribution by (feature,mode)\n")
        for (fk, m), c in cnt.most_common():
            f.write(f"- {fk} / {m}: {c}\n")

    print(f"\n[summary] -> {OUT_SUM}")


if __name__ == "__main__":
    main()
