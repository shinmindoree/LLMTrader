"use client";

import Link from "next/link";
import { useMemo } from "react";

import useSWR from "swr";
import { getBinanceAccountSummary, listTrades } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { BinanceAccountSummary, JobSummary, JobStatus, Trade } from "@/lib/types";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { JobConfigInline } from "@/components/JobConfigSummary";
import { jobDetailPath } from "@/lib/routes";
import { normalizeLiveTrades, buildPositions, computeTradeStats } from "@/components/TradeAnalysis";
import { PositionPanel } from "@/components/LivePositionPanel";
import { TimeCell } from "@/components/TimeCell";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function extractSymbols(config: Record<string, unknown>): string[] {
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const syms: string[] = [];
  for (const raw of streams) {
    if (!isRecord(raw)) continue;
    const sym = typeof raw.symbol === "string" ? raw.symbol.trim().toUpperCase() : "";
    if (sym) syms.push(sym);
  }
  if (syms.length === 0 && typeof config.symbol === "string") {
    const s = config.symbol.trim().toUpperCase();
    if (s) syms.push(s);
  }
  return syms;
}

const formatNumber = (value: number, digits = 2): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const formatSigned = (value: number, suffix = ""): string => {
  const formatted = formatNumber(value, 2);
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatted}${suffix ? ` ${suffix}` : ""}`;
};

export function ActiveJobCard({
  job,
  busy,
  onStop,
}: {
  job: JobSummary;
  busy: boolean;
  onStop: (job: JobSummary) => void;
}) {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
  const isActive = !FINISHED_STATUSES.has(job.status);

  const { data: trades = [] } = useSWR<Trade[]>(
    isActive ? ["trades", job.job_id] : null,
    () => listTrades(job.job_id),
    {
      refreshInterval: isVisible ? 10_000 : 30_000,
      dedupingInterval: 5_000,
    },
  );

  const { data: snapshot } = useSWR<BinanceAccountSummary>(
    isActive ? "binanceAccountSummary" : null,
    () => getBinanceAccountSummary(),
    {
      refreshInterval: isVisible ? 15_000 : 30_000,
      dedupingInterval: 5_000,
    },
  );

  const symbols = useMemo(
    () => (job.config ? extractSymbols(job.config) : []),
    [job.config],
  );

  const positions = snapshot?.positions;
  const matchedPositions = useMemo(() => {
    if (!positions || symbols.length === 0) return [];
    return positions.filter((p) => symbols.includes(p.symbol.toUpperCase()));
  }, [positions, symbols]);

  const leverage = useMemo(() => {
    if (!job.config) return 1;
    const cfg = job.config as Record<string, unknown>;
    return typeof cfg.leverage === "number" ? cfg.leverage : 1;
  }, [job.config]);

  const closedPositionsList = useMemo(() => {
    const normalized = normalizeLiveTrades(trades);
    const sorted = [...normalized].sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
    const built = buildPositions(sorted, leverage);
    return built.filter((p) => p.status === "Closed");
  }, [trades, leverage]);

  const netPnl = useMemo(() => {
    if (closedPositionsList.length === 0) return null;
    return closedPositionsList.reduce((s, p) => s + p.realizedPnl, 0);
  }, [closedPositionsList]);

  const numTrades = closedPositionsList.length;
  const winCount = closedPositionsList.filter((p) => p.realizedPnl > 0).length;
  const lossCount = numTrades - winCount;
  const winRate = numTrades > 0 ? (winCount / numTrades) * 100 : null;

  const positionPnls = useMemo(
    () => closedPositionsList.map((p) => p.realizedPnl),
    [closedPositionsList],
  );
  const tradeStats = useMemo(() => computeTradeStats(positionPnls), [positionPnls]);

  const unrealizedPnl = useMemo(() => {
    return matchedPositions.reduce((s, p) => s + p.unrealized_pnl, 0);
  }, [matchedPositions]);

  const totalPnl = netPnl !== null ? netPnl + unrealizedPnl : null;

  const runningDuration = useMemo(() => {
    const ms = Date.now() - new Date(job.created_at).getTime();
    if (ms < 0) return null;
    const totalMin = Math.floor(ms / 60_000);
    const d = Math.floor(totalMin / 1440);
    const h = Math.floor((totalMin % 1440) / 60);
    const m = totalMin % 60;
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  }, [job.created_at]);

  const lastTradeAgo = useMemo(() => {
    if (trades.length === 0) return null;
    const timestamps = trades
      .map((tr) => {
        if (tr.ts) return typeof tr.ts === "number" ? tr.ts : Date.parse(String(tr.ts));
        return null;
      })
      .filter((ts): ts is number => ts !== null && !Number.isNaN(ts));
    if (timestamps.length === 0) return null;
    const last = Math.max(...timestamps);
    const ms = Date.now() - last;
    if (ms < 0) return null;
    const totalMin = Math.floor(ms / 60_000);
    if (totalMin < 1) return "just now";
    const h = Math.floor(totalMin / 60);
    const m = totalMin % 60;
    if (h > 0) return `${h}h ${m}m ago`;
    return `${m}m ago`;
  }, [trades]);

  return (
    <li className="rounded-lg border border-[#2962ff]/30 bg-[#1a2340]/50 px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#868993]">{t.live.strategyLabel}</span>
            <Link
              className="font-medium text-[#d1d4dc] hover:text-[#2962ff] hover:underline transition-colors"
              href={jobDetailPath("LIVE", job.job_id)}
            >
              {strategyNameFromPath(job.strategy_path)}
            </Link>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <JobStatusBadge status={job.status} />
          {(job.status === "PENDING" || job.status === "RUNNING") && (
            <button
              className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] hover:border-[#ef5350] hover:text-[#ef5350] disabled:opacity-50 transition-colors"
              disabled={busy}
              onClick={() => onStop(job)}
              type="button"
            >
              {t.common.stop}
            </button>
          )}
        </div>
      </div>

      {/* Trading Config */}
      {job.config ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#868993]">{t.live.configLabel}</span>
          <JobConfigInline type="LIVE" config={job.config} />
        </div>
      ) : null}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 text-xs text-[#868993]">
        <span className="inline-flex items-center gap-1">{t.live.started} <TimeCell value={job.created_at} /></span>
        {runningDuration && (
          <span className="text-[#d1d4dc]">· ⏱ {runningDuration}</span>
        )}
        {lastTradeAgo && (
          <span>· {t.live.lastTrade} {lastTradeAgo}</span>
        )}
      </div>

      {trades.length > 0 ? (
        <div className="mt-3 grid grid-cols-3 gap-2">
          <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
            <div className="text-[10px] text-[#868993]">{t.result.netProfit}</div>
            <div className={`text-sm font-semibold ${netPnl !== null && netPnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}`}>
              {netPnl !== null ? formatSigned(netPnl, "USDT") : "-"}
            </div>
            {matchedPositions.length > 0 && totalPnl !== null && (
              <div className={`mt-0.5 text-[10px] ${totalPnl >= 0 ? "text-[#26a69a]/70" : "text-[#ef5350]/70"}`}>
                {t.live.totalIncUnrealized} {formatSigned(totalPnl, "USDT")}
              </div>
            )}
          </div>
          <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
            <div className="text-[10px] text-[#868993]">{t.result.totalTrades}</div>
            <div className="text-sm font-semibold text-[#d1d4dc]">
              {numTrades} <span className="text-[10px] font-normal text-[#868993]">({winCount}W / {lossCount}L)</span>
            </div>
            {tradeStats && (
              <div className="mt-0.5 text-[10px] text-[#868993]">
                {t.result.profitFactor} {tradeStats.profitFactor === Infinity ? "∞" : formatNumber(tradeStats.profitFactor)}
              </div>
            )}
          </div>
          <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
            <div className="text-[10px] text-[#868993]">{t.result.winRate}</div>
            <div className={`text-sm font-semibold ${winRate !== null && winRate >= 50 ? "text-[#26a69a]" : winRate !== null && winRate > 0 ? "text-[#ef5350]" : "text-[#d1d4dc]"}`}>
              {winRate !== null ? `${formatNumber(winRate, 1)}%` : "-"}
            </div>
            {tradeStats?.expectancy != null && (
              <div className={`mt-0.5 text-[10px] ${tradeStats.expectancy >= 0 ? "text-[#26a69a]/70" : "text-[#ef5350]/70"}`}>
                {t.tradeAnalysis.expectancy} {formatSigned(tradeStats.expectancy, "USDT")}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="mt-2 text-xs text-[#868993] italic">
          {t.latestResult.runInProgress}
        </div>
      )}

      {matchedPositions.length > 0 && (
        <div className="mt-2">
          <PositionPanel positions={matchedPositions} />
        </div>
      )}
    </li>
  );
}
