"""Iter4 — Robustness validation for top OOS survivors from iter1 (LSR) + iter23 (OI/taker).

Runs each candidate strategy across the full available history (2020-09 .. 2026-04, ~68 months)
and slices results into rolling 6-month windows AND non-overlapping quarters
to verify the alpha is not a single-window artifact.

Acceptance for "live-tradable robust":
  - >=70% of 6m rolling windows (with >=10 trades) are positive
  - No single quarter <= -10%
  - Worst rolling 6m DD <= 25%
  - 95% trade-count CI not collapsing (i.e., consistent activity across windows)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from micro_alpha_lib import (
    load_klines_15m, load_micro, align_to_klines,
    build_signal_z, build_signal_oi_price, simulate_signal,
    aggregate, slice_monthly, COMMISSION,
)

OUT_RESULTS = Path(__file__).parent / "_micro_iter4_robust_results.jsonl"
OUT_SUMMARY = Path(__file__).parent / "_micro_iter4_robust_summary.md"

# Top candidates: handpicked from iter1 (LSR) and iter23 (OI), best OOS survivors.
CANDIDATES = [
    # iter1 LSR contrarian winner
    {"id":"lsr_top_pos_w96_k2.5_h48_tp1.2_sl2.0",
     "kind":"z","feature":"lsr_top_pos","direction":"contra",
     "win":96,"k":2.5,"hold_h":48,"tp":0.012,"sl":0.020},
    {"id":"lsr_top_pos_w96_k2.5_h24_tp1.2_sl2.0",
     "kind":"z","feature":"lsr_top_pos","direction":"contra",
     "win":96,"k":2.5,"hold_h":24,"tp":0.012,"sl":0.020},
    {"id":"lsr_acc_w96_k2.0_h24_tp0.6_sl2.0",
     "kind":"z","feature":"lsr_acc","direction":"contra",
     "win":96,"k":2.0,"hold_h":24,"tp":0.006,"sl":0.020},
    {"id":"lsr_top_pos_w96_k2.5_h48_tp0.6_sl2.0",
     "kind":"z","feature":"lsr_top_pos","direction":"contra",
     "win":96,"k":2.5,"hold_h":48,"tp":0.006,"sl":0.020},
    # iter3 OI winners
    {"id":"oi_up_p_down_long_w4_koi0.5_kp0.5_h8_tp1.2_sl2.0",
     "kind":"oi","mode":"oi_up_p_down_long",
     "win":4,"k_oi":0.005,"k_p":0.005,"hold_h":8,"tp":0.012,"sl":0.020},
    {"id":"oi_down_p_up_short_w4_koi0.5_kp1.0_h48_tp2.0_sl2.0",
     "kind":"oi","mode":"oi_down_p_up_short",
     "win":4,"k_oi":0.005,"k_p":0.010,"hold_h":48,"tp":0.020,"sl":0.020},
    {"id":"oi_down_p_up_short_w4_koi0.5_kp1.0_h24_tp2.0_sl2.0",
     "kind":"oi","mode":"oi_down_p_up_short",
     "win":4,"k_oi":0.005,"k_p":0.010,"hold_h":24,"tp":0.020,"sl":0.020},
    {"id":"oi_down_p_down_long_w96_koi2.0_kp0.5_h48_tp2.0_sl1.2",
     "kind":"oi","mode":"oi_down_p_down_long",
     "win":96,"k_oi":0.020,"k_p":0.005,"hold_h":48,"tp":0.020,"sl":0.012},
    {"id":"oi_up_p_up_short_w4_koi0.5_kp0.5_h48_tp0.6_sl2.0",
     "kind":"oi","mode":"oi_up_p_up_short",
     "win":4,"k_oi":0.005,"k_p":0.005,"hold_h":48,"tp":0.006,"sl":0.020},
    {"id":"oi_up_p_down_long_w4_koi0.5_kp1.0_h8_tp1.2_sl2.0",
     "kind":"oi","mode":"oi_up_p_down_long",
     "win":4,"k_oi":0.005,"k_p":0.010,"hold_h":8,"tp":0.012,"sl":0.020},
]


def quarters_between(start: datetime, end: datetime):
    """Yield (qstart, qend) quarter-window pairs."""
    cur = start
    while cur < end:
        y, m = cur.year, cur.month
        # advance to next quarter end
        m_end = ((m - 1) // 3 + 1) * 3
        if m_end >= 12:
            qend = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        else:
            qend = datetime(y, m_end + 1, 1, tzinfo=timezone.utc)
        if qend > end:
            qend = end
        yield cur, qend
        cur = qend


def rolling_6m_windows(start: datetime, end: datetime):
    """Non-overlapping 6m windows."""
    cur = start
    while cur < end:
        ne = cur
        for _ in range(6):
            y, m = ne.year, ne.month
            ne = datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
        if ne > end:
            ne = end
        if (ne - cur).days < 90:
            break
        yield cur, ne
        cur = ne


def main():
    print("[load] klines + features")
    kl = load_klines_15m()
    ts = kl["ts"].to_numpy(dtype="int64")
    o = kl["o"].to_numpy(); h = kl["h"].to_numpy()
    l = kl["l"].to_numpy(); c = kl["c"].to_numpy()
    months_arr = pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m").to_numpy()

    feat_cache = {}
    for k in ("lsr_top_pos","lsr_acc","oi"):
        feat_cache[k] = align_to_klines(ts, load_micro(k))

    out_rows = []
    for cand in CANDIDATES:
        cid = cand["id"]
        if cand["kind"] == "z":
            sig = build_signal_z(feat_cache[cand["feature"]], cand["win"], cand["k"], cand["direction"])
        else:
            sig = build_signal_oi_price(feat_cache["oi"], c, cand["win"], cand["k_oi"], cand["k_p"], cand["mode"])
        bars = int(cand["hold_h"] * 4)
        res = simulate_signal(ts, o, h, l, c, sig, bars, cand["tp"], cand["sl"], months_arr=months_arr)
        monthly = res["monthly"]

        # Full sample: 2020-09..2026-04
        full_start = datetime(2020, 9, 1, tzinfo=timezone.utc)
        full_end = datetime(2026, 4, 29, tzinfo=timezone.utc)
        full_slice = slice_monthly(monthly, full_start, full_end)
        full_agg = aggregate(full_slice)

        # Quarterly windows
        qrows = []
        for qs, qe in quarters_between(full_start, full_end):
            sl = slice_monthly(monthly, qs, qe)
            ag = aggregate(sl)
            qrows.append({
                "q": qs.strftime("%Y-Q%d") % ((qs.month-1)//3+1) if False else qs.strftime("%Y-%m"),
                "qstart": qs.isoformat(), "qend": qe.isoformat(),
                "ret": ag["agg_ret"], "trades": ag["agg_trades"], "pf": ag["agg_pf"], "dd": ag["agg_dd"],
            })

        # Rolling 6m
        wrows = []
        for ws, we in rolling_6m_windows(full_start, full_end):
            sl = slice_monthly(monthly, ws, we)
            ag = aggregate(sl)
            wrows.append({
                "wstart": ws.isoformat(), "wend": we.isoformat(),
                "ret": ag["agg_ret"], "trades": ag["agg_trades"], "pf": ag["agg_pf"], "dd": ag["agg_dd"],
                "pos_m": ag["pos_months"], "neg_m": ag["neg_months"],
            })

        # Robustness scores
        active_qs = [q for q in qrows if q["trades"] >= 5]
        pos_qs = sum(1 for q in active_qs if q["ret"] > 0)
        worst_q = min((q["ret"] for q in active_qs), default=0.0)
        active_ws = [w for w in wrows if w["trades"] >= 10]
        pos_ws = sum(1 for w in active_ws if w["ret"] > 0)
        worst_w = min((w["ret"] for w in active_ws), default=0.0)
        worst_w_dd = max((w["dd"] for w in active_ws), default=0.0)

        rec = {
            "id": cid, "spec": cand,
            "full": full_agg,
            "n_quarters": len(qrows), "n_q_active": len(active_qs),
            "pos_quarters": pos_qs, "worst_q_ret": worst_q,
            "n_windows_6m": len(wrows), "n_w_active": len(active_ws),
            "pos_windows_6m": pos_ws, "worst_w_ret": worst_w, "worst_w_dd": worst_w_dd,
            "quarters": qrows, "windows_6m": wrows,
        }
        out_rows.append(rec)
        print(f"[done] {cid}")
        print(f"  full: ret={full_agg['agg_ret']*100:+.1f}% pf={full_agg['agg_pf']:.2f} "
              f"dd={full_agg['agg_dd']*100:.1f}% trades={full_agg['agg_trades']} +M={full_agg['pos_months']}/{full_agg['n_months']}")
        print(f"  Q: {pos_qs}/{len(active_qs)} positive (worst={worst_q*100:+.1f}%)  "
              f"6m: {pos_ws}/{len(active_ws)} positive (worst={worst_w*100:+.1f}% DD={worst_w_dd*100:.1f}%)")

    with OUT_RESULTS.open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, default=str) + "\n")

    # Summary
    lines = ["# Iter4 Robustness Results — Microstructure Alphas\n",
             f"Sample: 2020-09..2026-04. Quarters and rolling 6m non-overlapping windows.\n",
             f"Robust acceptance: pos_q/n_active >= 0.70, worst_q_ret >= -0.10, worst_w_dd <= 0.25.\n",
             "\n## Candidates ranked by full-sample agg_ret\n"]
    out_rows.sort(key=lambda r: r["full"]["agg_ret"], reverse=True)
    lines.append("| id | full ret | full PF | full DD | trades | +M/N | Q+ / Qact | worst Q | 6m+ / 6mact | worst 6m | worst 6m DD |\n")
    lines.append("|----|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for r in out_rows:
        f0 = r["full"]
        lines.append(
            f"| {r['id']} | {f0['agg_ret']*100:+.1f}% | {f0['agg_pf']:.2f} | {f0['agg_dd']*100:.1f}% | "
            f"{f0['agg_trades']} | {f0['pos_months']}/{f0['n_months']} | "
            f"{r['pos_quarters']}/{r['n_q_active']} | {r['worst_q_ret']*100:+.1f}% | "
            f"{r['pos_windows_6m']}/{r['n_w_active']} | {r['worst_w_ret']*100:+.1f}% | {r['worst_w_dd']*100:.1f}% |\n"
        )
    lines.append("\n## Per-quarter detail (top 3 by full ret)\n")
    for r in out_rows[:3]:
        lines.append(f"\n### {r['id']}\n\n")
        lines.append("| quarter_start | ret | trades | pf | dd |\n|----|---:|---:|---:|---:|\n")
        for q in r["quarters"]:
            lines.append(f"| {q['qstart'][:10]} | {q['ret']*100:+.1f}% | {q['trades']} | {q['pf']:.2f} | {q['dd']*100:.1f}% |\n")
    OUT_SUMMARY.write_text("".join(lines), encoding="utf-8")
    print(f"[summary] -> {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
