"use client";

import { useState } from "react";
import useSWR from "swr";
import { getFundingArbStatus, startFundingArb, stopFundingArb } from "@/lib/api";
import type { FundingArbitrageParams } from "@/lib/types";

const REFRESH_MS = 15_000;

const DEFAULT_PARAMS: FundingArbitrageParams = {
  symbol: "BTCUSDT",
  env: "testnet",
  allocated_usdt: 1000,
  entry_deadband_pct: 0.15,
  exit_deadband_pct: 0.05,
  margin_alert_ratio: 0.80,
  rebalance_transfer_pct: 0.20,
};

function fmt2(v: number) {
  return v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v: number | null, digits = 2) {
  if (v === null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

export function ArbitrageConfigPanel() {
  const [params, setParams] = useState<FundingArbitrageParams>(DEFAULT_PARAMS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: status, mutate } = useSWR("funding-arb-status", getFundingArbStatus, {
    refreshInterval: REFRESH_MS,
  });

  const running = status?.running ?? false;

  const handleStart = async () => {
    setBusy(true);
    setError(null);
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

  const set = <K extends keyof FundingArbitrageParams>(key: K, val: FundingArbitrageParams[K]) =>
    setParams((p) => ({ ...p, [key]: val }));

  const annPct = status?.annualized_funding_pct;
  const pnlColor =
    (status?.unrealized_pnl ?? 0) >= 0 ? "text-[#26a69a]" : "text-[#ef5350]";
  const fundingColor =
    annPct !== null && annPct !== undefined && annPct > 0 ? "text-[#26a69a]" : "text-[#ef5350]";

  return (
    <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-[#d1d4dc]">
              Funding Rate Arbitrage
            </span>
            <span
              className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${
                running
                  ? "bg-[#26a69a]/15 text-[#26a69a]"
                  : "bg-[#2a2e39] text-[#868993]"
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

        <button
          type="button"
          disabled={busy}
          onClick={running ? handleStop : handleStart}
          className={`rounded px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 ${
            running
              ? "border border-[#ef5350]/50 bg-[#ef5350]/10 text-[#ef5350] hover:bg-[#ef5350]/20"
              : "border border-[#26a69a]/50 bg-[#26a69a]/10 text-[#26a69a] hover:bg-[#26a69a]/20"
          }`}
        >
          {busy ? "..." : running ? "Stop" : "Start"}
        </button>
      </div>

      {error && (
        <p className="mt-3 rounded border border-[#ef5350]/40 bg-[#ef5350]/10 px-3 py-2 text-xs text-[#ef5350]">
          {error}
        </p>
      )}

      {/* Live Status */}
      {running && status && (
        <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Funding Rate (Ann.)" value={fmtPct(annPct ?? null)} valueClass={fundingColor} />
          <Stat
            label="Unrealized PnL"
            value={
              status.unrealized_pnl !== null
                ? `${status.unrealized_pnl >= 0 ? "+" : ""}$${fmt2(status.unrealized_pnl)}`
                : "—"
            }
            valueClass={pnlColor}
          />
          <Stat
            label="Funding Income"
            value={`$${fmt2(status.accumulated_funding_income)}`}
            valueClass="text-[#26a69a]"
          />
          <Stat
            label="Position"
            value={
              status.spot_qty
                ? `${status.spot_qty.toFixed(5)} ${status.symbol ?? ""}`
                : "—"
            }
          />
        </div>
      )}

      {/* Config Form (only when not running) */}
      {!running && (
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <Field
            label="심볼"
            description="차익거래 대상 코인 (USDT 페어)"
          >
            <input
              type="text"
              className="w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              value={params.symbol}
              onChange={(e) => set("symbol", e.target.value.toUpperCase())}
            />
          </Field>

          <Field
            label="환경 (Environment)"
            description="사용할 API 키 환경. Testnet은 테스트넷 선물+현물 키를 함께 사용합니다."
          >
            <select
              className="w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              value={params.env}
              onChange={(e) => set("env", e.target.value as "mainnet" | "testnet")}
            >
              <option value="testnet">Testnet (Futures + Spot 테스트넷)</option>
              <option value="mainnet">Mainnet (실거래)</option>
            </select>
          </Field>

          <Field
            label="할당 시드 (USDT)"
            description="현물 매수에 사용할 자본"
          >
            <input
              type="number"
              className="w-full rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
              min={10}
              step={100}
              value={params.allocated_usdt}
              onChange={(e) => set("allocated_usdt", Number(e.target.value))}
            />
          </Field>

          <Field
            label="진입 임계치 (%)"
            description="왕복 수수료+슬리피지 합계. 연환산 펀딩비가 이 값 초과 시 진입"
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                className="w-24 rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                min={0.01}
                max={1}
                step={0.01}
                value={params.entry_deadband_pct}
                onChange={(e) => set("entry_deadband_pct", Number(e.target.value))}
              />
              <span className="text-xs text-[#868993]">
                ≈ 연환산 {(params.entry_deadband_pct * 1095).toFixed(1)}%
              </span>
            </div>
          </Field>

          <Field
            label="청산 임계치 (%)"
            description="펀딩비가 이 값 이하로 하락 시 언와인딩"
          >
            <div className="flex items-center gap-2">
              <input
                type="number"
                className="w-24 rounded border border-[#2a2e39] bg-[#131722] px-2 py-1.5 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
                min={0}
                max={1}
                step={0.01}
                value={params.exit_deadband_pct}
                onChange={(e) => set("exit_deadband_pct", Number(e.target.value))}
              />
              <span className="text-xs text-[#868993]">
                ≈ 연환산 {(params.exit_deadband_pct * 1095).toFixed(1)}%
              </span>
            </div>
          </Field>

          <Field
            label="마진 위험 수위"
            description="선물 유지마진/총마진 비율이 이 이상이면 현물→선물 자동 이체"
          >
            <div className="flex items-center gap-2">
              <input
                type="range"
                className="h-2 w-full accent-[#f0b90b]"
                min={0.5}
                max={0.95}
                step={0.01}
                value={params.margin_alert_ratio}
                onChange={(e) => set("margin_alert_ratio", Number(e.target.value))}
              />
              <span className="w-10 text-right text-xs font-mono text-[#d1d4dc]">
                {(params.margin_alert_ratio * 100).toFixed(0)}%
              </span>
            </div>
          </Field>

          <Field
            label="리밸런싱 이체 비율"
            description="마진 위험 시 현물 잔고의 몇 %를 선물로 이체할지"
          >
            <div className="flex items-center gap-2">
              <input
                type="range"
                className="h-2 w-full accent-[#2962ff]"
                min={0.05}
                max={0.5}
                step={0.05}
                value={params.rebalance_transfer_pct}
                onChange={(e) => set("rebalance_transfer_pct", Number(e.target.value))}
              />
              <span className="w-10 text-right text-xs font-mono text-[#d1d4dc]">
                {(params.rebalance_transfer_pct * 100).toFixed(0)}%
              </span>
            </div>
          </Field>
        </div>
      )}

      {/* Caution */}
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
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] p-2.5">
      <p className="text-[10px] text-[#868993]">{label}</p>
      <p className={`mt-0.5 text-sm font-semibold ${valueClass}`}>{value}</p>
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
