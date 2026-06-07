"""Binance Options 체인 스모크 스크립트 (Phase 0 PoC).

실행 예::

    # 테스트넷에서 BTCUSDT 가장 가까운 만기의 ATM 부근 옵션 체인 출력
    uv run python scripts/check_options_chain.py

    # 메인넷에서 ETHUSDT 옵션 체인 출력
    uv run python scripts/check_options_chain.py --env mainnet --underlying ETHUSDT

    # 만기 인덱스(0 = 가장 가까운 만기, 1 = 다음 만기, ...) 와 폭(strike count)
    uv run python scripts/check_options_chain.py --expiry-index 1 --strikes 7

목적:
    1. ``BinanceOptionsClient`` 가 mainnet/testnet 모두에서 정상 동작하는지 확인.
    2. 옵션 체인 표기(Strike × Call/Put) UI 와이어프레임의 기초 데이터 형태 확인.
    3. Greeks (Delta/Theta/Vega) 및 IV 표기가 의미 있는 값으로 들어오는지 확인.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from binance.options_client import (  # noqa: E402
    BinanceOptionsClient,
    BinanceOptionsClientError,
)
from options.symbol import OptionSide, parse_option_symbol  # noqa: E402

app = typer.Typer(add_completion=False)


@dataclass
class _ChainRow:
    """단일 행사가에 대응하는 콜/풋 마크 데이터."""

    strike: int
    call: dict[str, Any] | None = None
    put: dict[str, Any] | None = None

    def best_iv(self) -> float | None:
        for src in (self.call, self.put):
            if src is None:
                continue
            iv = src.get("markIV")
            try:
                if iv is not None:
                    return float(iv)
            except (TypeError, ValueError):
                continue
        return None


def _format_num(value: Any, fmt: str = "{:.4f}", missing: str = "-") -> str:
    try:
        if value is None or value == "":
            return missing
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return missing


def _format_int(value: Any, missing: str = "-") -> str:
    try:
        if value is None or value == "":
            return missing
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return missing


def _pick_expiry(
    symbols_meta: list[dict[str, Any]], underlying: str, expiry_index: int
) -> tuple[int, str]:
    """``underlying`` 의 만기 중 ``expiry_index`` 번째(0=가장 가까운) 만기를 선택.

    ``status == "TRADING"`` 인 심볼만 후보로 본다.

    Returns:
        ``(expiry_ms, expiry_label)``
    """
    targets = [
        s
        for s in symbols_meta
        if s.get("underlying") == underlying and s.get("status") == "TRADING"
    ]
    if not targets:
        raise typer.BadParameter(
            f"No tradable option symbols found for underlying={underlying!r}"
        )
    expiries = sorted({int(s["expiryDate"]) for s in targets if s.get("expiryDate")})
    if not expiries:
        raise typer.BadParameter(f"No expiry timestamps for {underlying!r}")
    if expiry_index < 0 or expiry_index >= len(expiries):
        raise typer.BadParameter(
            f"expiry_index out of range (0..{len(expiries) - 1}): {expiry_index}"
        )
    ts = expiries[expiry_index]
    label = datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    return ts, label


def _select_chain_symbols(
    symbols_meta: list[dict[str, Any]],
    *,
    underlying: str,
    expiry_ms: int,
    index_price: float,
    strikes: int,
) -> list[str]:
    """주어진 만기에서 인덱스 가격 주변 ``strikes`` 개의 행사가에 해당하는 심볼들 반환."""
    bucket: dict[int, dict[OptionSide, str]] = defaultdict(dict)
    for s in symbols_meta:
        if s.get("underlying") != underlying:
            continue
        if s.get("status") != "TRADING":
            continue
        if int(s.get("expiryDate", 0)) != expiry_ms:
            continue
        sym = s.get("symbol")
        if not isinstance(sym, str):
            continue
        try:
            parsed = parse_option_symbol(sym)
        except ValueError:
            continue
        bucket[parsed.strike][parsed.side] = sym

    if not bucket:
        return []

    sorted_strikes = sorted(bucket.keys())
    nearest_idx = min(
        range(len(sorted_strikes)),
        key=lambda i: abs(sorted_strikes[i] - index_price),
    )
    half = strikes // 2
    lo = max(0, nearest_idx - half)
    hi = min(len(sorted_strikes), lo + strikes)
    lo = max(0, hi - strikes)
    selected_strikes = sorted_strikes[lo:hi]

    selected_symbols: list[str] = []
    for k in selected_strikes:
        for sym in bucket[k].values():
            selected_symbols.append(sym)
    return selected_symbols


def _print_chain(
    *,
    underlying: str,
    env_label: str,
    expiry_label: str,
    index_price: float,
    rows: list[_ChainRow],
) -> None:
    print()
    print(f"📊 Binance Options Chain — {underlying}  [{env_label}]")
    print(f"   Expiry  : {expiry_label}")
    print(f"   Index   : {index_price:,.2f}")
    print()
    header = (
        f"{'Call Δ':>8} {'Call IV':>8} {'Call Mark':>10} {'Call Bid':>9} {'Call Ask':>9} "
        f"│ {'Strike':>9} │ "
        f"{'Put Bid':>9} {'Put Ask':>9} {'Put Mark':>10} {'Put IV':>8} {'Put Δ':>8}"
    )
    print(header)
    print("─" * len(header))

    for row in rows:
        call = row.call or {}
        put = row.put or {}
        line = (
            f"{_format_num(call.get('delta'), '{:+.3f}'):>8} "
            f"{_format_num(call.get('markIV'), '{:.3f}'):>8} "
            f"{_format_num(call.get('markPrice'), '{:.2f}'):>10} "
            f"{_format_num(call.get('bidPrice'), '{:.2f}'):>9} "
            f"{_format_num(call.get('askPrice'), '{:.2f}'):>9} "
            f"│ {row.strike:>9,} │ "
            f"{_format_num(put.get('bidPrice'), '{:.2f}'):>9} "
            f"{_format_num(put.get('askPrice'), '{:.2f}'):>9} "
            f"{_format_num(put.get('markPrice'), '{:.2f}'):>10} "
            f"{_format_num(put.get('markIV'), '{:.3f}'):>8} "
            f"{_format_num(put.get('delta'), '{:+.3f}'):>8}"
        )
        print(line)
    print()
    print(f"   rows={len(rows)}  legend: Δ=delta, IV=mark IV (annualized), prices in USDT")


async def _run(
    *,
    env: str,
    underlying: str,
    expiry_index: int,
    strikes: int,
) -> int:
    client = BinanceOptionsClient(env=env)
    env_label = f"{env.lower()} @ {client.base_url}"
    try:
        try:
            await client.ping()
        except BinanceOptionsClientError as exc:
            print(f"❌ ping failed: {exc}", file=sys.stderr)
            return 2

        info = await client.fetch_exchange_info()
        symbols_meta = info.get("optionSymbols") or []
        if not isinstance(symbols_meta, list) or not symbols_meta:
            print("❌ exchangeInfo returned no optionSymbols", file=sys.stderr)
            return 3

        expiry_ms, expiry_label = _pick_expiry(symbols_meta, underlying, expiry_index)
        index_price = await client.fetch_index_price(underlying)

        symbols = _select_chain_symbols(
            symbols_meta,
            underlying=underlying,
            expiry_ms=expiry_ms,
            index_price=index_price,
            strikes=strikes,
        )
        if not symbols:
            print(
                f"❌ No option symbols around index={index_price:.2f} "
                f"for {underlying} expiry={expiry_label}",
                file=sys.stderr,
            )
            return 4

        marks_all = await client.fetch_mark()
        tickers_all = await client.fetch_ticker()

        wanted = set(symbols)
        mark_map = {m.get("symbol"): m for m in marks_all if m.get("symbol") in wanted}
        ticker_map = {t.get("symbol"): t for t in tickers_all if t.get("symbol") in wanted}

        rows_by_strike: dict[int, _ChainRow] = {}
        for sym in symbols:
            try:
                parsed = parse_option_symbol(sym)
            except ValueError:
                continue
            mark = mark_map.get(sym) or {}
            ticker = ticker_map.get(sym) or {}
            merged = {
                **mark,
                "bidPrice": ticker.get("bidPrice"),
                "askPrice": ticker.get("askPrice"),
            }
            row = rows_by_strike.setdefault(parsed.strike, _ChainRow(strike=parsed.strike))
            if parsed.side is OptionSide.CALL:
                row.call = merged
            else:
                row.put = merged

        rows = [rows_by_strike[k] for k in sorted(rows_by_strike.keys())]
        _print_chain(
            underlying=underlying,
            env_label=env_label,
            expiry_label=expiry_label,
            index_price=index_price,
            rows=rows,
        )
        return 0
    finally:
        await client.aclose()


@app.command()
def main(
    env: str = typer.Option(
        "testnet",
        "--env",
        "-e",
        help="대상 환경: testnet | mainnet",
    ),
    underlying: str = typer.Option(
        "BTCUSDT",
        "--underlying",
        "-u",
        help="기초자산. 테스트넷은 BTCUSDT만 제공된다.",
    ),
    expiry_index: int = typer.Option(
        0,
        "--expiry-index",
        "-i",
        help="만기 인덱스. 0 = 가장 가까운 만기.",
    ),
    strikes: int = typer.Option(
        9,
        "--strikes",
        "-s",
        help="ATM 주변으로 출력할 행사가 개수(홀수 권장).",
    ),
) -> None:
    rc = asyncio.run(
        _run(
            env=env,
            underlying=underlying.upper(),
            expiry_index=expiry_index,
            strikes=strikes,
        )
    )
    raise typer.Exit(code=rc)


if __name__ == "__main__":
    app()
