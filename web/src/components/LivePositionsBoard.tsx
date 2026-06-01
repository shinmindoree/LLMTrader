"use client";

import { useState } from "react";
import useSWR from "swr";

import { getLivePositions } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import { TimeCell } from "@/components/TimeCell";
import { PositionRow } from "@/components/LivePositionPanel";
import type { LivePositionsResponse, LiveStrategyPositions } from "@/lib/types";

const REFRESH_MS = 15_000;

const fmt = (v: number) =>
  v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtSigned = (v: number) => `${v >= 0 ? "+" : "-"}$${fmt(Math.abs(v))}`;

function SummaryCard({ label, value, color = "text-[#d1d4dc]" }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] p-3">
      <div className="text-xs text-[#868993]">{label}</div>
      <div className={`mt-1 text-lg font-semibold ${color}`}>{value}</div>
    </div>
  );
}

function StrategyCard({
  group,
  t,
}: {
  group: LiveStrategyPositions;
  t: ReturnType<typeof useI18n>["t"];
}) {
  const pnlColor = group.total_unrealized_pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";
  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 shrink-0 rounded-full bg-[#26a69a]" />
            <span className="truncate text-sm font-semibold text-[#d1d4dc]">{group.strategy_name}</span>
          </div>
          <div className="mt-1 text-xs text-[#868993]">
            {t.livePositions.symbolsLabel}: {group.symbols.length ? group.symbols.join(", ") : "-"}
          </div>
        </div>
        <div className="text-right">
          {group.allocated_usdt > 0 && (
            <div className="text-xs text-[#868993]">
              {t.livePositions.allocated} ${fmt(group.allocated_usdt)}
            </div>
          )}
          <div className={`text-sm font-semibold ${pnlColor}`}>{fmtSigned(group.total_unrealized_pnl)}</div>
        </div>
      </div>

      <div className="mt-3">
        {group.positions.length === 0 ? (
          <div className="text-xs text-[#868993]">{t.livePositions.noPositions}</div>
        ) : (
          <div className="space-y-1.5 rounded bg-[#1e222d] px-3 py-2">
            {group.positions.map((p) => (
              <PositionRow key={`${p.symbol}-${p.side}`} position={p} t={t} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function LivePositionsBoard() {
  const { t } = useI18n();
  const isVisible = usePageVisibility();
  const [refreshing, setRefreshing] = useState(false);

  const { data, error, isLoading, mutate } = useSWR<LivePositionsResponse>(
    "livePositions",
    () => getLivePositions(),
    {
      refreshInterval: isVisible ? REFRESH_MS : 0,
      dedupingInterval: 5_000,
      shouldRetryOnError: false,
    },
  );

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await mutate();
    } finally {
      setRefreshing(false);
    }
  };

  const lp = t.livePositions;
  const totals = data?.totals;
  const totalPnlColor = (totals?.total_unrealized_pnl ?? 0) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-[#d1d4dc]">{lp.title}</h3>
          <p className="mt-0.5 text-xs text-[#868993]">{lp.subtitle}</p>
        </div>
        <button
          type="button"
          onClick={() => void handleRefresh()}
          disabled={refreshing || isLoading}
          className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-1.5 text-xs text-[#d1d4dc] hover:border-[#2962ff] hover:bg-[#252936] transition-colors disabled:opacity-60"
        >
          {refreshing ? lp.refreshing : lp.refresh}
        </button>
      </div>

      {data?.as_of && (
        <div className="mt-1 text-xs text-[#868993]">
          {lp.lastUpdated} <TimeCell value={data.as_of} />
        </div>
      )}

      {isLoading && !data && <div className="mt-4 h-32 animate-pulse rounded bg-[#131722]" />}

      {error && (
        <div className="mt-4 rounded border border-[#ef5350]/40 bg-[#2d1f1f] px-4 py-3 text-sm text-[#ef5350]">
          {String(error)}
        </div>
      )}

      {data?.error && (
        <div className="mt-4 rounded border border-[#efb74d]/40 bg-[#2d2718] px-4 py-3 text-sm text-[#efb74d]">
          {data.error}
        </div>
      )}

      {data && !data.error && (
        <>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <SummaryCard label={lp.strategiesLabel} value={String(totals?.strategy_count ?? 0)} />
            <SummaryCard label={lp.openPositionsLabel} value={String(totals?.open_position_count ?? 0)} />
            <SummaryCard label={lp.notionalLabel} value={`$${fmt(totals?.total_notional ?? 0)}`} />
            <SummaryCard
              label={lp.unrealizedLabel}
              value={fmtSigned(totals?.total_unrealized_pnl ?? 0)}
              color={totalPnlColor}
            />
          </div>

          <div className="mt-4 space-y-3">
            {data.strategies.length === 0 ? (
              <div className="rounded border border-[#2a2e39] bg-[#131722] px-4 py-6 text-center text-sm text-[#868993]">
                {lp.noStrategies}
              </div>
            ) : (
              data.strategies.map((group) => (
                <StrategyCard key={group.job_id} group={group} t={t} />
              ))
            )}
          </div>

          {data.unattributed.length > 0 && (
            <div className="mt-4 rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
              <div className="text-sm font-medium text-[#d1d4dc]">{lp.unattributed}</div>
              <div className="mt-0.5 text-xs text-[#868993]">{lp.unattributedHint}</div>
              <div className="mt-3 space-y-1.5 rounded bg-[#1e222d] px-3 py-2">
                {data.unattributed.map((p) => (
                  <PositionRow key={`${p.symbol}-${p.side}`} position={p} t={t} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
