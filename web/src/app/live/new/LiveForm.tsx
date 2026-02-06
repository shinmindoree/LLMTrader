"use client";

import { useState } from "react";

import { createJob, preflightJob } from "@/lib/api";
import type { Job, StrategyInfo } from "@/lib/types";

const EXECUTION_DEFAULTS_KEY = "llmtrader.execution_defaults";
const LIVE_INTERVALS = ["1m", "5m", "15m", "1h"] as const;

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

function formatPolicyMessages(title: string, items: string[]): string {
  if (items.length === 0) return title;
  return `${title}\n${items.map((item, idx) => `${idx + 1}. ${item}`).join("\n")}`;
}

export function LiveForm({
  strategies,
  onCreated,
}: {
  strategies: StrategyInfo[];
  onCreated?: (job: Job) => void;
}) {
  const defaults = loadExecutionDefaults();
  const [strategyPath, setStrategyPath] = useState(strategies[0]?.path ?? "");
  const [symbol, setSymbol] = useState(defaults.symbol);
  const [interval, setInterval] = useState(defaults.interval);
  const [leverage, setLeverage] = useState(1);
  const [maxPosition, setMaxPosition] = useState(0.5);
  const [dailyLossLimit, setDailyLossLimit] = useState(500);
  const [stopLossPct, setStopLossPct] = useState(0.05);
  const [stoplossCooldownCandles, setStoplossCooldownCandles] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = async () => {
    setError(null);
    try {
      const config = {
        streams: [
          {
            symbol,
            interval,
            leverage,
            max_position: maxPosition,
            daily_loss_limit: dailyLossLimit,
            stop_loss_pct: stopLossPct,
            max_consecutive_losses: 0,
            stoploss_cooldown_candles: stoplossCooldownCandles,
          },
        ],
      };

      const preflight = await preflightJob({
        type: "LIVE",
        config,
      });
      if (!preflight.ok) {
        const msg = formatPolicyMessages("실행이 차단되었습니다. 설정을 수정해주세요.", preflight.blockers);
        setError(msg);
        return;
      }
      if (preflight.warnings.length > 0) {
        const proceed = window.confirm(
          formatPolicyMessages("고위험 경고가 있습니다. 계속 실행하시겠습니까?", preflight.warnings),
        );
        if (!proceed) {
          return;
        }
      }

      const job = await createJob({
        type: "LIVE",
        strategy_path: strategyPath,
        config,
      });
      if (!job?.job_id || !isUuid(job.job_id)) {
        throw new Error(`Invalid job_id returned: ${String(job?.job_id)}`);
      }
      onCreated?.(job);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="rounded border border-[#2a2e39] bg-[#1e222d] p-5">
      {error ? (
        <p className="mb-4 rounded border border-[#ef5350]/30 bg-[#2d1f1f]/50 px-4 py-3 text-sm text-[#ef5350]">
          {error}
        </p>
      ) : null}
      <p className="mb-4 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-xs text-[#868993]">
        전략 생성 프롬프트에 거래 설정(심볼/간격/레버리지 등)을 적었더라도, 실제 실행에는 이 폼의 값이
        우선 적용됩니다.
      </p>
      {defaults.applied ? (
        <p className="mb-4 text-xs text-[#868993]">
          최근 전략 입력에서 추출한 기본값을 반영했습니다. 필요하면 아래에서 변경하세요.
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Strategy</div>
          <select
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            value={strategyPath}
            onChange={(e) => setStrategyPath(e.target.value)}
          >
            {strategies.map((s) => (
              <option key={s.path} value={s.path} className="bg-[#131722]">
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Symbol</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Interval</div>
          <select
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
          >
            {LIVE_INTERVALS.map((itv) => (
              <option key={itv} value={itv} className="bg-[#131722]">
                {itv}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Leverage</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={leverage}
            min={1}
            max={10}
            onChange={(e) => setLeverage(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Max Position (0-1)</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.01"
            value={maxPosition}
            min={0.01}
            max={1}
            onChange={(e) => setMaxPosition(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">Daily Loss Limit (USDT)</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={dailyLossLimit}
            min={0}
            onChange={(e) => setDailyLossLimit(Number(e.target.value))}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">StopLoss (%)</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            step="0.1"
            value={stopLossPct * 100}
            onChange={(e) => setStopLossPct(Number(e.target.value) / 100)}
          />
        </label>
        <label className="text-sm">
          <div className="mb-1 text-xs text-[#868993]">StopLoss Cooldown (candles)</div>
          <input
            className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none transition-colors"
            type="number"
            value={stoplossCooldownCandles}
            min={0}
            max={1000}
            onChange={(e) => setStoplossCooldownCandles(Number(e.target.value))}
          />
          <div className="mt-1 text-xs text-[#868993]">
            0 = off, StopLoss 청산 후 N 캔들 동안 신규 진입 차단
          </div>
        </label>
      </div>

      <button
        className="mt-5 rounded bg-[#ef5350] px-4 py-2 text-sm text-white hover:bg-[#d32f2f] transition-colors"
        onClick={onSubmit}
      >
        Run Live (Testnet)
      </button>
    </div>
  );
}
