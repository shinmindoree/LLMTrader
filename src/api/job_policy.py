from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from control.enums import JobType


_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,20}$")
_BACKTEST_ALLOWED_INTERVALS = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
}
_LIVE_ALLOWED_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}
_LIVE_MAX_STREAMS = 5
_MS_PER_DAY = 86_400_000


@dataclass
class JobPolicyCheckResult:
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.blockers


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _check_symbol(symbol: str, field_name: str, blockers: list[str]) -> None:
    if not symbol:
        blockers.append(f"{field_name}: symbol 값이 비어 있습니다.")
        return
    if not _SYMBOL_PATTERN.match(symbol):
        blockers.append(f"{field_name}: symbol 형식이 유효하지 않습니다 ({symbol}).")


def _check_backtest(config: dict[str, Any], result: JobPolicyCheckResult) -> None:
    symbol = str(config.get("symbol") or "").strip().upper()
    _check_symbol(symbol, "config.symbol", result.blockers)

    interval = str(config.get("interval") or "").strip().lower()
    if interval not in _BACKTEST_ALLOWED_INTERVALS:
        result.blockers.append(
            "config.interval: 지원되지 않는 interval입니다. "
            f"허용값={sorted(_BACKTEST_ALLOWED_INTERVALS)}"
        )

    leverage = _to_int(config.get("leverage"))
    if leverage is None:
        result.blockers.append("config.leverage: 숫자여야 합니다.")
    elif leverage < 1 or leverage > 20:
        result.blockers.append("config.leverage: 1~20 범위여야 합니다.")
    elif leverage > 10:
        result.warnings.append("레버리지가 10x 초과입니다. 과도한 손실 위험이 있습니다.")

    initial_balance = _to_float(config.get("initial_balance"))
    if initial_balance is None or initial_balance <= 0:
        result.blockers.append("config.initial_balance: 0보다 커야 합니다.")
    elif initial_balance < 100:
        result.warnings.append("initial_balance가 100 USDT 미만입니다. 체결/수수료 영향이 큽니다.")

    commission = _to_float(config.get("commission"))
    if commission is None or commission < 0:
        result.blockers.append("config.commission: 0 이상 숫자여야 합니다.")
    elif commission > 0.01:
        result.blockers.append("config.commission: 0.01 이하(1% 이하)여야 합니다.")
    elif commission > 0.003:
        result.warnings.append("commission 값이 높습니다. 백테스트 성과 왜곡 가능성이 있습니다.")

    stop_loss_pct = _to_float(config.get("stop_loss_pct"))
    if stop_loss_pct is None or stop_loss_pct <= 0:
        result.blockers.append("config.stop_loss_pct: 0보다 커야 합니다.")
    elif stop_loss_pct > 0.5:
        result.blockers.append("config.stop_loss_pct: 0.5 이하(50% 이하)여야 합니다.")
    elif stop_loss_pct > 0.2:
        result.warnings.append("stop_loss_pct가 20% 초과입니다. 손실 허용 폭이 큽니다.")

    max_position = _to_float(config.get("max_position"))
    if max_position is not None:
        if max_position <= 0 or max_position > 1:
            result.blockers.append("config.max_position: (0, 1] 범위여야 합니다.")
        elif max_position > 0.5:
            result.warnings.append("max_position이 0.5 초과입니다. 포지션 집중도가 높습니다.")

    start_ts = _to_int(config.get("start_ts"))
    end_ts = _to_int(config.get("end_ts"))
    if start_ts is None or end_ts is None:
        result.blockers.append("config.start_ts/config.end_ts: 둘 다 필요합니다.")
        return
    if start_ts <= 0 or end_ts <= 0:
        result.blockers.append("config.start_ts/config.end_ts: 양수 타임스탬프(ms)여야 합니다.")
        return
    if start_ts > end_ts:
        result.blockers.append("config.start_ts는 config.end_ts보다 같거나 작아야 합니다.")
        return

    days = (end_ts - start_ts) / _MS_PER_DAY
    if days > 3650:
        result.blockers.append("백테스트 기간이 10년 초과입니다. 기간을 축소하세요.")
    elif days > 730:
        result.warnings.append("백테스트 기간이 2년 초과입니다. 실행 시간이 길어질 수 있습니다.")


