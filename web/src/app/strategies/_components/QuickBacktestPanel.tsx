"use client";

import { useState } from "react";
import type { QuickBacktestResponse } from "@/lib/types";
import { quickBacktest } from "@/lib/api";
import MiniEquityCurve from "./MiniEquityCurve";

const INTERVAL_MAX_DAYS: Record<string, number> = {
  "1m": 7,
  "5m": 30,
  "15m": 60,
  "1h": 90,
  "4h": 90,
  "1d": 90,
};

type Props = {
  code: string;
  strategyParams?: Record<string, unknown>;
  onAnalyzeWithAI?: (result: QuickBacktestResponse, config: { symbol: string; interval: string; days: number }) => void;
};

export default function QuickBacktestPanel({ code, strategyParams, onAnalyzeWithAI }: Props) {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setInterval] = useState("1h");
  const [days, setDays] = useState(30);
  const [initialBalance, setInitialBalance] = useState(10000);
  const [leverage, setLeverage] = useState(1);
  const [commission, setCommission] = useState(0.04);
  const [stopLossPct, setStopLossPct] = useState(5);

  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QuickBacktestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const maxDays = INTERVAL_MAX_DAYS[interval] ?? 90;

  const handleIntervalChange = (newInterval: string) => {
    setInterval(newInterval);
    const newMax = INTERVAL_MAX_DAYS[newInterval] ?? 90;
    if (days > newMax) setDays(newMax);
  };

  const handleRun = async () => {
    if (!code.trim() || running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const res = await quickBacktest({
        code,
        symbol: symbol.trim().toUpperCase(),
        interval,
        days,
        initial_balance: initialBalance,
        leverage,
        commission: commission / 100,
        stop_loss_pct: stopLossPct / 100,
        strategy_params: strategyParams,
      });
      if (res.success) {
        setResult(res);
      } else {
        setError(res.message ?? "백테스트 실행에 실패했습니다.");
        setResult(res);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "알 수 없는 오류가 발생했습니다.";
      setError(msg);
    } finally {
      setRunning(false);
    }
  };

  const m = result?.metrics;

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="flex flex-col gap-3 px-3 py-3">
        {/* ── Config Form ── */}
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] text-[#9aa0ad]">심볼</span>
            <input
              type="text"
              className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 font-mono text-xs text-[#d1d4dc] uppercase focus:border-[#2962ff] focus:outline-none"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] text-[#9aa0ad]">인터벌</span>
            <select
              className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-xs text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              value={interval}
              onChange={(e) => handleIntervalChange(e.target.value)}
            >
              {Object.keys(INTERVAL_MAX_DAYS).map((iv) => (
                <option key={iv} value={iv}>{iv}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] text-[#9aa0ad]">기간 (일)</span>
            <input
              type="number"
              className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 font-mono text-xs text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              min={1}
              max={maxDays}
              value={days}
              onChange={(e) => setDays(Math.min(maxDays, Math.max(1, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] text-[#9aa0ad]">초기 잔고</span>
            <input
              type="number"
              className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 font-mono text-xs text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              min={100}
              value={initialBalance}
              onChange={(e) => setInitialBalance(Number(e.target.value) || 10000)}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] text-[#9aa0ad]">레버리지</span>
            <input
              type="number"
              className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 font-mono text-xs text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              min={1}
              max={20}
              value={leverage}
              onChange={(e) => setLeverage(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
            />
          </label>
          <label className="flex flex-col gap-0.5">
            <span className="text-[11px] text-[#9aa0ad]">수수료 (%)</span>
            <input
              type="number"
              className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 font-mono text-xs text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              min={0}
              max={1}
              step={0.01}
              value={commission}
              onChange={(e) => setCommission(Number(e.target.value) || 0)}
            />
          </label>
          <label className="col-span-2 flex flex-col gap-0.5">
            <span className="text-[11px] text-[#9aa0ad]">손절 (%)</span>
            <input
              type="number"
              className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 font-mono text-xs text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              min={0.1}
              max={50}
              step={0.1}
              value={stopLossPct}
              onChange={(e) => setStopLossPct(Number(e.target.value) || 5)}
            />
          </label>
        </div>

        {/* ── Run button + quota ── */}
        <button
          type="button"
          className="w-full rounded bg-[#2962ff] px-3 py-2 text-sm font-medium text-white transition hover:bg-[#1e4bd8] disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!code.trim() || running}
          onClick={handleRun}
        >
          {running ? (
            <span className="flex items-center justify-center gap-2">
              <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
              테스트 실행 중...
            </span>
          ) : (
            "▶ 백테스트 실행"
          )}
        </button>

        {result?.quota_remaining != null && (
          <p className="text-center text-[11px] text-[#6b7383]">
            오늘 남은 횟수: {result.quota_remaining}회
          </p>
        )}

        {/* ── Error ── */}
        {error && (
          <div className="rounded border border-[#3b1f26] bg-[#1a1012] px-3 py-2 text-xs text-[#ef9a9a]">
            {error}
          </div>
        )}

        {/* ── Results ── */}
        {m && (
          <>
            {/* Metric cards */}
            <div className="grid grid-cols-3 gap-2">
              <MetricCard
                label="수익률"
                value={`${m.total_return_pct >= 0 ? "+" : ""}${m.total_return_pct}%`}
                positive={m.total_return_pct >= 0}
              />
              <MetricCard label="승률" value={`${m.win_rate}%`} positive={m.win_rate >= 50} />
              <MetricCard label="거래수" value={`${m.total_trades}건`} />
              <MetricCard label="최대낙폭" value={`-${m.max_drawdown_pct}%`} positive={false} />
              <MetricCard label="샤프비율" value={`${m.sharpe_ratio}`} positive={m.sharpe_ratio > 0} />
              <MetricCard
                label="순이익"
                value={`$${m.net_profit >= 0 ? "+" : ""}${m.net_profit.toLocaleString()}`}
                positive={m.net_profit >= 0}
              />
            </div>

            {/* Equity curve */}
            {result.equity_curve.length >= 2 && (
              <div className="rounded border border-[#2a2e39] bg-[#131722] p-2">
                <p className="mb-1 text-[10px] text-[#6b7383]">수익 곡선</p>
                <MiniEquityCurve data={result.equity_curve} initialBalance={m.initial_balance} height={100} />
              </div>
            )}

            {/* Trade list */}
            {result.trades_summary.length > 0 && (
              <div className="rounded border border-[#2a2e39]">
                <p className="border-b border-[#2a2e39] px-2 py-1.5 text-[10px] text-[#6b7383]">
                  최근 거래 ({Math.min(result.trades_summary.length, 5)}/{result.trades_summary.length})
                </p>
                <div className="divide-y divide-[#2a2e39]">
                  {result.trades_summary.slice(-5).map((t, i) => (
                    <div key={i} className="flex items-center justify-between px-2 py-1.5 text-[11px]">
                      <span className={t.pnl >= 0 ? "text-[#26a69a]" : "text-[#ef5350]"}>
                        {t.side} {t.pnl >= 0 ? "+" : ""}{t.return_pct}%
                      </span>
                      <span className="text-[#6b7383]">
                        ${t.entry_price.toLocaleString()} → ${t.exit_price.toLocaleString()}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Duration */}
            {result.duration_ms > 0 && (
              <p className="text-center text-[10px] text-[#4a4e59]">
                실행 시간: {(result.duration_ms / 1000).toFixed(1)}초
              </p>
            )}

            {/* AI Analyze button */}
            <button
              type="button"
              className="w-full rounded border border-[#2962ff]/50 px-3 py-2 text-sm font-medium text-[#8fa8ff] transition hover:bg-[#2962ff]/10"
              onClick={() => onAnalyzeWithAI?.(result, { symbol, interval, days })}
            >
              🤖 AI에게 분석 요청
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function MetricCard({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  let colorClass = "text-[#d1d4dc]";
  if (positive === true) colorClass = "text-[#26a69a]";
  if (positive === false) colorClass = "text-[#ef5350]";

  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-center">
      <p className="text-[10px] text-[#6b7383]">{label}</p>
      <p className={`text-sm font-semibold ${colorClass}`}>{value}</p>
    </div>
  );
}
