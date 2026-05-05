"""Full OI Capitulation parameter sweep with TRAIN/OOS evaluation.

Sweeps the following dimensions:
  - candle_interval (15m / 30m / 1h / 2h / 4h)  [resampled from 15m parquet]
  - leverage
  - tp_pct
  - sl_pct
  - max_hold_bars

For each candle interval, oi_lookback_bars is auto-rescaled to keep the
24h lookback window invariant (e.g. 96 on 15m, 48 on 30m, 24 on 1h, ...).

Data sources:
  - klines: data/perp_meta/BTCUSDT_15m_klines.parquet (resampled in-process)
  - OI:     data/perp_meta/BTCUSDT_oi_5m.parquet (loaded by strategy provider)

Example (PowerShell):
  python scripts/sweep_oi_full.py `
    --train-start 2023-04-01 --train-end 2025-04-30 `
    --test-start  2025-05-01 --test-end  2026-04-29 `
    --interval-list 15m,30m,1h,2h,4h `
    --leverage-list 1,3,5,7,10 `
    --tp-list 0.02,0.03,0.05,0.08 `
    --sl-list 0.010,0.012,0.015,0.020,0.030 `
    --max-hold-list 24,48,96 `
    --workers 4
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backtest.context import BacktestContext  # noqa: E402
from backtest.engine import BacktestEngine  # noqa: E402
from backtest.risk import BacktestRiskManager  # noqa: E402
from common.risk import RiskConfig  # noqa: E402

KLINES_PARQUET = PROJECT_ROOT / "data/perp_meta/BTCUSDT_15m_klines.parquet"

INTERVAL_TO_MIN: dict[str, int] = {
    "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240,
}


def _to_ms(date_str: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp() * 1000)


def _resample_klines(df15: pd.DataFrame, target_min: int) -> pd.DataFrame:
    """Resample 15m parquet into target interval OHLCV. Volume not used by strategy."""
    if target_min == 15:
        return df15
    rule = f"{target_min}min"
    df = df15.copy()
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("dt").sort_index()
    agg = df.resample(rule, label="left", closed="left").agg(
        o=("o", "first"),
        h=("h", "max"),
        l=("l", "min"),
        c=("c", "last"),
    ).dropna(subset=["o", "h", "l", "c"])
    # Convert datetime index to epoch milliseconds. Some pandas builds set the
    # resampled index dtype to datetime64[ms,UTC] (so .view returns ms already);
    # use astype('int64') after explicit ns conversion for cross-version safety.
    agg["ts"] = agg.index.astype("datetime64[ns, UTC]").astype("int64") // 1_000_000
    agg["ts"] = agg["ts"].astype("int64")
    return agg.reset_index(drop=True)[["ts", "o", "h", "l", "c"]]


def load_klines_window(start_ts: int, end_ts: int, interval: str) -> list[list[Any]]:
    target_min = INTERVAL_TO_MIN[interval]
    interval_ms = target_min * 60 * 1000
    df = pd.read_parquet(KLINES_PARQUET)
    if target_min != 15:
        df = _resample_klines(df, target_min)
    mask = (df["ts"] >= start_ts) & (df["ts"] <= end_ts)
    df = df.loc[mask].sort_values("ts").reset_index(drop=True)
    out: list[list[Any]] = []
    for row in df.itertuples(index=False):
        ot = int(row.ts)
        ct = ot + interval_ms - 1
        out.append([ot, float(row.o), float(row.h), float(row.l), float(row.c),
                    0.0, ct, 0.0, 0, 0.0, 0.0, ""])
    return out


def load_strategy_class(strategy_file: Path):
    spec = importlib.util.spec_from_file_location("custom_strategy", strategy_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load strategy file: {strategy_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_strategy"] = module
    spec.loader.exec_module(module)
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
            return obj
    raise ValueError(f"strategy class not found in {strategy_file}")


def load_strategy_default_params(strategy_file: Path) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("oi_strat_params", strategy_file)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    params = getattr(module, "STRATEGY_PARAMS", {})
    return dict(params) if isinstance(params, dict) else {}


def compute_metrics(results: dict[str, Any]) -> dict[str, Any]:
    trades = results.get("trades", [])
    closes = [t for t in trades if t.get("pnl") is not None]
    n = len(closes)
    pnls = [float(t["pnl"]) for t in closes]
    pos_sum = sum(p for p in pnls if p > 0)
    neg_sum = -sum(p for p in pnls if p < 0)
    if neg_sum > 1e-9:
        pf = pos_sum / neg_sum
    elif pos_sum > 0:
        pf = float("inf")
    else:
        pf = 0.0
    wins = sum(1 for p in pnls if p > 0)
    win_rate = (wins / n * 100.0) if n else 0.0
    init_bal = float(results.get("initial_balance", 1000.0))
    peak = init_bal
    cur = init_bal
    max_dd = 0.0
    for t in trades:
        ba = t.get("balance_after")
        if ba is None:
            continue
        cur = float(ba)
        if cur > peak:
            peak = cur
        if peak > 0:
            dd = (peak - cur) / peak
            if dd > max_dd:
                max_dd = dd
    return {
        "trades": n,
        "win_rate": round(win_rate, 2),
        "pf": float("inf") if pf == float("inf") else round(pf, 3),
        "ret_pct": round(float(results.get("total_return_pct", 0.0)), 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "net_profit": round(float(results.get("net_profit", 0.0)), 2),
    }


def _oi_lookback_for_interval(interval: str) -> int:
    """Return bars-per-24h for the given interval (keeps OI lookback invariant in time)."""
    minutes = INTERVAL_TO_MIN[interval]
    return max(1, (24 * 60) // minutes)


def run_one(payload: dict[str, Any]) -> dict[str, Any]:
    strategy_file = Path(payload["strategy_file"])
    base_params = dict(payload["base_params"])
    base_params["tp_pct"] = float(payload["tp"])
    base_params["sl_pct"] = float(payload["sl"])
    base_params["max_hold_bars"] = int(payload["max_hold_bars"])
    base_params["oi_lookback_bars"] = int(payload["oi_lookback_bars"])

    klines = load_klines_window(int(payload["start_ts"]), int(payload["end_ts"]), payload["interval"])

    strat_cls = load_strategy_class(strategy_file)
    leverage = int(payload["leverage"])
    risk = BacktestRiskManager(
        RiskConfig(
            max_leverage=float(leverage),
            max_position_size=float(payload["max_position"]),
            max_order_size=float(payload["max_position"]),
            stop_loss_pct=float(payload["stop_loss_pct"]),
        )
    )
    ctx = BacktestContext(
        symbol=payload["symbol"],
        leverage=leverage,
        initial_balance=float(payload["initial_balance"]),
        risk_manager=risk,
        commission_rate=float(payload["commission"]),
        fixed_notional=payload.get("fixed_notional"),
        slippage_bps=float(payload["slippage_bps"]),
    )
    strat = strat_cls(**base_params)
    engine = BacktestEngine(strat, ctx, klines)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        results = engine.run()
    m = compute_metrics(results)
    m.update({
        "interval": payload["interval"],
        "tp": float(payload["tp"]),
        "sl": float(payload["sl"]),
        "leverage": leverage,
        "max_hold_bars": int(payload["max_hold_bars"]),
        "oi_lookback_bars": int(payload["oi_lookback_bars"]),
        "label": payload["label"],
    })
    return m


def parse_floats(s: str) -> list[float]:
    return [float(x.strip()) for x in s.split(",") if x.strip()]


def parse_ints(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def parse_strs(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def score_oos(row: dict[str, Any], min_trades: int) -> float:
    te = row["test"]
    if te["trades"] < min_trades:
        return -1e18
    pf = te["pf"] if te["pf"] != float("inf") else 10.0
    dd = max(te["max_dd_pct"], 1.0)
    return pf * te["ret_pct"] / dd


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", default=str(PROJECT_ROOT / "scripts/strategies/oi_capitulation_bottom_strategy.py"))
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--max-position", type=float, default=0.5)
    ap.add_argument("--initial-balance", type=float, default=1000.0)
    ap.add_argument("--commission", type=float, default=0.0004)
    ap.add_argument("--slippage-bps", type=float, default=0.0)
    ap.add_argument("--stop-loss-pct", type=float, default=0.10,
                    help="risk-manager hard SL; strategy enforces own sl_pct internally.")
    ap.add_argument("--fixed-notional", type=float, default=None)
    ap.add_argument("--train-start", required=True)
    ap.add_argument("--train-end", required=True)
    ap.add_argument("--test-start", required=True)
    ap.add_argument("--test-end", required=True)
    ap.add_argument("--interval-list", required=True, help="comma-separated, e.g. '15m,30m,1h,2h,4h'")
    ap.add_argument("--leverage-list", required=True, help="comma-separated, e.g. '1,3,5,7,10'")
    ap.add_argument("--tp-list", required=True)
    ap.add_argument("--sl-list", required=True)
    ap.add_argument("--max-hold-list", required=True, help="comma-separated bars, e.g. '24,48,96'")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--min-trades", type=int, default=10, help="ranking 시 OOS 최소 거래 수")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "sweep_oi_full_results.json")
    args = ap.parse_args()

    strategy_file = Path(args.strategy).resolve()
    base_params = load_strategy_default_params(strategy_file)
    for key in ("tp_pct", "sl_pct", "max_hold_bars", "oi_lookback_bars"):
        base_params.pop(key, None)

    train = (_to_ms(args.train_start), _to_ms(args.train_end, end_of_day=True))
    test = (_to_ms(args.test_start), _to_ms(args.test_end, end_of_day=True))

    interval_list = parse_strs(args.interval_list)
    for it in interval_list:
        if it not in INTERVAL_TO_MIN:
            raise SystemExit(f"unsupported interval: {it} (allowed: {list(INTERVAL_TO_MIN)})")
    lev_list = parse_ints(args.leverage_list)
    tp_list = parse_floats(args.tp_list)
    sl_list = parse_floats(args.sl_list)
    mh_list = parse_ints(args.max_hold_list)

    grid = [(itv, lev, tp, sl, mh)
            for itv in interval_list
            for lev in lev_list
            for tp in tp_list
            for sl in sl_list
            for mh in mh_list]

    print("=" * 90)
    print("OI Full Sweep (interval × leverage × TP × SL × max_hold_bars)")
    print("=" * 90)
    print(f"strategy file : {strategy_file}")
    print(f"base params   : {base_params}")
    print(f"TRAIN window  : {args.train_start} .. {args.train_end}")
    print(f"OOS   window  : {args.test_start} .. {args.test_end}")
    print(f"intervals ({len(interval_list)}): {interval_list}")
    print(f"leverages ({len(lev_list)}): {lev_list}")
    print(f"TP   ({len(tp_list)}): {tp_list}")
    print(f"SL   ({len(sl_list)}): {sl_list}")
    print(f"hold ({len(mh_list)}): {mh_list}")
    print(f"grid combos   : {len(grid)}  → {len(grid)*2} backtests")
    print(f"commission    : {args.commission}  slippage_bps: {args.slippage_bps}")
    print(f"workers       : {args.workers}")
    print("=" * 90)

    common = {
        "strategy_file": str(strategy_file),
        "base_params": base_params,
        "symbol": args.symbol,
        "max_position": args.max_position,
        "initial_balance": args.initial_balance,
        "commission": args.commission,
        "slippage_bps": args.slippage_bps,
        "stop_loss_pct": args.stop_loss_pct,
        "fixed_notional": args.fixed_notional,
    }

    payloads: list[dict[str, Any]] = []
    for itv, lev, tp, sl, mh in grid:
        oilb = _oi_lookback_for_interval(itv)
        for label, window in (("train", train), ("test", test)):
            payloads.append({
                **common,
                "interval": itv,
                "leverage": lev,
                "tp": tp,
                "sl": sl,
                "max_hold_bars": mh,
                "oi_lookback_bars": oilb,
                "label": label,
                "start_ts": window[0],
                "end_ts": window[1],
            })

    t0 = time.time()
    metrics_by_key: dict[tuple[str, int, float, float, int, str], dict[str, Any]] = {}

    def _log(i: int, total: int, m: dict[str, Any]) -> None:
        print(f"[{i}/{total}] {m['label']:5s} itv={m['interval']:>3s} L={m['leverage']:>2d}x "
              f"TP={m['tp']:.3f} SL={m['sl']:.3f} hold={m['max_hold_bars']:>3d}  "
              f"ret={m['ret_pct']:+8.2f}% pf={fmt_pf(m['pf']):>6s} dd={m['max_dd_pct']:6.2f}% n={m['trades']:3d}")

    total = len(payloads)
    if args.workers <= 1:
        for i, p in enumerate(payloads, 1):
            m = run_one(p)
            metrics_by_key[(m["interval"], m["leverage"], m["tp"], m["sl"], m["max_hold_bars"], m["label"])] = m
            _log(i, total, m)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(run_one, p) for p in payloads]
            for i, fut in enumerate(as_completed(futures), 1):
                m = fut.result()
                metrics_by_key[(m["interval"], m["leverage"], m["tp"], m["sl"], m["max_hold_bars"], m["label"])] = m
                _log(i, total, m)

    rows: list[dict[str, Any]] = []
    for itv, lev, tp, sl, mh in grid:
        tr = metrics_by_key.get((itv, lev, tp, sl, mh, "train"))
        te = metrics_by_key.get((itv, lev, tp, sl, mh, "test"))
        if tr is None or te is None:
            continue
        rr = round(tp / sl, 2) if sl > 0 else None
        rows.append({
            "interval": itv, "leverage": lev, "tp": tp, "sl": sl, "max_hold_bars": mh,
            "rr": rr, "train": tr, "test": te,
        })

    rows.sort(key=lambda r: score_oos(r, args.min_trades), reverse=True)

    elapsed = time.time() - t0
    print("=" * 100)
    print(f"completed {len(rows)} combos in {elapsed:.1f}s")
    print(f"\n=== TOP 25 by OOS PF×ret/maxDD (min OOS trades = {args.min_trades}) ===")
    header = (f"{'#':>2} {'itv':>4} {'L':>3} {'TP':>6} {'SL':>6} {'hold':>5} {'RR':>5} | "
              f"{'tr.ret':>9} {'tr.pf':>6} {'tr.dd':>6} {'tr.n':>5} | "
              f"{'te.ret':>9} {'te.pf':>6} {'te.dd':>6} {'te.n':>5} {'te.wr':>6}")
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:25], 1):
        tr, te = r["train"], r["test"]
        print(f"{i:>2} {r['interval']:>4} {r['leverage']:>3d} {r['tp']:>6.3f} {r['sl']:>6.3f} "
              f"{r['max_hold_bars']:>5d} {str(r['rr']):>5} | "
              f"{tr['ret_pct']:>+9.2f} {fmt_pf(tr['pf']):>6} {tr['max_dd_pct']:>6.2f} {tr['trades']:>5} | "
              f"{te['ret_pct']:>+9.2f} {fmt_pf(te['pf']):>6} {te['max_dd_pct']:>6.2f} {te['trades']:>5} {te['win_rate']:>6.2f}")

    out = {
        "meta": {
            "strategy_file": str(strategy_file),
            "base_params": base_params,
            "train": {"start": args.train_start, "end": args.train_end},
            "test": {"start": args.test_start, "end": args.test_end},
            "commission": args.commission,
            "slippage_bps": args.slippage_bps,
            "elapsed_sec": round(elapsed, 1),
        },
        "results": rows,
    }
    args.out.write_text(json.dumps(out, default=str, indent=2), encoding="utf-8")
    print(f"\nsaved: {args.out}")


if __name__ == "__main__":
    main()
