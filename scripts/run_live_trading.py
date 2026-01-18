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
from live.engine import LiveTradingEngine
from live.price_feed import PriceFeed
from live.risk import LiveRiskManager
from notifications.slack import SlackNotifier
from settings import get_settings


app = typer.Typer(add_completion=False)


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


@app.command()
def main(
    strategy_file: Path = typer.Argument(..., help="전략 파일 경로"),
    symbol: str = typer.Option("BTCUSDT", help="거래 심볼"),
    leverage: int = typer.Option(
        int(os.getenv("LEVERAGE", "1")),
        help="레버리지 (기본: 1). 환경 변수 LEVERAGE로도 설정 가능",
    ),
    candle_interval: str = typer.Option(
        os.getenv("CANDLE_INTERVAL", "1m"),
        help="캔들 봉 간격 (예: 1m, 5m, 15m). 환경 변수 CANDLE_INTERVAL로도 설정 가능",
    ),
    max_position: float = typer.Option(
        float(os.getenv("MAX_POSITION", "0.5")),
        help="최대 포지션 크기 (자산 대비, 기본: 0.5). 환경 변수 MAX_POSITION로도 설정 가능",
    ),
    daily_loss_limit: float = typer.Option(500.0, help="일일 손실 한도 (USDT)"),
    max_consecutive_losses: int = typer.Option(
        0,
        help="최대 연속 손실 횟수 (0이면 비활성화)",
    ),
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
    stoploss_cooldown_candles: int = typer.Option(
        int(os.getenv("STOPLOSS_COOLDOWN_CANDLES", "0")),
        help="StopLoss 청산 후 거래 중단 캔들 수 (0이면 비활성화, 기본: 0). 환경 변수 STOPLOSS_COOLDOWN_CANDLES로도 설정 가능",
    ),
    stop_loss_pct: float = typer.Option(
        float(os.getenv("STOP_LOSS_PCT", "0.05")),
        help="StopLoss 비율 (0.0~1.0, 예: 0.05 = 5%, 기본: 0.05). 환경 변수 STOP_LOSS_PCT로도 설정 가능",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="대화형 확인 프롬프트를 건너뛰고 즉시 실행합니다(컨테이너/서버 환경 필수).",
    ),
) -> None:
    strategy_params_data = _load_json_dict(strategy_params, strategy_params_file, "전략 파라미터")
    indicator_config_data = _load_json_dict(indicator_config, indicator_config_file, "지표 설정")

    strategy_class = load_strategy_class(strategy_file)
    strategy = _build_strategy(strategy_class, strategy_params_data)

    asyncio.run(
        _run(
            strategy_file=strategy_file,
            symbol=symbol,
            leverage=leverage,
            candle_interval=candle_interval,
            max_position=max_position,
            daily_loss_limit=daily_loss_limit,
            max_consecutive_losses=max_consecutive_losses,
            log_interval=log_interval,
            stoploss_cooldown_candles=stoploss_cooldown_candles,
            stop_loss_pct=stop_loss_pct,
            yes=yes,
            strategy=strategy,
            strategy_params=strategy_params_data,
            indicator_config=indicator_config_data,
        )
    )


async def _run(
    strategy_file: Path,
    symbol: str,
    leverage: int,
    candle_interval: str,
    max_position: float,
    daily_loss_limit: float,
    max_consecutive_losses: int,
    log_interval: int,
    stoploss_cooldown_candles: int,
    stop_loss_pct: float,
    yes: bool,
    strategy: Any,
    strategy_params: dict[str, Any],
    indicator_config: dict[str, Any],
) -> None:
    print("=" * 80)
    print("🚀 라이브 트레이딩 시작")
    print("=" * 80)
    print(f"전략 파일: {strategy_file}")
    print(f"심볼: {symbol}")
    print(f"레버리지: {leverage}x")
    print(f"최대 포지션: {max_position * 100}% (자산 대비)")
    print(f"캔들 봉 간격: {candle_interval}")
    if strategy_params:
        print(f"전략 파라미터: {json.dumps(strategy_params, ensure_ascii=True)}")
    else:
        print("전략 파라미터: 없음")
    if indicator_config:
        print(f"지표 설정: {json.dumps(indicator_config, ensure_ascii=True)}")
    else:
        print("지표 설정: 기본값")
    print(f"일일 손실 한도: ${daily_loss_limit}")
    if max_consecutive_losses > 0:
        print(f"최대 연속 손실: {max_consecutive_losses}회")
    else:
        print("최대 연속 손실: 비활성화")
    if stoploss_cooldown_candles > 0:
        print(f"StopLoss Cooldown: {stoploss_cooldown_candles}개 캔들")
    else:
        print("StopLoss Cooldown: 비활성화")
    print(f"StopLoss 비율: {stop_loss_pct * 100:.1f}%")
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

    # 리스크 관리자 생성
    risk_config = RiskConfig(
        max_leverage=float(leverage),
        max_position_size=max_position,
        # 단일 주문 한도는 기본적으로 "최대 포지션 한도"와 동일하게 둔다.
        # 사용자가 --max-position 1.0 으로 설정해 "최대한 진입"을 원할 때,
        # 기본 max_order_size=0.5 때문에 주문이 거절되는 혼란을 방지한다.
        max_order_size=max_position,
        daily_loss_limit=daily_loss_limit,
        max_consecutive_losses=max_consecutive_losses,
        stoploss_cooldown_candles=stoploss_cooldown_candles,
        stop_loss_pct=stop_loss_pct,
    )
    risk_manager = LiveRiskManager(risk_config)

    notifier = SlackNotifier(settings.slack.webhook_url) if settings.slack.webhook_url else None

    # 컨텍스트 생성
    ctx = LiveContext(
        client=client,
        risk_manager=risk_manager,
        symbol=symbol,
        leverage=leverage,
        env=settings.env,
        notifier=notifier,
        indicator_config=indicator_config,
    )

    # 가격 피드 생성
    price_feed = PriceFeed(client, symbol, candle_interval=candle_interval)

    # 엔진 생성
    log_interval_value = log_interval if log_interval > 0 else None
    engine = LiveTradingEngine(
        strategy,
        ctx,
        price_feed,
        log_interval=log_interval_value,
        indicator_config=indicator_config,
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
