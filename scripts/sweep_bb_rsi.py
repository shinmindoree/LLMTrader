"""BB+RSI 전략 빠른 파라미터 스윕.

run_backtest 엔진을 in-process 호출 (네트워크 fetch 결과 캐시).
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backtest.context import BacktestContext  # noqa: E402
from backtest.data_fetcher import fetch_all_klines  # noqa: E402
from backtest.engine import BacktestEngine  # noqa: E402
from backtest.risk import BacktestRiskManager  # noqa: E402
from binance.client import BinanceHTTPClient, normalize_binance_base_url  # noqa: E402
from common.risk import RiskConfig  # noqa: E402
from settings import get_settings  # noqa: E402

# Import strategy module
import importlib.util
strategy_path = PROJECT_ROOT / "scripts/strategies/bb_rsi_mean_reversion_strategy.py"
spec = importlib.util.spec_from_file_location("bb_rsi_mod", strategy_path)
mod = importlib.util.module_from_spec(spec)
sys.modules["bb_rsi_mod"] = mod
spec.loader.exec_module(mod)
BbRsiMeanReversionStrategy = mod.BbRsiMeanReversionStrategy

SYMBOL = "BTCUSDT"
INTERVAL = "3m"
START = "2026-02-28"
END = "2026-03-29"


async def fetch_data():
    settings = get_settings()
    base = normalize_binance_base_url(settings.binance.base_url_backtest or settings.binance.base_url)
    client = BinanceHTTPClient(api_key=settings.binance.api_key or "", api_secret=settings.binance.api_secret or "", base_url=base)
    try:
        sd = datetime.strptime(START, "%Y-%m-%d")
        ed = datetime.strptime(END, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        kl = await fetch_all_klines(
            client=client, symbol=SYMBOL, interval=INTERVAL,
            start_ts=int(sd.timestamp()*1000), end_ts=int(ed.timestamp()*1000),
        )
        return kl
    finally:
        await client.aclose()


def run_one(klines, params: dict) -> dict:
    rc = RiskConfig(max_leverage=1.0, max_position_size=0.5, max_order_size=0.5, stop_loss_pct=0.05)
    rm = BacktestRiskManager(rc)
    # 평균회귀(BB extremes)는 limit order로 진입하므로 maker fee(0.02%) 가정.
    ctx = BacktestContext(symbol=SYMBOL, leverage=1, initial_balance=1000.0, risk_manager=rm, commission_rate=0.0002)
    strat = BbRsiMeanReversionStrategy(**params)
    eng = BacktestEngine(strat, ctx, klines)
    res = eng.run()
    return {
        "params": params,
        "trades": res.get("total_trades", 0),
        "win_rate": res.get("win_rate", 0),
        "return_pct": res.get("total_return_pct", 0),
        "pnl": res.get("total_pnl", 0),
        "commission": res.get("total_commission", 0),
        "final": res.get("final_balance", 0),
    }


async def main():
    print(f"Fetching {SYMBOL} {INTERVAL} {START}..{END} ...")
    klines = await fetch_data()
    print(f"  → {len(klines)} bars")

    # 시도할 파라미터 조합 (TP/SL 비율과 진입 강도)
    grid = [
        # 3m: 거래수 100+ 목표. ADX 필터 완화 + RR 조정.
        # 형식: (atr_tp, atr_sl, bb_std, rsi_long, rsi_short, adx_max, cd, hold)
        (3.0, 0.5, 2.0, 28, 72, 30, 5, 60),    # 5m winner를 3m로
        (3.0, 0.5, 2.0, 30, 70, 30, 5, 60),
        (3.0, 0.5, 1.8, 30, 70, 30, 5, 60),
        (3.0, 0.5, 2.0, 30, 70, 35, 5, 60),
        (2.5, 0.5, 1.8, 32, 68, 30, 5, 60),
        (2.5, 0.5, 1.8, 32, 68, 35, 5, 60),
        (2.5, 0.5, 2.0, 32, 68, 35, 5, 45),
        (3.0, 0.5, 1.75, 32, 68, 35, 4, 60),
        (3.0, 0.4, 2.0, 30, 70, 30, 5, 60),
        (3.0, 0.4, 1.8, 32, 68, 35, 5, 60),
        (4.0, 0.5, 2.0, 30, 70, 30, 5, 90),
        (4.0, 0.5, 2.0, 30, 70, 35, 5, 90),
    ]

    rows = []
    for p in grid:
        params = {
            "atr_tp_multiplier": p[0],
            "atr_sl_multiplier": p[1],
            "bb_stddev": p[2],
            "rsi_long_level": p[3],
            "rsi_short_level": p[4],
            "adx_max": p[5],
            "cooldown_bars": p[6],
            "max_hold_bars": p[7],
        }
        r = run_one(klines, params)
        rows.append(r)
        print(f"TP={p[0]} SL={p[1]} bb={p[2]} rsi={p[3]}/{p[4]} adx<{p[5]} cd={p[6]} hold={p[7]}: "
              f"trades={r['trades']} win={r['win_rate']:.1f}% ret={r['return_pct']:.2f}% pnl={r['pnl']:.1f} fee={r['commission']:.1f}")

    rows.sort(key=lambda x: x["return_pct"], reverse=True)
    print("\n=== TOP 3 ===")
    for r in rows[:3]:
        print(json.dumps(r, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
