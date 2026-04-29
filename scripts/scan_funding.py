"""Scan funding alpha sweep results: print top-K by 1y return with min trades, also 6m & 3m."""
import json
from pathlib import Path

P = Path(__file__).parent / "_funding_alpha_results.jsonl"
rows = [json.loads(l) for l in P.read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"Total combos: {len(rows)}")

def topk(window: str, min_trades: int, k: int = 10):
    cands = [r for r in rows if r.get(window, {}).get("trades", 0) >= min_trades]
    cands.sort(key=lambda r: r[window]["ret"], reverse=True)
    print(f"\n=== Top-{k} {window} (trades>={min_trades}) [{len(cands)} candidates] ===")
    for r in cands[:k]:
        w = r[window]
        print(f"  pos={r['pos_thr']:+.5f} neg={r['neg_thr']:+.5f} hold={r['hold_h']}h tp={r['tp']:.3f} sl={r['sl']:.3f} "
              f"| trades={w['trades']:3d} (L={w['L']}/S={w['S']}) ret={w['ret']:+.4f} pf={w['pf']} dd={w['dd']:.4f}")

for win, mt in [("1y", 30), ("6m", 20), ("3m", 10), ("1m", 5)]:
    topk(win, mt, 10)

# Show stability: positive across all windows
print("\n=== Cross-window stability (all 4 windows positive, 1y trades>=30) ===")
stable = [r for r in rows
          if r.get("1y", {}).get("trades", 0) >= 30
          and r["1y"]["ret"] > 0 and r["6m"]["ret"] > 0
          and r["3m"]["ret"] > 0 and r["1m"]["ret"] > 0]
stable.sort(key=lambda r: r["1y"]["ret"], reverse=True)
print(f"  {len(stable)} stable combos")
for r in stable[:10]:
    print(f"  pos={r['pos_thr']:+.5f} neg={r['neg_thr']:+.5f} hold={r['hold_h']}h tp={r['tp']:.3f} sl={r['sl']:.3f}")
    for w in ("1m", "3m", "6m", "1y"):
        x = r[w]
        print(f"    {w}: ret={x['ret']:+.4f} trades={x['trades']} pf={x['pf']} dd={x['dd']:.4f} L/S={x['L']}/{x['S']}")
