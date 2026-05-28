"""펀딩비 차익거래 엔진 — 현물 롱 + 선물 숏 (Delta-Neutral).

전략 흐름:
  1. 매 10초 펀딩비 조회 (GET /fapi/v1/premiumIndex)
  2. 연환산 펀딩비 > entry_deadband → 진입 (현물 매수 + 선물 숏)
  3. 연환산 펀딩비 < exit_deadband  → 언와인딩 (분할 지정가 청산)
  4. 선물 마진 비율 > margin_alert_ratio → 현물→선물 자동 이체
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from api.schemas import FundingArbitrageParams, FundingArbitrageStatusResponse

_log = logging.getLogger("llmtrader.funding_arb")

# 펀딩비는 8시간마다 → 연 3 × 365 = 1095회
_FUNDING_PERIODS_PER_YEAR = 1095
_POLL_INTERVAL_SEC = 10
_UNWIND_SLICES = 4  # 분할 청산 횟수
_UNWIND_INTERVAL_SEC = 3


@dataclass
class _EngineState:
    running: bool = False
    symbol: str | None = None
    spot_qty: float = 0.0
    futures_short_qty: float = 0.0
    current_funding_rate: float | None = None
    unrealized_pnl: float | None = None
    accumulated_funding_income: float = 0.0
    last_funding_ts: datetime | None = None
    params: FundingArbitrageParams | None = None
    _task: asyncio.Task[None] | None = field(default=None, repr=False, compare=False)


_state = _EngineState()


# ── 퍼블릭 인터페이스 ──────────────────────────────────────


def get_engine_status() -> FundingArbitrageStatusResponse:
    ann = (
        _state.current_funding_rate * _FUNDING_PERIODS_PER_YEAR * 100
        if _state.current_funding_rate is not None
        else None
    )
    return FundingArbitrageStatusResponse(
        running=_state.running,
        symbol=_state.symbol,
        spot_qty=_state.spot_qty if _state.spot_qty else None,
        futures_short_qty=_state.futures_short_qty if _state.futures_short_qty else None,
        current_funding_rate=_state.current_funding_rate,
        annualized_funding_pct=ann,
        unrealized_pnl=_state.unrealized_pnl,
        accumulated_funding_income=_state.accumulated_funding_income,
        last_funding_ts=_state.last_funding_ts,
        params=_state.params,
    )


async def start_engine(
    *,
    params: FundingArbitrageParams,
    api_key: str,
    api_secret: str,
    base_url: str,
) -> None:
    global _state  # noqa: PLW0603
    if _state.running:
        _log.warning("Engine already running — ignoring start request")
        return
    _state = _EngineState(running=True, symbol=params.symbol, params=params)
    _state._task = asyncio.create_task(
        _engine_loop(params=params, api_key=api_key, api_secret=api_secret, base_url=base_url),
        name="funding_arb_engine",
    )
    _log.info("Funding arbitrage engine started: %s", params.symbol)


async def stop_engine() -> None:
    if not _state.running:
        return
    _state.running = False
    if _state._task and not _state._task.done():
        _state._task.cancel()
        try:
            await _state._task
        except asyncio.CancelledError:
            pass
    _log.info("Funding arbitrage engine stopped")


# ── 내부 루프 ──────────────────────────────────────────────


async def _engine_loop(
    *,
    params: FundingArbitrageParams,
    api_key: str,
    api_secret: str,
    base_url: str,
) -> None:
    futures_base = _normalize_base(base_url, kind="futures")
    spot_base = "https://api.binance.com"

    async with (
        httpx.AsyncClient(base_url=futures_base, timeout=10.0) as futures_client,
        httpx.AsyncClient(base_url=spot_base, timeout=10.0) as spot_client,
    ):
        while _state.running:
            try:
                await _tick(
                    params=params,
                    futures_client=futures_client,
                    spot_client=spot_client,
                    api_key=api_key,
                    api_secret=api_secret,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _log.exception("Tick error — will retry in %ds", _POLL_INTERVAL_SEC)
            await asyncio.sleep(_POLL_INTERVAL_SEC)


async def _tick(
    *,
    params: FundingArbitrageParams,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
) -> None:
    # 1. 현재 펀딩비 조회
    funding_rate, mark_price = await _fetch_funding_rate(futures_client, params.symbol)
    _state.current_funding_rate = funding_rate
    ann_pct = funding_rate * _FUNDING_PERIODS_PER_YEAR * 100

    # 2. 마진 비율 감시 → 위험 시 현물→선물 이체
    await _check_margin_and_rebalance(
        futures_client=futures_client,
        spot_client=spot_client,
        api_key=api_key,
        api_secret=api_secret,
        params=params,
        mark_price=mark_price,
    )

    no_position = _state.spot_qty == 0.0 and _state.futures_short_qty == 0.0

    # 3. 진입 조건: 포지션 없음 + 연환산 펀딩비 > entry_deadband
    entry_threshold_ann = params.entry_deadband_pct * _FUNDING_PERIODS_PER_YEAR
    if no_position and ann_pct > entry_threshold_ann:
        _log.info(
            "ENTER signal: annualized=%.2f%% > threshold=%.2f%%",
            ann_pct,
            entry_threshold_ann,
        )
        await _enter_position(
            futures_client=futures_client,
            spot_client=spot_client,
            api_key=api_key,
            api_secret=api_secret,
            params=params,
            mark_price=mark_price,
        )
        return

    # 4. 청산 조건: 포지션 있음 + 연환산 펀딩비 < exit_deadband
    exit_threshold_ann = params.exit_deadband_pct * _FUNDING_PERIODS_PER_YEAR
    if not no_position and ann_pct < exit_threshold_ann:
        _log.info(
            "EXIT signal: annualized=%.2f%% < exit_threshold=%.2f%%",
            ann_pct,
            exit_threshold_ann,
        )
        await _unwind_position(
            futures_client=futures_client,
            spot_client=spot_client,
            api_key=api_key,
            api_secret=api_secret,
            params=params,
        )


async def _fetch_funding_rate(
    client: httpx.AsyncClient,
    symbol: str,
) -> tuple[float, float]:
    resp = await client.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    rate = float(data.get("lastFundingRate") or 0)
    mark_price = float(data.get("markPrice") or 0)
    _state.last_funding_ts = datetime.now(timezone.utc)
    return rate, mark_price


async def _check_margin_and_rebalance(
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    params: FundingArbitrageParams,
    mark_price: float,
) -> None:
    """선물 마진 비율이 위험 수위면 현물→선물 이체로 방어."""
    if _state.futures_short_qty == 0.0 or mark_price <= 0:
        return

    headers = _auth_headers(api_key)
    try:
        resp = await futures_client.get("/fapi/v2/account", headers=headers)
        resp.raise_for_status()
        acc: dict[str, Any] = resp.json()
    except Exception:  # noqa: BLE001
        _log.warning("Failed to fetch futures account for margin check")
        return

    total_margin = float(acc.get("totalMarginBalance") or 0)
    maint_margin = float(acc.get("totalMaintMargin") or 0)
    if total_margin <= 0:
        return

    margin_ratio = maint_margin / total_margin
    if margin_ratio < params.margin_alert_ratio:
        return

    # 이체할 금액: 현물 잔고의 rebalance_transfer_pct
    _log.warning(
        "Margin ratio %.2f >= alert %.2f — auto-rebalancing",
        margin_ratio,
        params.margin_alert_ratio,
    )
    try:
        spot_resp = await spot_client.get(
            "/api/v3/account",
            headers=_auth_headers(api_key),
            params=_signed_params(api_key, api_secret, {}),
        )
        spot_resp.raise_for_status()
        balances: list[dict[str, Any]] = spot_resp.json().get("balances", [])
        usdt_free = next(
            (float(b["free"]) for b in balances if b.get("asset") == "USDT"),
            0.0,
        )
    except Exception:  # noqa: BLE001
        _log.warning("Failed to fetch spot balance for rebalance")
        return

    transfer_amt = usdt_free * params.rebalance_transfer_pct
    if transfer_amt < 1.0:
        return

    try:
        # SPOT → USD-M Futures (type=2)
        await _universal_transfer(
            spot_client=spot_client,
            api_key=api_key,
            api_secret=api_secret,
            transfer_type=2,
            asset="USDT",
            amount=transfer_amt,
        )
        _log.info("Transferred %.2f USDT spot→futures for margin defense", transfer_amt)
    except Exception:  # noqa: BLE001
        _log.exception("Auto-rebalance transfer failed")


async def _enter_position(
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    params: FundingArbitrageParams,
    mark_price: float,
) -> None:
    """현물 시장가 매수 + 선물 시장가 숏."""
    if mark_price <= 0:
        return
    qty = round(params.allocated_usdt / mark_price, 5)
    if qty <= 0:
        return

    # 현물 매수 (시장가)
    try:
        spot_order = await _place_spot_order(
            client=spot_client,
            api_key=api_key,
            api_secret=api_secret,
            symbol=params.symbol,
            side="BUY",
            qty=qty,
        )
        _state.spot_qty = float(spot_order.get("executedQty") or qty)
        _log.info("Spot BUY filled: qty=%.5f", _state.spot_qty)
    except Exception:  # noqa: BLE001
        _log.exception("Spot BUY failed — aborting entry")
        return

    # 선물 숏 (시장가)
    try:
        fut_order = await _place_futures_order(
            client=futures_client,
            api_key=api_key,
            api_secret=api_secret,
            symbol=params.symbol,
            side="SELL",
            qty=_state.spot_qty,
            position_side="SHORT",
        )
        _state.futures_short_qty = float(fut_order.get("executedQty") or _state.spot_qty)
        _log.info("Futures SHORT filled: qty=%.5f", _state.futures_short_qty)
    except Exception:  # noqa: BLE001
        _log.exception("Futures SHORT failed — spot leg orphaned, manual intervention needed")


async def _unwind_position(
    *,
    futures_client: httpx.AsyncClient,
    spot_client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    params: FundingArbitrageParams,
) -> None:
    """분할 지정가 청산 (_UNWIND_SLICES 회)."""
    spot_slice = _state.spot_qty / _UNWIND_SLICES
    fut_slice = _state.futures_short_qty / _UNWIND_SLICES

    for i in range(_UNWIND_SLICES):
        try:
            await _place_spot_order(
                client=spot_client,
                api_key=api_key,
                api_secret=api_secret,
                symbol=params.symbol,
                side="SELL",
                qty=round(spot_slice, 5),
            )
            await _place_futures_order(
                client=futures_client,
                api_key=api_key,
                api_secret=api_secret,
                symbol=params.symbol,
                side="BUY",
                qty=round(fut_slice, 5),
                position_side="SHORT",
            )
            _log.info("Unwind slice %d/%d done", i + 1, _UNWIND_SLICES)
        except Exception:  # noqa: BLE001
            _log.exception("Unwind slice %d failed", i + 1)
        if i < _UNWIND_SLICES - 1:
            await asyncio.sleep(_UNWIND_INTERVAL_SEC)

    _state.spot_qty = 0.0
    _state.futures_short_qty = 0.0
    _log.info("Position fully unwound")


# ── Binance API helpers ────────────────────────────────────


def _normalize_base(url: str, kind: str) -> str:
    if kind == "futures":
        return url if "fapi" in url else "https://fapi.binance.com"
    return "https://api.binance.com"


def _auth_headers(api_key: str) -> dict[str, str]:
    return {"X-MBX-APIKEY": api_key}


def _signed_params(api_key: str, api_secret: str, params: dict[str, Any]) -> dict[str, Any]:
    import hashlib
    import hmac
    import time

    p = {**params, "timestamp": int(time.time() * 1000)}
    query = "&".join(f"{k}={v}" for k, v in p.items())
    sig = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return {**p, "signature": sig}


async def _place_spot_order(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    qty: float,
) -> dict[str, Any]:
    params = _signed_params(api_key, api_secret, {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": f"{qty:.5f}",
    })
    resp = await client.post("/api/v3/order", headers=_auth_headers(api_key), data=params)
    resp.raise_for_status()
    return resp.json()  # type: ignore[return-value]


async def _place_futures_order(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    symbol: str,
    side: str,
    qty: float,
    position_side: str = "BOTH",
) -> dict[str, Any]:
    params = _signed_params(api_key, api_secret, {
        "symbol": symbol,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": f"{qty:.5f}",
    })
    resp = await client.post("/fapi/v1/order", headers=_auth_headers(api_key), data=params)
    resp.raise_for_status()
    return resp.json()  # type: ignore[return-value]


async def _universal_transfer(
    *,
    spot_client: httpx.AsyncClient,
    api_key: str,
    api_secret: str,
    transfer_type: int,
    asset: str,
    amount: float,
) -> None:
    params = _signed_params(api_key, api_secret, {
        "type": transfer_type,
        "asset": asset,
        "amount": f"{amount:.2f}",
    })
    resp = await spot_client.post(
        "/sapi/v1/asset/transfer",
        headers=_auth_headers(api_key),
        data=params,
    )
    resp.raise_for_status()
