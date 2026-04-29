"""Microstructure alpha sweep harness — BTCUSDT-PERP.

Loads 15m klines (cached) and each microstructure feature (OI, LSR variants, taker
ratio) resampled to 15m. Computes a generic per-bar signal and runs a vectorized
event simulator across a parameter grid, then evaluates train/test OOS split.

Signal modes supported:
  - 'z_contra': trade contrarian when feature z-score (rolling N bars) is extreme
  - 'z_follow': trade direction-follow on feature z-score extreme
  - 'level_contra': trade contrarian when feature crosses absolute thresholds
  - 'oi_price': enter based on (OI delta sign, price delta sign) regime

Entry rule: enter at NEXT bar open when condition met, exit on TP/SL/max_hold.
Cooldown: no overlapping positions.
"""
from __future__ import annotations

import itertools
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

KLINES_CACHE = PROJECT_ROOT / "data" / "perp_meta" / "BTCUSDT_15m_klines.parquet"
META_DIR = PROJECT_ROOT / "data" / "perp_meta"
COMMISSION = 0.0002

TRAIN_START = datetime(2023, 4, 29, tzinfo=timezone.utc)
TRAIN_END = datetime(2025, 4, 29, tzinfo=timezone.utc)
TEST_START = TRAIN_END
TEST_END = datetime(2026, 4, 29, tzinfo=timezone.utc)


def load_klines_15m():
    df = pd.read_parquet(KLINES_CACHE).rename(columns={"ts":"ts","o":"o","h":"h","l":"l","c":"c"})
    df = df.sort_values("ts").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def load_micro(feature_key: str) -> pd.DataFrame:
    """Load microstructure parquet, return DataFrame with ts (ms) and one column 'val'."""
    if feature_key == "lsr_top_pos":
        df = pd.read_parquet(META_DIR / "BTCUSDT_lsr_5m.parquet")
        df = df.rename(columns={"sum_toptrader_long_short_ratio": "val"})[["timestamp","val"]]
    elif feature_key == "lsr_top_acc":
        df = pd.read_parquet(META_DIR / "BTCUSDT_lsr_5m.parquet")
        df = df.rename(columns={"count_toptrader_long_short_ratio": "val"})[["timestamp","val"]]
    elif feature_key == "lsr_acc":
        df = pd.read_parquet(META_DIR / "BTCUSDT_lsr_5m.parquet")
        df = df.rename(columns={"count_long_short_ratio": "val"})[["timestamp","val"]]
    elif feature_key == "oi":
        df = pd.read_parquet(META_DIR / "BTCUSDT_oi_5m.parquet")
        df = df.rename(columns={"sum_oi": "val"})[["timestamp","val"]]
    elif feature_key == "taker":
        df = pd.read_parquet(META_DIR / "BTCUSDT_taker_5m.parquet")
        df = df.rename(columns={"sum_taker_long_short_vol_ratio": "val"})[["timestamp","val"]]
    else:
        raise ValueError(feature_key)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].astype("int64")
    return df


def align_to_klines(klines_ts_ms: np.ndarray, micro: pd.DataFrame) -> np.ndarray:
    """For each kline ts, take the most recent micro value (forward-fill).
    Returns numpy array of same length as klines_ts_ms.
    """
    m_t = micro["timestamp"].to_numpy(dtype="int64")
    m_v = micro["val"].to_numpy(dtype="float64")
    idx = np.searchsorted(m_t, klines_ts_ms, side="right") - 1
    idx = np.clip(idx, 0, len(m_t) - 1)
    out = m_v[idx]
    # mark NaN where idx is before micro range
    out[klines_ts_ms < m_t[0]] = np.nan
    return out


def rolling_z(arr: np.ndarray, win: int) -> np.ndarray:
    s = pd.Series(arr)
    mean = s.rolling(win, min_periods=win).mean()
    std = s.rolling(win, min_periods=win).std()
    z = (s - mean) / std
    return z.to_numpy()