def _check_live(config: dict[str, Any], result: JobPolicyCheckResult) -> None:
    streams_raw = config.get("streams")
    if not isinstance(streams_raw, list) or not streams_raw:
        result.blockers.append("LIVE는 config.streams(비어있지 않은 리스트)가 필요합니다.")
        return
    if len(streams_raw) > _LIVE_MAX_STREAMS:
        result.blockers.append(f"LIVE streams 개수는 최대 {_LIVE_MAX_STREAMS}개까지만 허용됩니다.")
        return

    seen_streams: set[tuple[str, str]] = set()
    total_max_position = 0.0

    for idx, stream in enumerate(streams_raw):
        field_prefix = f"config.streams[{idx}]"
        if not isinstance(stream, dict):
            result.blockers.append(f"{field_prefix}: 객체여야 합니다.")
            continue

        symbol = str(stream.get("symbol") or "").strip().upper()
        _check_symbol(symbol, f"{field_prefix}.symbol", result.blockers)

        interval = str(stream.get("interval") or "").strip().lower()
        if interval not in _LIVE_ALLOWED_INTERVALS:
            result.blockers.append(
                f"{field_prefix}.interval: 허용값={sorted(_LIVE_ALLOWED_INTERVALS)}"
            )
        key = (symbol, interval)
        if symbol and interval:
            if key in seen_streams:
                result.warnings.append(f"{field_prefix}: 중복 stream({symbol}/{interval}) 입니다.")
            seen_streams.add(key)

        leverage = _to_int(stream.get("leverage"))
        if leverage is None:
            result.blockers.append(f"{field_prefix}.leverage: 숫자여야 합니다.")
        elif leverage < 1 or leverage > 10:
            result.blockers.append(f"{field_prefix}.leverage: LIVE는 1~10 범위만 허용됩니다.")
        elif leverage >= 8:
            result.warnings.append(f"{field_prefix}.leverage가 {leverage}x 입니다. 고위험 설정입니다.")

        max_position = _to_float(stream.get("max_position"))
        if max_position is None:
            result.blockers.append(f"{field_prefix}.max_position: 숫자여야 합니다.")
        elif max_position <= 0 or max_position > 0.5:
            result.blockers.append(f"{field_prefix}.max_position: (0, 0.5] 범위여야 합니다.")
        else:
            total_max_position += max_position
            if max_position > 0.3:
                result.warnings.append(f"{field_prefix}.max_position이 {max_position:.2f}로 큽니다.")

        daily_loss_limit = _to_float(stream.get("daily_loss_limit"))
        if daily_loss_limit is None:
            result.blockers.append(f"{field_prefix}.daily_loss_limit: 숫자여야 합니다.")
        elif daily_loss_limit <= 0:
            result.blockers.append(f"{field_prefix}.daily_loss_limit: 0보다 커야 합니다.")
        elif daily_loss_limit > 5000:
            result.blockers.append(f"{field_prefix}.daily_loss_limit: 5000 이하만 허용됩니다.")
        elif daily_loss_limit > 1000:
            result.warnings.append(
                f"{field_prefix}.daily_loss_limit이 {daily_loss_limit:.2f} USDT로 큽니다."
            )

        stop_loss_pct = _to_float(stream.get("stop_loss_pct"))
        if stop_loss_pct is None:
            result.blockers.append(f"{field_prefix}.stop_loss_pct: 숫자여야 합니다.")
        elif stop_loss_pct <= 0:
            result.blockers.append(f"{field_prefix}.stop_loss_pct: 0보다 커야 합니다.")
        elif stop_loss_pct > 0.2:
            result.blockers.append(f"{field_prefix}.stop_loss_pct: 0.2 이하(20% 이하)여야 합니다.")
        elif stop_loss_pct > 0.1:
            result.warnings.append(f"{field_prefix}.stop_loss_pct가 10% 초과입니다.")

        max_consecutive_losses = _to_int(stream.get("max_consecutive_losses"))
        if max_consecutive_losses is None:
            result.blockers.append(f"{field_prefix}.max_consecutive_losses: 숫자여야 합니다.")
        elif max_consecutive_losses < 0 or max_consecutive_losses > 20:
            result.blockers.append(f"{field_prefix}.max_consecutive_losses: 0~20 범위여야 합니다.")

        stoploss_cooldown_candles = _to_int(stream.get("stoploss_cooldown_candles"))
        if stoploss_cooldown_candles is None:
            result.blockers.append(f"{field_prefix}.stoploss_cooldown_candles: 숫자여야 합니다.")
        elif stoploss_cooldown_candles < 0 or stoploss_cooldown_candles > 2000:
            result.blockers.append(
                f"{field_prefix}.stoploss_cooldown_candles: 0~2000 범위여야 합니다."
            )

    if total_max_position > 1.5:
        result.blockers.append(
            f"streams 전체 max_position 합({total_max_position:.2f})이 1.5를 초과합니다."
        )
    elif total_max_position > 1.0:
        result.warnings.append(
            f"streams 전체 max_position 합({total_max_position:.2f})이 1.0을 초과합니다."
        )


def evaluate_job_policy(job_type: JobType, config: dict[str, Any]) -> JobPolicyCheckResult:
    result = JobPolicyCheckResult()
    if not isinstance(config, dict):
        result.blockers.append("config는 JSON object여야 합니다.")
        return result

    if job_type == JobType.BACKTEST:
        _check_backtest(config, result)
    elif job_type == JobType.LIVE:
        _check_live(config, result)
    else:
        result.blockers.append(f"지원되지 않는 job type: {job_type}")
    return result
