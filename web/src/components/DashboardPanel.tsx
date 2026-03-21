"use client";

import Link from "next/link";
import { useMemo } from "react";
import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import { getBinanceKeysStatus, getJobCounts, listJobs, listStrategies } from "@/lib/api";
import { AssetOverviewPanel } from "@/components/AssetOverviewPanel";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { LoadingSpinner } from "@/components/LoadingSpinner";
import { jobDetailPath } from "@/lib/routes";
import type { Job, JobType } from "@/lib/types";

const DASHBOARD_RECENT_LIMIT = 12;
const DASHBOARD_RUNNING_LIMIT = 64;

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "—";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

function jobSymbol(type: JobType, config: Record<string, unknown>): string {
  if (type === "BACKTEST") {
    return typeof config.symbol === "string" && config.symbol.trim() ? config.symbol : "—";
  }
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const first = streams[0];
  if (isRecord(first) && typeof first.symbol === "string" && first.symbol.trim()) {
    return first.symbol;
  }
  return "—";
}

function jobInterval(type: JobType, config: Record<string, unknown>): string {
  if (type === "BACKTEST") {
    return typeof config.interval === "string" && config.interval.trim() ? config.interval : "—";
  }
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const first = streams[0];
  if (isRecord(first) && typeof first.interval === "string" && first.interval.trim()) {
    return first.interval;
  }
  return "—";
}

function mergeRecent(
  backtest: Job[],
  live: Job[],
  limit: number,
): { job: Job; type: JobType }[] {
  const merged = [
    ...backtest.map((job) => ({ job, type: "BACKTEST" as const })),
    ...live.map((job) => ({ job, type: "LIVE" as const })),
  ];
  merged.sort(
    (a, b) => new Date(b.job.created_at).getTime() - new Date(a.job.created_at).getTime(),
  );
  return merged.slice(0, limit);
}

