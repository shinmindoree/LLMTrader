"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import type { Locale } from "@/lib/i18n/translations";
import { getBinanceKeysStatus, getJobCounts, listJobSummaries, listStrategies } from "@/lib/api";
import type { JobSummary, QuickBacktestEquityPoint } from "@/lib/types";
import { useLiveJobStream } from "@/lib/useLiveJobStream";
import { AssetOverviewPanel } from "@/components/AssetOverviewPanel";
import { FuturesWatchlistRail } from "@/components/FuturesWatchlistRail";
import { NewsFlowPanel } from "@/components/NewsFlowPanel";
import { DashboardSkeleton } from "@/components/skeletons/DashboardSkeleton";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { DASHBOARD_FUTURES_WATCH_SYMBOLS } from "@/lib/dashboardFuturesSymbols";
import { useBinanceFuturesTickerStream } from "@/lib/useBinanceFuturesTickerStream";

const MiniEquityCurve = dynamic(
  () => import("@/app/strategies/_components/MiniEquityCurve"),
  { ssr: false, loading: () => <div className="h-12 animate-pulse rounded bg-[#131722]" /> },
);

const DASHBOARD_RUNNING_LIMIT = 64;
const EMPTY_JOBS: JobSummary[] = [];

const linkFocusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2962ff] focus-visible:ring-offset-2 focus-visible:ring-offset-[#131722]";

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

function formatRelativePast(msAgo: number, locale: Locale): string {
  if (!Number.isFinite(msAgo) || msAgo < 0) return "";
  const seconds = Math.floor(msAgo / 1000);
  if (seconds < 5) return "";
  const rtf = new Intl.RelativeTimeFormat(locale === "ko" ? "ko" : "en", { numeric: "auto" });
  if (seconds < 60) return rtf.format(-seconds, "second");
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return rtf.format(-minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return rtf.format(-hours, "hour");
  const days = Math.floor(hours / 24);
  return rtf.format(-days, "day");
}

function equityPointsFromSummary(
  summary: Record<string, unknown> | null | undefined,
): QuickBacktestEquityPoint[] | null {
  if (!summary || typeof summary !== "object") return null;
  const curve = summary.equity_curve;
  if (Array.isArray(curve) && curve.length >= 2) {
    const pts: QuickBacktestEquityPoint[] = [];
    for (const row of curve) {
      if (!row || typeof row !== "object") continue;
      const r = row as Record<string, unknown>;
      const ts = typeof r.ts === "number" ? r.ts : Number(r.ts);
      const balance = typeof r.balance === "number" ? r.balance : Number(r.balance);
      if (Number.isFinite(ts) && Number.isFinite(balance)) pts.push({ ts, balance });
    }
    if (pts.length >= 2) return pts;
  }
  const initial =
    (typeof summary.initial_balance === "number" ? summary.initial_balance : Number(summary.initial_balance)) ||
    (typeof summary.initial_equity === "number" ? summary.initial_equity : Number(summary.initial_equity));
  const final =
    (typeof summary.final_balance === "number" ? summary.final_balance : Number(summary.final_balance)) ||
    (typeof summary.final_equity === "number" ? summary.final_equity : Number(summary.final_equity));
  if (Number.isFinite(initial) && Number.isFinite(final)) {
    return [
      { ts: 0, balance: initial as number },
      { ts: 1, balance: final as number },
    ];
  }
  return null;
}

function StatBadge({ label, value, color = "text-[#868993]" }: { label: string; value: string; color?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded bg-[#2a2e39] px-1.5 py-0.5 text-[11px] font-medium ${color}`}>
      {label && <span className="text-[#555]">{label}</span>}
      {value}
    </span>
  );
}

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

function CardShell({
  href,
  error,
  onRetry,
  errorLabel,
  retryLabel,
  openSectionLabel,
  className,
  children,
}: {
  href: string;
  error: unknown;
  onRetry: () => void;
  errorLabel: string;
  retryLabel: string;
  openSectionLabel: string;
  className: string;
  children: ReactNode;
}) {
  if (error) {
    return (
      <div
        className={`flex flex-col rounded-lg border border-[#ef5350]/40 bg-[#1e222d] p-4 ${className}`}
        role="group"
      >
        <p className="text-sm text-[#ef5350]">{errorLabel}</p>
        <button
          type="button"
          onClick={onRetry}
          className={`mt-3 w-fit rounded border border-[#2a2e39] px-3 py-1.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#2962ff]/10 ${linkFocusRing}`}
        >
          {retryLabel}
        </button>
        <Link href={href} className={`mt-3 text-xs font-medium text-[#2962ff] hover:text-[#5b8cff] ${linkFocusRing} w-fit rounded-sm`}>
          {openSectionLabel} →
        </Link>
      </div>
    );
  }
  return (
    <Link href={href} className={`group flex flex-col rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4 transition-colors ${className} ${linkFocusRing}`}>
      {children}
    </Link>
  );
}

export function DashboardPanel() {
  const { t, locale } = useI18n();
  const futuresTickers = useBinanceFuturesTickerStream(DASHBOARD_FUTURES_WATCH_SYMBOLS);

  const {
    data: strategies,
    error: strategiesError,
    isLoading: strategiesLoading,
    mutate: mutateStrategies,
  } = useSWR(["dashboard", "strategies"], () => listStrategies());

  const {
    data: jobCounts,
    error: jobCountsError,
    isLoading: jobCountsLoading,
    mutate: mutateJobCounts,
  } = useSWR(["dashboard", "job-counts"], () => getJobCounts());

  const {
    data: liveRunningJobs,
    error: liveRunningError,
    isLoading: liveRunningLoading,
    mutate: mutateLiveRunning,
  } = useSWR(
    ["dashboard", "jobs", "LIVE", "RUNNING", DASHBOARD_RUNNING_LIMIT],
    () =>
      listJobSummaries({
        type: "LIVE",
        status: "RUNNING",
        limit: DASHBOARD_RUNNING_LIMIT,
      }),
  );

  const {
    data: latestBacktest,
    error: latestBacktestError,
    isLoading: latestBacktestLoading,
    mutate: mutateLatestBacktest,
  } = useSWR(["dashboard", "jobs", "BACKTEST", "SUCCEEDED", 1], () =>
    listJobSummaries({ type: "BACKTEST", status: "SUCCEEDED", limit: 1 }),
  );

  const {
    data: keysStatus,
    error: keysError,
    isLoading: keysLoading,
    mutate: mutateKeys,
  } = useSWR(["dashboard", "binance-keys"], () => getBinanceKeysStatus());

  const runningLive = liveRunningJobs ?? EMPTY_JOBS;

  const binanceOk = !!keysStatus?.configured;

  const lastBt = latestBacktest?.[0];
  const lastBtSummary = lastBt?.result_summary as Record<string, unknown> | null | undefined;

  const sortedStrategies = useMemo(() => {
    if (!strategies?.length) return [];
    return [...strategies].sort((a, b) => a.path.localeCompare(b.path));
  }, [strategies]);

  const featuredStrategyName = useMemo(() => {
    if (lastBt?.strategy_path) return strategyNameFromPath(lastBt.strategy_path);
    if (runningLive[0]?.strategy_path) return strategyNameFromPath(runningLive[0].strategy_path);
    if (sortedStrategies[0]) return strategyNameFromPath(sortedStrategies[0].path);
    return null;
  }, [lastBt, runningLive, sortedStrategies]);

  const equityPoints = useMemo(() => equityPointsFromSummary(lastBtSummary), [lastBtSummary]);
  const equityInitial =
    equityPoints && equityPoints.length >= 1
      ? equityPoints[0].balance
      : typeof lastBtSummary?.initial_balance === "number"
        ? lastBtSummary.initial_balance
        : Number(lastBtSummary?.initial_balance);

  const liveStats = useLiveTradeStats(runningLive.length > 0);

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, []);

  const [lastBundleFetchedAt, setLastBundleFetchedAt] = useState(0);
  const showSkeleton =
    strategiesLoading &&
    strategies === undefined &&
    !strategiesError &&
    jobCountsLoading &&
    jobCounts === undefined &&
    !jobCountsError &&
    liveRunningLoading &&
    liveRunningJobs === undefined &&
    !liveRunningError &&
    keysLoading &&
    keysStatus === undefined &&
    !keysError &&
    latestBacktestLoading &&
    latestBacktest === undefined &&
    !latestBacktestError;

  useEffect(() => {
    if (showSkeleton) return;
    const id = requestAnimationFrame(() => setLastBundleFetchedAt(Date.now()));
    return () => cancelAnimationFrame(id);
  }, [showSkeleton, strategies, jobCounts, liveRunningJobs, keysStatus, latestBacktest]);

  const anyFetchError = !!(
    strategiesError ||
    jobCountsError ||
    liveRunningError ||
    keysError ||
    latestBacktestError
  );

  const retryAll = () => {
    void mutateStrategies();
    void mutateJobCounts();
    void mutateLiveRunning();
    void mutateKeys();
    void mutateLatestBacktest();
  };

  const relativeUpdate =
    !showSkeleton && lastBundleFetchedAt > 0
      ? formatRelativePast(nowMs - lastBundleFetchedAt, locale)
      : "";
  const lastUpdatedText =
    !showSkeleton && lastBundleFetchedAt > 0
      ? relativeUpdate
        ? t.dashboard.lastUpdated.replace("{relative}", relativeUpdate)
        : t.dashboard.lastUpdated.replace("{relative}", t.dashboard.lastUpdatedJustNow)
      : null;

  const ariaLiveText = useMemo(() => {
    if (runningLive.length === 0 || !liveStats) return "";
    const wr =
      liveStats.winRate !== null ? `${formatNumber(liveStats.winRate, 1)}%` : "—";
    return t.dashboard.ariaLiveStats
      .replace("{count}", String(runningLive.length))
      .replace("{pnl}", formatSigned(liveStats.netPnl))
      .replace("{wr}", wr);
  }, [runningLive.length, liveStats, t.dashboard.ariaLiveStats]);

  if (showSkeleton) {
    return <DashboardSkeleton />;
  }

  return (
    <div className="relative flex w-full py-4 pl-0 pr-4">
      <a
        href="#dashboard-main"
        className="absolute left-[-9999px] top-0 z-[60] rounded bg-[#2962ff] px-3 py-2 text-sm text-white focus:left-0 focus:top-20 focus:outline-none"
      >
        {t.dashboard.skipToContent}
      </a>

      <div className="min-w-0 w-full max-w-6xl flex-1 shrink-0" id="dashboard-main">
        {anyFetchError ? (
          <div
            className="mb-4 flex flex-col gap-2 rounded-lg border border-[#ef5350]/35 bg-[#ef5350]/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            role="alert"
          >
            <p className="text-sm text-[#d1d4dc]">{t.dashboard.errorPartialLoad}</p>
            <button
              type="button"
              onClick={retryAll}
              className={`rounded border border-[#2a2e39] px-3 py-1.5 text-sm font-medium text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#2962ff]/10 ${linkFocusRing}`}
            >
              {t.dashboard.retryLoad}
            </button>
          </div>
        ) : null}

        <header className="mb-6">
          <h1 className="text-xl font-semibold text-[#d1d4dc]">{t.dashboard.title}</h1>
          <p className="mt-1 max-w-xl text-sm text-[#868993]">{t.dashboard.subtitle}</p>
          {lastUpdatedText ? (
            <p className="mt-2 text-xs text-[#555]" aria-live="polite">
              {lastUpdatedText}
            </p>
          ) : null}
        </header>

        <div className="flex flex-col gap-3 rounded-lg border border-[#2a2e39] bg-[#1e222d] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="font-semibold text-[#F0B90B]">₿</span>
            <span className="font-medium text-[#d1d4dc]">Binance USDⓈ-M Futures</span>
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
            className={`text-sm font-medium text-[#2962ff] hover:text-[#5b8cff] sm:shrink-0 ${linkFocusRing} rounded-sm`}
          >
            {t.dashboard.settingsLink} →
          </Link>
        </div>

        {keysError ? (
          <p className="mt-3 text-xs text-[#ef5350]">{t.dashboard.cardLoadError}</p>
        ) : null}
        {!keysLoading && !binanceOk ? (
          <p className="mt-3 text-xs text-[#868993]">{t.dashboard.hintKeys}</p>
        ) : null}
        {!strategiesLoading && strategies !== undefined && strategies.length === 0 ? (
          <p className="mt-2 text-xs text-[#868993]">{t.dashboard.hintNoStrategies}</p>
        ) : null}

        {(!binanceOk || (strategies !== undefined && strategies.length === 0)) && (
          <div className="mt-4 rounded-lg border border-dashed border-[#2a2e39] bg-[#1e222d]/50 px-4 py-3">
            <p className="text-xs font-medium uppercase tracking-wide text-[#868993]">{t.dashboard.onboardingNext}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {!binanceOk ? (
                <Link
                  href="/settings"
                  className={`rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#2962ff]/10 ${linkFocusRing}`}
                >
                  {t.dashboard.ctaSettings}
                </Link>
              ) : null}
              {strategies !== undefined && strategies.length === 0 ? (
                <Link
                  href="/strategies"
                  className={`rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#2962ff]/10 ${linkFocusRing}`}
                >
                  {t.dashboard.ctaStrategies}
                </Link>
              ) : null}
              <Link
                href="/backtest/new"
                className={`rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium text-[#d1d4dc] transition-colors hover:border-[#2962ff] hover:bg-[#2962ff]/10 ${linkFocusRing}`}
              >
                {t.dashboard.ctaBacktest}
              </Link>
              <Link
                href="/live/new"
                className={`rounded border border-[#2a2e39] px-3 py-1.5 text-xs font-medium text-[#d1d4dc] transition-colors hover:border-[#26a69a] hover:bg-[#26a69a]/10 ${linkFocusRing}`}
              >
                {t.dashboard.ctaLive}
              </Link>
            </div>
          </div>
        )}

        <div className="sr-only" aria-live="polite" aria-atomic="true">
          {ariaLiveText}
        </div>

        <div className="mt-6">
          <AssetOverviewPanel />
        </div>

        <NewsFlowPanel tickersBySymbol={futuresTickers.bySymbol} />
      </div>

      <aside
        className="sticky top-2 hidden h-[calc(100dvh-5rem)] max-h-[calc(100dvh-5rem)] w-80 shrink-0 self-start overflow-hidden xl:block"
        aria-label={t.dashboard.futuresRailTitle}
      >
        <FuturesWatchlistRail
          symbols={DASHBOARD_FUTURES_WATCH_SYMBOLS}
          bySymbol={futuresTickers.bySymbol}
          status={futuresTickers.status}
          onRetryRest={futuresTickers.refetchRest}
        />
      </aside>
    </div>
  );
}
