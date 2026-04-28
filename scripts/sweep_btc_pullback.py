"""BTC pullback (EMA + RSI + ATR) multi-timeframe 파라미터 스윕.

목표: 30일 / 100건 이상 / 일평균 5건 이상 / 양수 수익률.
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

strategy_path = PROJECT_ROOT / "scripts/strategies/btc_pullback_long_short_strategy.py"
spec = importlib.util.spec_from_file_location("btc_pullback_mod", strategy_path)
mod = importlib.util.module_from_spec(spec)
sys.modules["btc_pullback_mod"] = mod
spec.loader.exec_module(mod)

# btc_pullback의 register_talib_indicator_all_outputs는 no-op이라 indicator가 등록되지 않아
# bb_rsi 모듈의 working 버전으로 monkey-patch.
bb_rsi_path = PROJECT_ROOT / "scripts/strategies/bb_rsi_mean_reversion_strategy.py"
spec2 = importlib.util.spec_from_file_location("bb_rsi_mod_for_pp", bb_rsi_path)
mod2 = importlib.util.module_from_spec(spec2)
sys.modules["bb_rsi_mod_for_pp"] = mod2
spec2.loader.exec_module(mod2)
mod.register_talib_indicator_all_outputs = mod2.register_talib_indicator_all_outputs

StrategyClass = mod.BtcPullbackLongShortStrategy

SYMBOL = "BTCUSDT"
START = "2026-03-30"
END = "2026-04-29"


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
    print(f"=== BTC Pullback sweep — period {days}d, target trades>=100 (>=5/day), return>0 ===\n")

    # (interval, ema_fast, ema_slow, rsi_p, atr_p, pullback_atr, rsi_long_min, rsi_short_max, atr_sl, atr_tp, entry_pct)
    configs = [
        # 1m: 빠른 회전 (ema_fast 작게)
        ("1m", 9,  21, 14, 14, 0.3, 50, 50, 1.0, 2.0, 0.95),
        ("1m", 9,  21, 14, 14, 0.3, 45, 55, 1.0, 2.0, 0.95),
        ("1m", 9,  21, 14, 14, 0.5, 50, 50, 1.0, 2.0, 0.95),
        ("1m", 12, 26, 14, 14, 0.4, 50, 50, 1.0, 2.0, 0.95),
        ("1m", 12, 26, 14, 14, 0.4, 50, 50, 1.5, 3.0, 0.95),
        ("1m", 20, 50, 14, 14, 0.4, 45, 55, 1.5, 2.4, 0.95),
        ("1m", 20, 50, 14, 14, 0.5, 50, 50, 1.0, 2.5, 0.95),
        # 3m
        ("3m", 9,  21, 14, 14, 0.4, 50, 50, 1.0, 2.0, 0.95),
        ("3m", 12, 26, 14, 14, 0.4, 50, 50, 1.5, 2.4, 0.95),
        ("3m", 20, 50, 14, 14, 0.5, 50, 50, 1.5, 2.4, 0.95),
        # 5m
        ("5m", 9,  21, 14, 14, 0.4, 50, 50, 1.0, 2.0, 0.95),
        ("5m", 12, 26, 14, 14, 0.5, 50, 50, 1.5, 2.4, 0.95),
        ("5m", 20, 50, 14, 14, 0.4, 45, 55, 1.5, 2.4, 0.95),
        # 15m
        ("15m", 9, 21, 14, 14, 0.4, 50, 50, 1.0, 2.0, 0.95),
        ("15m", 20, 50, 14, 14, 0.4, 45, 55, 1.5, 2.4, 0.95),
    ]

    klines_cache: dict[str, list] = {}
    rows = []
    for cfg in configs:
        itv, ef, es, rp, ap, pb, rl, rs, sl, tp, ep = cfg
        if itv not in klines_cache:
            print(f"Fetching {SYMBOL} {itv}...")
            klines_cache[itv] = await fetch_data(itv)
            print(f"  → {len(klines_cache[itv])} bars")
        params = {
            "ema_fast_period": ef,
            "ema_slow_period": es,
            "rsi_period": rp,
            "atr_period": ap,
            "pullback_atr_mult": pb,
            "rsi_long_min": rl,
            "rsi_short_max": rs,
            "atr_stop_mult": sl,
            "atr_take_mult": tp,
            "entry_pct": ep,
        }
        r = run_one(klines_cache[itv], params)
        r["cfg"] = cfg
        rows.append(r)
        flag = "✓" if passes(r) else " "
        print(f"[{flag}] {itv} ema={ef}/{es} pb={pb} rsi={rl}/{rs} SL={sl} TP={tp}: "
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
