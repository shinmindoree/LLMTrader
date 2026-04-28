"""Donchian breakout 전략 multi-timeframe 파라미터 스윕.

목표: 30일 이내 / 100건 이상 / 일평균 5건 이상 / 양수 수익률.
"""
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

strategy_path = PROJECT_ROOT / "scripts/strategies/donchian_breakout_strategy.py"
spec = importlib.util.spec_from_file_location("donchian_mod", strategy_path)
mod = importlib.util.module_from_spec(spec)
sys.modules["donchian_mod"] = mod
spec.loader.exec_module(mod)
DonchianBreakoutStrategy = mod.DonchianBreakoutStrategy

SYMBOL = "BTCUSDT"
START = "2026-03-30"
END = "2026-04-29"

INTERVAL_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}


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


def run_one(klines, params: dict) -> dict:
    rc = RiskConfig(max_leverage=1.0, max_position_size=0.5, max_order_size=0.5, stop_loss_pct=0.05)
    rm = BacktestRiskManager(rc)
    ctx = BacktestContext(symbol=SYMBOL, leverage=1, initial_balance=1000.0, risk_manager=rm, commission_rate=0.0004)
    strat = DonchianBreakoutStrategy(**params)
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
    print(f"=== Donchian breakout sweep — period {days}d, target trades>=100 (>=5/day), return>0 ===\n")

    # (interval, donchian_period, adx_min, atr_tp, atr_sl, ema_period, use_ema, max_hold, cooldown, allow_long, allow_short, fade)
    configs = [
        # === Turtle classic (large TP, tight SL, EMA trend filter) ===
        ("1m", 20, 25, 4.0, 1.0, 100, True, 60, 5, True, True, False),
        ("1m", 20, 25, 5.0, 1.0, 100, True, 90, 5, True, True, False),
        ("1m", 30, 25, 5.0, 1.0, 200, True, 90, 5, True, True, False),
        ("1m", 30, 25, 6.0, 0.8, 200, True, 120, 5, True, True, False),
        ("1m", 50, 25, 6.0, 0.8, 200, True, 120, 10, True, True, False),
        ("1m", 50, 25, 8.0, 0.8, 200, True, 180, 10, True, True, False),
        # === Quick-scalp breakout (small TP, large SL) ===
        ("1m", 20, 25, 0.5, 2.0, 100, True, 30, 2, True, True, False),
        ("1m", 20, 25, 0.7, 2.0, 100, True, 30, 2, True, True, False),
        ("1m", 30, 25, 0.5, 1.5, 100, True, 20, 2, True, True, False),
        # === Fade with EMA filter (counter-trend mean revert) ===
        ("1m", 20, 0, 1.0, 0.5, 100, True, 20, 2, True, True, True),
        ("1m", 20, 15, 1.0, 0.5, 100, True, 20, 2, True, True, True),
        ("1m", 30, 0, 1.5, 0.5, 100, True, 30, 3, True, True, True),
        ("3m", 20, 0, 1.0, 0.5, 100, True, 20, 2, True, True, True),
        ("3m", 30, 0, 1.5, 0.5, 100, True, 30, 3, True, True, True),
    ]

    klines_cache: dict[str, list] = {}
    rows = []
    for cfg in configs:
        itv, donch, adx_min, tp, sl, ema_p, ema_on, hold, cd, al, asg, fade = cfg
        if itv not in klines_cache:
            print(f"Fetching {SYMBOL} {itv}...")
            klines_cache[itv] = await fetch_data(itv)
            print(f"  → {len(klines_cache[itv])} bars")
        params = {
            "donchian_period": donch,
            "adx_min": adx_min,
            "atr_tp_multiplier": tp,
            "atr_sl_multiplier": sl,
            "ema_period": ema_p,
            "use_ema_filter": ema_on,
            "max_hold_bars": hold,
            "cooldown_bars": cd,
            "allow_long": al,
            "allow_short": asg,
            "fade_mode": fade,
        }
        r = run_one(klines_cache[itv], params)
        r["cfg"] = cfg
        rows.append(r)
        flag = "✓" if passes(r) else " "
        mode = "FADE" if fade else "BRK "
        print(f"[{flag}] {mode} {itv} donch={donch} adx>={adx_min} TP={tp} SL={sl} "
              f"ema={ema_p}({'on' if ema_on else 'off'}) hold={hold} cd={cd}: "
              f"trades={r['trades']} win={r['win_rate']:.1f}% "
              f"ret={r['return_pct']:.2f}% pnl={r['pnl']:.1f} fee={r['commission']:.1f}")

    print("\n=== Filter (trades>=100, >=5/day, ret>0) ===")
    winners = [r for r in rows if passes(r)]
    if winners:
        winners.sort(key=lambda x: x["return_pct"], reverse=True)
        for r in winners[:5]:
            print(r)
    else:
        print("No setup passes thresholds; printing top 5 by return:")
        rows.sort(key=lambda x: x["return_pct"], reverse=True)
        for r in rows[:5]:
            print(r)


if __name__ == "__main__":
    asyncio.run(main())
