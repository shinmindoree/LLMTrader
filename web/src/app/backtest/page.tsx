"use client";

import { useCallback, useEffect, useState } from "react";

import { deleteAllJobs, deleteJob, listJobs, listStrategies, stopAllJobs } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { Job, JobStatus, StrategyInfo } from "@/lib/types";
import { LatestJobResult } from "@/components/LatestJobResult";
import { RunHistoryTable } from "@/components/RunHistoryTable";
import { BacktestForm } from "./new/BacktestForm";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);
const ACTIVE_STATUSES = new Set<JobStatus>(["PENDING", "RUNNING", "STOP_REQUESTED"]);

export default function BacktestJobsPage() {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
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
    const ms = isVisible ? 2_500 : 12_000;
    const interval = setInterval(refresh, ms);
    return () => clearInterval(interval);
  }, [hasActiveJobs, refresh, isVisible]);

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
          <RunHistoryTable
            items={items}
            type="BACKTEST"
            onDeleteJob={onDeleteJob}
            busy={busy}
            canDeleteJob={(j) => FINISHED_STATUSES.has(j.status)}
          />
        )}
      </section>
    </main>
  );
}
