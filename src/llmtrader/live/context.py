"""라이브 트레이딩 컨텍스트."""

import asyncio
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import time
from typing import Any

from llmtrader.binance.client import BinanceHTTPClient
from llmtrader.indicators.rsi import rsi_wilder_from_closes
from llmtrader.live.risk import RiskManager
from llmtrader.notifications.slack import SlackNotifier


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
        env: str = "local",
        notifier: SlackNotifier | None = None,
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
        self.env = env
        self.notifier = notifier

        # 전략 메타(로그/알림 용도; 엔진이 주입)
        self.strategy_rsi_period: int | None = None
        self.strategy_entry_rsi: float | None = None
        self.strategy_exit_rsi: float | None = None

        # 주문 중복 방지(특히 run_on_tick 전략에서 동일 신호가 연속으로 발생하는 문제 방지)
        # - 주문 제출/체결 후 포지션이 계정에 반영될 때까지 추가 주문을 막는다.
        self._order_inflight: bool = False
        self._last_order_started_at: float = 0.0
        
        self.balance: float = 0.0
        # 바이낸스 선물 계정의 사용가능 잔고(availableBalance). 포지션 증거금으로 묶이면 0에 가까워질 수 있음.
        self.available_balance: float = 0.0
        self.position = LivePosition()
        self._current_price: float = 0.0
        self._price_history: list[float] = []
        
        # 주문 추적
        self.pending_orders: dict[int, dict[str, Any]] = {}
        self.filled_orders: list[dict[str, Any]] = []
        
        # 미체결 주문 (거래소에서 조회)
        self.open_orders: list[dict[str, Any]] = []
        
        # 거래소 필터 정보 (정밀도 보정용)
        self.step_size: Decimal | None = None  # LOT_SIZE - 수량 스텝
        self.tick_size: Decimal | None = None  # PRICE_FILTER - 가격 스텝
        self.min_notional: Decimal | None = None  # MIN_NOTIONAL - 최소 주문 금액
        self.min_qty: Decimal | None = None  # LOT_SIZE - 최소 수량
        self.max_qty: Decimal | None = None  # LOT_SIZE - 최대 수량
        
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
        """컨텍스트 초기화 (레버리지 설정, 잔고 조회, 거래소 필터 조회)."""
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

        # 거래소 필터 정보 조회 (정밀도 보정용)
        try:
            exchange_info = await self.client.fetch_exchange_info(self.symbol)
            if self.symbol in exchange_info:
                filters = exchange_info[self.symbol]
                self.step_size = Decimal(filters.get("step_size", "0.001"))
                self.tick_size = Decimal(filters.get("tick_size", "0.01"))
                self.min_notional = Decimal(filters.get("min_notional", "5.0"))
                self.min_qty = Decimal(filters.get("min_qty", "0.001"))
                self.max_qty = Decimal(filters.get("max_qty", "1000"))
                self._log_audit("EXCHANGE_INFO_LOADED", {
                    "step_size": str(self.step_size),
                    "tick_size": str(self.tick_size),
                    "min_notional": str(self.min_notional),
                    "min_qty": str(self.min_qty),
                    "max_qty": str(self.max_qty),
                })
                print(f"📊 거래소 필터: step={self.step_size}, tick={self.tick_size}, min_notional={self.min_notional}")
        except Exception as e:
            # 필터 조회 실패해도 트레이딩은 계속 (기본값 사용)
            self._log_audit("EXCHANGE_INFO_FAILED", {"error": str(e)})
            print(f"⚠️ 거래소 필터 조회 실패 (기본값 사용): {e}")

        # 계좌 잔고 조회
        await self.update_account_info()

    async def update_account_info(self) -> None:
        """계좌 정보 업데이트."""
        try:
            account = await self.client._signed_request("GET", "/fapi/v2/account", {})
            # 바이낸스 UI에서 보는 "자산"은 보통 Wallet Balance(지갑 잔고)에 가깝습니다.
            # availableBalance는 포지션 증거금으로 묶이면 0에 가까워질 수 있어,
            # balance(총자산/리스크 계산)에는 walletBalance(또는 totalWalletBalance)를 사용합니다.
            wallet = account.get("walletBalance")
            if wallet is None:
                wallet = account.get("totalWalletBalance")
            if wallet is None:
                # fallback: 그래도 없으면 availableBalance 사용
                wallet = account.get("availableBalance", 0)
            self.balance = float(wallet)
            self.available_balance = float(account.get("availableBalance", 0))
            
            # 포지션 정보 업데이트
            positions = account.get("positions", [])
            for pos in positions:
                if pos["symbol"] == self.symbol:
                    self.position.size = float(pos["positionAmt"])
                    self.position.entry_price = float(pos["entryPrice"]) if self.position.size != 0 else 0.0
                    self.position.unrealized_pnl = float(pos["unrealizedProfit"])
                    break
            
            # 미체결 주문 목록 업데이트
            try:
                self.open_orders = await self.client.fetch_open_orders(self.symbol)
            except Exception as oe:  # noqa: BLE001
                # 미체결 주문 조회 실패는 치명적이지 않음
                self._log_audit("OPEN_ORDERS_FETCH_FAILED", {"error": str(oe)})
                
        except Exception as e:
            self._log_audit("ACCOUNT_UPDATE_FAILED", {"error": str(e)})
            raise

    def get_open_orders(self) -> list[dict[str, Any]]:
        """현재 미체결 주문 목록 반환.

        Returns:
            미체결 주문 목록
        """
        return self.open_orders

    def buy(self, quantity: float, price: float | None = None, reason: str | None = None) -> None:
        """매수 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가)
        """
        if self._order_inflight:
            # 비정상 상황에서 락이 풀리지 않아 주문이 영구히 막히는 것을 방지
            if (time.time() - self._last_order_started_at) > 5.0:
                print("⚠️ order_inflight timeout: releasing lock")
                self._release_order_inflight()
            else:
                return
        self._order_inflight = True
        self._last_order_started_at = time.time()
        # 비동기 주문을 태스크로 스케줄링
        task = asyncio.create_task(self._place_order("BUY", quantity, price, reason=reason))
        # 태스크 완료를 기다리지 않고 백그라운드에서 실행
        task.add_done_callback(self._handle_order_result)

    def sell(self, quantity: float, price: float | None = None, reason: str | None = None) -> None:
        """매도 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가)
        """
        if self._order_inflight:
            if (time.time() - self._last_order_started_at) > 5.0:
                print("⚠️ order_inflight timeout: releasing lock")
                self._release_order_inflight()
            else:
                return
        self._order_inflight = True
        self._last_order_started_at = time.time()
        # 비동기 주문을 태스크로 스케줄링
        task = asyncio.create_task(self._place_order("SELL", quantity, price, reason=reason))
        # 태스크 완료를 기다리지 않고 백그라운드에서 실행
        task.add_done_callback(self._handle_order_result)

    def close_position(self, reason: str | None = None) -> None:
        """현재 포지션 전체 청산."""
        if self._order_inflight:
            if (time.time() - self._last_order_started_at) > 5.0:
                print("⚠️ order_inflight timeout: releasing lock")
                self._release_order_inflight()
            else:
                return
        if self.position.size == 0:
            return
        
        if self.position.size > 0:
            self.sell(abs(self.position.size), reason=reason)
        else:
            self.buy(abs(self.position.size), reason=reason)
    
    def set_strategy_meta(self, strategy: Any) -> None:
        """전략 메타데이터를 컨텍스트에 주입(로그/알림용).

        Args:
            strategy: Strategy 인스턴스(duck typing)
        """
        try:
            p = getattr(strategy, "rsi_period", None)
            self.strategy_rsi_period = int(p) if p is not None else None
        except Exception:  # noqa: BLE001
            self.strategy_rsi_period = None
        try:
            v = getattr(strategy, "entry_rsi", None)
            self.strategy_entry_rsi = float(v) if v is not None else None
        except Exception:  # noqa: BLE001
            self.strategy_entry_rsi = None
        try:
            v = getattr(strategy, "exit_rsi", None)
            self.strategy_exit_rsi = float(v) if v is not None else None
        except Exception:  # noqa: BLE001
            self.strategy_exit_rsi = None

    def _handle_order_result(self, task: asyncio.Task) -> None:
        """주문 결과 처리 콜백.
        
        Args:
            task: 완료된 주문 태스크
        """
        try:
            result = task.result()
            # 체결 후 계좌/포지션 재조회 + RSI 로그/슬랙 알림은 비동기로 처리
            after_task = asyncio.create_task(self._after_order_filled(result))
            after_task.add_done_callback(lambda _t: self._release_order_inflight())
        except Exception as e:
            print(f"❌ 주문 실패: {e}")
            self._release_order_inflight()

    def _release_order_inflight(self) -> None:
        # 혹시 모를 예외로 인해 락이 영구히 걸리는 상황 방지
        self._order_inflight = False

    async def _after_order_filled(self, result: dict[str, Any]) -> None:
        """주문 체결 후 후처리:
        - 계좌/포지션 최신화
        - 체결 로그에 RSI(진입/청산 조건 RSI) 포함
        - 진입/청산 시 Slack 알림
        """
        # [Race Condition 해결] 스냅샷 데이터 우선 사용
        # _place_order에서 저장한 주문 전 포지션 상태를 사용하여 정확한 PnL 계산]
        reason = result.get("_reason", None)
        before_pos = float(result.get("_snapshot_pos_size", self.position.size))
        before_entry = float(result.get("_snapshot_entry_price", self.position.entry_price if self.position.size != 0 else 0.0))

        # 체결 직후 account 반영이 약간 지연될 수 있어 짧게 재시도
        after_pos = before_pos
        before_unrealized_pnl = float(self.position.unrealized_pnl)  # 청산 전 미실현 손익 저장
        for _ in range(3):
            try:
                await self.update_account_info()
                after_pos = float(self.position.size)
                break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.2)

        order_id = result.get("orderId", "N/A")
        side = result.get("side") or result.get("positionSide") or "N/A"
        executed_qty = result.get("executedQty") or result.get("origQty") or ""
        avg_price = result.get("avgPrice") or result.get("price") or ""

        # RSI: 전략 rsi_period(없으면 14)
        p = self.strategy_rsi_period or 14
        rsi_p = float(self.get_indicator("rsi", p))
        rsi_rt_p = float(self.get_indicator("rsi_rt", p))

        entry_thr = self.strategy_entry_rsi
        exit_thr = self.strategy_exit_rsi

        # 포지션 이벤트 분류(진입/청산만 Slack)
        event: str | None = None
        if abs(before_pos) < 1e-12 and abs(after_pos) >= 1e-12:
            event = "ENTRY"
        elif abs(before_pos) >= 1e-12 and abs(after_pos) < 1e-12:
            event = "EXIT"

        # EXIT PnL(추정): 청산 시점의 평균 체결가 기준으로 계산
        # - market 주문은 응답에 avgPrice가 "0", "0.00", 빈값으로 오는 경우가 있어 현재가를 fallback으로 사용
        # - 중요: avgPrice가 0이면 현재가를 사용해야 함 (0으로 계산하면 PnL이 엄청나게 틀려짐!)
        try:
            parsed_avg_price = float(avg_price) if avg_price not in ("", None, "0", "0.0", "0.00") else 0.0
        except (ValueError, TypeError):
            parsed_avg_price = 0.0
        
        # avgPrice가 0이거나 비정상적으로 작으면 현재가 사용
        if parsed_avg_price < 1.0:  # 가격이 1 미만이면 비정상
            exit_price = float(self.current_price)
        else:
            exit_price = parsed_avg_price
        
        # PnL 계산: EXIT 이벤트일 때만 계산
        # 방법 1: before_entry를 사용한 계산 (검증 후)
        # 방법 2: unrealizedProfit 차이 사용 (더 정확)
        pnl_exit = None
        if event == "EXIT" and before_pos != 0:
            current_price_check = float(self.current_price)
            
            # before_entry 검증: 0이거나 현재가의 2배 이상이면 비정상
            entry_price_valid = (
                before_entry > 0 
                and before_entry < current_price_check * 2.0 
                and before_entry > current_price_check * 0.1  # 현재가의 10% 이상
            )
            
            if entry_price_valid:
                # 방법 1: entry_price 기반 계산
                pnl_exit = before_pos * (exit_price - before_entry)
            else:
                # 방법 2: unrealizedProfit 차이 사용 (더 정확)
                # 청산 전 미실현 손익이 실제 실현 손익과 유사함
                after_unrealized_pnl = float(self.position.unrealized_pnl)
                pnl_exit = before_unrealized_pnl - after_unrealized_pnl
                
                # 여전히 비정상적이면 None으로 설정
                if abs(pnl_exit) > abs(before_pos * current_price_check * 0.5):  # 포지션 가치의 50% 이상 손익은 비정상
                    self._log_audit("PNL_CALC_ABNORMAL", {
                        "pnl_calculated": pnl_exit,
                        "before_entry": before_entry,
                        "before_unrealized_pnl": before_unrealized_pnl,
                        "after_unrealized_pnl": after_unrealized_pnl,
                        "current_price": current_price_check,
                        "exit_price": exit_price,
                    })
                    pnl_exit = None

        now = datetime.now().isoformat(timespec="seconds")
        last_now = float(self.current_price)
        msg = (
            f"✅ 주문 체결[{now}] orderId={order_id} side={side} qty={executed_qty} avg={avg_price} "
            f"| pos {before_pos:+.4f} -> {after_pos:+.4f} "
            f"| last={last_now:,.2f} "
            f"| rsi({p})={rsi_p:.2f} rsi_rt({p})={rsi_rt_p:.2f}"
        )
        
        if reason is not None:
            msg += f" | reason={reason}"
        if pnl_exit is not None:
            msg += f" | pnl={pnl_exit:+.2f} (est)"
        if entry_thr is not None or exit_thr is not None:
            msg += f" | thr(entry={entry_thr}, exit={exit_thr})"
        print(msg)

        self._log_audit(
            "ORDER_FILLED",
            {
                "order_id": order_id,
                "side": side,
                "executed_qty": executed_qty,
                "avg_price": avg_price,
                "position_before": before_pos,
                "position_after": after_pos,
                "rsi_period": p,
                "rsi_p": rsi_p,
                "rsi_rt_p": rsi_rt_p,
                "entry_rsi": entry_thr,
                "exit_rsi": exit_thr,
                "event": event,
                "pnl_exit_est": pnl_exit,
            },
        )

        if self.notifier and event in {"ENTRY", "EXIT"}:
            text = (
                f"*{event}* ({self.env}) {self.symbol}\n"
                f"- orderId: {order_id}\n"
                f"- side: {side}, qty: {executed_qty}, avg: {avg_price}\n"
                f"- pos: {before_pos:+.4f} -> {after_pos:+.4f}\n"
                f"- last: {last_now:,.2f}\n"
                f"- rsi({p}): {rsi_p:.2f} (rt {rsi_rt_p:.2f})\n"
                + (f"- thresholds: entry={entry_thr}, exit={exit_thr}\n" if entry_thr is not None or exit_thr is not None else "")
            )
            if event == "EXIT" and pnl_exit is not None:
                text += f"- pnl: {pnl_exit:+.2f} (est, using last price)\n"
            # Fire-and-forget: Slack API 지연이 트레이딩 루프를 막지 않도록 함
            asyncio.create_task(self._send_notification_safe(text))

    async def _send_notification_safe(self, text: str) -> None:
        """Slack 알림 전송 (fire-and-forget, 실패해도 무시).

        Args:
            text: 알림 메시지
        """
        if not self.notifier:
            return
        try:
            await asyncio.wait_for(self.notifier.send(text), timeout=5.0)
        except asyncio.TimeoutError:
            print("⚠️ Slack 알림 타임아웃 (5초)")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ Slack 알림 실패: {e}")

    def _adjust_quantity(self, quantity: float) -> float:
        """수량을 거래소 step_size 배수로 내림 처리.

        Args:
            quantity: 원래 수량

        Returns:
            정밀도가 보정된 수량
        """
        if self.step_size is None:
            return quantity
        
        qty_decimal = Decimal(str(quantity))
        # step_size 배수로 내림
        adjusted = (qty_decimal / self.step_size).to_integral_value(rounding=ROUND_DOWN) * self.step_size
        return float(adjusted)

    def _adjust_price(self, price: float) -> float:
        """가격을 거래소 tick_size 배수로 반올림 처리.

        Args:
            price: 원래 가격

        Returns:
            정밀도가 보정된 가격
        """
        if self.tick_size is None:
            return price
        
        price_decimal = Decimal(str(price))
        # tick_size 배수로 반올림
        adjusted = (price_decimal / self.tick_size).to_integral_value(rounding=ROUND_HALF_UP) * self.tick_size
        return float(adjusted)

    def _check_min_notional(self, quantity: float, price: float | None = None) -> tuple[bool, str]:
        """최소 주문 금액(MIN_NOTIONAL) 검증.

        Args:
            quantity: 주문 수량
            price: 주문 가격 (None이면 현재가 사용)

        Returns:
            (통과 여부, 메시지)
        """
        if self.min_notional is None:
            return True, ""
        
        use_price = price if price is not None else self._current_price
        if use_price <= 0:
            return False, "가격이 0 이하"
        
        notional = Decimal(str(quantity)) * Decimal(str(use_price))
        if notional < self.min_notional:
            return False, f"주문 금액({notional:.2f})이 최소 금액({self.min_notional})보다 작음"
        
        return True, ""

    async def _place_order(
        self,
        side: str,
        quantity: float,
        price: float | None = None,
        reason: str | None = None,
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

        # 정밀도 보정: 수량을 step_size 배수로 내림
        original_qty = quantity
        quantity = self._adjust_quantity(quantity)
        
        # 정밀도 보정: 가격을 tick_size 배수로 반올림
        original_price = price
        if price is not None:
            price = self._adjust_price(price)
        
        if original_qty != quantity or original_price != price:
            self._log_audit("ORDER_PRECISION_ADJUSTED", {
                "original_qty": original_qty,
                "adjusted_qty": quantity,
                "original_price": original_price,
                "adjusted_price": price,
            })
            print(f"📐 정밀도 보정: qty {original_qty} -> {quantity}, price {original_price} -> {price}")

        # 최소 수량 검증
        if self.min_qty is not None and Decimal(str(quantity)) < self.min_qty:
            error_msg = f"수량({quantity})이 최소 수량({self.min_qty})보다 작음"
            self._log_audit("ORDER_REJECTED_MIN_QTY", {"side": side, "quantity": quantity, "min_qty": str(self.min_qty)})
            raise ValueError(error_msg)

        # MIN_NOTIONAL 검증 (최소 주문 금액)
        valid, notional_msg = self._check_min_notional(quantity, price)
        if not valid:
            self._log_audit("ORDER_REJECTED_MIN_NOTIONAL", {
                "side": side,
                "quantity": quantity,
                "price": price or self._current_price,
                "reason": notional_msg,
            })
            raise ValueError(f"최소 주문 금액 미달: {notional_msg}")

        # 새 포지션 크기 계산 및 검증
        new_position_size = self.position.size + (quantity if side == "BUY" else -quantity)

        # 포지션을 '줄이는' 주문(청산/감축)은 리스크 관점에서 허용해야 합니다.
        # 예: 이미 롱(+0.02)인데 SELL 0.02로 청산하려는 경우, 단일 주문 크기 제한에 걸리면
        # 오히려 포지션을 줄일 수 없어 리스크가 커집니다.
        is_reducing_order = abs(new_position_size) < abs(self.position.size) - 1e-12

        # 주문 크기 검증 (감축 주문은 예외 처리)
        if not is_reducing_order:
            valid, msg = self.risk_manager.validate_order_size(
                quantity, self._current_price, self.total_equity, float(self.leverage)
            )
            if not valid:
                self._log_audit("ORDER_REJECTED_SIZE", {"side": side, "quantity": quantity, "reason": msg})
                raise ValueError(f"주문 크기 검증 실패: {msg}")

        # 포지션 크기 검증도 감축 주문은 예외 처리(총자산이 일시적으로 0/음수일 때도 청산은 가능해야 함)
        if not is_reducing_order:
            valid, msg = self.risk_manager.validate_position_size(
                new_position_size, self._current_price, self.total_equity, float(self.leverage)
            )
            if not valid:
                self._log_audit("ORDER_REJECTED_POSITION", {"side": side, "quantity": quantity, "reason": msg})
                raise ValueError(f"포지션 크기 검증 실패: {msg}")

        # [Race Condition 해결] 주문 전 포지션 상태 스냅샷 저장
        # _after_order_filled에서 정확한 PnL 계산을 위해 주문 전 상태를 보존
        snapshot_pos_size = self.position.size
        snapshot_entry_price = self.position.entry_price

        # 주문 실행
        order_type = "MARKET" if price is None else "LIMIT"
        try:
            order_params: dict[str, Any] = {"type": order_type}
            if price is not None:
                order_params["price"] = price
                order_params["timeInForce"] = "GTC"
            # 감축(청산) 주문은 reduceOnly로 보내 안전하게 포지션을 줄이도록 합니다.
            if is_reducing_order:
                order_params["reduceOnly"] = True

            response = await self.client.place_order(
                symbol=self.symbol,
                side=side,
                quantity=quantity,
                **order_params,
            )

            # [Race Condition 해결] 응답에 스냅샷 데이터 주입
            # _after_order_filled에서 이 스냅샷을 사용하여 정확한 PnL 계산]
            response["_reason"] = reason
            response["_snapshot_pos_size"] = snapshot_pos_size
            response["_snapshot_entry_price"] = snapshot_entry_price

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

            # [삭제] Race Condition 원인 제거
            # 주문 직후 update_account_info()를 백그라운드로 실행하면,
            # _after_order_filled 콜백 시점에 이미 포지션이 업데이트되어
            # before_pos/before_entry가 잘못된 값이 됨
            # 대신 _after_order_filled 내부에서 update_account_info()를 호출함
            # asyncio.create_task(self.update_account_info())  <-- 삭제

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
            return rsi_wilder_from_closes(list(self._price_history), int(period))

        elif name == "rsi_rt":
            period = args[0] if args else kwargs.get("period", 14)
            closes = list(self._price_history) + [float(self._current_price)]
            return rsi_wilder_from_closes(closes, int(period))

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

