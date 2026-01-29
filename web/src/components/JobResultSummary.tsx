"use client";

import type { JobType } from "@/lib/types";

type MetricTone = "neutral" | "positive" | "negative";
type Metric = { label: string; value: string; tone?: MetricTone };

const metricToneClass: Record<MetricTone, string> = {
  neutral: "text-[#d1d4dc]",
  positive: "text-[#26a69a]",
  negative: "text-[#ef5350]",
};

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const formatNumber = (value: number, digits = 2): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const formatSigned = (value: number, suffix = ""): string => {
  const formatted = formatNumber(value, 2);
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatted}${suffix ? ` ${suffix}` : ""}`;
};

function MetricCard({ label, value, tone = "neutral" }: Metric) {
  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
      <div className="text-xs text-[#868993]">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${metricToneClass[tone]}`}>{value}</div>
    </div>
  );
}

type TradeStats = {
  winRatePct: number;
  profitFactor: number;
  maxProfit: number | null;
  maxLoss: number | null;
  maxConsecutiveWins: number;
  maxConsecutiveLosses: number;
  totalTrades: number;
};

function computeBacktestTradeStats(trades: unknown): TradeStats | null {
  if (!Array.isArray(trades)) return null;
  const pnls = trades
    .map((t) => (isRecord(t) ? asNumber(t.pnl) : null))
    .filter((p): p is number => p !== null);
  if (pnls.length === 0) return null;

  let wins = 0;
  let losses = 0;
  let totalProfit = 0;
  let totalLoss = 0;
  let maxProfit = -Infinity;
  let maxLoss = Infinity;
  let maxConsecutiveWins = 0;
  let maxConsecutiveLosses = 0;
  let currentWins = 0;
  let currentLosses = 0;

  for (const pnl of pnls) {
    if (pnl > 0) {
      wins += 1;
      totalProfit += pnl;
      maxProfit = Math.max(maxProfit, pnl);
      currentWins += 1;
      currentLosses = 0;
      maxConsecutiveWins = Math.max(maxConsecutiveWins, currentWins);
    } else if (pnl < 0) {
      losses += 1;
      totalLoss += Math.abs(pnl);
      maxLoss = Math.min(maxLoss, pnl);
      currentLosses += 1;
      currentWins = 0;
      maxConsecutiveLosses = Math.max(maxConsecutiveLosses, currentLosses);
    } else {
      currentWins = 0;
      currentLosses = 0;
    }
  }

  const totalTrades = wins + losses;
  const winRatePct = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
  const profitFactor = totalLoss > 0 ? totalProfit / totalLoss : totalProfit > 0 ? Infinity : 0;

  return {
    winRatePct,
    profitFactor,
    maxProfit: Number.isFinite(maxProfit) ? maxProfit : null,
    maxLoss: Number.isFinite(maxLoss) ? maxLoss : null,
    maxConsecutiveWins,
    maxConsecutiveLosses,
    totalTrades,
  };
}

