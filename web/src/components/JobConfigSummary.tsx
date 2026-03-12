"use client";

import type { JobType } from "@/lib/types";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function formatTs(ms: unknown): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "-";
  return new Date(ms).toLocaleDateString();
}

function extractLiveConfig(config: Record<string, unknown>) {
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const stream = streams.length > 0 && isRecord(streams[0]) ? (streams[0] as Record<string, unknown>) : null;
  if (!stream) return null;
  return {
    symbol: String(stream.symbol ?? "-"),
    interval: String(stream.interval ?? "-"),
    leverage: stream.leverage != null ? `${stream.leverage}x` : "-",
    maxPosition: stream.max_position != null ? String(stream.max_position) : "-",
    dailyLossLimit: stream.daily_loss_limit != null ? `${stream.daily_loss_limit} USDT` : "-",
    stopLossPct: stream.stop_loss_pct != null ? `${(Number(stream.stop_loss_pct) * 100).toFixed(1)}%` : "-",
    cooldownCandles: stream.stoploss_cooldown_candles != null ? String(stream.stoploss_cooldown_candles) : null,
    maxPyramidEntries: stream.max_pyramid_entries != null ? String(stream.max_pyramid_entries) : null,
  };
}

function extractBacktestConfig(config: Record<string, unknown>) {
  return {
    symbol: String(config.symbol ?? "-"),
    interval: String(config.interval ?? "-"),
    leverage: config.leverage != null ? `${config.leverage}x` : "-",
    initialBalance: config.initial_balance != null ? `${config.initial_balance} USDT` : "-",
    commission: config.commission != null ? `${(Number(config.commission) * 100).toFixed(2)}%` : "-",
    stopLossPct: config.stop_loss_pct != null ? `${(Number(config.stop_loss_pct) * 100).toFixed(1)}%` : "-",
    maxPyramidEntries: config.max_pyramid_entries != null ? String(config.max_pyramid_entries) : null,
    startDate: formatTs(config.start_ts),
    endDate: formatTs(config.end_ts),
  };
}

type ConfigEntryProps = { label: string; value: string };

function ConfigEntry({ label, value }: ConfigEntryProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[#868993]">{label}:</span>
      <span className="text-[#d1d4dc]">{value}</span>
    </div>
  );
}

export function JobConfigSummary({
  type,
  config,
}: {
  type: JobType;
  config: Record<string, unknown>;
}) {
  if (type === "LIVE") {
    const c = extractLiveConfig(config);
    if (!c) return null;
    return (
      <div className="mt-3 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
        <div className="mb-2 text-xs font-medium text-[#868993]">Trading Config</div>
        <div className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
          <ConfigEntry label="Symbol" value={c.symbol} />
          <ConfigEntry label="Interval" value={c.interval} />
          <ConfigEntry label="Leverage" value={c.leverage} />
          <ConfigEntry label="Max Position" value={c.maxPosition} />
          <ConfigEntry label="Daily Loss Limit" value={c.dailyLossLimit} />
          <ConfigEntry label="Stop Loss" value={c.stopLossPct} />
          {c.cooldownCandles && c.cooldownCandles !== "0" ? (
            <ConfigEntry label="SL Cooldown" value={`${c.cooldownCandles} candles`} />
          ) : null}
          {c.maxPyramidEntries && c.maxPyramidEntries !== "0" ? (
            <ConfigEntry label="Pyramid" value={`max ${c.maxPyramidEntries}`} />
          ) : null}
        </div>
      </div>
    );
  }

  const c = extractBacktestConfig(config);
  return (
    <div className="mt-3 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
      <div className="mb-2 text-xs font-medium text-[#868993]">Trading Config</div>
      <div className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
        <ConfigEntry label="Symbol" value={c.symbol} />
        <ConfigEntry label="Interval" value={c.interval} />
        <ConfigEntry label="Leverage" value={c.leverage} />
        <ConfigEntry label="Initial Balance" value={c.initialBalance} />
        <ConfigEntry label="Commission" value={c.commission} />
        <ConfigEntry label="Stop Loss" value={c.stopLossPct} />
        {c.maxPyramidEntries && c.maxPyramidEntries !== "0" ? (
          <ConfigEntry label="Pyramid" value={`max ${c.maxPyramidEntries}`} />
        ) : null}
        <ConfigEntry label="Period" value={`${c.startDate} ~ ${c.endDate}`} />
      </div>
    </div>
  );
}

export function JobConfigInline({
  type,
  config,
}: {
  type: JobType;
  config: Record<string, unknown>;
}) {
  if (type === "LIVE") {
    const c = extractLiveConfig(config);
    if (!c) return null;
    return (
      <span className="text-[#868993]">
        {c.symbol} · {c.interval} · {c.leverage} · SL {c.stopLossPct}
      </span>
    );
  }

  const c = extractBacktestConfig(config);
  return (
    <span className="text-[#868993]">
      {c.symbol} · {c.interval} · {c.leverage} · {c.initialBalance} · {c.startDate}~{c.endDate}
    </span>
  );
}
