"use client";

import Link from "next/link";
import { useMemo } from "react";
import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import { getBinanceKeysStatus, getJobCounts, listJobSummaries, listStrategies } from "@/lib/api";
import { useLiveJobStream } from "@/lib/useLiveJobStream";
import { AssetOverviewPanel } from "@/components/AssetOverviewPanel";
import { DashboardSkeleton } from "@/components/skeletons/DashboardSkeleton";
import { LoadingSpinner } from "@/components/LoadingSpinner";

const DASHBOARD_RUNNING_LIMIT = 64;

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "—";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

const formatNumber = (value: number, digits = 2): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const formatSigned = (value: number, suffix = ""): string => {
  const formatted = formatNumber(value, 2);
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatted}${suffix ? ` ${suffix}` : ""}`;
};

/* ── Shared tiny components ── */

function StatBadge({ label, value, color = "text-[#868993]" }: { label: string; value: string; color?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded bg-[#2a2e39] px-1.5 py-0.5 text-[11px] font-medium ${color}`}>
      {label && <span className="text-[#555]">{label}</span>}
      {value}
    </span>
  );
}

/* ── Aggregated live stats from SSE stream ── */

type LiveStats = { netPnl: number; totalTrades: number; winRate: number | null };

function useLiveTradeStats(hasRunningJobs: boolean): LiveStats | null {
  const { jobs } = useLiveJobStream(hasRunningJobs);

  return useMemo(() => {
    if (jobs.length === 0) return null;
    const allTrades = jobs.flatMap((j) => j.trades);
    if (allTrades.length === 0) return null;

    const netPnl = allTrades.reduce((s, tr) => s + (tr.realized_pnl ?? 0), 0);
    const closedPnls = allTrades
      .map((tr) => tr.realized_pnl)
      .filter((p): p is number => p !== null && p !== undefined && Number.isFinite(p) && p !== 0);
    const winCount = closedPnls.filter((p) => p > 0).length;
    const totalClosed = closedPnls.length;
    const winRate = totalClosed > 0 ? (winCount / totalClosed) * 100 : null;

    return { netPnl, totalTrades: allTrades.length, winRate };
  }, [jobs]);
}

