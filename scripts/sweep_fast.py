"""Fast vectorized backtest harness for confluence-style long strategies.

Pre-computes all indicators once via talib, then runs a Python loop with O(1) lookups
per bar. ~100x faster than the engine path. Models: long-only, fee per side, ATR TP/SL,
time exit, EMA trend filter, optional CDL pattern triggers. Uses bar-close fills.
"""
from __future__ import annotations

import asyncio
import builtins
import importlib
import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backtest.data_fetcher import fetch_all_klines  # noqa: E402
from binance.client import BinanceHTTPClient, normalize_binance_base_url  # noqa: E402
from settings import get_settings  # noqa: E402

talib_abs = importlib.import_module("talib.abstract")

DAYS = 31
RESULTS = PROJECT_ROOT / "scripts" / "_sweep_fast_results.jsonl"
COMMISSION = 0.0004  # per side
_p = builtins.print


async def fetch(itv: str):
    s = get_settings()
    base = normalize_binance_base_url(s.binance.base_url_backtest or s.binance.base_url)
    c = BinanceHTTPClient(api_key=s.binance.api_key or "", api_secret=s.binance.api_secret or "", base_url=base)
    try:
        sd = datetime(2026, 3, 30)
        ed = datetime(2026, 4, 29, 23, 59, 59)
        return await fetch_all_klines(client=c, symbol="BTCUSDT", interval=itv,
                                      start_ts=int(sd.timestamp() * 1000), end_ts=int(ed.timestamp() * 1000))
    finally:
        await c.aclose()


def precompute(klines, ema_periods=(50, 100, 200), rsi_periods=(7, 14, 21),
               wr_periods=(14,), stoch_kw=(14, 3, 3), macd_kw=(12, 26, 9),
               atr_periods=(14,), cdl_names=("CDLHAMMER", "CDLENGULFING", "CDLPIERCING",
                                              "CDLMORNINGSTAR", "CDLINVERTEDHAMMER",
                                              "CDLDRAGONFLYDOJI", "CDL3WHITESOLDIERS",
                                              "CDLBELTHOLD", "CDL3INSIDE")):
    o = np.array([float(k[1]) for k in klines], dtype="float64")
    h = np.array([float(k[2]) for k in klines], dtype="float64")
    l = np.array([float(k[3]) for k in klines], dtype="float64")
    c = np.array([float(k[4]) for k in klines], dtype="float64")
    inputs = {"open": o, "high": h, "low": l, "close": c, "real": c}

    ind: dict[str, np.ndarray] = {"open": o, "high": h, "low": l, "close": c}
    for p in rsi_periods:
        ind[f"rsi_{p}"] = talib_abs.Function("RSI")(inputs, timeperiod=p)
    for p in wr_periods:
        ind[f"wr_{p}"] = talib_abs.Function("WILLR")(inputs, timeperiod=p)
    fk, sk, sd = stoch_kw
    res = talib_abs.Function("STOCH")(inputs, fastk_period=fk, slowk_period=sk, slowd_period=sd)
    if isinstance(res, dict):
        ind["stoch_k"] = res["slowk"]
    else:
        ind["stoch_k"] = res[0]
    fp, sp, sgp = macd_kw
    macd_res = talib_abs.Function("MACD")(inputs, fastperiod=fp, slowperiod=sp, signalperiod=sgp)
    if isinstance(macd_res, dict):
        ind["macd"] = macd_res["macd"]
        ind["macd_sig"] = macd_res["macdsignal"]
    else:
        ind["macd"] = macd_res[0]
        ind["macd_sig"] = macd_res[1]
    for p in atr_periods:
        ind[f"atr_{p}"] = talib_abs.Function("ATR")(inputs, timeperiod=p)
    for p in ema_periods:
        ind[f"ema_{p}"] = talib_abs.Function("EMA")(inputs, timeperiod=p)
    for nm in cdl_names:
        ind[f"cdl_{nm}"] = talib_abs.Function(nm)(inputs)
    return ind