function BacktestResultSummary({ result }: { result: Record<string, unknown> }) {
  const initialBalance = asNumber(result.initial_balance);
  const finalBalance = asNumber(result.final_balance);
  const netProfit = asNumber(result.net_profit);
  const totalReturnPct = asNumber(result.total_return_pct);
  const totalTrades = asNumber(result.total_trades);
  const totalCommission = asNumber(result.total_commission);

  const metrics: Metric[] = [];
  if (totalReturnPct !== null) {
    metrics.push({
      label: "Return",
      value: `${formatNumber(totalReturnPct)}%`,
      tone: totalReturnPct >= 0 ? "positive" : "negative",
    });
  }
  if (initialBalance !== null) {
    metrics.push({ label: "Initial Balance", value: `${formatNumber(initialBalance)} USDT` });
  }
  if (finalBalance !== null) {
    metrics.push({ label: "Final Balance", value: `${formatNumber(finalBalance)} USDT` });
  }
  if (netProfit !== null) {
    metrics.push({
      label: "Net Profit",
      value: formatSigned(netProfit, "USDT"),
      tone: netProfit >= 0 ? "positive" : "negative",
    });
  }
  if (totalTrades !== null) {
    metrics.push({ label: "Total Trades", value: `${totalTrades}` });
  }
  if (totalCommission !== null) {
    metrics.push({ label: "Total Commission", value: `${formatNumber(totalCommission)} USDT`, tone: "negative" });
  }
  if (netProfit !== null && totalTrades !== null && totalTrades > 0) {
    metrics.push({
      label: "Avg Profit / Trade",
      value: formatSigned(netProfit / totalTrades, "USDT"),
      tone: netProfit >= 0 ? "positive" : "negative",
    });
  }

  const tradeStats = computeBacktestTradeStats(result.trades);
  const statsMetrics: Metric[] = tradeStats
    ? [
        { label: "Win Rate", value: `${formatNumber(tradeStats.winRatePct, 1)}%` },
        {
          label: "Profit Factor",
          value: tradeStats.profitFactor === Infinity ? "∞" : formatNumber(tradeStats.profitFactor),
        },
        tradeStats.maxProfit !== null
          ? { label: "Max Profit", value: formatSigned(tradeStats.maxProfit, "USDT"), tone: "positive" }
          : { label: "Max Profit", value: "-", tone: "neutral" },
        tradeStats.maxLoss !== null
          ? { label: "Max Loss", value: formatSigned(tradeStats.maxLoss, "USDT"), tone: "negative" }
          : { label: "Max Loss", value: "-", tone: "neutral" },
        { label: "Max Consecutive Wins", value: `${tradeStats.maxConsecutiveWins}` },
        { label: "Max Consecutive Losses", value: `${tradeStats.maxConsecutiveLosses}` },
      ]
    : [];

  return (
    <div className="mt-4 space-y-4">
      {metrics.length ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {metrics.map((metric) => (
            <MetricCard key={metric.label} {...metric} />
          ))}
        </div>
      ) : null}
      {statsMetrics.length ? (
        <div>
          <div className="mb-2 text-sm font-medium text-[#d1d4dc]">Trade Stats</div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {statsMetrics.map((metric) => (
              <MetricCard key={metric.label} {...metric} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function LiveResultSummary({ result }: { result: Record<string, unknown> }) {
  const summary = isRecord(result.summary) ? (result.summary as Record<string, unknown>) : result;
  const initialEquity = asNumber(summary.initial_equity);
  const finalEquity = asNumber(summary.final_equity);
  const totalReturnPct = asNumber(summary.total_return_pct);
  const maxDrawdownPct = asNumber(summary.max_drawdown_pct);
  const filledOrders = asNumber(summary.num_filled_orders);

  const metrics: Metric[] = [];
  if (totalReturnPct !== null) {
    metrics.push({
      label: "Total Return",
      value: `${formatNumber(totalReturnPct)}%`,
      tone: totalReturnPct >= 0 ? "positive" : "negative",
    });
  }
  if (initialEquity !== null) {
    metrics.push({ label: "Initial Equity", value: `${formatNumber(initialEquity)} USDT` });
  }
  if (finalEquity !== null) {
    metrics.push({ label: "Final Equity", value: `${formatNumber(finalEquity)} USDT` });
  }
  if (maxDrawdownPct !== null) {
    metrics.push({ label: "Max Drawdown", value: `${formatNumber(maxDrawdownPct)}%`, tone: "negative" });
  }

  const symbols = isRecord(summary.symbols) ? summary.symbols : null;
  const symbolRows = symbols
    ? Object.entries(symbols).map(([symbol, info]) => {
        const record = isRecord(info) ? info : {};
        return {
          symbol,
          positionSize: asNumber(record.position_size),
          unrealizedPnl: asNumber(record.unrealized_pnl),
          filledOrders: asNumber(record.num_filled_orders),
        };
      })
    : [];
  const derivedFilledOrders =
    filledOrders ?? (symbolRows.length ? symbolRows.reduce((sum, row) => sum + (row.filledOrders ?? 0), 0) : null);
  if (derivedFilledOrders !== null) {
    metrics.push({ label: "Filled Orders", value: `${derivedFilledOrders}` });
  }

  const riskStatus = isRecord(summary.risk_status) ? summary.risk_status : null;
  const riskMetrics: Metric[] = [];
  if (riskStatus && isRecord(riskStatus)) {
    const dailyPnl = asNumber(riskStatus.daily_pnl);
    const consecutiveLosses = asNumber(riskStatus.consecutive_losses);
    const isInCooldown = riskStatus.is_in_cooldown;
    if (dailyPnl !== null) {
      riskMetrics.push({
        label: "Daily PnL",
        value: formatSigned(dailyPnl, "USDT"),
        tone: dailyPnl >= 0 ? "positive" : "negative",
      });
    }
    if (consecutiveLosses !== null) {
      riskMetrics.push({ label: "Consecutive Losses", value: `${consecutiveLosses}` });
    }
    if (typeof isInCooldown === "boolean") {
      riskMetrics.push({ label: "Cooldown", value: isInCooldown ? "On" : "Off" });
    }
  }

  return (
    <div className="mt-4 space-y-4">
      {metrics.length ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {metrics.map((metric) => (
            <MetricCard key={metric.label} {...metric} />
          ))}
        </div>
      ) : null}
      {riskMetrics.length ? (
        <div>
          <div className="mb-2 text-sm font-medium text-[#d1d4dc]">Risk Status</div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {riskMetrics.map((metric) => (
              <MetricCard key={metric.label} {...metric} />
            ))}
          </div>
        </div>
      ) : null}
      {symbolRows.length ? (
        <div className="rounded border border-[#2a2e39] bg-[#131722]">
          <div className="border-b border-[#2a2e39] px-4 py-2 text-xs font-medium text-[#d1d4dc]">
            Symbols
          </div>
          <table className="w-full text-xs">
            <thead className="bg-[#131722]">
              <tr className="border-b border-[#2a2e39] text-left text-[#868993]">
                <th className="px-4 py-2">Symbol</th>
                <th className="px-4 py-2">Position</th>
                <th className="px-4 py-2">Unrealized PnL</th>
                <th className="px-4 py-2">Filled Orders</th>
              </tr>
            </thead>
            <tbody>
              {symbolRows.map((row) => (
                <tr key={row.symbol} className="border-b border-[#2a2e39]">
                  <td className="px-4 py-2 text-[#d1d4dc]">{row.symbol}</td>
                  <td className="px-4 py-2 text-[#d1d4dc]">
                    {row.positionSize !== null ? formatNumber(row.positionSize, 4) : "-"}
                  </td>
                  <td
                    className={`px-4 py-2 font-medium ${
                      (row.unrealizedPnl ?? 0) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"
                    }`}
                  >
                    {row.unrealizedPnl !== null ? formatSigned(row.unrealizedPnl, "USDT") : "-"}
                  </td>
                  <td className="px-4 py-2 text-[#d1d4dc]">
                    {row.filledOrders !== null ? row.filledOrders : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

export function JobResultSummary({ type, result }: { type: JobType; result: Record<string, unknown> }) {
  return type === "BACKTEST" ? (
    <BacktestResultSummary result={result} />
  ) : (
    <LiveResultSummary result={result} />
  );
}
