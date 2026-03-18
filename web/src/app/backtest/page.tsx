"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { deleteAllJobs, deleteJob, listJobs, listStrategies, stopAllJobs } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import type { Job, JobStatus, StrategyInfo } from "@/lib/types";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { LatestJobResult } from "@/components/LatestJobResult";
import { JobConfigInline } from "@/components/JobConfigSummary";
import { jobDetailPath } from "@/lib/routes";
import { BacktestForm } from "./new/BacktestForm";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);
const ACTIVE_STATUSES = new Set<JobStatus>(["PENDING", "RUNNING", "STOP_REQUESTED"]);

function strategyNameFromPath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return "Strategy";
  const base = trimmed.split("/").pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, "");
}

export default function BacktestJobsPage() {
  const { t } = useI18n();
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategyError, setStrategyError] = useState<string | null>(null);
  const [items, setItems] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [latestJob, setLatestJob] = useState<Job | null>(null);
  const [runPending, setRunPending] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setError(null);
      const data = await listJobs({ type: "BACKTEST", limit: 200 });
      setItems(data);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const hasActiveJobs = items.some((j) => ACTIVE_STATUSES.has(j.status));
  useEffect(() => {
    if (!hasActiveJobs) return;
    const interval = setInterval(refresh, 2000);
    return () => clearInterval(interval);
  }, [hasActiveJobs, refresh]);

  useEffect(() => {
    listStrategies()
      .then(setStrategies)
      .catch((e) => setStrategyError(String(e)));
  }, []);

  const onCreated = (job: Job) => {
    setLatestJob(job);
    setNotice(t.backtest.runStarted);
    refresh();
  };

  const onDeleteJob = async (job: Job) => {
    if (busy) return;
    if (!FINISHED_STATUSES.has(job.status)) {
      setError(t.backtest.onlyFinishedDelete);
      return;
    }
    const ok = confirm(t.backtest.deleteConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      await deleteJob(job.job_id);
      setLatestJob((prev) => (prev?.job_id === job.job_id ? null : prev));
      setNotice(t.backtest.runDeleted);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteAll = async () => {
    if (busy) return;
    const ok = confirm(t.backtest.deleteAllConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      const res = await deleteAllJobs("BACKTEST");
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
    const ok = confirm(t.backtest.stopAllConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setError(null);
      setNotice(null);
      const res = await stopAllJobs("BACKTEST");
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
    <main className="w-full px-4 py-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-[#868993]">
          {t.backtest.queueInfo}
        </p>
        <div className="flex gap-2 text-sm">
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#252936] hover:border-[#2962ff] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={refresh}
            type="button"
          >
            {t.common.refresh}
          </button>
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={onDeleteAll}
            type="button"
          >
            {t.common.deleteAll}
          </button>
          <button
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-2 text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] disabled:opacity-60 transition-colors"
            disabled={busy}
            onClick={onStopAll}
            type="button"
          >
            {t.common.stopAll}
          </button>
        </div>
      </div>

      {notice ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-3 text-sm text-[#d1d4dc]">
          {notice}
        </div>
      ) : null}

      {strategyError ? (
        <p className="mt-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {strategyError}
        </p>
      ) : null}

      <section className="mt-6">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">{t.backtest.newBacktest}</div>
        <p className="mb-3 text-xs text-[#868993]">{t.backtest.newBacktestDesc}</p>
        {strategies.length ? (
          <BacktestForm
            strategies={strategies}
            onCreated={onCreated}
            onSubmittingChange={setRunPending}
          />
        ) : (
          <div className="text-sm text-[#868993]">{t.common.loading}</div>
        )}
      </section>

      <LatestJobResult
        jobType="BACKTEST"
        focusJobId={latestJob?.job_id ?? null}
        title={t.backtest.latestResult}
        showPendingSpinner={runPending}
      />

      <section className="mt-10">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">{t.backtest.runHistory}</div>
        {error ? (
          <p className="mb-4 text-sm text-[#ef5350] rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3">
            {error}
          </p>
        ) : null}

        {items.length === 0 && !error ? (
          <div className="rounded border border-[#2a2e39] bg-[#1e222d] px-4 py-8 text-center text-sm text-[#868993]">
            {t.backtest.emptyState}
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
                    href={jobDetailPath("BACKTEST", j.job_id)}
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
                      {t.common.delete}
                    </button>
                  </div>
                </div>
                <div className="mt-1 text-xs">
                  <span className="text-[#868993]">{new Date(j.created_at).toLocaleString()}</span>
                  {j.config ? (
                    <span className="ml-2 text-xs"><JobConfigInline type="BACKTEST" config={j.config} /></span>
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
