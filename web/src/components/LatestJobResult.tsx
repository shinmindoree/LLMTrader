"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { getJob, listJobs } from "@/lib/api";
import type { Job, JobStatus, JobType } from "@/lib/types";
import { JobResultSummary, isRecord } from "@/components/JobResultSummary";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { jobDetailPath } from "@/lib/routes";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);

const formatDateTime = (value: string | null): string => {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
};

type LatestJobResultProps = {
  jobType: JobType;
  focusJobId?: string | null;
  title: string;
};

export function LatestJobResult({ jobType, focusJobId, title }: LatestJobResultProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;

    const load = async () => {
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

  const finished = useMemo(() => (job ? FINISHED_STATUSES.has(job.status) : false), [job]);

  return (
    <section className="mt-10">
      <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-[#d1d4dc]">{title}</div>
            {job ? (
              <div className="mt-1 text-xs text-[#868993]">{job.strategy_path}</div>
            ) : null}
          </div>
          {job ? <JobStatusBadge status={job.status} /> : null}
        </div>

        {loading && !job ? (
          <div className="mt-4 text-sm text-[#868993]">Loading latest run…</div>
        ) : null}

        {error ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {error}
          </p>
        ) : null}

        {!job && !loading && !error ? (
          <div className="mt-4 text-sm text-[#868993]">No runs yet. Start one to see results here.</div>
        ) : null}

        {job ? (
          <div className="mt-3 text-xs text-[#868993]">
            <span>Created {formatDateTime(job.created_at)}</span>
            <span className="mx-2">•</span>
            <span>Ended {formatDateTime(job.ended_at)}</span>
          </div>
        ) : null}

        {job?.error ? (
          <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
            {job.error}
          </p>
        ) : null}

        {job && !finished ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            Run in progress. Results will appear here once it finishes.
          </div>
        ) : null}

        {job && finished && job.result && isRecord(job.result) ? (
          <JobResultSummary type={job.type} result={job.result} />
        ) : null}

        {job && finished && job.result && !isRecord(job.result) ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            Result payload is not structured for summary. See raw payload below.
          </div>
        ) : null}

        {job && finished && !job.result ? (
          <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3 text-sm text-[#d1d4dc]">
            No result payload found for this run.
          </div>
        ) : null}

        {job ? (
          <div className="mt-4 flex items-center gap-3 text-xs text-[#868993]">
            <Link className="text-[#2962ff] hover:underline" href={jobDetailPath(job.type, job.job_id)}>
              Open run details
            </Link>
            <span className="font-mono">{job.job_id}</span>
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
      </div>
    </section>
  );
}
