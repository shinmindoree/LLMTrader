"use client";

import { useEffect, useMemo, useState } from "react";

import { getJob, listJobs, listTrades } from "@/lib/api";
import type { Job, JobStatus, JobType, Trade } from "@/lib/types";
import { JobResultSummary, isRecord } from "@/components/JobResultSummary";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { JobProgressGauge } from "@/components/JobProgressGauge";
import { TradeAnalysis } from "@/components/TradeAnalysis";

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
  const [job, setJob] = useState<Job | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    const load = async () => {
      if (focusJobId) setLoading(true);
      try {
        setError(null);
        const data = focusJobId
          ? await getJob(focusJobId)
          : (await listJobs({ type: jobType }))[0] ?? null;
        if (!active) return;
        setJob(data);
      } catch (e) {
        if (!active) return;
        setError(String(e));
      } finally {
        if (!active) return;
        setLoading(false);
      }
    };

    load();
    const timer = setInterval(load, 3000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [focusJobId, jobType]);

  useEffect(() => {
    if (!job || jobType !== "LIVE") return;
    let active = true;

    const fetchTrades = () => {
      listTrades(job.job_id).then((data) => {
        if (active) setTrades(data);
      }).catch(() => {});
    };

    fetchTrades();
    const timer = setInterval(fetchTrades, 3000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [job?.job_id, jobType]);

  const finished = useMemo(() => (job ? FINISHED_STATUSES.has(job.status) : false), [job]);
  const hasLiveTrades = jobType === "LIVE" && trades.length > 0;
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
              <span>Progress</span>
              <span>preparing…</span>
            </div>
            <div className="mt-3">
              <div className="mb-1 flex items-center justify-between text-xs text-[#d1d4dc]">
                <span>Progress</span>
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
          <div className="mt-4 text-sm text-[#868993]">Loading latest run…</div>
        ) : null}

        {!showPlaceholderGauge && error ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {error}
          </p>
        ) : null}

        {!showPlaceholderGauge && !job && !loading && !error ? (
          <div className="mt-4 text-sm text-[#868993]">No runs yet. Start one to see results here.</div>
        ) : null}

        {!showPlaceholderGauge && job ? (
          <div className="mt-3 text-xs text-[#868993]">
            <span>Created {formatDateTime(job.created_at)}</span>
            <span className="mx-2">•</span>
            <span>Ended {formatDateTime(job.ended_at)}</span>
          </div>
        ) : null}

        {!showPlaceholderGauge && job?.error ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {job.error}
          </p>
        ) : null}

        {!showPlaceholderGauge && job && !finished && !hasLiveTrades ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            Run in progress. Results will appear here once it finishes.
          </div>
        ) : null}

        {!showPlaceholderGauge && job ? <JobProgressGauge jobId={job.job_id} jobType={job.type} status={job.status} /> : null}

        {!showPlaceholderGauge &&
        ((job?.type === "BACKTEST" && finished && job.result && isRecord(job.result)) ||
        (job?.type === "LIVE" && (hasLiveTrades || (finished && job.result && isRecord(job.result))))) ? (
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
            Result payload is not structured for summary. See raw payload below.
          </div>
        ) : null}

        {!showPlaceholderGauge && job && finished && !job.result && !hasLiveTrades ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            No result payload found for this run.
          </div>
        ) : null}

        {!showPlaceholderGauge && job?.result ? (
          <details className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
            <summary className="cursor-pointer text-xs text-[#868993]">Technical result payload</summary>
            <pre className="mt-3 max-h-[240px] overflow-auto text-xs text-[#d1d4dc]">
              {JSON.stringify(job.result, null, 2)}
            </pre>
          </details>
        ) : null}
      </div>
    </section>
  );
}
