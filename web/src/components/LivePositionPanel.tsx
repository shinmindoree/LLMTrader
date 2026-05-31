"use client";

import { useMemo } from "react";
import useSWR from "swr";

import { getBinanceAccountSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePageVisibility } from "@/lib/usePageVisibility";
import type {
  BinanceAccountSummary,
  BinancePositionSummary,
  Job,
  JobSummary,
} from "@/lib/types";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function extractSymbols(config: Record<string, unknown> | null | undefined): string[] {
  if (!config) return [];
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const syms: string[] = [];
  for (const raw of streams) {
    if (!isRecord(raw)) continue;
    const sym = typeof raw.symbol === "string" ? raw.symbol.trim().toUpperCase() : "";
    if (sym) syms.push(sym);
  }
  if (syms.length === 0 && typeof config.symbol === "string") {
    const s = config.symbol.trim().toUpperCase();
    if (s) syms.push(s);
  }
  return syms;
}

const formatNumber = (value: number, digits = 2): string =>
  value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

const formatSigned = (value: number, suffix = ""): string => {
  const formatted = formatNumber(value, 2);
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatted}${suffix ? ` ${suffix}` : ""}`;
};

/**
 * Single row showing a Binance futures position summary. Reused by
 * ``ActiveJobCard`` (live tab list) and ``LiveJobPositionPanel`` (live
 * job detail page).
 */
export function PositionRow({
  position,
  t,
}: {
  position: BinancePositionSummary;
  t: ReturnType<typeof useI18n>["t"];
}) {
  const pnlColor = position.unrealized_pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";
  const sideColor = position.side === "LONG" ? "text-[#26a69a]" : "text-[#ef5350]";
  const entryTime = position.entry_time
    ? new Date(position.entry_time).toLocaleString()
    : null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 text-xs">
      <span className="text-[#d1d4dc] font-medium">{position.symbol}</span>
      <span className={sideColor}>{position.side}</span>
      <span className="text-[#868993]">
        {t.live.posQty}{" "}
        <span className="text-[#d1d4dc]">
          {formatNumber(Math.abs(position.position_amt), 5)}
        </span>
      </span>
      <span className="text-[#868993]">
        {t.live.posEntry}{" "}
        <span className="text-[#d1d4dc]">{formatNumber(position.entry_price, 2)}</span>
      </span>
      <span className="text-[#868993]">
        {t.live.posNotional}{" "}
        <span className="text-[#d1d4dc]">
          {formatNumber(Math.abs(position.notional), 2)}
        </span>
      </span>
      <span className="text-[#868993]">
        {t.live.posLeverage} <span className="text-[#d1d4dc]">{position.leverage}x</span>
      </span>
      <span className="text-[#868993]">
        {t.live.posUnrealizedPnl}{" "}
        <span className={pnlColor}>{formatSigned(position.unrealized_pnl, "USDT")}</span>
      </span>
      {entryTime && (
        <span className="text-[#868993]">
          {t.live.posEntryTime}{" "}
          <span className="text-[#d1d4dc]">{entryTime}</span>
        </span>
      )}
    </div>
  );
}

/**
 * Render the orange-glowing "open position" panel for a list of
 * positions already filtered to a single job. Pure presentation — the
 * caller owns data fetching. ``ActiveJobCard`` uses this directly so it
 * can also reuse the same positions list to compute unrealized PnL on
 * the summary cards.
 */
export function PositionPanel({
  positions,
}: {
  positions: BinancePositionSummary[];
}) {
  const { t } = useI18n();
  if (positions.length === 0) return null;
  return (
    <>
      <style>{`
        @keyframes orange-glow {
          0%, 100% { border-color: rgba(255, 152, 0, 0.2); box-shadow: none; }
          50% { border-color: rgba(255, 152, 0, 0.5); box-shadow: 0 0 8px rgba(255, 152, 0, 0.1); }
        }
      `}</style>
      <div
        className="rounded bg-[#131722] px-3 py-2"
        style={{
          border: "1px solid rgba(255, 152, 0, 0.2)",
          animation: "orange-glow 2.5s ease-in-out infinite",
        }}
      >
        <div className="mb-1 text-[10px] font-medium text-[#868993]">
          {t.live.openPosition}
        </div>
        <div className="space-y-1">
          {positions.map((p) => (
            <PositionRow key={`${p.symbol}-${p.side}`} position={p} t={t} />
          ))}
        </div>
      </div>
    </>
  );
}

/**
 * Self-contained "open position" panel for a single LIVE job — fetches
 * the Binance account snapshot via SWR (sharing the cache key with
 * ``ActiveJobCard`` so the request is deduped across the page) and
 * filters to symbols configured on this job. Renders nothing when the
 * job has no open position for those symbols.
 *
 * Used on the LIVE job detail page so users see the same blinking
 * position indicator they get on the live tab list.
 */
export function LiveJobPositionPanel({
  job,
  active = true,
  className,
}: {
  job: Job | JobSummary;
  active?: boolean;
  className?: string;
}) {
  const isVisible = usePageVisibility();
  const config = isRecord(job.config) ? job.config : null;

  const { data: snapshot } = useSWR<BinanceAccountSummary>(
    active && job.type === "LIVE" ? "binanceAccountSummary" : null,
    () => getBinanceAccountSummary(),
    {
      refreshInterval: isVisible ? 15_000 : 30_000,
      dedupingInterval: 5_000,
    },
  );

  const symbols = useMemo(() => extractSymbols(config), [config]);

  const matchedPositions = useMemo(() => {
    const positions = snapshot?.positions;
    if (!positions || symbols.length === 0) return [];
    return positions.filter((p) => symbols.includes(p.symbol.toUpperCase()));
  }, [snapshot, symbols]);

  if (matchedPositions.length === 0) return null;

  return (
    <div className={className}>
      <PositionPanel positions={matchedPositions} />
    </div>
  );
}
