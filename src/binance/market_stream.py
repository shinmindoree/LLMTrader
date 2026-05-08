"""바이낸스 퓨처스 마켓 스트림.

- ``BinanceMarketStream``: Kline 데이터를 공급한다. 본래 WebSocket
  ``wss://fstream.binance.com/ws/<symbol>@kline_<interval>`` 를 구독했으나,
  최근 Binance Futures 의 시간 기반 집계 스트림(``kline_*``, ``markPrice``,
  ``continuousKline_*``)이 우리 IP 에서 무응답 상태가 되어 라이브 잡이
  새 봉을 받지 못하는 문제가 발생했다. 이벤트 기반 스트림(``bookTicker``,
  ``trade``)은 정상이며, REST ``/fapi/v1/klines`` 도 정상 동작한다.
  따라서 이 클래스는 동일한 콜백 인터페이스를 유지하면서 내부적으로
  REST 폴링을 통해 합성된 kline 이벤트를 송출한다.
- ``BinanceBookTickerStream``: best bid/ask 스트림. 정상 동작하므로
  기존 WebSocket 구현을 유지한다.
"""

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable

import aiohttp


class BinanceMarketStream:
    """Kline 데이터 공급자 (REST 폴링 기반).

    인터페이스는 기존 WebSocket 구현과 호환된다 — 콜백에는
    ``{"e": "kline", "E": <event_ms>, "s": <SYMBOL>, "k": {...}}`` 형태의
    딕셔너리가 전달되며, 종가가 확정된 봉에서는 ``k.x = True`` 가 된다.
    """

    def __init__(
        self,
        symbol: str,
        interval: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        testnet: bool = False,
    ) -> None:
        """마켓 스트림 초기화.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)
            interval: 캔들 간격 (예: 1m, 5m, 15m)
            callback: kline 이벤트 수신 시 호출될 비동기 콜백
            testnet: 테스트넷 사용 여부
        """
        self.symbol = symbol.upper()
        self.interval = interval
        self.callback = callback
        self.testnet = testnet
        # REST 베이스 URL (REST 만 사용하므로 ws base_url 은 불필요).
        self.rest_base_url = (
            "https://testnet.binancefuture.com/fapi/v1"
            if testnet
            else "https://fapi.binance.com/fapi/v1"
        )
        # 로깅 호환을 위해 stream_name 유지.
        self.stream_name = f"{self.symbol.lower()}@kline_{self.interval}"
        self.running = False
        self._session: aiohttp.ClientSession | None = None
        # 폴링 주기 (초). 환경변수로 조정 가능. 기본 10초.
        try:
            self._poll_interval_sec = max(
                1.0, float(os.environ.get("BINANCE_KLINE_POLL_SEC", "10"))
            )
        except ValueError:
            self._poll_interval_sec = 10.0
        # 이미 콜백으로 송출한 마지막 "확정 봉" 의 open_time(ms).
        self._last_emitted_close_open_time: int | None = None

    async def start(self) -> None:
        """REST 폴링 루프 시작 (자동 재시도 포함)."""
        self.running = True
        url = f"{self.rest_base_url}/klines"
        params = {"symbol": self.symbol, "interval": self.interval, "limit": 2}
        print(
            f"⚡ Market Stream(REST poll) 시작: {self.stream_name} "
            f"every {self._poll_interval_sec:.1f}s ("
            f"{'테스트넷' if self.testnet else '라이브'})",
            flush=True,
        )
        last_log_sec = 0.0
        poll_count = 0
        consecutive_errors = 0

        self._session = aiohttp.ClientSession()
        try:
            while self.running:
                try:
                    async with self._session.get(
                        url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status != 200:
                            body = (await resp.text())[:300]
                            consecutive_errors += 1
                            print(
                                f"⚠️ Market Stream(REST) HTTP {resp.status} "
                                f"({consecutive_errors}회 연속): {body}",
                                flush=True,
                            )
                        else:
                            consecutive_errors = 0
                            klines = await resp.json()
                            await self._emit_klines(klines)
                            poll_count += 1
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    consecutive_errors += 1
                    if self.running:
                        print(
                            f"⚠️ Market Stream(REST) 폴링 오류 "
                            f"({consecutive_errors}회 연속): {exc!r}",
                            flush=True,
                        )

                # 60초마다 헬스 로그.
                now_sec = time.time()
                if now_sec - last_log_sec >= 60.0:
                    last_log_sec = now_sec
                    print(
                        f"[market_stream] {self.stream_name} REST polls={poll_count} "
                        f"last_closed_open_t={self._last_emitted_close_open_time} "
                        f"errors={consecutive_errors}",
                        flush=True,
                    )

                # 연속 실패 시 백오프, 그 외엔 정상 폴링 주기.
                sleep_sec = self._poll_interval_sec
                if consecutive_errors > 0:
                    sleep_sec = min(60.0, self._poll_interval_sec * (1 + consecutive_errors))
                try:
                    await asyncio.sleep(sleep_sec)
                except asyncio.CancelledError:
                    break
        except asyncio.CancelledError:
            print("⚠️ Market Stream(REST) 취소됨", flush=True)
        finally:
            if self._session:
                await self._session.close()
                self._session = None

    async def _emit_klines(self, klines: Any) -> None:
        """REST 응답을 합성된 kline 이벤트로 변환해 콜백 송출."""
        if not isinstance(klines, list) or not klines:
            return

        now_ms = int(time.time() * 1000)
        # Binance 응답: [[open_time, o, h, l, c, v, close_time, ...], ...]
        # 보통 limit=2 → [방금 닫힌 봉, 진행 중인 봉]. 하나만 올 수도 있음.
        try:
            in_progress = klines[-1]
            closed = klines[-2] if len(klines) >= 2 else None
        except Exception:  # noqa: BLE001
            return

        # 1) 새로 닫힌 봉이 있으면 한 번만 x=True 이벤트 송출.
        if closed is not None:
            try:
                closed_open_t = int(closed[0])
            except (TypeError, ValueError):
                closed_open_t = None
            if (
                closed_open_t is not None
                and (
                    self._last_emitted_close_open_time is None
                    or closed_open_t > self._last_emitted_close_open_time
                )
            ):
                self._last_emitted_close_open_time = closed_open_t
                payload = self._build_kline_payload(closed, now_ms, is_closed=True)
                if payload is not None:
                    try:
                        await self.callback(payload)
                    except Exception as exc:  # noqa: BLE001
                        print(
                            f"⚠️ Market Stream(REST) 콜백 오류(closed): {exc!r}",
                            flush=True,
                        )

        # 2) 진행 중인 봉은 매 폴링마다 x=False 로 송출 → mark_price 갱신.
        payload = self._build_kline_payload(in_progress, now_ms, is_closed=False)
        if payload is not None:
            try:
                await self.callback(payload)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"⚠️ Market Stream(REST) 콜백 오류(in-progress): {exc!r}",
                    flush=True,
                )

    def _build_kline_payload(
        self,
        kline: Any,
        now_ms: int,
        *,
        is_closed: bool,
    ) -> dict[str, Any] | None:
        """REST kline 배열을 WebSocket 형태의 이벤트로 변환."""
        try:
            open_t = int(kline[0])
            close_t = int(kline[6])
            o = str(kline[1])
            h = str(kline[2])
            l = str(kline[3])
            c = str(kline[4])
            v = str(kline[5])
        except (TypeError, ValueError, IndexError) as exc:
            print(f"⚠️ Market Stream(REST) kline 파싱 오류: {exc!r}", flush=True)
            return None
        return {
            "e": "kline",
            "E": now_ms,
            "s": self.symbol,
            "k": {
                "t": open_t,
                "T": close_t,
                "s": self.symbol,
                "i": self.interval,
                "o": o,
                "c": c,
                "h": h,
                "l": l,
                "v": v,
                "x": is_closed,
            },
        }

    async def stop(self) -> None:
        """폴링 루프 중지."""
        self.running = False
        if self._session:
            await self._session.close()
            self._session = None


