import type { QuickBacktestResponse } from "@/lib/types";

/**
 * Format quick backtest results into a concise text block for AI analysis.
 */
export function formatBacktestResultsForAI(
  result: QuickBacktestResponse,
  config: { symbol: string; interval: string; days: number },
): string {
  if (!result.success || !result.metrics) {
    return `[백테스트 실패 - ${config.symbol} ${config.interval}, ${config.days}일]\n오류: ${result.message ?? "알 수 없는 오류"}`;
  }

  const m = result.metrics;
  const trades = result.trades_summary;

  const longCount = trades.filter((t) => t.side === "LONG").length;
  const shortCount = trades.filter((t) => t.side === "SHORT").length;

  let bestTrade = 0;
  let worstTrade = 0;
  for (const t of trades) {
    if (t.return_pct > bestTrade) bestTrade = t.return_pct;
    if (t.return_pct < worstTrade) worstTrade = t.return_pct;
  }

  const lines = [
    `[백테스트 결과 - ${config.symbol} ${config.interval}, ${config.days}일]`,
    `수익률: ${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct}%, 최종 잔고: $${m.final_balance.toLocaleString()}`,
    `승률: ${m.win_rate}%, 거래수: ${m.total_trades}건`,
    `최대낙폭: -${m.max_drawdown_pct}%, 샤프비율: ${m.sharpe_ratio}`,
    `평균 수익: ${m.avg_win_pct >= 0 ? "+" : ""}${m.avg_win_pct}%, 평균 손실: ${m.avg_loss_pct}%`,
    `총 수수료: $${m.total_commission}`,
  ];

  if (m.total_trades > 0) {
    lines.push(`포지션 비율: 롱 ${longCount}건 / 숏 ${shortCount}건`);
  }
  if (bestTrade !== 0 || worstTrade !== 0) {
    lines.push(`최고 거래: ${bestTrade >= 0 ? "+" : ""}${bestTrade}%, 최저 거래: ${worstTrade}%`);
  }

  return lines.join("\n");
}
