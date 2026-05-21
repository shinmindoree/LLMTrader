"""Verify MFP backtest gap-fill: after initialize() in backtest mode, the
leg state's tf_ts arrays should extend past the parquet cutoff and
_tf_idx_for(ts) should return non-(-1) for post-cutoff bars.

Mirrors _diag_mfp_freeze.py but exercises the real initialize() path with a
stub BacktestContext that exposes ``end_ts`` (the field added by the fix).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import strategies.multi_factor_portfolio_strategy as mfp


class _StubCtx(SimpleNamespace):
    """Minimal StrategyContext-like object that triggers the backtest branch.

    The class name must contain 'Backtest' so MFP.initialize() picks the
    backtest path (see ``ctx_cls = type(ctx).__name__``).
    """


# Pretend the class is named "FakeBacktestCtx" so the discriminator picks
# backtest mode.
_StubCtx.__name__ = "FakeBacktestCtx"


def main() -> None:
    end_dt = pd.Timestamp("2026-05-21 23:45:00", tz="UTC")
    end_ts = int(end_dt.timestamp() * 1000)

    ctx = _StubCtx(symbol="BTCUSDT", end_ts=end_ts)

    strat = mfp.MultiFactorPortfolioStrategy()
    strat.initialize(ctx)  # type: ignore[arg-type]

    unified = strat._unified
    last_ts = int(unified["ts"].iloc[-1])
    print(f"unified rows={len(unified)} last_ts={pd.Timestamp(last_ts, unit='ms', tz='UTC')}")
    print(f"data_gap_counts={dict(strat._data_gap_counts)}")
    print()

    # Sample post-cutoff bars and count leg hits.
    parquet_last = int(pd.Timestamp("2026-05-10 09:00:00", tz="UTC").timestamp() * 1000)
    bar_ms = 15 * 60 * 1000

    after = 0
    hits_after = 0
    ts = parquet_last + bar_ms
    print(f"{'bar_ts (UTC)':30s}  any_leg_hit  legs_w_hit")
    sample = 0
    while ts <= end_ts:
        hits = sum(1 for leg in strat._legs if strat._tf_idx_for(leg, ts) >= 0)
        after += 1
        if hits > 0:
            hits_after += 1
        if sample < 12 and ts % (12 * 3600 * 1000) == 0:
            ts_iso = str(pd.Timestamp(ts, unit="ms", tz="UTC"))
            print(f"{ts_iso:30s}  {'yes' if hits else 'no':11s}  {hits}")
            sample += 1
        ts += bar_ms

    print()
    print(f"summary: post-cutoff bars total={after} bars_with_leg_hit={hits_after}")


if __name__ == "__main__":
    main()