export function DashboardPanel() {
  const { t, locale } = useI18n();

  const { data: strategies, isLoading: strategiesLoading } = useSWR(
    ["dashboard", "strategies"],
    () => listStrategies(),
  );

  const { data: jobCounts, isLoading: jobCountsLoading } = useSWR(
    ["dashboard", "job-counts"],
    () => getJobCounts(),
  );

  const { data: backtestJobs, isLoading: btLoading } = useSWR(
    ["dashboard", "jobs", "BACKTEST", DASHBOARD_RECENT_LIMIT],
    () => listJobs({ type: "BACKTEST", limit: DASHBOARD_RECENT_LIMIT }),
  );

  const { data: liveRecentJobs, isLoading: liveRecentLoading } = useSWR(
    ["dashboard", "jobs", "LIVE", DASHBOARD_RECENT_LIMIT],
    () => listJobs({ type: "LIVE", limit: DASHBOARD_RECENT_LIMIT }),
  );

  const { data: liveRunningJobs, isLoading: liveRunningLoading } = useSWR(
    ["dashboard", "jobs", "LIVE", "RUNNING", DASHBOARD_RUNNING_LIMIT],
    () =>
      listJobs({
        type: "LIVE",
        status: "RUNNING",
        limit: DASHBOARD_RUNNING_LIMIT,
      }),
  );

  const { data: keysStatus, isLoading: keysLoading } = useSWR(
    ["dashboard", "binance-keys"],
    () => getBinanceKeysStatus(),
  );

  const runningLive = liveRunningJobs ?? [];
  const recent = useMemo(
    () => mergeRecent(backtestJobs ?? [], liveRecentJobs ?? [], 6),
    [backtestJobs, liveRecentJobs],
  );

  const runningSectionLoading = liveRunningLoading && liveRunningJobs === undefined;
  const recentSectionLoading =
    (btLoading && backtestJobs === undefined) || (liveRecentLoading && liveRecentJobs === undefined);

  const binanceOk = !!keysStatus?.configured;
  const localeTag = locale === "ko" ? "ko-KR" : "en-US";

  const stats = [
    {
      label: t.dashboard.strategyCount,
      value:
        strategies === undefined && strategiesLoading ? null : (strategies?.length ?? 0),
      href: "/strategies",
      color: "text-[#d1d4dc]",
      hoverBorder: "hover:border-[#2962ff]",
    },
    {
      label: t.dashboard.backtestCount,
      value:
        jobCounts === undefined && jobCountsLoading ? null : (jobCounts?.backtest_total ?? 0),
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
          </Link>
        ))}
      </div>

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-[#d1d4dc]">{t.dashboard.sectionRunning}</h2>
            <Link href="/live" className="text-xs text-[#2962ff] hover:text-[#5b8cff]">
              {t.dashboard.viewLive}
            </Link>
          </div>
          {runningSectionLoading ? (
            <div className="mt-4 flex justify-center py-6">
              <LoadingSpinner size="md" />
            </div>
          ) : runningLive.length === 0 ? (
            <p className="mt-3 text-sm text-[#868993]">{t.dashboard.sectionRunningEmpty}</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {runningLive.slice(0, 5).map((job) => {
                const cfg = (job.config ?? {}) as Record<string, unknown>;
                const sym = jobSymbol("LIVE", cfg);
                const iv = jobInterval("LIVE", cfg);
                return (
                  <li key={job.job_id}>
                    <Link
                      href={jobDetailPath("LIVE", job.job_id)}
                      className="block rounded-md border border-[#2a2e39] bg-[#131722] px-3 py-2.5 transition-colors hover:border-[#2962ff]/40"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-[#d1d4dc]">
                          {strategyNameFromPath(job.strategy_path)}
                        </span>
                        <span className="shrink-0 text-xs text-[#26a69a]">{t.status.running}</span>
                      </div>
                      <div className="mt-0.5 text-xs text-[#868993]">
                        {sym}
                        {iv !== "—" ? ` · ${iv}` : ""}
                      </div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-4">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-[#d1d4dc]">{t.dashboard.sectionRecent}</h2>
            <div className="flex flex-wrap justify-end gap-x-3 gap-y-1 text-xs">
              <Link href="/backtest" className="text-[#2962ff] hover:text-[#5b8cff]">
                {t.dashboard.viewBacktest}
              </Link>
              <Link href="/live" className="text-[#2962ff] hover:text-[#5b8cff]">
                {t.dashboard.viewLive}
              </Link>
            </div>
          </div>
          {recentSectionLoading ? (
            <div className="mt-4 flex justify-center py-6">
              <LoadingSpinner size="md" />
            </div>
          ) : recent.length === 0 ? (
            <p className="mt-3 text-sm text-[#868993]">{t.dashboard.sectionRecentEmpty}</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {recent.map(({ job, type }) => {
                const cfg = (job.config ?? {}) as Record<string, unknown>;
                const sym = jobSymbol(type, cfg);
                const iv = jobInterval(type, cfg);
                const when = new Date(job.created_at).toLocaleString(localeTag, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                });
                return (
                  <li key={`${type}-${job.job_id}`}>
                    <Link
                      href={jobDetailPath(type, job.job_id)}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[#2a2e39] bg-[#131722] px-3 py-2 transition-colors hover:border-[#2962ff]/40"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-[11px] font-medium uppercase tracking-wide text-[#868993]">
                            {type === "BACKTEST" ? t.dashboard.jobTypeSimulation : t.dashboard.jobTypeLive}
                          </span>
                          <span className="truncate text-sm text-[#d1d4dc]">
                            {strategyNameFromPath(job.strategy_path)}
                          </span>
                        </div>
                        <div className="mt-0.5 text-xs text-[#868993]">
                          {when}
                          {sym !== "—" ? ` · ${sym}` : ""}
                          {iv !== "—" ? ` · ${iv}` : ""}
                        </div>
                      </div>
                      <JobStatusBadge status={job.status} />
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      <div className="mt-8">
        <AssetOverviewPanel keysStatus={keysStatus ?? null} />
      </div>
    </div>
  );
}
