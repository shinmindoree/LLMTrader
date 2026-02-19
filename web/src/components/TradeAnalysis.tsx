"use client";

import { useCallback, useMemo, useState } from "react";

import type { Job, Trade } from "@/lib/types";
import { isRecord } from "@/components/JobResultSummary";

type NormalizedTrade = {
  id: string | number;
  timestamp: number | null;
  timeLabel: string;
  symbol: string | null;
  side: string | null;
  quantity: number | null;
  price: number | null;
  pnl: number | null;
  commission: number | null;
  positionSizeUsdt: number | null;
  entryPrice: number | null;
  balanceAfter: number | null;
  reason: string | null;
  exitReason: string | null;
};

type ChartPoint = {
  index: number;
  timestamp: number | null;
  pnl: number;
  equity: number;
  symbol: string | null;
  side: string | null;
  positionSizeUsdt: number | null;
  reason: string | null;
};

const asNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const asString = (value: unknown): string | null =>
  typeof value === "string" && value.trim() ? value : null;

const formatNumber = (value: number, digits = 2): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const formatSigned = (value: number, suffix = ""): string => {
  const formatted = formatNumber(value, 2);
  const sign = value > 0 ? "+" : value < 0 ? "" : "";
  return `${sign}${formatted}${suffix ? ` ${suffix}` : ""}`;
};

type MetricTone = "neutral" | "positive" | "negative";
const metricToneClass: Record<MetricTone, string> = {
  neutral: "text-[#d1d4dc]",
  positive: "text-[#26a69a]",
  negative: "text-[#ef5350]",
};

function MetricCard({ label, value, tone = "neutral" }: { label: string; value: string; tone?: MetricTone }) {
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
};

function computeTradeStats(pnls: number[]): TradeStats | null {
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
  };
}

function escapeCsvValue(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function formatDateTime(ms: number | null): string {
  if (!ms) return "-";
  const dt = new Date(ms);
  if (Number.isNaN(dt.getTime())) return "-";
  return dt.toLocaleString();
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) return parsed;
  }
  return null;
}

function normalizeBacktestTrades(raw: unknown): NormalizedTrade[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((t) => isRecord(t))
    .map((t, idx) => {
      const timestamp = asNumber(t.timestamp) ?? null;
      return {
        id: idx + 1,
        timestamp,
        timeLabel: formatDateTime(timestamp),
        symbol: asString(t.symbol),
        side: asString(t.side),
        quantity: asNumber(t.quantity),
        price: asNumber(t.price),
        pnl: asNumber(t.pnl),
        commission: asNumber(t.commission),
        positionSizeUsdt: asNumber(t.position_size_usdt),
        entryPrice: asNumber(t.entry_price),
        balanceAfter: asNumber(t.balance_after),
        reason: asString(t.reason),
        exitReason: asString(t.exit_reason),
      } satisfies NormalizedTrade;
    });
}

function normalizeLiveTrades(trades: Trade[]): NormalizedTrade[] {
  return trades.map((t) => {
    const raw = isRecord(t.raw) ? (t.raw as Record<string, unknown>) : null;
    const rawSide = raw ? asString(raw.side) : null;
    const buyer = raw && typeof raw.buyer === "boolean" ? raw.buyer : null;
    const seller = raw && typeof raw.seller === "boolean" ? raw.seller : null;
    const derivedSide = rawSide ?? (buyer ? "BUY" : seller ? "SELL" : null);
    const timestamp =
      parseTimestamp(t.ts) ??
      parseTimestamp(raw?.time) ??
      parseTimestamp(raw?.tradeTime) ??
      parseTimestamp(raw?.T);
    const quantity = t.quantity ?? null;
    const price = t.price ?? null;
    const positionSizeUsdt =
      quantity !== null && price !== null ? quantity * price : null;
    const isExit = derivedSide === "SELL" && t.realized_pnl != null;
    const reasonFromRaw = raw ? asString(raw.reason) : null;
    const exitReasonFromRaw = raw ? asString(raw.exit_reason) : null;
    const reason =
      reasonFromRaw ?? exitReasonFromRaw ?? (derivedSide === "BUY" ? "Entry Long" : "Exit Long");
    const exitReason = exitReasonFromRaw ?? (isExit ? "Exit Long" : null);
    return {
      id: t.trade_id,
      timestamp,
      timeLabel: formatDateTime(timestamp),
      symbol: t.symbol ?? null,
      side: derivedSide,
      quantity,
      price,
      pnl: t.realized_pnl ?? null,
      commission: t.commission ?? null,
      positionSizeUsdt,
      entryPrice: null,
      balanceAfter: null,
      reason,
      exitReason,
    } satisfies NormalizedTrade;
  });
}

