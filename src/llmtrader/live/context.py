"""라이브 트레이딩 컨텍스트."""

import asyncio
from datetime import datetime
from typing import Any

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.live.risk import RiskManager


class LivePosition:
    """라이브 포지션."""

    def __init__(self) -> None:
        """포지션 초기화."""
        self.size: float = 0.0
        self.entry_price: float = 0.0
        self.unrealized_pnl: float = 0.0


class LiveContext:
    """라이브 트레이딩 컨텍스트."""

    def __init__(
        self,
        client: BinanceHTTPClient,
        risk_manager: RiskManager,
        symbol: str = "BTCUSDT",
        leverage: int = 1,
    ) -> None:
        """컨텍스트 초기화.

        Args:
            client: 바이낸스 클라이언트
            risk_manager: 리스크 관리자
            symbol: 거래 심볼
            leverage: 레버리지
        """
        self.client = client
        self.risk_manager = risk_manager
        self.symbol = symbol
        self.leverage = leverage
        
        self.balance: float = 0.0
        self.position = LivePosition()
        self._current_price: float = 0.0
        self._price_history: list[float] = []
        
        # 주문 추적
        self.pending_orders: dict[int, dict[str, Any]] = {}
        self.filled_orders: list[dict[str, Any]] = []
        
        # 감사 로그
        self.audit_log: list[dict[str, Any]] = []

    @property
    def current_price(self) -> float:
        """현재 가격."""
        return self._current_price

    @property
    def position_size(self) -> float:
        """현재 포지션 크기."""
        return self.position.size

    @property
    def position_entry_price(self) -> float:
        """현재 포지션 진입가 (포지션 없으면 0)."""
        return self.position.entry_price if self.position.size != 0 else 0.0

    @property
    def unrealized_pnl(self) -> float:
        """미실현 손익."""
        return self.position.unrealized_pnl

    @property
    def total_equity(self) -> float:
        """총 자산."""
        return self.balance + self.unrealized_pnl

    async def initialize(self) -> None:
        """컨텍스트 초기화 (레버리지 설정, 잔고 조회)."""
        # 레버리지 검증
        valid, msg = self.risk_manager.validate_leverage(self.leverage)
        if not valid:
            raise ValueError(f"레버리지 검증 실패: {msg}")
        
        # 레버리지 설정 (바이낸스 API)
        try:
            await self.client._signed_request(
                "POST",
                "/fapi/v1/leverage",
                {"symbol": self.symbol, "leverage": self.leverage}
            )
            self._log_audit("LEVERAGE_SET", {"leverage": self.leverage})
        except Exception as e:
            self._log_audit("LEVERAGE_SET_FAILED", {"error": str(e)})
            raise

        # 계좌 잔고 조회
        await self.update_account_info()

    async def update_account_info(self) -> None:
        """계좌 정보 업데이트."""
        try:
            account = await self.client._signed_request("GET", "/fapi/v2/account", {})
            self.balance = float(account.get("availableBalance", 0))
            
            # 포지션 정보 업데이트
            positions = account.get("positions", [])
            for pos in positions:
                if pos["symbol"] == self.symbol:
                    self.position.size = float(pos["positionAmt"])
                    self.position.entry_price = float(pos["entryPrice"]) if self.position.size != 0 else 0.0
                    self.position.unrealized_pnl = float(pos["unrealizedProfit"])
                    break
        except Exception as e:
            self._log_audit("ACCOUNT_UPDATE_FAILED", {"error": str(e)})
            raise

    def buy(self, quantity: float, price: float | None = None) -> None:
        """매수 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가)
        """
        # 비동기 주문을 태스크로 스케줄링
        task = asyncio.create_task(self._place_order("BUY", quantity, price))
        # 태스크 완료를 기다리지 않고 백그라운드에서 실행
        task.add_done_callback(self._handle_order_result)

    def sell(self, quantity: float, price: float | None = None) -> None:
        """매도 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가)
        """
        # 비동기 주문을 태스크로 스케줄링
        task = asyncio.create_task(self._place_order("SELL", quantity, price))
        # 태스크 완료를 기다리지 않고 백그라운드에서 실행
        task.add_done_callback(self._handle_order_result)

    def close_position(self) -> None:
        """현재 포지션 전체 청산."""
        if self.position.size == 0:
            return
        
        if self.position.size > 0:
            self.sell(abs(self.position.size))
        else:
            self.buy(abs(self.position.size))
    
    def _handle_order_result(self, task: asyncio.Task) -> None:
        """주문 결과 처리 콜백.
        
        Args:
            task: 완료된 주문 태스크
        """
        try:
            result = task.result()
            print(f"✅ 주문 체결: {result.get('orderId', 'N/A')}")
        except Exception as e:
            print(f"❌ 주문 실패: {e}")

    async def _place_order(
        self,
        side: str,
        quantity: float,
        price: float | None = None,
    ) -> dict[str, Any]:
        """주문 실행.

        Args:
            side: BUY/SELL
            quantity: 수량
            price: 가격 (None이면 시장가)

        Returns:
            주문 응답
        """
        # 거래 가능 여부 확인
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            error_msg = f"거래 불가: {reason}"
            self._log_audit("ORDER_REJECTED_RISK", {"side": side, "quantity": quantity, "reason": reason})
            raise ValueError(error_msg)

        # 주문 크기 검증
        valid, msg = self.risk_manager.validate_order_size(
            quantity, self._current_price, self.total_equity
        )
        if not valid:
            self._log_audit("ORDER_REJECTED_SIZE", {"side": side, "quantity": quantity, "reason": msg})
            raise ValueError(f"주문 크기 검증 실패: {msg}")

        # 새 포지션 크기 계산 및 검증
        new_position_size = self.position.size + (quantity if side == "BUY" else -quantity)
        valid, msg = self.risk_manager.validate_position_size(
            new_position_size, self._current_price, self.total_equity
        )
        if not valid:
            self._log_audit("ORDER_REJECTED_POSITION", {"side": side, "quantity": quantity, "reason": msg})
            raise ValueError(f"포지션 크기 검증 실패: {msg}")

        # 주문 실행
        order_type = "MARKET" if price is None else "LIMIT"
        try:
            order_params: dict[str, Any] = {"type": order_type}
            if price is not None:
                order_params["price"] = price
                order_params["timeInForce"] = "GTC"

            response = await self.client.place_order(
                symbol=self.symbol,
                side=side,
                quantity=quantity,
                **order_params,
            )

            self._log_audit("ORDER_PLACED", {
                "order_id": response.get("orderId"),
                "side": side,
                "quantity": quantity,
                "type": order_type,
                "price": price,
                "response": response,
            })

            # 주문 추적
            order_id = response.get("orderId")
            if order_id:
                self.pending_orders[order_id] = {
                    "order_id": order_id,
                    "side": side,
                    "quantity": quantity,
                    "type": order_type,
                    "price": price,
                    "timestamp": datetime.now().isoformat(),
                }

            # 계좌 정보 업데이트 (백그라운드)
            asyncio.create_task(self.update_account_info())

            return response

        except Exception as e:
            self._log_audit("ORDER_FAILED", {
                "side": side,
                "quantity": quantity,
                "error": str(e),
            })
            raise

    def cancel_order(self, order_id: int) -> None:
        """주문 취소.

        Args:
            order_id: 주문 ID
        """
        # 비동기 취소를 태스크로 스케줄링
        task = asyncio.create_task(self._cancel_order_async(order_id))
        task.add_done_callback(self._handle_cancel_result)
    
    async def _cancel_order_async(self, order_id: int) -> dict[str, Any]:
        """주문 취소 (비동기 내부 구현).

        Args:
            order_id: 주문 ID

        Returns:
            취소 응답
        """
        try:
            response = await self.client.cancel_order(self.symbol, order_id)
            
            self._log_audit("ORDER_CANCELLED", {
                "order_id": order_id,
                "response": response,
            })

            # 대기 주문에서 제거
            if order_id in self.pending_orders:
                del self.pending_orders[order_id]

            return response

        except Exception as e:
            self._log_audit("ORDER_CANCEL_FAILED", {
                "order_id": order_id,
                "error": str(e),
            })
            raise
    
    def _handle_cancel_result(self, task: asyncio.Task) -> None:
        """주문 취소 결과 처리 콜백.
        
        Args:
            task: 완료된 취소 태스크
        """
        try:
            result = task.result()
            print(f"✅ 주문 취소: {result.get('orderId', 'N/A')}")
        except Exception as e:
            print(f"❌ 주문 취소 실패: {e}")

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회.

        Args:
            name: 지표 이름 (예: 'sma', 'rsi', 'ema')
            *args: 위치 인자
            **kwargs: 키워드 인자

        Returns:
            지표 값
        """
        if name == "sma":
            period = args[0] if args else kwargs.get("period", 20)
            if len(self._price_history) < period:
                return self._current_price
            return sum(self._price_history[-period:]) / period

        elif name == "ema":
            period = args[0] if args else kwargs.get("period", 20)
            if len(self._price_history) < period:
                return self._current_price
            prices = self._price_history[-period:]
            multiplier = 2 / (period + 1)
            ema = prices[0]
            for price in prices[1:]:
                ema = (price - ema) * multiplier + ema
            return ema

        elif name == "rsi":
            period = args[0] if args else kwargs.get("period", 14)
            if len(self._price_history) < period + 1:
                return 50.0

            prices = self._price_history[-(period + 1):]
            gains = []
            losses = []

            for i in range(1, len(prices)):
                change = prices[i] - prices[i - 1]
                if change > 0:
                    gains.append(change)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(abs(change))

            avg_gain = sum(gains) / period if gains else 0
            avg_loss = sum(losses) / period if losses else 0

            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return rsi

        elif name == "rsi_rt":
            period = args[0] if args else kwargs.get("period", 14)
            if len(self._price_history) < period:
                return 50.0
            closes = self._price_history + [self._current_price]
            if len(closes) < period + 1:
                return 50.0
            prices = closes[-(period + 1) :]
            gains = []
            losses = []
            for i in range(1, len(prices)):
                change = prices[i] - prices[i - 1]
                if change > 0:
                    gains.append(change)
                    losses.append(0)
                else:
                    gains.append(0)
                    losses.append(abs(change))
            avg_gain = sum(gains) / period if gains else 0
            avg_loss = sum(losses) / period if losses else 0
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))

        return 0.0

    def update_price(self, price: float) -> None:
        """현재 가격 업데이트.

        Args:
            price: 새 가격
        """
        self._current_price = price
        self._price_history.append(price)
        if len(self._price_history) > 1000:
            self._price_history = self._price_history[-1000:]

        # 미실현 손익 업데이트
        if self.position.size != 0 and self.position.entry_price != 0:
            self.position.unrealized_pnl = self.position.size * (price - self.position.entry_price)

    def mark_price(self, price: float) -> None:
        """현재가(Last/Mark) 업데이트만 수행 (지표용 price_history는 건드리지 않음)."""
        self._current_price = price
        if self.position.size != 0 and self.position.entry_price != 0:
            self.position.unrealized_pnl = self.position.size * (price - self.position.entry_price)

    def _log_audit(self, action: str, data: dict[str, Any]) -> None:
        """감사 로그 기록.

        Args:
            action: 액션 타입
            data: 로그 데이터
        """
        self.audit_log.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "data": data,
        })

