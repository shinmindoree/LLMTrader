"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { deleteAllJobs, deleteJob, getBillingStatus, getBinanceKeysStatus, listJobs, listStrategies, stopAllJobs, stopJob } from "@/lib/api";
import type { BillingStatus, BinanceKeysStatus, Job, JobStatus, StrategyInfo } from "@/lib/types";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { LatestJobResult } from "@/components/LatestJobResult";
import { JobConfigInline } from "@/components/JobConfigSummary";
import { jobDetailPath } from "@/lib/routes";
import { LiveForm } from "./new/LiveForm";

const MAX_SLOTS_FALLBACK = 5;
const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);
const ACTIVE_STATUSES = new Set<JobStatus>(["PENDING", "RUNNING", "STOP_REQUESTED"]);

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
  const [keysStatus, setKeysStatus] = useState<BinanceKeysStatus | null>(null);
  const [billing, setBilling] = useState<BillingStatus | null>(null);
  const [runPending, setRunPending] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const data = await listJobs({ type: "LIVE", limit: 200 });
      setItems(data);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    getBinanceKeysStatus().then(setKeysStatus).catch(() => {});
    getBillingStatus().then(setBilling).catch(() => {});
  }, [refresh]);

  const activeJobs = useMemo(() => items.filter((j) => ACTIVE_STATUSES.has(j.status)), [items]);
  const activeCount = activeJobs.length;
  const maxSlots = billing?.limits?.max_live_jobs ?? MAX_SLOTS_FALLBACK;

  useEffect(() => {
    if (activeCount === 0) return;
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, [activeCount, refresh]);

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

  const onStopJob = async (job: Job) => {
    if (busy) return;
    try {
      setBusy(true);
      setError(null);
      await stopJob(job.job_id);
      setNotice("Stop requested.");
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
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

  const keysNotConfigured = keysStatus !== null && !keysStatus.configured;

  return (
    <main className="w-full px-6 py-10">
      {keysNotConfigured && (
        <div className="mb-4 rounded-lg border border-[#efb74d]/40 bg-[#2d2718] px-4 py-3 text-sm text-[#efb74d]">
          Binance API keys are not configured. Live trading requires your own API keys.{" "}
          <a className="underline hover:text-[#d1d4dc] transition-colors" href="/settings">
            Go to Settings
          </a>
        </div>
      )}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-[#d1d4dc]">Live</h1>
          <p className="mt-1 text-xs text-[#868993]">
            Run up to {maxSlots} strategies simultaneously. Each strategy runs as an independent job.
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

      {/* Active strategies panel */}
      {activeCount > 0 && (
        <section className="mt-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-[#d1d4dc]">
            Active Strategies
            <span className="rounded bg-[#2962ff]/20 px-2 py-0.5 text-xs text-[#2962ff]">
              {activeCount}/{maxSlots}
            </span>
          </div>
          <ul className="space-y-2">
            {activeJobs.map((j) => (
              <li
                key={j.job_id}
                className="rounded border border-[#2962ff]/30 bg-[#1a2340]/50 px-4 py-3"
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <Link
                      className="font-medium text-[#d1d4dc] hover:text-[#2962ff] hover:underline transition-colors"
                      href={jobDetailPath("LIVE", j.job_id)}
                    >
                      {strategyNameFromPath(j.strategy_path)}
                    </Link>
                    {j.config ? (
                      <span className="text-xs"><JobConfigInline type="LIVE" config={j.config} /></span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <JobStatusBadge status={j.status} />
                    {(j.status === "PENDING" || j.status === "RUNNING") && (
                      <button
                        className="rounded border border-[#2a2e39] px-2 py-1 text-xs text-[#d1d4dc] hover:border-[#ef5350] hover:text-[#ef5350] disabled:opacity-50 transition-colors"
                        disabled={busy}
                        onClick={() => onStopJob(j)}
                        type="button"
                      >
                        Stop
                      </button>
                    )}
                  </div>
                </div>
                <div className="mt-1 text-xs text-[#868993]">
                  Started {new Date(j.created_at).toLocaleString()}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

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
        <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[#d1d4dc]">
          Add Strategy
          {activeCount > 0 && (
            <span className="text-xs font-normal text-[#868993]">
              ({activeCount}/{maxSlots} slots used)
            </span>
          )}
        </div>
        <p className="mb-3 text-xs text-[#efb6b2]">
          Each strategy runs independently with its own symbol and settings. You can run up to {maxSlots} at once.
        </p>
        {strategies.length ? (
          <LiveForm
            strategies={strategies}
            onCreated={onCreated}
            onSubmittingChange={setRunPending}
            activeCount={activeCount}
            maxSlots={maxSlots}
          />
        ) : (
          <div className="text-sm text-[#868993]">Loading…</div>
        )}
      </section>

      <LatestJobResult
        jobType="LIVE"
        focusJobId={latestJob?.job_id ?? null}
        title="Latest Live Result"
        showPendingSpinner={runPending}
      />

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
            {items.filter((j) => !ACTIVE_STATUSES.has(j.status)).map((j) => (
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
                <div className="mt-1 text-xs">
                  <span className="text-[#868993]">{new Date(j.created_at).toLocaleString()}</span>
                  {j.config ? (
                    <span className="ml-2 text-xs"><JobConfigInline type="LIVE" config={j.config} /></span>
                  ) : null}
                  {j.error ? <span className="text-[#868993]"> · error: {j.error}</span> : ""}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
