"""TP/SL grid sweep + OOS evaluation for the OI Capitulation strategy.

- 다른 전략 파라미터(oi_lookback_bars, oi_drop_threshold, price_drop_threshold,
  max_hold_bars 등)는 strategy 파일의 STRATEGY_PARAMS(=best sweep 기준)를 그대로 사용
- TP/SL grid 만 sweep
- 각 조합에 대해 TRAIN 구간과 OOS(TEST) 구간을 모두 백테스트
- 결과를 OOS 기준으로 랭킹하여 출력 + JSON 저장

데이터 소스:
- 캔들: data/perp_meta/BTCUSDT_15m_klines.parquet (Binance API 호출 X)
- OI:   data/perp_meta/BTCUSDT_oi_5m.parquet (전략의 OI provider가 자동 로드)

사용 예 (Windows PowerShell):
  python scripts/sweep_oi_tpsl.py \
    --train-start 2023-04-01 --train-end 2025-04-30 \
    --test-start  2025-05-01 --test-end  2026-04-29 \
    --tp-list 0.020,0.025,0.030,0.040,0.050,0.060,0.080,0.100,0.120 \
    --sl-list 0.008,0.010,0.012,0.015,0.020,0.030,0.040 \
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
INTERVAL_MS = 15 * 60 * 1000


def _to_ms(date_str: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp() * 1000)


def load_klines_window(start_ts: int, end_ts: int) -> list[list[Any]]:
    """Parquet에서 [start_ts, end_ts] 구간의 15m 캔들을 Binance kline list 형식으로 반환."""
    df = pd.read_parquet(KLINES_PARQUET)
    mask = (df["ts"] >= start_ts) & (df["ts"] <= end_ts)
    df = df.loc[mask].sort_values("ts").reset_index(drop=True)
    out: list[list[Any]] = []
    for row in df.itertuples(index=False):
        ot = int(row.ts)
        ct = ot + INTERVAL_MS - 1
        # Binance kline format: [open_time, o, h, l, c, vol, close_time, qav, ntrade, tbbav, tbqav, ignore]
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
    # equity curve maxDD via balance_after across all trade rows
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


def run_one(payload: dict[str, Any]) -> dict[str, Any]:
    """워커 프로세스 진입점. payload는 모두 picklable."""
    strategy_file = Path(payload["strategy_file"])
    base_params = dict(payload["base_params"])
    base_params["tp_pct"] = float(payload["tp"])
    base_params["sl_pct"] = float(payload["sl"])

    klines = load_klines_window(int(payload["start_ts"]), int(payload["end_ts"]))

    strat_cls = load_strategy_class(strategy_file)
    risk = BacktestRiskManager(
        RiskConfig(
            max_leverage=float(payload["leverage"]),
            max_position_size=float(payload["max_position"]),
            max_order_size=float(payload["max_position"]),
            stop_loss_pct=float(payload["stop_loss_pct"]),
        )
    )
    ctx = BacktestContext(
        symbol=payload["symbol"],
        leverage=int(payload["leverage"]),
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
    m.update({"tp": float(payload["tp"]), "sl": float(payload["sl"]), "label": payload["label"]})
    return m


def parse_floats(s: str) -> list[float]:
    return [float(x.strip()) for x in s.split(",") if x.strip()]


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
    ap.add_argument("--leverage", type=int, default=1)
    ap.add_argument("--max-position", type=float, default=0.5)
    ap.add_argument("--initial-balance", type=float, default=1000.0)
    ap.add_argument("--commission", type=float, default=0.0004,
                    help="per-side commission rate; 0.0004=4bps (sweep doc baseline). For taker-real, use 0.0005.")
    ap.add_argument("--slippage-bps", type=float, default=0.0)
    ap.add_argument("--stop-loss-pct", type=float, default=0.05,
                    help="risk-manager hard SL; strategy enforces own sl_pct internally.")
    ap.add_argument("--fixed-notional", type=float, default=None)
    ap.add_argument("--train-start", required=True)
    ap.add_argument("--train-end", required=True)
    ap.add_argument("--test-start", required=True)
    ap.add_argument("--test-end", required=True)
    ap.add_argument("--tp-list", required=True)
    ap.add_argument("--sl-list", required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--min-trades", type=int, default=10, help="ranking 시 OOS 최소 거래 수")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "sweep_oi_tpsl_results.json")
    args = ap.parse_args()

    strategy_file = Path(args.strategy).resolve()
    base_params = load_strategy_default_params(strategy_file)
    base_params.pop("tp_pct", None)
    base_params.pop("sl_pct", None)

    train = (_to_ms(args.train_start), _to_ms(args.train_end, end_of_day=True))
    test = (_to_ms(args.test_start), _to_ms(args.test_end, end_of_day=True))

    tp_list = parse_floats(args.tp_list)
    sl_list = parse_floats(args.sl_list)
    grid = [(tp, sl) for tp in tp_list for sl in sl_list]

    print("=" * 80)
    print("OI TP/SL Sweep")
    print("=" * 80)
    print(f"strategy file : {strategy_file}")
    print(f"base params   : {base_params}")
    print(f"TRAIN window  : {args.train_start} .. {args.train_end}")
    print(f"OOS   window  : {args.test_start} .. {args.test_end}")
    print(f"TP list ({len(tp_list)}): {tp_list}")
    print(f"SL list ({len(sl_list)}): {sl_list}")
    print(f"grid combos   : {len(grid)}  → {len(grid)*2} backtests")
    print(f"commission    : {args.commission}  slippage_bps: {args.slippage_bps}")
    print(f"workers       : {args.workers}")
    print("=" * 80)

    common = {
        "strategy_file": str(strategy_file),
        "base_params": base_params,
        "symbol": args.symbol,
        "leverage": args.leverage,
        "max_position": args.max_position,
        "initial_balance": args.initial_balance,
        "commission": args.commission,
        "slippage_bps": args.slippage_bps,
        "stop_loss_pct": args.stop_loss_pct,
        "fixed_notional": args.fixed_notional,
    }

    payloads: list[dict[str, Any]] = []
    for tp, sl in grid:
        payloads.append({**common, "tp": tp, "sl": sl, "label": "train",
                         "start_ts": train[0], "end_ts": train[1]})
        payloads.append({**common, "tp": tp, "sl": sl, "label": "test",
                         "start_ts": test[0], "end_ts": test[1]})

    t0 = time.time()
    metrics_by_key: dict[tuple[float, float, str], dict[str, Any]] = {}

    if args.workers <= 1:
        for i, p in enumerate(payloads, 1):
            m = run_one(p)
            metrics_by_key[(m["tp"], m["sl"], m["label"])] = m
            print(f"[{i}/{len(payloads)}] {m['label']:5s} TP={m['tp']:.3f} SL={m['sl']:.3f}  "
                  f"ret={m['ret_pct']:+7.2f}% pf={fmt_pf(m['pf']):>5s} dd={m['max_dd_pct']:5.2f}% n={m['trades']:3d}")
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = [ex.submit(run_one, p) for p in payloads]
            for i, fut in enumerate(as_completed(futures), 1):
                m = fut.result()
                metrics_by_key[(m["tp"], m["sl"], m["label"])] = m
                print(f"[{i}/{len(payloads)}] {m['label']:5s} TP={m['tp']:.3f} SL={m['sl']:.3f}  "
                      f"ret={m['ret_pct']:+7.2f}% pf={fmt_pf(m['pf']):>5s} dd={m['max_dd_pct']:5.2f}% n={m['trades']:3d}")

    # zip train+test per combo
    rows: list[dict[str, Any]] = []
    for tp, sl in grid:
        tr = metrics_by_key.get((tp, sl, "train"))
        te = metrics_by_key.get((tp, sl, "test"))
        if tr is None or te is None:
            continue
        rr = round(tp / sl, 2) if sl > 0 else None
        rows.append({"tp": tp, "sl": sl, "rr": rr, "train": tr, "test": te})

    rows.sort(key=lambda r: score_oos(r, args.min_trades), reverse=True)

    elapsed = time.time() - t0
    print("=" * 80)
    print(f"completed {len(rows)} combos in {elapsed:.1f}s")
    print(f"\n=== TOP 15 by OOS PF×ret/maxDD (min OOS trades = {args.min_trades}) ===")
    header = f"{'#':>2} {'TP':>6} {'SL':>6} {'RR':>5} | {'tr.ret':>8} {'tr.pf':>6} {'tr.dd':>6} {'tr.n':>5} | {'te.ret':>8} {'te.pf':>6} {'te.dd':>6} {'te.n':>5} {'te.wr':>6}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:15], 1):
        tr, te = r["train"], r["test"]
        print(f"{i:>2} {r['tp']:>6.3f} {r['sl']:>6.3f} {str(r['rr']):>5} | "
              f"{tr['ret_pct']:>+8.2f} {fmt_pf(tr['pf']):>6} {tr['max_dd_pct']:>6.2f} {tr['trades']:>5} | "
              f"{te['ret_pct']:>+8.2f} {fmt_pf(te['pf']):>6} {te['max_dd_pct']:>6.2f} {te['trades']:>5} {te['win_rate']:>6.2f}")

    # also dump full grid sorted by TP then SL
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
    print(f"\n💾 saved: {args.out}")


if __name__ == "__main__":
    main()
