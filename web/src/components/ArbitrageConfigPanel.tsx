"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { getFundingArbStatus, getFundingScreener, startFundingArb, stopFundingArb } from "@/lib/api";
import type { FundingArbitrageParams, FundingScreenerItem } from "@/lib/types";

const REFRESH_MS = 15_000;
const SCREENER_REFRESH_MS = 30_000;
const ROUNDTRIP_COST_PCT = 0.20; // VIP0 conservative
const EXIT_RATIOS: Record<number, number> = { 1: 0.50, 3: 0.25 };
// 즉시 검증 모드: 진입 임계치를 사실상 0으로 낮춰 펀딩비가 양수이기만 하면 진입시킨다.
const VERIFY_ENTRY_PCT = 0.0001;
const VERIFY_EXIT_PCT = 0.00005;

function computeDeadband(item: FundingScreenerItem, holdDays: number) {
  const entry = ROUNDTRIP_COST_PCT / item.half_life_settlements;
  const exit = entry * (EXIT_RATIOS[holdDays] ?? 0.30);
  return { entry, exit };
}

function fmt2(v: number) {
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v: number | null | undefined, digits = 2) {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

function fmtCountdown(targetIso: string | null | undefined, nowMs: number): string | null {
  if (!targetIso) return null;
  const t = Date.parse(targetIso);
  if (!Number.isFinite(t)) return null;
  let diff = Math.max(0, t - nowMs);
  const h = Math.floor(diff / 3_600_000);
  diff -= h * 3_600_000;
  const m = Math.floor(diff / 60_000);
  diff -= m * 60_000;
  const s = Math.floor(diff / 1_000);
  if (h > 0) return `${h}시간 ${m}분 ${s}초`;
  return `${m}분 ${s}초`;
}

function ScoreBar({ score }: { score: number }) {
  const capped = Math.min(score, 5);
  const pct = (capped / 5) * 100;
  const color = score >= 2 ? "#26a69a" : score >= 1 ? "#f0b90b" : "#ef5350";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-[#2a2e39]">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="text-xs font-mono" style={{ color }}>
        {score.toFixed(1)}×
      </span>
    </div>
  );
}

export function ArbitrageConfigPanel() {
  const [selected, setSelected] = useState<FundingScreenerItem | null>(null);
  const [holdDays, setHoldDays] = useState(1);
  const [allocatedUsdt, setAllocatedUsdt] = useState(1000);
  const [env, setEnv] = useState<"mainnet" | "testnet">("testnet");
  const [marginAlertRatio, setMarginAlertRatio] = useState(0.80);
  const [rebalancePct, setRebalancePct] = useState(0.20);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [verifyMode, setVerifyMode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: status, mutate } = useSWR("funding-arb-status", getFundingArbStatus, {
    refreshInterval: REFRESH_MS,
  });
  const { data: screener, isLoading: screenerLoading } = useSWR(
    "funding-arb-screener",
    () => getFundingScreener(5),
    { refreshInterval: SCREENER_REFRESH_MS },
  );

  const running = status?.running ?? false;
  const deadband = selected ? computeDeadband(selected, holdDays) : null;

  const screenerItems = screener?.items ?? [];
  const hasScreenerItems = screenerItems.length > 0;
  const profitableCount = screenerItems.filter((i) => i.score >= 1).length;
  const noProfitable = hasScreenerItems && profitableCount === 0;
  const selectedBelowThreshold = selected != null && selected.score < 1;
  const awaitingEntry = running && !status?.spot_qty;

  const annPct = status?.annualized_funding_pct;
  const baseAsset = (status?.symbol ?? "").replace(/USDT$/, "") || (status?.symbol ?? "");

  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowMs(Date.now()), 1_000);
    return () => clearInterval(id);
  }, []);
  const fundingCountdown = fmtCountdown(status?.next_funding_time, nowMs);
  const pnlColor = (status?.unrealized_pnl ?? 0) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";
  const fundingColor = annPct != null && annPct > 0 ? "text-[#26a69a]" : "text-[#ef5350]";

  const handleStart = async () => {
    if (!selected || !deadband) return;
    setBusy(true);
    setError(null);
    const params: FundingArbitrageParams = {
      symbol: selected.symbol,
      env,
      allocated_usdt: allocatedUsdt,
      // 검증 모드에서는 hold_days를 생략해 백엔드의 자동 임계치 덮어쓰기를 비활성화한다.
      hold_days: verifyMode ? null : holdDays,
      entry_deadband_pct: verifyMode ? VERIFY_ENTRY_PCT : deadband.entry,
      exit_deadband_pct: verifyMode ? VERIFY_EXIT_PCT : deadband.exit,
      margin_alert_ratio: marginAlertRatio,
      rebalance_transfer_pct: rebalancePct,
    };
    try {
      await startFundingArb(params);
      await mutate();
    } catch (e) {
      setError(e instanceof Error ? e.message : "시작 실패");
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    setError(null);
    try {
      await stopFundingArb();
      await mutate();
    } catch (e) {
      setError(e instanceof Error ? e.message : "정지 실패");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      {/* ── Header ── */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-[#d1d4dc]">Funding Rate Arbitrage</span>
            <span
              className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${
                running ? "bg-[#26a69a]/15 text-[#26a69a]" : "bg-[#2a2e39] text-[#868993]"
              }`}
            >
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full ${
                  running ? "animate-pulse bg-[#26a69a]" : "bg-[#555]"
                }`}
              />
              {running ? "Running" : "Idle"}
            </span>
          </div>
          <p className="mt-0.5 text-xs text-[#868993]">
            현물 롱 + 선물 숏 · Delta-Neutral 펀딩비 수취
          </p>
        </div>
      </div>

      {error && (
        <p className="mt-3 rounded border border-[#ef5350]/40 bg-[#ef5350]/10 px-3 py-2 text-xs text-[#ef5350]">
          {error}
        </p>
      )}

      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        {/* ── LEFT: 실시간 스크리너 + 설정 (항상 표시) ── */}
        <div>
          {/* Screener */}
          <div>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-[#9aa0ad]">
                🔍 실시간 스크리너 — Top 5
              </p>
              <p className="text-[10px] text-[#555]">
                score = 현재 펀딩비 ÷ 최소 진입 임계치 · 30초마다 갱신
              </p>
            </div>

            {screenerLoading && !screener && (
              <p className="py-4 text-center text-xs text-[#868993]">데이터 로딩 중…</p>
            )}
            {noProfitable && (
              <p className="mb-2 rounded border border-[#f0b90b]/30 bg-[#f0b90b]/10 px-3 py-2 text-[11px] leading-relaxed text-[#f0b90b]">
                ℹ 현재 모든 종목의 펀딩비가 최소 진입 임계치(score 1.0×) 아래입니다. 지금 시작하면 봇은
                포지션을 열지 않고 임계치를 넘을 때까지 대기합니다. score ≥ 1.0× 종목이 나타나면 즉시 진입합니다.
              </p>
            )}
            {screener?.error && (
              <p className="rounded border border-[#f0b90b]/30 bg-[#f0b90b]/10 px-3 py-2 text-xs text-[#f0b90b]">
                ⚠ {screener.error}
              </p>
            )}
            {screener && !screener.error && screener.items.length === 0 && (
              <p className="py-3 text-center text-xs text-[#868993]">
                현재 기준 충족 종목 없음 (모든 종목 펀딩비 ≤ 0)
              </p>
            )}
            {screener && screener.items.length > 0 && (
              <div className="overflow-hidden rounded border border-[#2a2e39]">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[#2a2e39] bg-[#131722]">
                      <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wide text-[#555]">종목</th>
                      <th className="px-3 py-2 text-right text-[10px] font-medium uppercase tracking-wide text-[#555]">현재 펀딩비</th>
                      <th className="px-3 py-2 text-right text-[10px] font-medium uppercase tracking-wide text-[#555]">연환산</th>
                      <th className="px-3 py-2 text-right text-[10px] font-medium uppercase tracking-wide text-[#555]">Half-life</th>
                      <th className="px-3 py-2 text-left text-[10px] font-medium uppercase tracking-wide text-[#555]">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {screener.items.map((item, i) => {
                      const isSelected = selected?.symbol === item.symbol;
                      return (
                        <tr
                          key={item.symbol}
                          onClick={() => setSelected(isSelected ? null : item)}
                          className={`cursor-pointer border-b border-[#2a2e39] transition-colors last:border-0 ${
                            isSelected
                              ? "bg-[#2962ff]/15"
                              : i % 2 === 0
                                ? "bg-[#1e222d] hover:bg-[#2a2e39]"
                                : "bg-[#131722] hover:bg-[#2a2e39]"
                          }`}
                        >
                          <td className="px-3 py-2.5">
                            <div className="flex items-center gap-1.5">
                              {isSelected && <span className="text-[#2962ff]">✓</span>}
                              <span className="font-semibold text-[#d1d4dc]">
                                {item.symbol.replace("USDT", "")}
                              </span>
                              <span className="text-[#555]">USDT</span>
                            </div>
                          </td>
                          <td className="px-3 py-2.5 text-right font-mono text-[#26a69a]">
                            {item.current_rate_pct.toFixed(4)}%
                          </td>
                          <td className="px-3 py-2.5 text-right font-mono text-[#26a69a]">
                            {item.annualized_pct.toFixed(1)}%
                          </td>
                          <td className="px-3 py-2.5 text-right font-mono text-[#9aa0ad]">
                            {item.half_life_settlements.toFixed(1)}회
                          </td>
                          <td className="px-3 py-2.5">
                            <div className="flex items-center gap-2">
                              <ScoreBar score={item.score} />
                              <span
                                className={`rounded px-1.5 py-0.5 text-[9px] font-medium ${
                                  item.score >= 1
                                    ? "bg-[#26a69a]/15 text-[#26a69a]"
                                    : "bg-[#2a2e39] text-[#868993]"
                                }`}
                              >
                                {item.score >= 1 ? "진입 가능" : "대기"}
                              </span>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Config (symbol selected) */}
          {selected && (
            <div className="mt-5 space-y-4">
              {/* Selected symbol card */}
              <div className="flex items-center justify-between rounded border border-[#2962ff]/40 bg-[#2962ff]/10 px-4 py-3">
                <div>
                  <p className="text-xs text-[#868993]">선택된 종목</p>
                  <p className="text-sm font-bold text-[#d1d4dc]">{selected.symbol}</p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-[#868993]">연환산 펀딩비</p>
                  <p className="text-sm font-semibold text-[#26a69a]">
                    {selected.annualized_pct.toFixed(1)}% / yr
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-xs text-[#868993]">Half-life</p>
                  <p className="text-sm font-semibold text-[#d1d4dc]">
                    {selected.half_life_settlements.toFixed(1)} 회
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setSelected(null)}
                  className="ml-2 text-[11px] text-[#555] hover:text-[#ef5350]"
                >
                  ✕
                </button>
              </div>

              {/* Hold days selector (검증 모드에서는 숨김) */}
              {!verifyMode && (
              <Field label="목표 유지 기간" description="기간에 따라 진입·청산 임계치가 자동 조정됩니다.">
                <div className="flex gap-2">
                  {[1, 3].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setHoldDays(d)}
                      className={`rounded border px-4 py-1.5 text-xs font-medium transition-colors ${
                        holdDays === d
                          ? "border-[#2962ff]/70 bg-[#2962ff]/20 text-[#2962ff]"
                          : "border-[#2a2e39] text-[#868993] hover:border-[#2962ff]/40 hover:text-[#d1d4dc]"
                      }`}
                    >
                      {d === 1 ? "1일 (단기)" : "3일 (장기)"}
                    </button>
                  ))}
                </div>
              </Field>
              )}

              {/* Auto-computed thresholds (read-only, 검증 모드에서는 숨김) */}
              {!verifyMode && deadband && (
                <div className="grid grid-cols-2 gap-2 rounded border border-[#2a2e39] bg-[#131722] p-3">
                  <div>
                    <p className="text-[10px] text-[#868993]">진입 임계치 (자동)</p>
                    <p className="mt-0.5 font-mono text-sm font-semibold text-[#f0b90b]">
                      {(deadband.entry).toFixed(5)}%
                    </p>
                    <p className="text-[10px] text-[#555]">
                      ≈ 연환산 {(deadband.entry * 1095).toFixed(2)}%
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] text-[#868993]">청산 임계치 (자동)</p>
                    <p className="mt-0.5 font-mono text-sm font-semibold text-[#868993]">
                      {(deadband.exit).toFixed(5)}%
                    </p>
                    <p className="text-[10px] text-[#555]">
                      ≈ 연환산 {(deadband.exit * 1095).toFixed(2)}%
                    </p>
                  </div>
                  <div className="col-span-2 mt-1 border-t border-[#2a2e39] pt-2">
                    <p className="text-[10px] text-[#555]">
                      왕복 수수료 {ROUNDTRIP_COST_PCT.toFixed(2)}% ÷ half-life {selected.half_life_settlements.toFixed(1)}회
                      = 진입 임계치 {(deadband.entry).toFixed(5)}%
                    </p>
                  </div>
                </div>
              )}

              {/* Seed + Env */}
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="할당 시드 (USDT)" description="현물 매수에 사용할 자본">
                  <input
                    type="number"
                    className="w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                    min={10}
                    step={100}
                    value={allocatedUsdt}
                    onChange={(e) => setAllocatedUsdt(Number(e.target.value))}
                  />
                </Field>
                <Field
                  label="환경 (Environment)"
                  description="Testnet은 바이낸스 데모 트레이딩 키를 사용합니다."
                >
                  <select
                    className="w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                    value={env}
                    onChange={(e) => setEnv(e.target.value as "mainnet" | "testnet")}
                  >
                    <option value="testnet">Testnet (Demo Trading)</option>
                    <option value="mainnet">Mainnet (실거래)</option>
                  </select>
                </Field>
              </div>

              {/* 즉시 검증 모드 */}
              <div className="rounded border border-[#f0b90b]/30 bg-[#f0b90b]/5 p-3">
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 accent-[#f0b90b]"
                    checked={verifyMode}
                    onChange={(e) => setVerifyMode(e.target.checked)}
                  />
                  <div>
                    <p className="text-xs font-semibold text-[#f0b90b]">⚡ 즉시 검증 모드</p>
                    <p className="mt-0.5 text-[11px] leading-relaxed text-[#9aa0ad]">
                      진입 임계치를 사실상 0(={VERIFY_ENTRY_PCT}%)으로 낮추고 자동 임계치 계산(목표 유지 기간)을
                      비활성화합니다. 펀딩비가 양수이기만 하면 다음 조회(≤10초)에 즉시 진입하여 현물 매수 +
                      선물 숏 실행이 정상 동작하는지 검증할 수 있습니다.
                    </p>
                    {verifyMode && env === "mainnet" && (
                      <p className="mt-2 rounded border border-[#ef5350]/40 bg-[#ef5350]/10 px-2 py-1.5 text-[11px] font-semibold text-[#ef5350]">
                        ⚠ Mainnet에서는 실제 자금으로 즉시 진입합니다. 검증은 Testnet에서 진행하세요.
                      </p>
                    )}
                  </div>
                </label>
              </div>

              {/* Advanced settings */}
              <div>
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  className="flex items-center gap-1 text-[11px] text-[#555] hover:text-[#9aa0ad]"
                >
                  <span>{showAdvanced ? "▾" : "▸"}</span>
                  고급 설정 (마진 리밸런싱)
                </button>
                {showAdvanced && (
                  <div className="mt-3 grid gap-4 sm:grid-cols-2">
                    <Field label="마진 위험 수위" description="유지마진/총마진 초과 시 현물→선물 이체">
                      <div className="flex items-center gap-2">
                        <input
                          type="range"
                          className="h-2 w-full accent-[#f0b90b]"
                          min={0.5}
                          max={0.95}
                          step={0.01}
                          value={marginAlertRatio}
                          onChange={(e) => setMarginAlertRatio(Number(e.target.value))}
                        />
                        <span className="w-10 text-right text-xs font-mono text-[#d1d4dc]">
                          {(marginAlertRatio * 100).toFixed(0)}%
                        </span>
                      </div>
                    </Field>
                    <Field label="리밸런싱 이체 비율" description="현물 잔고의 몇 %를 선물로 이체할지">
                      <div className="flex items-center gap-2">
                        <input
                          type="range"
                          className="h-2 w-full accent-[#2962ff]"
                          min={0.05}
                          max={0.5}
                          step={0.05}
                          value={rebalancePct}
                          onChange={(e) => setRebalancePct(Number(e.target.value))}
                        />
                        <span className="w-10 text-right text-xs font-mono text-[#d1d4dc]">
                          {(rebalancePct * 100).toFixed(0)}%
                        </span>
                      </div>
                    </Field>
                  </div>
                )}
              </div>

              {/* Start button */}
              {!verifyMode && selectedBelowThreshold && (
                <p className="rounded border border-[#f0b90b]/30 bg-[#f0b90b]/10 px-3 py-2 text-[11px] leading-relaxed text-[#f0b90b]">
                  ⏳ 현재 {selected.symbol} 펀딩비(score {selected.score.toFixed(2)}×)가 진입 임계치 미만입니다.
                  지금 시작해도 즉시 진입하지 않고, 펀딩비가 임계치를 넘을 때까지 대기합니다.
                </p>
              )}
              <button
                type="button"
                disabled={busy || !selected || running}
                onClick={handleStart}
                className={`w-full rounded border px-4 py-2.5 text-sm font-semibold transition-colors disabled:opacity-50 ${
                  verifyMode
                    ? "border-[#f0b90b]/50 bg-[#f0b90b]/10 text-[#f0b90b] hover:bg-[#f0b90b]/20"
                    : "border-[#26a69a]/50 bg-[#26a69a]/10 text-[#26a69a] hover:bg-[#26a69a]/20"
                }`}
              >
                {running
                  ? "이미 실행 중 (먼저 정지하세요)"
                  : busy
                    ? "시작 중…"
                    : verifyMode
                      ? `⚡ ${selected.symbol} 즉시 검증 진입`
                      : `▶ ${selected.symbol} 차익거래 시작`}
              </button>
            </div>
          )}

          {!selected && !screenerLoading && (
            <p className="mt-4 text-center text-xs text-[#555]">
              위 스크리너에서 종목을 클릭하면 자동으로 파라미터가 설정됩니다.
            </p>
          )}
        </div>

        {/* ── RIGHT: Running 중인 전략 ── */}
        <div>
          {running && status ? (
            <div className="rounded-lg border border-[#26a69a]/30 bg-[#131722] p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center gap-1 rounded bg-[#26a69a]/15 px-2 py-0.5 text-xs font-medium text-[#26a69a]">
                      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#26a69a]" />
                      Running
                    </span>
                    <span className="truncate font-mono text-sm font-semibold text-[#d1d4dc]">
                      {status.symbol}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-[#868993]">
                    {awaitingEntry
                      ? "진입 대기 중 · 펀딩비가 임계치 초과 시 자동 체결"
                      : "현물 롱 + 선물 숏 · Delta-Neutral 펀딩비 수취"}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={handleStop}
                  className="shrink-0 rounded border border-[#ef5350]/50 bg-[#ef5350]/10 px-4 py-2 text-sm font-medium text-[#ef5350] transition-colors hover:bg-[#ef5350]/20 disabled:opacity-50"
                >
                  {busy ? "..." : "Stop"}
                </button>
              </div>

              <div className="mt-4 grid grid-cols-2 gap-2">
                <Stat label="Funding Rate (Ann.)" value={fmtPct(annPct ?? null)} valueClass={fundingColor} />
                <Stat
                  label="Unrealized PnL"
                  value={
                    status.unrealized_pnl != null
                      ? `${status.unrealized_pnl >= 0 ? "+" : ""}$${fmt2(status.unrealized_pnl)}`
                      : "—"
                  }
                  valueClass={pnlColor}
                  hint="양 레그 평가손익 합 · 수수료·펀딩 미포함"
                />
                <Stat
                  label="현물 롱 (Spot Long)"
                  value={
                    status.spot_qty
                      ? `+${status.spot_qty.toFixed(5)} ${baseAsset}`
                      : "대기 중"
                  }
                  valueClass={status.spot_qty ? "text-[#26a69a]" : "text-[#868993]"}
                />
                <Stat
                  label="선물 숏 (Perp Short)"
                  value={
                    status.futures_short_qty
                      ? `-${status.futures_short_qty.toFixed(5)} ${baseAsset}`
                      : "대기 중"
                  }
                  valueClass={status.futures_short_qty ? "text-[#ef5350]" : "text-[#868993]"}
                />
                <div className="col-span-2">
                  <Stat
                    label="Funding Income"
                    value={`$${fmt2(status.accumulated_funding_income)}`}
                    valueClass="text-[#26a69a]"
                    hint={
                      fundingCountdown
                        ? `다음 펀딩 정산까지 ${fundingCountdown} · 정산 시 수취`
                        : "선물 숏 보유 시 펀딩 정산마다 수취"
                    }
                  />
                </div>
                <Stat
                  label="진입 수수료 (Entry Fee)"
                  value={status.entry_fee != null ? `-$${fmt2(status.entry_fee)}` : "—"}
                  valueClass={status.entry_fee ? "text-[#ef5350]" : "text-[#868993]"}
                  hint="현물 매수 + 선물 숏 체결 수수료 합"
                />
                <Stat
                  label="청산 수수료 (Exit Fee)"
                  value={status.exit_fee != null ? `-$${fmt2(status.exit_fee)}` : "—"}
                  valueClass={status.exit_fee ? "text-[#ef5350]" : "text-[#868993]"}
                  hint="현물 매도 + 선물 매수 체결 수수료 합"
                />
                <div className="col-span-2">
                  <Stat
                    label="총 수수료 (Total Fees)"
                    value={`-$${fmt2((status.entry_fee ?? 0) + (status.exit_fee ?? 0))}`}
                    valueClass="text-[#d1d4dc]"
                    hint="진입 + 청산 수수료 합 · 펀딩 수익에서 차감되는 실비용"
                  />
                </div>
              </div>

              {status.last_error && (
                <p className="mt-3 rounded border border-[#ef5350]/40 bg-[#ef5350]/10 px-3 py-2 text-[11px] leading-relaxed text-[#ef5350]">
                  ⚠ 진입 차단: {status.last_error}
                </p>
              )}

              {awaitingEntry && !status.last_error && (
                <p className="mt-3 rounded border border-[#f0b90b]/30 bg-[#f0b90b]/10 px-3 py-2 text-[11px] leading-relaxed text-[#f0b90b]">
                  ⏳ 봇이 정상 가동 중이며 진입 신호를 대기하고 있습니다. 현재 펀딩비가 최소 진입 임계치
                  {annPct != null ? ` (현재 연환산 ${fmtPct(annPct)})` : ""}를 넘지 않아 수수료 휩소를 피하기 위해
                  포지션을 열지 않습니다. 임계치를 넘으면 자동으로 체결합니다.
                </p>
              )}
            </div>
          ) : (
            <div className="flex h-full min-h-[160px] flex-col items-center justify-center rounded-lg border border-dashed border-[#2a2e39] bg-[#131722]/40 p-6 text-center">
              <p className="text-sm font-medium text-[#9aa0ad]">실행 중인 전략이 없습니다</p>
              <p className="mt-1 text-xs text-[#555]">
                좌측 스크리너에서 종목을 선택하고 시작하면 이곳에 실시간 현황이 표시됩니다.
              </p>
            </div>
          )}
        </div>
      </div>

      <p className="mt-4 text-[11px] leading-relaxed text-[#555]">
        ⚠ 진입 전 Binance Hedge Mode 활성화 필요. 현물 지갑에 충분한 USDT가 있어야 합니다.
      </p>
    </section>
  );
}

function Stat({
  label,
  value,
  valueClass = "text-[#d1d4dc]",
  hint,
}: {
  label: string;
  value: string;
  valueClass?: string;
  hint?: string;
}) {
  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] p-2.5">
      <p className="text-[10px] text-[#868993]">{label}</p>
      <p className={`mt-0.5 text-sm font-semibold ${valueClass}`}>{value}</p>
      {hint && <p className="mt-0.5 text-[10px] leading-snug text-[#6b7383]">{hint}</p>}
    </div>
  );
}

function Field({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-[11px] font-medium text-[#9aa0ad]">{label}</p>
      {description && <p className="text-[11px] leading-snug text-[#6b7383]">{description}</p>}
      {children}
    </div>
  );
}

