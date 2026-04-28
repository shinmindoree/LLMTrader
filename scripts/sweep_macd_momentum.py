"""MACD momentum + EMA200 trend filter sweep across timeframes."""
from __future__ import annotations

import asyncio
import importlib.util
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

strategy_path = PROJECT_ROOT / "scripts/strategies/macd_momentum_trend_strategy.py"
spec = importlib.util.spec_from_file_location("macd_mom_mod", strategy_path)
mod = importlib.util.module_from_spec(spec)
sys.modules["macd_mom_mod"] = mod
spec.loader.exec_module(mod)
StrategyClass = mod.MacdMomentumStrategy

SYMBOL = "BTCUSDT"
START = "2026-01-01"
END = "2026-01-31"
COMMISSION = 0.0002  # maker fee (limit orders 가정)


async def fetch_data(interval: str):
    settings = get_settings()
    base = normalize_binance_base_url(settings.binance.base_url_backtest or settings.binance.base_url)
    client = BinanceHTTPClient(api_key=settings.binance.api_key or "", api_secret=settings.binance.api_secret or "", base_url=base)
    try:
        sd = datetime.strptime(START, "%Y-%m-%d")
        ed = datetime.strptime(END, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        return await fetch_all_klines(
            client=client, symbol=SYMBOL, interval=interval,
            start_ts=int(sd.timestamp() * 1000), end_ts=int(ed.timestamp() * 1000),
        )
    finally:
        await client.aclose()


def run_one(klines, params: dict, commission: float = COMMISSION) -> dict:
    rc = RiskConfig(max_leverage=1.0, max_position_size=0.5, max_order_size=0.5, stop_loss_pct=0.05)
    rm = BacktestRiskManager(rc)
    ctx = BacktestContext(symbol=SYMBOL, leverage=1, initial_balance=1000.0, risk_manager=rm, commission_rate=commission)
    strat = StrategyClass(**params)
    eng = BacktestEngine(strat, ctx, klines)
    res = eng.run()
    return {
        "trades": res.get("total_trades", 0),
        "win_rate": res.get("win_rate", 0),
        "return_pct": res.get("total_return_pct", 0),
        "pnl": res.get("total_pnl", 0),
        "commission": res.get("total_commission", 0),
    }


def days_between() -> int:
    sd = datetime.strptime(START, "%Y-%m-%d")
    ed = datetime.strptime(END, "%Y-%m-%d")
    return (ed - sd).days + 1


def passes(r: dict) -> bool:
    days = days_between()
    return r["trades"] >= 100 and r["trades"] / days >= 5 and r["return_pct"] > 0


async def main():
    days = days_between()
    print(f"=== MACD momentum sweep — period {days}d, target trades>=100 (>=5/day), return>0 ===\n")

    # (interval, fast, slow, signal, trend_p, trend_on, atr_tp, atr_sl, hold, cd, exit_xcross)
    configs = [
        # 1m: 빠른 신호
        ("1m", 12, 26, 9, 200, True, 2.0, 1.0, 60, 2, True),
        ("1m", 12, 26, 9, 200, True, 2.5, 1.0, 60, 2, True),
        ("1m", 12, 26, 9, 200, True, 3.0, 1.0, 90, 2, False),
        ("1m", 6,  13, 5, 100, True, 2.0, 1.0, 30, 2, True),
        ("1m", 6,  13, 5, 100, True, 2.5, 1.0, 60, 3, True),
        ("1m", 8,  21, 5, 200, True, 3.0, 1.0, 60, 3, True),
        # No trend filter (양방향 모두)
        ("1m", 12, 26, 9, 200, False, 2.0, 1.0, 60, 2, True),
        ("1m", 12, 26, 9, 200, False, 3.0, 1.0, 90, 2, True),
        # 3m
        ("3m", 12, 26, 9, 100, True, 2.0, 1.0, 30, 2, True),
        ("3m", 12, 26, 9, 200, True, 2.5, 1.0, 45, 2, True),
        ("3m", 12, 26, 9, 200, True, 3.0, 1.0, 60, 2, True),
        ("3m", 6,  13, 5, 100, True, 2.0, 1.0, 30, 2, True),
        # 5m
        ("5m", 12, 26, 9, 100, True, 2.0, 1.0, 30, 2, True),
        ("5m", 12, 26, 9, 200, True, 2.5, 1.0, 45, 2, True),
        ("5m", 12, 26, 9, 200, True, 3.0, 1.0, 60, 2, True),
        # 15m
        ("15m", 12, 26, 9, 100, True, 2.0, 1.0, 30, 2, True),
        ("15m", 12, 26, 9, 200, True, 2.5, 1.0, 45, 2, True),
    ]

    klines_cache: dict[str, list] = {}
    rows = []
    for cfg in configs:
        itv, f, s, sg, tp_p, tp_on, atp, asl, h, c, ex = cfg
        if itv not in klines_cache:
            print(f"Fetching {SYMBOL} {itv}...")
            klines_cache[itv] = await fetch_data(itv)
            print(f"  → {len(klines_cache[itv])} bars")
        params = {
            "macd_fast": f, "macd_slow": s, "macd_signal": sg,
            "trend_period": tp_p, "use_trend_filter": tp_on,
            "atr_tp_multiplier": atp, "atr_sl_multiplier": asl,
            "max_hold_bars": h, "cooldown_bars": c,
            "exit_on_opposite_cross": ex,
        }
        r = run_one(klines_cache[itv], params)
        r["cfg"] = cfg
        rows.append(r)
        flag = "✓" if passes(r) else " "
        print(f"[{flag}] {itv} macd={f}/{s}/{sg} trend={tp_p}({'on' if tp_on else 'off'}) "
              f"TP={atp} SL={asl} hold={h} ex={ex}: trades={r['trades']} win={r['win_rate']:.1f}% "
              f"ret={r['return_pct']:.2f}% pnl={r['pnl']:.1f} fee={r['commission']:.1f}")

    print("\n=== Filter (trades>=100, >=5/day, ret>0) ===")
    winners = [r for r in rows if passes(r)]
    if winners:
        winners.sort(key=lambda x: x["return_pct"], reverse=True)
        for r in winners[:5]:
            print(r)
    else:
        print("No setup passes; top 5 by return:")
        rows.sort(key=lambda x: x["return_pct"], reverse=True)
        for r in rows[:5]:
            print(r)


if __name__ == "__main__":
    asyncio.run(main())
