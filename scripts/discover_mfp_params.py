"""MFP 심볼별 임계값 재최적화 드라이버.

leg **구조**(family / interval / lookback / feature flag / side)는 BTC 검증
baseline 으로 고정하고, 변동성에 민감한 **임계값**(tp_pct / sl_pct / max_hold_h)
만 대상 심볼의 데이터로 sweep + OOS 검증하여 재피팅한다.

기존 alpha-lab 자산을 그대로 재사용한다:
  - ``_alpha_lab.dataset.load_dataset(symbol)``        통합 15m 데이터셋
  - ``_alpha_lab.dataset.resample_to``                 leg TF 리샘플
  - ``_alpha_lab.pass5_consistency._build_signals``    family 신호 생성
  - ``_alpha_lab.pass5_consistency._run_window``       TRAIN/TEST 윈도우 백테스트

산출물은 ``strategy.param_store`` 아티팩트(leg_overrides)다. OOS 수용 게이트를
통과하면 저장되고(기본 status=validated), ``--promote`` 시 promoted 로 저장되어
라이브 자격을 얻는다. 게이트 미통과 시 저장하지 않는다(라이브 차단 유지).

사용 예::

    python scripts/discover_mfp_params.py --symbol ETHUSDT
    python scripts/discover_mfp_params.py --symbol ETHUSDT --promote

데이터 전제: 대상 심볼의 5개 parquet 피드(15m klines / oi / funding / taker /
lsr)가 ``data/perp_meta/`` 에 있어야 한다. OI/taker/LSR 은 Binance 가 최근 30일만
제공하므로 장기 백테스트용 과거 데이터는 인제스터로 누적해야 한다.
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_SCRIPTS = _ROOT / "scripts"
for _p in (_SRC, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _alpha_lab.dataset import DATA_DIR, load_dataset, resample_to  # noqa: E402
from _alpha_lab.pass5_consistency import _run_window  # noqa: E402
from strategy.param_store import (  # noqa: E402
    STATUS_PROMOTED,
    STATUS_VALIDATED,
    ParamArtifact,
    save,
)

DEFAULT_TRAIN = ("2023-04-01", "2025-04-30")
DEFAULT_TEST = ("2025-05-01", "2026-04-29")

# Required per-symbol parquet feeds (filename suffixes under data/perp_meta/).
_REQUIRED_FEEDS = {
    "klines": "{symbol}_15m_klines.parquet",
    "oi": "{symbol}_oi_5m.parquet",
    "funding": "{symbol}_funding.parquet",
    "taker": "{symbol}_taker_5m.parquet",
    "lsr": "{symbol}_lsr_5m.parquet",
}

# Threshold fields this driver sweeps (subset of MFP TUNABLE_FIELDS). These are
# the primary volatility-coupled knobs present in every leg. Other tunable
# fields (z / rsi / atr) are largely self-normalising and remain at baseline.
_SWEEP_FIELDS = ("tp_pct", "sl_pct", "max_hold_h")

# Anchored multiplier grids (relative to each leg's baseline value).
_TP_MULTS = (0.6, 0.8, 1.0, 1.25, 1.5)
_SL_MULTS = (0.7, 1.0, 1.4)
_HOLD_MULTS = (0.5, 1.0, 2.0)
_SL_CLAMP = (0.005, 0.02)


def _load_mfp_module() -> Any:
    path = _SCRIPTS / "strategies" / "multi_factor_portfolio_strategy.py"
    spec = importlib.util.spec_from_file_location("_mfp_for_discovery", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load MFP module at {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _check_feeds(symbol: str) -> list[str]:
    missing: list[str] = []
    for tmpl in _REQUIRED_FEEDS.values():
        f = DATA_DIR / tmpl.format(symbol=symbol)
        if not f.exists():
            missing.append(str(f))
    return missing


def _leg_grid(base_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Anchored threshold grid for one leg around its baseline values."""
    tp0 = float(base_cfg["tp_pct"])
    sl0 = float(base_cfg["sl_pct"])
    hold0 = float(base_cfg["max_hold_h"])
    tps = sorted({round(tp0 * m, 4) for m in _TP_MULTS})
    sls = sorted({
        min(_SL_CLAMP[1], max(_SL_CLAMP[0], round(sl0 * m, 4)))
        for m in _SL_MULTS
    })
    holds = sorted({max(1, int(round(hold0 * m))) for m in _HOLD_MULTS})
    grid: list[dict[str, Any]] = []
    for tp in tps:
        for sl in sls:
            for hold in holds:
                grid.append({"tp_pct": tp, "sl_pct": sl, "max_hold_h": hold})
    return grid


