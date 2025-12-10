"""페이퍼 트레이딩 컨텍스트."""

from typing import Any


class PaperPosition:
    """페이퍼 포지션."""

    def __init__(self) -> None:
        """포지션 초기화."""
        self.size: float = 0.0
        self.entry_price: float = 0.0


class PaperOrder:
    """페이퍼 주문."""

    def __init__(
        self,
        order_id: int,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
    ) -> None:
        """주문 초기화."""
        self.order_id = order_id
        self.symbol = symbol
        self.side = side  # BUY/SELL
        self.quantity = quantity
        self.order_type = order_type  # MARKET/LIMIT
        self.price = price
        self.status = "NEW"  # NEW, FILLED, CANCELLED
        self.filled_qty: float = 0.0
        self.filled_price: float = 0.0


class PaperContext:
    """페이퍼 트레이딩 컨텍스트."""

    def __init__(
        self,
        initial_balance: float,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        slippage: float = 0.0001,
    ) -> None:
        """컨텍스트 초기화.

        Args:
            initial_balance: 초기 잔고
            maker_fee: 메이커 수수료율
            taker_fee: 테이커 수수료율
            slippage: 슬리피지율
        """
        self.balance = initial_balance
        self.initial_balance = initial_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage = slippage
        self.position = PaperPosition()
        self._current_price: float = 0.0
        self._price_history: list[float] = []
        self._order_id_counter = 1
        self.open_orders: dict[int, PaperOrder] = {}
        self.filled_orders: list[PaperOrder] = []

    @property
    def current_price(self) -> float:
        """현재 가격."""
        return self._current_price

    @property
    def position_size(self) -> float:
        """현재 포지션 크기."""
        return self.position.size

    @property
    def unrealized_pnl(self) -> float:
        """미실현 손익."""
        if self.position.size == 0:
            return 0.0
        return self.position.size * (self._current_price - self.position.entry_price)

    def buy(self, quantity: float, price: float | None = None) -> None:
        """매수 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가)
        """
        order_type = "MARKET" if price is None else "LIMIT"
        order = PaperOrder(
            order_id=self._order_id_counter,
            symbol="BTCUSDT",  # 임시 하드코딩
            side="BUY",
            quantity=quantity,
            order_type=order_type,
            price=price,
        )
        self._order_id_counter += 1

        if order_type == "MARKET":
            self._execute_order(order)
        else:
            self.open_orders[order.order_id] = order

    def sell(self, quantity: float, price: float | None = None) -> None:
        """매도 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가)
        """
        order_type = "MARKET" if price is None else "LIMIT"
        order = PaperOrder(
            order_id=self._order_id_counter,
            symbol="BTCUSDT",
            side="SELL",
            quantity=quantity,
            order_type=order_type,
            price=price,
        )
        self._order_id_counter += 1

        if order_type == "MARKET":
            self._execute_order(order)
        else:
            self.open_orders[order.order_id] = order

    def close_position(self) -> None:
        """현재 포지션 전체 청산."""
        if self.position.size > 0:
            self.sell(self.position.size)
        elif self.position.size < 0:
            self.buy(abs(self.position.size))

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회 (단순 이동평균만 지원).

        Args:
            name: 지표 이름
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
        return 0.0

    def update_price(self, price: float) -> None:
        """현재 가격 업데이트 및 대기 주문 체결 확인.

        Args:
            price: 새 가격
        """
        self._current_price = price
        self._price_history.append(price)
        if len(self._price_history) > 1000:
            self._price_history = self._price_history[-1000:]

        # 대기 주문 체결 확인
        to_fill = []
        for order_id, order in self.open_orders.items():
            if self._should_fill(order, price):
                to_fill.append(order_id)

        for order_id in to_fill:
            order = self.open_orders.pop(order_id)
            self._execute_order(order)

    def _should_fill(self, order: PaperOrder, current_price: float) -> bool:
        """지정가 주문 체결 여부 확인.

        Args:
            order: 주문
            current_price: 현재 가격

        Returns:
            체결 여부
        """
        if order.order_type != "LIMIT" or order.price is None:
            return False

        if order.side == "BUY":
            return current_price <= order.price
        return current_price >= order.price

    def _execute_order(self, order: PaperOrder) -> None:
        """주문 체결.

        Args:
            order: 주문
        """
        exec_price = self._current_price if order.order_type == "MARKET" else (order.price or self._current_price)

        # 슬리피지 반영
        if order.side == "BUY":
            exec_price *= 1 + self.slippage
        else:
            exec_price *= 1 - self.slippage

        quantity = order.quantity
        cost = quantity * exec_price
        fee = cost * self.taker_fee

        # 포지션 업데이트
        if order.side == "BUY":
            if self.position.size <= 0:
                # 숏 청산 후 롱 진입
                if self.position.size < 0:
                    realized_pnl = -self.position.size * (self.position.entry_price - exec_price)
                    self.balance += realized_pnl
                self.position.size = quantity
                self.position.entry_price = exec_price
                self.balance -= cost + fee
            else:
                # 롱 확대
                total_cost = self.position.size * self.position.entry_price + cost
                self.position.size += quantity
                self.position.entry_price = total_cost / self.position.size
                self.balance -= cost + fee
        else:  # SELL
            if self.position.size > 0:
                # 롱 청산
                if quantity >= self.position.size:
                    realized_pnl = self.position.size * (exec_price - self.position.entry_price)
                    self.balance += realized_pnl + self.position.size * exec_price - fee
                    self.position.size = 0
                    self.position.entry_price = 0
                else:
                    realized_pnl = quantity * (exec_price - self.position.entry_price)
                    self.balance += realized_pnl + cost - fee
                    self.position.size -= quantity
            else:
                # 숏 진입/확대
                self.position.size -= quantity
                if self.position.entry_price == 0:
                    self.position.entry_price = exec_price
                else:
                    total_proceeds = -self.position.size * self.position.entry_price + cost
                    self.position.entry_price = total_proceeds / abs(self.position.size)
                self.balance += cost - fee

        # 주문 완료
        order.status = "FILLED"
        order.filled_qty = quantity
        order.filled_price = exec_price
        self.filled_orders.append(order)




