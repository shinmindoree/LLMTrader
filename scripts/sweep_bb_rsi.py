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
INTERVAL = "1m"
START = "2026-03-30"
END = "2026-04-29"


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
    ctx = BacktestContext(symbol=SYMBOL, leverage=1, initial_balance=1000.0, risk_manager=rm, commission_rate=0.0004)
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
        # (atr_tp, atr_sl, bb_std, rsi_l, rsi_s, adx_max, cooldown, max_hold)
        (1.0, 0.8, 1.75, 31, 69, 29, 4, 30),    # baseline (ATR TP 도입)
        (1.5, 0.8, 1.75, 31, 69, 29, 4, 30),    # TP 더 크게
        (2.0, 0.8, 1.75, 31, 69, 29, 4, 30),
        (1.5, 0.6, 1.75, 31, 69, 29, 4, 30),    # SL 더 짧게
        (1.5, 0.6, 2.0, 28, 72, 25, 5, 30),     # 진입 더 엄격 (거래수 ↓)
        (2.0, 0.6, 2.0, 28, 72, 25, 5, 30),
        (2.5, 0.8, 2.0, 28, 72, 25, 5, 45),     # TP 크고 보유 길게
        (2.0, 0.5, 2.0, 25, 75, 22, 5, 30),     # 진짜 극단 + 작은 SL
        (3.0, 0.7, 2.0, 25, 75, 22, 5, 60),     # 큰 TP 큰 보유
        (1.5, 0.6, 2.2, 28, 72, 25, 5, 30),
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
