"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import { getJob, listTrades, stopJob } from "@/lib/api";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { Job, JobStatus, JobType, Trade } from "@/lib/types";
import { jobDetailPath } from "@/lib/routes";
import { JobResultSummary, isRecord } from "@/components/JobResultSummary";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { TradeAnalysis } from "@/components/TradeAnalysis";
import { JobProgressGauge } from "@/components/JobProgressGauge";
import { JobConfigSummary } from "@/components/JobConfigSummary";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function formatDateTime(value: string | null): string {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

function toRunReference(jobId: string): string {
  if (!jobId) return "-";
  return jobId.length > 8 ? `#${jobId.slice(0, 8)}` : `#${jobId}`;
}

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

export function JobDetailPage({ expectedType }: { expectedType?: JobType }) {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
  const params = useParams<{ jobId?: string | string[] }>();
  const raw = params?.jobId;
  const jobId = Array.isArray(raw) ? raw[0] : raw;
  const validJobId = typeof jobId === "string" && isUuid(jobId);
  const [error, setError] = useState<string | null>(null);

  const jobFinished = (j: Job | null | undefined) =>
    j != null && FINISHED_STATUSES.has(j.status);

  const { data: job = null, error: fetchError } = useSWR<Job>(
    validJobId && jobId ? ["job", jobId] : null,
    () => getJob(jobId!),
    {
      refreshInterval: (latestData: Job | null | undefined) => {
        if (jobFinished(latestData)) return 0;
        return isVisible ? 5_000 : 15_000;
      },
      dedupingInterval: 3_000,
    },
  );

  const { data: trades = [] } = useSWR<Trade[]>(
    validJobId && jobId ? ["trades", jobId] : null,
    () => listTrades(jobId!),
    {
      refreshInterval: (latestData: Trade[] | undefined) => {
        if (jobFinished(job)) {
          // For finished jobs: fetch once (if no trades yet), then stop
          if (!latestData || latestData.length === 0) return 5_000;
          return 0;
        }
        return isVisible ? 10_000 : 30_000;
      },
      dedupingInterval: 5_000,
    },
  );

  const displayError = error || (fetchError ? String(fetchError) : null);

  const onStop = async () => {
    try {
      if (!validJobId || !jobId) return;
      await stopJob(jobId);
    } catch (e) {
      setError(String(e));
    }
  };

  const finished = useMemo(() => (job ? FINISHED_STATUSES.has(job.status) : false), [job]);
  const mismatchedType = Boolean(expectedType && job && job.type !== expectedType);
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

  return (
    <main className="w-full px-6 py-10">
      {displayError ? (
        <p className="mb-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {displayError}
        </p>
      ) : null}

      {!validJobId ? (
        <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-4 text-sm text-[#d1d4dc]">
          {t.jobDetail.invalidRunLink} <span className="font-mono text-[#868993]">{String(jobId)}</span>
        </div>
      ) : null}

      {mismatchedType && job ? (
        <div className="mb-4 rounded border border-[#f9a825]/30 bg-[#2d2414]/50 px-4 py-3 text-sm text-[#f9a825]">
          {t.jobDetail.jobTypeMismatch} <strong>{job.type}</strong>.{" "}
          <Link className="text-[#2962ff] hover:underline" href={jobDetailPath(job.type, job.job_id)}>
            {t.jobDetail.openCorrectPage}
          </Link>
          .
        </div>
      ) : null}

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[#d1d4dc]">
            {job?.type === "BACKTEST" ? t.jobDetail.backtestRun : job?.type === "LIVE" ? t.jobDetail.liveRun : t.jobDetail.runDetails}
          </h1>
          <div className="mt-1 font-mono text-sm text-[#868993]">{t.jobDetail.runReference} {toRunReference(jobId ?? "")}</div>
          {job ? (
            <div className="mt-4 space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">{t.jobDetail.status}:</span>
                <JobStatusBadge status={job.status} />
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">{t.jobDetail.strategy}:</span>
                <span className="text-[#d1d4dc]">{strategyNameFromPath(job.strategy_path)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">{t.jobDetail.created}:</span>
                <span className="text-[#d1d4dc]">{formatDateTime(job.created_at)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">{t.jobDetail.ended}:</span>
                <span className="text-[#d1d4dc]">{formatDateTime(job.ended_at)}</span>
              </div>
            </div>
          ) : null}
          {job ? <JobConfigSummary type={job.type} config={job.config} /> : null}
        </div>
        <button
          className="rounded border border-[#ef5350] bg-[#ef5350] px-4 py-2 text-sm text-white hover:bg-[#d32f2f] hover:border-[#d32f2f] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          onClick={onStop}
          disabled={!validJobId || !job || (job.status !== "RUNNING" && job.status !== "PENDING")}
        >
          {t.common.stop}
        </button>
      </div>

      {job && !finished ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
          {t.jobDetail.runInProgress}
        </div>
      ) : null}

      {job ? <JobProgressGauge jobId={job.job_id} jobType={job.type} status={job.status} /> : null}

      {(job?.type === "BACKTEST" && finished && job.result && isRecord(job.result)) ||
      (job?.type === "LIVE" && (trades.length > 0 || (finished && job.result && isRecord(job.result)))) &&
      !hasTrades ? (
        <section className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4">
          <div className="mb-2 text-sm font-medium text-[#d1d4dc]">{t.jobDetail.tradeResultSummary}</div>
          <JobResultSummary
            type={job!.type}
            result={job!.result && isRecord(job!.result) ? job!.result : {}}
            liveTrades={job!.type === "LIVE" ? trades : undefined}
          />
        </section>
      ) : null}

      {job && finished && job.result && !isRecord(job.result) ? (
        <div className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4 text-xs">
          <div className="mb-2 font-medium text-[#d1d4dc]">{t.jobDetail.result}</div>
          <pre className="overflow-auto text-[#868993]">{JSON.stringify(job.result, null, 2)}</pre>
        </div>
      ) : null}

      {job && finished && !job.result && !(job.type === "LIVE" && trades.length > 0) ? (
        <div className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4 text-xs text-[#868993]">
          {t.jobDetail.noResult}
        </div>
      ) : null}

      {job ? <TradeAnalysis job={job} liveTrades={trades} /> : null}
    </main>
  );
}
