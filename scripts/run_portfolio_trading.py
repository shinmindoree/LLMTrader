"""멀티 전략 포트폴리오 라이브 트레이딩 실행 스크립트.

최대 5개의 전략 슬롯(각각 독립된 전략+스트림)을 동시에 실행합니다.
심볼은 슬롯 간 중복 불가 — 같은 심볼의 멀티 타임프레임은 하나의 슬롯 내에서 처리합니다.

사용법:
    uv run python scripts/run_portfolio_trading.py --config portfolio.json --yes
    uv run python scripts/run_portfolio_trading.py \\
        --slots '[{"strategy":"rsi_long_short_strategy.py","streams":[{"symbol":"BTCUSDT","interval":"1m","leverage":5}]}]' \\
        --yes
"""

import asyncio
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import typer

project_root = Path(__file__).parent.parent
src_path = project_root / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from binance.client import BinanceHTTPClient
from common.risk import RiskConfig
from live.context import LiveContext
from live.indicator_context import CandleStreamIndicatorContext
from live.portfolio_context import PortfolioContext
from live.portfolio_engine import PortfolioLiveTradingEngine
from live.price_feed import PriceFeed
from live.risk import LiveRiskManager
from live.user_stream_hub import UserStreamHub
from notifications.slack import SlackNotifier
from settings import get_settings

app = typer.Typer(add_completion=False)
MAX_SLOTS = 5
MAX_TOTAL_STREAMS = 5


# ---------------------------------------------------------------------------
# UserStreamHub proxy — 개별 엔진이 공유 허브를 start/stop하지 않도록 차단
# ---------------------------------------------------------------------------

class _NonOwningStreamHub:
    def __init__(self, hub: UserStreamHub) -> None:
        self._hub = hub

    def register_handler(self, handler):  # type: ignore[no-untyped-def]
        self._hub.register_handler(handler)

    def register_disconnect_handler(self, handler):  # type: ignore[no-untyped-def]
        self._hub.register_disconnect_handler(handler)

    def register_reconnect_handler(self, handler):  # type: ignore[no-untyped-def]
        self._hub.register_reconnect_handler(handler)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _load_strategy_class(strategy_file: Path, slot_index: int):  # type: ignore[no-untyped-def]
    module_name = f"portfolio_slot_{slot_index}_{strategy_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, strategy_file)
    if not spec or not spec.loader:
        raise ValueError(f"전략 파일을 로드할 수 없습니다: {strategy_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
            print(f"  🧩 전략 클래스 로드됨: {name} (파일: {strategy_file})")
            return obj
    raise ValueError(f"전략 클래스를 찾을 수 없습니다: {strategy_file}")


def _resolve_strategy_path(raw: str) -> Path:
    p = Path(raw)
    if p.exists():
        return p.resolve()
    candidate = (project_root / raw).resolve()
    if candidate.exists():
        return candidate
    candidate2 = (project_root / "scripts/strategies" / p.name).resolve()
    if candidate2.exists():
        return candidate2
    raise typer.BadParameter(f"전략 파일을 찾을 수 없습니다: {raw}")


def _build_strategy(strategy_class: type, params: dict[str, Any]) -> Any:
    if not params:
        return strategy_class()
    return strategy_class(**params)


def _parse_interval_seconds(interval: str) -> int:
    s = interval.strip().lower()
    if not s:
        return 0
    try:
        if s.endswith("m"):
            return int(s[:-1]) * 60
        if s.endswith("h"):
            return int(s[:-1]) * 3600
        if s.endswith("d"):
            return int(s[:-1]) * 86400
        if s.endswith("w"):
            return int(s[:-1]) * 7 * 86400
    except ValueError:
        return 0
    return 0


def _normalize_stream_configs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        symbol = str(it.get("symbol") or it.get("s") or "").strip().upper()
        interval = str(
            it.get("interval") or it.get("candle_interval") or it.get("timeframe") or ""
        ).strip()
        if not symbol or not interval:
            continue
        cfg: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "leverage": int(it.get("leverage", 1)),
            "max_position": float(it.get("max_position", it.get("max_position_size", 0.5))),
            "daily_loss_limit": float(it.get("daily_loss_limit", 500.0)),
            "max_consecutive_losses": int(it.get("max_consecutive_losses", 0)),
            "stoploss_cooldown_candles": int(it.get("stoploss_cooldown_candles", 0)),
            "stop_loss_pct": float(it.get("stop_loss_pct", 0.05)),
            "max_pyramid_entries": int(it.get("max_pyramid_entries", 0)),
        }
        out.append(cfg)

    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for cfg in out:
        key = (str(cfg["symbol"]), str(cfg["interval"]))
        if key not in seen:
            seen.add(key)
            deduped.append(cfg)
    return deduped


