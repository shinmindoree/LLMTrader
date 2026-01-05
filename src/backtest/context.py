"""백테스트용 컨텍스트."""

from typing import Any

from indicators.rsi import rsi_wilder_from_closes
from backtest.risk import BacktestRiskManager
from strategy.context import StrategyContext


class BacktestPosition:
    """백테스트 포지션."""
    
    def __init__(self) -> None:
        self.size: float = 0.0
        self.entry_price: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.entry_balance: float = 0.0  # 포지션 진입 시점의 balance


class BacktestContext:
    """백테스트 컨텍스트 (StrategyContext 구현)."""
    
    def __init__(
        self,
        symbol: str,
        leverage: int,
        initial_balance: float,
        risk_manager: BacktestRiskManager,
        commission_rate: float = 0.0004,  # taker 수수료 0.04%
    ) -> None:
        self.symbol = symbol
        self.leverage = leverage
        self._balance = initial_balance
        self.risk_manager = risk_manager
        self.commission_rate = commission_rate
        
        self.position = BacktestPosition()
        self._current_price: float = 0.0
        self._price_history: list[float] = []
        self._current_timestamp: int = 0
        
        self.trades: list[dict[str, Any]] = []
        self.orders: list[dict[str, Any]] = []
        self._closes: list[float] = []
    
    @property
    def current_price(self) -> float:
        return self._current_price
    
    @property
    def position_size(self) -> float:
        return self.position.size
    
    @property
    def position_entry_price(self) -> float:
        return self.position.entry_price if self.position.size != 0 else 0.0
    
    @property
    def position_entry_balance(self) -> float:
        """포지션 진입 시점의 balance."""
        return self.position.entry_balance if abs(self.position.size) > 1e-12 else 0.0
    
    @property
    def unrealized_pnl(self) -> float:
        if self.position.size == 0:
            return 0.0
        pnl = (self._current_price - self.position.entry_price) * self.position.size
        return pnl
    
    @property
    def balance(self) -> float:
        return self._balance
    
    @balance.setter
    def balance(self, value: float) -> None:
        self._balance = value
    
    @property
    def total_equity(self) -> float:
        return self.balance + self.unrealized_pnl
    
    def update_price(self, price: float, timestamp: int | None = None) -> None:
        """가격 업데이트 (tick마다 호출).
        
        Args:
            price: 가격
            timestamp: 타임스탬프 (밀리초, 선택사항)
        """
        self._current_price = price
        self._price_history.append(price)
        if len(self._price_history) > 1000:
            self._price_history = self._price_history[-1000:]
        
        if timestamp is not None:
            self._current_timestamp = timestamp
        
        if self.position.size != 0 and self.position.entry_price != 0:
            self.position.unrealized_pnl = (price - self.position.entry_price) * self.position.size
    
    def update_bar(self, close: float) -> None:
        """새 캔들이 닫힐 때 호출 (지표 계산용)."""
        self._closes.append(close)
        if len(self._closes) > 500:
            self._closes = self._closes[-500:]
    
    def buy(self, quantity: float, price: float | None = None, reason: str = "") -> None:
        """매수 주문 (시장가 체결 시뮬레이션).
        
        - 포지션이 없으면: 롱 포지션 진입
        - 롱 포지션이 있으면: 롱 포지션 추가 (평균 진입가 계산)
        - 숏 포지션이 있으면: 숏 포지션 청산
        """
        if price is None:
            price = self._current_price
        
        if price <= 0 or quantity <= 0:
            return
        
        if self.position.size < 0:
            fill_qty = min(quantity, abs(self.position.size))
            
            order_value = fill_qty * price
            commission = order_value * self.commission_rate
            
            pnl = (self.position.entry_price - price) * fill_qty
            self.balance += pnl - commission
            
            self.position.size += fill_qty
            if abs(self.position.size) < 1e-12:
                self.position.size = 0.0
                self.position.entry_price = 0.0
                self.position.entry_balance = 0.0
            
            position_size_usdt = fill_qty * price
            balance_after = self.balance
            self.trades.append({
                "side": "BUY",
                "quantity": fill_qty,
                "price": price,
                "pnl": pnl,
                "commission": commission,
                "reason": reason,
                "timestamp": self._current_timestamp,
                "position_size_usdt": position_size_usdt,
                "entry_price": self.position.entry_price if self.position.size != 0 else price,
                "balance_after": balance_after,
            })
            
            self.orders.append({
                "side": "BUY",
                "quantity": fill_qty,
                "price": price,
            })
            return
        
        valid, msg = self.risk_manager.validate_order_size(
            quantity, price, self.total_equity, self.leverage
        )
        if not valid:
            return
        
        order_value = quantity * price
        commission = order_value * self.commission_rate
        
        if self.position.size == 0:
            self.position.size = quantity
            self.position.entry_price = price
            self.position.entry_balance = self._balance  # 포지션 진입 시점의 balance 저장
        else:
            total_value = self.position.size * self.position.entry_price + quantity * price
            self.position.size += quantity
            self.position.entry_price = total_value / self.position.size
        
        self.balance -= commission
        
        position_size_usdt = quantity * price
        balance_after = self.balance
        self.trades.append({
            "side": "BUY",
            "quantity": quantity,
            "price": price,
            "commission": commission,
            "reason": reason,
            "timestamp": self._current_timestamp,
            "position_size_usdt": position_size_usdt,
            "entry_price": self.position.entry_price,
            "balance_after": balance_after,
        })
        
        self.orders.append({
            "side": "BUY",
            "quantity": quantity,
            "price": price,
        })
    
    def sell(self, quantity: float, price: float | None = None, reason: str = "") -> None:
        """매도 주문 (시장가 체결 시뮬레이션).
        
        - 포지션이 없으면: 숏 포지션 진입
        - 숏 포지션이 있으면: 숏 포지션 추가 (평균 진입가 계산)
        - 롱 포지션이 있으면: 롱 포지션 청산
        """
        if price is None:
            price = self._current_price
        
        if price <= 0 or quantity <= 0:
            return
        
        if self.position.size > 0:
            fill_qty = min(quantity, abs(self.position.size))
            
            order_value = fill_qty * price
            commission = order_value * self.commission_rate
            
            pnl = (price - self.position.entry_price) * fill_qty
            self.balance += pnl - commission
            
            self.position.size -= fill_qty
            if abs(self.position.size) < 1e-12:
                self.position.size = 0.0
                self.position.entry_price = 0.0
                self.position.entry_balance = 0.0
            
            position_size_usdt = fill_qty * price
            balance_after = self.balance
            self.trades.append({
                "side": "SELL",
                "quantity": fill_qty,
                "price": price,
                "pnl": pnl,
                "commission": commission,
                "reason": reason,
                "timestamp": self._current_timestamp,
                "position_size_usdt": position_size_usdt,
                "entry_price": self.position.entry_price if abs(self.position.size) > 1e-12 else price,
                "balance_after": balance_after,
            })
            
            self.orders.append({
                "side": "SELL",
                "quantity": fill_qty,
                "price": price,
            })
            return
        
        valid, msg = self.risk_manager.validate_order_size(
            quantity, price, self.total_equity, self.leverage
        )
        if not valid:
            return
        
        order_value = quantity * price
        commission = order_value * self.commission_rate
        
        if self.position.size == 0:
            self.position.size = -quantity
            self.position.entry_price = price
            self.position.entry_balance = self._balance  # 포지션 진입 시점의 balance 저장
        else:
            total_value = abs(self.position.size) * self.position.entry_price + quantity * price
            self.position.size -= quantity
            self.position.entry_price = total_value / abs(self.position.size)
        
        self.balance -= commission
        
        position_size_usdt = quantity * price
        balance_after = self.balance
        self.trades.append({
            "side": "SELL",
            "quantity": quantity,
            "price": price,
            "commission": commission,
            "reason": reason,
            "timestamp": self._current_timestamp,
            "position_size_usdt": position_size_usdt,
            "entry_price": self.position.entry_price if self.position.size != 0 else price,
            "balance_after": balance_after,
        })
        
        self.orders.append({
            "side": "SELL",
            "quantity": quantity,
            "price": price,
        })
    
    def close_position(self, reason: str = "") -> None:
        """포지션 전체 청산 (롱/숏 모두 지원)."""
        if self.position.size == 0:
            return
        
        quantity = abs(self.position.size)
        if self.position.size > 0:
            self.sell(quantity, reason=reason)
        else:
            self.buy(quantity, reason=reason)
    
    def close_position_at_price(self, price: float, reason: str = "") -> None:
        """포지션 전체 청산 (롱/숏 모두 지원) - 지정 가격으로 체결.
        
        Args:
            price: 청산 가격
            reason: 청산 사유
        """
        if self.position.size == 0:
            return
        
        quantity = abs(self.position.size)
        if self.position.size > 0:
            self.sell(quantity, price=price, reason=reason)
        else:
            self.buy(quantity, price=price, reason=reason)
    
    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회.
        
        Args:
            name: 지표 이름 (예: 'sma', 'rsi', 'ema', 'rsi_rt')
            *args: 위치 인자
            **kwargs: 키워드 인자
        
        Returns:
            지표 값
        """
        if name == "sma":
            period = args[0] if args else kwargs.get("period", 20)
            if len(self._price_history) < period:
                return self._current_price if self._current_price > 0 else 0.0
            return sum(self._price_history[-period:]) / period
        
        elif name == "ema":
            period = args[0] if args else kwargs.get("period", 20)
            if len(self._price_history) < period:
                return self._current_price if self._current_price > 0 else 0.0
            prices = self._price_history[-period:]
            multiplier = 2 / (period + 1)
            ema = prices[0]
            for price in prices[1:]:
                ema = (price - ema) * multiplier + ema
            return ema
        
        elif name == "rsi":
            period = args[0] if args else kwargs.get("period", 14)
            if len(self._closes) < period + 1:
                return 50.0
            return rsi_wilder_from_closes(self._closes, int(period))
        
        elif name == "rsi_rt":
            period = args[0] if args else kwargs.get("period", 14)
            if len(self._closes) < period:
                return 50.0
            closes = list(self._closes) + [float(self._current_price)]
            return rsi_wilder_from_closes(closes, int(period))
        
        return 0.0
    
    def get_open_orders(self) -> list[dict[str, Any]]:
        """미체결 주문 목록."""
        return []