def simulate_signal(ts, o, h, l, c, signal, hold_bars, tp_pct, sl_pct, mask=None, months_arr=None):
    """signal[i] in {-1,0,+1} = at bar i, enter SHORT/no/LONG at bar i+1 open.
    Returns dict of stats; also collects monthly stats keyed by year-month string.
    """
    n = len(ts)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    gp = gl = 0.0
    trades = longs = shorts = wins = 0
    busy_until = -1
    monthly = {}  # ym -> {trades, ret, gp, gl, longs, shorts}

    if months_arr is None:
        months_arr = pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m").to_numpy()

    # Iterate only over nonzero, finite signal indices
    sig_int = np.zeros(n, dtype=np.int8)
    valid = np.isfinite(signal)
    sig_int[valid & (signal > 0.5)] = 1
    sig_int[valid & (signal < -0.5)] = -1
    nz_idx = np.flatnonzero(sig_int)

    for i in nz_idx:
        if i <= busy_until:
            continue
        if mask is not None and not mask[i]:
            continue
        bar_i = i + 1
        s = sig_int[i]
        if bar_i >= n - 1:
            continue
        side = int(s)
        entry_p = o[bar_i]
        if side == 1:
            tp_p = entry_p * (1 + tp_pct); sl_p = entry_p * (1 - sl_pct)
        else:
            tp_p = entry_p * (1 - tp_pct); sl_p = entry_p * (1 + sl_pct)
        end_bar = min(bar_i + hold_bars, n - 1)
        exit_p = None
        for j in range(bar_i, end_bar + 1):
            if side == 1:
                if l[j] <= sl_p: exit_p = sl_p; break
                if h[j] >= tp_p: exit_p = tp_p; break
            else:
                if h[j] >= sl_p: exit_p = sl_p; break
                if l[j] <= tp_p: exit_p = tp_p; break
        if exit_p is None:
            exit_p = c[end_bar]

        if side == 1:
            ret = (exit_p / entry_p) - 1.0
        else:
            ret = (entry_p / exit_p) - 1.0
        net = ret - 2 * COMMISSION
        equity *= (1 + net)
        if equity > peak: peak = equity
        d = (peak - equity) / peak
        if d > max_dd: max_dd = d
        trades += 1
        if side == 1: longs += 1
        else: shorts += 1
        if net > 0: wins += 1; gp += net
        else: gl += -net
        ym = months_arr[bar_i]
        if ym not in monthly:
            monthly[ym] = {"trades":0,"eq":1.0,"gp":0.0,"gl":0.0,"L":0,"S":0}
        m = monthly[ym]
        m["trades"] += 1
        m["eq"] *= (1 + net)
        if net > 0: m["gp"] += net
        else: m["gl"] += -net
        if side == 1: m["L"] += 1
        else: m["S"] += 1
        busy_until = end_bar

    pf = (gp / gl) if gl > 1e-12 else (float("inf") if gp > 0 else 0.0)
    return {
        "trades": trades, "longs": longs, "shorts": shorts, "wins": wins,
        "ret": equity - 1.0, "pf": pf, "dd": max_dd,
        "gp": gp, "gl": gl, "monthly": monthly,
    }


def slice_monthly(monthly: dict, start: datetime, end: datetime):
    out = {}
    cur = start.replace(day=1)
    while cur < end:
        key = cur.strftime("%Y-%m")
        if key in monthly:
            out[key] = monthly[key]
        nxt = cur.replace(year=cur.year + (1 if cur.month==12 else 0),
                          month=1 if cur.month==12 else cur.month+1)
        cur = nxt
    return out


