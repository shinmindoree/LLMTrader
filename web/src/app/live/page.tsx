"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { deleteAllJobs, deleteJob, listJobs, listStrategies, stopAllJobs } from "@/lib/api";
import type { Job, JobStatus, StrategyInfo } from "@/lib/types";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { LatestJobResult } from "@/components/LatestJobResult";
import { jobDetailPath } from "@/lib/routes";
import { LiveForm } from "./new/LiveForm";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

export default function LiveJobsPage() {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  const [items, setItems] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [latestJob, setLatestJob] = useState<Job | null>(null);

  const refresh = async () => {
    try {
      setError(null);
      const data = await listJobs({ type: "LIVE", limit: 200 });
      setItems(data);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    listStrategies()
      .then(setStrategies)
      .catch((e) => setStrategyError(String(e)));
  }, []);

  const onCreated = (job: Job) => {
    setLatestJob(job);
    setNotice("Live run started.");
    refresh();
  };

  const onDeleteJob = async (job: Job) => {
    if (busy) return;
    if (!FINISHED_STATUSES.has(job.status)) {
      setError("Only completed runs can be deleted.");
      return;
    }
    const ok = confirm(
      "Delete this live run?\n\nThis also removes related events, orders, and trades.",
    );
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      await deleteJob(job.job_id);
      setLatestJob((prev) => (prev?.job_id === job.job_id ? null : prev));
      setNotice("Run deleted.");
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteAll = async () => {
    if (busy) return;
    const ok = confirm(
      "Delete all live runs?\n\n- Only completed runs are deleted.\n- Queued/running/stopping runs are kept.",
    );
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      const res = await deleteAllJobs("LIVE");
      setLatestJob((prev) =>
        prev && FINISHED_STATUSES.has(prev.status) ? null : prev,
      );
      setNotice(`Done: deleted=${res.deleted}, skipped_active=${res.skipped_active}`);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onStopAll = async () => {
    if (busy) return;
    const ok = confirm(
      "Request stop for all live runs?\n\n- Queued runs stop immediately.\n- Running runs move to Stopping.",
    );
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      const res = await stopAllJobs("LIVE");
      setNotice(
        `Stop requested: stopped_queued=${res.stopped_queued}, stop_requested_running=${res.stop_requested_running}`,
      );
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="w-full px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#d1d4dc]">Live</h1>
          <p className="mt-1 text-xs text-[#868993]">
            Live runs use a separate queue from backtests.
          </p>
        </div>
        <div className="flex gap-2 text-sm">
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#252936] hover:border-[#2962ff] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={refresh}
            type="button"
          >
            Refresh
          </button>
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={onDeleteAll}
            type="button"
          >
            Delete All
          </button>
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={onStopAll}
            type="button"
          >
            Stop All
          </button>
        </div>
      </div>

      {notice ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-3 text-sm text-[#d1d4dc]">
          {notice}
          {latestJob ? (
            <>
              {" "}
              <Link
                className="text-[#2962ff] hover:underline"
                href={jobDetailPath("LIVE", latestJob.job_id)}
              >
                Open run details
              </Link>
            </>
          ) : null}
        </div>
      ) : null}

      {strategyError ? (
        <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {strategyError}
        </p>
      ) : null}

      <section className="mt-6">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">New Live Run</div>
        <p className="mb-3 text-xs text-[#efb6b2]">
          Caution: this places real testnet orders. Only one stream is supported in this MVP.
        </p>
        {strategies.length ? (
          <LiveForm strategies={strategies} onCreated={onCreated} />
        ) : (
          <div className="text-sm text-[#868993]">Loading…</div>
        )}
      </section>

      <LatestJobResult jobType="LIVE" focusJobId={latestJob?.job_id ?? null} title="Latest Live Result" />

      <section className="mt-10">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">Run History</div>
        {error ? (
          <p className="mb-4 text-sm text-[#ef5350] rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3">
            {error}
          </p>
        ) : null}

        {items.length === 0 && !error ? (
          <div className="rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-8 text-center text-sm text-[#868993]">
            No live jobs found. Create a new live run to get started.
          </div>
        ) : (
          <ul className="space-y-2">
            {items.map((j) => (
              <li
                key={j.job_id}
                className="rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-3 hover:border-[#2962ff] hover:bg-[#252936] transition-colors"
              >
                <div className="flex items-center justify-between">
                  <Link
                    className="font-medium text-[#d1d4dc] hover:text-[#2962ff] hover:underline transition-colors"
                    href={jobDetailPath("LIVE", j.job_id)}
                  >
                    {strategyNameFromPath(j.strategy_path)}
                  </Link>
                  <div className="flex items-center gap-2">
                    <JobStatusBadge status={j.status} />
                    <button
                      className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] hover:border-[#ef5350] hover:text-[#ef5350] disabled:opacity-50 transition-colors"
                      disabled={busy || !FINISHED_STATUSES.has(j.status)}
                      onClick={() => onDeleteJob(j)}
                      type="button"
                    >
                      Delete
                    </button>
                  </div>
                </div>
                <div className="mt-1 text-xs text-[#868993]">
                  {new Date(j.created_at).toLocaleString()}
                  {j.error ? ` • error: ${j.error}` : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
