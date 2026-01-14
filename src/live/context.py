"""라이브 트레이딩 컨텍스트."""

import asyncio
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import time
from typing import Any

from binance.client import BinanceHTTPClient
from binance.user_stream import BinanceUserStream
from indicators.rsi import rsi_wilder_from_closes
from live.risk import LiveRiskManager
from live.logger import get_logger
from notifications.slack import SlackNotifier


class LivePosition:
    """라이브 포지션."""

    def __init__(self) -> None:
        """포지션 초기화."""
        self.size: float = 0.0
        self.entry_price: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.entry_balance: float = 0.0  # 포지션 진입 시점의 balance


class LiveContext:
    """라이브 트레이딩 컨텍스트."""

    def __init__(
        self,
        client: BinanceHTTPClient,
        risk_manager: LiveRiskManager,
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
        self._logger = get_logger("llmtrader.live")

        self.strategy_rsi_period: int | None = None
        self.strategy_entry_rsi: float | None = None
        self.strategy_exit_rsi: float | None = None
        
        self.candle_interval: str = "1m"

        self._order_inflight: bool = False
        self._last_order_started_at: float = 0.0
        
        self._last_account_update_time: float = 0.0
        self._min_account_update_interval: float = 1.0

        self._chase_enabled: bool = True
        self._chase_max_attempts: int = 5
        self._chase_interval: float = 1.0
        self._chase_slippage_bps: float = 1.0
        self._chase_fallback_to_market: bool = True

        self._user_stream: BinanceUserStream | None = None
        self._user_stream_task: asyncio.Task | None = None
        self._use_user_stream: bool = False
        self._user_stream_connected: bool = False
        self._last_user_stream_account_update: float = 0.0
        self._account_reconcile_interval: float = 600.0
        self._last_reconcile_time: float = 0.0
        self._open_orders_by_id: dict[int, dict[str, Any]] = {}
        
        self._rest_fallback_active: bool = False
        self._rest_fallback_interval: float = 2.0
        self._rest_fallback_task: asyncio.Task | None = None
        
        self._last_trade_check_time: float = 0.0
        self._processed_trade_ids: set[int] = set()
        self._processed_order_ids: set[int] = set()
        self._chase_order_ids: list[int] = []
        
        # StopLoss cooldown 관련 변수
        self._stoploss_cooldown_until_bar_timestamp: int | None = None
        self._last_bar_timestamp: int | None = None
        
        self.balance: float = 0.0
        self.available_balance: float = 0.0
        self.position = LivePosition()
        self._current_price: float = 0.0
        self._price_history: list[float] = []
        
        self.pending_orders: dict[int, dict[str, Any]] = {}
        self.filled_orders: list[dict[str, Any]] = []
        
        self.open_orders: list[dict[str, Any]] = []
        
        self.step_size: Decimal | None = None
        self.tick_size: Decimal | None = None
        self.min_notional: Decimal | None = None
        self.min_qty: Decimal | None = None
        self.max_qty: Decimal | None = None
        
        self._best_bid: Decimal | None = None
        self._best_ask: Decimal | None = None
        
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
    def position_entry_balance(self) -> float:
        """포지션 진입 시점의 balance."""
        return self.position.entry_balance if abs(self.position.size) > 1e-12 else 0.0

    @property
    def unrealized_pnl(self) -> float:
        """미실현 손익."""
        return self.position.unrealized_pnl

    @property
    def total_equity(self) -> float:
        """총 자산."""
        return self.balance + self.unrealized_pnl

    async def initialize(self) -> None:
        """컨텍스트 초기화 (시간 동기화, 레버리지 설정, 잔고 조회, 거래소 필터 조회)."""
        await self.client.sync_time()
        
        valid, msg = self.risk_manager.validate_leverage(self.leverage)
        if not valid:
            raise ValueError(f"레버리지 검증 실패: {msg}")
        
        await self.update_account_info(force=True)
        
        if abs(self.position.size) < 1e-12:
            try:
                await self.client._signed_request(
                    "POST",
                    "/fapi/v1/leverage",
                    {"symbol": self.symbol, "leverage": self.leverage}
                )
                self._log_audit("LEVERAGE_SET", {"leverage": self.leverage})
                print(f"✅ 레버리지 설정 완료: {self.leverage}x")
            except Exception as e:
                self._log_audit("LEVERAGE_SET_FAILED", {"error": str(e)})
                raise
        else:
            print(f"⚠️ 기존 포지션 존재 (size={self.position.size:+.6f}). 레버리지 변경 건너뜀.")
            print(f"   포지션 청산 후 레버리지를 변경할 수 있습니다.")
            self._log_audit("LEVERAGE_SET_SKIPPED", {
                "reason": "position_exists",
                "position_size": self.position.size,
                "requested_leverage": self.leverage
            })

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
            self._log_audit("EXCHANGE_INFO_FAILED", {"error": str(e)})
            print(f"⚠️ 거래소 필터 조회 실패 (기본값 사용): {e}")

    async def start_user_stream(self) -> None:
        """유저데이터 스트림 시작 (중복 호출 방지 강화)."""
        if self._user_stream_task and not self._user_stream_task.done():
            return
        
        if self._user_stream_task and self._user_stream_task.done():
            try:
                self._user_stream_task.result()
            except Exception:
                pass
            self._user_stream_task = None
            self._user_stream = None

        is_testnet = "testnet" in self.client.base_url.lower()
        self._user_stream = BinanceUserStream(
            client=self.client,
            callback=self._handle_user_stream_event,
            testnet=is_testnet,
            on_disconnect=self._on_user_stream_disconnect,
            on_reconnect=self._on_user_stream_reconnect,
        )
        self._use_user_stream = True
        self._user_stream_connected = True
        now = time.time()
        self._last_user_stream_account_update = now
        self._last_reconcile_time = now
        self._last_trade_check_time = now
        self._user_stream_task = asyncio.create_task(self._user_stream.start())
        self._user_stream_task.add_done_callback(self._handle_user_stream_task_result)

    async def stop_user_stream(self) -> None:
        """유저데이터 스트림 중지."""
        if self._user_stream:
            await self._user_stream.stop()
        if self._user_stream_task:
            try:
                await asyncio.wait_for(self._user_stream_task, timeout=2.0)
            except asyncio.TimeoutError:
                pass
        self._user_stream_task = None
        self._user_stream = None
        self._use_user_stream = False

    def _handle_user_stream_task_result(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001
            print(f"User Stream stopped: {exc}")
            self._use_user_stream = False
            self._user_stream_connected = False
            self._user_stream_task = None
            self._user_stream = None

    async def _on_user_stream_disconnect(self) -> None:
        """User Stream 연결 끊김 시 호출 - REST 폴백 활성화.
        
        Note: 이 콜백은 실제 연결 끊김 시에만 호출됨 (user_stream.py에서 _is_actual_disconnect=True일 때만)
        """
        self._user_stream_connected = False
        self._rest_fallback_active = True
        self._log_audit("USER_STREAM_DISCONNECTED", {
            "fallback_enabled": True,
            "fallback_interval": self._rest_fallback_interval,
        })
        print(f"📡 REST 폴백 활성화 (주기: {self._rest_fallback_interval}초)")
        
        if self._rest_fallback_task is None or self._rest_fallback_task.done():
            self._rest_fallback_task = asyncio.create_task(self._rest_fallback_loop())

    async def _on_user_stream_reconnect(self, is_actual_disconnect: bool) -> None:
        """User Stream 재연결 시 호출 - 누락 거래 보정.
        
        Args:
            is_actual_disconnect: 실제 연결 끊김 여부 (True면 실제 문제, False면 메시지 타임아웃 등)
        """
        self._user_stream_connected = True
        self._rest_fallback_active = False
        
        if self._rest_fallback_task and not self._rest_fallback_task.done():
            self._rest_fallback_task.cancel()
            try:
                await self._rest_fallback_task
            except asyncio.CancelledError:
                pass
            self._rest_fallback_task = None
        
        # 실제 연결 끊김인 경우에만 로그 출력
        if is_actual_disconnect:
            print("🔄 REST 폴백 비활성화, 누락 거래 확인 중...")
            await self._reconcile_missed_trades(is_actual_disconnect=True)
            await self.update_account_info(force=True)
        else:
            # 메시지 타임아웃으로 인한 정상 재연결: 조용히 처리
            await self._reconcile_missed_trades(is_actual_disconnect=False)
            await self.update_account_info(force=True)
        
        self._log_audit("USER_STREAM_RECONNECTED", {
            "is_actual_disconnect": is_actual_disconnect,
            "position_size": self.position.size,
            "balance": self.balance,
        })

    async def _rest_fallback_loop(self) -> None:
        """REST 폴백 루프 - User Stream 끊김 시 주기적으로 REST로 계좌/포지션 조회.
        
        Note: 이 루프는 실제 연결 끊김 시에만 시작되므로 로그 출력 안 함 (조용히 동작)
        """
        while self._rest_fallback_active and self._use_user_stream:
            try:
                await self.update_account_info(force=True)
                await self._check_recent_trades()
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ REST 폴백 조회 오류: {e}")
            
            await asyncio.sleep(self._rest_fallback_interval)

    async def _reconcile_missed_trades(self, is_actual_disconnect: bool = True) -> None:
        """재연결 후 누락된 거래 보정.
        
        Args:
            is_actual_disconnect: 실제 연결 끊김 여부 (True면 로그 출력, False면 조용히 처리)
        """
        try:
            now_ms = int(time.time() * 1000)
            start_time = int(self._last_trade_check_time * 1000) if self._last_trade_check_time > 0 else now_ms - 3600000
            
            trades = await self.client.fetch_user_trades(
                symbol=self.symbol,
                start_time=start_time,
                end_time=now_ms,
                limit=100,
            )
            
            if not trades:
                if is_actual_disconnect:
                    print("✅ 누락 거래 없음")
                return
            
            new_trades = [t for t in trades if t.get("id") not in self._processed_trade_ids]
            
            if new_trades:
                if is_actual_disconnect:
                    print(f"📋 누락 거래 {len(new_trades)}건 발견, 로그 기록 중...")
                for trade in new_trades:
                    trade_id = trade.get("id")
                    if trade_id:
                        self._processed_trade_ids.add(trade_id)
                    
                    self._log_audit("MISSED_TRADE_RECONCILED", {
                        "trade_id": trade_id,
                        "order_id": trade.get("orderId"),
                        "side": trade.get("side"),
                        "price": trade.get("price"),
                        "qty": trade.get("qty"),
                        "realized_pnl": trade.get("realizedPnl"),
                        "commission": trade.get("commission"),
                        "time": trade.get("time"),
                    })
                
                if len(self._processed_trade_ids) > 10000:
                    sorted_ids = sorted(self._processed_trade_ids)
                    self._processed_trade_ids = set(sorted_ids[-5000:])
            else:
                if is_actual_disconnect:
                    print("✅ 모든 거래가 이미 처리됨")
            
            self._last_trade_check_time = time.time()
            
        except Exception as e:  # noqa: BLE001
            self._log_audit("RECONCILE_TRADES_FAILED", {"error": str(e)})
            if is_actual_disconnect:
                print(f"⚠️ 누락 거래 조회 실패: {e}")

    async def _check_recent_trades(self) -> None:
        """최근 거래 확인 (REST 폴백 시 사용)."""
        try:
            now_ms = int(time.time() * 1000)
            start_time = now_ms - 60000
            
            trades = await self.client.fetch_user_trades(
                symbol=self.symbol,
                start_time=start_time,
                limit=20,
            )
            
            for trade in trades:
                trade_id = trade.get("id")
                if trade_id and trade_id not in self._processed_trade_ids:
                    self._processed_trade_ids.add(trade_id)
                    print(f"📋 REST 폴백: 거래 감지 orderId={trade.get('orderId')} side={trade.get('side')} qty={trade.get('qty')}")
                    self._log_audit("REST_FALLBACK_TRADE_DETECTED", {
                        "trade_id": trade_id,
                        "order_id": trade.get("orderId"),
                        "side": trade.get("side"),
                        "price": trade.get("price"),
                        "qty": trade.get("qty"),
                    })
            
            self._last_trade_check_time = time.time()
            
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 최근 거래 확인 실패: {e}")

    async def _verify_order_with_rest(
        self,
        result: dict[str, Any],
        before_pos: float,
        after_pos_api: float,
    ) -> None:
        """주문 체결 후 REST API로 거래 검증.
        
        Args:
            result: 주문 응답
            before_pos: 주문 전 포지션
            after_pos_api: User Stream/REST로 확인된 현재 포지션
        """
        try:
            order_id = result.get("orderId")
            all_order_ids = result.get("_all_order_ids", [])
            
            if not order_id and not all_order_ids:
                return
            
            now_ms = int(time.time() * 1000)
            start_time = now_ms - 300000
            
            trades = await self.client.fetch_user_trades(
                symbol=self.symbol,
                start_time=start_time,
                limit=50,
            )
            
            order_ids_to_check = set(all_order_ids) if all_order_ids else {order_id}
            matched_trades = [t for t in trades if t.get("orderId") in order_ids_to_check]
            
            if matched_trades:
                total_qty = sum(float(t.get("qty", 0)) for t in matched_trades)
                total_commission = sum(float(t.get("commission", 0)) for t in matched_trades)
                total_pnl = sum(float(t.get("realizedPnl", 0)) for t in matched_trades)
                
                self._log_audit("ORDER_VERIFIED_REST", {
                    "order_ids": list(order_ids_to_check),
                    "matched_trade_count": len(matched_trades),
                    "total_qty": total_qty,
                    "total_commission": total_commission,
                    "total_realized_pnl": total_pnl,
                    "before_pos": before_pos,
                    "after_pos_api": after_pos_api,
                })
                
                for trade in matched_trades:
                    trade_id = trade.get("id")
                    if trade_id:
                        self._processed_trade_ids.add(trade_id)
            else:
                self._log_audit("ORDER_VERIFY_NO_MATCH", {
                    "order_ids": list(order_ids_to_check),
                    "trades_checked": len(trades),
                })
                
        except Exception as e:  # noqa: BLE001
            self._log_audit("ORDER_VERIFY_FAILED", {"error": str(e)})

    async def _handle_user_stream_event(self, data: dict[str, Any]) -> None:
        event_type = data.get("e")
        if event_type == "ACCOUNT_UPDATE":
            self._apply_account_update(data)
        elif event_type == "ORDER_TRADE_UPDATE":
            self._apply_order_update(data)

    def _apply_account_update(self, data: dict[str, Any]) -> None:
        account = data.get("a", {})
        balances = account.get("B", [])
        for bal in balances:
            if bal.get("a") == "USDT":
                wallet = bal.get("wb")
                cross = bal.get("cw")
                if wallet is not None:
                    self.balance = float(wallet)
                if cross is not None:
                    self.available_balance = float(cross)
                break

        positions = account.get("P", [])
        for pos in positions:
            if pos.get("s") != self.symbol:
                continue
            try:
                size = float(pos.get("pa", 0))
                prev_size = self.position.size
                self.position.size = size
                self.position.entry_price = float(pos.get("ep", 0)) if abs(size) > 1e-12 else 0.0
                self.position.unrealized_pnl = float(pos.get("up", 0))
                
                # 포지션이 새로 진입한 경우 entry_balance 저장
                if abs(prev_size) < 1e-12 and abs(size) > 1e-12:
                    self.position.entry_balance = self.balance
                # 포지션이 청산된 경우 entry_balance 리셋
                elif abs(prev_size) > 1e-12 and abs(size) < 1e-12:
                    self.position.entry_balance = 0.0
            except (TypeError, ValueError):
                pass
            break

        now = time.time()
        self._last_user_stream_account_update = now
        self._last_account_update_time = now

    def _apply_order_update(self, data: dict[str, Any]) -> None:
        order = data.get("o", {})
        if order.get("s") != self.symbol:
            return

        order_id = order.get("i")
        if order_id is None:
            return

        try:
            order_id_int = int(order_id)
        except (TypeError, ValueError):
            return

        status = order.get("X")

        def _to_float(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        order_info = {
            "order_id": order_id_int,
            "side": order.get("S"),
            "type": order.get("o"),
            "price": _to_float(order.get("p")),
            "avg_price": _to_float(order.get("ap")),
            "orig_qty": _to_float(order.get("q")),
            "executed_qty": _to_float(order.get("z")),
            "status": status,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }

        if status in {"NEW", "PARTIALLY_FILLED"}:
            self.pending_orders[order_id_int] = order_info
            self._open_orders_by_id[order_id_int] = order_info
        else:
            self.pending_orders.pop(order_id_int, None)
            self._open_orders_by_id.pop(order_id_int, None)

        self.open_orders = list(self._open_orders_by_id.values())

    async def _wait_for_user_stream_account_update(self, timeout: float = 1.0) -> bool:
        if not self._use_user_stream:
            return False

        start_ts = self._last_user_stream_account_update
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._last_user_stream_account_update > start_ts:
                return True
            await asyncio.sleep(0.05)
        return False

    async def update_book_ticker(self, data: dict[str, Any]) -> None:
        """BookTicker 스트림 콜백 - best bid/ask 업데이트.

        Args:
            data: BookTicker 데이터 {"b": "best_bid", "a": "best_ask", ...}
        """
        try:
            self._best_bid = Decimal(data["b"])
            self._best_ask = Decimal(data["a"])
        except (KeyError, ValueError) as e:
            print(f"⚠️ BookTicker 데이터 파싱 오류: {e}")

    async def update_account_info(self, force: bool = False) -> None:
        """계좌 정보 업데이트.

        Args:
            force: 유저데이터 스트림 사용 중에도 REST 조회를 강제할지 여부
        """
        if self._use_user_stream and not force and self._last_user_stream_account_update > 0:
            now = time.time()
            if self._account_reconcile_interval <= 0:
                return
            if (now - self._last_reconcile_time) < self._account_reconcile_interval:
                return
        current_time = time.time()
        time_since_last_update = current_time - self._last_account_update_time
        if time_since_last_update < self._min_account_update_interval:
            wait_time = self._min_account_update_interval - time_since_last_update
            await asyncio.sleep(wait_time)
            current_time = time.time()
        
        try:
            account = await self.client._signed_request("GET", "/fapi/v2/account", {})
            now = time.time()
            self._last_account_update_time = now
            if self._use_user_stream:
                self._last_reconcile_time = now
            
            multi_assets_mode = account.get("multiAssetsMargin", False)
            
            if not multi_assets_mode:
                assets = account.get("assets", [])
                usdt_asset = next((a for a in assets if a.get("asset") == "USDT"), None)
                if usdt_asset:
                    wallet = usdt_asset.get("walletBalance")
                else:
                    wallet = account.get("walletBalance")
            else:
                wallet = account.get("walletBalance")
            
            if wallet is None:
                wallet = account.get("totalWalletBalance")
            if wallet is None:
                wallet = account.get("availableBalance", 0)
            
            self.balance = float(wallet)
            self.available_balance = float(account.get("availableBalance", 0))
            
            positions = account.get("positions", [])
            for pos in positions:
                if pos["symbol"] == self.symbol:
                    prev_size = self.position.size
                    self.position.size = float(pos["positionAmt"])
                    self.position.entry_price = float(pos["entryPrice"]) if self.position.size != 0 else 0.0
                    self.position.unrealized_pnl = float(pos["unrealizedProfit"])
                    
                    # 포지션이 새로 진입한 경우 entry_balance 저장
                    if abs(prev_size) < 1e-12 and abs(self.position.size) > 1e-12:
                        self.position.entry_balance = self.balance
                    # 포지션이 청산된 경우 entry_balance 리셋
                    elif abs(prev_size) > 1e-12 and abs(self.position.size) < 1e-12:
                        self.position.entry_balance = 0.0
                    break
            
            if not self._use_user_stream or force:
                try:
                    self.open_orders = await self.client.fetch_open_orders(self.symbol)
                    self._open_orders_by_id = {
                        int(o.get("orderId")): o
                        for o in self.open_orders
                        if o.get("orderId") is not None
                    }
                except Exception as oe:  # noqa: BLE001
                    self._log_audit("OPEN_ORDERS_FETCH_FAILED", {"error": str(oe)})
                
        except Exception as e:
            self._log_audit("ACCOUNT_UPDATE_FAILED", {"error": str(e)})

    def get_open_orders(self) -> list[dict[str, Any]]:
        """현재 미체결 주문 목록 반환.

        Returns:
            미체결 주문 목록
        """
        return self.open_orders

    def buy(self, quantity: float, price: float | None = None, reason: str | None = None, use_chase: bool | None = None) -> None:
        """매수 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가 또는 Chase Order)
            reason: 주문 사유
            use_chase: Chase Order 사용 여부 (None이면 _chase_enabled 설정 따름)
        """
        if self._order_inflight:
            if (time.time() - self._last_order_started_at) > 5.0:
                print("⚠️ order_inflight timeout: releasing lock")
                self._release_order_inflight()
            else:
                return
        self._order_inflight = True
        self._last_order_started_at = time.time()

        should_chase = use_chase if use_chase is not None else self._chase_enabled
        if should_chase and price is None:
            task = asyncio.create_task(self._place_chase_order("BUY", quantity, reason=reason))
        else:
            task = asyncio.create_task(self._place_order("BUY", quantity, price, reason=reason))
        task.add_done_callback(self._handle_order_result)

    def sell(self, quantity: float, price: float | None = None, reason: str | None = None, use_chase: bool | None = None) -> None:
        """매도 주문.

        Args:
            quantity: 수량
            price: 가격 (None이면 시장가 또는 Chase Order)
            reason: 주문 사유
            use_chase: Chase Order 사용 여부 (None이면 _chase_enabled 설정 따름)
        """
        if self._order_inflight:
            if (time.time() - self._last_order_started_at) > 5.0:
                print("⚠️ order_inflight timeout: releasing lock")
                self._release_order_inflight()
            else:
                return
        self._order_inflight = True
        self._last_order_started_at = time.time()

        should_chase = use_chase if use_chase is not None else self._chase_enabled
        if should_chase and price is None:
            task = asyncio.create_task(self._place_chase_order("SELL", quantity, reason=reason))
        else:
            task = asyncio.create_task(self._place_order("SELL", quantity, price, reason=reason))
        task.add_done_callback(self._handle_order_result)

    def close_position(self, reason: str | None = None, use_chase: bool | None = None) -> None:
        """현재 포지션 전체 청산.
        
        Args:
            reason: 청산 사유
            use_chase: Chase Order 사용 여부 (None이면 _chase_enabled 설정 따름)
        """
        if self._order_inflight:
            if (time.time() - self._last_order_started_at) > 5.0:
                print("⚠️ order_inflight timeout: releasing lock")
                self._release_order_inflight()
            else:
                return
        if self.position.size == 0:
            return
        
        if self.position.size > 0:
            self.sell(abs(self.position.size), reason=reason, use_chase=use_chase)
        else:
            self.buy(abs(self.position.size), reason=reason, use_chase=use_chase)

    def close_position_at_price(self, price: float, reason: str | None = None) -> None:
        """포지션 전체 청산 (지정가).
        
        Args:
            price: 청산 가격
            reason: 청산 사유
        """
        if self._order_inflight:
            if (time.time() - self._last_order_started_at) > 5.0:
                print("⚠️ order_inflight timeout: releasing lock")
                self._release_order_inflight()
            else:
                return
        if self.position.size == 0:
            return
        
        if self.position.size > 0:
            self.sell(abs(self.position.size), price=price, reason=reason, use_chase=False)
        else:
            self.buy(abs(self.position.size), price=price, reason=reason, use_chase=False)

    def configure_chase_order(
        self,
        enabled: bool | None = None,
        max_attempts: int | None = None,
        interval: float | None = None,
        slippage_bps: float | None = None,
        fallback_to_market: bool | None = None,
    ) -> None:
        """Chase Order 설정 변경.

        Args:
            enabled: Chase Order 활성화 여부
            max_attempts: 최대 재시도 횟수 (기본값: 5)
            interval: 재시도 간격 (초, 기본값: 1.0)
            slippage_bps: 슬리피지 (bps 단위, 기본값: 1.0 = 0.01%)
            fallback_to_market: 실패 시 시장가 전환 여부 (기본값: True)
        """
        if enabled is not None:
            self._chase_enabled = enabled
        if max_attempts is not None:
            self._chase_max_attempts = max_attempts
        if interval is not None:
            self._chase_interval = interval
        if slippage_bps is not None:
            self._chase_slippage_bps = slippage_bps
        if fallback_to_market is not None:
            self._chase_fallback_to_market = fallback_to_market

        print(f"⚙️ Chase Order 설정: enabled={self._chase_enabled}, max_attempts={self._chase_max_attempts}, "
              f"interval={self._chase_interval}s, slippage={self._chase_slippage_bps}bps, "
              f"fallback_to_market={self._chase_fallback_to_market}")

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
            after_task = asyncio.create_task(self._after_order_filled(result))
            after_task.add_done_callback(lambda _t: self._release_order_inflight())
        except Exception as e:
            print(f"❌ 주문 실패: {e}")
            self._release_order_inflight()

    def _release_order_inflight(self) -> None:
        self._order_inflight = False

    async def _after_order_filled(self, result: dict[str, Any]) -> None:
        """주문 체결 후 후처리."""
        order_id = result.get("orderId")
        
        # 중복 처리 방지: 이미 처리된 주문이면 건너뜀
        if order_id and order_id != "N/A":
            try:
                order_id_int = int(order_id)
                if order_id_int in self._processed_order_ids:
                    print(f"⚠️ 이미 처리된 주문: orderId={order_id}, 중복 처리 건너뜀")
                    self._log_audit("ORDER_AFTER_FILLED_SKIPPED_DUPLICATE", {"order_id": order_id_int})
                    return
                self._processed_order_ids.add(order_id_int)
            except (TypeError, ValueError):
                pass
        
        reason = result.get("_reason", None)
        initial_pos = result.get("_initial_pos_size")
        before_pos = float(initial_pos if initial_pos is not None else result.get("_snapshot_pos_size", self.position.size))
        before_entry = float(result.get("_snapshot_entry_price", self.position.entry_price if self.position.size != 0 else 0.0))

        before_unrealized_pnl = float(self.position.unrealized_pnl)
        
        all_order_ids = result.get("_all_order_ids", [])
        if all_order_ids:
            # Chase Order의 모든 orderId도 처리됨으로 표시
            for oid in all_order_ids:
                try:
                    oid_int = int(oid)
                    self._processed_order_ids.add(oid_int)
                except (TypeError, ValueError):
                    pass
            
            self._log_audit("CHASE_ORDER_IDS_SUMMARY", {
                "all_order_ids": all_order_ids,
                "count": len(all_order_ids),
            })
        
        if not order_id or order_id == "N/A":
            order_id = "N/A"
        side = result.get("side") or result.get("positionSide") or "N/A"
        executed_qty = result.get("executedQty") or result.get("origQty") or ""
        avg_price = result.get("avgPrice") or result.get("price") or ""
        order_type = result.get("type", "MARKET")
        
        internal_order_type = result.get("_order_type")
        is_maker = order_type == "LIMIT" or internal_order_type in ("CHASE_LIMIT", "LIMIT")
        order_type_display = "LIMIT(Maker)" if is_maker else "MARKET(Taker)"
        
        executed_qty_float = float(executed_qty) if executed_qty else 0.0
        
        # executed_qty 기반으로 포지션 계산 (REST API 응답이므로 정확함)
        calculated_after_pos: float | None = None
        if executed_qty_float > 0:
            if side == "BUY":
                calculated_after_pos = before_pos + executed_qty_float
            elif side == "SELL":
                calculated_after_pos = before_pos - executed_qty_float
        
        # API 포지션 확인 (User Stream 또는 REST)
        if initial_pos is not None:
            # Chase Order: User Stream이 연결되어 있으면 업데이트 대기, 아니면 계산값 사용
            if self._use_user_stream and self._user_stream_connected:
                updated = await self._wait_for_user_stream_account_update(timeout=0.5)
                if updated:
                    after_pos_api = float(self.position.size)
                else:
                    # User Stream 업데이트 없으면 계산값 사용
                    after_pos_api = calculated_after_pos if calculated_after_pos is not None else float(self.position.size)
            else:
                # User Stream 끊김: 계산값 우선 사용 (REST API 호출 없음)
                after_pos_api = calculated_after_pos if calculated_after_pos is not None else float(self.position.size)
        else:
            # 일반 주문: User Stream 업데이트 대기 또는 REST API 호출
            after_pos_api = before_pos
            for _ in range(5):
                try:
                    if self._use_user_stream and self._user_stream_connected:
                        updated = await self._wait_for_user_stream_account_update(timeout=0.6)
                        if updated:
                            after_pos_api = float(self.position.size)
                            break
                        await asyncio.sleep(0.3)
                    else:
                        await self.update_account_info(force=True)
                        after_pos_api = float(self.position.size)
                        break
                except Exception:  # noqa: BLE001
                        await asyncio.sleep(0.3)
        
        await self._verify_order_with_rest(result, before_pos, after_pos_api)
        
        # 최종 포지션 결정: 계산값과 API 값 비교
        after_pos = after_pos_api
        if calculated_after_pos is not None and abs(executed_qty_float) > 1e-12:
            if abs(after_pos_api - before_pos) < 1e-12:
                # API 값이 변화 없으면 계산값 사용 (API 지연)
                print(f"⚠️ API 지연 감지: after_pos_api={after_pos_api:+.6f} (변화 없음), executedQty={executed_qty_float:+.6f} 기반 계산값={calculated_after_pos:+.6f} 사용")
                after_pos = calculated_after_pos
            elif abs(calculated_after_pos - after_pos_api) > 1e-8:
                # 불일치 시 계산값 우선 (REST API 응답이 더 정확)
                print(f"⚠️ 포지션 불일치: API={after_pos_api:+.6f}, 계산값={calculated_after_pos:+.6f}, executedQty={executed_qty_float:+.6f}, side={side} → 계산값 사용")
                if (calculated_after_pos * after_pos_api) >= 0:
                    after_pos = calculated_after_pos
            else:
                # 일치하면 API 값 사용
                after_pos = after_pos_api
        
        def parse_price(price_str: str) -> float:
            """가격 문자열을 float로 변환. 0이거나 유효하지 않으면 0.0 반환."""
            if not price_str or price_str in ("0", "0.0", "0.00", "0.000", "0.0000", "0.00000"):
                return 0.0
            try:
                price = float(price_str)
                return price if price > 0 else 0.0
            except (ValueError, TypeError):
                return 0.0
        
        parsed_avg_price = parse_price(avg_price)
        if parsed_avg_price <= 0:
            parsed_avg_price = float(self.current_price)
        
        commission_asset = result.get("commissionAsset", "USDT")
        MAKER_COMMISSION_RATE = 0.0002
        TAKER_COMMISSION_RATE = 0.0004
        commission_rate = MAKER_COMMISSION_RATE if is_maker else TAKER_COMMISSION_RATE
        commission_rate_pct = commission_rate * 100
        
        final_commission = 0.0
        if "commission" in result:
            try:
                api_commission = float(result.get("commission", "0"))
                if api_commission > 0:
                    final_commission = api_commission
            except (ValueError, TypeError):
                pass
        
        if final_commission == 0.0 and parsed_avg_price > 0 and executed_qty_float > 0:
            notional = executed_qty_float * parsed_avg_price
            final_commission = notional * commission_rate

        p = self.strategy_rsi_period or 14
        rsi_p = float(self.get_indicator("rsi", p))
        rsi_rt_p = float(self.get_indicator("rsi_rt", p))

        entry_thr = self.strategy_entry_rsi
        exit_thr = self.strategy_exit_rsi

        event: str | None = None
        if abs(before_pos) < 1e-12 and abs(after_pos) >= 1e-12:
            event = "ENTRY"
        elif abs(before_pos) >= 1e-12 and abs(after_pos) < 1e-12:
            event = "EXIT"
        
        if event:
            print(f"🔔 이벤트 분류: {event} (before_pos={before_pos:+.6f}, after_pos={after_pos:+.6f}, after_pos_api={after_pos_api:+.6f}, side={side}, executed_qty={executed_qty_float:+.6f})")
        elif abs(before_pos) >= 1e-12 or abs(after_pos) >= 1e-12:
            print(f"⚠️ 이벤트 분류 실패: before_pos={before_pos:+.6f}, after_pos={after_pos:+.6f}, after_pos_api={after_pos_api:+.6f}, side={side}, executed_qty={executed_qty_float:+.6f}")

        exit_price = parsed_avg_price
        
        # StopLoss로 청산된 경우 cooldown 시작
        if event == "EXIT" and reason and "StopLoss" in reason:
            cooldown_candles = self.risk_manager.config.stoploss_cooldown_candles
            if cooldown_candles > 0:
                # 현재 봉부터 cooldown_candles 개의 봉 동안 거래 중단
                # 봉 간격을 계산 (예: 5m = 300초)
                interval_seconds = self._get_candle_interval_seconds()
                cooldown_duration_ms = cooldown_candles * interval_seconds * 1000
                
                # _last_bar_timestamp가 없으면 현재 시간을 기반으로 계산
                if self._last_bar_timestamp is not None:
                    start_timestamp = self._last_bar_timestamp
                else:
                    # 현재 시간을 밀리초로 변환하고, 봉 간격으로 반올림
                    current_time_ms = int(time.time() * 1000)
                    start_timestamp = (current_time_ms // (interval_seconds * 1000)) * (interval_seconds * 1000)
                
                self._stoploss_cooldown_until_bar_timestamp = start_timestamp + cooldown_duration_ms
                
                # 시스템 로그 출력
                interval_str = self.candle_interval
                cooldown_duration_minutes = (cooldown_candles * interval_seconds) / 60
                self._logger.info(
                    f"STOPLOSS_COOLDOWN_STARTED | symbol={self.symbol}, cooldown_candles={cooldown_candles}, "
                    f"candle_interval={interval_str}, duration_minutes={cooldown_duration_minutes:.1f}, "
                    f"until_bar_timestamp={self._stoploss_cooldown_until_bar_timestamp}, reason={reason}"
                )
                
                print(f"⏸️ StopLoss 청산으로 인한 거래 중단: {cooldown_candles}개 캔들 동안 거래 중단 (종료 예상: {self._stoploss_cooldown_until_bar_timestamp})")
                
                self._log_audit("STOPLOSS_COOLDOWN_STARTED", {
                    "cooldown_candles": cooldown_candles,
                    "until_bar_timestamp": self._stoploss_cooldown_until_bar_timestamp,
                    "last_bar_timestamp": self._last_bar_timestamp,
                    "start_timestamp": start_timestamp,
                })
                
                # Slack 알림 전송
                if self.notifier:
                    cooldown_text = (
                        f"*⏸️ StopLoss Cooldown 시작* ({self.env}) {self.symbol}\n"
                        f"- 이유: {reason}\n"
                        f"- Cooldown 기간: {cooldown_candles}개 캔들 ({cooldown_duration_minutes:.1f}분)\n"
                        f"- 캔들 간격: {interval_str}\n"
                        f"- 거래 재개 예상: {cooldown_candles}개 캔들 후"
                    )
                    asyncio.create_task(self._send_notification_safe(cooldown_text, "warning"))
        
        pnl_exit = None
        if event == "EXIT" and before_pos != 0:
            current_price_check = float(self.current_price)
            
            entry_price_valid = (
                before_entry > 0 
                and before_entry < current_price_check * 2.0 
                and before_entry > current_price_check * 0.1
            )
            
            if entry_price_valid:
                pnl_exit = before_pos * (exit_price - before_entry)
            else:
                after_unrealized_pnl = float(self.position.unrealized_pnl)
                pnl_exit = before_unrealized_pnl - after_unrealized_pnl
                
                if abs(pnl_exit) > abs(before_pos * current_price_check * 0.5):
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
        
        before_pos_usd = before_pos * (before_entry if before_entry > 0 else last_now)
        after_pos_usd = after_pos * last_now

        self._logger.log_order_filled(
            symbol=self.symbol,
            order_id=order_id,
            side=side,
            event=event,
            position_before=before_pos,
            position_after=after_pos,
            position_before_usd=before_pos_usd,
            position_after_usd=after_pos_usd,
            price=last_now,
            rsi=rsi_p,
            rsi_rt=rsi_rt_p,
            rsi_period=p,
            pnl=pnl_exit,
            commission=final_commission,
            reason=reason,
            order_type=order_type_display,
            commission_rate=commission_rate_pct,
        )

        self._log_audit(
            "ORDER_FILLED",
            {
                "order_id": order_id,
                "side": side,
                "executed_qty": executed_qty,
                "avg_price": avg_price,
                "order_type": order_type_display,
                "is_maker": is_maker,
                "position_before": before_pos,
                "position_after": after_pos,
                "position_before_usd": before_pos_usd,
                "position_after_usd": after_pos_usd,
                "commission": final_commission,
                "commission_asset": commission_asset,
                "commission_rate": commission_rate,
                "commission_rate_pct": commission_rate_pct,
                "rsi_period": p,
                "rsi_p": rsi_p,
                "rsi_rt_p": rsi_rt_p,
                "entry_rsi": entry_thr,
                "exit_rsi": exit_thr,
                "event": event,
                "pnl_exit_est": pnl_exit,
                "pnl_exit_after_fee": pnl_exit - final_commission if pnl_exit is not None else None,
            },
        )

        if self.notifier and event in {"ENTRY", "EXIT"}:
            max_position_pct = self.risk_manager.config.max_position_size * 100
            
            text = (
                f"*{event}* ({self.env}) {self.symbol}\n"
                f"- orderId: {order_id}\n"
                f"- side: {side}\n"
                f"- type: {order_type_display}\n"
                f"- pos: {before_pos:+.4f} -> {after_pos:+.4f}\n"
                + (f"- thresholds: entry={entry_thr}, exit={exit_thr}\n" if entry_thr is not None or exit_thr is not None else "")
            )
            text += f"- candle-interval: {self.candle_interval}\n"
            text += f"- commission: {final_commission:.4f} {commission_asset} (rate={commission_rate_pct:.2f}%)\n"
            
            if event == "EXIT" and pnl_exit is not None:
                pnl_after_fee = pnl_exit - final_commission
                text += f"- pnl (before fee): {pnl_exit:+.2f} (est, using last price)\n"
                text += f"- pnl (after fee): {pnl_after_fee:+.2f} (est)\n"
            if reason:
                text += f"- reason: {reason}\n"
            color = "good" if event == "ENTRY" else "danger"
            print(f"📤 Slack 알림 전송 시도: event={event}, notifier={'있음' if self.notifier else '없음'}")
            asyncio.create_task(self._send_notification_safe(text, color))
        elif event in {"ENTRY", "EXIT"}:
            print(f"⚠️ Slack 알림 건너뜀: event={event}, notifier={'있음' if self.notifier else '없음'}")
        elif self.notifier:
            print(f"ℹ️ Slack 알림 건너뜀: event={event} (ENTRY/EXIT 아님)")

    async def _send_notification_safe(self, text: str, color: str | None = None) -> None:
        """Slack 알림 전송 (fire-and-forget, 실패해도 무시).

        Args:
            text: 알림 메시지
            color: 색상 ("good"=녹색, "danger"=빨간색)
        """
        if not self.notifier:
            print("⚠️ Slack 알림 실패: notifier가 None입니다")
            return
        
        if not self.notifier.webhook_url or not self.notifier.webhook_url.strip():
            print("⚠️ Slack 알림 실패: webhook_url이 비어있습니다")
            return
        
        try:
            await asyncio.wait_for(self.notifier.send(text, color=color), timeout=5.0)
            print("✅ Slack 알림 전송 성공")
        except asyncio.TimeoutError:
            print("⚠️ Slack 알림 타임아웃 (5초)")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ Slack 알림 실패: {e}")
            import traceback
            traceback.print_exc()

    def _adjust_quantity(self, quantity: float) -> Decimal:
        """수량을 거래소 step_size 배수로 내림 처리.

        Args:
            quantity: 원래 수량

        Returns:
            정밀도가 보정된 수량 (Decimal - API 전달 시 str()로 변환 필요)
        """
        if self.step_size is None:
            return Decimal(str(quantity))
        
        qty_decimal = Decimal(str(quantity))
        adjusted = (qty_decimal / self.step_size).to_integral_value(rounding=ROUND_DOWN) * self.step_size
        return adjusted

    def _adjust_price(self, price: float) -> Decimal:
        """가격을 거래소 tick_size 배수로 반올림 처리.

        Args:
            price: 원래 가격

        Returns:
            정밀도가 보정된 가격 (Decimal - API 전달 시 str()로 변환 필요)
        """
        if self.tick_size is None:
            return Decimal(str(price))
        
        price_decimal = Decimal(str(price))
        adjusted = (price_decimal / self.tick_size).to_integral_value(rounding=ROUND_HALF_UP) * self.tick_size
        return adjusted

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
        can_trade, risk_reason = self.risk_manager.can_trade()
        if not can_trade:
            error_msg = f"거래 불가: {risk_reason}"
            self._log_audit("ORDER_REJECTED_RISK", {"side": side, "quantity": quantity, "reason": risk_reason})
            raise ValueError(error_msg)
        
        # StopLoss cooldown 체크 (포지션 진입만 차단, 청산은 허용)
        if abs(self.position.size) < 1e-12:  # 포지션이 없을 때만 체크 (진입 시도)
            in_cooldown, cooldown_reason = self.is_in_stoploss_cooldown()
            if in_cooldown:
                error_msg = f"거래 불가: {cooldown_reason}"
                self._log_audit("ORDER_REJECTED_STOPLOSS_COOLDOWN", {"side": side, "quantity": quantity, "reason": cooldown_reason})
                raise ValueError(error_msg)

        original_qty = quantity
        quantity = self._adjust_quantity(quantity)
        
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

        if self.min_qty is not None and Decimal(str(quantity)) < self.min_qty:
            error_msg = f"수량({quantity})이 최소 수량({self.min_qty})보다 작음"
            self._log_audit("ORDER_REJECTED_MIN_QTY", {"side": side, "quantity": quantity, "min_qty": str(self.min_qty)})
            raise ValueError(error_msg)

        valid, notional_msg = self._check_min_notional(quantity, price)
        if not valid:
            self._log_audit("ORDER_REJECTED_MIN_NOTIONAL", {
                "side": side,
                "quantity": quantity,
                "price": price or self._current_price,
                "reason": notional_msg,
            })
            raise ValueError(f"최소 주문 금액 미달: {notional_msg}")

        new_position_size = self.position.size + (float(quantity) if side == "BUY" else -float(quantity))

        is_reducing_order = abs(new_position_size) < abs(self.position.size) - 1e-12

        if not is_reducing_order:
            order_value = float(quantity) * self._current_price
            max_order_value = self.total_equity * float(self.leverage) * self.risk_manager.config.max_order_size
            print(f"🔍 주문 크기 검증: order_value=${order_value:.2f}, max_order_value=${max_order_value:.2f}, total_equity=${self.total_equity:.2f}, leverage={self.leverage}, max_order_size={self.risk_manager.config.max_order_size}")
            
            valid, msg = self.risk_manager.validate_order_size(
                float(quantity), self._current_price, self.total_equity, float(self.leverage)
            )
            if not valid:
                self._log_audit("ORDER_REJECTED_SIZE", {"side": side, "quantity": quantity, "reason": msg})
                raise ValueError(f"주문 크기 검증 실패: {msg}")

        if not is_reducing_order:
            valid, msg = self.risk_manager.validate_position_size(
                new_position_size, self._current_price, self.total_equity, float(self.leverage)
            )
            if not valid:
                self._log_audit("ORDER_REJECTED_POSITION", {"side": side, "quantity": quantity, "reason": msg})
                raise ValueError(f"포지션 크기 검증 실패: {msg}")

        snapshot_pos_size = self.position.size
        snapshot_entry_price = self.position.entry_price

        order_type = "MARKET" if price is None else "LIMIT"
        try:
            order_params: dict[str, Any] = {"type": order_type}
            if price is not None:
                order_params["price"] = str(price)
                order_params["timeInForce"] = "GTC"
            if is_reducing_order:
                order_params["reduceOnly"] = True

            response = await self.client.place_order(
                symbol=self.symbol,
                side=side,
                quantity=str(quantity),
                **order_params,
            )

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

            return response

        except Exception as e:
            self._log_audit("ORDER_FAILED", {
                "side": side,
                "quantity": quantity,
                "error": str(e),
            })
            raise

    async def _place_chase_order(
        self,
        side: str,
        quantity: float,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Chase Order 실행 - 지정가로 시도하고 미체결 시 가격을 추적하여 재주문.

        Args:
            side: BUY/SELL
            quantity: 수량
            reason: 주문 사유

        Returns:
            주문 응답 (모든 orderId 포함)
        """
        can_trade, risk_reason = self.risk_manager.can_trade()
        if not can_trade:
            error_msg = f"거래 불가: {risk_reason}"
            self._log_audit("ORDER_REJECTED_RISK", {"side": side, "quantity": quantity, "reason": risk_reason})
            raise ValueError(error_msg)

        # StopLoss cooldown 체크 (포지션 진입만 차단, 청산은 허용)
        if abs(self.position.size) < 1e-12:
            in_cooldown, cooldown_reason = self.is_in_stoploss_cooldown()
            if in_cooldown:
                error_msg = f"거래 불가: {cooldown_reason}"
                self._log_audit("ORDER_REJECTED_STOPLOSS_COOLDOWN", {"side": side, "quantity": quantity, "reason": cooldown_reason})
                raise ValueError(error_msg)

        original_qty = quantity
        quantity = self._adjust_quantity(quantity)
        
        initial_pos_size = self.position.size
        
        # 시작 시 포지션 확인: 이미 목표 포지션에 도달했는지 확인
        expected_pos_change = float(quantity) if side == "BUY" else -float(quantity)
        target_pos = initial_pos_size + expected_pos_change
        
        # 이미 충분한 포지션이 있는지 확인 (chase order가 이미 체결되었을 수 있음)
        # BUY인 경우: 현재 포지션이 목표 포지션 이상이면 이미 체결됨
        # SELL인 경우: 현재 포지션이 목표 포지션 이하이면 이미 체결됨
        if side == "BUY" and self.position.size >= target_pos - 1e-9:
            print(f"✅ Chase Order 이미 체결됨 (포지션 확인: {initial_pos_size:+.4f} → {self.position.size:+.4f}, 목표: {target_pos:+.4f})")
            return {
                "status": "ALREADY_FILLED",
                "_reason": reason,
                "_order_type": "CHASE_LIMIT",
                "_chase_attempts": 0,
                "_initial_pos_size": initial_pos_size,
                "_all_order_ids": [],
                "_chase_fills": [],
                "side": side,
                "executedQty": str(float(original_qty)),
            }
        elif side == "SELL" and self.position.size <= target_pos + 1e-9:
            print(f"✅ Chase Order 이미 체결됨 (포지션 확인: {initial_pos_size:+.4f} → {self.position.size:+.4f}, 목표: {target_pos:+.4f})")
            return {
                "status": "ALREADY_FILLED",
                "_reason": reason,
                "_order_type": "CHASE_LIMIT",
                "_chase_attempts": 0,
                "_initial_pos_size": initial_pos_size,
                "_all_order_ids": [],
                "_chase_fills": [],
                "side": side,
                "executedQty": str(float(original_qty)),
            }
        
        total_executed_qty = Decimal("0")
        last_response: dict[str, Any] | None = None
        
        chase_order_ids: list[int] = []
        chase_fills: list[dict[str, Any]] = []

        for attempt in range(self._chase_max_attempts):
            # 루프 중에도 포지션 확인 (이전 체크와 동일한 로직)
            if side == "BUY" and self.position.size >= target_pos - 1e-9:
                print(f"✅ Chase Order 이미 체결됨 (포지션 확인: {initial_pos_size:+.4f} → {self.position.size:+.4f}, 목표: {target_pos:+.4f})")
                if last_response:
                    last_response["_initial_pos_size"] = initial_pos_size
                    last_response["_all_order_ids"] = chase_order_ids
                    last_response["_chase_fills"] = chase_fills
                    last_response.setdefault("side", side)
                    last_response.setdefault("executedQty", str(float(original_qty)))
                    return last_response
                return {
                    "status": "FILLED",
                    "_reason": reason,
                    "_order_type": "CHASE_LIMIT",
                    "_chase_attempts": attempt,
                    "_initial_pos_size": initial_pos_size,
                    "_all_order_ids": chase_order_ids,
                    "_chase_fills": chase_fills,
                    "side": side,
                    "executedQty": str(float(original_qty)),
                }
            elif side == "SELL" and self.position.size <= target_pos + 1e-9:
                print(f"✅ Chase Order 이미 체결됨 (포지션 확인: {initial_pos_size:+.4f} → {self.position.size:+.4f}, 목표: {target_pos:+.4f})")
                if last_response:
                    last_response["_initial_pos_size"] = initial_pos_size
                    last_response["_all_order_ids"] = chase_order_ids
                    last_response["_chase_fills"] = chase_fills
                    last_response.setdefault("side", side)
                    last_response.setdefault("executedQty", str(float(original_qty)))
                    return last_response
                return {
                    "status": "FILLED",
                    "_reason": reason,
                    "_order_type": "CHASE_LIMIT",
                    "_chase_attempts": attempt,
                    "_initial_pos_size": initial_pos_size,
                    "_all_order_ids": chase_order_ids,
                    "_chase_fills": chase_fills,
                    "side": side,
                    "executedQty": str(float(original_qty)),
                }
            
            current_price = self._current_price
            if current_price <= 0:
                raise ValueError("현재 가격이 유효하지 않습니다")

            if self._best_bid is not None and self._best_ask is not None and self.tick_size is not None:
                if side == "BUY":
                    limit_price = self._best_ask - self.tick_size
                else:
                    limit_price = self._best_bid + self.tick_size
            else:
                slippage_mult = self._chase_slippage_bps / 10000.0
                if side == "BUY":
                    limit_price = self._adjust_price(current_price * (1 - slippage_mult))
                else:
                    limit_price = self._adjust_price(current_price * (1 + slippage_mult))

            self._log_audit("CHASE_ORDER_ATTEMPT", {
                "attempt": attempt + 1,
                "max_attempts": self._chase_max_attempts,
                "side": side,
                "quantity": quantity,
                "limit_price": limit_price,
                "current_price": current_price,
            })
            print(f"🎯 Chase Order 시도 {attempt + 1}/{self._chase_max_attempts}: {side} {quantity} @ {float(limit_price):,.2f} (현재가: {current_price:,.2f})")

            try:
                snapshot_pos_size = self.position.size
                snapshot_entry_price = self.position.entry_price
                new_position_size = self.position.size + (float(quantity) if side == "BUY" else -float(quantity))
                is_reducing_order = abs(new_position_size) < abs(self.position.size) - 1e-12

                order_params: dict[str, Any] = {
                    "type": "LIMIT",
                    "price": str(limit_price),
                    "timeInForce": "GTX",
                }
                if is_reducing_order:
                    order_params["reduceOnly"] = True

                response = await self.client.place_order(
                    symbol=self.symbol,
                    side=side,
                    quantity=str(quantity),
                    **order_params,
                )

                order_id = response.get("orderId")
                order_status = response.get("status")
                
                if order_id:
                    chase_order_ids.append(order_id)
                    chase_fills.append({
                        "order_id": order_id,
                        "attempt": attempt + 1,
                        "price": str(limit_price),
                        "qty": str(quantity),
                        "status": order_status,
                        "executed_qty": response.get("executedQty", "0"),
                    })

                if order_status == "FILLED":
                    response["_reason"] = reason
                    response["_snapshot_pos_size"] = snapshot_pos_size
                    response["_snapshot_entry_price"] = snapshot_entry_price
                    response["_chase_attempts"] = attempt + 1
                    response["_order_type"] = "CHASE_LIMIT"
                    response["_initial_pos_size"] = initial_pos_size
                    response["_all_order_ids"] = chase_order_ids
                    response["_chase_fills"] = chase_fills

                    self._log_audit("CHASE_ORDER_FILLED", {
                        "order_id": order_id,
                        "all_order_ids": chase_order_ids,
                        "attempts": attempt + 1,
                        "final_price": limit_price,
                        "total_fills": len(chase_fills),
                    })
                    print(f"✅ Chase Order 체결: {side} {quantity} @ {float(limit_price):,.2f} ({attempt + 1}번 시도, 총 {len(chase_order_ids)}개 주문)")
                    return response

                if order_status in ("NEW", "PARTIALLY_FILLED"):
                    await asyncio.sleep(self._chase_interval)

                    try:
                        order_info = await self.client.fetch_order(self.symbol, order_id)
                        current_status = order_info.get("status")
                        executed_qty = float(order_info.get("executedQty", 0))

                        if current_status == "FILLED":
                            order_info["_reason"] = reason
                            order_info["_snapshot_pos_size"] = snapshot_pos_size
                            order_info["_snapshot_entry_price"] = snapshot_entry_price
                            order_info["_chase_attempts"] = attempt + 1
                            order_info["_order_type"] = "CHASE_LIMIT"
                            order_info["_initial_pos_size"] = initial_pos_size
                            order_info["_all_order_ids"] = chase_order_ids
                            order_info["_chase_fills"] = chase_fills
                            print(f"✅ Chase Order 체결: {side} {quantity} @ {float(limit_price):,.2f} ({attempt + 1}번 시도, 총 {len(chase_order_ids)}개 주문)")
                            return order_info

                        if executed_qty > 0:
                            remaining_qty = float(quantity) - executed_qty
                            print(f"⚠️ 부분 체결: {executed_qty}/{quantity}, 남은 수량 {remaining_qty}")
                            quantity = self._adjust_quantity(remaining_qty)

                        await self.client.cancel_order(self.symbol, order_id)
                        self._log_audit("CHASE_ORDER_CANCELLED", {
                            "order_id": order_id,
                            "attempt": attempt + 1,
                            "reason": "price_moved",
                        })
                        print(f"🔄 Chase Order 취소 후 재시도: 가격 이동")

                    except Exception as e:
                        self._log_audit("CHASE_ORDER_CHECK_FAILED", {
                            "order_id": order_id,
                            "error": str(e),
                        })
                        try:
                            await self.client.cancel_order(self.symbol, order_id)
                        except Exception:
                            pass

                elif order_status == "EXPIRED":
                    self._log_audit("CHASE_ORDER_EXPIRED_GTX", {
                        "order_id": order_id,
                        "attempt": attempt + 1,
                        "reason": "would_be_taker",
                    })
                    print(f"⚠️ GTX 주문 거부 (Taker 방지): 가격 갱신 후 재시도")

            except Exception as e:
                self._log_audit("CHASE_ORDER_ERROR", {
                    "attempt": attempt + 1,
                    "error": str(e),
                })
                print(f"⚠️ Chase Order 에러: {e}")

        pos_change = abs(self.position.size - initial_pos_size)
        if pos_change >= float(original_qty) * 0.99:
            print(f"✅ Chase Order 이미 체결됨 (시장가 전환 전 확인: {initial_pos_size:.4f} → {self.position.size:.4f}, 총 {len(chase_order_ids)}개 주문)")
            return {
                "status": "FILLED",
                "_reason": reason,
                "_order_type": "CHASE_LIMIT",
                "_chase_attempts": self._chase_max_attempts,
                "_initial_pos_size": initial_pos_size,
                "_all_order_ids": chase_order_ids,
                "_chase_fills": chase_fills,
                "side": side,
                "executedQty": str(float(original_qty)),
            }
        
        remaining_qty_to_fill = float(original_qty) - pos_change
        if remaining_qty_to_fill < float(self.min_qty or Decimal("0.001")):
            print(f"✅ Chase Order 거의 체결됨 (남은 수량 무시: {remaining_qty_to_fill:.6f}, 총 {len(chase_order_ids)}개 주문)")
            return {
                "status": "FILLED",
                "_reason": reason,
                "_order_type": "CHASE_LIMIT",
                "_chase_attempts": self._chase_max_attempts,
                "_initial_pos_size": initial_pos_size,
                "_all_order_ids": chase_order_ids,
                "_chase_fills": chase_fills,
                "side": side,
                "executedQty": str(float(original_qty)),
            }
        
        if self._chase_fallback_to_market:
            print(f"🚨 Chase Order 실패 → 시장가로 전환 (남은 수량: {remaining_qty_to_fill:.4f}, 기존 {len(chase_order_ids)}개 주문)")
            self._log_audit("CHASE_ORDER_FALLBACK_MARKET", {
                "original_qty": original_qty,
                "remaining_qty": remaining_qty_to_fill,
                "position_change": pos_change,
                "chase_order_ids": chase_order_ids,
            })
            adjusted_remaining = self._adjust_quantity(remaining_qty_to_fill)
            if float(adjusted_remaining) < float(self.min_qty or Decimal("0.001")):
                print(f"✅ 남은 수량이 최소 수량 미만으로 시장가 전환 생략")
                return {
                    "status": "FILLED",
                    "_reason": reason,
                    "_order_type": "CHASE_LIMIT",
                    "_chase_attempts": self._chase_max_attempts,
                    "_initial_pos_size": initial_pos_size,
                    "_all_order_ids": chase_order_ids,
                    "_chase_fills": chase_fills,
                    "side": side,
                    "executedQty": str(float(original_qty)),
                }
            response = await self._place_order(side, float(adjusted_remaining), price=None, reason=reason)
            response["_initial_pos_size"] = initial_pos_size
            response["_all_order_ids"] = chase_order_ids + [response.get("orderId")]
            response["_chase_fills"] = chase_fills
            response["executedQty"] = str(float(original_qty))
            return response
        else:
            raise ValueError(f"Chase Order 실패: {self._chase_max_attempts}회 시도 후 미체결 (주문 IDs: {chase_order_ids})")

    def cancel_order(self, order_id: int) -> None:
        """주문 취소.

        Args:
            order_id: 주문 ID
        """
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
        """현재가(Last/Mark) 업데이트만 수행."""
        self._current_price = price
        if self.position.size != 0 and self.position.entry_price != 0:
            self.position.unrealized_pnl = self.position.size * (price - self.position.entry_price)

    def _get_candle_interval_seconds(self) -> int:
        """캔들 간격을 초 단위로 반환.
        
        Returns:
            캔들 간격 (초)
        """
        interval_str = self.candle_interval.lower()
        if interval_str.endswith("m"):
            return int(interval_str[:-1]) * 60
        elif interval_str.endswith("h"):
            return int(interval_str[:-1]) * 3600
        elif interval_str.endswith("d"):
            return int(interval_str[:-1]) * 86400
        else:
            # 기본값: 5분
            return 300
    
    def is_in_stoploss_cooldown(self, bar_timestamp: int | None = None) -> tuple[bool, str]:
        """StopLoss cooldown 중인지 확인.
        
        Args:
            bar_timestamp: 현재 봉의 timestamp (None이면 마지막 봉 timestamp 사용)
            
        Returns:
            (cooldown 중 여부, 사유)
        """
        if self.risk_manager.config.stoploss_cooldown_candles <= 0:
            return False, ""
        
        if self._stoploss_cooldown_until_bar_timestamp is None:
            return False, ""
        
        check_timestamp = bar_timestamp if bar_timestamp is not None else self._last_bar_timestamp
        if check_timestamp is None:
            # 봉 timestamp가 없으면 cooldown이 활성화되어 있으면 True 반환
            return True, "StopLoss cooldown 중 (봉 timestamp 없음)"
        
        if check_timestamp < self._stoploss_cooldown_until_bar_timestamp:
            remaining_candles = (self._stoploss_cooldown_until_bar_timestamp - check_timestamp) // (self._get_candle_interval_seconds() * 1000)
            return True, f"StopLoss cooldown 중 (남은 캔들: 약 {remaining_candles}개)"
        
        # cooldown 종료
        if self._stoploss_cooldown_until_bar_timestamp > 0:
            print(f"✅ StopLoss cooldown 종료, 거래 재개 가능")
            self._stoploss_cooldown_until_bar_timestamp = None
        
        return False, ""
    
    def on_new_bar(self, bar_timestamp: int) -> None:
        """새 봉이 시작될 때 호출 (cooldown 업데이트용).
        
        Args:
            bar_timestamp: 새 봉의 timestamp
        """
        self._last_bar_timestamp = bar_timestamp
        
        # cooldown 종료 확인
        if self._stoploss_cooldown_until_bar_timestamp is not None:
            if bar_timestamp >= self._stoploss_cooldown_until_bar_timestamp:
                cooldown_candles = self.risk_manager.config.stoploss_cooldown_candles
                
                # 시스템 로그 출력
                self._logger.info(
                    f"STOPLOSS_COOLDOWN_ENDED | symbol={self.symbol}, bar_timestamp={bar_timestamp}, "
                    f"cooldown_candles={cooldown_candles}"
                )
                
                print(f"✅ StopLoss cooldown 종료, 거래 재개 가능")
                self._stoploss_cooldown_until_bar_timestamp = None
                
                self._log_audit("STOPLOSS_COOLDOWN_ENDED", {
                    "bar_timestamp": bar_timestamp,
                    "cooldown_candles": cooldown_candles,
                })
                
                # Slack 알림 전송
                if self.notifier:
                    cooldown_text = (
                        f"*✅ StopLoss Cooldown 종료* ({self.env}) {self.symbol}\n"
                        f"- 거래 재개 가능\n"
                        f"- Cooldown 기간: {cooldown_candles}개 캔들 완료"
                    )
                    asyncio.create_task(self._send_notification_safe(cooldown_text, "good"))

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
