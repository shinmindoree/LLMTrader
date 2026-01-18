"""백테스트용 컨텍스트."""

from collections.abc import Callable
from typing import Any

from backtest.risk import BacktestRiskManager
from indicators.builtin import compute as compute_builtin_indicator
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
        self._opens: list[float] = []
        self._highs: list[float] = []
        self._lows: list[float] = []
        self._volumes: list[float] = []
        self._indicator_registry: dict[str, Callable[..., Any]] = {}
        self._indicator_error_logged: set[str] = set()
    
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

    def check_stoploss(self) -> bool:
        """StopLoss 조건 확인 후 필요 시 청산.

        Returns:
            StopLoss가 트리거되어 청산을 수행했는지 여부
        """
        if abs(self.position.size) < 1e-12:
            return False
        stop_loss_pct = self.risk_manager.config.stop_loss_pct
        if stop_loss_pct <= 0:
            return False
        entry_balance = float(self.position.entry_balance or 0.0)
        if entry_balance <= 0:
            return False
        current_pnl_pct = float(self.position.unrealized_pnl) / entry_balance
        if current_pnl_pct <= -stop_loss_pct:
            position_type = "Long" if self.position.size > 0 else "Short"
            reason_msg = f"StopLoss {position_type} (PnL {current_pnl_pct * 100:.2f}% of entry balance)"
            self.close_position(reason=reason_msg, exit_reason="STOP_LOSS")
            return True
        return False

    def calc_entry_quantity(self, entry_pct: float | None = None, price: float | None = None) -> float:
        """시스템 설정 기반으로 진입 수량 계산."""
        use_price = float(price if price is not None else self._current_price)
        if use_price <= 0:
            return 0.0
        equity = float(self.total_equity)
        if equity <= 0:
            return 0.0
        max_position = float(self.risk_manager.config.max_position_size)
        max_order = float(self.risk_manager.config.max_order_size)
        pct = float(entry_pct) if entry_pct is not None else min(max_position, max_order)
        if pct <= 0:
            return 0.0
        notional = equity * float(self.leverage) * pct
        qty = notional / use_price
        return max(0.0, qty)

    def enter_long(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        """시스템 리스크 설정 기반으로 롱 진입."""
        if abs(self.position.size) > 1e-12:
            return
        qty = self.calc_entry_quantity(entry_pct=entry_pct)
        if qty <= 0:
            return
        self.buy(qty, reason=reason)

    def enter_short(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        """시스템 리스크 설정 기반으로 숏 진입."""
        if abs(self.position.size) > 1e-12:
            return
        qty = self.calc_entry_quantity(entry_pct=entry_pct)
        if qty <= 0:
            return
        self.sell(qty, reason=reason)
    
    def update_bar(
        self, open_price: float, high_price: float, low_price: float, close_price: float, volume: float = 0.0
    ) -> None:
        """새 캔들이 닫힐 때 호출 (지표 계산용 OHLCV 히스토리 업데이트)."""
        self._opens.append(float(open_price))
        self._highs.append(float(high_price))
        self._lows.append(float(low_price))
        self._closes.append(float(close_price))
        self._volumes.append(float(volume))

        max_len = 500
        if len(self._closes) > max_len:
            self._opens = self._opens[-max_len:]
            self._highs = self._highs[-max_len:]
            self._lows = self._lows[-max_len:]
            self._closes = self._closes[-max_len:]
            self._volumes = self._volumes[-max_len:]

    def _get_builtin_indicator_inputs(self) -> dict[str, list[float]]:
        closes = list(self._closes)
        n = len(closes)
        if len(self._opens) != n or len(self._highs) != n or len(self._lows) != n or len(self._volumes) != n:
            return {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [0.0] * n,
            }
        return {
            "open": list(self._opens),
            "high": list(self._highs),
            "low": list(self._lows),
            "close": closes,
            "volume": list(self._volumes),
        }
    
    def buy(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
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
                "reason": reason or "",
                "exit_reason": exit_reason,
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
            "reason": reason or "",
            "exit_reason": exit_reason,
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
    
    def sell(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
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
                "reason": reason or "",
                "exit_reason": exit_reason,
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
            "reason": reason or "",
            "exit_reason": exit_reason,
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
    
    def close_position(
        self,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        """포지션 전체 청산 (롱/숏 모두 지원)."""
        if self.position.size == 0:
            return
        
        quantity = abs(self.position.size)
        if self.position.size > 0:
            self.sell(quantity, reason=reason, exit_reason=exit_reason)
        else:
            self.buy(quantity, reason=reason, exit_reason=exit_reason)
    
    def close_position_at_price(
        self,
        price: float,
        reason: str | None = None,
        exit_reason: str | None = None,
    ) -> None:
        """포지션 전체 청산 (롱/숏 모두 지원) - 지정 가격으로 체결.
        
        Args:
            price: 청산 가격
            reason: 청산 사유
        """
        if self.position.size == 0:
            return
        
        quantity = abs(self.position.size)
        if self.position.size > 0:
            self.sell(quantity, price=price, reason=reason, exit_reason=exit_reason)
        else:
            self.buy(quantity, price=price, reason=reason, exit_reason=exit_reason)
    
    def register_indicator(self, name: str, func: Callable[..., Any]) -> None:
        """지표 계산 함수 등록."""
        normalized = name.strip()
        if not normalized:
            raise ValueError("indicator name is required")
        if not callable(func):
            raise ValueError(f"indicator '{name}' must be callable")
        self._indicator_registry[normalized.lower()] = func

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """지표 조회.
        
        Args:
            name: 지표 이름
            *args: 위치 인자
            **kwargs: 키워드 인자
        
        Returns:
            지표 값
        """
        normalized = name.strip()
        if not normalized:
            raise ValueError("indicator name is required")

        func = self._indicator_registry.get(normalized.lower())
        if func:
            return func(self, *args, **kwargs)

        if args:
            if len(args) == 1 and "period" not in kwargs and "timeperiod" not in kwargs:
                kwargs["period"] = args[0]
            else:
                raise TypeError("builtin indicator params must be passed as keywords (or single period)")

        return compute_builtin_indicator(
            normalized,
            self._get_builtin_indicator_inputs(),
            **kwargs,
        )

    def get_open_orders(self) -> list[dict[str, Any]]:
        """미체결 주문 목록."""
        return []
