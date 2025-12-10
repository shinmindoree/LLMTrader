"""백테스트 리포트 생성."""

import math
from typing import Any


def calculate_sharpe_ratio(equity_curve: list[dict[str, Any]], risk_free_rate: float = 0.0) -> float:
    """샤프 비율 계산.

    Args:
        equity_curve: 에쿼티 커브 데이터
        risk_free_rate: 무위험 이자율 (연율, 기본 0%)

    Returns:
        샤프 비율
    """
    if len(equity_curve) < 2:
        return 0.0

    returns = []
    for i in range(1, len(equity_curve)):
        prev_equity = equity_curve[i - 1]["total_equity"]
        curr_equity = equity_curve[i]["total_equity"]
        if prev_equity > 0:
            returns.append((curr_equity - prev_equity) / prev_equity)

    if not returns:
        return 0.0

    mean_return = sum(returns) / len(returns)
    if len(returns) < 2:
        return 0.0

    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        return 0.0

    # 연율화 (가정: 1일 단위 리턴)
    annual_return = mean_return * 365
    annual_std = std_dev * math.sqrt(365)

    return (annual_return - risk_free_rate) / annual_std


def calculate_win_rate(equity_curve: list[dict[str, Any]]) -> dict[str, Any]:
    """승률 계산 (포지션 변화 기준).

    Args:
        equity_curve: 에쿼티 커브 데이터

    Returns:
        승률 통계 {win_rate, num_trades, num_wins, num_losses}
    """
    if len(equity_curve) < 2:
        return {"win_rate": 0.0, "num_trades": 0, "num_wins": 0, "num_losses": 0}

    # 포지션이 0으로 돌아온 시점을 거래 종료로 간주
    trades = []
    entry_equity = None
    for i, point in enumerate(equity_curve):
        if entry_equity is None and point["position_size"] != 0:
            entry_equity = equity_curve[i - 1]["total_equity"] if i > 0 else point["total_equity"]
        elif entry_equity is not None and point["position_size"] == 0:
            exit_equity = point["total_equity"]
            pnl = exit_equity - entry_equity
            trades.append({"pnl": pnl, "is_win": pnl > 0})
            entry_equity = None

    if not trades:
        return {"win_rate": 0.0, "num_trades": 0, "num_wins": 0, "num_losses": 0}

    num_wins = sum(1 for t in trades if t["is_win"])
    num_losses = len(trades) - num_wins
    win_rate = num_wins / len(trades) if trades else 0.0

    return {
        "win_rate": win_rate,
        "win_rate_pct": win_rate * 100,
        "num_trades": len(trades),
        "num_wins": num_wins,
        "num_losses": num_losses,
    }


def generate_full_report(
    summary: dict[str, Any],
    equity_curve: list[dict[str, Any]],
) -> dict[str, Any]:
    """전체 백테스트 리포트 생성.

    Args:
        summary: 기본 요약 통계
        equity_curve: 에쿼티 커브 데이터

    Returns:
        상세 리포트
    """
    sharpe = calculate_sharpe_ratio(equity_curve)
    win_stats = calculate_win_rate(equity_curve)

    return {
        **summary,
        "sharpe_ratio": sharpe,
        **win_stats,
    }




