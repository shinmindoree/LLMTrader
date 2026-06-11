"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "@/lib/i18n";
import type { Job, Trade } from "@/lib/types";
import { isRecord } from "@/components/JobResultSummary";
import { TimeCell } from "@/components/TimeCell";
import { useTopChart } from "@/components/TopChartContext";
import { BacktestExecutionChart } from "@/components/BacktestExecutionChart";
import { formatBinanceTime } from "@/lib/timeFormat";

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
  orderId: number | null;
  role: "Maker" | "Taker" | null;
};

type Position = {
  symbol: string;
  direction: string;
  status: "Closed" | "Open";
  realizedPnl: number;
  grossPnl: number;
  commission: number;
  roi: number;
  roiGross: number;
  closedVol: number;
  entryPrice: number;
  avgClosePrice: number | null;
  maxOi: number;
  openedAt: string;
  closedAt: string | null;
  closedTimestamp: number | null;
};

type ChartPoint = {
  index: number;
  timestamp: number | null;
  pnl: number;
  pnlNet: number;
  equity: number;
  equityGross: number;
  commission: number;
  symbol: string | null;
  side: string | null;
  positionSizeUsdt: number | null;
  reason: string | null;
};

type BacktestChartPayload = {
  symbol?: string;
  interval?: string;
  candles?: Array<{
    open_time: number;
    close_time: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
  indicator_config?: Record<string, unknown>;
  indicator_series?: Array<{
    id: string;
    indicator: string;
    output: string | null;
    label: string;
    pane: "overlay" | "oscillator";
    values: Array<number | null>;
  }>;
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

function InfoTip({ text }: { text: string }) {
  return (
    <span className="group/tip relative ml-1 inline-flex items-center align-middle">
      <span
        className="flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-[#3a3f4b] text-[9px] font-semibold leading-none text-[#868993]"
        aria-hidden="true"
      >
        i
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-1.5 w-52 -translate-x-1/2 rounded border border-[#2a2e39] bg-[#1e222d] px-2.5 py-1.5 text-[11px] font-normal leading-snug text-[#d1d4dc] opacity-0 shadow-lg transition-opacity duration-150 group-hover/tip:opacity-100"
      >
        {text}
      </span>
    </span>
  );
}

function MetricCard({
  label,
  value,
  tone = "neutral",
  info,
}: {
  label: string;
  value: string;
  tone?: MetricTone;
  info?: string;
}) {
  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
      <div className="flex items-center text-xs text-[#868993]">
        <span>{label}</span>
        {info ? <InfoTip text={info} /> : null}
      </div>
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
  avgWin: number | null;
  avgLoss: number | null;
  payoffRatio: number | null;
  expectancy: number | null;
};

export function computeTradeStats(pnls: number[]): TradeStats | null {
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
  const avgWin = wins > 0 ? totalProfit / wins : null;
  const avgLoss = losses > 0 ? totalLoss / losses : null;
  const payoffRatio = avgWin !== null && avgLoss !== null && avgLoss > 0 ? avgWin / avgLoss : null;
  const winRate = totalTrades > 0 ? wins / totalTrades : 0;
  const lossRate = totalTrades > 0 ? losses / totalTrades : 0;
  const expectancy = avgWin !== null && avgLoss !== null
    ? winRate * avgWin - lossRate * avgLoss
    : null;
  return {
    winRatePct,
    profitFactor,
    maxProfit: Number.isFinite(maxProfit) ? maxProfit : null,
    maxLoss: Number.isFinite(maxLoss) ? maxLoss : null,
    maxConsecutiveWins,
    maxConsecutiveLosses,
    avgWin,
    avgLoss,
    payoffRatio,
    expectancy,
  };
}

function escapeCsvValue(value: string): string {
  if (value.includes(",") || value.includes('"') || value.includes("\n")) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function formatDateTime(ms: number | null): string {
  // Used for CSV export and equity-tooltip labels. Binance-style
  // `YYYY-MM-DD HH:mm:ss` in KST, locale-independent so exported files
  // are parseable. The interactive trade table uses <TimeCell /> which
  // honours the user's KST/UTC toggle; this fallback is intentionally
  // fixed to KST 24h to keep CSVs deterministic across machines.
  return formatBinanceTime(ms, "KST");
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
        orderId: null,
        role: null,
      } satisfies NormalizedTrade;
    });
}

export function normalizeLiveTrades(trades: Trade[]): NormalizedTrade[] {
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
    // Reason / exit_reason are populated by the runner from the strategy's
    // own _reason / _exit_reason fields when the chase order is verified
    // (see src/runner/event_sink.py::record_trade_from_user_trade and
    // src/live/context.py TRADE_RECORDED). Trust those values verbatim —
    // do NOT synthesize a fake "Entry Long" / "Exit Long" fallback. A
    // SELL with realized_pnl == 0 is a fresh short entry, not a long
    // exit, so heuristics on side+pnl mislabel real strategy events.
    const reasonFromRaw = raw ? asString(raw.reason) : null;
    const exitReasonFromRaw = raw ? asString(raw.exit_reason) : null;
    const reason = reasonFromRaw ?? exitReasonFromRaw;
    const exitReason = exitReasonFromRaw;
    const orderId = t.order_id ?? (raw ? (typeof raw.orderId === "number" ? raw.orderId : null) : null);
    const makerRaw = raw ? raw.maker : null;
    const role: "Maker" | "Taker" | null = typeof makerRaw === "boolean" ? (makerRaw ? "Maker" : "Taker") : null;
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
      orderId: typeof orderId === "number" ? orderId : null,
      role,
    } satisfies NormalizedTrade;
  });
}

