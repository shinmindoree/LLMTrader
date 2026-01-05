"""실시간 가격 피드 (라이브 트레이딩 전용).

WebSocket 기반으로:
- Kline Stream을 통해 실시간 캔들 데이터를 수신
- 마지막 닫힌 캔들(bar_close)과 현재가를 제공
"""

from typing import Any, Callable

from binance.client import BinanceHTTPClient
from binance.market_stream import BinanceMarketStream


class PriceFeed:
    """실시간 가격 피드 (WebSocket 기반)."""

    def __init__(
        self,
        client: BinanceHTTPClient,
        symbol: str,
        candle_interval: str = "1m",
    ) -> None:
        """가격 피드 초기화.

        Args:
            client: 바이낸스 HTTP 클라이언트 (REST API용 및 testnet 판단용)
            symbol: 심볼 (예: BTCUSDT)
            candle_interval: 캔들 봉 간격 (예: '1m', '5m', '15m', '1h')
        """
        self.client = client
        self.symbol = symbol
        self.candle_interval = candle_interval
        self._running = False
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._last_price: float = 0.0
        self._last_emitted_timestamp: int | None = None
        self._last_emitted_close: float = 0.0
        self._stream: BinanceMarketStream | None = None

    @property
    def last_price(self) -> float:
        """마지막 가격."""
        return self._last_price

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """가격 업데이트 콜백 등록.

        Args:
            callback: 가격 업데이트 시 호출될 함수 (인자: tick 데이터)
        """
        self._callbacks.append(callback)

    async def fetch_closed_closes(self, limit: int = 200) -> list[tuple[int, float]]:
        """최근 캔들 종가 히스토리(닫힌 봉) 조회.

        RSI/MA 등 지표가 시작부터 의미 있게 나오도록 price_history를 시딩(seed)할 때 사용.

        Returns:
            (timestamp_ms, close) 리스트. timestamp는 kline open time.
        """
        klines = await self.client.fetch_klines(
            symbol=self.symbol, interval=self.candle_interval, limit=limit + 1
        )
        if not klines:
            return []

        # 일반적으로 마지막 원소는 진행 중인 현재 봉일 수 있으므로 제외(닫힌 봉만 사용)
        closed = klines[:-1] if len(klines) > 1 else klines
        out: list[tuple[int, float]] = []
        for k in closed:
            try:
                ts = int(k[0])
                close = float(k[4])
            except Exception:  # noqa: BLE001
                continue
            out.append((ts, close))
        return out

    async def _handle_websocket_message(self, data: dict[str, Any]) -> None:
        """웹소켓 메시지 처리.

        Args:
            data: 웹소켓으로부터 수신한 JSON 데이터
                - 단일 스트림: {"e": "kline", "k": {...}}
                - 스트림 이름 포함: {"stream": "...", "data": {"e": "kline", "k": {...}}}
        """
        try:
            # 바이낸스 Kline Stream 형식 처리
            # 스트림 이름이 있는 경우: {"stream": "...", "data": {...}}
            # 단일 스트림인 경우: {"e": "kline", "k": {...}}
            if "data" in data:
                kline_data = data["data"]
            elif "e" in data:
                kline_data = data
            else:
                return  # 알 수 없는 형식

            # kline 이벤트 확인
            if kline_data.get("e") != "kline":
                return

            k = kline_data.get("k", {})
            if not k:
                return

            # Kline 데이터 파싱
            try:
                bar_ts = int(k["t"])  # Kline Open Time (ms)
                bar_close = float(k["c"])  # Close Price
                current_price = float(k["c"])  # 현재가 = close price
                is_closed = bool(k["x"])  # Is this kline closed?
                volume = float(k.get("v", 0))  # Volume
            except (KeyError, ValueError, TypeError) as e:
                print(f"⚠️ PriceFeed: Kline 데이터 파싱 오류: {e}")
                return

            # bar_ts가 과거로 되돌아가는 경우(노드/캐시 흔들림) 마지막 값으로 고정
            if self._last_emitted_timestamp is not None and bar_ts < self._last_emitted_timestamp:
                bar_ts = self._last_emitted_timestamp
                bar_close = self._last_emitted_close

            self._last_price = current_price

            # is_new_bar: 봉이 막 닫혔을 때만 True
            is_new_bar = is_closed and (
                self._last_emitted_timestamp is None or self._last_emitted_timestamp != bar_ts
            )

            if is_new_bar:
                self._last_emitted_timestamp = bar_ts
                self._last_emitted_close = bar_close

            # tick 데이터 생성
            tick = {
                "timestamp": bar_ts,  # Kline Open Time을 timestamp로 사용
                "bar_timestamp": bar_ts,
                "bar_close": bar_close,
                "price": current_price,
                "volume": volume,
                "is_new_bar": is_new_bar,
            }

            # 콜백 호출
            for callback in self._callbacks:
                callback(tick)

        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ PriceFeed: 웹소켓 메시지 처리 오류: {exc}")

    async def start(self) -> None:
        """가격 피드 시작 (WebSocket 스트림 시작)."""
        self._running = True

        # testnet 여부 판단 (base_url에서)
        is_testnet = "testnet" in self.client.base_url.lower()

        # WebSocket 스트림 생성 및 시작
        self._stream = BinanceMarketStream(
            symbol=self.symbol,
            interval=self.candle_interval,
            callback=self._handle_websocket_message,
            testnet=is_testnet,
        )

        try:
            await self._stream.start()
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ PriceFeed: 스트림 시작 오류: {exc}")
            raise
        finally:
            self._running = False

    async def stop(self) -> None:
        """가격 피드 중지."""
        self._running = False
        if self._stream:
            await self._stream.stop()