function buildEquitySeries(trades: NormalizedTrade[], initialEquity: number | null): ChartPoint[] {
  let equity = initialEquity ?? 0;
  const points: ChartPoint[] = [];
  let tradeIndex = 0;

  for (const trade of trades) {
    const pnl = trade.pnl ?? 0;
    const commission = trade.commission ?? 0;
    equity += pnl - commission;

    if (trade.pnl !== null && trade.pnl !== 0) {
      tradeIndex += 1;
      points.push({
        index: tradeIndex,
        timestamp: trade.timestamp,
        pnl: trade.pnl,
        equity,
        symbol: trade.symbol,
        side: trade.side,
        positionSizeUsdt: trade.positionSizeUsdt,
        reason: trade.exitReason ?? trade.reason,
      });
    }
  }

  return points;
}

function enrichBacktestTrades(
  trades: NormalizedTrade[],
  initialBalance: number | null,
): Array<NormalizedTrade & { balanceChange: number | null }> {
  let previousBalance = initialBalance;
  return trades.map((trade) => {
    let balanceChange: number | null = null;
    if (trade.balanceAfter !== null && previousBalance !== null) {
      if (trade.pnl !== null && trade.pnl !== 0) {
        balanceChange = trade.balanceAfter - previousBalance;
        previousBalance = trade.balanceAfter;
      } else {
        previousBalance = trade.balanceAfter;
      }
    }
    return { ...trade, balanceChange };
  });
}

function enrichLiveTrades(
  trades: NormalizedTrade[],
  initialEquity: number | null,
): Array<NormalizedTrade & { balanceChange: number | null; balanceAfter: number | null }> {
  if (initialEquity === null) {
    return trades.map((trade) => ({ ...trade, balanceChange: null, balanceAfter: null }));
  }
  let equity = initialEquity;
  return trades.map((trade) => {
    const pnl = trade.pnl ?? 0;
    const commission = trade.commission ?? 0;
    const delta = pnl - commission;
    equity += delta;
    return { ...trade, balanceChange: delta, balanceAfter: equity };
  });
}

