"""동적 캘리(Dynamic Kelly) 기반 범용 포지션 사이저.

이 모듈은 **특정 전략에 종속되지 않는** Generic 포지션 사이징 컴포넌트이다.
전략 내부의 지표나 신호 로직을 전혀 알 필요가 없으며, 오직 다음 두 종류의
입력만 사용한다.

1. **통계 데이터** — 최근 거래 PnL 시퀀스 또는 (승률 ``p``, 손익비 ``b``).
2. **계좌/시스템 상태** — 현재 MDD(%) 및 전략의 최대 허용 MDD(%).

산출 절차는 다음 3단계로 구성된다.

* (a) **번인(Burn-in) 가드** — 표본 크기가
  ``min_trades_required`` 미만이면 캘리 공식을 사용하지 않고
  보수적인 고정 비중(``burn_in_leverage``)을 반환한다.
* (b) **Half-Kelly** — 꼬리 위험(fat-tail) 방어를 위해
  표준 캘리 비율 ``f* = p - (1-p)/b`` 에 ``kelly_fraction`` (기본 0.5)을
  곱해 축소한다. 음수가 나오면 0으로 클램프한다.
* (c) **MDD 페널티** — 현재 MDD가 최대 허용 MDD에 근접할수록
  0에 수렴하는 선형 감쇠 계수(``1 - current/max``)를 곱한다.

최종 결과는 항상 ``[0.0, max_leverage]`` 범위로 클램프된다.

이 모듈은 NumPy/Pandas/Redis 등 외부 의존성이 없으며 순수 표준 라이브러리만
사용한다. 백테스트·라이브 양쪽에서 동일하게 호출할 수 있다.
"""

import math
from collections.abc import Sequence


