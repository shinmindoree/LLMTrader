"use client";

import { useCallback, useState } from "react";

import { useRouter } from "next/navigation";
import useSWR from "swr";
import { deleteAllJobs, deleteJob, listJobSummaries, listStrategies, stopAllJobs } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type { Job, JobStatus, JobSummary, StrategyInfo } from "@/lib/types";
import { RunHistoryTable } from "@/components/RunHistoryTable";
import { RunHistoryTableSkeleton } from "@/components/skeletons/RunHistoryTableSkeleton";
import { InlineLoadingIndicator } from "@/components/InlineLoadingIndicator";
import { FormModal } from "@/components/FormModal";
import { BacktestForm } from "./new/BacktestForm";

const FINISHED_STATUSES = new Set<JobStatus>(["SUCCEEDED", "FAILED", "STOPPED"]);
const ACTIVE_STATUSES = new Set<JobStatus>(["PENDING", "RUNNING", "STOP_REQUESTED"]);

export default function BacktestJobsPage() {
  const { t } = useI18n();
  const router = useRouter();
  const isVisible = usePageVisibility();
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [, setRunPending] = useState(false);
  const [formOpen, setFormOpen] = useState(false);

  const { data: strategies = [], error: strategyError } = useSWR<StrategyInfo[]>(
    "strategies",
    () => listStrategies(),
  );

  const hasActiveJobs = (items: JobSummary[]) => items.some((j) => ACTIVE_STATUSES.has(j.status));

  const { data: items = [], error, isLoading: itemsLoading, mutate: refreshItems } = useSWR<JobSummary[]>(
    ["jobSummaries", "BACKTEST"],
    () => listJobSummaries({ type: "BACKTEST", limit: 50 }),
    {
      refreshInterval: (latestData: JobSummary[] | undefined) => {
        if (!latestData || !hasActiveJobs(latestData)) return 0;
        return isVisible ? 5_000 : 15_000;
      },
      dedupingInterval: 2_000,
    },
  );

  const refresh = useCallback(() => refreshItems(), [refreshItems]);

  const onCreated = (job: Job) => {
    refreshItems();
    setFormOpen(false);
    router.push(`/backtest/jobs/${job.job_id}`);
  };

  const onDeleteJob = async (job: Job | JobSummary) => {
    if (busy) return;
    if (!FINISHED_STATUSES.has(job.status)) {
      setActionError(t.backtest.onlyFinishedDelete);
      return;
    }
    const ok = confirm(t.backtest.deleteConfirm);
    if (!ok) return;

    try {
      setBusy(true);
      setActionError(null);
      setNotice(null);
      await deleteJob(job.job_id);
      setNotice(t.backtest.runDeleted);
      await refreshItems();
    } catch (e) {
      setActionError(String(e));
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
      setActionError(null);
      setNotice(null);
      const res = await deleteAllJobs("BACKTEST");
      setNotice(`Done: deleted=${res.deleted}, skipped_active=${res.skipped_active}`);
      await refreshItems();
    } catch (e) {
      setActionError(String(e));
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
      setActionError(null);
      setNotice(null);
      const res = await stopAllJobs("BACKTEST");
      setNotice(
        `Stop requested: stopped_queued=${res.stopped_queued}, stop_requested_running=${res.stop_requested_running}`,
      );
      await refreshItems();
    } catch (e) {
      setActionError(String(e));
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
          {String(strategyError)}
        </p>
      ) : null}

      <section className="mt-4">
        <button
          type="button"
          onClick={() => setFormOpen(true)}
          className="w-full rounded-lg border-2 border-dashed border-[#2a2e39] bg-[#1e222d]/50 py-5 text-sm transition-colors hover:border-[#2962ff] hover:text-[#2962ff]"
        >
          <span className="flex items-center justify-center gap-2 text-[#868993]">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="M8 3v10M3 8h10" /></svg>
            {t.backtest.newBacktest}
          </span>
        </button>
      </section>

      <FormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        title={t.backtest.newBacktest}
      >
        <p className="mb-3 text-xs text-[#868993]">{t.backtest.newBacktestDesc}</p>
        {strategies.length ? (
          <BacktestForm
            strategies={strategies}
            onCreated={onCreated}
            onSubmittingChange={setRunPending}
            onClose={() => setFormOpen(false)}
          />
        ) : (
          <InlineLoadingIndicator message={t.common.loading} />
        )}
      </FormModal>

      <section className="mt-6">
        <div className="mb-3 text-sm font-medium text-[#d1d4dc]">{t.backtest.runHistory}</div>
        {(actionError || error) ? (
          <p className="mb-4 text-sm text-[#ef5350] rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3">
            {actionError || String(error)}
          </p>
        ) : null}

        {itemsLoading ? (
          <RunHistoryTableSkeleton />
        ) : items.length === 0 && !error && !actionError ? (
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