function Chart({
  points,
  showEquity,
  backtestSymbol,
}: {
  points: ChartPoint[];
  showEquity: boolean;
  backtestSymbol: string | null;
}) {
  const [hoveredPoint, setHoveredPoint] = useState<ChartPoint | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  if (!points.length) {
    return (
      <div className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-6 text-center text-xs text-[#868993]">
        No trade PnL data to chart yet.
      </div>
    );
  }

  const width = 900;
  const height = 320;
  const padding = 36;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;
  const step = points.length > 1 ? plotWidth / (points.length - 1) : plotWidth;
  const barWidth = Math.max(4, Math.min(18, step * 0.6));

  const pnlValues = points.map((p) => p.pnl);
  const maxAbsPnl = Math.max(...pnlValues.map((v) => Math.abs(v)), 1);
  const yZero = padding + plotHeight / 2;
  const pnlScale = plotHeight / (2 * maxAbsPnl);

  const equityValues = points.map((p) => p.equity);
  const eqMin = Math.min(...equityValues);
  const eqMax = Math.max(...equityValues);
  const eqRange = Math.max(eqMax - eqMin, 1);

  const yPnl = (value: number) => yZero - value * pnlScale;
  const yEq = (value: number) => padding + ((eqMax - value) / eqRange) * plotHeight;

  const linePath = points
    .map((p, idx) => {
      const x = padding + idx * step;
      const y = yEq(p.equity);
      return `${idx === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  const symbolLabel = (p: ChartPoint) => p.symbol ?? backtestSymbol ?? "-";

  return (
    <div
      className="relative rounded border border-[#2a2e39] bg-[#131722] p-4"
      onMouseLeave={() => setHoveredPoint(null)}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[#868993]">
        <div>
          <span className="mr-3 inline-flex items-center gap-2">
            <span className="h-2 w-2 rounded-sm bg-[#26a69a]" /> PnL
          </span>
          <span className="inline-flex items-center gap-2">
            <span className="h-0.5 w-4 rounded-full bg-[#42a5f5]" /> Equity
          </span>
        </div>
        <div>
          {points.length} trades - PnL range +/-{formatNumber(maxAbsPnl, 2)} USDT
        </div>
      </div>
      {hoveredPoint ? (
        <div
          className="pointer-events-none fixed z-50 min-w-[180px] rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-xs shadow-lg"
          style={{ left: tooltipPos.x + 12, top: tooltipPos.y + 12 }}
        >
          <ul className="list-inside list-disc space-y-1 text-[#d1d4dc]">
            <li>Time: {formatDateTime(hoveredPoint.timestamp)}</li>
            <li>Symbol: {symbolLabel(hoveredPoint)}</li>
            <li>Side: {hoveredPoint.side ?? "-"}</li>
            <li>
              Position (USDT):{" "}
              {hoveredPoint.positionSizeUsdt !== null
                ? formatNumber(hoveredPoint.positionSizeUsdt, 2)
                : "-"}
            </li>
            <li>
              PnL:{" "}
              <span
                className={
                  hoveredPoint.pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"
                }
              >
                {formatSigned(hoveredPoint.pnl, "USDT")}
              </span>
            </li>
            <li>Equity: {formatNumber(hoveredPoint.equity, 2)} USDT</li>
            <li>Reason: {hoveredPoint.reason ?? "-"}</li>
          </ul>
        </div>
      ) : null}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label="Trade PnL and equity chart"
      >
        <rect x={padding} y={padding} width={plotWidth} height={plotHeight} fill="#0f141f" />
        <line x1={padding} y1={yZero} x2={width - padding} y2={yZero} stroke="#2a2e39" strokeWidth={1} />
        {points.map((p, idx) => {
          const xCenter = padding + idx * step;
          const y = yPnl(p.pnl);
          const barHeight = Math.max(2, Math.abs(y - yZero));
          const yTop = p.pnl >= 0 ? y : yZero;
          const color = p.pnl >= 0 ? "#26a69a" : "#ef5350";
          return (
            <g
              key={`bar-${p.index}`}
              onMouseEnter={(e) => {
                setHoveredPoint(p);
                setTooltipPos({ x: e.clientX, y: e.clientY });
              }}
              onMouseMove={(e) => setTooltipPos({ x: e.clientX, y: e.clientY })}
            >
              <rect
                x={xCenter - barWidth / 2}
                y={yTop}
                width={barWidth}
                height={barHeight}
                fill={color}
                rx={2}
              >
                <title>
                  {`#${p.index} ${formatDateTime(p.timestamp)}\nPnL ${formatSigned(p.pnl, "USDT")}`}
                </title>
              </rect>
            </g>
          );
        })}
        {showEquity ? (
          <>
            <path d={linePath} fill="none" stroke="#42a5f5" strokeWidth={2} />
            {points.map((p, idx) => {
              const x = padding + idx * step;
              const y = yEq(p.equity);
              return (
                <g
                  key={`pt-${p.index}`}
                  onMouseEnter={(e) => {
                    setHoveredPoint(p);
                    setTooltipPos({ x: e.clientX, y: e.clientY });
                  }}
                  onMouseMove={(e) => setTooltipPos({ x: e.clientX, y: e.clientY })}
                >
                  <circle cx={x} cy={y} r={8} fill="transparent" />
                  <circle cx={x} cy={y} r={3} fill="#42a5f5">
                    <title>
                      {`#${p.index} ${formatDateTime(p.timestamp)}\nEquity ${formatSigned(p.equity, "USDT")}`}
                    </title>
                  </circle>
                </g>
              );
            })}
          </>
        ) : null}
      </svg>
      {!showEquity ? (
        <div className="mt-2 text-xs text-[#868993]">
          Equity line is hidden until initial balance is available.
        </div>
      ) : null}
    </div>
  );
}

