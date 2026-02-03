"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { getJob, listTrades, stopJob } from "@/lib/api";
import type { Job, JobStatus, JobType, Trade } from "@/lib/types";
import { jobDetailPath } from "@/lib/routes";
import { JobResultSummary, isRecord } from "@/components/JobResultSummary";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { JobEventsConsole } from "@/app/jobs/[jobId]/JobEventsConsole";
import { TradeAnalysis } from "@/components/TradeAnalysis";
import { JobProgressGauge } from "@/components/JobProgressGauge";

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

export function JobDetailPage({ expectedType }: { expectedType?: JobType }) {
  const params = useParams<{ jobId?: string | string[] }>();
  const raw = params?.jobId;
  const jobId = Array.isArray(raw) ? raw[0] : raw;
  const validJobId = typeof jobId === "string" && isUuid(jobId);
  const [job, setJob] = useState<Job | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!validJobId || !jobId) return;
    getJob(jobId)
      .then(setJob)
      .catch((e) => setError(String(e)));
  }, [jobId, validJobId]);

  useEffect(() => {
    if (!validJobId || !jobId) return;
    const tick = () => {
      listTrades(jobId).then(setTrades).catch(() => {});
      getJob(jobId).then(setJob).catch(() => {});
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, [jobId, validJobId]);

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

  return (
    <main className="w-full px-6 py-10">
      {error ? (
        <p className="mb-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}

      {!validJobId ? (
        <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-4 text-sm text-[#d1d4dc]">
          Invalid job id: <span className="font-mono text-[#868993]">{String(jobId)}</span>
        </div>
      ) : null}

      {mismatchedType && job ? (
        <div className="mb-4 rounded border border-[#f9a825]/30 bg-[#2d2414]/50 px-4 py-3 text-sm text-[#f9a825]">
          This job is <strong>{job.type}</strong>.{" "}
          <Link className="text-[#2962ff] hover:underline" href={jobDetailPath(job.type, job.job_id)}>
            Open correct page
          </Link>
          .
        </div>
      ) : null}

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-[#d1d4dc]">
            {job?.type === "BACKTEST" ? "Backtest Job" : job?.type === "LIVE" ? "Live Job" : "Job Details"}
          </h1>
          <div className="mt-1 font-mono text-sm text-[#868993]">{jobId}</div>
          {job ? (
            <div className="mt-4 space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">Status:</span>
                <JobStatusBadge status={job.status} />
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">Strategy:</span>
                <span className="text-[#d1d4dc]">{job.strategy_path}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">Created:</span>
                <span className="text-[#d1d4dc]">{formatDateTime(job.created_at)}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-[#868993]">Ended:</span>
                <span className="text-[#d1d4dc]">{formatDateTime(job.ended_at)}</span>
              </div>
            </div>
          ) : null}
        </div>
        <button
          className="rounded border border-[#ef5350] bg-[#ef5350] px-4 py-2 text-sm text-white hover:bg-[#d32f2f] hover:border-[#d32f2f] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          onClick={onStop}
          disabled={!validJobId || !job || (job.status !== "RUNNING" && job.status !== "PENDING")}
        >
          Stop
        </button>
      </div>

      {job && !finished ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
          Run in progress. Results will appear here once it finishes.
        </div>
      ) : null}

      {job ? <JobProgressGauge jobId={job.job_id} jobType={job.type} status={job.status} /> : null}

      {(job?.type === "BACKTEST" && finished && job.result && isRecord(job.result)) ||
      (job?.type === "LIVE" && (trades.length > 0 || (finished && job.result && isRecord(job.result)))) ? (
        <section className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4">
          <div className="mb-2 text-sm font-medium text-[#d1d4dc]">Trade Result Summary</div>
          <JobResultSummary
            type={job!.type}
            result={job!.result && isRecord(job!.result) ? job!.result : {}}
            liveTrades={job!.type === "LIVE" ? trades : undefined}
          />
        </section>
      ) : null}

      {job && finished && job.result && !isRecord(job.result) ? (
        <div className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4 text-xs">
          <div className="mb-2 font-medium text-[#d1d4dc]">Result</div>
          <pre className="overflow-auto text-[#868993]">{JSON.stringify(job.result, null, 2)}</pre>
        </div>
      ) : null}

      {job && finished && !job.result && !(job.type === "LIVE" && trades.length > 0) ? (
        <div className="mt-6 rounded border border-[#2a2e39] bg-[#1e222d] p-4 text-xs text-[#868993]">
          No result payload found for this run.
        </div>
      ) : null}

      {job?.result ? (
        <details className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
          <summary className="cursor-pointer text-xs text-[#868993]">Raw result payload</summary>
          <pre className="mt-3 max-h-[240px] overflow-auto text-xs text-[#d1d4dc]">
            {JSON.stringify(job.result, null, 2)}
          </pre>
        </details>
      ) : null}

      {job ? <TradeAnalysis job={job} liveTrades={trades} /> : null}

      {job?.type === "LIVE" && validJobId && jobId ? (
        <div className="mt-6">
          <JobEventsConsole jobId={jobId} />
        </div>
      ) : null}
    </main>
  );
}
