"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { useI18n } from "@/lib/i18n";
import { getJob, listJobs, listTrades } from "@/lib/api";
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

export function LatestJobResult({ jobType, focusJobId, title, showPendingSpinner }: LatestJobResultProps) {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
  const [job, setJob] = useState<Job | null>(null);
  const jobRef = useRef<Job | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    jobRef.current = job;
  }, [job]);

  useEffect(() => {
    let active = true;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;

    if (focusJobId) setLoading(true);

    const load = async (): Promise<Job | null> => {
      try {
        setError(null);
        const data = focusJobId
          ? await getJob(focusJobId)
          : (await listJobs({ type: jobType }))[0] ?? null;
        if (!active) return null;
        setJob(data);
        return data;
      } catch (e) {
        if (!active) return null;
        setError(String(e));
        return null;
      } finally {
        if (active) setLoading(false);
      }
    };

    const loop = async () => {
      const data = await load();
      if (!active) return;
      const terminal = data != null && FINISHED_STATUSES.has(data.status);
      if (terminal) return;
      const noJob = data == null;
      const ms = !isVisible ? 15_000 : noJob ? 12_000 : 3_000;
      timeoutId = setTimeout(() => void loop(), ms);
    };

    void loop();

    return () => {
      active = false;
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    };
  }, [focusJobId, jobType, isVisible]);

  /* Poll trades only for active LIVE jobs; use job_id/status in deps so we do not restart on every job object update from the main poll. */
  useEffect(() => {
    if (!job || jobType !== "LIVE") return;
    if (FINISHED_STATUSES.has(job.status)) return;
    let active = true;
    let timeoutId: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      try {
        const data = await listTrades(job.job_id);
        if (active) setTrades(data);
      } catch {
        // ignore
      }
      if (!active) return;
      const j = jobRef.current;
      if (j && FINISHED_STATUSES.has(j.status)) return;
      const ms = !isVisible ? 12_000 : 3_000;
      timeoutId = setTimeout(() => void tick(), ms);
    };

    void tick();

    return () => {
      active = false;
      if (timeoutId !== undefined) clearTimeout(timeoutId);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- jobRef carries latest job inside the timer; avoid restarting when unrelated job fields change
  }, [job?.job_id, jobType, job?.status, isVisible]);

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

        {!showPlaceholderGauge && error ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {error}
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