export function DashboardPanel() {
  const { t } = useI18n();

  const { data: strategies, isLoading: strategiesLoading } = useSWR(
    ["dashboard", "strategies"],
    () => listStrategies(),
  );

  const { data: jobCounts, isLoading: jobCountsLoading } = useSWR(
    ["dashboard", "job-counts"],
    () => getJobCounts(),
  );

  const { data: liveRunningJobs, isLoading: liveRunningLoading } = useSWR(
    ["dashboard", "jobs", "LIVE", "RUNNING", DASHBOARD_RUNNING_LIMIT],
    () =>
      listJobSummaries({
        type: "LIVE",
        status: "RUNNING",
        limit: DASHBOARD_RUNNING_LIMIT,
      }),
  );

  const { data: latestBacktest } = useSWR(
    ["dashboard", "jobs", "BACKTEST", "SUCCEEDED", 1],
    () => listJobSummaries({ type: "BACKTEST", status: "SUCCEEDED", limit: 1 }),
  );

  const { data: keysStatus, isLoading: keysLoading } = useSWR(
    ["dashboard", "binance-keys"],
    () => getBinanceKeysStatus(),
  );

  const runningLive = liveRunningJobs ?? [];

  const binanceOk = !!keysStatus?.configured;

  const latestStrategy =
    strategies && strategies.length > 0
      ? strategyNameFromPath(strategies[strategies.length - 1].path)
      : null;

  // Latest completed backtest summary
  const lastBt = latestBacktest?.[0];
  const lastBtSummary = lastBt?.result_summary as Record<string, unknown> | null | undefined;

  // Real-time aggregated stats from SSE stream
  const liveStats = useLiveTradeStats(runningLive.length > 0);

  // Show skeleton until primary data has loaded (SWR isLoading = first fetch only)
  if (strategiesLoading || jobCountsLoading || liveRunningLoading || keysLoading) {
    return <DashboardSkeleton />;
  }

  return (
    <div className="w-full px-4 py-4">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-[#d1d4dc]">{t.dashboard.title}</h1>
        <p className="mt-1 max-w-xl text-sm text-[#868993]">{t.dashboard.subtitle}</p>
      </header>

      {/* Exchange connection bar */}
      <div className="flex flex-col gap-3 rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-[#868993]">{t.dashboard.exchangeLabel}</span>
          <span className="font-medium text-[#d1d4dc]">{t.dashboard.binance}</span>
          <span
            className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-medium ${
              keysLoading
                ? "bg-[#2a2e39] text-[#868993]"
                : binanceOk
                  ? "bg-[#26a69a]/15 text-[#26a69a]"
                  : "bg-[#ef5350]/15 text-[#ef5350]"
            }`}
          >
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${
                keysLoading ? "bg-[#868993]" : binanceOk ? "bg-[#26a69a]" : "bg-[#ef5350]"
              }`}
            />
            {keysLoading
              ? t.dashboard.exchangeChecking
              : binanceOk
                ? t.dashboard.statusConnected
                : t.dashboard.statusNotConnected}
          </span>
        </div>
        <Link
          href="/settings"
          className="text-sm font-medium text-[#2962ff] hover:text-[#5b8cff] sm:shrink-0"
        >
          {t.dashboard.settingsLink} →
        </Link>
      </div>

      {!keysLoading && !binanceOk && (
        <p className="mt-3 text-xs text-[#868993]">{t.dashboard.hintKeys}</p>
      )}
      {!strategiesLoading && strategies !== undefined && strategies.length === 0 && (
        <p className="mt-2 text-xs text-[#868993]">{t.dashboard.hintNoStrategies}</p>
      )}

      {/* ── Stat cards ── */}
      <div className="mt-5 grid gap-3 sm:grid-cols-3">

        {/* Card: My Strategies */}
        <Link
          href="/strategies"
          className="group flex flex-col rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4 transition-colors hover:border-[#2962ff]"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-[#868993]">
              {t.dashboard.strategyCount}
            </span>
            <svg className="h-4 w-4 text-[#555] transition-colors group-hover:text-[#2962ff]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25" />
            </svg>
          </div>
          <div className="mt-2 text-3xl font-bold text-[#d1d4dc]">
            {strategies === undefined && strategiesLoading
              ? <LoadingSpinner size="md" />
              : (strategies?.length ?? 0)}
          </div>
          {latestStrategy && (
            <div className="mt-3 flex flex-col gap-1 border-t border-[#2a2e39] pt-3">
              <span className="text-[10px] font-medium uppercase tracking-wider text-[#555]">{t.dashboard.statLatest}</span>
              <span className="truncate text-xs text-[#b2b5be]">{latestStrategy}</span>
            </div>
          )}
        </Link>

        {/* Card: Simulations */}
        <Link
          href="/backtest"
          className="group flex flex-col rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4 transition-colors hover:border-[#2962ff]"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-[#868993]">
              {t.dashboard.backtestCount}
            </span>
            <svg className="h-4 w-4 text-[#555] transition-colors group-hover:text-[#2962ff]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
            </svg>
          </div>
          <div className="mt-2 text-3xl font-bold text-[#d1d4dc]">
            {jobCounts === undefined && jobCountsLoading
              ? <LoadingSpinner size="md" />
              : (jobCounts?.backtest_total ?? 0)}
          </div>
          {lastBt && (
            <div className="mt-3 flex flex-col gap-2 border-t border-[#2a2e39] pt-3">
              <span className="truncate text-xs font-medium text-[#b2b5be]">{strategyNameFromPath(lastBt.strategy_path)}</span>
              <div className="grid grid-cols-3 gap-1.5">
                {typeof lastBtSummary?.total_return_pct === "number" && (
                  <div className="rounded bg-[#131722] px-2 py-1.5">
                    <div className="text-[10px] text-[#555]">{t.dashboard.labelReturn}</div>
                    <div className={`text-xs font-semibold ${lastBtSummary.total_return_pct >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}`}>
                      {lastBtSummary.total_return_pct >= 0 ? "+" : ""}{lastBtSummary.total_return_pct.toFixed(1)}%
                    </div>
                  </div>
                )}
                {typeof lastBtSummary?.total_trades === "number" && lastBtSummary.total_trades > 0 && (
                  <div className="rounded bg-[#131722] px-2 py-1.5">
                    <div className="text-[10px] text-[#555]">{t.dashboard.labelTotalTrades}</div>
                    <div className="text-xs font-semibold text-[#d1d4dc]">{lastBtSummary.total_trades.toLocaleString()}</div>
                  </div>
                )}
                {typeof lastBtSummary?.win_rate === "number" && (
                  <div className="rounded bg-[#131722] px-2 py-1.5">
                    <div className="text-[10px] text-[#555]">{t.dashboard.labelWinRate}</div>
                    <div className="text-xs font-semibold text-[#d1d4dc]">{lastBtSummary.win_rate.toFixed(1)}%</div>
                  </div>
                )}
              </div>
            </div>
          )}
        </Link>

        {/* Card: Live Trading */}
        <Link
          href="/live"
          className="group flex flex-col rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4 transition-colors hover:border-[#26a69a]"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium uppercase tracking-wide text-[#868993]">
              {t.dashboard.runningLive}
            </span>
            {runningLive.length > 0 ? (
              <span className="relative flex h-3 w-3">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#26a69a] opacity-60" />
                <span className="relative inline-flex h-3 w-3 rounded-full bg-[#26a69a]" />
              </span>
            ) : (
              <svg className="h-4 w-4 text-[#555] transition-colors group-hover:text-[#26a69a]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3v11.25A2.25 2.25 0 0 0 6 16.5h2.25M3.75 3h-1.5m1.5 0h16.5m0 0h1.5m-1.5 0v11.25A2.25 2.25 0 0 1 18 16.5h-2.25m-7.5 0h7.5m-7.5 0-1 3m8.5-3 1 3m0 0 .5 1.5m-.5-1.5h-9.5m0 0-.5 1.5" />
              </svg>
            )}
          </div>
          <div className="mt-2 text-3xl font-bold text-[#26a69a]">
            {liveRunningJobs === undefined && liveRunningLoading
              ? <LoadingSpinner size="md" />
              : (runningLive.length)}
          </div>
          <div className="mt-3 flex flex-col gap-2 border-t border-[#2a2e39] pt-3">
            {runningLive.length > 0 ? (
              <>
                {runningLive.slice(0, 3).map((j) => {
                  const name = strategyNameFromPath(j.strategy_path);
                  const cfg = j.config as Record<string, unknown>;
                  const symbol = typeof cfg?.symbol === "string" ? cfg.symbol : null;
                  const interval = typeof cfg?.interval === "string" ? cfg.interval : null;
                  return (
                    <div key={j.job_id} className="flex items-center gap-1.5">
                      <span className="truncate text-xs font-medium text-[#b2b5be]">{name}</span>
                      {symbol && <StatBadge label="" value={symbol} color="text-[#d1d4dc]" />}
                      {interval && <StatBadge label="" value={interval} color="text-[#868993]" />}
                    </div>
                  );
                })}
                {runningLive.length > 3 && (
                  <span className="text-[11px] text-[#555]">+{runningLive.length - 3} more</span>
                )}
                {/* Real-time aggregated stats from trades */}
                {liveStats && (
                  <div className="mt-1 grid grid-cols-3 gap-1.5 border-t border-[#2a2e39] pt-2">
                    <div className="rounded bg-[#131722] px-2 py-1.5">
                      <div className="text-[10px] text-[#555]">{t.dashboard.labelNetProfit}</div>
                      <div className={`text-xs font-semibold ${liveStats.netPnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}`}>
                        {formatSigned(liveStats.netPnl, "USDT")}
                      </div>
                    </div>
                    <div className="rounded bg-[#131722] px-2 py-1.5">
                      <div className="text-[10px] text-[#555]">{t.dashboard.labelTotalTrades}</div>
                      <div className="text-xs font-semibold text-[#d1d4dc]">{liveStats.totalTrades}</div>
                    </div>
                    <div className="rounded bg-[#131722] px-2 py-1.5">
                      <div className="text-[10px] text-[#555]">{t.dashboard.labelWinRate}</div>
                      <div className="text-xs font-semibold text-[#d1d4dc]">
                        {liveStats.winRate !== null ? `${formatNumber(liveStats.winRate, 1)}%` : "—"}
                      </div>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <span className="text-xs text-[#555]">{t.dashboard.statNoRunning}</span>
            )}
          </div>
        </Link>

      </div>

      <div className="mt-6">
        <AssetOverviewPanel keysStatus={keysStatus ?? null} />
      </div>
    </div>
  );
}