class DynamicKellyRiskManager:
    """동적 캘리 + MDD 방어 페널티 기반 목표 비중 산출기.

    인스턴스는 상태(state)를 갖지 않고 정책 파라미터만 보관한다. 따라서
    어떤 전략, 어떤 심볼에도 동일한 인스턴스를 안전하게 공유 사용할 수 있다.

    Attributes:
        min_trades_required: 캘리 공식을 신뢰하기 위한 최소 거래 표본 수.
            이 값 미만이면 ``burn_in_leverage`` 가 반환된다.
        burn_in_leverage: 번인 구간(콜드 스타트)에서 사용할 보수적인
            고정 비중. ``[0, max_leverage]`` 범위여야 한다.
        kelly_fraction: 표준 캘리 비율에 곱할 축소 계수. 기본값
            ``0.5`` 는 Half-Kelly 를 의미한다. ``(0, 1]`` 범위여야 한다.
        max_leverage: 최종 비중의 절대 상한. 페널티/캘리 결과가 아무리 커도
            이 값을 초과하지 않는다.
    """

    def __init__(
        self,
        min_trades_required: int = 30,
        burn_in_leverage: float = 0.01,
        kelly_fraction: float = 0.5,
        max_leverage: float = 1.0,
    ) -> None:
        """파라미터 검증과 함께 인스턴스를 초기화한다.

        Args:
            min_trades_required: 번인 임계 거래 수 (기본 30).
            burn_in_leverage: 번인 구간 고정 비중 (기본 0.01 = 1%).
            kelly_fraction: 캘리 축소 계수 (기본 0.5 = Half-Kelly).
            max_leverage: 최종 비중의 절대 상한 (기본 1.0 = 100%).

        Raises:
            ValueError: 파라미터가 허용 범위를 벗어난 경우.
        """
        if min_trades_required < 0:
            raise ValueError(
                f"min_trades_required 는 0 이상이어야 합니다: {min_trades_required}"
            )
        if max_leverage <= 0:
            raise ValueError(f"max_leverage 는 양수여야 합니다: {max_leverage}")
        if not (0.0 <= burn_in_leverage <= max_leverage):
            raise ValueError(
                "burn_in_leverage 는 [0, max_leverage] 범위여야 합니다: "
                f"burn_in_leverage={burn_in_leverage}, max_leverage={max_leverage}"
            )
        if not (0.0 < kelly_fraction <= 1.0):
            raise ValueError(
                f"kelly_fraction 은 (0, 1] 범위여야 합니다: {kelly_fraction}"
            )

        self.min_trades_required = int(min_trades_required)
        self.burn_in_leverage = float(burn_in_leverage)
        self.kelly_fraction = float(kelly_fraction)
        self.max_leverage = float(max_leverage)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_target_leverage(
        self,
        *,
        current_mdd_pct: float,
        max_allowed_mdd_pct: float,
        trades: Sequence[float] | None = None,
        win_rate: float | None = None,
        payoff_ratio: float | None = None,
        n_trades: int | None = None,
    ) -> float:
        """현재 통계와 MDD 상태로부터 목표 비중을 산출한다.

        호출자는 ``trades`` 만 넘기거나, 또는 직접 계산한
        ``win_rate`` + ``payoff_ratio`` 조합을 넘길 수 있다(두 방식 중
        정확히 하나만 사용해야 한다). 후자의 경우 번인 가드를 적용받으려면
        ``n_trades`` 도 함께 넘겨야 한다.

        Args:
            current_mdd_pct: 현재 시스템의 MDD. 0 이상.
                ``max_allowed_mdd_pct`` 와 **동일한 단위(% 또는 비율)** 로
                넘겨야 한다 — 예) 둘 다 0~100 스케일이거나 둘 다 0~1 스케일.
            max_allowed_mdd_pct: 전략의 최대 허용 MDD. 양수여야 한다.
            trades: 최근 거래 PnL 시퀀스. 단위는 임의(USDT, 수익률 등)이며
                부호(>0 승, <0 패)만 사용된다. ``win_rate`` /
                ``payoff_ratio`` 와 동시에 지정할 수 없다.
            win_rate: 승률 ``p`` (0~1). ``payoff_ratio`` 와 함께 지정해야
                한다.
            payoff_ratio: 손익비 ``b`` (평균 수익 / 평균 손실의 절대값).
                0 이상이어야 한다.
            n_trades: ``win_rate`` / ``payoff_ratio`` 경로에서 번인 검사에
                사용할 거래 수. 생략 시 번인 검사를 건너뛴다.

        Returns:
            ``[0.0, max_leverage]`` 범위로 클램프된 목표 비중.

        Raises:
            ValueError: 입력 파라미터가 상호 배타 조건을 위배하거나
                값이 허용 범위를 벗어난 경우.
        """
        p, b, n = self._resolve_stats(
            trades=trades,
            win_rate=win_rate,
            payoff_ratio=payoff_ratio,
            n_trades=n_trades,
        )

        if current_mdd_pct < 0:
            raise ValueError(
                f"current_mdd_pct 는 0 이상이어야 합니다: {current_mdd_pct}"
            )
        if max_allowed_mdd_pct <= 0:
            raise ValueError(
                f"max_allowed_mdd_pct 는 양수여야 합니다: {max_allowed_mdd_pct}"
            )

        penalty = self._mdd_penalty(current_mdd_pct, max_allowed_mdd_pct)

        # 번인 구간: 캘리 공식을 신뢰할 표본이 부족하면 보수적 고정 비중.
        # MDD 페널티는 번인 구간에도 동일하게 적용한다 — 표본이 적더라도
        # 시스템이 이미 큰 손실 중이라면 그만큼 더 줄여야 안전하다.
        if n is not None and n < self.min_trades_required:
            return self._clamp(self.burn_in_leverage * penalty)

        kelly = self.compute_kelly_fraction(p, b)
        target = self.kelly_fraction * kelly * penalty
        return self._clamp(target)

    # ------------------------------------------------------------------
    # Stateless utilities (also useful for tests / analytics)
    # ------------------------------------------------------------------
    @staticmethod
    def compute_win_rate_and_payoff(
        trades: Sequence[float],
    ) -> tuple[float, float, int]:
        """PnL 시퀀스로부터 ``(승률 p, 손익비 b, 거래 수 n)`` 을 계산한다.

        ``pnl == 0`` 인 거래(브레이크이븐)는 표본에서 제외된다 — 캘리 공식은
        승/패 이진 분류를 가정하기 때문이다.

        Args:
            trades: 거래 PnL 시퀀스. 부호로 승/패를 판정한다.

        Returns:
            ``(p, b, n)`` 튜플.

            * ``p``: 승률 (0~1). 표본이 비어 있으면 0.0.
            * ``b``: 평균 수익 / 평균 손실의 절대값. 손실 거래가 0건이면
              ``math.inf`` (무한 손익비), 승리 거래가 0건이면 0.0.
            * ``n``: 0이 아닌 PnL을 가진 표본 수.
        """
        wins: list[float] = []
        losses: list[float] = []
        for pnl in trades:
            v = float(pnl)
            if not math.isfinite(v):
                continue
            if v > 0:
                wins.append(v)
            elif v < 0:
                losses.append(-v)  # 절대값으로 저장
            # v == 0 인 거래는 무시.

        n = len(wins) + len(losses)
        if n == 0:
            return 0.0, 0.0, 0

        p = len(wins) / n

        if not losses:
            # 손실 없음 → 손익비는 사실상 무한. 캘리 식에서
            # ``(1-p)/b → 0`` 이 되어 f* ≈ p 로 수렴한다.
            b = math.inf
        elif not wins:
            b = 0.0
        else:
            avg_win = sum(wins) / len(wins)
            avg_loss = sum(losses) / len(losses)
            b = avg_win / avg_loss

        return p, b, n

    @staticmethod
    def compute_kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
        """순수 캘리 비율 ``f* = p - (1 - p) / b`` 를 계산한다.

        결과가 음수이면(즉, 시스템이 기대값에서 음수 엣지를 가질 때)
        ``0.0`` 으로 클램프한다 — 음수 베팅은 의미가 없기 때문이다.

        Args:
            win_rate: 승률 ``p`` (0~1).
            payoff_ratio: 손익비 ``b`` (0 이상). ``math.inf`` 도 허용 — 이
                경우 ``(1-p)/b == 0`` 으로 처리되어 ``f* = p`` 가 된다.

        Returns:
            ``[0.0, 1.0]`` 범위의 캘리 비율 (1.0 으로도 클램프).

        Raises:
            ValueError: ``win_rate`` 또는 ``payoff_ratio`` 가 허용 범위를
                벗어난 경우.
        """
        if not (0.0 <= win_rate <= 1.0):
            raise ValueError(f"win_rate 는 [0, 1] 범위여야 합니다: {win_rate}")
        if payoff_ratio < 0 or math.isnan(payoff_ratio):
            raise ValueError(
                f"payoff_ratio 는 0 이상의 유한값 또는 inf여야 합니다: {payoff_ratio}"
            )

        if payoff_ratio == 0.0:
            # 모든 거래가 손실(또는 승리 평균이 0) → 베팅 금지.
            return 0.0
        if math.isinf(payoff_ratio):
            # 손실이 없는 경우 → (1-p)/b == 0 한계.
            f = win_rate
        else:
            f = win_rate - (1.0 - win_rate) / payoff_ratio

        # 음수 엣지: 베팅 금지. 1 초과: 캘리 정의상 불가능하지만 방어적
        # 으로 클램프.
        if f < 0.0:
            return 0.0
        if f > 1.0:
            return 1.0
        return f

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _resolve_stats(
        self,
        *,
        trades: Sequence[float] | None,
        win_rate: float | None,
        payoff_ratio: float | None,
        n_trades: int | None,
    ) -> tuple[float, float, int | None]:
        """입력 모드를 판별해 ``(p, b, n)`` 으로 정규화한다.

        ``trades`` 경로와 ``(win_rate, payoff_ratio)`` 경로는 상호 배타이다.
        """
        trades_provided = trades is not None
        stats_provided = win_rate is not None or payoff_ratio is not None

        if trades_provided and stats_provided:
            raise ValueError(
                "'trades' 와 'win_rate/payoff_ratio' 는 동시에 지정할 수 없습니다."
            )
        if not trades_provided and not stats_provided:
            raise ValueError(
                "'trades' 또는 'win_rate'+'payoff_ratio' 중 하나는 반드시 지정해야 합니다."
            )

        if trades_provided:
            assert trades is not None  # for type checkers
            if n_trades is not None:
                raise ValueError(
                    "'trades' 경로에서는 'n_trades' 를 별도로 지정할 수 없습니다 "
                    "(len(trades) 가 자동으로 사용됩니다)."
                )
            p, b, n = self.compute_win_rate_and_payoff(trades)
            return p, b, n

        # stats_provided 경로.
        if win_rate is None or payoff_ratio is None:
            raise ValueError(
                "'win_rate' 와 'payoff_ratio' 는 함께 지정해야 합니다."
            )
        if n_trades is not None and n_trades < 0:
            raise ValueError(f"n_trades 는 0 이상이어야 합니다: {n_trades}")
        return float(win_rate), float(payoff_ratio), n_trades

    @staticmethod
    def _mdd_penalty(current_mdd_pct: float, max_allowed_mdd_pct: float) -> float:
        """선형 감쇠 페널티 계수 ``1 - current/max`` 를 ``[0, 1]`` 로 클램프.

        * ``current == 0`` → 1.0 (페널티 없음).
        * ``current == max`` → 0.0 (베팅 금지).
        * ``current > max`` → 0.0 (이미 한도 초과, 즉시 비중 0).
        """
        ratio = current_mdd_pct / max_allowed_mdd_pct
        penalty = 1.0 - ratio
        if penalty < 0.0:
            return 0.0
        if penalty > 1.0:
            return 1.0
        return penalty

    def _clamp(self, value: float) -> float:
        """최종 비중을 ``[0, max_leverage]`` 범위로 클램프한다."""
        if not math.isfinite(value) or value < 0.0:
            return 0.0
        if value > self.max_leverage:
            return self.max_leverage
        return value
