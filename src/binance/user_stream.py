"""바이낸스 퓨처스 유저데이터 웹소켓 스트림."""

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

import aiohttp

from binance.client import BinanceHTTPClient


class BinanceUserStream:
    """바이낸스 퓨처스 유저데이터 스트림 클라이언트."""

    def __init__(
        self,
        client: BinanceHTTPClient,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        testnet: bool = False,
        keepalive_interval: float = 25 * 60.0,
        on_disconnect: Callable[[], Awaitable[None]] | None = None,
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """유저데이터 스트림 초기화.

        Args:
            client: 바이낸스 HTTP 클라이언트
            callback: 웹소켓 메시지 수신 시 호출될 콜백 함수
            testnet: 테스트넷 사용 여부
            keepalive_interval: listenKey 갱신 주기(초)
            on_disconnect: 연결 끊김 시 호출될 콜백 (REST 폴백 트리거용)
            on_reconnect: 재연결 시 호출될 콜백 (누락 거래 보정용)
        """
        self.client = client
        self.callback = callback
        self.testnet = testnet
        self.keepalive_interval = keepalive_interval
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect
        self.base_url = (
            "wss://stream.binancefuture.com/ws"
            if testnet
            else "wss://fstream.binance.com/ws"
        )
        self.running = False
        self._listen_key: str | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._healthcheck_task: asyncio.Task | None = None
        
        self._connected: bool = False
        self._last_message_time: float = 0.0
        self._connection_count: int = 0
        self._disconnect_count: int = 0
        self._healthcheck_interval: float = 5
        self._message_timeout: float = 60
        self._is_actual_disconnect: bool = False

    @property
    def is_connected(self) -> bool:
        """현재 연결 상태 반환."""
        return self._connected and self._ws is not None

    @property
    def last_message_age(self) -> float:
        """마지막 메시지 수신 이후 경과 시간(초)."""
        if self._last_message_time == 0:
            return float("inf")
        return time.time() - self._last_message_time

    @property
    def stats(self) -> dict[str, Any]:
        """연결 통계 반환."""
        return {
            "connected": self._connected,
            "connection_count": self._connection_count,
            "disconnect_count": self._disconnect_count,
            "last_message_age": self.last_message_age,
        }

    async def start(self) -> None:
        """유저데이터 스트림 시작 (자동 재연결 포함)."""
        self.running = True
        is_first_connect = True

        while self.running:
            reconnect = False
            was_connected = self._connected
            self._is_actual_disconnect = False
            try:
                self._listen_key = await self.client.create_listen_key()
                self._keepalive_task = asyncio.create_task(self._keepalive_loop())

                url = f"{self.base_url}/{self._listen_key}"
                self._session = aiohttp.ClientSession()
                async with self._session.ws_connect(url, heartbeat=30) as ws:
                    self._ws = ws
                    self._connected = True
                    self._connection_count += 1
                    self._last_message_time = time.time()
                    
                    if is_first_connect:
                        print("✅ User Stream 연결됨")
                        is_first_connect = False
                    else:
                        print(f"🔄 User Stream 재연결됨 (연결 #{self._connection_count})")
                        if self.on_reconnect:
                            try:
                                await self.on_reconnect()
                            except Exception as e:  # noqa: BLE001
                                print(f"⚠️ on_reconnect 콜백 오류: {e}")

                    self._healthcheck_task = asyncio.create_task(self._healthcheck_loop())

                    async for msg in ws:
                        if not self.running:
                            break

                        self._last_message_time = time.time()

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except Exception:  # noqa: BLE001
                                continue

                            if data.get("e") == "listenKeyExpired":
                                print("⚠️ User Stream listenKey 만료")
                                self._is_actual_disconnect = True
                                reconnect = True
                                break

                            await self.callback(data)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"⚠️ User Stream 오류: {msg.data}")
                            self._is_actual_disconnect = True
                            reconnect = True
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSE:
                            print("⚠️ User Stream 연결 종료됨")
                            self._is_actual_disconnect = True
                            reconnect = True
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                if self.running:
                    print(f"⚠️ User Stream 오류 발생, 재연결 예정: {exc}")
                    self._is_actual_disconnect = True
                    reconnect = True
                else:
                    break
            finally:
                if was_connected or self._connected:
                    self._connected = False
                    self._disconnect_count += 1
                    
                    # 실제 연결 끊김인 경우에만 로그 출력
                    if self._is_actual_disconnect:
                        print(f"📡 User Stream 연결 끊김 (끊김 #{self._disconnect_count})")
                    
                    if self.on_disconnect:
                        try:
                            await self.on_disconnect()
                        except Exception as e:  # noqa: BLE001
                            print(f"⚠️ on_disconnect 콜백 오류: {e}")
                
                await self._stop_healthcheck()
                await self._stop_keepalive()
                if self._session:
                    await self._session.close()
                    self._session = None
                self._ws = None

            if self.running and reconnect:
                wait_time = min(5 * (1 + self._disconnect_count % 5), 30)
                print(f"⏳ {wait_time}초 후 재연결 시도...")
                await asyncio.sleep(wait_time)

        await self._close_listen_key()

    async def stop(self) -> None:
        """유저데이터 스트림 중지."""
        self.running = False
        self._connected = False
        await self._stop_healthcheck()
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None
        await self._stop_keepalive()
        await self._close_listen_key()

    async def _healthcheck_loop(self) -> None:
        """연결 상태 헬스체크 루프 - WebSocket 상태 확인 + 메시지 타임아웃 (하이브리드)."""
        while self.running and self._connected:
            await asyncio.sleep(self._healthcheck_interval)
            if not self.running or not self._connected:
                break
            
            reconnect_needed = False
            reason = ""
            is_actual_disconnect = False
            
            # 방법 2: WebSocket 연결 상태 직접 확인 (우선) - 실제 연결 끊김
            try:
                if self._ws is None:
                    reconnect_needed = True
                    is_actual_disconnect = True
                    reason = "WebSocket 객체가 None"
                elif self._ws.closed:
                    reconnect_needed = True
                    is_actual_disconnect = True
                    reason = "WebSocket 연결이 닫힘"
                elif self._ws.exception() is not None:
                    reconnect_needed = True
                    is_actual_disconnect = True
                    reason = f"WebSocket 예외 발생: {self._ws.exception()}"
            except Exception as e:
                reconnect_needed = True
                is_actual_disconnect = True
                reason = f"WebSocket 상태 확인 실패: {e}"
            
            # 방법 1: 메시지 타임아웃 확인 (백업) - 거래 없어서 메시지 없는 경우도 포함
            if not reconnect_needed and self.last_message_age > self._message_timeout:
                # 메시지 타임아웃인 경우, WebSocket 연결 상태를 다시 한 번 확인
                # 실제로 연결이 끊겼는지 확인 (거래 없어서 메시지 없는 정상 상태와 구분)
                try:
                    if self._ws is None or self._ws.closed or self._ws.exception() is not None:
                        reconnect_needed = True
                        is_actual_disconnect = True
                        reason = f"WebSocket 연결 끊김 감지 ({self.last_message_age:.1f}초간 메시지 없음)"
                    else:
                        # WebSocket은 정상인데 메시지만 없는 경우 (거래 없는 정상 상태)
                        # 조용히 재연결만 수행 (로그 출력 안 함)
                        reconnect_needed = True
                        is_actual_disconnect = False
                except Exception:
                    # 상태 확인 실패 시 안전하게 재연결
                    reconnect_needed = True
                    is_actual_disconnect = True
                    reason = f"상태 확인 실패 ({self.last_message_age:.1f}초간 메시지 없음)"
            
            if reconnect_needed:
                # 실제 연결 끊김 여부 저장 (start() 메서드에서 로그 출력용)
                self._is_actual_disconnect = is_actual_disconnect
                
                # 실제 연결 끊김인 경우에만 로그 출력
                if is_actual_disconnect:
                    print(f"⚠️ User Stream 헬스체크 실패: {reason}")
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                break

    async def _stop_healthcheck(self) -> None:
        """헬스체크 태스크 중지."""
        if not self._healthcheck_task:
            return
        self._healthcheck_task.cancel()
        try:
            await self._healthcheck_task
        except asyncio.CancelledError:
            pass
        self._healthcheck_task = None

    async def _keepalive_loop(self) -> None:
        """listenKey keepalive 루프 (제한적 재시도 + 지수 백오프)."""
        while self.running and self._listen_key:
            await asyncio.sleep(self.keepalive_interval)
            if not self.running or not self._listen_key:
                break
            
            # 제한적 재시도 (최대 3회) + 지수 백오프
            max_retries = 3
            success = False
            for attempt in range(max_retries):
                try:
                    await self.client.keepalive_listen_key(self._listen_key)
                    success = True
                    break  # 성공 시 루프 종료
                except Exception as exc:  # noqa: BLE001
                    if attempt < max_retries - 1:
                        # 지수 백오프: 1분, 2분, 4분 (최대 5분)
                        backoff_seconds = min(60 * (2 ** attempt), 300)
                        print(
                            f"User Stream keepalive failed (attempt {attempt + 1}/{max_retries}): {exc}. "
                            f"Retrying in {backoff_seconds}s..."
                        )
                        await asyncio.sleep(backoff_seconds)
                    else:
                        # 최종 실패: 재연결은 start() 메서드의 자동 재연결 로직이 처리
                        print(
                            f"User Stream keepalive failed after {max_retries} attempts: {exc}. "
                            f"Will reconnect on next listenKey expiration."
                        )
            
            # keepalive 실패 시 listenKey를 None으로 설정하여 재연결 트리거
            if not success:
                # start() 메서드의 재연결 로직이 새로운 listenKey를 생성하도록 함
                # 현재 listenKey는 만료될 것이므로 None으로 설정하지 않고 그대로 둠
                pass

    async def _stop_keepalive(self) -> None:
        if not self._keepalive_task:
            return
        self._keepalive_task.cancel()
        try:
            await self._keepalive_task
        except asyncio.CancelledError:
            pass
        self._keepalive_task = None

    async def _close_listen_key(self) -> None:
        if not self._listen_key:
            return
        try:
            await self.client.close_listen_key(self._listen_key)
        except Exception:  # noqa: BLE001
            pass
        self._listen_key = None
