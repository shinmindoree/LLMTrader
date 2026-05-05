import json
from collections import defaultdict
from pathlib import Path

p = Path("sweep_oi_full_results.json")
d = json.load(open(p, "r", encoding="utf-8"))
rows = d["results"]
print(f"total combos: {len(rows)}")

by_itv = defaultdict(list)
for r in rows:
    by_itv[r["interval"]].append(r)

for itv, lst in sorted(by_itv.items()):
    n_with_trades = sum(1 for r in lst if r["test"]["trades"] > 0)
    avg_te_n = sum(r["test"]["trades"] for r in lst) / len(lst)
    print(f"  {itv}: combos={len(lst)} with_oos_trades={n_with_trades} avg_oos_n={avg_te_n:.1f}")


def score(r, mt=5):
    te = r["test"]
    if te["trades"] < mt:
        return -1e18
    pf = te["pf"]
    try:
        pf = 10.0 if pf == "inf" else float(pf)
    except Exception:
        pf = 10.0
    dd = max(te["max_dd_pct"], 1.0)
    return pf * te["ret_pct"] / dd


print("\n=== TOP 5 per interval (by OOS PF*ret/dd, min_trades=5) ===")
for itv in sorted(by_itv.keys()):
    lst = sorted(by_itv[itv], key=score, reverse=True)
    print(f"-- {itv} --")
    for r in lst[:5]:
        tr, te = r["train"], r["test"]
        tp = r["tp"]
        sl = r["sl"]
        hold = r["max_hold_bars"]
        print(
            f"  TP={tp:.3f} SL={sl:.3f} hold={hold:>3d} | "
            f"TR ret={tr['ret_pct']:+7.2f}% pf={tr['pf']} dd={tr['max_dd_pct']:.1f}% n={tr['trades']:>4d} | "
            f"TE ret={te['ret_pct']:+7.2f}% pf={te['pf']} dd={te['max_dd_pct']:.1f}% n={te['trades']:>4d}"
        )