class BinanceBookTickerStream:
    """바이낸스 퓨처스 Best Bid/Ask 실시간 스트림."""

    def __init__(
        self,
        symbol: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        testnet: bool = False,
    ) -> None:
        """BookTicker 스트림 초기화.

        Args:
            symbol: 거래 심볼 (예: BTCUSDT)
            callback: 웹소켓 메시지 수신 시 호출될 콜백 함수
                      수신 데이터: {"b": "best_bid", "a": "best_ask", ...}
            testnet: 테스트넷 사용 여부
        """
        self.symbol = symbol.lower()
        self.callback = callback
        self.testnet = testnet
        self.base_url = (
            "wss://stream.binancefuture.com/ws"
            if testnet
            else "wss://fstream.binance.com/ws"
        )
        self.stream_name = f"{self.symbol}@bookTicker"
        self.running = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """웹소켓 스트림 시작 (자동 재연결 포함)."""
        self.running = True
        url = f"{self.base_url}/{self.stream_name}"

        while self.running:
            try:
                self._session = aiohttp.ClientSession()
                async with self._session.ws_connect(url) as ws:
                    self._ws = ws
                    print(f"⚡ BookTicker Stream 연결됨: {self.stream_name} ({'테스트넷' if self.testnet else '라이브'})")

                    async for msg in ws:
                        if not self.running:
                            break

                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                await self.callback(data)
                            except Exception as e:  # noqa: BLE001
                                print(f"⚠️ BookTicker Stream 메시지 처리 오류: {e}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            print(f"⚠️ BookTicker Stream 오류: {msg.data}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSE:
                            print("⚠️ BookTicker Stream 연결 종료됨")
                            break

            except asyncio.CancelledError:
                print("⚠️ BookTicker Stream 취소됨")
                break
            except Exception as e:  # noqa: BLE001
                if self.running:
                    print(f"⚠️ BookTicker Stream 재연결 대기 중: {e}")
                    await asyncio.sleep(5)
                else:
                    break
            finally:
                if self._session:
                    await self._session.close()
                    self._session = None
                self._ws = None

    async def stop(self) -> None:
        """웹소켓 스트림 중지."""
        self.running = False
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()
            self._session = None


