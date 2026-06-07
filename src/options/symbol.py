"""Binance European Options 심볼 파서/포매터.

Binance Options 심볼 형식::

    <ASSET>-<YYMMDD>-<STRIKE>-<C|P>

예시::

    BTC-241229-100000-C    # 2024-12-29 만기, 행사가 100000, 콜
    ETH-260626-4000-P      # 2026-06-26 만기, 행사가 4000, 풋

- ``ASSET``: 옵션의 기초자산 코인 티커 (USDT 제외). 예: ``BTC``, ``ETH``.
  주의: REST 응답의 ``underlying`` 필드는 ``BTCUSDT`` 처럼 견적 자산이 붙은
  형태로 오기 때문에, 심볼 파싱 결과의 ``asset`` 과는 다르다.
- ``YYMMDD``: 만기일 (UTC 기준, ``08:00:00 UTC`` 정산).
- ``STRIKE``: 행사가 정수부. 정수가 아니면 ``X`` 등의 구분자를 쓰지 않고
  소수 이하 자리를 잘라낸 정수 표현이 사용된다. 안전을 위해 본 파서는
  정수만 지원하며, 비정수 행사가 심볼이 등장하면 ``ValueError`` 를 던진다.
- ``C`` / ``P``: 콜 / 풋.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Final


class OptionSide(StrEnum):
    """옵션의 매매 방향(콜/풋)."""

    CALL = "C"
    PUT = "P"

    @classmethod
    def from_token(cls, token: str) -> OptionSide:
        """심볼의 마지막 토큰(``C`` / ``P`` / ``CALL`` / ``PUT``)을 해석."""
        normalized = token.strip().upper()
        if normalized in {"C", "CALL"}:
            return cls.CALL
        if normalized in {"P", "PUT"}:
            return cls.PUT
        raise ValueError(f"Unknown option side token: {token!r}")


_SYMBOL_RE: Final = re.compile(
    r"^(?P<asset>[A-Z0-9]{2,10})-"
    r"(?P<yymmdd>\d{6})-"
    r"(?P<strike>\d+)-"
    r"(?P<side>[CP])$"
)


@dataclass(frozen=True, slots=True)
class OptionSymbol:
    """파싱된 옵션 심볼.

    Attributes:
        asset: 기초자산 코인 티커 (예: ``"BTC"``). 견적 자산(``USDT``)은 포함하지 않는다.
        expiry: 만기일(UTC, 시간/분/초는 ``00:00:00``).
        strike: 행사가 (정수). 비정수 행사가는 현재 미지원.
        side: 콜/풋.
        raw: 원본 심볼 문자열.
    """

    asset: str
    expiry: date
    strike: int
    side: OptionSide
    raw: str

    @property
    def underlying(self) -> str:
        """REST 응답의 ``underlying`` 필드와 동일한 USDT 견적 표현(``BTCUSDT``)."""
        return f"{self.asset}USDT"

    @property
    def expiry_ms(self) -> int:
        """만기일을 UTC 자정 기준 밀리초 타임스탬프로 변환.

        Binance는 ``08:00:00 UTC`` 에 정산되지만, 심볼 표기는 만기일자만
        담으므로 본 헬퍼는 자정(UTC) 기준으로 환산한다. 정확한 정산
        시각이 필요하면 ``/eapi/v1/exchangeInfo`` 의 ``expiryDate`` 를 사용할 것.
        """
        dt = datetime(self.expiry.year, self.expiry.month, self.expiry.day, tzinfo=UTC)
        return int(dt.timestamp() * 1000)

    def days_to_expiry(self, *, now: datetime | None = None) -> float:
        """현재 시각 기준 만기까지 남은 일수 (소수 포함)."""
        ref = now or datetime.now(UTC)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=UTC)
        expiry_dt = datetime(
            self.expiry.year, self.expiry.month, self.expiry.day, tzinfo=UTC
        )
        return (expiry_dt - ref).total_seconds() / 86400.0

    def __str__(self) -> str:
        return self.raw


def parse_option_symbol(symbol: str) -> OptionSymbol:
    """옵션 심볼 문자열을 :class:`OptionSymbol` 로 변환.

    Args:
        symbol: 예: ``"BTC-241229-100000-C"``. 대소문자는 자동 정규화된다.

    Raises:
        ValueError: 형식이 맞지 않거나 만기일이 유효하지 않은 경우.
    """
    if not symbol:
        raise ValueError("symbol must be non-empty")
    raw = symbol.strip().upper()
    match = _SYMBOL_RE.match(raw)
    if not match:
        raise ValueError(
            f"Invalid option symbol format: {symbol!r} "
            "(expected '<ASSET>-<YYMMDD>-<STRIKE>-<C|P>')"
        )
    yymmdd = match.group("yymmdd")
    try:
        expiry = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid expiry date in symbol: {yymmdd!r}") from exc

    strike = int(match.group("strike"))
    if strike <= 0:
        raise ValueError(f"Strike must be positive: {strike}")

    return OptionSymbol(
        asset=match.group("asset"),
        expiry=expiry,
        strike=strike,
        side=OptionSide.from_token(match.group("side")),
        raw=raw,
    )


def format_option_symbol(
    *,
    asset: str,
    expiry: date | datetime,
    strike: int | float,
    side: OptionSide | str,
) -> str:
    """구성 요소로부터 옵션 심볼 문자열을 생성.

    Args:
        asset: 기초자산 코인 티커 (예: ``"BTC"``). ``"BTCUSDT"`` 처럼 견적
            자산이 붙어도 자동으로 ``"BTC"`` 로 정규화한다.
        expiry: 만기일.
        strike: 행사가. 정수만 지원하며, ``float`` 가 들어오면 소수부가 0인지
            검증한 뒤 정수로 캐스팅한다.
        side: 콜/풋. 문자열인 경우 ``"C"/"CALL"/"P"/"PUT"`` 를 허용.

    Raises:
        ValueError: 입력이 형식 요구를 만족하지 않을 때.
    """
    asset_norm = asset.strip().upper()
    if asset_norm.endswith("USDT"):
        asset_norm = asset_norm[:-4]
    if not asset_norm or not asset_norm.isalnum():
        raise ValueError(f"Invalid asset ticker: {asset!r}")

    expiry_date = expiry.date() if isinstance(expiry, datetime) else expiry

    if isinstance(strike, float):
        if not strike.is_integer():
            raise ValueError(f"Non-integer strike not supported: {strike}")
        strike_int = int(strike)
    else:
        strike_int = int(strike)
    if strike_int <= 0:
        raise ValueError(f"Strike must be positive: {strike_int}")

    side_enum = side if isinstance(side, OptionSide) else OptionSide.from_token(side)

    return f"{asset_norm}-{expiry_date.strftime('%y%m%d')}-{strike_int}-{side_enum.value}"
