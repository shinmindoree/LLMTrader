"""Quick scanner: find top qualifying results from sweep_fast jsonl."""
import json
from pathlib import Path

p = Path(__file__).parent / "_sweep_fast_results.jsonl"
recs = []
with open(p) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            continue

print(f"total={len(recs)}")
qual = [r for r in recs if r["tpd"] >= 5 and r["ret_pct"] > 0]
qual.sort(key=lambda r: r["ret_pct"], reverse=True)
print(f"qualifying (>=5/d AND ret>0): {len(qual)}")
for r in qual[:40]:
    pp = r["params"]
    short = {k: pp[k] for k in pp if k in ("rsi_os", "wr_os", "ema_trend_period",
        "atr_tp_multiplier", "atr_sl_multiplier", "max_hold_bars", "use_cdl",
        "min_confluence", "confluence_window", "require_close_above_open")}
    print(f"[{r['itv']}] t={r['trades']} ({r['tpd']:.2f}/d) win={r['win_rate']:.1f}% ret={r['ret_pct']:+.2f}% {short}")
