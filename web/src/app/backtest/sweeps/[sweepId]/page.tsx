"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import useSWR from "swr";
import { getSweep, listStrategies, stopSweep } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import { jobDetailPath, sweepDetailPath } from "@/lib/routes";
import { JobStatusBadge } from "@/components/JobStatusBadge";
import { FormModal } from "@/components/FormModal";
import { BacktestForm, type BacktestInitialConfig } from "@/app/backtest/new/BacktestForm";
import type { JobStatus, StrategyInfo, SweepDetailResponse } from "@/lib/types";

const ACTIVE_STATUSES = new Set<JobStatus>(["PENDING", "RUNNING", "STOP_REQUESTED"]);
const PCT_PATHS = new Set(["stop_loss_pct", "max_position"]);

type SortKey = "returnPct" | "netProfit" | "winRate" | "trades" | "maxDrawdown";
type SortOrder = "asc" | "desc";

function SortIcon({ active, order }: { active: boolean; order: SortOrder }) {
  if (!active) {
    return <span className="ml-1 inline-block text-[#868993] opacity-50">↕</span>;
  }
  return <span className="ml-1 text-[#2962ff]">{order === "asc" ? "↑" : "↓"}</span>;
}

type RunMetrics = {
  returnPct: number | null;
  netProfit: number | null;
  winRate: number | null;
  trades: number | null;
  maxDrawdown: number | null;
};

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function formatDateFromTs(ms: number): string {
  const d = new Date(ms);
  const yyyy = String(d.getFullYear());
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function buildInitialConfig(
  strategyPath: string,
  c: Record<string, unknown>,
): BacktestInitialConfig {
  const slPct = Number(c.stop_loss_pct ?? 0);
  return {
    strategyPath,
    symbol: String(c.symbol ?? "BTCUSDT"),
    interval: String(c.interval ?? "1h"),
    leverage: Number(c.leverage ?? 1),
    initialBalance: Number(c.initial_balance ?? 1000),
    commission: Number(c.commission ?? 0.0004),
    slippageBps: Number(c.slippage_bps ?? 0),
    stopLossPct: slPct > 0 ? slPct : 0.05,
    stopLossEnabled: slPct > 0,
    maxPosition: c.max_position != null ? Number(c.max_position) : undefined,
    maxPyramidEntries: Number(c.max_pyramid_entries ?? 0),
    fixedNotional: c.fixed_notional != null ? Number(c.fixed_notional) : null,
    startDate: typeof c.start_ts === "number" ? formatDateFromTs(c.start_ts) : undefined,
    endDate: typeof c.end_ts === "number" ? formatDateFromTs(c.end_ts) : undefined,
    strategyParams:
      typeof c.strategy_params === "object" && c.strategy_params != null
        ? (c.strategy_params as Record<string, unknown>)
        : undefined,
  };
}

function runMetrics(summary: Record<string, unknown> | null): RunMetrics {
  if (!summary) {
    return { returnPct: null, netProfit: null, winRate: null, trades: null, maxDrawdown: null };
  }
  const initial = num(summary.initial_balance);
  const final = num(summary.final_balance);
  return {
    returnPct:
      num(summary.total_return_pct) ??
      (initial !== null && initial > 0 && final !== null ? ((final - initial) / initial) * 100 : null),
    netProfit:
      num(summary.net_profit) ?? (initial !== null && final !== null ? final - initial : null),
    winRate: num(summary.win_rate),
    trades: num(summary.total_trades),
    maxDrawdown: num(summary.max_drawdown_pct),
  };
}

function formatNum(value: number | null, digits = 2): string {
  if (value === null) return "-";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatParamValue(path: string, value: unknown): string {
  if (path === "strategy_path" && typeof value === "string") {
    const parts = value.split(/[\\/]/);
    return parts[parts.length - 1] || value;
  }
  if (path === "interval") {
    return String(value ?? "-");
  }
  const n = num(value);
  if (n === null) return String(value ?? "-");
  if (PCT_PATHS.has(path)) {
    return `${(n * 100).toLocaleString(undefined, { maximumFractionDigits: 4 })}%`;
  }
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

export default function SweepDetailPage() {
  const { t } = useI18n();
  const router = useRouter();
  const params = useParams<{ sweepId: string }>();
  const sweepId = String(params?.sweepId ?? "");
  const isVisible = usePageVisibility();
  const [sortKey, setSortKey] = useState<SortKey>("returnPct");
  const [sortOrder, setSortOrder] = useState<SortOrder>("desc");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rerunOpen, setRerunOpen] = useState(false);

  const { data: strategies = [] } = useSWR<StrategyInfo[]>("strategies", () => listStrategies());

  const { data, error, isLoading, mutate } = useSWR<SweepDetailResponse>(
    sweepId ? ["sweep", sweepId] : null,
    () => getSweep(sweepId),
    {
      refreshInterval: (latest: SweepDetailResponse | undefined) => {
        if (!latest) return 0;
        const active = latest.runs.some((r) => ACTIVE_STATUSES.has(r.status));
        if (!active) return 0;
        return isVisible ? 4_000 : 12_000;
      },
      dedupingInterval: 2_000,
    },
  );

  const paths = useMemo(() => (data?.dimensions ?? []).map((d) => d.path), [data]);

  const rows = useMemo(() => {
    if (!data) return [];
    const enriched = data.runs.map((run) => ({
      run,
      metrics: runMetrics(run.result_summary as Record<string, unknown> | null),
    }));
    const value = (m: RunMetrics): number => {
      switch (sortKey) {
        case "returnPct":
          return m.returnPct ?? -Infinity;
        case "netProfit":
          return m.netProfit ?? -Infinity;
        case "winRate":
          return m.winRate ?? -Infinity;
        case "trades":
          return m.trades ?? -Infinity;
        case "maxDrawdown":
          return m.maxDrawdown ?? -Infinity;
      }
    };
    return [...enriched].sort((a, b) => {
      const cmp = value(a.metrics) - value(b.metrics);
      return sortOrder === "asc" ? cmp : -cmp;
    });
  }, [data, sortKey, sortOrder]);

  const completed = data?.runs.filter((r) => !ACTIVE_STATUSES.has(r.status)).length ?? 0;
  const total = data?.total_runs ?? 0;
  const hasActive = data?.runs.some((r) => ACTIVE_STATUSES.has(r.status)) ?? false;

  const onStop = async () => {
    if (busy || !data) return;
    if (!window.confirm(t.sweep.stopConfirm)) return;
    try {
      setBusy(true);
      setActionError(null);
      await stopSweep(sweepId);
      await mutate();
    } catch (e) {
      setActionError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onExportCsv = () => {
    if (!data) return;
    const header = [
      t.sweep.rank,
      t.sweep.status,
      ...paths.map((p) => t.sweep.paramLabels[p as keyof typeof t.sweep.paramLabels] ?? p),
      t.sweep.return,
      t.sweep.netProfit,
      t.sweep.winRate,
      t.sweep.trades,
      t.sweep.maxDrawdown,
    ];
    const lines = rows.map((row, idx) => {
      const m = row.metrics;
      return [
        String(idx + 1),
        String(row.run.status),
        ...paths.map((p) => formatParamValue(p, (row.run.params as Record<string, unknown>)[p])),
        m.returnPct === null ? "" : String(m.returnPct),
        m.netProfit === null ? "" : String(m.netProfit),
        m.winRate === null ? "" : String(m.winRate),
        m.trades === null ? "" : String(m.trades),
        m.maxDrawdown === null ? "" : String(m.maxDrawdown),
      ]
        .map((cell) => `"${String(cell).replace(/"/g, '""')}"`)
        .join(",");
    });
    const csv = [header.map((h) => `"${h}"`).join(","), ...lines].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sweep-${sweepId.slice(0, 8)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (isLoading) {
    return (
      <main className="w-full px-4 py-6">
        <p className="text-sm text-[#868993]">{t.common.loading}</p>
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="w-full px-4 py-6">
        <p className="mb-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error ? String(error) : t.sweep.notFound}
        </p>
        <Link href="/backtest" className="text-sm text-[#2962ff] hover:underline">
          ← {t.sweep.back}
        </Link>
      </main>
    );
  }

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortOrder((o) => (o === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortOrder("desc");
    }
  };

  return (
    <main className="w-full px-4 py-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/backtest" className="text-xs text-[#2962ff] hover:underline">
            ← {t.sweep.back}
          </Link>
          <h1 className="mt-1 text-lg font-semibold text-[#d1d4dc]">{t.sweep.detailTitle}</h1>
          <p className="mt-0.5 text-xs text-[#868993]">
            {data.strategy_path} · {String((data.base_config.symbol as string) ?? "")}{" "}
            {String((data.base_config.interval as string) ?? "")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded bg-[#1e222d] px-3 py-1.5 text-xs text-[#d1d4dc]">
            {t.sweep.progress}: {completed}/{total}
          </span>
          <button
            type="button"
            className="rounded border border-[#2962ff] bg-[#2962ff] px-3 py-1.5 text-xs text-white hover:bg-[#1e53d5] hover:border-[#1e53d5] transition-colors"
            onClick={() => setRerunOpen(true)}
          >
            {t.sweep.reRun}
          </button>
          <button
            type="button"
            className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-1.5 text-xs text-[#d1d4dc] hover:border-[#2962ff] transition-colors disabled:opacity-60"
            onClick={onExportCsv}
          >
            {t.sweep.exportCsv}
          </button>
          {hasActive ? (
            <button
              type="button"
              className="rounded border border-[#2a2e39] bg-[#1e222d] px-3 py-1.5 text-xs text-[#d1d4dc] hover:bg-[#2d1f1f] hover:border-[#ef5350] transition-colors disabled:opacity-60"
              onClick={onStop}
              disabled={busy}
            >
              {t.sweep.stop}
            </button>
          ) : null}
        </div>
      </div>

      {actionError ? (
        <p className="mb-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {actionError}
        </p>
      ) : null}

      <div className="overflow-x-auto rounded border border-[#2a2e39]">
        <table className="w-full text-sm">
          <thead className="bg-[#1e222d] text-xs text-[#868993]">
            <tr>
              <th className="px-3 py-2 text-left font-medium">{t.sweep.rank}</th>
              <th className="px-3 py-2 text-left font-medium">{t.sweep.status}</th>
              {paths.map((p) => (
                <th key={p} className="px-3 py-2 text-left font-medium">
                  {t.sweep.paramLabels[p as keyof typeof t.sweep.paramLabels] ?? p}
                </th>
              ))}
              <th
                className="px-3 py-2 text-right font-medium cursor-pointer select-none hover:text-[#d1d4dc] whitespace-nowrap"
                onClick={() => handleSort("returnPct")}
              >
                {t.sweep.return}
                <SortIcon active={sortKey === "returnPct"} order={sortOrder} />
              </th>
              <th
                className="px-3 py-2 text-right font-medium cursor-pointer select-none hover:text-[#d1d4dc] whitespace-nowrap"
                onClick={() => handleSort("netProfit")}
              >
                {t.sweep.netProfit}
                <SortIcon active={sortKey === "netProfit"} order={sortOrder} />
              </th>
              <th
                className="px-3 py-2 text-right font-medium cursor-pointer select-none hover:text-[#d1d4dc] whitespace-nowrap"
                onClick={() => handleSort("winRate")}
              >
                {t.sweep.winRate}
                <SortIcon active={sortKey === "winRate"} order={sortOrder} />
              </th>
              <th
                className="px-3 py-2 text-right font-medium cursor-pointer select-none hover:text-[#d1d4dc] whitespace-nowrap"
                onClick={() => handleSort("trades")}
              >
                {t.sweep.trades}
                <SortIcon active={sortKey === "trades"} order={sortOrder} />
              </th>
              <th
                className="px-3 py-2 text-right font-medium cursor-pointer select-none hover:text-[#d1d4dc] whitespace-nowrap"
                onClick={() => handleSort("maxDrawdown")}
              >
                {t.sweep.maxDrawdown}
                <SortIcon active={sortKey === "maxDrawdown"} order={sortOrder} />
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => {
              const m = row.metrics;
              const runParams = row.run.params as Record<string, unknown>;
              return (
                <tr
                  key={row.run.job_id}
                  className="cursor-pointer border-t border-[#2a2e39] hover:bg-[#1e222d]"
                  onClick={() => router.push(jobDetailPath("BACKTEST", row.run.job_id))}
                >
                  <td className="px-3 py-2 text-[#868993]">{idx + 1}</td>
                  <td className="px-3 py-2">
                    <JobStatusBadge status={row.run.status} />
                  </td>
                  {paths.map((p) => (
                    <td key={p} className="px-3 py-2 text-[#d1d4dc]">
                      {formatParamValue(p, runParams[p])}
                    </td>
                  ))}
                  <td
                    className={`px-3 py-2 text-right ${
                      m.returnPct === null
                        ? "text-[#868993]"
                        : m.returnPct >= 0
                          ? "text-[#26a69a]"
                          : "text-[#ef5350]"
                    }`}
                  >
                    {m.returnPct === null ? "-" : `${formatNum(m.returnPct)}%`}
                  </td>
                  <td className="px-3 py-2 text-right text-[#d1d4dc]">{formatNum(m.netProfit)}</td>
                  <td className="px-3 py-2 text-right text-[#d1d4dc]">
                    {m.winRate === null ? "-" : `${formatNum(m.winRate, 1)}%`}
                  </td>
                  <td className="px-3 py-2 text-right text-[#d1d4dc]">
                    {m.trades === null ? "-" : m.trades}
                  </td>
                  <td className="px-3 py-2 text-right text-[#ef5350]">
                    {m.maxDrawdown === null ? "-" : `-${formatNum(m.maxDrawdown)}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {data.runs.some((r) => r.status === "FAILED" && r.error) ? (
        <div className="mt-4 rounded border border-[#2a2e39] bg-[#131722] p-3 text-xs text-[#868993]">
          {data.runs
            .filter((r) => r.status === "FAILED" && r.error)
            .map((r) => (
              <p key={r.job_id} className="truncate">
                #{r.index + 1}: {r.error}
              </p>
            ))}
        </div>
      ) : null}

      <FormModal open={rerunOpen} onClose={() => setRerunOpen(false)} title={t.sweep.reRun}>
        <BacktestForm
          strategies={strategies}
          initialConfig={buildInitialConfig(data.strategy_path, data.base_config)}
          initialMode="sweep"
          initialSweepDimensions={data.dimensions}
          onCreatedSweep={(newSweepId) => {
            setRerunOpen(false);
            router.push(sweepDetailPath(newSweepId));
          }}
          onClose={() => setRerunOpen(false)}
        />
      </FormModal>
    </main>
  );
}
