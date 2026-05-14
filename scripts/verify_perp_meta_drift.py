"""Verify parquet ↔ Redis ZSET drift for OI / funding / taker / LSR providers.

Why
---
``MultiFactorPortfolioStrategy`` reads OI/funding/taker/LSR from two
different sources depending on mode:

  * backtest: parquet snapshot files under ``data/perp_meta/`` (or blob)
  * live:     Redis ZSETs maintained by ``oi_ingestor`` + ``perp_meta_ingestor``

If the two sources disagree on the same ``ts_ms``, identical strategy code
produces different signals on identical input — exactly the symptom seen
when comparing backtest and live trade lists on the same window. This
script quantifies that disagreement.

How
---
For each of the 4 indicators we:

  1. Open both backends side-by-side (forces parquet + live).
  2. Sample the last ``--hours`` of timestamps from the parquet backend.
  3. Call ``value_at(ts)`` on both backends for each sample ts.
  4. Report rows where the values disagree by more than ``--tol-rel``.

Usage
-----
    # Local dev (with REDIS_URL set to live cache)
    python scripts/verify_perp_meta_drift.py --symbol BTCUSDT --hours 24

    # In CI / cron: emit JSON for downstream alerting
    python scripts/verify_perp_meta_drift.py --json > drift.json

Exit code 0 if no drift exceeds the tolerance; 1 if any indicator does.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Imports placed after sys.path injection so they resolve from src/.
from indicators.oi_provider import get_oi_provider  # noqa: E402
from indicators.perp_meta_provider import (  # noqa: E402
    get_funding_provider,
    get_lsr_provider,
    get_taker_provider,
)

logger = logging.getLogger("verify_perp_meta_drift")


# kind -> (parquet provider factory, live provider factory, value_at attr)
_KINDS: dict[str, dict[str, Any]] = {
    "oi": {
        "factory": get_oi_provider,
        "value_attr": ("value_at", "_value_at"),
        # OI cadence is 5m; relative tolerance because sum_oi is in coin units.
        "tol_rel": 1e-4,
    },
    "funding": {
        "factory": get_funding_provider,
        "value_attr": ("value_at",),
        "tol_rel": 1e-8,
    },
    "taker": {
        "factory": get_taker_provider,
        "value_attr": ("value_at",),
        # Taker ratio is bounded ~0.5..2; small abs diffs matter.
        "tol_rel": 1e-6,
    },
    "lsr": {
        "factory": get_lsr_provider,
        "value_attr": ("value_at",),
        "tol_rel": 1e-6,
    },
}


def _get_value_at(provider: Any, ts: int, attrs: tuple[str, ...]) -> float:
    for name in attrs:
        fn = getattr(provider, name, None)
        if callable(fn):
            try:
                return float(fn(int(ts)))
            except Exception as exc:  # noqa: BLE001
                logger.debug("value_at(%s) failed via %s: %s", ts, name, exc)
                return math.nan
    return math.nan


def _sample_timestamps(
    *,
    parquet_provider: Any,
    end_ms: int,
    hours: int,
    max_samples: int,
) -> np.ndarray:
    """Pick at most ``max_samples`` ts from the parquet provider over the
    last ``hours`` ending at ``end_ms``.

    Uses ``range()`` when available; otherwise falls back to scanning
    ``provider._ts`` directly.
    """
    start_ms = end_ms - hours * 3600 * 1000
    if hasattr(parquet_provider, "range"):
        ts_arr, _ = parquet_provider.range(start_ms, end_ms)
    else:
        ts_arr = getattr(parquet_provider, "_ts", np.empty(0, dtype="int64"))
        ts_arr = ts_arr[(ts_arr >= start_ms) & (ts_arr <= end_ms)]
    if ts_arr is None or len(ts_arr) == 0:
        return np.empty(0, dtype="int64")
    if len(ts_arr) <= max_samples:
        return np.asarray(ts_arr, dtype="int64")
    idx = np.linspace(0, len(ts_arr) - 1, num=max_samples).astype(int)
    return np.asarray(ts_arr, dtype="int64")[idx]


def _compare_one(
    *,
    symbol: str,
    kind: str,
    spec: dict[str, Any],
    hours: int,
    max_samples: int,
    tol_rel_override: float | None,
) -> dict[str, Any]:
    factory = spec["factory"]
    value_attrs: tuple[str, ...] = spec["value_attr"]
    tol_rel = float(tol_rel_override if tol_rel_override is not None else spec["tol_rel"])

    try:
        parquet = factory(symbol, mode="backtest")
    except Exception as exc:  # noqa: BLE001
        return {"kind": kind, "ok": False, "error": f"parquet init failed: {exc!r}"}

    try:
        live = factory(symbol, mode="live")
    except Exception as exc:  # noqa: BLE001
        return {"kind": kind, "ok": False, "error": f"live init failed: {exc!r}"}

    end_ms = int(time.time() * 1000)
    ts_samples = _sample_timestamps(
        parquet_provider=parquet,
        end_ms=end_ms,
        hours=hours,
        max_samples=max_samples,
    )

    n = int(len(ts_samples))
    if n == 0:
        return {
            "kind": kind, "ok": False,
            "error": f"no parquet samples in last {hours}h",
        }

    n_parquet_nan = 0
    n_live_nan = 0
    n_disagree = 0
    max_abs_diff = 0.0
    max_rel_diff = 0.0
    worst: dict[str, Any] | None = None
    first_disagreements: list[dict[str, Any]] = []

    for ts in ts_samples:
        v_p = _get_value_at(parquet, int(ts), value_attrs)
        v_l = _get_value_at(live, int(ts), value_attrs)
        if not math.isfinite(v_p):
            n_parquet_nan += 1
            continue
        if not math.isfinite(v_l):
            n_live_nan += 1
            continue
        abs_diff = abs(v_p - v_l)
        rel_diff = abs_diff / max(abs(v_p), 1e-12)
        if rel_diff > max_rel_diff:
            max_rel_diff = rel_diff
            max_abs_diff = abs_diff
            worst = {
                "ts": int(ts), "parquet": float(v_p), "live": float(v_l),
                "abs": float(abs_diff), "rel": float(rel_diff),
            }
        if rel_diff > tol_rel:
            n_disagree += 1
            if len(first_disagreements) < 5:
                first_disagreements.append({
                    "ts": int(ts), "parquet": float(v_p), "live": float(v_l),
                    "abs": float(abs_diff), "rel": float(rel_diff),
                })

    ok = (n_disagree == 0)
    return {
        "kind": kind,
        "ok": ok,
        "samples": n,
        "tol_rel": tol_rel,
        "n_parquet_nan": n_parquet_nan,
        "n_live_nan": n_live_nan,
        "n_disagree": n_disagree,
        "max_abs_diff": float(max_abs_diff),
        "max_rel_diff": float(max_rel_diff),
        "worst": worst,
        "first_disagreements": first_disagreements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Window size in hours to sample from the parquet timeline.",
    )
    parser.add_argument(
        "--max-samples", type=int, default=500,
        help="Max ts samples to compare per indicator.",
    )
    parser.add_argument(
        "--kinds", default="oi,funding,taker,lsr",
        help="Comma-separated subset of {oi,funding,taker,lsr}.",
    )
    parser.add_argument(
        "--tol-rel", type=float, default=None,
        help="Override the per-kind relative tolerance (default kind-specific).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    selected = [k.strip().lower() for k in args.kinds.split(",") if k.strip()]
    unknown = [k for k in selected if k not in _KINDS]
    if unknown:
        print(f"Unknown kinds: {unknown}. Valid: {sorted(_KINDS)}", file=sys.stderr)
        return 2

    # Sanity: live backend needs REDIS_URL or REDIS_HOST.
    if not (os.environ.get("REDIS_URL", "").strip()
            or os.environ.get("REDIS_HOST", "").strip()):
        print(
            "ERROR: REDIS_URL or REDIS_HOST must be set so the live backend "
            "can be exercised. (For local dev, point it at a read replica.)",
            file=sys.stderr,
        )
        return 2

    results: list[dict[str, Any]] = []
    for kind in selected:
        result = _compare_one(
            symbol=args.symbol.upper(),
            kind=kind,
            spec=_KINDS[kind],
            hours=args.hours,
            max_samples=args.max_samples,
            tol_rel_override=args.tol_rel,
        )
        results.append(result)

    exit_code = 0 if all(r.get("ok") for r in results) else 1

    if args.json:
        print(json.dumps({
            "symbol": args.symbol.upper(),
            "hours": args.hours,
            "results": results,
            "exit_code": exit_code,
        }, indent=2))
        return exit_code

    # Human-readable text output.
    print(f"Drift check: symbol={args.symbol.upper()} window={args.hours}h")
    print("-" * 72)
    for r in results:
        kind = r["kind"]
        if "error" in r:
            print(f"  {kind:8}  ERROR: {r['error']}")
            continue
        status = "OK " if r["ok"] else "FAIL"
        print(
            f"  {kind:8} [{status}] samples={r['samples']:4}  "
            f"nan(p/l)={r['n_parquet_nan']}/{r['n_live_nan']}  "
            f"disagree={r['n_disagree']}  "
            f"max_rel={r['max_rel_diff']:.2e} (tol={r['tol_rel']:.0e})"
        )
        if r["worst"] is not None and not r["ok"]:
            w = r["worst"]
            print(
                f"      worst @ ts={w['ts']}: parquet={w['parquet']:.6g} "
                f"live={w['live']:.6g} abs={w['abs']:.6g} rel={w['rel']:.2e}"
            )
        for d in r["first_disagreements"][:3]:
            print(
                f"      diff @ ts={d['ts']}: parquet={d['parquet']:.6g} "
                f"live={d['live']:.6g} rel={d['rel']:.2e}"
            )
    print("-" * 72)
    print("PASS" if exit_code == 0 else "FAIL")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
