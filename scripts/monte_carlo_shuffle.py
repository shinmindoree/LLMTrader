"""Monte Carlo trade-order shuffle for backtest robustness analysis.

Given a list of closed trades from a backtest, this shuffles their order N
times and recomputes the compounded equity curve to estimate the distribution
of final returns and drawdowns. This isolates how much of a backtest's
headline number is explained by *the order* in which trades happened
(path-dependence) vs the underlying alpha.

Inputs (one of):
  --job-id <uuid>   Fetch trades from the running API.
                    Requires --api-base, --api-token, --user-email,
                    and --chat-user-id (or env vars LLMT_API_BASE,
                    LLMT_API_TOKEN, LLMT_USER_EMAIL, LLMT_CHAT_USER_ID).
  --json <path>     Read trades from a local JSON file (list[dict]).
                    Each item must contain an 'r_pct' field (signed return as
                    a fraction, e.g. 0.0123 = +1.23%) OR ('entry_price',
                    'exit_price', 'side') to derive it.

Common options:
  --initial 1000          Starting equity (USDT)
  --leverage 7            Leverage to apply when --r-mode=raw_pct
  --runs 1000             Number of Monte Carlo paths
  --seed 42               RNG seed for reproducibility
  --r-mode raw_pct        How to interpret returns:
                            raw_pct  -> equity *= (1 + leverage * r_pct * pos_pct)
                            equity_pct -> equity *= (1 + r_pct)
  --pos-pct 0.5           Capital fraction used per trade (raw_pct mode only).
  --no-commission         Skip applying --commission per fill.
  --commission 0.0004     One-way commission fraction (applied 2x per round-trip).

Outputs to stdout:
  - Trade stats (count, win rate, mean R, sum R)
  - Final equity quantiles (5/25/50/75/95%)
  - Max drawdown quantiles (5/50/95%)
  - Probability of ruin (final equity <= 0)
  - Original (unshuffled) result for reference

Example:
  python scripts/monte_carlo_shuffle.py --json trades.json \
      --initial 1000 --leverage 7 --runs 5000 --pos-pct 0.5
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from pathlib import Path
from typing import Any


def _load_trades_from_file(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        # Backtest result blob — try common shapes
        if "trades" in raw:
            raw = raw["trades"]
        elif "result" in raw and isinstance(raw["result"], dict) and "trades" in raw["result"]:
            raw = raw["result"]["trades"]
    if not isinstance(raw, list):
        raise ValueError("Could not locate a list of trades in the JSON file.")
    return raw


def _load_trades_from_api(
    job_id: str,
    api_base: str,
    api_token: str,
    user_email: str,
    chat_user_id: str,
) -> list[dict[str, Any]]:
    import urllib.request

    url = f"{api_base.rstrip('/')}/api/jobs/{job_id}/trades?limit=100000"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_token}",
        "X-User-Email": user_email,
        "X-Chat-User-Id": chat_user_id,
    })
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body = resp.read()
    data = json.loads(body)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected /trades response: {data!r}")
    return data


def _extract_returns(
    trades: list[dict[str, Any]],
    *,
    commission: float,
    apply_commission: bool,
) -> list[float]:
    """Return a list of round-trip percent returns (signed).

    Interprets several backtest schemas. We only want closed (exit) trades.
    """
    rets: list[float] = []
    for t in trades:
        # 1) Pre-computed signed return.
        if "r_pct" in t:
            try:
                rets.append(float(t["r_pct"]))
                continue
            except (TypeError, ValueError):
                pass
        # 2) Derive from entry/exit price + side.
        entry = t.get("entry_price") or t.get("entry") or t.get("avg_entry_price")
        exit_ = t.get("exit_price") or t.get("price")  # backtest uses 'price' as fill price
        side = (t.get("side") or t.get("direction") or "").upper()
        # Skip non-closing rows (entries without pnl recorded).
        pnl = t.get("pnl") if "pnl" in t else t.get("realized_pnl")
        if pnl is None or entry in (None, 0) or exit_ in (None, 0):
            continue
        try:
            entry_f = float(entry)
            exit_f = float(exit_)
        except (TypeError, ValueError):
            continue
        if entry_f <= 0:
            continue
        # In our backtest, a SELL fill that closes a long is the exit.
        # 'side' on the trade row is the order side (BUY/SELL), so the
        # position direction is the *opposite* of the closing fill.
        if side == "SELL":
            r = (exit_f - entry_f) / entry_f  # closing a long
        elif side == "BUY":
            r = (entry_f - exit_f) / entry_f  # closing a short
        else:
            # Fall back to sign of pnl
            try:
                pnl_f = float(pnl)
            except (TypeError, ValueError):
                continue
            r = pnl_f / abs(pnl_f) * abs(exit_f - entry_f) / entry_f if pnl_f else 0.0
        if apply_commission:
            r -= 2.0 * commission  # entry + exit
        rets.append(r)
    return rets


def _simulate_path(
    returns: list[float],
    *,
    initial: float,
    mode: str,
    leverage: float,
    pos_pct: float,
) -> tuple[float, float]:
    """Return (final_equity, max_drawdown_fraction)."""
    eq = initial
    peak = initial
    max_dd = 0.0
    ruined = False
    for r in returns:
        if mode == "raw_pct":
            # equity-relative pnl = leverage * r * pos_pct
            delta = leverage * r * pos_pct
            eq *= 1.0 + delta
        else:  # equity_pct: r is already equity-relative
            eq *= 1.0 + r
        if eq <= 0:
            eq = 0.0
            ruined = True
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
        if ruined:
            break
    return eq, max_dd


def _quantiles(values: list[float], qs: list[float]) -> dict[str, float]:
    if not values:
        return {f"p{int(q * 100)}": float("nan") for q in qs}
    s = sorted(values)
    n = len(s)
    out: dict[str, float] = {}
    for q in qs:
        idx = min(n - 1, max(0, int(round(q * (n - 1)))))
        out[f"p{int(q * 100)}"] = s[idx]
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--job-id", help="Backtest job UUID to fetch trades from")
    src.add_argument("--json", type=Path, help="Local JSON file with trades")

    # API auth
    p.add_argument("--api-base", default=os.environ.get("LLMT_API_BASE", ""))
    p.add_argument("--api-token", default=os.environ.get("LLMT_API_TOKEN", ""))
    p.add_argument("--user-email", default=os.environ.get("LLMT_USER_EMAIL", ""))
    p.add_argument("--chat-user-id", default=os.environ.get("LLMT_CHAT_USER_ID", ""))

    # Simulation params
    p.add_argument("--initial", type=float, default=1000.0)
    p.add_argument("--leverage", type=float, default=7.0)
    p.add_argument("--pos-pct", type=float, default=0.5,
                   help="Capital fraction per trade (raw_pct mode). 0.5 matches default backtest.")
    p.add_argument("--runs", type=int, default=1000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--r-mode", choices=["raw_pct", "equity_pct"], default="raw_pct")
    p.add_argument("--commission", type=float, default=0.0004)
    p.add_argument("--no-commission", action="store_true",
                   help="Do not subtract 2*commission from each return.")

    args = p.parse_args()

    # Load trades
    if args.json is not None:
        trades = _load_trades_from_file(args.json)
    else:
        if not all([args.api_base, args.api_token, args.user_email, args.chat_user_id]):
            print("ERROR: API mode requires --api-base, --api-token, --user-email, --chat-user-id "
                  "(or LLMT_API_BASE/LLMT_API_TOKEN/LLMT_USER_EMAIL/LLMT_CHAT_USER_ID env vars).",
                  file=sys.stderr)
            sys.exit(2)
        trades = _load_trades_from_api(
            job_id=args.job_id,
            api_base=args.api_base,
            api_token=args.api_token,
            user_email=args.user_email,
            chat_user_id=args.chat_user_id,
        )

    rets = _extract_returns(
        trades,
        commission=args.commission,
        apply_commission=not args.no_commission,
    )
    if not rets:
        print("ERROR: No round-trip returns extracted from trades. Check schema.", file=sys.stderr)
        sys.exit(1)

    # ------------------------------------------------------------------
    # Trade stats (returns are signed fractions per round trip)
    # ------------------------------------------------------------------
    n = len(rets)
    wins = sum(1 for r in rets if r > 0)
    losses = sum(1 for r in rets if r < 0)
    win_rate = wins / n
    mean_r = statistics.mean(rets)
    median_r = statistics.median(rets)
    stdev_r = statistics.pstdev(rets) if n > 1 else 0.0
    sum_r = sum(rets)
    gross_win = sum(r for r in rets if r > 0)
    gross_loss = -sum(r for r in rets if r < 0)
    pf = (gross_win / gross_loss) if gross_loss > 0 else math.inf

    print("=" * 60)
    print("Trade stats (round-trip pct returns, post-commission" if not args.no_commission else
          "Trade stats (round-trip pct returns, pre-commission")
    print(f"  applied 2x commission = {args.commission * 100:.3f}%/leg)" if not args.no_commission else "")
    print("=" * 60)
    print(f"  n_trades        : {n}")
    print(f"  win_rate        : {win_rate * 100:.2f}%  ({wins} wins / {losses} losses)")
    print(f"  mean_r          : {mean_r * 100:+.4f}%   per trade")
    print(f"  median_r        : {median_r * 100:+.4f}%")
    print(f"  stdev_r         : {stdev_r * 100:.4f}%")
    print(f"  sum_r           : {sum_r * 100:+.2f}%   (additive, fixed-size proxy)")
    print(f"  profit_factor   : {pf:.2f}")

    # ------------------------------------------------------------------
    # Original (unshuffled) compounded path
    # ------------------------------------------------------------------
    orig_final, orig_dd = _simulate_path(
        rets,
        initial=args.initial,
        mode=args.r_mode,
        leverage=args.leverage,
        pos_pct=args.pos_pct,
    )
    orig_total_pct = (orig_final / args.initial - 1.0) * 100.0

    # ------------------------------------------------------------------
    # Monte Carlo
    # ------------------------------------------------------------------
    rng = random.Random(args.seed)
    finals: list[float] = []
    dds: list[float] = []
    ruins = 0
    for _ in range(args.runs):
        order = rets[:]
        rng.shuffle(order)
        f, dd = _simulate_path(
            order,
            initial=args.initial,
            mode=args.r_mode,
            leverage=args.leverage,
            pos_pct=args.pos_pct,
        )
        finals.append(f)
        dds.append(dd)
        if f <= 0:
            ruins += 1

    final_qs = _quantiles(finals, [0.05, 0.25, 0.50, 0.75, 0.95])
    dd_qs = _quantiles(dds, [0.05, 0.50, 0.95])
    finals_pct = sorted([(x / args.initial - 1.0) * 100.0 for x in finals])
    final_pct_qs = _quantiles(finals_pct, [0.05, 0.25, 0.50, 0.75, 0.95])

    print()
    print("=" * 60)
    print(f"Compounded simulation  (mode={args.r_mode}, leverage={args.leverage}x, "
          f"pos_pct={args.pos_pct}, initial={args.initial:.0f} USDT)")
    print("=" * 60)
    print(f"  Original order  : final={orig_final:,.2f} USDT  "
          f"({orig_total_pct:+,.1f}%)   max_dd={orig_dd * 100:.1f}%")
    print()
    print(f"  Monte Carlo     : runs={args.runs}, seed={args.seed}")
    print(f"  Final equity (USDT) percentiles:")
    for q in ["p5", "p25", "p50", "p75", "p95"]:
        print(f"     {q:>4}: {final_qs[q]:>16,.2f}")
    print(f"  Final return (%) percentiles:")
    for q in ["p5", "p25", "p50", "p75", "p95"]:
        print(f"     {q:>4}: {final_pct_qs[q]:>+16,.1f}%")
    print(f"  Max drawdown percentiles:")
    for q in ["p5", "p50", "p95"]:
        print(f"     {q:>4}: {dd_qs[q] * 100:>6.1f}%")
    print(f"  Ruin probability (final<=0): {ruins / args.runs * 100:.2f}%  ({ruins}/{args.runs})")

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("Interpretation")
    print("=" * 60)
    spread = (final_qs["p95"] - final_qs["p5"]) / max(1.0, final_qs["p50"])
    print(f"  p95/p5 spread vs median = {spread:.1f}x")
    print(f"    → high spread (>>1) means the headline number is highly")
    print(f"      path-dependent (luck-of-order). Low spread (<1x) means")
    print(f"      the alpha is robust to trade ordering.")
    if mean_r > 0:
        print(f"  mean per-trade return is positive ({mean_r * 100:+.3f}%) → edge present (pre-friction).")
    else:
        print(f"  mean per-trade return is non-positive ({mean_r * 100:+.3f}%) → no clear edge.")


if __name__ == "__main__":
    main()