def _extract_strategy_stream_config(strategy: Any) -> list[dict[str, Any]]:
    streams: list[dict[str, Any]] = []
    meta = getattr(strategy, "meta", None)
    if isinstance(meta, dict):
        meta_streams = meta.get("streams") or meta.get("candle_streams")
        if isinstance(meta_streams, list):
            for item in meta_streams:
                if isinstance(item, dict):
                    streams.append(dict(item))
    for attr_name in ("streams", "candle_streams"):
        attr = getattr(strategy, attr_name, None)
        if isinstance(attr, list):
            for item in attr:
                if isinstance(item, dict):
                    streams.append(dict(item))
    return streams


# ---------------------------------------------------------------------------
# Per-slot engine builder
# ---------------------------------------------------------------------------

def _build_slot_engine(
    *,
    slot_index: int,
    strategy: Any,
    stream_configs: list[dict[str, Any]],
    client: BinanceHTTPClient,
    notifier: SlackNotifier | None,
    hub_proxy: _NonOwningStreamHub,
    indicator_config: dict[str, Any],
    log_interval: int | None,
    settings: Any,
) -> PortfolioLiveTradingEngine:
    normalized_streams = [(str(cfg["symbol"]), str(cfg["interval"])) for cfg in stream_configs]

    symbol_settings: dict[str, dict[str, Any]] = {}
    for cfg in stream_configs:
        sym = str(cfg["symbol"])
        if sym not in symbol_settings:
            symbol_settings[sym] = cfg
    symbols = sorted(symbol_settings.keys())

    portfolio_risk_config = RiskConfig(
        max_leverage=max(float(s["leverage"]) for s in symbol_settings.values()),
        max_position_size=max(float(s["max_position"]) for s in symbol_settings.values()),
        max_order_size=max(float(s["max_position"]) for s in symbol_settings.values()),
        daily_loss_limit=min(float(s["daily_loss_limit"]) for s in symbol_settings.values()),
        max_consecutive_losses=int(
            min(int(s["max_consecutive_losses"]) for s in symbol_settings.values())
        ),
        stoploss_cooldown_candles=int(
            max(int(s["stoploss_cooldown_candles"]) for s in symbol_settings.values())
        ),
        stop_loss_pct=max(float(s["stop_loss_pct"]) for s in symbol_settings.values()),
        max_pyramid_entries=int(
            max(int(s.get("max_pyramid_entries", 0)) for s in symbol_settings.values())
        ),
    )
    portfolio_risk_manager = LiveRiskManager(portfolio_risk_config)

    trade_contexts: dict[str, LiveContext] = {}
    for sym in symbols:
        s = symbol_settings[sym]
        symbol_risk_config = RiskConfig(
            max_leverage=float(s["leverage"]),
            max_position_size=float(s["max_position"]),
            max_order_size=float(s["max_position"]),
            daily_loss_limit=float(s["daily_loss_limit"]),
            max_consecutive_losses=int(s["max_consecutive_losses"]),
            stoploss_cooldown_candles=int(s["stoploss_cooldown_candles"]),
            stop_loss_pct=float(s["stop_loss_pct"]),
            max_pyramid_entries=int(s.get("max_pyramid_entries", 0)),
        )
        ctx = LiveContext(
            client=client,
            risk_manager=LiveRiskManager(symbol_risk_config),
            symbol=sym,
            leverage=int(s["leverage"]),
            env=settings.env,
            notifier=notifier,
            indicator_config=indicator_config,
            risk_reporter=portfolio_risk_manager.record_trade,
        )
        trade_contexts[sym] = ctx

    stream_contexts: dict[tuple[str, str], CandleStreamIndicatorContext] = {}
    price_feeds: dict[tuple[str, str], PriceFeed] = {}
    for sym, itv in normalized_streams:
        key = (sym, itv)
        stream_contexts[key] = CandleStreamIndicatorContext(symbol=sym, interval=itv)
        price_feeds[key] = PriceFeed(client, sym, candle_interval=itv)

    trade_intervals: dict[str, str] = {}
    for sym in symbols:
        intervals = sorted(
            [itv for s, itv in normalized_streams if s == sym],
            key=_parse_interval_seconds,
        )
        trade_intervals[sym] = intervals[0]

    primary_symbol = normalized_streams[0][0]
    portfolio_ctx = PortfolioContext(
        primary_symbol=primary_symbol,
        trade_contexts=trade_contexts,
        stream_contexts=stream_contexts,
        portfolio_risk_manager=portfolio_risk_manager,
        portfolio_multiplier=float(max(1, len(symbols))),
    )

    return PortfolioLiveTradingEngine(
        strategy=strategy,
        portfolio_ctx=portfolio_ctx,
        price_feeds=price_feeds,
        stream_contexts=stream_contexts,
        trade_contexts=trade_contexts,
        trade_intervals=trade_intervals,
        user_stream_hub=hub_proxy,  # type: ignore[arg-type]
        log_interval=log_interval,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@app.command()
def main(
    config: Path | None = typer.Option(
        None,
        help="포트폴리오 설정 JSON 파일 경로 (slots 배열 포함)",
    ),
    slots: str = typer.Option(
        os.getenv("PORTFOLIO_SLOTS", ""),
        help=(
            "포트폴리오 슬롯 JSON 배열. "
            '예: [{"strategy":"rsi_long_short_strategy.py","streams":[{"symbol":"BTCUSDT","interval":"1m","leverage":5}]}]'
        ),
    ),
    indicator_config: str = typer.Option(
        os.getenv("INDICATOR_CONFIG", ""),
        help='로그용 지표 설정 JSON 문자열 (예: {"rsi": {"period": 14}})',
    ),
    log_interval: int = typer.Option(
        int(os.getenv("LOG_INTERVAL", "0")),
        help="로그 출력 주기 (초). 0이면 캔들 마감 시에만 저장",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="대화형 확인 프롬프트를 건너뛰고 즉시 실행합니다",
    ),
) -> None:
    slot_defs = _load_slot_definitions(config, slots)
    indicator_config_data = _parse_json_dict(indicator_config)

    asyncio.run(
        _run(
            slot_defs=slot_defs,
            indicator_config=indicator_config_data,
            log_interval=log_interval,
            yes=yes,
        )
    )


def _load_slot_definitions(config_file: Path | None, inline_slots: str) -> list[dict[str, Any]]:
    raw: list[Any] = []
    if config_file:
        if not config_file.exists():
            raise typer.BadParameter(f"설정 파일을 찾을 수 없습니다: {config_file}")
        data = json.loads(config_file.read_text())
        if isinstance(data, list):
            raw = data
        elif isinstance(data, dict) and isinstance(data.get("slots"), list):
            raw = data["slots"]
        else:
            raise typer.BadParameter("설정 파일은 슬롯 배열이거나 {\"slots\": [...]} 형식이어야 합니다.")

    value = inline_slots.strip()
    if value:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            raw = parsed
        else:
            raise typer.BadParameter("--slots는 JSON 배열이어야 합니다.")

    if not raw:
        raise typer.BadParameter("--config 또는 --slots로 최소 1개 슬롯을 설정하세요.")
    if len(raw) > MAX_SLOTS:
        raise typer.BadParameter(f"최대 {MAX_SLOTS}개 슬롯만 지원합니다 (요청: {len(raw)}개).")

    for i, slot in enumerate(raw):
        if not isinstance(slot, dict):
            raise typer.BadParameter(f"슬롯 {i + 1}: 오브젝트여야 합니다.")
        if not slot.get("strategy"):
            raise typer.BadParameter(f"슬롯 {i + 1}: 'strategy' 필드가 필요합니다.")
    return raw


def _parse_json_dict(raw: str) -> dict[str, Any]:
    value = raw.strip()
    if not value:
        return {}
    data = json.loads(value)
    if not isinstance(data, dict):
        raise typer.BadParameter("JSON 오브젝트여야 합니다.")
    return data


async def _run(
    *,
    slot_defs: list[dict[str, Any]],
    indicator_config: dict[str, Any],
    log_interval: int,
    yes: bool,
) -> None:
    print("=" * 80)
    print("🚀 멀티 전략 포트폴리오 라이브 트레이딩")
    print("=" * 80)
    print()

    # 1) 전략 로드 & 스트림 결정
    loaded_slots: list[dict[str, Any]] = []
    all_symbols: set[str] = set()
    total_stream_count = 0

    for i, slot_def in enumerate(slot_defs):
        idx = i + 1
        print(f"── 슬롯 {idx}/{len(slot_defs)} ──")

        strategy_path = _resolve_strategy_path(str(slot_def["strategy"]))
        strategy_class = _load_strategy_class(strategy_path, i)
        params = slot_def.get("params") or {}
        if isinstance(params, str):
            params = json.loads(params) if params.strip() else {}
        strategy = _build_strategy(strategy_class, params)

        slot_streams_raw = slot_def.get("streams") or []
        if not slot_streams_raw:
            slot_streams_raw = _extract_strategy_stream_config(strategy)
        stream_configs = _normalize_stream_configs(slot_streams_raw)

        if not stream_configs:
            raise typer.BadParameter(f"슬롯 {idx}: 스트림이 비어있습니다. 'streams' 필드를 설정하세요.")

        slot_symbols = {str(cfg["symbol"]) for cfg in stream_configs}
        overlap = slot_symbols & all_symbols
        if overlap:
            raise typer.BadParameter(
                f"슬롯 {idx}: 심볼 {overlap}이(가) 다른 슬롯과 중복됩니다. "
                "같은 심볼은 하나의 슬롯 내에서만 사용할 수 있습니다."
            )
        all_symbols.update(slot_symbols)
        total_stream_count += len(stream_configs)

        streams_pretty = ", ".join(f"{cfg['symbol']}@{cfg['interval']}" for cfg in stream_configs)
        print(f"  전략: {strategy.__class__.__name__}")
        print(f"  스트림: {streams_pretty}")
        if params:
            print(f"  파라미터: {json.dumps(params, ensure_ascii=True)}")
        print()

        loaded_slots.append({
            "index": i,
            "strategy": strategy,
            "strategy_name": strategy.__class__.__name__,
            "stream_configs": stream_configs,
        })

    if total_stream_count > MAX_TOTAL_STREAMS:
        raise typer.BadParameter(
            f"전체 스트림 수 {total_stream_count}개는 한도 {MAX_TOTAL_STREAMS}개를 초과합니다."
        )

    print(f"총 슬롯: {len(loaded_slots)}/{MAX_SLOTS}, 총 스트림: {total_stream_count}/{MAX_TOTAL_STREAMS}")
    print()

    # 2) 확인
    print("⚠️  경고: 실제 계좌에 주문이 실행됩니다!")
    print("⚠️  테스트넷 API를 사용 중인지 확인하세요.")
    print()

    if not yes:
        try:
            response = input("계속하시겠습니까? (yes/no): ")
        except EOFError:
            print("❌ 대화형 입력(stdin)을 사용할 수 없습니다. --yes 옵션을 추가해서 실행하세요.")
            return
        if response.lower() != "yes":
            print("취소되었습니다.")
            return

    # 3) 공유 리소스 생성
    settings = get_settings()
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )
    notifier = SlackNotifier(settings.slack.webhook_url) if settings.slack.webhook_url else None
    user_stream_hub = UserStreamHub(client)
    hub_proxy = _NonOwningStreamHub(user_stream_hub)
    log_interval_value = log_interval if log_interval > 0 else None

    # 4) 슬롯별 엔진 생성
    engines: list[tuple[str, PortfolioLiveTradingEngine]] = []
    for slot in loaded_slots:
        engine = _build_slot_engine(
            slot_index=slot["index"],
            strategy=slot["strategy"],
            stream_configs=slot["stream_configs"],
            client=client,
            notifier=notifier,
            hub_proxy=hub_proxy,
            indicator_config=indicator_config,
            log_interval=log_interval_value,
            settings=settings,
        )
        engines.append((slot["strategy_name"], engine))

    # 5) 시그널 핸들러
    def signal_handler(sig: int, frame: Any) -> None:
        print("\n\n정지 신호를 받았습니다. 모든 엔진 종료 중...")
        for _, eng in engines:
            eng.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 6) 실행
    print("=" * 80)
    print("▶ 엔진 시작")
    print("=" * 80)

    try:
        await user_stream_hub.start()

        tasks = [
            asyncio.create_task(eng.start(), name=f"slot-{name}")
            for name, eng in engines
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

        for t in done:
            if t.exception():
                print(f"\n❌ 엔진 오류 ({t.get_name()}): {t.exception()}")

        for t in pending:
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=5.0)

    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        for _, eng in engines:
            eng.stop()
        await user_stream_hub.stop()

        print("\n" + "=" * 80)
        print("📊 포트폴리오 트레이딩 요약")
        print("=" * 80)
        for name, eng in engines:
            print(f"\n── {name} ──")
            summary = eng.get_summary()
            print(json.dumps(summary, indent=2))

        await client.aclose()


if __name__ == "__main__":
    app()
