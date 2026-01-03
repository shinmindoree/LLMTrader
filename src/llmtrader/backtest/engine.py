"""백테스트 엔진."""

from typing import Any, Callable

from llmtrader.backtest.context import BacktestContext
from llmtrader.strategy.base import Strategy


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
        
        # 각 캔들에 대해 전략 실행
        for i, kline in enumerate(self.klines):
            open_time = int(kline[0])
            close_time = int(kline[6])
            open_price = float(kline[1])
            high_price = float(kline[2])
            low_price = float(kline[3])
            close_price = float(kline[4])
            volume = float(kline[5])
            
            # 새 봉인지 확인
            is_new_bar = prev_bar_timestamp != open_time
            
            # 새 봉이 시작될 때 이전 봉의 종가로 지표 업데이트
            # 중요: 지표는 "닫힌 봉"의 종가만 사용해야 함
            if is_new_bar:
                if prev_bar_timestamp is not None and i > 0:
                    # 이전 봉이 닫힌 후 지표 업데이트
                    prev_close = float(self.klines[i-1][4])
                    self.ctx.update_bar(prev_close)
                # 첫 번째 캔들은 이전 봉이 없으므로 지표 업데이트하지 않음
            
            # 포지션이 있는 경우, 캔들 내부의 high/low로 StopLoss 체크
            position_size_before = self.ctx.position_size
            if abs(position_size_before) > 1e-12:
                # 전략의 StopLoss 로직을 그대로 사용하되, low/high 가격으로 체크
                # 롱 포지션: low 가격으로 StopLoss 체크
                if position_size_before > 0:
                    # low 가격으로 가격 업데이트 및 StopLoss 체크
                    self.ctx.update_price(low_price, timestamp=close_time)
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
                        "is_new_bar": False,  # StopLoss 체크만 하고 RSI 계산은 건너뜀
                    }
                    self.strategy.on_bar(self.ctx, bar_stoploss)
                    
                    # StopLoss가 발생했는지 확인 (포지션이 청산되었는지)
                    if abs(self.ctx.position_size) < 1e-12:
                        # StopLoss로 청산됨, 다음 캔들로 진행
                        prev_bar_timestamp = open_time
                        continue
                
                # 숏 포지션: high 가격으로 StopLoss 체크
                elif position_size_before < 0:
                    # high 가격으로 가격 업데이트 및 StopLoss 체크
                    self.ctx.update_price(high_price, timestamp=close_time)
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
                        "is_new_bar": False,  # StopLoss 체크만 하고 RSI 계산은 건너뜀
                    }
                    self.strategy.on_bar(self.ctx, bar_stoploss)
                    
                    # StopLoss가 발생했는지 확인 (포지션이 청산되었는지)
                    if abs(self.ctx.position_size) < 1e-12:
                        # StopLoss로 청산됨, 다음 캔들로 진행
                        prev_bar_timestamp = open_time
                        continue
            
            # StopLoss가 발생하지 않았거나 포지션이 없는 경우, 종가로 일반 로직 진행
            # 가격 업데이트 (현재가 = 종가, 타임스탬프 포함)
            self.ctx.update_price(close_price, timestamp=close_time)
            
            # 바 데이터 생성
            bar = {
                "timestamp": close_time,  # 현재 시간 (캔들 종료 시간)
                "bar_timestamp": open_time,  # 캔들 시작 시간
                "bar_close": close_price,
                "price": close_price,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "is_new_bar": is_new_bar,
            }
            
            # 전략 실행
            self.strategy.on_bar(self.ctx, bar)
            
            # 현재 캔들의 종가를 지표에 추가 (다음 캔들에서 사용)
            # 새 봉이 확정된 경우에만 지표 업데이트 (중복 방지)
            if is_new_bar:
                self.ctx.update_bar(close_price)
            
            prev_bar_timestamp = open_time
            
            # 진행률 업데이트 (1% 단위로 업데이트)
            progress = (i + 1) / len(self.klines) * 100
            if self.progress_callback:
                self.progress_callback(progress)
            
            # 진행 상황 출력 (10% 단위)
            if len(self.klines) > 10 and (i + 1) % (len(self.klines) // 10 + 1) == 0:
                print(f"   진행 중... {progress:.1f}%")
        
        # 마지막 봉 종가 업데이트
        if self.klines:
            last_close = float(self.klines[-1][4])
            self.ctx.update_bar(last_close)
        
        # 전략 종료
        self.strategy.finalize(self.ctx)
        
        # 결과 계산
        final_balance = self.ctx.balance
        
        # 포지션이 남아있으면 청산
        if abs(self.ctx.position_size) > 1e-12:
            self.ctx.close_position(reason="백테스트 종료")
            final_balance = self.ctx.balance
        
        final_equity = final_balance
        total_return = (final_equity / initial_balance - 1) * 100 if initial_balance > 0 else 0
        
        # 거래별 손익 계산
        total_pnl = sum(t.get("pnl", 0) for t in self.ctx.trades if t.get("side") == "SELL")
        total_commission = sum(t.get("commission", 0) for t in self.ctx.trades)
        
        self.results = {
            "initial_balance": initial_balance,
            "final_balance": final_equity,
            "total_return_pct": total_return,
            "total_pnl": total_pnl,
            "total_commission": total_commission,
            "net_profit": final_equity - initial_balance,
            "total_trades": len([t for t in self.ctx.trades if t.get("side") == "SELL"]),  # 청산 거래 수
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
