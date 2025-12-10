from fastapi import APIRouter, Depends, HTTPException, status

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.dependencies import get_binance_client
from llmtrader.schemas import (
    CancelOrderRequest,
    CancelOrderResponse,
    ErrorResponse,
    KlinesRequest,
    KlinesResponse,
    PlaceOrderRequest,
    PlaceOrderResponse,
)

router = APIRouter(prefix="/api", tags=["binance"])


@router.post(
    "/order",
    response_model=PlaceOrderResponse,
    responses={400: {"model": ErrorResponse}},
)
async def place_order(
    body: PlaceOrderRequest,
    client: BinanceHTTPClient = Depends(get_binance_client),
) -> PlaceOrderResponse:
    try:
        result = await client.place_order(
            symbol=body.symbol,
            side=body.side,
            quantity=body.quantity,
            type=body.order_type,
            price=body.price,
            timeInForce=body.time_in_force,
            recvWindow=body.recv_window,
            reduceOnly=body.reduce_only,
        )
        return PlaceOrderResponse.model_validate(result)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/order/cancel",
    response_model=CancelOrderResponse,
    responses={400: {"model": ErrorResponse}},
)
async def cancel_order(
    body: CancelOrderRequest,
    client: BinanceHTTPClient = Depends(get_binance_client),
) -> CancelOrderResponse:
    try:
        result = await client.cancel_order(
            symbol=body.symbol,
            order_id=body.order_id,
        )
        return CancelOrderResponse.model_validate(result)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/klines",
    response_model=KlinesResponse,
    responses={400: {"model": ErrorResponse}},
)
async def get_klines(
    body: KlinesRequest,
    client: BinanceHTTPClient = Depends(get_binance_client),
) -> KlinesResponse:
    try:
        data = await client.fetch_klines(
            symbol=body.symbol,
            interval=body.interval,
            start_ts=body.start_ts,
            end_ts=body.end_ts,
            limit=body.limit,
        )
        return KlinesResponse(data=data)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