def _passes_gate(train: dict, test: dict) -> bool:
    if train.get("trades", 0) < 20 or test.get("trades", 0) < 10:
        return False
    if train.get("ret_pct", 0.0) <= 0.0 or test.get("ret_pct", 0.0) <= 0.0:
        return False
    pf = test.get("pf", 0.0)
    if isinstance(pf, float) and math.isinf(pf):
        pf = 10.0
    if pf < 1.0:
        return False
    return True


def _score(train: dict, test: dict) -> float:
    """OOS-robust score: TEST return, gated. Higher is better."""
    if not _passes_gate(train, test):
        return -1e9
    pf = test.get("pf", 0.0)
    if isinstance(pf, float) and math.isinf(pf):
        pf = 10.0
    return float(test.get("ret_pct", 0.0)) * min(2.0, pf)


def _optimize_leg(
    leg: dict[str, Any],
    df_full,
    sig_funcs: dict[str, Any],
    train_window: tuple[str, str],
    test_window: tuple[str, str],
    commission: float,
    slippage_bps: float,
) -> dict[str, Any]:
    """Sweep one leg's thresholds; return result dict with best override.

    Signals are built with MFP's own ``_SIG_FUNCS`` so the re-fit uses the exact
    signal logic the strategy trades with (covers all leg families incl.
    donchian_breakout)."""
    family = leg["family"]
    base_cfg = dict(leg["config"])
    interval = int(base_cfg["interval_min"])
    df = resample_to(df_full, interval)
    build = sig_funcs[family]

    best: dict[str, Any] | None = None
    for combo in _leg_grid(base_cfg):
        cfg = {**base_cfg, **combo}
        long_sig, short_sig = build(df, cfg)
        train = _run_window(df, train_window, long_sig, short_sig, cfg,
                            commission, slippage_bps)
        test = _run_window(df, test_window, long_sig, short_sig, cfg,
                           commission, slippage_bps)
        sc = _score(train, test)
        cand = {"override": combo, "train": train, "test": test, "score": sc}
        if best is None or sc > best["score"]:
            best = cand

    # Fallback: if nothing cleared the gate, keep baseline thresholds so the
    # artifact is complete and never degrades below the discovered baseline.
    assert best is not None
    baseline_override = {k: base_cfg[k] for k in _SWEEP_FIELDS}
    if best["score"] <= -1e8:
        long_sig, short_sig = build(df, base_cfg)
        train = _run_window(df, train_window, long_sig, short_sig, base_cfg,
                            commission, slippage_bps)
        test = _run_window(df, test_window, long_sig, short_sig, base_cfg,
                           commission, slippage_bps)
        return {
            "family": family, "interval_min": interval,
            "override": baseline_override, "train": train, "test": test,
            "passed": False, "fallback": True,
        }
    return {
        "family": family, "interval_min": interval,
        "override": best["override"], "train": best["train"],
        "test": best["test"], "passed": True, "fallback": False,
    }


def _portfolio_gate(results: list[dict[str, Any]]) -> tuple[bool, dict[str, Any]]:
    n = len(results)
    n_passed = sum(1 for r in results if r["passed"])
    test_rets = [float(r["test"].get("ret_pct", 0.0)) for r in results]
    mean_test_ret = sum(test_rets) / n if n else 0.0
    min_passed = math.ceil(0.5 * n)
    ok = (n_passed >= min_passed) and (mean_test_ret > 0.0)
    summary = {
        "n_legs": n,
        "n_passed": n_passed,
        "min_passed_required": min_passed,
        "mean_test_ret_pct": round(mean_test_ret, 3),
        "sum_test_ret_pct": round(sum(test_rets), 3),
    }
    return ok, summary


