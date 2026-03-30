"use client";

import { useI18n } from "@/lib/i18n";
import type { JobType } from "@/lib/types";

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function formatTs(ms: unknown): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "-";
  return new Date(ms).toLocaleDateString();
}

interface LiveStreamInfo {
  symbol: string;
  interval: string;
  leverage: string;
  maxPosition: string;
  dailyLossLimit: string;
  stopLossPct: string;
  cooldownCandles: string | null;
  maxPyramidEntries: string | null;
}

function extractLiveStreams(config: Record<string, unknown>): LiveStreamInfo[] {
  const streams = Array.isArray(config.streams) ? config.streams : [];
  const result: LiveStreamInfo[] = [];
  for (const raw of streams) {
    if (!isRecord(raw)) continue;
    result.push({
      symbol: String(raw.symbol ?? "-"),
      interval: String(raw.interval ?? "-"),
      leverage: raw.leverage != null ? `${raw.leverage}x` : "-",
      maxPosition: raw.max_position != null ? String(raw.max_position) : "-",
      dailyLossLimit: raw.daily_loss_limit != null ? `${raw.daily_loss_limit} USDT` : "-",
      stopLossPct: raw.stop_loss_pct != null ? `${(Number(raw.stop_loss_pct) * 100).toFixed(1)}%` : "-",
      cooldownCandles: raw.stoploss_cooldown_candles != null ? String(raw.stoploss_cooldown_candles) : null,
      maxPyramidEntries: raw.max_pyramid_entries != null ? String(raw.max_pyramid_entries) : null,
    });
  }
  return result;
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

function LiveStreamSection({ stream, index, total }: { stream: LiveStreamInfo; index: number; total: number }) {
  const { t } = useI18n();
  return (
    <div>
      {total > 1 && (
        <div className="mb-1 text-[10px] font-medium text-[#868993] uppercase tracking-wider">
          {t.jobConfig.streams} {index + 1} — {stream.symbol}@{stream.interval}
        </div>
      )}
      <div className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
        <ConfigEntry label={t.jobConfig.symbol} value={stream.symbol} />
        <ConfigEntry label={t.jobConfig.interval} value={stream.interval} />
        <ConfigEntry label={t.jobConfig.leverage} value={stream.leverage} />
        <ConfigEntry label={t.jobConfig.maxPosition} value={stream.maxPosition} />
        <ConfigEntry label={t.jobConfig.dailyLossLimit} value={stream.dailyLossLimit} />
        <ConfigEntry label={t.jobConfig.stopLoss} value={stream.stopLossPct} />
        {stream.cooldownCandles && stream.cooldownCandles !== "0" ? (
          <ConfigEntry label={t.jobConfig.slCooldown} value={`${stream.cooldownCandles} ${t.jobConfig.candles}`} />
        ) : null}
        {stream.maxPyramidEntries && stream.maxPyramidEntries !== "0" ? (
          <ConfigEntry label={t.jobConfig.pyramid} value={`${t.jobConfig.max} ${stream.maxPyramidEntries}`} />
        ) : null}
      </div>
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
  const { t } = useI18n();
  if (type === "LIVE") {
    const streams = extractLiveStreams(config);
    if (streams.length === 0) return null;
    return (
      <div className="mt-3 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-[#868993]">
          {t.jobConfig.tradingConfig}
          {streams.length > 1 && (
            <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#d1d4dc]">
              {streams.length} {t.jobConfig.streams}
            </span>
          )}
        </div>
        <div className="space-y-3">
          {streams.map((s, i) => (
            <LiveStreamSection key={`${s.symbol}-${s.interval}`} stream={s} index={i} total={streams.length} />
          ))}
        </div>
      </div>
    );
  }

  const c = extractBacktestConfig(config);
  return (
    <div className="mt-3 rounded border border-[#2a2e39] bg-[#131722] px-4 py-3">
      <div className="mb-2 text-xs font-medium text-[#868993]">{t.jobConfig.tradingConfig}</div>
      <div className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
        <ConfigEntry label={t.jobConfig.symbol} value={c.symbol} />
        <ConfigEntry label={t.jobConfig.interval} value={c.interval} />
        <ConfigEntry label={t.jobConfig.leverage} value={c.leverage} />
        <ConfigEntry label={t.jobConfig.initialBalance} value={c.initialBalance} />
        <ConfigEntry label={t.jobConfig.commission} value={c.commission} />
        <ConfigEntry label={t.jobConfig.stopLoss} value={c.stopLossPct} />
        {c.maxPyramidEntries && c.maxPyramidEntries !== "0" ? (
          <ConfigEntry label={t.jobConfig.pyramid} value={`${t.jobConfig.max} ${c.maxPyramidEntries}`} />
        ) : null}
        <ConfigEntry label={t.jobConfig.period} value={`${c.startDate} ~ ${c.endDate}`} />
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
  const { t } = useI18n();
  if (type === "LIVE") {
    const streams = extractLiveStreams(config);
    if (streams.length === 0) return null;
    if (streams.length === 1) {
      const c = streams[0];
      return (
        <span className="text-[#868993]">
          {c.symbol} · {c.interval} · {t.jobConfig.leverage} {c.leverage} · {t.jobConfig.stopLoss} {c.stopLossPct}
        </span>
      );
    }
    const tags = streams.map((s) => `${s.symbol}@${s.interval}`).join(", ");
    return (
      <span className="text-[#868993]">
        {streams.length} {t.jobConfig.streams} · {tags}
      </span>
    );
  }

  const c = extractBacktestConfig(config);
  return (
    <span className="text-[#868993]">
      {c.symbol} · {c.interval} · {t.jobConfig.leverage} {c.leverage} · {c.initialBalance} · {c.startDate}~{c.endDate}
    </span>
  );
}
