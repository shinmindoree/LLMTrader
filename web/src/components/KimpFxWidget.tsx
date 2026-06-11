"use client";

import useSWR from "swr";
import { useI18n } from "@/lib/i18n";
import { getKimpFx } from "@/lib/api";
import type { KimpFxRateResponse } from "@/lib/types";

const REFRESH_MS = 60_000;

function fmtTime(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString();
}

type Props = {
  rate?: KimpFxRateResponse | null;
  onRefresh?: () => void;
};

export default function KimpFxWidget({ rate, onRefresh }: Props) {
  const { t } = useI18n();
  const w = t.hubs.arbitrage.kimp.fxWidget;

  const { data, error, isLoading, mutate, isValidating } = useSWR(
    rate ? null : "kimp:fx",
    () => getKimpFx(false),
    { refreshInterval: REFRESH_MS, revalidateOnFocus: false },
  );
  const display = rate ?? data;

  return (
    <div className="rounded-2xl border border-[#26272d] bg-[#13141a] p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-xs uppercase tracking-wider text-[#868993]">
            {w.title}
          </div>
          <div className="mt-1 text-2xl font-semibold tabular-nums text-white">
            {display
              ? `₩${display.rate.toLocaleString("en-US", {
                  minimumFractionDigits: 2,
                  maximumFractionDigits: 2,
                })}`
              : isLoading
                ? t.hubs.arbitrage.kimp.common.loading
                : "—"}
          </div>
        </div>
        <button
          type="button"
          onClick={() => {
            if (onRefresh) {
              onRefresh();
            } else {
              void mutate();
            }
          }}
          disabled={isValidating}
          className="rounded-md border border-[#26272d] bg-[#1a1b22] px-2.5 py-1 text-xs text-[#c3c5cc] hover:bg-[#22232b] disabled:opacity-50"
        >
          {isValidating ? w.refreshing : w.refresh}
        </button>
      </div>
      <div className="mt-3 flex items-center justify-between text-[11px] text-[#868993]">
        <div>
          {w.source}: <span className="text-[#c3c5cc]">{display?.source ?? "—"}</span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
              display?.stale
                ? "bg-amber-500/10 text-amber-400"
                : "bg-emerald-500/10 text-emerald-400"
            }`}
          >
            {display?.stale ? w.stale : w.fresh}
          </span>
          <span>
            {w.fetchedAt}: {fmtTime(display?.fetched_at)}
          </span>
        </div>
      </div>
      {error ? (
        <div className="mt-2 text-[11px] text-rose-400">
          {t.hubs.arbitrage.kimp.common.loadFailed}
        </div>
      ) : null}
    </div>
  );
}
