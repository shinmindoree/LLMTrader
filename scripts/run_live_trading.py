"""라이브 트레이딩 실행 스크립트."""

import asyncio
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import typer

# src 디렉토리를 Python 경로에 추가
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
MAX_CANDLE_STREAMS = 5


def load_strategy_class(strategy_file: Path):
    """전략 클래스 로드."""
    spec = importlib.util.spec_from_file_location("custom_strategy", strategy_file)
    if not spec or not spec.loader:
        raise ValueError(f"전략 파일을 로드할 수 없습니다: {strategy_file}")

    module = importlib.util.module_from_spec(spec)
    sys.modules["custom_strategy"] = module
    spec.loader.exec_module(module)

    # Strategy 클래스 찾기
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and name.endswith("Strategy") and name != "Strategy":
            print(f"🧩 전략 클래스 로드됨: {name} (파일: {strategy_file})")
            return obj

    raise ValueError(f"전략 클래스를 찾을 수 없습니다: {strategy_file}")


def resolve_strategy_path(strategy_file: Path) -> Path:
    if strategy_file.exists():
        return strategy_file
    candidate = (project_root / "scripts/strategies" / strategy_file).resolve()
    if candidate.exists():
        return candidate
    return strategy_file


def _load_json_dict(raw_value: str, file_path: Path | None, label: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if file_path:
        if not file_path.exists():
            raise typer.BadParameter(f"{label} 파일을 찾을 수 없습니다: {file_path}")
        try:
            data = json.loads(file_path.read_text())
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"{label} 파일 JSON 파싱 실패: {exc}") from exc
        if not isinstance(data, dict):
            raise typer.BadParameter(f"{label} 파일은 JSON 오브젝트여야 합니다.")
    value = raw_value.strip()
    if value:
        try:
            override = json.loads(value)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"{label} JSON 파싱 실패: {exc}") from exc
        if not isinstance(override, dict):
            raise typer.BadParameter(f"{label}는 JSON 오브젝트여야 합니다.")
        data.update(override)
    return data


def _build_strategy(strategy_class: type, params: dict[str, Any]):
    if not params:
        return strategy_class()
    try:
        return strategy_class(**params)
    except TypeError as exc:
        raise typer.BadParameter(f"전략 파라미터가 생성자와 일치하지 않습니다: {exc}") from exc


def _parse_json_list(raw_value: str, label: str) -> list[Any]:
    value = (raw_value or "").strip()
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{label} JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, list):
        raise typer.BadParameter(f"{label}는 JSON 리스트여야 합니다.")
    return data


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


def _extract_strategy_stream_config(strategy: Any) -> list[dict[str, Any]]:
    """전략에서 streams 선언을 추출한다(없으면 빈 값).

    포트폴리오 모드에서도 전략 코드가 심볼을 하드코딩하지 않도록,
    외부 설정(스트림 리스트)로 동작하도록 한다.
    """
    streams: list[dict[str, Any]] = []

    meta = getattr(strategy, "meta", None)
    if isinstance(meta, dict):
        meta_streams = meta.get("streams") or meta.get("candle_streams")
        if isinstance(meta_streams, list):
            for item in meta_streams:
                if isinstance(item, dict):
                    streams.append(dict(item))

    streams_attr = getattr(strategy, "streams", None)
    if isinstance(streams_attr, list):
        for item in streams_attr:
            if isinstance(item, dict):
                streams.append(dict(item))

    streams_attr_legacy = getattr(strategy, "candle_streams", None)
    if isinstance(streams_attr_legacy, list):
        for item in streams_attr_legacy:
            if isinstance(item, dict):
                streams.append(dict(item))

    return streams