def aggregate(monthly_slice: dict):
    eq = 1.0; peak = 1.0; dd = 0.0
    gp = gl = 0.0; trades = 0; longs = shorts = 0
    pos_m = neg_m = 0
    for k in sorted(monthly_slice.keys()):
        m = monthly_slice[k]
        ret = m["eq"] - 1.0
        eq *= (1 + ret)
        if eq > peak: peak = eq
        d = (peak - eq) / peak
        if d > dd: dd = d
        gp += m["gp"]; gl += m["gl"]
        trades += m["trades"]
        longs += m["L"]; shorts += m["S"]
        if ret > 0: pos_m += 1
        elif ret < 0: neg_m += 1
    pf = (gp / gl) if gl > 1e-12 else (float("inf") if gp > 0 else 0.0)
    return {"agg_ret": eq - 1.0, "agg_trades": trades, "agg_pf": pf, "agg_dd": dd,
            "pos_months": pos_m, "neg_months": neg_m, "longs": longs, "shorts": shorts,
            "n_months": pos_m + neg_m + sum(1 for k in monthly_slice if monthly_slice[k]["trades"]==0)}


def build_signal_z(feat: np.ndarray, win: int, k: float, direction: str = "contra"):
    """Z-score based signal. direction='contra' (high feature -> short, low -> long)
    or 'follow' (high -> long).
    Feature is interpreted directly (e.g. taker ratio: high = aggressive buying, contra=>short).
    For LSR: high LSR (more longs) -> contra: short.
    For taker ratio: high (more buying) -> contra: short.
    """
    z = rolling_z(feat, win)
    sig = np.zeros_like(z)
    if direction == "contra":
        sig[z > k] = -1
        sig[z < -k] = 1
    else:
        sig[z > k] = 1
        sig[z < -k] = -1
    return sig


def build_signal_oi_price(oi_arr: np.ndarray, c: np.ndarray, win: int, k_oi: float, k_p: float, mode: str):
    """OI delta + price delta regime signal.
    mode='oi_up_p_down_long': entered when OI rising and price falling (short squeeze setup) -> LONG
    mode='oi_up_p_up_long':  OI rising + price rising -> LONG (trend follow)
    mode='oi_down_p_up_short':OI falling + price rising -> SHORT (short cover top)
    mode='oi_down_p_down_long': OI falling + price falling -> LONG (capitulation bottom)
    """
    oi_chg = pd.Series(oi_arr).pct_change(win).to_numpy()
    p_chg = pd.Series(c).pct_change(win).to_numpy()
    sig = np.zeros_like(oi_chg)
    if mode == "oi_up_p_down_long":
        sig[(oi_chg > k_oi) & (p_chg < -k_p)] = 1
    elif mode == "oi_up_p_up_long":
        sig[(oi_chg > k_oi) & (p_chg > k_p)] = 1
    elif mode == "oi_down_p_up_short":
        sig[(oi_chg < -k_oi) & (p_chg > k_p)] = -1
    elif mode == "oi_down_p_down_long":
        sig[(oi_chg < -k_oi) & (p_chg < -k_p)] = 1
    elif mode == "oi_up_p_down_short":
        sig[(oi_chg > k_oi) & (p_chg < -k_p)] = -1
    elif mode == "oi_up_p_up_short":
        sig[(oi_chg > k_oi) & (p_chg > k_p)] = -1
    return sig


def fmt_combo(c):
    return " ".join(f"{k}={v}" for k, v in c.items() if k != "monthly")


@dataclass
class SweepResult:
    rows: list


def run_z_sweep(klines_df, feat_arr, feature_name, direction, grids, label):
    """grids: dict with 'win','k','hold','tp','sl' lists."""
    ts = klines_df["ts"].to_numpy(dtype="int64")
    o = klines_df["o"].to_numpy(); h = klines_df["h"].to_numpy()
    l = klines_df["l"].to_numpy(); c = klines_df["c"].to_numpy()

    rows = []
    n_combos = (len(grids["win"])*len(grids["k"])*len(grids["hold"])*len(grids["tp"])*len(grids["sl"]))
    print(f"[{label}] {feature_name} {direction}: {n_combos} combos")
    written = 0
    for win in grids["win"]:
        for k in grids["k"]:
            sig = build_signal_z(feat_arr, win, k, direction)
            for hold_h, tp, sl in itertools.product(grids["hold"], grids["tp"], grids["sl"]):
                hold_bars = int(hold_h * 4)
                full = simulate_signal(ts, o, h, l, c, sig, hold_bars, tp, sl)
                tr = aggregate(slice_monthly(full["monthly"], TRAIN_START, TRAIN_END))
                te = aggregate(slice_monthly(full["monthly"], TEST_START, TEST_END))
                rows.append({
                    "feature": feature_name, "mode": f"z_{direction}",
                    "win": win, "k": k, "hold_h": hold_h, "tp": tp, "sl": sl,
                    "train": tr, "test": te,
                })
                written += 1
                if written % 200 == 0:
                    print(f"  ...{written}/{n_combos}")
    return rows


