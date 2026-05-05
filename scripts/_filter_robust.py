"""Filter sweep_oi_full_results.json for robust combos and report ranked list."""
import json
from pathlib import Path

p = Path("sweep_oi_full_results.json")
d = json.load(open(p, "r", encoding="utf-8"))
rows = d["results"]

def to_pf(x):
    if x == "inf":
        return 10.0
    try:
        return float(x)
    except Exception:
        return 0.0

robust = []
for r in rows:
    tr = r["train"]; te = r["test"]
    if tr["trades"] < 30 or te["trades"] < 20:
        continue
    if tr["ret_pct"] <= 0 or te["ret_pct"] <= 0:
        continue
    if to_pf(tr["pf"]) < 1.10 or to_pf(te["pf"]) < 1.15:
        continue
    # combined score: harmonic mean of train/test
    tr_score = to_pf(tr["pf"]) * tr["ret_pct"] / max(tr["max_dd_pct"], 1.0)
    te_score = to_pf(te["pf"]) * te["ret_pct"] / max(te["max_dd_pct"], 1.0)
    combo = 2 * tr_score * te_score / max(tr_score + te_score, 1e-6)
    r["_combo"] = round(combo, 3)
    r["_tr_score"] = round(tr_score, 3)
    r["_te_score"] = round(te_score, 3)
    robust.append(r)

robust.sort(key=lambda r: r["_combo"], reverse=True)

print(f"robust combos (TR+ AND TE+, PF>=1.1/1.15, n>=30/20): {len(robust)}/{len(rows)}")
print()
header = (f"{'#':>2} {'itv':>4} {'TP':>6} {'SL':>6} {'hold':>5} {'RR':>5} | "
          f"{'tr.ret':>8} {'tr.pf':>6} {'tr.dd':>6} {'tr.n':>5} | "
          f"{'te.ret':>8} {'te.pf':>6} {'te.dd':>6} {'te.n':>5} {'te.wr':>6} | "
          f"{'combo':>7}")
print(header)
print("-" * len(header))
for i, r in enumerate(robust[:25], 1):
    tr, te = r["train"], r["test"]
    print(f"{i:>2} {r['interval']:>4} {r['tp']:>6.3f} {r['sl']:>6.3f} {r['max_hold_bars']:>5d} "
          f"{str(r['rr']):>5} | "
          f"{tr['ret_pct']:>+8.2f} {str(tr['pf']):>6} {tr['max_dd_pct']:>6.2f} {tr['trades']:>5} | "
          f"{te['ret_pct']:>+8.2f} {str(te['pf']):>6} {te['max_dd_pct']:>6.2f} {te['trades']:>5} {te['win_rate']:>6.2f} | "
          f"{r['_combo']:>7.3f}")
