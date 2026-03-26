"use client";

import { useMemo } from "react";

import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import { getJob, listJobSummaries, listTrades } from "@/lib/api";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { Job, JobStatus, JobType, Trade } from "@/lib/types";
import { JobResultSummary, isRecord } from "@/components/JobResultSummary";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { JobProgressGauge } from "@/components/JobProgressGauge";
import { TradeAnalysis } from "@/components/TradeAnalysis";
import { JobConfigSummary } from "@/components/JobConfigSummary";
import { LoadingSpinner } from "@/components/LoadingSpinner";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);

const formatDateTime = (value: string | null): string => {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
};

const strategyNameFromPath = (path: string): string => {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
};

type LatestJobResultProps = {
  jobType: JobType;
  focusJobId?: string | null;
  title: string;
  showPendingSpinner?: boolean;
};

async function fetchLatestJob(jobType: JobType, focusJobId?: string | null): Promise<Job | null> {
  if (focusJobId) {
    return getJob(focusJobId);
  }
  const summaries = await listJobSummaries({ type: jobType, limit: 1 });
  if (summaries.length > 0) {
    return getJob(summaries[0].job_id);
  }
  return null;
}

export function LatestJobResult({ jobType, focusJobId, title, showPendingSpinner }: LatestJobResultProps) {
  const { t } = useI18n();
  const isVisible = usePageVisibility();

  const jobFinished = (j: Job | null | undefined) => j != null && FINISHED_STATUSES.has(j.status);

  const { data: job = null, error, isLoading: loading } = useSWR(
    ["latestJob", jobType, focusJobId ?? "latest"],
    () => fetchLatestJob(jobType, focusJobId),
    {
      refreshInterval: (latestData: Job | null | undefined) => {
        if (!isVisible) return 15_000;
        if (jobFinished(latestData)) return 0;
        if (latestData == null) return 12_000;
        return 5_000;
      },
      dedupingInterval: 3_000,
    },
  );

  const isLiveActive = job != null && jobType === "LIVE" && !FINISHED_STATUSES.has(job.status);
  const { data: trades = [] } = useSWR<Trade[]>(
    isLiveActive ? ["trades", job.job_id] : null,
    () => listTrades(job!.job_id),
    {
      refreshInterval: isVisible ? 10_000 : 30_000,
      dedupingInterval: 5_000,
    },
  );

  const errorStr = error ? String(error) : null;
  const finished = useMemo(() => (job ? FINISHED_STATUSES.has(job.status) : false), [job]);
  const hasLiveTrades = jobType === "LIVE" && trades.length > 0;
  const hasTrades = useMemo(
    () =>
      job
        ? (job.type === "BACKTEST" &&
            Array.isArray((job.result as Record<string, unknown>)?.trades) &&
            ((job.result as Record<string, unknown>).trades as unknown[]).length > 0) ||
          (job.type === "LIVE" && trades.length > 0)
        : false,
    [job, trades],
  );
  const showPlaceholderGauge =
    showPendingSpinner ||
    (!!focusJobId && ((loading && !job) || (job != null && job.job_id !== focusJobId)));

  return (
    <section className="mt-10">
      <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-[#d1d4dc]">{title}</div>
            {!showPlaceholderGauge && job ? (
              <div className="mt-1 text-xs text-[#868993]">{strategyNameFromPath(job.strategy_path)}</div>
            ) : null}
          </div>
          {!showPlaceholderGauge && job ? <JobStatusBadge status={job.status} /> : null}
        </div>

        {showPlaceholderGauge ? (
          <section className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
            <div className="flex items-center justify-between text-xs text-[#868993]">
              <span>{t.progress.progress}</span>
              <span>{t.progress.preparing}</span>
            </div>
            <div className="mt-3">
              <div className="mb-1 flex items-center justify-between text-xs text-[#d1d4dc]">
                <span>{t.progress.progress}</span>
                <span className="text-[#868993]">0%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-[#0f141f]">
                <div
                  className="h-full rounded-full bg-[#2962ff] transition-[width] duration-200 ease-out"
                  style={{ width: "0%" }}
                />
              </div>
            </div>
          </section>
        ) : null}

        {loading && !job && !showPlaceholderGauge ? (
          <div className="mt-4 flex items-center gap-2.5 text-sm text-[#868993]">
            <LoadingSpinner size="sm" />
            <span>{t.latestResult.loadingLatest}</span>
          </div>
        ) : null}

        {!showPlaceholderGauge && errorStr ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {errorStr}
          </p>
        ) : null}

        {!showPlaceholderGauge && !job && !loading && !error ? (
          <div className="mt-4 text-sm text-[#868993]">{t.latestResult.noRuns}</div>
        ) : null}

        {!showPlaceholderGauge && job ? (
          <JobConfigSummary type={job.type} config={job.config} />
        ) : null}

        {!showPlaceholderGauge && job ? (
          <div className="mt-3 text-xs text-[#868993]">
            <span>{t.jobDetail.created} {formatDateTime(job.created_at)}</span>
            <span className="mx-2">•</span>
            <span>{t.jobDetail.ended} {formatDateTime(job.ended_at)}</span>
          </div>
        ) : null}

        {!showPlaceholderGauge && job?.error ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {job.error}
          </p>
        ) : null}

        {!showPlaceholderGauge && job && !finished && !hasLiveTrades ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            {t.latestResult.runInProgress}
          </div>
        ) : null}

        {!showPlaceholderGauge && job ? <JobProgressGauge jobId={job.job_id} jobType={job.type} status={job.status} /> : null}

        {!showPlaceholderGauge &&
        ((job?.type === "BACKTEST" && finished && job.result && isRecord(job.result)) ||
        (job?.type === "LIVE" && (hasLiveTrades || (finished && job.result && isRecord(job.result))))) &&
        !hasTrades ? (
          <JobResultSummary
            type={job!.type}
            result={job!.result && isRecord(job!.result) ? job!.result : {}}
            liveTrades={job!.type === "LIVE" ? trades : undefined}
          />
        ) : null}

        {!showPlaceholderGauge && job && finished && job.type === "BACKTEST" ? (
          <TradeAnalysis job={job} liveTrades={[]} />
        ) : null}

        {!showPlaceholderGauge && job && job.type === "LIVE" ? (
          <TradeAnalysis job={job} liveTrades={trades} />
        ) : null}

        {!showPlaceholderGauge && job && finished && job.result && !isRecord(job.result) ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            {t.tradeAnalysis.noResultStructured}
          </div>
        ) : null}

        {!showPlaceholderGauge && job && finished && !job.result && !hasLiveTrades ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            {t.tradeAnalysis.noResultFound}
          </div>
        ) : null}

      </div>
    </section>
  );
}