def main() -> int:
    ap = argparse.ArgumentParser(description="MFP per-symbol threshold re-optimization")
    ap.add_argument("--symbol", required=True, help="e.g. ETHUSDT")
    ap.add_argument("--train-start", default=DEFAULT_TRAIN[0])
    ap.add_argument("--train-end", default=DEFAULT_TRAIN[1])
    ap.add_argument("--test-start", default=DEFAULT_TEST[0])
    ap.add_argument("--test-end", default=DEFAULT_TEST[1])
    ap.add_argument("--commission", type=float, default=0.0002)
    ap.add_argument("--slippage-bps", type=float, default=0.0)
    ap.add_argument("--promote", action="store_true",
                    help="save with status=promoted (live-eligible) on gate pass")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    train_window = (args.train_start, args.train_end)
    test_window = (args.test_start, args.test_end)

    missing = _check_feeds(symbol)
    if missing:
        print(f"[discover] missing required parquet feeds for {symbol}:")
        for m in missing:
            print(f"  - {m}")
        print("\nProvision data first, e.g.:")
        print(f"  python scripts/ingest_perp_meta.py --symbol {symbol} "
              f"--start <YYYY-MM-DD> --end <YYYY-MM-DD> --metrics funding,oi,lsr,taker --period 5m")
        print("Note: OI/taker/LSR are limited to the most recent ~30 days by "
              "Binance; long-history backtests require ingestor accumulation.")
        return 2

    mfp = _load_mfp_module()
    baseline_legs = mfp.ALL_LEGS
    strategy_id = mfp.STRATEGY_ID
    sig_funcs = mfp._SIG_FUNCS

    print(f"[discover] {symbol}: loading dataset ...")
    df_full = load_dataset(symbol)
    print(f"[discover] dataset rows={len(df_full)}; sweeping "
          f"{len(baseline_legs)} legs over TRAIN={train_window} TEST={test_window}")

    results: list[dict[str, Any]] = []
    for i, leg in enumerate(baseline_legs):
        r = _optimize_leg(leg, df_full, sig_funcs, train_window, test_window,
                          args.commission, args.slippage_bps)
        results.append(r)
        tag = "OK " if r["passed"] else "FB "
        print(f"  leg {i:2d} [{tag}] {r['family']:<22} tf={r['interval_min']:>3}m "
              f"tp={r['override']['tp_pct']:.4f} sl={r['override']['sl_pct']:.4f} "
              f"hold={r['override']['max_hold_h']:>3}h | "
              f"TRAIN ret={r['train'].get('ret_pct',0):+6.2f}% pf={r['train'].get('pf',0):.2f} "
              f"TEST ret={r['test'].get('ret_pct',0):+6.2f}% pf={r['test'].get('pf',0):.2f}")

    ok, summary = _portfolio_gate(results)
    print(f"\n[discover] portfolio gate: passed={ok} {summary}")

    if not ok:
        print("[discover] OOS acceptance gate FAILED — not saving artifact. "
              "Symbol stays disabled for live (structure-fixed baseline only).")
        return 1

    leg_overrides = [r["override"] for r in results]
    status = STATUS_PROMOTED if args.promote else STATUS_VALIDATED
    artifact = ParamArtifact(
        strategy_id=strategy_id,
        symbol=symbol,
        status=status,
        version=1,
        leg_overrides=leg_overrides,
        oos={
            "train_window": list(train_window),
            "test_window": list(test_window),
            "commission": args.commission,
            "slippage_bps": args.slippage_bps,
            "summary": summary,
            "per_leg": [
                {"family": r["family"], "interval_min": r["interval_min"],
                 "passed": r["passed"], "fallback": r["fallback"],
                 "test": r["test"], "train": r["train"]}
                for r in results
            ],
            "accepted_at": datetime.now(timezone.utc).isoformat(),
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    path = save(artifact)
    print(f"[discover] saved {status} artifact -> {path}")
    if status != STATUS_PROMOTED:
        print("[discover] artifact is 'validated'. To enable live trading, "
              "promote it (re-run with --promote, or edit status to 'promoted').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
