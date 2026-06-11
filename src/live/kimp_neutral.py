"""김프 델타-중립 북의 사이징·환헤지·PnL 순수 로직.

업비트 현물 롱 + 바이낸스 무기한 숏으로 구성되는 **단 하나의** 델타-중립 김프
북을 다룬다. 네트워크/DB 의존이 없는 순수 함수 모음이라 단위테스트로 중립성을
직접 증명할 수 있고, 추후 라이브 엔진이 그대로 재사용할 수 있다.

핵심 정의 (KRW 회계 기준)
------------------------
- ``upbit_krw``    ``S_u`` : 업비트 가격 (KRW / coin)
- ``binance_usdt`` ``S_b`` : 바이낸스 무기한 가격 (USDT / coin, USDT ≈ USD 가정)
- ``usd_krw``      ``e``   : USDT/KRW 기준가 (KRW / USDT, 필드명은 호환성상 유지)
- 김프            ``k``   : ``k = S_u / (S_b * e) - 1``

중립북은 **항상 하나** 다::

    업비트 현물 롱(q_u)  +  바이낸스 무기한 숏(q_b)

김프가 쌀 때(z 낮음) 북을 **키우고**, 비쌀 때(z 높음) **줄인다**. "새 롱을 여는"
것이 아니라 같은 북의 크기만 바꾼다 — 두 레그가 항상 함께 움직여 순(net) 코인
델타가 0 근처로 유지된다. (이 모듈은 과거 설명의 "업비트 매도 + 바이낸스 신규
롱 = 네이키드 롱" 오류를 구조적으로 차단한다.)

헤지 모드
---------
- :data:`HedgeMode.QUANTITY` : ``q_b = q_u`` (코인 수량 일치). 공통 가격 변동에
  대한 잔여 델타 = ``S_b·e·q·k`` 의 작은 "김프 누수". 단순·실무 기본값.
- :data:`HedgeMode.DELTA`    : ``q_b = q_u·(1+k)``. 공통 가격 변동에 대한 델타가
  정확히 0. 업비트 KRW 가치가 김프만큼 부풀려진 것을 숏으로 정확히 상쇄한다.

FX 노트
-------
중립북의 즉시(mark-to-market) FX 노출은 **바이낸스에 예치된 USDT 담보(margin) +
숏의 미실현 손익(USD)** 이다. 업비트 코인 보유분은 김프 ``k`` 를 고정하면 환율과
무관(코인의 USD 가치는 환율 불변)하므로, 환율 충격은 별도 항이 아니라 김프
변동으로 라우팅되어 들어온다. 따라서 FX 헤지는 "USD 담보를 그만큼 매도(USD/KRW
선물환 숏)" 로 충분하며, 그 크기는 :func:`book_deltas` 가 현재 시세 기준으로
재계산한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "HedgeMode",
    "RebalanceAction",
    "LotFilter",
    "LotPair",
    "KimpQuote",
    "SizingConfig",
    "SignalConfig",
    "NeutralBook",
    "EntryPlan",
    "BookDeltas",
    "PnLBreakdown",
    "RebalanceOrder",
    "plan_entry",
    "book_deltas",
    "realized_pnl",
    "target_book_krw",
    "plan_rebalance",
]


class HedgeMode(StrEnum):
    """숏 레그 수량 결정 방식."""

    QUANTITY = "quantity"  # q_b = q_u (코인 수량 일치)
    DELTA = "delta"        # q_b = q_u * (1 + kimp) (공통 가격변동 델타 0)


class RebalanceAction(StrEnum):
    """리밸런스 방향."""

    HOLD = "hold"
    SCALE_UP = "scale_up"      # 김프 쌈 → 북 확대 (업비트 매수 + 숏 추가)
    SCALE_DOWN = "scale_down"  # 김프 비쌈 → 북 축소 (업비트 매도 + 숏 환매)


def _round_step(qty: float, step: float) -> float:
    """``step`` 단위로 내림(floor). ``step<=0`` 이면 원값."""
    if step <= 0:
        return qty
    return math.floor(qty / step) * step


@dataclass(frozen=True)
class LotFilter:
    """거래소 한 종목의 주문 제약(LOT_SIZE / minNotional)."""

    step_size: float = 0.0
    min_qty: float = 0.0
    min_notional_quote: float = 0.0  # 해당 거래소 quote 통화(업비트=KRW, 바이낸스=USDT)


@dataclass(frozen=True)
class LotPair:
    """업비트·바이낸스 양쪽 주문 제약 묶음."""

    upbit: LotFilter = field(default_factory=LotFilter)
    binance: LotFilter = field(default_factory=LotFilter)


@dataclass(frozen=True)
class KimpQuote:
    """동시 시점의 업비트/바이낸스 가격 + 환율 스냅샷."""

    symbol: str
    upbit_krw: float      # S_u (KRW / coin)
    binance_usdt: float   # S_b (USDT / coin)
    usd_krw: float        # e   (KRW / USDT; legacy field name)

    def __post_init__(self) -> None:
        if self.upbit_krw <= 0 or self.binance_usdt <= 0 or self.usd_krw <= 0:
            raise ValueError("upbit_krw, binance_usdt, usd_krw 는 모두 양수여야 합니다")

    @property
    def binance_krw(self) -> float:
        """바이낸스 가격의 KRW 환산 = ``S_b * e``."""
        return self.binance_usdt * self.usd_krw

    @property
    def kimp(self) -> float:
        """김프 = ``S_u / (S_b * e) - 1`` (예: 0.0345 == 3.45%)."""
        return self.upbit_krw / self.binance_krw - 1.0


@dataclass(frozen=True)
class SizingConfig:
    """사이징 정책: 헤지 모드·레버리지·수수료율."""

    hedge_mode: HedgeMode = HedgeMode.QUANTITY
    leverage: float = 1.0          # 바이낸스 숏 레버리지 (>=1)
    upbit_taker_fee: float = 0.0005    # 업비트 테이커 0.05%
    binance_taker_fee: float = 0.0005  # 바이낸스 선물 테이커 ~0.05%

    def __post_init__(self) -> None:
        if self.leverage < 1.0:
            raise ValueError("leverage 는 1.0 이상이어야 합니다")

    def short_ratio(self, kimp: float) -> float:
        """롱 수량 대비 숏 수량 배수."""
        return 1.0 + kimp if self.hedge_mode is HedgeMode.DELTA else 1.0


@dataclass(frozen=True)
class SignalConfig:
    """z-score → 목표 북 크기 매핑 정책.

    Long-Kimp 북은 김프가 **확대**될 때 이익이므로, 김프가 **쌀 때**(z 낮음)
    북을 키우고 **비쌀 때**(z 높음) 줄인다.

    - ``z <= full_build_z`` : 목표 = 100% (gross_cap_krw)
    - ``z >= flat_z``       : 목표 = 0% (청산)
    - 사이 구간            : 선형 보간
    """

    gross_cap_krw: float
    full_build_z: float = -2.0  # 이 이하로 김프가 싸지면 풀사이즈
    flat_z: float = 0.5         # 이 이상으로 김프가 비싸지면 플랫

    def __post_init__(self) -> None:
        if self.flat_z <= self.full_build_z:
            raise ValueError("flat_z 는 full_build_z 보다 커야 합니다")
        if self.gross_cap_krw <= 0:
            raise ValueError("gross_cap_krw 는 양수여야 합니다")


@dataclass(frozen=True)
class NeutralBook:
    """현재 보유 중립북 상태."""

    symbol: str
    upbit_long_qty: float       # 업비트 현물 롱 수량 (coins, >=0)
    binance_short_qty: float    # 바이낸스 무기한 숏 수량 (coins, >=0)
    entry: KimpQuote            # 진입 시점 시세 (PnL/델타 기준)
    binance_margin_usdt: float = 0.0  # 바이낸스에 예치된 숏 담보(USDT)
    hedge_mode: HedgeMode = HedgeMode.QUANTITY


@dataclass(frozen=True)
class EntryPlan:
    """진입 사이징 결과. ``ok=False`` 면 ``reason`` 에 거절 사유."""

    ok: bool
    symbol: str
    upbit_long_qty: float = 0.0
    binance_short_qty: float = 0.0
    upbit_cost_krw: float = 0.0        # 업비트 현물 매수 비용(KRW)
    binance_margin_usdt: float = 0.0   # 바이낸스 숏 담보(USDT)
    binance_margin_krw: float = 0.0    # 위 담보의 KRW 환산
    total_capital_krw: float = 0.0     # 업비트 비용 + 담보 KRW
    entry_fee_krw: float = 0.0         # 양 레그 테이커 수수료(KRW)
    fx_hedge_usd: float = 0.0          # 헤지로 매도해야 할 USD(>0: USD 매도)
    expected_coin_delta_qty: float = 0.0
    expected_price_delta_krw: float = 0.0
    kimp: float = 0.0
    entry_quote: KimpQuote | None = None
    hedge_mode: HedgeMode = HedgeMode.QUANTITY
    reason: str | None = None

    def to_book(self) -> NeutralBook:
        """체결되었다고 가정한 :class:`NeutralBook` 으로 변환(시뮬/테스트용)."""
        if not self.ok or self.entry_quote is None:
            raise ValueError(f"거절된 진입 계획은 북으로 변환할 수 없습니다: {self.reason}")
        return NeutralBook(
            symbol=self.symbol,
            upbit_long_qty=self.upbit_long_qty,
            binance_short_qty=self.binance_short_qty,
            entry=self.entry_quote,
            binance_margin_usdt=self.binance_margin_usdt,
            hedge_mode=self.hedge_mode,
        )


@dataclass(frozen=True)
class BookDeltas:
    """중립북의 리스크 분해 — 중립성 증명용.

    - ``coin_delta_qty``   : 순 코인 수량 (롱 − 숏). 수량 관점.
    - ``price_delta_krw``  : 공통(글로벌) 코인 가격이 1.0(=100%) 움직일 때 북의
      KRW 가치 변화. **이것이 진짜 방향성 델타**. 중립이면 ≈ 0.
    - ``fx_exposure_usd``  : USD 표시 순자산(담보 + 숏 미실현). >0 = 순 USD 롱.
    - ``fx_hedge_usd``     : FX 평탄화를 위해 매도할 USD(= ``fx_exposure_usd``).
    """

    coin_delta_qty: float
    price_delta_krw: float
    fx_exposure_usd: float
    fx_hedge_usd: float
    upbit_notional_krw: float
    binance_notional_usd: float
    kimp: float


@dataclass(frozen=True)
class PnLBreakdown:
    """청산 손익 분해(KRW)."""

    total_krw: float
    upbit_leg_krw: float
    binance_leg_krw: float
    kimp_component_krw: float   # 김프 변동 기여분 ≈ q_u·S_b0·e0·Δk
    residual_krw: float         # 공통변동 누수 + FX 타이밍 + 수량불일치
    fee_krw: float
    kimp_change: float          # Δk = k_exit − k_entry


@dataclass(frozen=True)
class RebalanceOrder:
    """목표 북 크기에 도달하기 위한 대칭 주문(두 레그 동시 이동)."""

    action: RebalanceAction
    upbit_qty: float = 0.0       # 업비트에서 매수/매도할 코인 수량
    binance_qty: float = 0.0     # 바이낸스에서 추가/환매할 숏 수량
    upbit_side: str | None = None    # "BUY" | "SELL" | None
    binance_side: str | None = None  # "SELL"(숏 추가) | "BUY"(숏 환매) | None
    notional_krw: float = 0.0
    reason: str | None = None


# ── 1) 진입 사이징 ─────────────────────────────────────────


def plan_entry(
    quote: KimpQuote,
    capital_krw: float,
    lots: LotPair,
    config: SizingConfig,
) -> EntryPlan:
    """KRW 시드로 진입할 중립북 수량·담보·환헤지를 계산한다.

    자본 배분: 업비트 현물은 풀 노셔널(레버리지 없음), 바이낸스 숏은 레버리지
    ``L`` 의 담보만 필요하므로 ``capital = q·S_u + q_b·S_b·e/L`` 로 ``q`` 를 푼다.
    """
    if capital_krw <= 0:
        return EntryPlan(ok=False, symbol=quote.symbol, reason="capital_krw 가 0 이하입니다")

    s_u = quote.upbit_krw
    s_b = quote.binance_usdt
    e = quote.usd_krw
    k = quote.kimp
    lev = config.leverage
    ratio_b = config.short_ratio(k)

    denom = s_u + ratio_b * s_b * e / lev
    raw_q = capital_krw / denom

    # 두 거래소 step 중 거친 쪽으로 롱 수량을 맞춰 수량 불일치를 최소화한다.
    coarse_step = max(lots.upbit.step_size, lots.binance.step_size)
    q_u = _round_step(raw_q, coarse_step or lots.upbit.step_size)
    q_b = _round_step(q_u * ratio_b, lots.binance.step_size)

    if q_u <= 0 or q_b <= 0:
        return EntryPlan(
            ok=False,
            symbol=quote.symbol,
            kimp=k,
            reason="진입 수량이 0 입니다. 시드(capital_krw)를 늘리세요.",
        )

    # 최소 수량/명목 검증 (양 거래소).
    if q_u < lots.upbit.min_qty or q_b < lots.binance.min_qty:
        return EntryPlan(
            ok=False,
            symbol=quote.symbol,
            kimp=k,
            reason=(
                f"수량이 최소주문 미만입니다 "
                f"(upbit {q_u:.8f}<{lots.upbit.min_qty:.8f} 또는 "
                f"binance {q_b:.8f}<{lots.binance.min_qty:.8f}). 시드를 늘리세요."
            ),
        )
    upbit_notional_krw = q_u * s_u
    binance_notional_usdt = q_b * s_b
    if lots.upbit.min_notional_quote and upbit_notional_krw < lots.upbit.min_notional_quote:
        return EntryPlan(
            ok=False,
            symbol=quote.symbol,
            kimp=k,
            reason=(
                f"업비트 명목 {upbit_notional_krw:.0f} KRW < 최소 "
                f"{lots.upbit.min_notional_quote:.0f} KRW. 시드를 늘리세요."
            ),
        )
    if lots.binance.min_notional_quote and binance_notional_usdt < lots.binance.min_notional_quote:
        return EntryPlan(
            ok=False,
            symbol=quote.symbol,
            kimp=k,
            reason=(
                f"바이낸스 명목 {binance_notional_usdt:.2f} USDT < 최소 "
                f"{lots.binance.min_notional_quote:.2f} USDT. 시드를 늘리세요."
            ),
        )

    margin_usdt = binance_notional_usdt / lev
    margin_krw = margin_usdt * e
    fee_krw = upbit_notional_krw * config.upbit_taker_fee + (
        binance_notional_usdt * e * config.binance_taker_fee
    )

    book = NeutralBook(
        symbol=quote.symbol,
        upbit_long_qty=q_u,
        binance_short_qty=q_b,
        entry=quote,
        binance_margin_usdt=margin_usdt,
        hedge_mode=config.hedge_mode,
    )
    deltas = book_deltas(book, quote)

    return EntryPlan(
        ok=True,
        symbol=quote.symbol,
        upbit_long_qty=q_u,
        binance_short_qty=q_b,
        upbit_cost_krw=upbit_notional_krw,
        binance_margin_usdt=margin_usdt,
        binance_margin_krw=margin_krw,
        total_capital_krw=upbit_notional_krw + margin_krw,
        entry_fee_krw=fee_krw,
        fx_hedge_usd=deltas.fx_hedge_usd,
        expected_coin_delta_qty=deltas.coin_delta_qty,
        expected_price_delta_krw=deltas.price_delta_krw,
        kimp=k,
        entry_quote=quote,
        hedge_mode=config.hedge_mode,
    )


# ── 2) 델타 분해 (중립성 증명) ─────────────────────────────


def book_deltas(book: NeutralBook, quote: KimpQuote) -> BookDeltas:
    """현재 시세 기준 북의 방향성 델타와 FX 노출을 계산한다.

    공통 가격변동 델타(KRW)::

        dV/drho = q_u*S_u - q_b*S_b*e = S_b*e*[q_u*(1+k) - q_b]

    - QUANTITY 모드(q_b=q_u): ``S_b*e*q*k`` (작은 김프 누수)
    - DELTA 모드(q_b=q_u*(1+k)): ``0``
    """
    s_b = quote.binance_usdt
    e = quote.usd_krw
    q_u = book.upbit_long_qty
    q_b = book.binance_short_qty

    upbit_value_krw = q_u * quote.upbit_krw          # 롱 보유 KRW 가치
    binance_value_krw = q_b * s_b * e                # 숏 명목 KRW 환산
    price_delta_krw = upbit_value_krw - binance_value_krw

    # FX 노출(USD): 담보 + 숏 미실현(USD). 진입가 대비 마크 변화.
    short_mtm_usd = q_b * (book.entry.binance_usdt - s_b)
    fx_exposure_usd = book.binance_margin_usdt + short_mtm_usd

    return BookDeltas(
        coin_delta_qty=q_u - q_b,
        price_delta_krw=price_delta_krw,
        fx_exposure_usd=fx_exposure_usd,
        fx_hedge_usd=fx_exposure_usd,
        upbit_notional_krw=upbit_value_krw,
        binance_notional_usd=q_b * s_b,
        kimp=quote.kimp,
    )


# ── 3) 손익 분해 ───────────────────────────────────────────


def realized_pnl(
    book: NeutralBook,
    exit_quote: KimpQuote,
    *,
    binance_fx: str = "exit",
    fee_krw: float = 0.0,
) -> PnLBreakdown:
    """진입→청산 손익을 KRW로 계산하고 김프 성분/잔차로 분해한다.

    ``binance_fx`` 가 ``"exit"`` 이면 숏 손익을 청산 환율로, ``"entry"`` 면 진입
    환율로 환산한다. 김프 성분은 ``q_u·S_b0·e0·Δk`` 로 정의한다(롱 레그 기준).
    """
    entry = book.entry
    q_u = book.upbit_long_qty
    q_b = book.binance_short_qty

    upbit_leg_krw = q_u * (exit_quote.upbit_krw - entry.upbit_krw)
    short_pnl_usdt = q_b * (entry.binance_usdt - exit_quote.binance_usdt)
    fx_for_short = exit_quote.usd_krw if binance_fx == "exit" else entry.usd_krw
    binance_leg_krw = short_pnl_usdt * fx_for_short

    total_krw = upbit_leg_krw + binance_leg_krw - fee_krw

    dk = exit_quote.kimp - entry.kimp
    kimp_component_krw = q_u * entry.binance_usdt * entry.usd_krw * dk
    residual_krw = (upbit_leg_krw + binance_leg_krw) - kimp_component_krw

    return PnLBreakdown(
        total_krw=total_krw,
        upbit_leg_krw=upbit_leg_krw,
        binance_leg_krw=binance_leg_krw,
        kimp_component_krw=kimp_component_krw,
        residual_krw=residual_krw,
        fee_krw=fee_krw,
        kimp_change=dk,
    )


# ── 4) 시그널: z-score → 목표 북 크기 ──────────────────────


def target_book_krw(z: float, config: SignalConfig) -> float:
    """김프 z-score 를 목표 북 크기(KRW)로 매핑한다.

    z 가 낮을수록(김프 쌀수록) 큰 북, 높을수록(비쌀수록) 작은 북.
    """
    if z <= config.full_build_z:
        frac = 1.0
    elif z >= config.flat_z:
        frac = 0.0
    else:
        frac = (config.flat_z - z) / (config.flat_z - config.full_build_z)
    return max(0.0, min(1.0, frac)) * config.gross_cap_krw


# ── 5) 리밸런스: 두 레그 대칭 이동 ─────────────────────────


def plan_rebalance(
    current_notional_krw: float,
    target_notional_krw: float,
    quote: KimpQuote,
    lots: LotPair,
    config: SizingConfig,
) -> RebalanceOrder:
    """현재 북 크기에서 목표 크기로 가기 위한 **대칭** 주문을 만든다.

    확대=업비트 매수+숏 추가, 축소=업비트 매도+숏 환매. 두 레그가 항상 같은
    코인 수량만큼 함께 움직여 네이키드 노출이 생기지 않는다. 한 step 미만의
    미세 차이는 자연스럽게 HOLD 로 처리된다(불필요한 회전 방지).
    """
    diff_krw = target_notional_krw - current_notional_krw
    if diff_krw == 0:
        return RebalanceOrder(action=RebalanceAction.HOLD, reason="목표 == 현재")

    s_u = quote.upbit_krw
    ratio_b = config.short_ratio(quote.kimp)
    coarse_step = max(lots.upbit.step_size, lots.binance.step_size)

    dq_u = _round_step(abs(diff_krw) / s_u, coarse_step or lots.upbit.step_size)
    if dq_u <= 0:
        return RebalanceOrder(
            action=RebalanceAction.HOLD,
            reason="조정 수량이 step 미만",
        )
    dq_b = _round_step(dq_u * ratio_b, lots.binance.step_size)

    if diff_krw > 0:
        return RebalanceOrder(
            action=RebalanceAction.SCALE_UP,
            upbit_qty=dq_u,
            binance_qty=dq_b,
            upbit_side="BUY",
            binance_side="SELL",
            notional_krw=dq_u * s_u,
        )
    return RebalanceOrder(
        action=RebalanceAction.SCALE_DOWN,
        upbit_qty=dq_u,
        binance_qty=dq_b,
        upbit_side="SELL",
        binance_side="BUY",
        notional_krw=dq_u * s_u,
    )