function buildEquitySeriesFromPositions(
  positions: Position[],
  initialEquity: number | null,
  cumPnlMode: boolean = false,
): ChartPoint[] {
  const base = cumPnlMode ? 0 : (initialEquity ?? 0);
  let equityNet = base;
  let equityGross = base;
  const points: ChartPoint[] = [];

  for (let i = 0; i < positions.length; i++) {
    const pos = positions[i];
    if (pos.status !== "Closed") continue;
    const netPnl = pos.realizedPnl; // already net of commission in buildPositions
    const grossPnl = pos.grossPnl; // before commission
    equityNet += netPnl;
    equityGross += grossPnl;

    points.push({
      index: i + 1,
      timestamp: pos.closedTimestamp,
      pnl: grossPnl,
      pnlNet: netPnl,
      equity: equityNet,
      equityGross,
      commission: pos.commission,
      symbol: pos.symbol,
      side: pos.direction,
      positionSizeUsdt: pos.entryPrice * pos.closedVol,
      reason: pos.direction,
    });
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

type EntryLeg = {
  price: number;
  qty: number;
  timeLabel: string;
  symbol: string | null;
  timestamp: number | null;
};

/**
 * Reconstruct positions from a chronological trade list using a signed
 * net-quantity model.
 *
 * The previous implementation only tracked BUY/SELL direction plus a running
 * entry/exit quantity and reset to flat as soon as the exit quantity reached
 * the entry quantity. That model broke on two real-world cases:
 *
 *  1. Reversals (flips): a single fill (or the fill that crosses zero) both
 *     closes the current position and opens the opposite one. The old code
 *     closed the position and *discarded* the surplus quantity, so the next
 *     position's real entry volume vanished and every subsequent position
 *     boundary was shifted by one trade.
 *  2. A position inherited from before the trade window (e.g. a live job that
 *     restarted while holding a position). Its closing fill carries a realized
 *     PnL but no visible entry, which the old code mislabeled as a brand-new
 *     entry, cascading the same off-by-one error.
 *
 * This version accumulates a signed quantity (BUY = +, SELL = -). A position
 * is closed exactly when the signed quantity returns to (or crosses) zero, a
 * flip opens a fresh opposite position with the leftover quantity, and a
 * closing fill seen while flat is treated as an orphan close (the position was
 * opened before our window) and excluded. Realized PnL is taken verbatim from
 * the exchange-provided per-trade values.
 */
export function buildPositions(trades: NormalizedTrade[], leverage: number): Position[] {
  const positions: Position[] = [];

  let netQty = 0; // signed running quantity: + long, - short
  let dir: "Long" | "Short" | null = null;
  let entryLegs: EntryLeg[] = [];
  let pnlAccum = 0; // exchange realized PnL accumulated for the current position
  let commissionAccum = 0; // commissions attributable to the current position
  let exitVol = 0; // gross closed volume so far
  let exitPriceVol = 0; // sum(price * qty) over closing fills (for avg close price)
  let lastExitTs: number | null = null;
  let lastExitLabel = "-";

  const resetPosition = () => {
    dir = null;
    entryLegs = [];
    pnlAccum = 0;
    commissionAccum = 0;
    exitVol = 0;
    exitPriceVol = 0;
    lastExitTs = null;
    lastExitLabel = "-";
  };

  const finalizePosition = () => {
    if (dir === null) return;
    const entryQty = entryLegs.reduce((s, l) => s + l.qty, 0);
    if (entryQty <= 0) {
      resetPosition();
      return;
    }
    const realizedPnl = pnlAccum - commissionAccum;
    const avgEntry = entryLegs.reduce((s, l) => s + l.price * l.qty, 0) / entryQty;
    const avgExit = exitVol > 0 ? exitPriceVol / exitVol : null;
    const maxOi = entryLegs.reduce((m, l) => Math.max(m, l.qty), 0);
    const costBasis = avgEntry * entryQty;
    const roi = costBasis > 0 ? (realizedPnl / costBasis) * 100 * leverage : 0;
    const roiGross = costBasis > 0 ? (pnlAccum / costBasis) * 100 * leverage : 0;

    positions.push({
      symbol: entryLegs[0]?.symbol ?? "-",
      direction: `Cross ${dir}`,
      status: "Closed",
      realizedPnl,
      grossPnl: pnlAccum,
      commission: commissionAccum,
      roi,
      roiGross,
      closedVol: entryQty,
      entryPrice: avgEntry,
      avgClosePrice: avgExit,
      maxOi,
      openedAt: entryLegs[0]?.timeLabel ?? "-",
      closedAt: lastExitLabel,
      closedTimestamp: lastExitTs,
    });
    resetPosition();
  };

  for (const trade of trades) {
    const side = trade.side?.toUpperCase();
    if (side !== "BUY" && side !== "SELL") continue;
    const qty = trade.quantity ?? 0;
    if (qty <= 0) continue;
    const price = trade.price ?? 0;
    const pnl = trade.pnl ?? 0;
    const commission = trade.commission ?? 0;
    const delta = side === "BUY" ? qty : -qty;

    if (dir === null) {
      // Flat. A fill that carries a realized PnL while we have no open
      // position is closing a position opened before this trade window
      // (e.g. inherited on a live-job restart). Treat it as an orphan close
      // and skip it so it is not counted as a new position.
      if (Math.abs(pnl) > 1e-9) continue;
      dir = side === "BUY" ? "Long" : "Short";
      netQty = delta;
      entryLegs = [{ price, qty, timeLabel: trade.timeLabel ?? "-", symbol: trade.symbol, timestamp: trade.timestamp }];
      commissionAccum = commission;
      continue;
    }

    const sameDirection =
      (dir === "Long" && side === "BUY") || (dir === "Short" && side === "SELL");

    if (sameDirection) {
      netQty += delta;
      entryLegs.push({ price, qty, timeLabel: trade.timeLabel ?? "-", symbol: trade.symbol, timestamp: trade.timestamp });
      commissionAccum += commission;
      continue;
    }

    // Opposite side: reduces (and possibly flips) the current position.
    const reduceQty = Math.min(qty, Math.abs(netQty));
    exitVol += reduceQty;
    exitPriceVol += price * reduceQty;
    pnlAccum += pnl;
    commissionAccum += commission;
    lastExitTs = trade.timestamp ?? lastExitTs;
    lastExitLabel = trade.timeLabel ?? lastExitLabel;
    netQty += delta;

    if (Math.abs(netQty) < 1e-9) {
      // Exactly flat → close the position.
      finalizePosition();
      netQty = 0;
      continue;
    }

    if ((dir === "Long" && netQty < 0) || (dir === "Short" && netQty > 0)) {
      // Flip: the fill crossed through zero. Close the old position, then open
      // a fresh opposite position with the leftover quantity.
      finalizePosition();
      const newDir: "Long" | "Short" = netQty > 0 ? "Long" : "Short";
      const openQty = Math.abs(netQty);
      dir = newDir;
      entryLegs = [{ price, qty: openQty, timeLabel: trade.timeLabel ?? "-", symbol: trade.symbol, timestamp: trade.timestamp }];
      // The single fill's commission was already attributed to the close above.
      continue;
    }
    // Otherwise: partial reduce, position stays open in the same direction.
  }

  // Trailing open position (not yet closed).
  if (dir !== null) {
    const entryQty = entryLegs.reduce((s, l) => s + l.qty, 0);
    if (entryQty > 0) {
      const avgEntry = entryLegs.reduce((s, l) => s + l.price * l.qty, 0) / entryQty;
      const maxOi = entryLegs.reduce((m, l) => Math.max(m, l.qty), 0);
      positions.push({
        symbol: entryLegs[0]?.symbol ?? "-",
        direction: `Cross ${dir}`,
        status: "Open",
        realizedPnl: 0,
        grossPnl: 0,
        commission: 0,
        roi: 0,
        roiGross: 0,
        closedVol: 0,
        entryPrice: avgEntry,
        avgClosePrice: null,
        maxOi,
        openedAt: entryLegs[0]?.timeLabel ?? "-",
        closedAt: null,
        closedTimestamp: null,
      });
    }
  }

  return positions;
}

function Chart({
  points,
  showEquity,
  backtestSymbol,
  isLive = false,
  afterFees,
}: {
  points: ChartPoint[];
  showEquity: boolean;
  backtestSymbol: string | null;
  isLive?: boolean;
  afterFees: boolean;
}) {
  const { t } = useI18n();
  const [hoveredPoint, setHoveredPoint] = useState<ChartPoint | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0 });

  const width = 900;
  const height = 320;
  const padding = 36;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;

  const totalRange = Math.max(1, points.length - 1);

  const [visibleRange, setVisibleRange] = useState<[number, number] | null>(null);
  const visibleRangeRef = useRef<[number, number] | null>(null);
  useEffect(() => {
    visibleRangeRef.current = visibleRange;
  }, [visibleRange]);

  // Reset zoom only when the dataset shrinks (e.g., switching jobs). When
  // new live trades append (length grows), keep the current zoom window —
  // clampRange below extends the default range automatically when not zoomed.
  // Uses the React 19 "set state during render" pattern to avoid an extra
  // paint with a stale window.
  const [lastPointsLen, setLastPointsLen] = useState(points.length);
  if (lastPointsLen !== points.length) {
    if (points.length < lastPointsLen) {
      setVisibleRange(null);
    }
    setLastPointsLen(points.length);
  }

  const svgRef = useRef<SVGSVGElement>(null);
  const panStateRef = useRef<{
    pointerId: number;
    startClientX: number;
    startVStart: number;
    startVEnd: number;
    moved: boolean;
  } | null>(null);
  const [isPanning, setIsPanning] = useState(false);

  const clampRange = useCallback(
    (start: number, end: number): [number, number] => {
      const minSpan = Math.min(1, totalRange);
      const span = Math.max(minSpan, Math.min(totalRange, end - start));
      let s = start;
      let e = s + span;
      if (s < 0) {
        s = 0;
        e = s + span;
      }
      if (e > totalRange) {
        e = totalRange;
        s = e - span;
      }
      if (s < 0) s = 0;
      return [s, e];
    },
    [totalRange],
  );

  // Attach a non-passive wheel listener so we can preventDefault and zoom.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;

    const handleWheel = (e: WheelEvent) => {
      if (points.length < 2) return;
      e.preventDefault();
      const rect = svg.getBoundingClientRect();
      if (rect.width <= 0) return;
      const xVB = ((e.clientX - rect.left) / rect.width) * width;
      const xPlot = Math.max(padding, Math.min(width - padding, xVB));
      const xRel = plotWidth > 0 ? (xPlot - padding) / plotWidth : 0;
      const [cs, ce] = visibleRangeRef.current ?? [0, totalRange];
      const range = Math.max(ce - cs, 0.0001);
      const idxAtCursor = cs + xRel * range;
      const zoomFactor = e.deltaY < 0 ? 0.85 : 1.18;
      const minRange = Math.min(2, totalRange);
      const newRange = Math.max(minRange, Math.min(totalRange, range * zoomFactor));
      let newStart = idxAtCursor - xRel * newRange;
      let newEnd = newStart + newRange;
      [newStart, newEnd] = clampRange(newStart, newEnd);
      setVisibleRange([newStart, newEnd]);
    };

    svg.addEventListener("wheel", handleWheel, { passive: false });
    return () => svg.removeEventListener("wheel", handleWheel);
  }, [points.length, totalRange, padding, plotWidth, width, clampRange]);

  const hasAnyCommission = points.some((p) => p.commission > 0);

  if (!points.length) {
    return (
      <div className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-6 text-center text-xs text-[#868993]">
        {t.tradeAnalysis.noPnlData}
      </div>
    );
  }

  const [vStartRaw, vEndRaw] = visibleRange ?? [0, totalRange];
  const [vStart, vEnd] = clampRange(vStartRaw, vEndRaw);
  const visibleSpan = Math.max(vEnd - vStart, 0.0001);
  const isZoomed =
    visibleRange !== null && (vStart > 0.0001 || vEnd < totalRange - 0.0001);
  const step = plotWidth / Math.max(visibleSpan, 1);
  const barWidth = Math.max(2, Math.min(24, step * 0.6));

  const xForIndex = (idx: number) =>
    padding + ((idx - vStart) / visibleSpan) * plotWidth;

  const getPnl = (p: ChartPoint) => (afterFees ? p.pnlNet : p.pnl);
  const getEquity = (p: ChartPoint) => (afterFees ? p.equity : p.equityGross);

  // Restrict y-range calculations to the visible window so zooming reveals
  // detail in calm regions (Binance-style autoscale).
  const visibleStartIdx = Math.max(0, Math.floor(vStart));
  const visibleEndIdx = Math.min(points.length - 1, Math.ceil(vEnd));
  const visiblePoints = points.slice(visibleStartIdx, visibleEndIdx + 1);
  const visiblePnlValues = visiblePoints.map(getPnl);
  const maxAbsPnl = Math.max(...visiblePnlValues.map((v) => Math.abs(v)), 1);
  const yZero = padding + plotHeight / 2;
  const pnlScale = plotHeight / (2 * maxAbsPnl);

  const visibleEquityValues = visiblePoints.map(getEquity);
  const eqMin = visibleEquityValues.length ? Math.min(...visibleEquityValues) : 0;
  const eqMax = visibleEquityValues.length ? Math.max(...visibleEquityValues) : 1;
  const eqRange = Math.max(eqMax - eqMin, 1);

  const yPnl = (value: number) => yZero - value * pnlScale;
  const yEq = (value: number) => padding + ((eqMax - value) / eqRange) * plotHeight;

  const linePath = visiblePoints
    .map((p, idx) => {
      const absoluteIdx = visibleStartIdx + idx;
      const x = xForIndex(absoluteIdx);
      const y = yEq(getEquity(p));
      return `${idx === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  const symbolLabel = (p: ChartPoint) => p.symbol ?? backtestSymbol ?? "-";

  const handlePointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    if (points.length < 2) return;
    panStateRef.current = {
      pointerId: e.pointerId,
      startClientX: e.clientX,
      startVStart: vStart,
      startVEnd: vEnd,
      moved: false,
    };
    setHoveredPoint(null);
    try {
      (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
    } catch {
      // setPointerCapture can throw if the pointer is no longer active; ignore.
    }
  };

  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const state = panStateRef.current;
    if (!state || state.pointerId !== e.pointerId) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0) return;
    const pxPerUnit = (rect.width * (plotWidth / width)) / visibleSpan;
    if (pxPerUnit <= 0) return;
    const dx = e.clientX - state.startClientX;
    if (!state.moved && Math.abs(dx) > 3) {
      state.moved = true;
      setIsPanning(true);
    }
    if (!state.moved) return;
    const deltaIdx = -dx / pxPerUnit;
    let newStart = state.startVStart + deltaIdx;
    let newEnd = state.startVEnd + deltaIdx;
    [newStart, newEnd] = clampRange(newStart, newEnd);
    setVisibleRange([newStart, newEnd]);
  };

  const finishPan = (e: React.PointerEvent<SVGSVGElement>) => {
    const state = panStateRef.current;
    if (!state) return;
    if (state.pointerId === e.pointerId) {
      panStateRef.current = null;
      setIsPanning(false);
      try {
        (e.currentTarget as SVGSVGElement).releasePointerCapture(e.pointerId);
      } catch {
        // ignore release errors
      }
    }
  };

  const handleDoubleClick = () => {
    setVisibleRange(null);
  };

  const cursorClass = isPanning ? "cursor-grabbing" : "cursor-grab";

  return (
    <div
      className="relative rounded border border-[#2a2e39] bg-[#131722] p-4"
      onMouseLeave={() => setHoveredPoint(null)}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-[#868993]">
        <div>
          <span className="mr-3 inline-flex items-center gap-2">
            <span className="h-2 w-2 rounded-sm bg-[#26a69a]" /> {t.tradeAnalysis.pnl}
          </span>
          <span className="inline-flex items-center gap-2">
            <span className="h-0.5 w-4 rounded-full bg-[#42a5f5]" /> {isLive ? "Cumulative PnL" : t.tradeAnalysis.equity}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span>
            {isZoomed
              ? `${visiblePoints.length}/${points.length} positions`
              : `${points.length} positions`}
            {` - PnL range +/-${formatNumber(maxAbsPnl, 2)} USDT`}
          </span>
          {hasAnyCommission && (
            <span className={afterFees ? "text-[#d1d4dc]" : "text-[#868993]"}>
              {afterFees ? t.tradeAnalysis.feeAfter : t.tradeAnalysis.feeBefore}
            </span>
          )}
          {isZoomed ? (
            <button
              type="button"
              onClick={handleDoubleClick}
              className="rounded border border-[#2a2e39] bg-[#1e222d] px-2 py-0.5 text-[10px] uppercase tracking-wide text-[#d1d4dc] hover:bg-[#252a37]"
            >
              {t.tradeAnalysis.resetZoom}
            </button>
          ) : (
            <span className="text-[10px] text-[#5d6275]">{t.tradeAnalysis.zoomHint}</span>
          )}
        </div>
      </div>
      {hoveredPoint ? (
        <div
          className="pointer-events-none fixed z-50 min-w-[180px] rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-xs shadow-lg"
          style={{ left: tooltipPos.x + 12, top: tooltipPos.y + 12 }}
        >
          <ul className="list-inside list-disc space-y-1 text-[#d1d4dc]">
            <li>Close Time: {formatDateTime(hoveredPoint.timestamp)}</li>
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
                  getPnl(hoveredPoint) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"
                }
              >
                {formatSigned(getPnl(hoveredPoint), "USDT")}
              </span>
            </li>
            <li>{isLive ? "Cum. PnL" : "Balance"}: {isLive ? formatSigned(getEquity(hoveredPoint), "USDT") : `${formatNumber(getEquity(hoveredPoint), 2)} USDT`}</li>
          </ul>
        </div>
      ) : null}
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        className={`w-full touch-none select-none ${cursorClass}`}
        role="img"
        aria-label="Trade PnL and equity chart"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
        onDoubleClick={handleDoubleClick}
      >
        <defs>
          <clipPath id="pnl-chart-plot-area">
            <rect x={padding} y={padding} width={plotWidth} height={plotHeight} />
          </clipPath>
        </defs>
        <rect x={padding} y={padding} width={plotWidth} height={plotHeight} fill="#0f141f" />
        <line x1={padding} y1={yZero} x2={width - padding} y2={yZero} stroke="#2a2e39" strokeWidth={1} />
        <g clipPath="url(#pnl-chart-plot-area)">
          {visiblePoints.map((p, idx) => {
            const absoluteIdx = visibleStartIdx + idx;
            const xCenter = xForIndex(absoluteIdx);
            const pnlVal = getPnl(p);
            const y = yPnl(pnlVal);
            const barHeight = Math.max(2, Math.abs(y - yZero));
            const yTop = pnlVal >= 0 ? y : yZero;
            const color = pnlVal >= 0 ? "#26a69a" : "#ef5350";
            return (
              <g
                key={`bar-${p.index}`}
                onMouseEnter={(e) => {
                  if (isPanning) return;
                  setHoveredPoint(p);
                  setTooltipPos({ x: e.clientX, y: e.clientY });
                }}
                onMouseMove={(e) => {
                  if (isPanning) return;
                  setTooltipPos({ x: e.clientX, y: e.clientY });
                }}
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
                    {`#${p.index} ${formatDateTime(p.timestamp)}\nPnL ${formatSigned(pnlVal, "USDT")}`}
                  </title>
                </rect>
              </g>
            );
          })}
          {showEquity ? (
            <>
              <path d={linePath} fill="none" stroke="#42a5f5" strokeWidth={2} />
              {visiblePoints.map((p, idx) => {
                const absoluteIdx = visibleStartIdx + idx;
                const x = xForIndex(absoluteIdx);
                const eqVal = getEquity(p);
                const y = yEq(eqVal);
                return (
                  <g
                    key={`pt-${p.index}`}
                    onMouseEnter={(e) => {
                      if (isPanning) return;
                      setHoveredPoint(p);
                      setTooltipPos({ x: e.clientX, y: e.clientY });
                    }}
                    onMouseMove={(e) => {
                      if (isPanning) return;
                      setTooltipPos({ x: e.clientX, y: e.clientY });
                    }}
                  >
                    <circle cx={x} cy={y} r={8} fill="transparent" />
                    <circle cx={x} cy={y} r={3} fill="#42a5f5">
                      <title>
                        {`#${p.index} ${formatDateTime(p.timestamp)}\nEquity ${formatSigned(eqVal, "USDT")}`}
                      </title>
                    </circle>
                  </g>
                );
              })}
            </>
          ) : null}
        </g>
      </svg>
      {!showEquity ? (
        <div className="mt-2 text-xs text-[#868993]">
          {t.tradeAnalysis.equityHidden}
        </div>
      ) : null}
    </div>
  );
}