def run_strategy(ind: dict[str, np.ndarray], p: dict) -> dict:
    rsi = ind[f"rsi_{int(p['rsi_period'])}"]
    wr = ind[f"wr_{int(p['wr_period'])}"]
    stoch_k = ind["stoch_k"]
    macd = ind["macd"]
    macd_sig = ind["macd_sig"]
    atr = ind[f"atr_{int(p['atr_period'])}"]
    ema = ind[f"ema_{int(p['ema_trend_period'])}"]
    o = ind["open"]; h = ind["high"]; l = ind["low"]; c = ind["close"]
    n = len(c)

    rsi_os = float(p["rsi_os"]); wr_os = float(p["wr_os"]); stoch_os = float(p["stoch_os"])
    use_rsi = int(p["use_rsi"]); use_wr = int(p["use_wr"]); use_stoch = int(p["use_stoch"])
    use_macd = int(p["use_macd"]); use_cdl = int(p["use_cdl"])
    min_confluence = int(p.get("min_confluence", 1))
    confluence_window = int(p.get("confluence_window", 1))
    use_trend_filter = int(p.get("use_trend_filter", 1))
    tp_mult = float(p["atr_tp_multiplier"]); sl_mult = float(p["atr_sl_multiplier"])
    max_hold = int(p["max_hold_bars"]); cooldown = int(p.get("cooldown_bars", 1))
    require_close_above_open = int(p.get("require_close_above_open", 0))
    require_close_above_ema_fast = int(p.get("require_close_above_ema_fast", 0))
    ema_fast_key = f"ema_{int(p.get('ema_fast_period', 50))}"
    ema_fast = ind.get(ema_fast_key)

    cdl_keys = [k for k in ind if k.startswith("cdl_")] if use_cdl else []

    pos = 0
    entry_price = 0.0
    tp_price = 0.0
    sl_price = 0.0
    bars_in = 0
    bars_since_exit = 10**9

    trades = 0
    wins = 0
    losses = 0
    total_commission = 0.0
    pnl_pct_sum = 0.0  # sum of net per-trade returns relative to entry (additive in % terms)
    equity = 1.0  # 1 unit, multiplicative compounding via (1 + net_per_trade) leverage=1

    # rolling triggers for confluence
    trigger_history: list[set] = [set() for _ in range(max(2, confluence_window + 1))]

    for i in range(1, n):
        # exits first if in position
        if pos > 0:
            bars_in += 1
            # intra-bar TP/SL: check low for SL, high for TP. SL has priority for safety
            triggered = False
            if l[i] <= sl_price:
                exit_p = sl_price
                pnl = (exit_p / entry_price) - 1.0
                fee = 2 * COMMISSION
                net = pnl - fee
                equity *= (1.0 + net)
                pnl_pct_sum += net
                total_commission += fee
                trades += 1
                if net > 0: wins += 1
                else: losses += 1
                pos = 0; bars_in = 0; bars_since_exit = 0
                triggered = True
            elif h[i] >= tp_price:
                exit_p = tp_price
                pnl = (exit_p / entry_price) - 1.0
                fee = 2 * COMMISSION
                net = pnl - fee
                equity *= (1.0 + net)
                pnl_pct_sum += net
                total_commission += fee
                trades += 1
                if net > 0: wins += 1
                else: losses += 1
                pos = 0; bars_in = 0; bars_since_exit = 0
                triggered = True
            elif bars_in >= max_hold:
                exit_p = c[i]
                pnl = (exit_p / entry_price) - 1.0
                fee = 2 * COMMISSION
                net = pnl - fee
                equity *= (1.0 + net)
                pnl_pct_sum += net
                total_commission += fee
                trades += 1
                if net > 0: wins += 1
                else: losses += 1
                pos = 0; bars_in = 0; bars_since_exit = 0
                triggered = True
            if triggered or pos > 0:
                pass

        # build current bar triggers (always, even when in position, for history tracking)
        cur: set = set()
        if use_rsi and not np.isnan(rsi[i-1]) and not np.isnan(rsi[i]):
            if rsi[i-1] <= rsi_os and rsi[i] > rsi_os:
                cur.add("RSI")
        if use_wr and not np.isnan(wr[i-1]) and not np.isnan(wr[i]):
            if wr[i-1] <= wr_os and wr[i] > wr_os:
                cur.add("WR")
        if use_stoch and not np.isnan(stoch_k[i-1]) and not np.isnan(stoch_k[i]):
            if stoch_k[i-1] <= stoch_os and stoch_k[i] > stoch_os:
                cur.add("STOCH")
        if use_macd and not np.isnan(macd[i-1]) and not np.isnan(macd_sig[i-1]) and not np.isnan(macd[i]) and not np.isnan(macd_sig[i]):
            if macd[i-1] <= macd_sig[i-1] and macd[i] > macd_sig[i]:
                cur.add("MACD")
        if cdl_keys:
            for k in cdl_keys:
                v = ind[k][i]
                if not np.isnan(v) and v > 0:
                    cur.add(k); break

        trigger_history.append(cur)
        trigger_history.pop(0)

        if pos == 0:
            bars_since_exit += 1
            if bars_since_exit < cooldown:
                continue
            atr_v = atr[i]
            if np.isnan(atr_v) or atr_v <= 0:
                continue
            ema_v = ema[i]
            price = c[i]
            if use_trend_filter and (np.isnan(ema_v) or price < ema_v):
                continue
            if require_close_above_open and not (c[i] > o[i]):
                continue
            if require_close_above_ema_fast and ema_fast is not None:
                if np.isnan(ema_fast[i]) or price < ema_fast[i]:
                    continue
            if not cur:
                continue
            union: set = set()
            for s in trigger_history[-confluence_window:]:
                union |= s
            if len(union) < min_confluence:
                continue
            entry_price = price
            tp_price = price + tp_mult * atr_v
            sl_price = price - sl_mult * atr_v
            pos = 1
            bars_in = 0

    # close any open position at last bar
    if pos > 0:
        exit_p = c[-1]
        pnl = (exit_p / entry_price) - 1.0
        fee = 2 * COMMISSION
        net = pnl - fee
        equity *= (1.0 + net)
        pnl_pct_sum += net
        total_commission += fee
        trades += 1
        if net > 0: wins += 1
        else: losses += 1

    return {
        "trades": trades,
        "tpd": trades / DAYS,
        "win_rate": (wins / trades * 100.0) if trades else 0.0,
        "ret_pct": (equity - 1.0) * 100.0,
        "comm_units": total_commission * 100.0,  # %
    }


