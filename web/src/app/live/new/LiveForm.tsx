"use client";

import { useCallback, useState } from "react";

import { createJob, preflightJob } from "@/lib/api";
import type { Job, StrategyInfo } from "@/lib/types";

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";
const LIVE_INTERVALS = ["1m", "5m", "15m", "1h"] as const;
const MAX_STREAMS = 5;

interface StreamConfig {
  id: string;
  symbol: string;
  interval: string;
  leverage: number;
  maxPosition: number;
  dailyLossLimit: number;
  stopLossPct: number;
  stoplossCooldownCandles: number;
  maxPyramidEntries: number;
}

let _nextStreamId = 1;
function nextStreamId(): string {
  return `stream-${_nextStreamId++}`;
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function loadExecutionDefaults(): { symbol: string; interval: string; applied: boolean } {
  if (typeof window === "undefined") {
    return { symbol: "BTCUSDT", interval: "1m", applied: false };
  }
  try {
    const raw = window.localStorage.getItem(EXECUTION_DEFAULTS_KEY);
    if (!raw) return { symbol: "BTCUSDT", interval: "1m", applied: false };
    const data = JSON.parse(raw) as Record<string, unknown>;
    const symbol = typeof data.symbol === "string" && data.symbol.trim()
      ? data.symbol.trim().toUpperCase()
      : "BTCUSDT";
    const intervalCandidate = typeof data.interval === "string" ? data.interval.trim() : "";
    const interval = LIVE_INTERVALS.includes(intervalCandidate as (typeof LIVE_INTERVALS)[number])
      ? intervalCandidate
      : "1m";
    return { symbol, interval, applied: true };
  } catch {
    return { symbol: "BTCUSDT", interval: "1m", applied: false };
  }
}

function createDefaultStream(overrides?: Partial<Pick<StreamConfig, "symbol" | "interval">>): StreamConfig {
  return {
    id: nextStreamId(),
    symbol: overrides?.symbol ?? "BTCUSDT",
    interval: overrides?.interval ?? "1m",
    leverage: 1,
    maxPosition: 0.5,
    dailyLossLimit: 500,
    stopLossPct: 0.05,
    stoplossCooldownCandles: 0,
    maxPyramidEntries: 0,
  };
}

function formatPolicyMessages(title: string, items: string[]): string {
  if (items.length === 0) return title;
  return `${title}\n${items.map((item, idx) => `${idx + 1}. ${item}`).join("\n")}`;
}

const inputCls =
  "w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors";
const selectCls = inputCls;
const labelCls = "text-sm";
const hintCls = "mb-1 text-xs text-[#868993]";

function StreamCard({
  stream,
  index,
  total,
  onChange,
  onRemove,
}: {
  stream: StreamConfig;
  index: number;
  total: number;
  onChange: (updated: StreamConfig) => void;
  onRemove: () => void;
}) {
  const update = useCallback(
    (patch: Partial<StreamConfig>) => onChange({ ...stream, ...patch }),
    [stream, onChange],
  );

  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium text-[#d1d4dc]">
          Stream {index + 1}
          <span className="ml-1.5 text-[#868993] font-normal">
            {stream.symbol || "…"}@{stream.interval}
          </span>
        </span>
        {total > 1 && (
          <button
            type="button"
            className="rounded border border-[#2a2e39] px-2 py-0.5 text-xs text-[#868993] hover:border-[#ef5350] hover:text-[#ef5350] transition-colors"
            onClick={onRemove}
          >
            Remove
          </button>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className={labelCls}>
          <div className={hintCls}>Symbol</div>
          <input
            className={inputCls}
            value={stream.symbol}
            onChange={(e) => update({ symbol: e.target.value.toUpperCase() })}
          />
        </label>
        <label className={labelCls}>
          <div className={hintCls}>Interval</div>
          <select
            className={selectCls}
            value={stream.interval}
            onChange={(e) => update({ interval: e.target.value })}
          >
            {LIVE_INTERVALS.map((itv) => (
              <option key={itv} value={itv} className="bg-[#131722]">{itv}</option>
            ))}
          </select>
        </label>
        <label className={labelCls}>
          <div className={hintCls}>Leverage</div>
          <input
            className={inputCls}
            type="number"
            value={stream.leverage}
            min={1}
            max={10}
            onChange={(e) => update({ leverage: Number(e.target.value) })}
          />
        </label>
        <label className={labelCls}>
          <div className={hintCls}>Max Position (0–1)</div>
          <input
            className={inputCls}
            type="number"
            step="0.01"
            value={stream.maxPosition}
            min={0.01}
            max={1}
            onChange={(e) => update({ maxPosition: Number(e.target.value) })}
          />
        </label>
        <label className={labelCls}>
          <div className={hintCls}>Daily Loss Limit (USDT)</div>
          <input
            className={inputCls}
            type="number"
            value={stream.dailyLossLimit}
            min={0}
            onChange={(e) => update({ dailyLossLimit: Number(e.target.value) })}
          />
        </label>
        <label className={labelCls}>
          <div className={hintCls}>StopLoss (%)</div>
          <input
            className={inputCls}
            type="number"
            step="0.1"
            value={stream.stopLossPct * 100}
            onChange={(e) => update({ stopLossPct: Number(e.target.value) / 100 })}
          />
        </label>
        <label className={labelCls}>
          <div className={hintCls}>SL Cooldown (candles)</div>
          <input
            className={inputCls}
            type="number"
            value={stream.stoplossCooldownCandles}
            min={0}
            max={1000}
            onChange={(e) => update({ stoplossCooldownCandles: Number(e.target.value) })}
          />
        </label>
        <label className={labelCls}>
          <div className={hintCls}>Max Pyramid Entries</div>
          <input
            className={inputCls}
            type="number"
            value={stream.maxPyramidEntries}
            min={0}
            max={10}
            onChange={(e) => update({ maxPyramidEntries: Number(e.target.value) })}
          />
        </label>
      </div>
    </div>
  );
}

export function LiveForm({
  strategies,
  onCreated,
  onSubmittingChange,
}: {
  strategies: StrategyInfo[];
  onCreated?: (job: Job) => void;
  onSubmittingChange?: (submitting: boolean) => void;
}) {
  const defaults = loadExecutionDefaults();
  const [strategyPath, setStrategyPath] = useState(strategies[0]?.path ?? "");
  const [streams, setStreams] = useState<StreamConfig[]>(() => [
    createDefaultStream({ symbol: defaults.symbol, interval: defaults.interval }),
  ]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const updateStream = useCallback((id: string, updated: StreamConfig) => {
    setStreams((prev) => prev.map((s) => (s.id === id ? updated : s)));
  }, []);

  const removeStream = useCallback((id: string) => {
    setStreams((prev) => prev.filter((s) => s.id !== id));
  }, []);

  const addStream = useCallback(() => {
    setStreams((prev) => {
      if (prev.length >= MAX_STREAMS) return prev;
      return [...prev, createDefaultStream({ symbol: "ETHUSDT" })];
    });
  }, []);

  const onSubmit = async () => {
    setError(null);
    setSubmitting(true);
    onSubmittingChange?.(true);
    try {
      const config = {
        streams: streams.map((s) => ({
          symbol: s.symbol.trim().toUpperCase(),
          interval: s.interval,
          leverage: s.leverage,
          max_position: s.maxPosition,
          daily_loss_limit: s.dailyLossLimit,
          stop_loss_pct: s.stopLossPct,
          max_consecutive_losses: 0,
          stoploss_cooldown_candles: s.stoplossCooldownCandles,
          max_pyramid_entries: s.maxPyramidEntries,
        })),
      };

      const preflight = await preflightJob({ type: "LIVE", config });
      if (!preflight.ok) {
        const msg = formatPolicyMessages("Run blocked. Please update your settings.", preflight.blockers);
        setError(msg);
        return;
      }
      if (preflight.warnings.length > 0) {
        const proceed = window.confirm(
          formatPolicyMessages("High-risk warnings detected. Do you want to continue?", preflight.warnings),
        );
        if (!proceed) return;
      }

      const job = await createJob({ type: "LIVE", strategy_path: strategyPath, config });
      if (!job?.job_id || !isUuid(job.job_id)) {
        throw new Error(`Invalid run reference returned: ${String(job?.job_id)}`);
      }
      onCreated?.(job);
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
      onSubmittingChange?.(false);
    }
  };

  return (
    <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-5">
      {error ? (
        <p className="mb-4 whitespace-pre-wrap rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}

      <p className="mb-4 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-xs text-[#868993]">
        Configure up to {MAX_STREAMS} streams (symbol + interval pairs) for portfolio trading.
        The strategy receives each stream's bars independently.
      </p>

      {defaults.applied ? (
        <p className="mb-4 text-xs text-[#868993]">
          Stream 1 defaults were pre-filled from your recent strategy request.
        </p>
      ) : null}

      {/* Strategy selector */}
      <div className="mb-4">
        <label className={labelCls}>
          <div className={hintCls}>Strategy</div>
          <select
            className={selectCls}
            value={strategyPath}
            onChange={(e) => setStrategyPath(e.target.value)}
          >
            {strategies.map((s) => (
              <option key={s.path} value={s.path} className="bg-[#131722]">{s.name}</option>
            ))}
          </select>
        </label>
      </div>

      {/* Stream cards */}
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-medium text-[#d1d4dc]">
          Streams
          <span className="ml-1.5 rounded bg-[#131722] px-1.5 py-0.5 text-[#868993]">
            {streams.length}/{MAX_STREAMS}
          </span>
        </span>
        {streams.length < MAX_STREAMS && (
          <button
            type="button"
            className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-1 text-xs text-[#2962ff] hover:border-[#2962ff] hover:bg-[#1a2340] transition-colors"
            onClick={addStream}
          >
            + Add Stream
          </button>
        )}
      </div>

      <div className="space-y-3">
        {streams.map((s, i) => (
          <StreamCard
            key={s.id}
            stream={s}
            index={i}
            total={streams.length}
            onChange={(updated) => updateStream(s.id, updated)}
            onRemove={() => removeStream(s.id)}
          />
        ))}
      </div>

      <button
        className="mt-5 rounded bg-[#ef5350] px-4 py-2 text-sm text-white hover:bg-[#d32f2f] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        onClick={onSubmit}
        disabled={submitting}
      >
        {streams.length > 1
          ? `Run Portfolio (${streams.length} streams)`
          : "Run Live (Testnet)"}
      </button>
    </div>
  );
}
