"""백테스트용 리스크 관리자."""

from common.risk import BaseRiskManager, RiskConfig


class BacktestRiskManager(BaseRiskManager):
    """백테스트용 리스크 관리자.

    시간 기반 로직(can_trade, record_trade)은 제외하고
    기본 검증 로직만 제공합니다.
    """

    def __init__(self, config: RiskConfig) -> None:
        """백테스트 리스크 관리자 초기화.

        Args:
            config: 리스크 설정
        """
        super().__init__(config)