def run_oi_sweep(klines_df, oi_arr, mode, grids, label):
    ts = klines_df["ts"].to_numpy(dtype="int64")
    o = klines_df["o"].to_numpy(); h = klines_df["h"].to_numpy()
    l = klines_df["l"].to_numpy(); c = klines_df["c"].to_numpy()
    rows = []
    n_combos = len(grids["win"])*len(grids["k_oi"])*len(grids["k_p"])*len(grids["hold"])*len(grids["tp"])*len(grids["sl"])
    print(f"[{label}] OI mode={mode}: {n_combos} combos")
    written = 0
    for win in grids["win"]:
        for k_oi in grids["k_oi"]:
            for k_p in grids["k_p"]:
                sig = build_signal_oi_price(oi_arr, c, win, k_oi, k_p, mode)
                for hold_h, tp, sl in itertools.product(grids["hold"], grids["tp"], grids["sl"]):
                    hold_bars = int(hold_h * 4)
                    full = simulate_signal(ts, o, h, l, c, sig, hold_bars, tp, sl)
                    tr = aggregate(slice_monthly(full["monthly"], TRAIN_START, TRAIN_END))
                    te = aggregate(slice_monthly(full["monthly"], TEST_START, TEST_END))
                    rows.append({
                        "feature": "oi", "mode": mode,
                        "win": win, "k_oi": k_oi, "k_p": k_p, "hold_h": hold_h, "tp": tp, "sl": sl,
                        "train": tr, "test": te,
                    })
                    written += 1
                    if written % 200 == 0:
                        print(f"  ...{written}/{n_combos}")
    return rows


def passes_train(r, min_pf=1.1, min_trades=20, max_dd=0.30, min_pos_m=14):
    t = r["train"]
    if t["agg_ret"] <= 0: return False
    if t["agg_pf"] is None: return False
    if t["agg_pf"] < min_pf: return False
    if t["agg_trades"] < min_trades: return False
    if t["agg_dd"] > max_dd: return False
    if t["pos_months"] < min_pos_m: return False
    return True


def survives_test(r, min_pf=1.05, min_trades=15, max_dd=0.20, min_pos_m=6):
    t = r["test"]
    if t["agg_ret"] <= 0: return False
    if t["agg_pf"] is None: return False
    if t["agg_pf"] < min_pf: return False
    if t["agg_trades"] < min_trades: return False
    if t["agg_dd"] > max_dd: return False
    if t["pos_months"] < min_pos_m: return False
    return True


def fmt_row(r):
    extras = []
    for k in ("feature","mode","win","k","k_oi","k_p","hold_h","tp","sl"):
        if k in r:
            v = r[k]
            extras.append(f"{k}={v}")
    head = " ".join(extras)
    tr = r["train"]; te = r["test"]
    return (f"{head} | TRAIN agg={tr['agg_ret']*100:+.1f}% pf={tr['agg_pf']:.2f} "
            f"dd={tr['agg_dd']*100:.1f}% +M={tr['pos_months']}/{tr['pos_months']+tr['neg_months']} "
            f"trades={tr['agg_trades']} | "
            f"TEST agg={te['agg_ret']*100:+.1f}% pf={te['agg_pf']:.2f} "
            f"dd={te['agg_dd']*100:.1f}% +M={te['pos_months']}/{te['pos_months']+te['neg_months']} "
            f"trades={te['agg_trades']}")