export function TradeAnalysis({ job, liveTrades }: { job: Job; liveTrades: Trade[] }) {
  const [activeTab, setActiveTab] = useState<"chart" | "trades">("chart");
  const result = job.result ?? null;

  const summary = useMemo(() => {
    if (!result || !isRecord(result)) return null;
    if (job.type === "LIVE" && isRecord(result.summary)) return result.summary as Record<string, unknown>;
    return result;
  }, [job.type, result]);

  const initialEquity =
    job.type === "BACKTEST"
      ? asNumber(summary?.initial_balance)
      : asNumber(summary?.initial_equity ?? summary?.initial_balance);

  const normalizedTrades = useMemo(() => {
    if (job.type === "BACKTEST") {
      return normalizeBacktestTrades(summary?.trades ?? []);
    }
    return normalizeLiveTrades(liveTrades);
  }, [job.type, liveTrades, summary]);

  const sortedTrades = useMemo(() => {
    return [...normalizedTrades].sort((a, b) => {
      const ta = a.timestamp ?? 0;
      const tb = b.timestamp ?? 0;
      if (ta === tb) return 0;
      return ta - tb;
    });
  }, [normalizedTrades]);

  const chartPoints = useMemo(() => buildEquitySeries(sortedTrades, initialEquity), [sortedTrades, initialEquity]);

  const totalPnl = useMemo(() => {
    return sortedTrades.reduce((sum, t) => sum + (t.pnl ?? 0), 0);
  }, [sortedTrades]);
  const totalCommission = useMemo(() => {
    return sortedTrades.reduce((sum, t) => sum + (t.commission ?? 0), 0);
  }, [sortedTrades]);

  const enrichedBacktest = useMemo(
    () => (job.type === "BACKTEST" ? enrichBacktestTrades(sortedTrades, initialEquity) : []),
    [job.type, sortedTrades, initialEquity],
  );
  const enrichedLive = useMemo(
    () => (job.type === "LIVE" ? enrichLiveTrades(sortedTrades, initialEquity) : []),
    [job.type, sortedTrades, initialEquity],
  );

  const finalEquity =
    initialEquity !== null ? initialEquity + totalPnl - totalCommission : null;

  const netProfit = initialEquity !== null && finalEquity !== null ? finalEquity - initialEquity : null;
  const totalReturnPct =
    initialEquity != null && initialEquity > 0 && finalEquity !== null
      ? ((finalEquity - initialEquity) / initialEquity) * 100
      : null;

  const pnls = useMemo(
    () =>
      sortedTrades
        .map((t) => t.pnl)
        .filter((p): p is number => p !== null && p !== undefined && Number.isFinite(p)),
    [sortedTrades],
  );
  const tradeStats = useMemo(() => computeTradeStats(pnls), [pnls]);

  const numTrades = sortedTrades.filter((t) => t.pnl != null && t.pnl !== 0).length;
  const winCount = pnls.filter((p) => p > 0).length;
  const winRatePct = numTrades > 0 ? (winCount / numTrades) * 100 : 0;

  const backtestSymbol = useMemo(() => {
    if (job.type !== "BACKTEST" || !job.config) return null;
    const cfg = job.config as Record<string, unknown>;
    const sym = asString(cfg.symbol);
    if (sym) return sym;
    const streams = Array.isArray(cfg.streams) ? cfg.streams : [];
    const first = streams[0];
    return isRecord(first) ? asString(first.symbol) : null;
  }, [job.type, job.config]);

  const downloadCsv = useCallback(() => {
    const enriched =
      job.type === "BACKTEST" ? enrichedBacktest : enrichedLive;
    const headers = [
      "#",
      "Time",
      "Symbol",
      "Price",
      "Side",
      "Position(Qty)",
      "Position(USDT)",
      "PnL",
      "Commission",
      "Equity Change",
      "Equity",
      "Reason",
    ];
    const rows = enriched.map((t, idx) => {
      const sym = t.symbol ?? (job.type === "BACKTEST" ? backtestSymbol : null) ?? "-";
      const reason = (t.exitReason ?? t.reason ?? "-") as string;
      return [
        String(idx + 1),
        t.timeLabel,
        sym,
        t.price !== null ? formatNumber(t.price, 2) : "-",
        t.side ?? "-",
        t.quantity !== null ? formatNumber(t.quantity, 6) : "-",
        t.positionSizeUsdt !== null ? formatNumber(t.positionSizeUsdt, 2) : "-",
        t.pnl !== null ? formatSigned(t.pnl, "USDT") : "-",
        t.commission !== null ? formatNumber(t.commission, 4) : "-",
        t.balanceChange !== null ? formatSigned(t.balanceChange, "USDT") : "-",
        t.balanceAfter !== null ? formatNumber(t.balanceAfter, 2) : "-",
        reason,
      ].map(escapeCsvValue);
    });
    const csv = [headers.map(escapeCsvValue).join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trades_${job.type.toLowerCase()}_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [job.type, backtestSymbol, enrichedBacktest, enrichedLive]);

  if (!sortedTrades.length) {
    return (
      <section className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4">
        <div className="text-sm font-medium text-[#d1d4dc]">Trade Analysis</div>
        <div className="mt-3 text-xs text-[#868993]">No trades recorded yet.</div>
      </section>
    );
  }

  const balanceLabel = job.type === "BACKTEST" ? "Initial Balance" : "Initial Equity";
  const finalLabel = job.type === "BACKTEST" ? "Final Balance" : "Final Equity";

  const tabButtonBase =
    "rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium transition-colors";
  const tabButtonActive = "bg-[#131722] text-[#d1d4dc]";
  const tabButtonInactive = "bg-[#1e222d] text-[#868993] hover:bg-[#252a37]";

  return (
    <section className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-[#d1d4dc]">Trade Analysis</span>
        <nav className="flex gap-1">
          <button
            type="button"
            className={`${tabButtonBase} ${activeTab === "chart" ? tabButtonActive : tabButtonInactive}`}
            onClick={() => setActiveTab("chart")}
            aria-pressed={activeTab === "chart"}
          >
            차트
          </button>
          <button
            type="button"
            className={`${tabButtonBase} ${activeTab === "trades" ? tabButtonActive : tabButtonInactive}`}
            onClick={() => setActiveTab("trades")}
            aria-pressed={activeTab === "trades"}
          >
            거래 내역
          </button>
        </nav>
      </div>

      <div className="rounded border border-[#2a2e39] bg-[#131722] p-4">
          {activeTab === "chart" ? (
            <>
              <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {totalReturnPct !== null && netProfit !== null && (
                  <MetricCard
                    label="Return"
                    value={`${formatSigned(netProfit, "USDT")} (${formatNumber(totalReturnPct)}%)`}
                    tone={netProfit >= 0 ? "positive" : "negative"}
                  />
                )}
                {initialEquity !== null && finalEquity !== null && (
                  <MetricCard
                    label="Balance"
                    value={`${formatNumber(initialEquity)} → ${formatNumber(finalEquity)} USDT`}
                  />
                )}
                <MetricCard
                  label="Total Trades"
                  value={`${numTrades} (${winCount} wins, ${formatNumber(winRatePct, 1)}%)`}
                />
              </div>

              <details className="mb-4 group">
                <summary className="flex cursor-pointer list-none items-center rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-2 text-xs font-medium text-[#d1d4dc] hover:bg-[#252a37] [&::-webkit-details-marker]:hidden [&::marker]:hidden">
                  Trade detail
                  <span className="ml-2 inline-block text-[#868993] transition-transform group-open:rotate-180">
                    ▾
                  </span>
                </summary>
                <div className="grid gap-3 rounded-b border border-[#2a2e39] border-t-0 bg-[#131722] p-4 sm:grid-cols-2 lg:grid-cols-3">
                  <MetricCard
                    label="Total Commission"
                    value={`${formatNumber(totalCommission)} USDT`}
                    tone="negative"
                  />
                  {numTrades > 0 && netProfit !== null && (
                    <MetricCard
                      label="Avg Profit / Trade"
                      value={formatSigned(netProfit / numTrades, "USDT")}
                      tone={netProfit >= 0 ? "positive" : "negative"}
                    />
                  )}
                  {tradeStats ? (
                    <>
                      <MetricCard
                        label="Profit Factor"
                        value={tradeStats.profitFactor === Infinity ? "∞" : formatNumber(tradeStats.profitFactor)}
                      />
                      {tradeStats.maxProfit !== null && (
                        <MetricCard
                          label="Max Profit"
                          value={formatSigned(tradeStats.maxProfit, "USDT")}
                          tone="positive"
                        />
                      )}
                      {tradeStats.maxLoss !== null && (
                        <MetricCard
                          label="Max Loss"
                          value={formatSigned(tradeStats.maxLoss, "USDT")}
                          tone="negative"
                        />
                      )}
                      <MetricCard label="Max Consecutive Wins" value={`${tradeStats.maxConsecutiveWins}`} />
                      <MetricCard label="Max Consecutive Losses" value={`${tradeStats.maxConsecutiveLosses}`} />
                    </>
                  ) : null}
                </div>
              </details>

              <Chart
                points={chartPoints}
                showEquity={initialEquity !== null}
                backtestSymbol={backtestSymbol}
              />
            </>
          ) : (
            <div className="rounded border border-[#2a2e39] bg-[#0f141f]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2a2e39] px-4 py-2">
                <span className="text-xs font-medium text-[#d1d4dc]">Trades ({sortedTrades.length})</span>
                <button
                  type="button"
                  onClick={downloadCsv}
                  className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-1.5 text-xs text-[#d1d4dc] hover:bg-[#252a37]"
                >
                  CSV 다운로드
                </button>
              </div>
              <div className="max-h-[520px] overflow-auto">
                {job.type === "BACKTEST" ? (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-[#131722]">
                <tr className="border-b border-[#2a2e39] text-left text-[#868993]">
                  <th className="px-4 py-2">#</th>
                  <th className="px-4 py-2">Time</th>
                  <th className="px-4 py-2">Symbol</th>
                  <th className="px-4 py-2">Price</th>
                  <th className="px-4 py-2">Side</th>
                  <th className="px-4 py-2">Position(Qty)</th>
                  <th className="px-4 py-2">Position(USDT)</th>
                  <th className="px-4 py-2">PnL</th>
                  <th className="px-4 py-2">Commission</th>
                  <th className="px-4 py-2">Equity Change</th>
                  <th className="px-4 py-2">Equity</th>
                  <th className="px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {enrichedBacktest.map((t, idx) => (
                  <tr key={`bt-${t.id}`} className="border-b border-[#2a2e39]">
                    <td className="px-4 py-2 text-[#868993]">{idx + 1}</td>
                    <td className="px-4 py-2 text-[#d1d4dc]">{t.timeLabel}</td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.symbol ?? backtestSymbol ?? "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.price !== null ? formatNumber(t.price, 2) : "-"}
                    </td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        t.side === "BUY" ? "text-[#26a69a]" : "text-[#ef5350]"
                      }`}
                    >
                      {t.side ?? "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.quantity !== null ? formatNumber(t.quantity, 6) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.positionSizeUsdt !== null ? formatNumber(t.positionSizeUsdt, 2) : "-"}
                    </td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        (t.pnl ?? 0) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"
                      }`}
                    >
                      {t.pnl !== null ? formatSigned(t.pnl, "USDT") : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.commission !== null ? formatNumber(t.commission, 4) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.balanceChange !== null ? formatSigned(t.balanceChange, "USDT") : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.balanceAfter !== null ? formatNumber(t.balanceAfter, 2) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#868993]">
                      {t.exitReason ? `${t.exitReason}` : t.reason ?? "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-[#131722]">
                <tr className="border-b border-[#2a2e39] text-left text-[#868993]">
                  <th className="px-4 py-2">#</th>
                  <th className="px-4 py-2">Time</th>
                  <th className="px-4 py-2">Symbol</th>
                  <th className="px-4 py-2">Price</th>
                  <th className="px-4 py-2">Side</th>
                  <th className="px-4 py-2">Position(Qty)</th>
                  <th className="px-4 py-2">Position(USDT)</th>
                  <th className="px-4 py-2">PnL</th>
                  <th className="px-4 py-2">Commission</th>
                  <th className="px-4 py-2">Equity Change</th>
                  <th className="px-4 py-2">Equity</th>
                  <th className="px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {enrichedLive.map((t, idx) => (
                  <tr key={`lv-${t.id}`} className="border-b border-[#2a2e39]">
                    <td className="px-4 py-2 text-[#868993]">{idx + 1}</td>
                    <td className="px-4 py-2 text-[#d1d4dc]">{t.timeLabel}</td>
                    <td className="px-4 py-2 text-[#d1d4dc]">{t.symbol ?? "-"}</td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.price !== null ? formatNumber(t.price, 2) : "-"}
                    </td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        t.side === "BUY" ? "text-[#26a69a]" : t.side === "SELL" ? "text-[#ef5350]" : "text-[#d1d4dc]"
                      }`}
                    >
                      {t.side ?? "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.quantity !== null ? formatNumber(t.quantity, 6) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.positionSizeUsdt !== null ? formatNumber(t.positionSizeUsdt, 2) : "-"}
                    </td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        (t.pnl ?? 0) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"
                      }`}
                    >
                      {t.pnl !== null ? formatSigned(t.pnl, "USDT") : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.commission !== null ? formatNumber(t.commission, 4) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.balanceChange !== null ? formatSigned(t.balanceChange, "USDT") : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.balanceAfter !== null ? formatNumber(t.balanceAfter, 2) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#868993]">
                      {t.exitReason ? `${t.exitReason}` : t.reason ?? "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
              </div>
            </div>
          )}
      </div>
    </section>
  );
}
