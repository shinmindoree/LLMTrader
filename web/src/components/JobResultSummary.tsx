"use client";

import { useI18n } from "@/lib/i18n";
import type { JobType, Trade } from "@/lib/types";

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

function pnlsFromTrades(trades: unknown): number[] {
  if (!Array.isArray(trades)) return [];
  return trades
    .map((t) => (isRecord(t) ? asNumber(t.pnl) : null))
    .filter((p): p is number => p !== null);
}

function computeTradeStatsFromPnls(pnls: number[]): TradeStats | null {
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

function computeBacktestTradeStats(trades: unknown): TradeStats | null {
  return computeTradeStatsFromPnls(pnlsFromTrades(trades));
}

function BacktestResultSummary({ result }: { result: Record<string, unknown> }) {
  const { t } = useI18n();
  const initialBalance = asNumber(result.initial_balance);
  const finalBalance = asNumber(result.final_balance);
  const netProfit = asNumber(result.net_profit);
  const totalReturnPct = asNumber(result.total_return_pct);
  const totalTrades = asNumber(result.total_trades);
  const totalCommission = asNumber(result.total_commission);

  const metrics: Metric[] = [];
  if (totalReturnPct !== null) {
    metrics.push({
      label: t.result.return,
      value: `${formatNumber(totalReturnPct)}%`,
      tone: totalReturnPct >= 0 ? "positive" : "negative",
    });
  }
  if (initialBalance !== null) {
    metrics.push({ label: t.result.initialBalance, value: `${formatNumber(initialBalance)} USDT` });
  }
  if (finalBalance !== null) {
    metrics.push({ label: t.result.finalBalance, value: `${formatNumber(finalBalance)} USDT` });
  }
  if (netProfit !== null) {
    metrics.push({
      label: t.result.netProfit,
      value: formatSigned(netProfit, "USDT"),
      tone: netProfit >= 0 ? "positive" : "negative",
    });
  }
  if (totalTrades !== null) {
    metrics.push({ label: t.result.totalTrades, value: `${totalTrades}` });
  }
  if (totalCommission !== null) {
    metrics.push({ label: t.result.totalCommission, value: `${formatNumber(totalCommission)} USDT`, tone: "negative" });
  }
  if (netProfit !== null && totalTrades !== null && totalTrades > 0) {
    metrics.push({
      label: t.result.avgProfitPerTrade,
      value: formatSigned(netProfit / totalTrades, "USDT"),
      tone: netProfit >= 0 ? "positive" : "negative",
    });
  }

  const tradeStats = computeBacktestTradeStats(result.trades);
  const statsMetrics: Metric[] = tradeStats
    ? [
        { label: t.result.winRate, value: `${formatNumber(tradeStats.winRatePct, 1)}%` },
        {
          label: t.result.profitFactor,
          value: tradeStats.profitFactor === Infinity ? "∞" : formatNumber(tradeStats.profitFactor),
        },
        tradeStats.maxProfit !== null
          ? { label: t.result.maxProfit, value: formatSigned(tradeStats.maxProfit, "USDT"), tone: "positive" }
          : { label: t.result.maxProfit, value: "-", tone: "neutral" },
        tradeStats.maxLoss !== null
          ? { label: t.result.maxLoss, value: formatSigned(tradeStats.maxLoss, "USDT"), tone: "negative" }
          : { label: t.result.maxLoss, value: "-", tone: "neutral" },
        { label: t.result.maxConsecutiveWins, value: `${tradeStats.maxConsecutiveWins}` },
        { label: t.result.maxConsecutiveLosses, value: `${tradeStats.maxConsecutiveLosses}` },
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
          <div className="mb-2 text-sm font-medium text-[#d1d4dc]">{t.result.tradeStats}</div>
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

function LiveResultSummary({
  result,
  liveTrades,
}: {
  result: Record<string, unknown>;
  liveTrades: Trade[];
}) {
  const { t } = useI18n();
  const summary = isRecord(result.summary) ? (result.summary as Record<string, unknown>) : result;
  const summaryInitialEquity = asNumber(summary.initial_equity);
  const summaryFinalEquity = asNumber(summary.final_equity);
  const summaryReturnPct = asNumber(summary.total_return_pct);
  const summaryNetProfit = asNumber(summary.net_profit);
  const summaryTotalCommission = asNumber(summary.total_commission);
  const summaryNumTrades = asNumber(summary.num_trades);

  const netProfit =
    liveTrades.length > 0
      ? liveTrades.reduce((s, t) => s + (t.realized_pnl ?? 0), 0)
      : (summaryNetProfit ?? 0);
  const totalCommission =
    liveTrades.length > 0
      ? liveTrades.reduce((s, t) => s + (t.commission ?? 0), 0)
      : (summaryTotalCommission ?? 0);
  const numTrades = liveTrades.length > 0 ? liveTrades.length : (summaryNumTrades ?? 0);
  const sortedByTime = [...liveTrades].sort((a, b) => {
    const ta = typeof a.ts === "number" ? a.ts : Date.parse(String(a.ts ?? 0)) || 0;
    const tb = typeof b.ts === "number" ? b.ts : Date.parse(String(b.ts ?? 0)) || 0;
    return ta - tb;
  });
  const pnls = sortedByTime
    .map((t) => t.realized_pnl)
    .filter((p): p is number => p !== null && p !== undefined && Number.isFinite(p));
  const initialEquity = summaryInitialEquity;
  const finalEquity =
    summaryFinalEquity ??
    (initialEquity !== null ? initialEquity + netProfit - totalCommission : null);
  const totalReturnPct =
    summaryReturnPct ??
    (initialEquity != null && initialEquity > 0 && finalEquity !== null
      ? ((finalEquity - initialEquity) / initialEquity) * 100
      : null);

  const metrics: Metric[] = [];
  if (totalReturnPct !== null) {
    metrics.push({
      label: t.result.return,
      value: `${formatNumber(totalReturnPct)}%`,
      tone: totalReturnPct >= 0 ? "positive" : "negative",
    });
  }
  if (initialEquity !== null) {
    metrics.push({ label: t.result.initialEquity, value: `${formatNumber(initialEquity)} USDT` });
  }
  if (finalEquity !== null) {
    metrics.push({ label: t.result.finalEquity, value: `${formatNumber(finalEquity)} USDT` });
  }
  metrics.push({
    label: t.result.netProfit,
    value: formatSigned(netProfit, "USDT"),
    tone: netProfit >= 0 ? "positive" : "negative",
  });
  metrics.push({ label: t.result.totalTrades, value: `${numTrades}` });
  metrics.push({
    label: t.result.totalCommission,
    value: `${formatNumber(totalCommission)} USDT`,
    tone: "negative",
  });
  if (numTrades > 0) {
    metrics.push({
      label: t.result.avgProfitPerTrade,
      value: formatSigned(netProfit / numTrades, "USDT"),
      tone: netProfit >= 0 ? "positive" : "negative",
    });
  }

  const tradeStats = computeTradeStatsFromPnls(pnls);
  const statsMetrics: Metric[] = tradeStats
    ? [
        { label: t.result.winRate, value: `${formatNumber(tradeStats.winRatePct, 1)}%` },
        {
          label: t.result.profitFactor,
          value: tradeStats.profitFactor === Infinity ? "∞" : formatNumber(tradeStats.profitFactor),
        },
        tradeStats.maxProfit !== null
          ? { label: t.result.maxProfit, value: formatSigned(tradeStats.maxProfit, "USDT"), tone: "positive" }
          : { label: t.result.maxProfit, value: "-", tone: "neutral" },
        tradeStats.maxLoss !== null
          ? { label: t.result.maxLoss, value: formatSigned(tradeStats.maxLoss, "USDT"), tone: "negative" }
          : { label: t.result.maxLoss, value: "-", tone: "neutral" },
        { label: t.result.maxConsecutiveWins, value: `${tradeStats.maxConsecutiveWins}` },
        { label: t.result.maxConsecutiveLosses, value: `${tradeStats.maxConsecutiveLosses}` },
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
          <div className="mb-2 text-sm font-medium text-[#d1d4dc]">{t.result.tradeStats}</div>
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

export function JobResultSummary({
  type,
  result,
  liveTrades,
}: {
  type: JobType;
  result: Record<string, unknown>;
  liveTrades?: Trade[];
}) {
  return type === "BACKTEST" ? (
    <BacktestResultSummary result={result} />
  ) : (
    <LiveResultSummary result={result} liveTrades={liveTrades ?? []} />
  );
}
