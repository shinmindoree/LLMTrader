"""Portfolio context for multi-symbol / multi-timeframe strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from live.context import LiveContext
from live.indicator_context import CandleStreamIndicatorContext
from live.risk import LiveRiskManager


StreamKey = tuple[str, str]  # (symbol, interval)


def _normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper()


def _normalize_interval(interval: str) -> str:
    return interval.strip()


@dataclass(frozen=True)
class CandleStream:
    symbol: str
    interval: str

    @property
    def key(self) -> StreamKey:
        return (_normalize_symbol(self.symbol), _normalize_interval(self.interval))


class _SymbolTradingProxy:
    """Portfolio risk + order routing wrapper around LiveContext."""

    def __init__(self, *, portfolio: "PortfolioContext", symbol: str, ctx: LiveContext) -> None:
        self._portfolio = portfolio
        self.symbol = symbol
        self._ctx = ctx

    @property
    def current_price(self) -> float:
        return self._ctx.current_price

    @property
    def position_size(self) -> float:
        return self._ctx.position_size

    @property
    def position_entry_price(self) -> float:
        return self._ctx.position_entry_price

    @property
    def unrealized_pnl(self) -> float:
        return self._ctx.unrealized_pnl

    @property
    def balance(self) -> float:
        return self._ctx.balance

    @property
    def total_equity(self) -> float:
        # per-symbol view (legacy). For portfolio, use PortfolioContext.portfolio_total_equity.
        return self._ctx.total_equity

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self._ctx.get_open_orders()

    def register_indicator(self, name: str, func: Any) -> None:
        self._portfolio.register_indicator(name, func)

    def get_indicator(self, name: str, *args: Any, **kwargs: Any) -> Any:
        # default to the stream matching this symbol + a configured interval (if any)
        return self._portfolio.get_indicator(name, *args, symbol=self.symbol, interval=None, **kwargs)

    def buy(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self._portfolio._pre_trade_check(symbol=self.symbol, side="BUY", quantity=float(quantity))
        self._ctx.buy(quantity, price=price, reason=reason, exit_reason=exit_reason, use_chase=use_chase)

    def sell(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self._portfolio._pre_trade_check(symbol=self.symbol, side="SELL", quantity=float(quantity))
        self._ctx.sell(quantity, price=price, reason=reason, exit_reason=exit_reason, use_chase=use_chase)

    def close_position(
        self,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        # exits should always be allowed (portfolio risk check bypass)
        self._ctx.close_position(reason=reason, exit_reason=exit_reason, use_chase=use_chase)

    def calc_entry_quantity(self, entry_pct: float | None = None, price: float | None = None) -> float:
        return self._ctx.calc_entry_quantity(entry_pct=entry_pct, price=price)

    def enter_long(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        qty = self.calc_entry_quantity(entry_pct=entry_pct)
        if qty > 0:
            self.buy(qty, reason=reason)

    def enter_short(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        qty = self.calc_entry_quantity(entry_pct=entry_pct)
        if qty > 0:
            self.sell(qty, reason=reason)


class PortfolioContext:
    """멀티 심볼/멀티 타임프레임 전략을 위한 컨텍스트."""

    def __init__(
        self,
        *,
        primary_symbol: str,
        trade_contexts: dict[str, LiveContext],
        stream_contexts: dict[StreamKey, CandleStreamIndicatorContext],
        portfolio_risk_manager: LiveRiskManager | None = None,
        portfolio_multiplier: float | None = None,
    ) -> None:
        self.primary_symbol = _normalize_symbol(primary_symbol)
        self._trade_contexts: dict[str, LiveContext] = {
            _normalize_symbol(sym): ctx for sym, ctx in (trade_contexts or {}).items()
        }
        self._stream_contexts: dict[StreamKey, CandleStreamIndicatorContext] = dict(stream_contexts or {})
        self._portfolio_risk_manager = portfolio_risk_manager
        self._portfolio_multiplier = float(portfolio_multiplier) if portfolio_multiplier is not None else float(
            max(1, len(self._trade_contexts))
        )

        if self.primary_symbol not in self._trade_contexts:
            raise ValueError(f"primary_symbol not in trade_contexts: {self.primary_symbol}")

        self._symbol_proxies: dict[str, _SymbolTradingProxy] = {
            sym: _SymbolTradingProxy(portfolio=self, symbol=sym, ctx=ctx) for sym, ctx in self._trade_contexts.items()
        }

    # ----- Legacy single-symbol compatibility (primary symbol) -----
    @property
    def current_price(self) -> float:
        return self._trade_contexts[self.primary_symbol].current_price

    @property
    def position_size(self) -> float:
        return self._trade_contexts[self.primary_symbol].position_size

    @property
    def position_entry_price(self) -> float:
        return self._trade_contexts[self.primary_symbol].position_entry_price

    @property
    def unrealized_pnl(self) -> float:
        return self._trade_contexts[self.primary_symbol].unrealized_pnl

    @property
    def balance(self) -> float:
        return self._trade_contexts[self.primary_symbol].balance

    def get_open_orders(self) -> list[dict[str, Any]]:
        return self._trade_contexts[self.primary_symbol].get_open_orders()

    def buy(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self.for_symbol(self.primary_symbol).buy(
            quantity, price=price, reason=reason, exit_reason=exit_reason, use_chase=use_chase
        )

    def sell(
        self,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self.for_symbol(self.primary_symbol).sell(
            quantity, price=price, reason=reason, exit_reason=exit_reason, use_chase=use_chase
        )

    def close_position(
        self,
        reason: str | None = None,
        exit_reason: str | None = None,
        use_chase: bool | None = None,
    ) -> None:
        self.for_symbol(self.primary_symbol).close_position(reason=reason, exit_reason=exit_reason, use_chase=use_chase)

    def calc_entry_quantity(self, entry_pct: float | None = None, price: float | None = None) -> float:
        return self._trade_contexts[self.primary_symbol].calc_entry_quantity(entry_pct=entry_pct, price=price)

    def enter_long(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        self.for_symbol(self.primary_symbol).enter_long(reason=reason, entry_pct=entry_pct)

    def enter_short(self, reason: str | None = None, entry_pct: float | None = None) -> None:
        self.for_symbol(self.primary_symbol).enter_short(reason=reason, entry_pct=entry_pct)

    # ----- Multi-symbol API -----
    @property
    def symbols(self) -> list[str]:
        return list(self._trade_contexts.keys())

    def for_symbol(self, symbol: str) -> _SymbolTradingProxy:
        normalized = _normalize_symbol(symbol)
        proxy = self._symbol_proxies.get(normalized)
        if not proxy:
            raise KeyError(f"unknown trade symbol: {symbol}")
        return proxy

    def get_stream(self, symbol: str, interval: str) -> CandleStreamIndicatorContext:
        key = (_normalize_symbol(symbol), _normalize_interval(interval))
        stream = self._stream_contexts.get(key)
        if not stream:
            raise KeyError(f"unknown candle stream: {key[0]}@{key[1]}")
        return stream

    def register_indicator(self, name: str, func: Any, *, symbol: str | None = None, interval: str | None = None) -> None:
        if symbol is not None and interval is not None:
            self.get_stream(symbol, interval).register_indicator(name, func)
            return

        for stream_ctx in self._stream_contexts.values():
            stream_ctx.register_indicator(name, func)
        for trade_ctx in self._trade_contexts.values():
            trade_ctx.register_indicator(name, func)

    def get_indicator(self, name: str, *args: Any, symbol: str | None = None, interval: str | None = None, **kwargs: Any) -> Any:
        if symbol is None and interval is None:
            return self._trade_contexts[self.primary_symbol].get_indicator(name, *args, **kwargs)

        normalized_symbol = self.primary_symbol if symbol is None else _normalize_symbol(symbol)
        if interval is None:
            # 기본: 해당 심볼의 첫 등록 interval(없으면 primary_symbol의 stream)
            for (sym, itv), _ctx in self._stream_contexts.items():
                if sym == normalized_symbol:
                    interval = itv
                    break
            if interval is None:
                for (sym, itv), _ctx in self._stream_contexts.items():
                    if sym == self.primary_symbol:
                        interval = itv
                        break
        if interval is None:
            raise ValueError("interval is required when candle streams are not configured")

        return self.get_stream(normalized_symbol, interval).get_indicator(name, *args, **kwargs)

    # ----- Portfolio risk helpers -----
    def portfolio_total_equity(self) -> float:
        balance = float(self._trade_contexts[self.primary_symbol].balance)
        unrealized = sum(float(ctx.unrealized_pnl) for ctx in self._trade_contexts.values())
        return balance + unrealized

    def _portfolio_position_value(self) -> float:
        total = 0.0
        for ctx in self._trade_contexts.values():
            price = float(ctx.current_price)
            if price <= 0:
                continue
            total += abs(float(ctx.position_size)) * price
        return total

    def _pre_trade_check(self, *, symbol: str, side: str, quantity: float) -> None:
        trade_ctx = self._trade_contexts[_normalize_symbol(symbol)]
        current_price = float(trade_ctx.current_price)
        if current_price <= 0 or quantity <= 0:
            return

        before_pos = float(trade_ctx.position_size)
        after_pos = before_pos + (quantity if side.upper() == "BUY" else -quantity)
        is_reducing = abs(after_pos) < abs(before_pos) - 1e-12
        if is_reducing:
            return

        if self._portfolio_risk_manager is not None:
            can_trade, reason = self._portfolio_risk_manager.can_trade()
            if not can_trade:
                raise ValueError(f"포트폴리오 거래 불가: {reason}")

        total_equity = float(self.portfolio_total_equity())
        leverage = max(float(ctx.leverage) for ctx in self._trade_contexts.values())
        cfg = trade_ctx.risk_manager.config
        multiplier = float(self._portfolio_multiplier)

        # (1) 포트폴리오 주문 크기 제한
        order_value = float(quantity) * current_price
        max_order_value = total_equity * leverage * float(cfg.max_order_size) * multiplier
        if order_value > max_order_value:
            raise ValueError(f"포트폴리오 주문 크기 초과 (최대: ${max_order_value:.2f})")

        # (2) 포트폴리오 총 노출 제한 (sum(|pos|*price))
        before_total = self._portfolio_position_value()
        before_symbol_value = abs(before_pos) * current_price
        after_symbol_value = abs(after_pos) * current_price
        after_total = before_total - before_symbol_value + after_symbol_value

        max_total_value = total_equity * leverage * float(cfg.max_position_size) * multiplier
        if after_total > max_total_value:
            raise ValueError(f"포트폴리오 총 노출 초과 (최대: ${max_total_value:.2f})")
