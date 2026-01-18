"""백테스트 엔진."""

from typing import Any, Callable

from backtest.context import BacktestContext
from strategy.base import Strategy


class BacktestEngine:
    """백테스트 엔진."""
    
    def __init__(
        self,
        strategy: Strategy,
        context: BacktestContext,
        klines: list[list[Any]],
        progress_callback: Callable[[float], None] | None = None,
    ) -> None:
        self.strategy = strategy
        self.ctx = context
        self.klines = klines
        self.results: dict[str, Any] = {}
        self.progress_callback = progress_callback
    
    def run(self) -> dict[str, Any]:
        """백테스트 실행."""
        print(f"🚀 백테스트 시작: {len(self.klines)}개 캔들")
        
        initial_balance = self.ctx.balance
        
        # 전략 초기화
        self.strategy.initialize(self.ctx)
        
        prev_bar_timestamp: int | None = None
        
        for i, kline in enumerate(self.klines):
            open_time = int(kline[0])
            close_time = int(kline[6])
            open_price = float(kline[1])
            high_price = float(kline[2])
            low_price = float(kline[3])
            close_price = float(kline[4])
            volume = float(kline[5])
            
            is_new_bar = prev_bar_timestamp != open_time
            
            position_size_before = self.ctx.position_size
            if abs(position_size_before) > 1e-12:
                # 실시간 PnL 기반 stoploss 체크를 위해 캔들 내부 가격 변동 시뮬레이션
                # 롱 포지션: open -> low -> close 순서로 체크
                # 숏 포지션: open -> high -> close 순서로 체크
                if position_size_before > 0:
                    # Open 가격 체크
                    self.ctx.update_price(open_price, timestamp=open_time)
                    if self.ctx.check_stoploss():
                        prev_bar_timestamp = open_time
                        continue
                    bar_stoploss = {
                        "timestamp": open_time,
                        "bar_timestamp": open_time,
                        "bar_close": close_price,
                        "price": open_price,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume,
                        "is_new_bar": False,
                    }
                    self.strategy.on_bar(self.ctx, bar_stoploss)
                    
                    if abs(self.ctx.position_size) < 1e-12:
                        prev_bar_timestamp = open_time
                        continue
                    
                    # Low 가격 체크 (롱 포지션에서 가장 불리한 가격)
                    if low_price < open_price:
                        self.ctx.update_price(low_price, timestamp=close_time)
                        if self.ctx.check_stoploss():
                            prev_bar_timestamp = open_time
                            continue
                        bar_stoploss = {
                            "timestamp": close_time,
                            "bar_timestamp": open_time,
                            "bar_close": close_price,
                            "price": low_price,
                            "open": open_price,
                            "high": high_price,
                            "low": low_price,
                            "close": close_price,
                            "volume": volume,
                            "is_new_bar": False,
                        }
                        self.strategy.on_bar(self.ctx, bar_stoploss)
                        
                        if abs(self.ctx.position_size) < 1e-12:
                            prev_bar_timestamp = open_time
                            continue
                
                elif position_size_before < 0:
                    # Open 가격 체크
                    self.ctx.update_price(open_price, timestamp=open_time)
                    if self.ctx.check_stoploss():
                        prev_bar_timestamp = open_time
                        continue
                    bar_stoploss = {
                        "timestamp": open_time,
                        "bar_timestamp": open_time,
                        "bar_close": close_price,
                        "price": open_price,
                        "open": open_price,
                        "high": high_price,
                        "low": low_price,
                        "close": close_price,
                        "volume": volume,
                        "is_new_bar": False,
                    }
                    self.strategy.on_bar(self.ctx, bar_stoploss)
                    
                    if abs(self.ctx.position_size) < 1e-12:
                        prev_bar_timestamp = open_time
                        continue
                    
                    # High 가격 체크 (숏 포지션에서 가장 불리한 가격)
                    if high_price > open_price:
                        self.ctx.update_price(high_price, timestamp=close_time)
                        if self.ctx.check_stoploss():
                            prev_bar_timestamp = open_time
                            continue
                        bar_stoploss = {
                            "timestamp": close_time,
                            "bar_timestamp": open_time,
                            "bar_close": close_price,
                            "price": high_price,
                            "open": open_price,
                            "high": high_price,
                            "low": low_price,
                            "close": close_price,
                            "volume": volume,
                            "is_new_bar": False,
                        }
                        self.strategy.on_bar(self.ctx, bar_stoploss)
                        
                        if abs(self.ctx.position_size) < 1e-12:
                            prev_bar_timestamp = open_time
                            continue
            
            self.ctx.update_price(close_price, timestamp=close_time)
            if self.ctx.check_stoploss():
                prev_bar_timestamp = open_time
                continue

            if is_new_bar:
                self.ctx.update_bar(open_price, high_price, low_price, close_price, volume)
            
            bar = {
                "timestamp": close_time,
                "bar_timestamp": open_time,
                "bar_close": close_price,
                "price": close_price,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "is_new_bar": is_new_bar,
            }
            
            self.strategy.on_bar(self.ctx, bar)
            
            prev_bar_timestamp = open_time
            
            progress = (i + 1) / len(self.klines) * 100
            if self.progress_callback:
                self.progress_callback(progress)
            
            if len(self.klines) > 10 and (i + 1) % (len(self.klines) // 10 + 1) == 0:
                print(f"   진행 중... {progress:.1f}%")
        
        self.strategy.finalize(self.ctx)
        
        final_balance = self.ctx.balance
        
        if abs(self.ctx.position_size) > 1e-12:
            self.ctx.close_position(reason="백테스트 종료")
            final_balance = self.ctx.balance
        
        final_equity = final_balance
        total_return = (final_equity / initial_balance - 1) * 100 if initial_balance > 0 else 0
        
        total_pnl = sum(t.get("pnl", 0) for t in self.ctx.trades if t.get("side") == "SELL")
        total_commission = sum(t.get("commission", 0) for t in self.ctx.trades)
        
        self.results = {
            "initial_balance": initial_balance,
            "final_balance": final_equity,
            "total_return_pct": total_return,
            "total_pnl": total_pnl,
            "total_commission": total_commission,
            "net_profit": final_equity - initial_balance,
            "total_trades": len([t for t in self.ctx.trades if t.get("side") == "SELL"]),
            "trades": self.ctx.trades,
        }
        
        print(f"✅ 백테스트 완료")
        print(f"   초기 자산: ${initial_balance:,.2f}")
        print(f"   최종 자산: ${final_equity:,.2f}")
        print(f"   수익률: {total_return:.2f}%")
        print(f"   순손익: ${final_equity - initial_balance:,.2f}")
        print(f"   총 거래 횟수: {self.results['total_trades']}")
        print(f"   총 수수료: ${total_commission:,.2f}")
        
        return self.results
    
    def get_summary(self) -> dict[str, Any]:
        """백테스트 요약 반환."""
        return self.results