async def main():
    klines = {}
    for itv in ("5m", "15m", "3m"):
        _p(f"fetching {itv} ...", flush=True)
        klines[itv] = await fetch(itv)
        _p(f"  {itv}: {len(klines[itv])}", flush=True)

    inds = {itv: precompute(kl) for itv, kl in klines.items()}
    _p("precomputed indicators", flush=True)

    fout = open(RESULTS, "w", encoding="utf-8")
    lb = []

    base = {
        "rsi_period": 14, "wr_period": 14, "atr_period": 14,
        "stoch_os": 20.0,
        "use_rsi": 1, "use_wr": 1, "use_stoch": 1, "use_macd": 1,
        "use_trend_filter": 1, "cooldown_bars": 1,
        "require_close_above_open": 0, "require_close_above_ema_fast": 0,
        "ema_fast_period": 50,
    }

    g = {
        "rsi_os": [30.0, 35.0, 40.0, 45.0],
        "wr_os": [-85.0, -80.0, -70.0, -60.0],
        "ema_trend_period": [50, 100, 200],
        "atr_tp_multiplier": [1.5, 2.0, 2.5, 3.0, 4.0],
        "atr_sl_multiplier": [0.7, 1.0, 1.5, 2.0],
        "max_hold_bars": [20, 40, 80],
        "use_cdl": [0, 1],
        "min_confluence": [1, 2],
        "confluence_window": [1, 3, 5],
        "require_close_above_open": [0, 1],
    }
    keys = list(g.keys())
    combos = list(itertools.product(*[g[k] for k in keys]))
    _p(f"combos per timeframe: {len(combos)}", flush=True)

    for itv in ("15m", "5m", "3m"):
        ind = inds[itv]
        for vals in combos:
            p = dict(base)
            for k, v in zip(keys, vals):
                p[k] = v
            r = run_strategy(ind, p)
            t = r["trades"]; tpd = r["tpd"]; ret = r["ret_pct"]; win = r["win_rate"]
            rec = {"itv": itv, **r, "params": {k: p[k] for k in keys}}
            lb.append(rec)
            fout.write(json.dumps(rec) + "\n")
            if (tpd >= 5 and ret > 0) or ret > 5.0:
                _p(f"{'GOAL' if (tpd >= 5 and ret > 0) else 'POS'} [{itv}] t={t} ({tpd:.2f}/d) win={win:.1f}% ret={ret:+.2f}% {p}", flush=True)
        fout.flush()
        _p(f"=== {itv} done, {len(lb)} cumulative ===", flush=True)

    fout.close()
    qual = [r for r in lb if r["tpd"] >= 5 and r["ret_pct"] > 0]
    qual.sort(key=lambda r: r["ret_pct"], reverse=True)
    _p(f"\n=== Qualifying (>=5/d AND ret>0): {len(qual)} ===", flush=True)
    for r in qual[:50]:
        _p(f"[{r['itv']}] t={r['trades']} ({r['tpd']:.2f}/d) win={r['win_rate']:.1f}% ret={r['ret_pct']:+.2f}% {r['params']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
