"use client";

import Link from "next/link";
import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import { getBinanceKeysStatus, getJobCounts, listJobSummaries, listStrategies } from "@/lib/api";
import { AssetOverviewPanel } from "@/components/AssetOverviewPanel";
import { LoadingSpinner } from "@/components/LoadingSpinner";

const DASHBOARD_RUNNING_LIMIT = 64;

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "—";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
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

  const { data: latestLiveCompleted } = useSWR(
    ["dashboard", "jobs", "LIVE", "SUCCEEDED", 1],
    () => listJobSummaries({ type: "LIVE", status: "SUCCEEDED", limit: 1 }),
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
  const btSub = lastBt
    ? (() => {
        const name = strategyNameFromPath(lastBt.strategy_path);
        const parts = [name];
        if (typeof lastBtSummary?.win_rate === "number") {
          parts.push(`${t.dashboard.labelWinRate} ${lastBtSummary.win_rate.toFixed(1)}%`);
        } else if (typeof lastBtSummary?.total_return_pct === "number") {
          const r = lastBtSummary.total_return_pct;
          parts.push(`${t.dashboard.labelReturn} ${r >= 0 ? "+" : ""}${r.toFixed(1)}%`);
        }
        if (typeof lastBtSummary?.total_trades === "number") {
          parts.push(`${lastBtSummary.total_trades.toLocaleString()} ${t.dashboard.labelTrades}`);
        }
        return parts.join(" · ");
      })()
    : null;

  // Running live jobs summary — show names + last completed stats
  const lastLive = latestLiveCompleted?.[0];
  const lastLiveSummary = lastLive?.result_summary as Record<string, unknown> | null | undefined;
  const liveSub = (() => {
    if (runningLive.length === 0) return t.dashboard.statNoRunning;
    const names = runningLive.slice(0, 2).map((j) => strategyNameFromPath(j.strategy_path)).join(", ");
    // Append last completed live result if available
    if (lastLiveSummary) {
      const parts: string[] = [];
      if (typeof lastLiveSummary.win_rate === "number") {
        parts.push(`${t.dashboard.labelWinRate} ${lastLiveSummary.win_rate.toFixed(1)}%`);
      }
      const trades = typeof lastLiveSummary.total_trades === "number" ? lastLiveSummary.total_trades
        : typeof lastLiveSummary.num_filled_orders === "number" ? lastLiveSummary.num_filled_orders : 0;
      if (trades > 0) {
        parts.push(`${trades.toLocaleString()} ${t.dashboard.labelTrades}`);
      }
      if (parts.length > 0) {
        return `${names} (${t.dashboard.labelLastResult}: ${parts.join(", ")})`;
      }
    }
    return names;
  })();

  const stats = [
    {
      label: t.dashboard.strategyCount,
      value:
        strategies === undefined && strategiesLoading ? null : (strategies?.length ?? 0),
      sub: latestStrategy ? `${t.dashboard.statLatest}: ${latestStrategy}` : null,
      href: "/strategies",
      color: "text-[#d1d4dc]",
      hoverBorder: "hover:border-[#2962ff]",
    },
    {
      label: t.dashboard.backtestCount,
      value:
        jobCounts === undefined && jobCountsLoading ? null : (jobCounts?.backtest_total ?? 0),
      sub: btSub,
      href: "/backtest",
      color: "text-[#d1d4dc]",
      hoverBorder: "hover:border-[#2962ff]",
    },
    {
      label: t.dashboard.runningLive,
      value:
        liveRunningJobs === undefined && liveRunningLoading
          ? null
          : (liveRunningJobs?.length ?? 0),
      sub: liveSub,
      href: "/live",
      color: "text-[#26a69a]",
      hoverBorder: "hover:border-[#26a69a]",
    },
  ];

  return (
    <div className="w-full px-4 py-4">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-[#d1d4dc]">{t.dashboard.title}</h1>
        <p className="mt-1 max-w-xl text-sm text-[#868993]">{t.dashboard.subtitle}</p>
      </header>

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

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        {stats.map((s) => (
          <Link
            key={s.href}
            href={s.href}
            className={`block rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4 transition-colors ${s.hoverBorder}`}
          >
            <div className="text-xs text-[#868993]">{s.label}</div>
            <div className={`mt-1 flex min-h-[2rem] items-center text-2xl font-semibold ${s.color}`}>
              {s.value === null ? <LoadingSpinner size="md" /> : s.value}
            </div>
            {s.sub && (
              <div className="mt-1 truncate text-xs text-[#868993]">{s.sub}</div>
            )}
          </Link>
        ))}
      </div>

      <div className="mt-6">
        <AssetOverviewPanel keysStatus={keysStatus ?? null} />
      </div>
    </div>
  );
}
