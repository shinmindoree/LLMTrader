r"""Screen perp symbols for mean-reversion (MR) suitability vs BTCUSDT.

The multi-factor-portfolio (MFP) strategy is 15/17 mean-reversion legs. Its edge
comes from *mean-reverting* volatility (range/chop), not raw volatility or trend.
This tool fetches 15m USDM-perp klines from Binance and computes, per symbol:

  * realized vol (annualized, from 15m log returns)
  * ATR% (median 14-period ATR / close) at 15m and at the MR legs' working TFs
  * Hurst exponent H  (< 0.5 => mean-reverting, 0.5 random walk, > 0.5 trending)
  * Variance Ratio VR(q)  (< 1 => mean-reverting)
  * lag-1 autocorrelation of returns (negative => mean-reverting)
  * ATR-gate pass rate: fraction of bars where ATR% sits inside the BTC-tuned
    gate [atr_min_pct, atr_max_pct] used by the MR legs (default 0.0025..0.025).
    A symbol that almost never sits in the gate would have most legs silenced;
    one that is always above it would trade constantly with a too-tight % SL.

A composite "MR score" combines (0.5 - H), (1 - VR), and (-autocorr_lag1),
z-scored across the screened universe, so higher = more MR-friendly than peers.
BTCUSDT is always included as the reference baseline.

Usage::

    .\.venv\Scripts\python.exe scripts/screen_mr_suitability.py \
        --symbols ETHUSDT,SOLUSDT,DOGEUSDT,BNBUSDT \
        --days 180

    # also emit JSON for downstream tooling
    .\.venv\Scripts\python.exe scripts/screen_mr_suitability.py \
        --symbols ETHUSDT,SOLUSDT --days 365 --json out/mr_screen.json

Notes
-----
* Klines are public; no API key required. OI/funding/LSR are NOT needed for this
  structural screen (they are second-order vs the price-process character).
* This screen is *necessary but not sufficient*: a favourable structure means the
  MR legs *can* work, but final parameters still need a per-symbol discovery
  re-run (scripts/_alpha_lab/pass5_consistency.py --symbol ...).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import talib

BINANCE_FAPI = "https://fapi.binance.com"
BAR_MS_15M = 15 * 60 * 1000

# MR-leg ATR gate (from _MEAN_REV_LEGS pullback_in_trend config in
# scripts/strategies/multi_factor_portfolio_strategy.py).
DEFAULT_ATR_MIN_PCT = 0.0025
DEFAULT_ATR_MAX_PCT = 0.025

# MR legs operate at these resampled intervals (minutes).
MR_TFS_MIN = (15, 30, 60)


def _now_ms() -> int:
    return int(time.time() * 1000)


def fetch_klines_15m(symbol: str, start_ms: int, end_ms: int,
                     *, base_url: str = BINANCE_FAPI) -> pd.DataFrame:
    """Fetch 15m USDM-perp klines into a tidy OHLCV DataFrame (inclusive range)."""
    url = f"{base_url}/fapi/v1/klines"
    chunk_ms = 1000 * BAR_MS_15M
    rows: list[list] = []
    cur = start_ms
    with httpx.Client(timeout=20.0) as cli:
        while cur <= end_ms:
            params = {
                "symbol": symbol.upper(),
                "interval": "15m",
                "startTime": int(cur),
                "endTime": int(min(cur + chunk_ms, end_ms)),
                "limit": 1000,
            }
            for attempt in range(5):
                try:
                    resp = cli.get(url, params=params)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        time.sleep(2 ** attempt)
                        continue
                    resp.raise_for_status()
                    batch = resp.json() or []
                    rows.extend(batch)
                    if not batch:
                        cur = cur + chunk_ms + BAR_MS_15M
                    else:
                        cur = int(batch[-1][0]) + BAR_MS_15M
                    break
                except httpx.HTTPError:
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
            else:
                break
    by_ts: dict[int, list] = {}
    for r in rows:
        ts = int(r[0])
        if start_ms <= ts <= end_ms:
            by_ts[ts] = r
    ordered = [by_ts[k] for k in sorted(by_ts)]
    if not ordered:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame({
        "ts": [int(r[0]) for r in ordered],
        "open": [float(r[1]) for r in ordered],
        "high": [float(r[2]) for r in ordered],
        "low": [float(r[3]) for r in ordered],
        "close": [float(r[4]) for r in ordered],
        "volume": [float(r[5]) for r in ordered],
    })
    return df


def _resample_ohlc(df: pd.DataFrame, target_min: int) -> pd.DataFrame:
    if target_min == 15:
        return df
    work = df.copy()
    work["dt"] = pd.to_datetime(work["ts"], unit="ms", utc=True)
    work = work.set_index("dt")
    out = work.resample(f"{target_min}min", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna(subset=["open", "close"])
    return out.reset_index(drop=True)


def hurst_exponent(log_close: np.ndarray,
                   lags: tuple[int, ...] = (2, 4, 8, 16, 32, 64, 128)) -> float:
    """Hurst via the variance-of-differences method.

    Var[Δk x] ∝ k^(2H)  =>  slope of log Var vs log k is 2H.
    Returns NaN if there are too few points.
    """
    x = np.asarray(log_close, dtype="float64")
    x = x[np.isfinite(x)]
    n = x.size
    usable = [k for k in lags if k < n // 2]
    if len(usable) < 3:
        return float("nan")
    logk: list[float] = []
    logv: list[float] = []
    for k in usable:
        diffs = x[k:] - x[:-k]
        v = float(np.var(diffs))
        if v <= 0:
            continue
        logk.append(np.log(k))
        logv.append(np.log(v))
    if len(logk) < 3:
        return float("nan")
    slope = float(np.polyfit(logk, logv, 1)[0])
    return slope / 2.0


def variance_ratio(log_close: np.ndarray, q: int = 8) -> float:
    """Lo-MacKinlay variance ratio VR(q) = Var[q-return] / (q * Var[1-return]).

    VR < 1 => mean reversion, VR = 1 => random walk, VR > 1 => trending.
    """
    x = np.asarray(log_close, dtype="float64")
    x = x[np.isfinite(x)]
    r1 = np.diff(x)
    if r1.size <= q:
        return float("nan")
    var1 = float(np.var(r1))
    if var1 <= 0:
        return float("nan")
    rq = x[q:] - x[:-q]
    varq = float(np.var(rq))
    return varq / (q * var1)


def autocorr_lag1(log_close: np.ndarray) -> float:
    x = np.asarray(log_close, dtype="float64")
    x = x[np.isfinite(x)]
    r = np.diff(x)
    if r.size < 3:
        return float("nan")
    r = r - r.mean()
    denom = float(np.dot(r, r))
    if denom <= 0:
        return float("nan")
    return float(np.dot(r[:-1], r[1:]) / denom)


def atr_pct_series(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    atr = talib.ATR(df["high"].to_numpy(dtype="float64"),
                    df["low"].to_numpy(dtype="float64"),
                    df["close"].to_numpy(dtype="float64"),
                    timeperiod=period)
    close = df["close"].to_numpy(dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = atr / close
    return pct[np.isfinite(pct)]


def analyze_symbol(symbol: str, df: pd.DataFrame, *,
                   atr_min_pct: float, atr_max_pct: float) -> dict:
    log_close = np.log(df["close"].to_numpy(dtype="float64"))

    # Annualized realized vol from 15m log returns (35040 bars/yr).
    r1 = np.diff(log_close)
    bars_per_year = 365 * 24 * 4
    realized_vol = float(np.std(r1) * np.sqrt(bars_per_year)) if r1.size else float("nan")

    # ATR% across MR working TFs (median + gate pass rate at each TF, averaged).
    atr_pct_by_tf: dict[str, float] = {}
    gate_pass_rates: list[float] = []
    for tf in MR_TFS_MIN:
        rdf = _resample_ohlc(df, tf)
        pct = atr_pct_series(rdf)
        if pct.size:
            atr_pct_by_tf[f"atr_pct_{tf}m_med"] = float(np.median(pct))
            in_gate = np.mean((pct >= atr_min_pct) & (pct <= atr_max_pct))
            gate_pass_rates.append(float(in_gate))
    gate_pass_rate = float(np.mean(gate_pass_rates)) if gate_pass_rates else float("nan")

    h = hurst_exponent(log_close)
    vr = variance_ratio(log_close, q=8)
    ac1 = autocorr_lag1(log_close)

    return {
        "symbol": symbol,
        "bars": int(df.shape[0]),
        "realized_vol_annual": realized_vol,
        "hurst": h,
        "variance_ratio_q8": vr,
        "autocorr_lag1": ac1,
        "atr_gate_pass_rate": gate_pass_rate,
        **atr_pct_by_tf,
    }


def _zscore(vals: np.ndarray) -> np.ndarray:
    finite = vals[np.isfinite(vals)]
    if finite.size < 2:
        return np.zeros_like(vals)
    mu = float(np.mean(finite))
    sd = float(np.std(finite))
    if sd <= 0:
        return np.zeros_like(vals)
    out = (vals - mu) / sd
    out[~np.isfinite(out)] = 0.0
    return out


def add_mr_score(rows: list[dict]) -> None:
    """Composite MR-friendliness z-score across the screened universe.

    Each component is oriented so that *more positive = more mean-reverting*:
      (0.5 - hurst), (1 - variance_ratio), (-autocorr_lag1).
    """
    if not rows:
        return
    h = np.array([r["hurst"] for r in rows], dtype="float64")
    vr = np.array([r["variance_ratio_q8"] for r in rows], dtype="float64")
    ac = np.array([r["autocorr_lag1"] for r in rows], dtype="float64")
    comp = (_zscore(0.5 - h) + _zscore(1.0 - vr) + _zscore(-ac)) / 3.0
    for r, s in zip(rows, comp):
        r["mr_score"] = float(s)


def _fmt(v: object) -> str:
    if isinstance(v, float):
        if not np.isfinite(v):
            return "   n/a"
        return f"{v:7.4f}"
    return str(v)


def print_table(rows: list[dict]) -> None:
    cols = [
        ("symbol", "symbol", 10),
        ("mr_score", "MR_score", 9),
        ("hurst", "Hurst", 8),
        ("variance_ratio_q8", "VR(q8)", 8),
        ("autocorr_lag1", "AC1", 8),
        ("atr_gate_pass_rate", "GatePass", 9),
        ("realized_vol_annual", "RVol", 8),
        ("bars", "bars", 7),
    ]
    header = "  ".join(f"{title:>{w}}" for _, title, w in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        line = "  ".join(
            f"{_fmt(r.get(key)):>{w}}" if key != "symbol"
            else f"{str(r.get(key)):>{w}}"
            for key, _, w in cols
        )
        print(line)
    print()
    print("Reading guide:")
    print("  Hurst   < 0.50 => mean-reverting (MR-friendly); > 0.50 => trending.")
    print("  VR(q8)  < 1.00 => mean-reverting; > 1.00 => trending/momentum.")
    print("  AC1     < 0    => returns reverse next bar (MR-friendly).")
    print("  GatePass        => frac of bars inside BTC-tuned ATR gate "
          f"[{DEFAULT_ATR_MIN_PCT}, {DEFAULT_ATR_MAX_PCT}].")
    print("  MR_score        => composite z-score vs this universe; higher = more MR.")
    print("  NOTE: a high MR_score means the MR legs *can* work, but final params")
    print("        still need a per-symbol discovery re-run before live use.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default="ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT",
                    help="comma-separated USDM perp symbols (BTCUSDT auto-added).")
    ap.add_argument("--days", type=int, default=180,
                    help="lookback window in days (default 180).")
    ap.add_argument("--atr-min-pct", type=float, default=DEFAULT_ATR_MIN_PCT)
    ap.add_argument("--atr-max-pct", type=float, default=DEFAULT_ATR_MAX_PCT)
    ap.add_argument("--base-url", default=BINANCE_FAPI)
    ap.add_argument("--json", default=None, help="optional path to write JSON results.")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if "BTCUSDT" not in symbols:
        symbols = ["BTCUSDT", *symbols]

    end_ms = _now_ms()
    start_ms = end_ms - args.days * 24 * 3600 * 1000

    rows: list[dict] = []
    for sym in symbols:
        print(f"[fetch] {sym} ({args.days}d of 15m klines)...", file=sys.stderr)
        try:
            df = fetch_klines_15m(sym, start_ms, end_ms, base_url=args.base_url)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {sym}: fetch failed: {exc}", file=sys.stderr)
            continue
        if df.shape[0] < 500:
            print(f"[warn] {sym}: only {df.shape[0]} bars; skipping.", file=sys.stderr)
            continue
        rows.append(analyze_symbol(sym, df,
                                   atr_min_pct=args.atr_min_pct,
                                   atr_max_pct=args.atr_max_pct))

    if not rows:
        print("No symbols analyzed.", file=sys.stderr)
        return 1

    add_mr_score(rows)
    rows.sort(key=lambda r: (r.get("mr_score") if np.isfinite(r.get("mr_score", float("nan"))) else -1e9),
              reverse=True)

    print()
    print_table(rows)

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "window_days": args.days,
            "atr_gate": [args.atr_min_pct, args.atr_max_pct],
            "results": rows,
        }, indent=2))
        print(f"[json] wrote {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