def _normalize_streams(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for it in items:
        symbol = str(it.get("symbol") or it.get("s") or "").strip().upper()
        interval = str(it.get("interval") or it.get("candle_interval") or it.get("timeframe") or "").strip()
        if not symbol or not interval:
            continue
        out.append((symbol, interval))
    # unique + stable order
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for s, i in out:
        key = (s, i)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _normalize_stream_configs(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """스트림 설정을 표준화한다.

    각 항목은 최소한 symbol/interval을 포함해야 하며, 나머지 거래 설정은 기본값으로 채운다.
    """
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        symbol = str(it.get("symbol") or it.get("s") or "").strip().upper()
        interval = str(it.get("interval") or it.get("candle_interval") or it.get("timeframe") or "").strip()
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

    # unique (symbol, interval) by first occurrence
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for cfg in out:
        key = (str(cfg["symbol"]), str(cfg["interval"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cfg)
    return deduped


def _validate_symbol_settings_consistency(streams: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """동일 심볼이 여러 interval에 나타날 경우, 거래 설정이 충돌하지 않도록 검증한다."""
    per_symbol: dict[str, dict[str, Any]] = {}
    for cfg in streams:
        symbol = str(cfg["symbol"])
        leverage = int(cfg["leverage"])
        max_position = float(cfg["max_position"])
        daily_loss_limit = float(cfg["daily_loss_limit"])
        max_consecutive_losses = int(cfg["max_consecutive_losses"])
        stoploss_cooldown_candles = int(cfg["stoploss_cooldown_candles"])
        stop_loss_pct = float(cfg["stop_loss_pct"])
        max_pyramid_entries = int(cfg.get("max_pyramid_entries", 0))

        existing = per_symbol.get(symbol)
        current = {
            "symbol": symbol,
            "leverage": leverage,
            "max_position": max_position,
            "daily_loss_limit": daily_loss_limit,
            "max_consecutive_losses": max_consecutive_losses,
            "stoploss_cooldown_candles": stoploss_cooldown_candles,
            "stop_loss_pct": stop_loss_pct,
            "max_pyramid_entries": max_pyramid_entries,
        }
        if existing is None:
            per_symbol[symbol] = current
            continue

        # leverage/max_position 등은 심볼 단위로 적용되므로, 여러 스트림에서 값이 다르면 혼란/오동작 가능.
        for key in (
            "leverage",
            "max_position",
            "daily_loss_limit",
            "max_consecutive_losses",
            "stoploss_cooldown_candles",
            "stop_loss_pct",
            "max_pyramid_entries",
        ):
            if existing.get(key) != current.get(key):
                raise typer.BadParameter(
                    f"동일 심볼({symbol})에 대해 스트림별 거래 설정이 다릅니다: "
                    f"{key} {existing.get(key)} != {current.get(key)}. "
                    "같은 심볼은 동일한 거래 설정을 사용하도록 맞춰주세요."
                )
    return per_symbol


@app.command()
def main(
    strategy_file: Path = typer.Argument(..., help="전략 파일 경로"),
    strategy_params: str = typer.Option(
        os.getenv("STRATEGY_PARAMS", ""),
        help='전략 파라미터 JSON 문자열 (예: {"rsi_period": 2})',
    ),
    strategy_params_file: Path | None = typer.Option(
        None,
        help="전략 파라미터 JSON 파일 경로",
    ),
    indicator_config: str = typer.Option(
        os.getenv("INDICATOR_CONFIG", ""),
        help='로그용 지표 설정 JSON 문자열 (예: {"rsi": {"period": 14}})',
    ),
    indicator_config_file: Path | None = typer.Option(
        None,
        help="로그용 지표 설정 JSON 파일 경로",
    ),
    log_interval: int = typer.Option(
        int(os.getenv("LOG_INTERVAL", "0")),
        help="로그 출력 주기 (초). 0이면 캔들 마감 시에만 저장 (기본: 0). 환경 변수 LOG_INTERVAL로도 설정 가능",
    ),
    streams: str = typer.Option(
        os.getenv("STREAMS", ""),
        help=(
            "거래 스트림(심볼+캔들간격 페어) JSON 리스트. "
            '예: [{"symbol":"BTCUSDT","interval":"1m","leverage":10,"max_position":0.2,"daily_loss_limit":500,"stop_loss_pct":0.05}]'
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="대화형 확인 프롬프트를 건너뛰고 즉시 실행합니다(컨테이너/서버 환경 필수).",
    ),
) -> None:
    strategy_params_data = _load_json_dict(strategy_params, strategy_params_file, "전략 파라미터")
    indicator_config_data = _load_json_dict(indicator_config, indicator_config_file, "지표 설정")

    strategy_file = resolve_strategy_path(strategy_file)
    strategy_class = load_strategy_class(strategy_file)
    strategy = _build_strategy(strategy_class, strategy_params_data)

    asyncio.run(
        _run(
            strategy_file=strategy_file,
            log_interval=log_interval,
            yes=yes,
            strategy=strategy,
            strategy_params=strategy_params_data,
            indicator_config=indicator_config_data,
            streams=streams,
        )
    )


async def _run(
    strategy_file: Path,
    log_interval: int,
    yes: bool,
    strategy: Any,
    strategy_params: dict[str, Any],
    indicator_config: dict[str, Any],
    streams: str,
) -> None:
    print("=" * 80)
    print("🚀 라이브 트레이딩 시작")
    print("=" * 80)
    print(f"전략 파일: {strategy_file}")
    if strategy_params:
        print(f"전략 파라미터: {json.dumps(strategy_params, ensure_ascii=True)}")
    else:
        print("전략 파라미터: 없음")
    if indicator_config:
        print(f"지표 설정: {json.dumps(indicator_config, ensure_ascii=True)}")
    else:
        print("지표 설정: 기본값")
    print("=" * 80)
    print()

    # 경고 메시지
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

    # 설정 로드
    settings = get_settings()

    # 바이낸스 클라이언트 생성
    client = BinanceHTTPClient(
        api_key=settings.binance.api_key,
        api_secret=settings.binance.api_secret,
        base_url=settings.binance.base_url,
    )

    notifier = SlackNotifier(settings.slack.webhook_url) if settings.slack.webhook_url else None

    # ===== 스트림 설정 추출/검증 =====
    cli_stream_items = _parse_json_list(streams, "streams")
    cli_stream_dicts = [it for it in cli_stream_items if isinstance(it, dict)]
    strategy_streams = _extract_strategy_stream_config(strategy)
    stream_dicts = cli_stream_dicts if cli_stream_dicts else strategy_streams
    stream_configs = _normalize_stream_configs(stream_dicts)

    if not stream_configs:
        raise typer.BadParameter("--streams가 비어있습니다. 최소 1개 스트림을 설정하세요.")

    normalized_streams = [(str(cfg["symbol"]), str(cfg["interval"])) for cfg in stream_configs]
    if len(normalized_streams) > MAX_CANDLE_STREAMS:
        pretty = ", ".join(f"{s}@{i}" for s, i in normalized_streams)
        raise typer.BadParameter(
            f"요청한 캔들 스트림 {len(normalized_streams)}개는 지원하지 않습니다. "
            f"현재 시스템 한도는 {MAX_CANDLE_STREAMS}개입니다. (요청: {pretty})"
        )

    streams_pretty = ", ".join(f"{s}@{i}" for s, i in normalized_streams)
    print(f"거래 스트림 ({len(normalized_streams)}/{MAX_CANDLE_STREAMS}): {streams_pretty}")
    print(f"모드: {'싱글(1개 스트림)' if len(normalized_streams) == 1 else '포트폴리오(2개+ 스트림)'}")
    print()

    # ===== 심볼별 거래 설정 검증/정리 =====
    symbol_settings = _validate_symbol_settings_consistency(stream_configs)
    symbols = sorted(symbol_settings.keys())
    print("거래 설정(심볼별):")
    for sym in symbols:
        s = symbol_settings[sym]
        print(
            f"- {sym}: interval(s)={', '.join(sorted({itv for ss, itv in normalized_streams if ss == sym}))}, "
            f"leverage={int(s['leverage'])}x, max_position={float(s['max_position']) * 100:.1f}%, "
            f"daily_loss_limit=${float(s['daily_loss_limit']):.0f}, "
            f"max_consecutive_losses={int(s['max_consecutive_losses'])}, "
            f"stop_loss_pct={float(s['stop_loss_pct']) * 100:.2f}%, "
            f"stoploss_cooldown_candles={int(s['stoploss_cooldown_candles'])}, "
            f"max_pyramid_entries={int(s.get('max_pyramid_entries', 0))}"
        )
    print()

    # ===== 엔진 생성 =====
    log_interval_value = log_interval if log_interval > 0 else None

    # 포트폴리오 리스크(합산) 용도: 가장 보수적인 설정으로 구성(필요 시 향후 별도 옵션으로 분리)
    min_daily_loss = min(float(s["daily_loss_limit"]) for s in symbol_settings.values())
    portfolio_max_consecutive = (
        0
        if any(int(s["max_consecutive_losses"]) <= 0 for s in symbol_settings.values())
        else min(int(s["max_consecutive_losses"]) for s in symbol_settings.values())
    )
    portfolio_max_leverage = max(float(s["leverage"]) for s in symbol_settings.values())
    portfolio_max_position = max(float(s["max_position"]) for s in symbol_settings.values())
    portfolio_stoploss_cooldown = max(int(s["stoploss_cooldown_candles"]) for s in symbol_settings.values())
    portfolio_stop_loss_pct = max(float(s["stop_loss_pct"]) for s in symbol_settings.values())
    portfolio_max_pyramid = max(int(s.get("max_pyramid_entries", 0)) for s in symbol_settings.values())
    portfolio_risk_config = RiskConfig(
        max_leverage=portfolio_max_leverage,
        max_position_size=portfolio_max_position,
        max_order_size=portfolio_max_position,
        daily_loss_limit=min_daily_loss,
        max_consecutive_losses=portfolio_max_consecutive,
        stoploss_cooldown_candles=portfolio_stoploss_cooldown,
        stop_loss_pct=portfolio_stop_loss_pct,
        max_pyramid_entries=portfolio_max_pyramid,
    )
    portfolio_risk_manager = LiveRiskManager(portfolio_risk_config)

    trade_contexts: dict[str, LiveContext] = {}
    for sym in symbols:
        s = symbol_settings[sym]
        leverage = int(s["leverage"])
        max_position = float(s["max_position"])
        daily_loss_limit = float(s["daily_loss_limit"])
        max_consecutive_losses = int(s["max_consecutive_losses"])
        stoploss_cooldown_candles = int(s["stoploss_cooldown_candles"])
        stop_loss_pct = float(s["stop_loss_pct"])
        max_pyramid_entries = int(s.get("max_pyramid_entries", 0))

        symbol_risk_config = RiskConfig(
            max_leverage=float(leverage),
            max_position_size=max_position,
            max_order_size=max_position,
            daily_loss_limit=daily_loss_limit,
            max_consecutive_losses=max_consecutive_losses,
            stoploss_cooldown_candles=stoploss_cooldown_candles,
            stop_loss_pct=stop_loss_pct,
            max_pyramid_entries=max_pyramid_entries,
        )
        symbol_risk_manager = LiveRiskManager(symbol_risk_config)
        ctx = LiveContext(
            client=client,
            risk_manager=symbol_risk_manager,
            symbol=sym,
            leverage=leverage,
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

    # trade interval: 심볼별로 가장 빠른(초가 작은) interval을 선택(StopLoss/로그용)
    trade_intervals: dict[str, str] = {}
    for sym in symbols:
        intervals = [itv for s, itv in normalized_streams if s == sym]
        intervals_sorted = sorted(intervals, key=_parse_interval_seconds)
        trade_intervals[sym] = intervals_sorted[0] if intervals_sorted else intervals[0]

    user_stream_hub = UserStreamHub(client)
    primary_symbol = normalized_streams[0][0]
    portfolio_ctx = PortfolioContext(
        primary_symbol=primary_symbol,
        trade_contexts=trade_contexts,
        stream_contexts=stream_contexts,
        portfolio_risk_manager=portfolio_risk_manager,
        portfolio_multiplier=float(max(1, len(symbols))),
    )

    engine: Any = PortfolioLiveTradingEngine(
        strategy=strategy,
        portfolio_ctx=portfolio_ctx,
        price_feeds=price_feeds,
        stream_contexts=stream_contexts,
        trade_contexts=trade_contexts,
        trade_intervals=trade_intervals,
        user_stream_hub=user_stream_hub,
        log_interval=log_interval_value,
    )

    # 시그널 핸들러 설정
    def signal_handler(sig, frame):
        print("\n\n정지 신호를 받았습니다. 종료 중...")
        engine.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 트레이딩 시작
    try:
        await engine.start()
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 종료 시 요약 출력
        print("\n" + "=" * 80)
        print("📊 라이브 트레이딩 요약")
        print("=" * 80)
        summary = engine.get_summary()
        print(json.dumps(summary, indent=2))

        await client.aclose()


if __name__ == "__main__":
    app()
