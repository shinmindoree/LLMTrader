from typing import Literal, Optional

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str


class PlaceOrderRequest(BaseModel):
    symbol: str = Field(default="BTCUSDT")
    side: Literal["BUY", "SELL"]
    order_type: Literal["MARKET", "LIMIT"] = Field(alias="type", default="MARKET")
    quantity: float
    price: Optional[float] = None
    time_in_force: Optional[str] = Field(default="GTC", alias="timeInForce")
    recv_window: int = Field(default=10_000, alias="recvWindow")
    reduce_only: Optional[bool] = Field(default=None, alias="reduceOnly")

    class Config:
        populate_by_name = True


class PlaceOrderResponse(BaseModel):
    orderId: int
    symbol: str
    status: str
    side: str
    type: str
    origQty: str
    executedQty: str
    price: str


class CancelOrderRequest(BaseModel):
    symbol: str
    order_id: int = Field(alias="orderId")

    class Config:
        populate_by_name = True


class CancelOrderResponse(BaseModel):
    orderId: int
    symbol: str
    status: str
    side: str
    type: str
    origQty: str
    executedQty: str
    price: str


class KlinesRequest(BaseModel):
    symbol: str
    interval: str
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    limit: int = 500


class KlinesResponse(BaseModel):
    data: list[list]