export function TradeAnalysis({ job, liveTrades }: { job: Job; liveTrades: Trade[] }) {
  const { t } = useI18n();
  const { setBacktestChart } = useTopChart();
  const [activeTab, setActiveTab] = useState<"chart" | "trades" | "positions">("chart");
  const [timeSortAsc, setTimeSortAsc] = useState(true);
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

  const leverage = useMemo(() => {
    if (!job.config) return 1;
    const cfg = job.config as Record<string, unknown>;
    return typeof cfg.leverage === "number" ? cfg.leverage : 1;
  }, [job.config]);

  const positions = useMemo(
    () => buildPositions(sortedTrades, leverage),
    [sortedTrades, leverage],
  );

  const isLive = job.type === "LIVE";
  const chartPoints = useMemo(
    () => buildEquitySeriesFromPositions(positions, initialEquity, isLive),
    [positions, initialEquity, isLive],
  );

  // Fee toggle (master). Checked = after fees (default), unchecked = before fees.
  // Drives both the metric cards below and the equity Chart at the bottom.
  const [afterFees, setAfterFees] = useState(true);

  const closedPositions = useMemo(
    () => positions.filter((p) => p.status === "Closed"),
    [positions],
  );

  // Per-position PnL / ROI selectors that honour the fee toggle.
  const positionPnls = useMemo(
    () => closedPositions.map((p) => (afterFees ? p.realizedPnl : p.grossPnl)),
    [closedPositions, afterFees],
  );
  const tradeStats = useMemo(() => computeTradeStats(positionPnls), [positionPnls]);

  // Net profit (USDT) = sum of closed-position PnL under the selected fee mode.
  // Balance-independent, so it stays valid for live jobs whose wallet equity
  // is unreliable (capital parked in Simple Earn).
  const netProfit = positionPnls.length > 0
    ? positionPnls.reduce((s, p) => s + p, 0)
    : null;

  // Total return % for backtests uses the known initial balance; for live it
  // is replaced below by the compounded per-trade ROI.
  const totalReturnPct =
    initialEquity != null && initialEquity > 0 && netProfit !== null
      ? (netProfit / initialEquity) * 100
      : null;

  // MDD is the largest percentage peak-to-trough drop on the equity curve.
  // Keep the USDT amount from the same worst-percentage interval; choosing the
  // largest absolute USDT drop first can understate MDD% after the account grows.
  const maxDrawdown = useMemo(() => {
    let cum = 0;
    let peak = 0;
    let maxDdAmount = 0;
    let maxDdPct: number | null = null;
    for (const p of positionPnls) {
      cum += p;
      if (cum > peak) peak = cum;
      const ddAmount = peak - cum;
      const peakEquity =
        initialEquity != null && initialEquity > 0 ? initialEquity + peak : null;
      const ddPct =
        peakEquity != null && peakEquity > 0 ? (ddAmount / peakEquity) * 100 : null;
      if (ddPct !== null) {
        if (maxDdPct === null || ddPct > maxDdPct) {
          maxDdPct = ddPct;
          maxDdAmount = ddAmount;
        }
      } else if (ddAmount > maxDdAmount) {
        maxDdAmount = ddAmount;
      }
    }
    return { amount: maxDdAmount, pct: maxDdPct };
  }, [positionPnls, initialEquity]);

  // Recovery factor = net profit / max drawdown. Higher = better risk-adjusted
  // recovery. Null when there has been no drawdown yet.
  const recoveryFactor =
    netProfit !== null && maxDrawdown.amount > 1e-9 ? netProfit / maxDrawdown.amount : null;

  const numTrades = closedPositions.length;
  const winCount = positionPnls.filter((p) => p > 0).length;
  const winRatePct = numTrades > 0 ? (winCount / numTrades) * 100 : 0;
  const avgPnlPerTrade = netProfit !== null && numTrades > 0 ? netProfit / numTrades : null;

  // Compounded per-trade ROI (return on deployed margin), chained across all
  // closed positions: Π(1 + roiᵢ) − 1. Each position's roi is measured against
  // the margin actually committed to that trade, so this metric is independent
  // of the (volatile) account/Earn balance and stays valid even as funds move
  // between the Futures wallet and Simple Earn.
  const compoundReturnPct = useMemo(() => {
    if (closedPositions.length === 0) return null;
    let factor = 1;
    for (const p of closedPositions) factor *= 1 + (afterFees ? p.roi : p.roiGross) / 100;
    return (factor - 1) * 100;
  }, [closedPositions, afterFees]);

  // Live jobs park most capital in Simple Earn, so the Futures-wallet-based
  // initialEquity is an unreliable denominator (it can be near-zero, blowing up
  // the percentage). Use the balance-independent compounded ROI for the live
  // headline %, while backtests keep the initial_balance-based total return.
  const displayReturnPct = isLive ? compoundReturnPct : totalReturnPct;

  const backtestSymbol = useMemo(() => {
    if (job.type !== "BACKTEST" || !job.config) return null;
    const cfg = job.config as Record<string, unknown>;
    const sym = asString(cfg.symbol);
    if (sym) return sym;
    const streams = Array.isArray(cfg.streams) ? cfg.streams : [];
    const first = streams[0];
    return isRecord(first) ? asString(first.symbol) : null;
  }, [job.type, job.config]);

  const backtestChartPayload = useMemo<BacktestChartPayload | null>(() => {
    if (job.type !== "BACKTEST" || !isRecord(summary)) return null;
    const chartRaw = summary.chart;
    if (!isRecord(chartRaw)) return null;
    return chartRaw as BacktestChartPayload;
  }, [job.type, summary]);

  const topChartTrades = useMemo(
    () =>
      sortedTrades.map((trade) => ({
        timestamp: trade.timestamp,
        side: trade.side,
        price: trade.price,
        pnl: trade.pnl,
        reason: trade.reason,
        exitReason: trade.exitReason,
      })),
    [sortedTrades],
  );

  useEffect(() => {
    if (backtestChartPayload) {
      setBacktestChart({ chart: backtestChartPayload, trades: topChartTrades });
    }
    return () => {
      setBacktestChart(null);
    };
  }, [backtestChartPayload, topChartTrades, setBacktestChart]);

  // ── Live chart (Binance klines + trade markers) ─────────────────────────
  // For LIVE jobs we don't get a precomputed chart payload from the runner,
  // so we fetch candles for the configured symbol/interval from the Binance
  // futures REST proxy and feed them into the same BacktestExecutionChart
  // component used for backtests. The window covers the period spanned by
  // existing trades (with a small pad for context) up to ``now`` for running
  // jobs or the job's ``ended_at`` for finished ones.
  const liveStream = useMemo(() => {
    if (job.type !== "LIVE" || !isRecord(job.config)) return null;
    const streams = Array.isArray(job.config.streams) ? job.config.streams : [];
    const first = streams[0];
    if (!isRecord(first)) return null;
    const symbol = String(first.symbol ?? "").toUpperCase();
    const interval = String(first.interval ?? "");
    if (!symbol || !interval) return null;
    return { symbol, interval };
  }, [job.type, job.config]);

  // Tick once a minute while a LIVE job is running so the chart end-window
  // creeps forward and we refetch the latest candles. For non-running jobs
  // the end is pinned to ``ended_at`` and the tick is disabled.
  const isLiveRunning = job.type === "LIVE" && job.status === "RUNNING";
  const [liveTick, setLiveTick] = useState(0);
  useEffect(() => {
    if (!isLiveRunning) return;
    const id = setInterval(() => setLiveTick((v) => v + 1), 60_000);
    return () => clearInterval(id);
  }, [isLiveRunning]);

  const liveChartWindow = useMemo(() => {
    if (job.type !== "LIVE" || !liveStream) return null;
    const tradeTs = sortedTrades
      .map((t) => t.timestamp)
      .filter((t): t is number => typeof t === "number" && Number.isFinite(t));
    const minTrade = tradeTs.length > 0 ? Math.min(...tradeTs) : null;
    const maxTrade = tradeTs.length > 0 ? Math.max(...tradeTs) : null;
    const startedAtMs = job.started_at ? Date.parse(job.started_at) : NaN;
    const endedAtMs = job.ended_at ? Date.parse(job.ended_at) : NaN;
    const startCandidates: number[] = [];
    if (Number.isFinite(startedAtMs)) startCandidates.push(startedAtMs);
    if (minTrade != null) startCandidates.push(minTrade);
    if (startCandidates.length === 0) return null;
    // 4h pad before/after for visual context around the first/last marker.
    const pad = 4 * 60 * 60_000;
    const startMs = Math.min(...startCandidates) - pad;
    const liveEnd = isLiveRunning ? Date.now() : (Number.isFinite(endedAtMs) ? endedAtMs : Date.now());
    const endCandidates: number[] = [liveEnd];
    if (maxTrade != null) endCandidates.push(maxTrade);
    const endMs = Math.max(...endCandidates) + pad;
    return { startMs, endMs };
    // ``liveTick`` is intentionally included so the window slides forward
    // every minute while the job is running, even if no new trades arrive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    job.type, job.started_at, job.ended_at, liveStream,
    sortedTrades, isLiveRunning, liveTick,
  ]);

  const [liveCandles, setLiveCandles] = useState<BacktestChartPayload["candles"] | null>(null);
  useEffect(() => {
    if (!liveStream || !liveChartWindow) {
      setLiveCandles(null);
      return;
    }
    const ctrl = new AbortController();
    (async () => {
      try {
        const url =
          `/api/binance/klines?symbol=${encodeURIComponent(liveStream.symbol)}` +
          `&interval=${encodeURIComponent(liveStream.interval)}` +
          `&startTime=${liveChartWindow.startMs}` +
          `&endTime=${liveChartWindow.endMs}`;
        const res = await fetch(url, { signal: ctrl.signal });
        if (!res.ok) return;
        const data = (await res.json()) as { candles?: BacktestChartPayload["candles"] };
        if (Array.isArray(data?.candles)) {
          setLiveCandles(data.candles);
        }
      } catch {
        // Aborts and transient errors silently drop; the previous candles
        // (if any) keep showing until the next successful fetch.
      }
    })();
    return () => ctrl.abort();
  }, [
    liveStream?.symbol, liveStream?.interval,
    liveChartWindow?.startMs, liveChartWindow?.endMs,
  ]);

  const liveChartPayload = useMemo<BacktestChartPayload | null>(() => {
    if (!liveStream || !liveCandles || liveCandles.length === 0) return null;
    return {
      symbol: liveStream.symbol,
      interval: liveStream.interval,
      candles: liveCandles,
    };
  }, [liveStream, liveCandles]);

  const downloadCsv = useCallback(() => {
    const enriched =
      job.type === "BACKTEST" ? enrichedBacktest : enrichedLive;
    const headers = [
      "Order No.",
      "Time",
      "Symbol",
      "Side",
      "Price",
      "Quantity",
      "Fee",
      "Role",
      "Realized Profit",
      "Reason",
    ];
    const rows = enriched.map((t, idx) => {
      const sym = t.symbol ?? (job.type === "BACKTEST" ? backtestSymbol : null) ?? "-";
      const reason = (t.exitReason ?? t.reason ?? "-") as string;
      return [
        String(t.orderId ?? idx + 1),
        t.timeLabel,
        sym,
        t.side === "BUY" ? "Buy" : t.side === "SELL" ? "Sell" : t.side ?? "-",
        t.price !== null ? formatNumber(t.price, 2) : "-",
        t.positionSizeUsdt !== null ? `${formatNumber(t.positionSizeUsdt, 1)} USDT` : "-",
        t.commission !== null ? `${formatNumber(t.commission, 8)} USDT` : "-",
        t.role ?? "-",
        t.pnl !== null ? `${formatNumber(t.pnl, 8)} USDT` : "0.00000000 USDT",
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
        <div className="text-sm font-medium text-[#d1d4dc]">{t.tradeAnalysis.title}</div>
        <div className="mt-3 text-xs text-[#868993]">{t.tradeAnalysis.noTrades}</div>
      </section>
    );
  }

  const tabButtonBase =
    "rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium transition-colors";
  const tabButtonActive = "bg-[#131722] text-[#d1d4dc]";
  const tabButtonInactive = "bg-[#1e222d] text-[#868993] hover:bg-[#252a37]";

  return (
    <section className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-[#d1d4dc]">{t.tradeAnalysis.title}</span>
        <nav className="flex gap-1">
          <button
            type="button"
            className={`${tabButtonBase} ${activeTab === "chart" ? tabButtonActive : tabButtonInactive}`}
            onClick={() => setActiveTab("chart")}
            aria-pressed={activeTab === "chart"}
          >
            {t.tradeAnalysis.chart}
          </button>
          <button
            type="button"
            className={`${tabButtonBase} ${activeTab === "trades" ? tabButtonActive : tabButtonInactive}`}
            onClick={() => setActiveTab("trades")}
            aria-pressed={activeTab === "trades"}
          >
            {t.tradeAnalysis.trades}
          </button>
          <button
            type="button"
            className={`${tabButtonBase} ${activeTab === "positions" ? tabButtonActive : tabButtonInactive}`}
            onClick={() => setActiveTab("positions")}
            aria-pressed={activeTab === "positions"}
          >
            Positions
          </button>
        </nav>
      </div>

      <div className="rounded border border-[#2a2e39] bg-[#131722] p-4">
          {activeTab === "chart" ? (
            <>
              {/* Fee toggle (master): checked = after fees, unchecked = before fees */}
              <div className="mb-3 flex items-center justify-end">
                <label className="inline-flex cursor-pointer items-center gap-2 select-none rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-1.5 text-xs">
                  <input
                    type="checkbox"
                    checked={afterFees}
                    onChange={() => setAfterFees((v) => !v)}
                    className="h-3.5 w-3.5 accent-[#2962ff]"
                  />
                  <span className={afterFees ? "text-[#d1d4dc]" : "text-[#868993]"}>
                    {afterFees ? t.tradeAnalysis.feeAfter : t.tradeAnalysis.feeBefore}
                  </span>
                </label>
              </div>

              {/* Group 1: Profitability */}
              <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-[#868993]">
                {t.tradeAnalysis.groupProfitability}
              </div>
              <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {netProfit !== null && (
                  <MetricCard
                    label={t.result.netProfit}
                    value={`${formatSigned(netProfit, "USDT")}${displayReturnPct !== null ? ` (${formatNumber(displayReturnPct)}%)` : ""}`}
                    tone={netProfit >= 0 ? "positive" : "negative"}
                    info={isLive ? t.tradeAnalysis.tips.netProfitLive : t.tradeAnalysis.tips.netProfit}
                  />
                )}
                {avgPnlPerTrade !== null && (
                  <MetricCard
                    label={t.result.avgProfitPerTrade}
                    value={formatSigned(avgPnlPerTrade, "USDT")}
                    tone={avgPnlPerTrade >= 0 ? "positive" : "negative"}
                    info={t.tradeAnalysis.tips.avgPnl}
                  />
                )}
                {tradeStats && (
                  <MetricCard
                    label={t.result.profitFactor}
                    value={tradeStats.profitFactor === Infinity ? "∞" : formatNumber(tradeStats.profitFactor)}
                    tone={tradeStats.profitFactor >= 1.5 ? "positive" : tradeStats.profitFactor >= 1 ? "neutral" : "negative"}
                    info={t.tradeAnalysis.tips.profitFactor}
                  />
                )}
                {tradeStats?.expectancy != null && (
                  <MetricCard
                    label={t.tradeAnalysis.expectancy}
                    value={formatSigned(tradeStats.expectancy, "USDT")}
                    tone={tradeStats.expectancy >= 0 ? "positive" : "negative"}
                    info={t.tradeAnalysis.tips.expectancy}
                  />
                )}
              </div>

              {/* Group 2: Win rate & consistency */}
              <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-[#868993]">
                {t.tradeAnalysis.groupWinConsistency}
              </div>
              <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                  label={t.result.winRate}
                  value={`${formatNumber(winRatePct, 1)}%  (${winCount}W / ${numTrades - winCount}L of ${numTrades})`}
                  tone={winRatePct >= 50 ? "positive" : winRatePct > 0 ? "negative" : "neutral"}
                  info={t.tradeAnalysis.tips.winRate}
                />
                {tradeStats?.payoffRatio != null && (
                  <MetricCard
                    label={t.tradeAnalysis.payoffRatio}
                    value={formatNumber(tradeStats.payoffRatio, 2)}
                    tone={tradeStats.payoffRatio >= 1 ? "positive" : "negative"}
                    info={t.tradeAnalysis.tips.payoffRatio}
                  />
                )}
                {tradeStats && (
                  <MetricCard
                    label={t.result.maxConsecutiveWins}
                    value={`${tradeStats.maxConsecutiveWins}`}
                    tone="positive"
                    info={t.tradeAnalysis.tips.maxConsecutiveWins}
                  />
                )}
                {tradeStats && (
                  <MetricCard
                    label={t.result.maxConsecutiveLosses}
                    value={`${tradeStats.maxConsecutiveLosses}`}
                    tone="negative"
                    info={t.tradeAnalysis.tips.maxConsecutiveLosses}
                  />
                )}
              </div>

              {/* Group 3: Risk & costs */}
              <div className="mb-2 text-[10px] font-medium uppercase tracking-wider text-[#868993]">
                {t.tradeAnalysis.groupRiskCost}
              </div>
              <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <MetricCard
                  label={t.tradeAnalysis.maxDrawdown}
                  value={maxDrawdown.amount > 0 ? `${formatSigned(-maxDrawdown.amount, "USDT")}${maxDrawdown.pct !== null ? ` (${formatNumber(maxDrawdown.pct)}%)` : ""}` : "-"}
                  tone={maxDrawdown.amount > 0 ? "negative" : "neutral"}
                  info={t.tradeAnalysis.tips.maxDrawdown}
                />
                {tradeStats && (tradeStats.maxProfit !== null || tradeStats.maxLoss !== null) && (
                  <MetricCard
                    label={t.tradeAnalysis.bestWorst}
                    value={`${tradeStats.maxProfit !== null ? formatSigned(tradeStats.maxProfit) : "-"} / ${tradeStats.maxLoss !== null ? formatSigned(tradeStats.maxLoss) : "-"} USDT`}
                    tone="neutral"
                    info={t.tradeAnalysis.tips.bestWorst}
                  />
                )}
                <MetricCard
                  label={t.tradeAnalysis.recoveryFactor}
                  value={recoveryFactor !== null ? formatNumber(recoveryFactor, 2) : "-"}
                  tone={recoveryFactor !== null && recoveryFactor >= 1 ? "positive" : "neutral"}
                  info={t.tradeAnalysis.tips.recoveryFactor}
                />
                <MetricCard
                  label={t.result.totalCommission}
                  value={`${formatNumber(totalCommission)} USDT`}
                  tone="negative"
                  info={t.tradeAnalysis.tips.totalCommission}
                />
              </div>

              {job.type === "BACKTEST" && backtestChartPayload && topChartTrades.length > 0 ? (
                <div className="mb-4">
                  <BacktestExecutionChart
                    chart={backtestChartPayload}
                    trades={topChartTrades}
                    height={420}
                  />
                </div>
              ) : null}

              {job.type === "LIVE" && liveChartPayload ? (
                <div className="mb-4">
                  <BacktestExecutionChart
                    chart={liveChartPayload}
                    trades={topChartTrades}
                    height={420}
                  />
                </div>
              ) : null}

              <Chart
                points={chartPoints}
                showEquity={isLive || initialEquity !== null}
                backtestSymbol={backtestSymbol}
                isLive={isLive}
                afterFees={afterFees}
              />
            </>
          ) : activeTab === "trades" ? (
            <div className="rounded border border-[#2a2e39] bg-[#0f141f]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2a2e39] px-4 py-2">
                <span className="text-xs font-medium text-[#d1d4dc]">Trades ({sortedTrades.length})</span>
                <button
                  type="button"
                  onClick={downloadCsv}
                  className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-1.5 text-xs text-[#d1d4dc] hover:bg-[#252a37]"
                >
                  {t.tradeAnalysis.csvDownload}
                </button>
              </div>
              <div className="max-h-[520px] overflow-auto">
                {job.type === "BACKTEST" ? (
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-[#131722]">
                <tr className="border-b border-[#2a2e39] text-left text-[#868993]">
                  <th className="px-4 py-2">Order No.</th>
                  <th className="px-4 py-2 cursor-pointer select-none hover:text-[#d1d4dc]" onClick={() => setTimeSortAsc((v) => !v)}>Time {timeSortAsc ? "▲" : "▼"}</th>
                  <th className="px-4 py-2">Symbol</th>
                  <th className="px-4 py-2">Side</th>
                  <th className="px-4 py-2">Price</th>
                  <th className="px-4 py-2">Quantity</th>
                  <th className="px-4 py-2">Fee</th>
                  <th className="px-4 py-2">Role</th>
                  <th className="px-4 py-2">Realized Profit</th>
                  <th className="px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {(timeSortAsc ? enrichedBacktest : [...enrichedBacktest].reverse()).map((t, idx) => (
                  <tr key={`bt-${t.id}`} className="border-b border-[#2a2e39]">
                    <td className="px-4 py-2 text-[#868993]">{t.orderId ?? idx + 1}</td>
                    <td className="px-4 py-2 text-[#d1d4dc]"><TimeCell value={t.timestamp} /></td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.symbol ?? backtestSymbol ?? "-"}{" "}
                      <span className="rounded bg-[#2a2e39] px-1 py-0.5 text-[10px] text-[#868993]">Perp</span>
                    </td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        t.side === "BUY" ? "text-[#26a69a]" : "text-[#ef5350]"
                      }`}
                    >
                      {t.side === "BUY" ? "Buy" : t.side === "SELL" ? "Sell" : t.side ?? "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.price !== null ? formatNumber(t.price, 2) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.positionSizeUsdt !== null ? `${formatNumber(t.positionSizeUsdt, 1)} USDT` : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.commission !== null ? `${formatNumber(t.commission, 8)} USDT` : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">{t.role ?? "-"}</td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        (t.pnl ?? 0) > 0 ? "text-[#26a69a]" : (t.pnl ?? 0) < 0 ? "text-[#ef5350]" : "text-[#d1d4dc]"
                      }`}
                    >
                      {t.pnl !== null ? `${formatNumber(t.pnl, 8)} USDT` : "0.00000000 USDT"}
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
                  <th className="px-4 py-2">Order No.</th>
                  <th className="px-4 py-2 cursor-pointer select-none hover:text-[#d1d4dc]" onClick={() => setTimeSortAsc((v) => !v)}>Time {timeSortAsc ? "▲" : "▼"}</th>
                  <th className="px-4 py-2">Symbol</th>
                  <th className="px-4 py-2">Side</th>
                  <th className="px-4 py-2">Price</th>
                  <th className="px-4 py-2">Quantity</th>
                  <th className="px-4 py-2">Fee</th>
                  <th className="px-4 py-2">Role</th>
                  <th className="px-4 py-2">Realized Profit</th>
                  <th className="px-4 py-2">Reason</th>
                </tr>
              </thead>
              <tbody>
                {(timeSortAsc ? enrichedLive : [...enrichedLive].reverse()).map((t, idx) => (
                  <tr key={`lv-${t.id}`} className="border-b border-[#2a2e39]">
                    <td className="px-4 py-2 text-[#868993]">{t.orderId ?? idx + 1}</td>
                    <td className="px-4 py-2 text-[#d1d4dc]"><TimeCell value={t.timestamp} /></td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.symbol ?? "-"}{" "}
                      <span className="rounded bg-[#2a2e39] px-1 py-0.5 text-[10px] text-[#868993]">Perp</span>
                    </td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        t.side === "BUY" ? "text-[#26a69a]" : t.side === "SELL" ? "text-[#ef5350]" : "text-[#d1d4dc]"
                      }`}
                    >
                      {t.side === "BUY" ? "Buy" : t.side === "SELL" ? "Sell" : t.side ?? "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.price !== null ? formatNumber(t.price, 2) : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.positionSizeUsdt !== null ? `${formatNumber(t.positionSizeUsdt, 1)} USDT` : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">
                      {t.commission !== null ? `${formatNumber(t.commission, 8)} USDT` : "-"}
                    </td>
                    <td className="px-4 py-2 text-[#d1d4dc]">{t.role ?? "-"}</td>
                    <td
                      className={`px-4 py-2 font-medium ${
                        (t.pnl ?? 0) > 0 ? "text-[#26a69a]" : (t.pnl ?? 0) < 0 ? "text-[#ef5350]" : "text-[#d1d4dc]"
                      }`}
                    >
                      {t.pnl !== null ? `${formatNumber(t.pnl, 8)} USDT` : "0.00000000 USDT"}
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
          ) : (
            /* Positions tab */
            <div className="rounded border border-[#2a2e39] bg-[#0f141f]">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#2a2e39] px-4 py-2">
                <span className="text-xs font-medium text-[#d1d4dc]">Position History ({positions.filter((p) => p.status === "Closed").length})</span>
              </div>
              {positions.filter((p) => p.status === "Closed").length === 0 ? (
                <div className="px-4 py-6 text-center text-xs text-[#868993]">No position history</div>
              ) : (
                <div className="max-h-[520px] overflow-auto">
                  <div className="divide-y divide-[#2a2e39]">
                    {[...positions].filter((p) => p.status === "Closed").reverse().map((pos, idx) => (
                      <div
                        key={idx}
                        className="px-4 py-4"
                      >
                        <div className="mb-3 flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-[#d1d4dc]">{pos.symbol}</span>
                          <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#868993]">Perp</span>
                          <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#868993]">{leverage}x</span>
                          <span
                            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                              pos.direction.includes("Long")
                                ? "bg-[#26a69a]/20 text-[#26a69a]"
                                : "bg-[#ef5350]/20 text-[#ef5350]"
                            }`}
                          >
                            {pos.direction}
                          </span>
                          <span className="text-xs text-[#868993]">{pos.status}</span>
                          <div className="ml-auto flex gap-4 text-[10px] text-[#868993]">
                            <span>{pos.openedAt} Opened</span>
                            {pos.closedAt && <span>{pos.closedAt} Closed</span>}
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3 lg:grid-cols-6">
                          <div>
                            <div className="text-[#868993]">Realized PNL (USDT)</div>
                            <div className={`font-medium ${pos.realizedPnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}`}>
                              {formatSigned(pos.realizedPnl, "USDT")}
                            </div>
                          </div>
                          <div>
                            <div className="text-[#868993]">ROI</div>
                            <div className={`font-medium ${pos.roi >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}`}>
                              {formatSigned(pos.roi)}%
                            </div>
                          </div>
                          <div>
                            <div className="text-[#868993]">Closed Vol. (BTC)</div>
                            <div className="text-[#d1d4dc]">{pos.closedVol > 0 ? formatNumber(pos.closedVol, 3) : "-"}</div>
                          </div>
                          <div>
                            <div className="text-[#868993]">Entry Price</div>
                            <div className="text-[#d1d4dc]">{formatNumber(pos.entryPrice, 2)}</div>
                          </div>
                          <div>
                            <div className="text-[#868993]">Avg. Close Price</div>
                            <div className="text-[#d1d4dc]">{pos.avgClosePrice !== null ? formatNumber(pos.avgClosePrice, 2) : "-"}</div>
                          </div>
                          <div>
                            <div className="text-[#868993]">Max OI (BTC)</div>
                            <div className="text-[#d1d4dc]">{formatNumber(pos.maxOi, 3)}</div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
      </div>
    </section>
  );
}
