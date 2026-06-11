"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR, { useSWRConfig } from "swr";

import {
  getBinanceAccountSummary,
  getManualLiveOrderSizing,
  submitManualLiveOrder,
} from "@/lib/api";
import type {
  BinanceAccountSummary,
  BinancePositionSummary,
  Job,
  ManualLiveOrderSizingResponse,
} from "@/lib/types";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function extractSymbols(config: Record<string, unknown> | null | undefined): string[] {
  if (!config) return [];
  const symbols: string[] = [];
  const streams = Array.isArray(config.streams) ? config.streams : [];
  for (const raw of streams) {
    if (!isRecord(raw)) continue;
    const symbol = typeof raw.symbol === "string" ? raw.symbol.trim().toUpperCase() : "";
    if (symbol && !symbols.includes(symbol)) symbols.push(symbol);
  }
  if (symbols.length === 0 && typeof config.symbol === "string") {
    const symbol = config.symbol.trim().toUpperCase();
    if (symbol) symbols.push(symbol);
  }
  return symbols;
}

function extractEnv(config: Record<string, unknown> | null | undefined): "mainnet" | "testnet" {
  return config?.env === "testnet" ? "testnet" : "mainnet";
}

function baseAsset(symbol: string): string {
  return symbol.endsWith("USDT") ? symbol.slice(0, -4) : "base";
}

function formatNumber(value: number, digits = 6): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  });
}

function formatInputNumber(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "";
  const digits = value >= 1 ? 2 : 8;
  return value.toFixed(digits).replace(/\.?0+$/, "");
}

export function LiveManualTradePanel({
  job,
  active,
}: {
  job: Job;
  active: boolean;
}) {
  const { mutate } = useSWRConfig();
  const config = isRecord(job.config) ? job.config : null;
  const symbols = useMemo(() => extractSymbols(config), [config]);
  const accountEnv = useMemo(() => extractEnv(config), [config]);
  const [symbol, setSymbol] = useState(symbols[0] ?? "BTCUSDT");
  const [side, setSide] = useState<"LONG" | "SHORT">("LONG");
  const [entryNotional, setEntryNotional] = useState("");
  const [useMaxEntry, setUseMaxEntry] = useState(false);
  const [submitting, setSubmitting] = useState<"ENTER" | "CLOSE" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (symbols.length > 0 && !symbols.includes(symbol)) {
      setSymbol(symbols[0]);
    }
  }, [symbol, symbols]);

  const snapshotKey = ["binanceAccountSummary", accountEnv, job.wallet_account_id ?? "default"];
  const { data: snapshot } = useSWR<BinanceAccountSummary>(
    active ? snapshotKey : null,
    () => getBinanceAccountSummary({ env: accountEnv, walletAccountId: job.wallet_account_id }),
    { dedupingInterval: 5_000 },
  );

  const currentPosition = useMemo<BinancePositionSummary | null>(() => {
    const positions = snapshot?.positions ?? [];
    return positions.find((position) => position.symbol.toUpperCase() === symbol) ?? null;
  }, [snapshot, symbol]);

  const sizingKey = ["manualLiveOrderSizing", job.job_id, symbol, side];
  const { data: sizing } = useSWR<ManualLiveOrderSizingResponse>(
    active && job.status === "RUNNING" ? sizingKey : null,
    () => getManualLiveOrderSizing(job.job_id, symbol, side),
    { refreshInterval: 5_000, dedupingInterval: 3_000 },
  );

  const positionSize = currentPosition?.position_amt ?? 0;
  const hasPosition = Math.abs(positionSize) > 1e-12;
  const canSubmit = active && job.status === "RUNNING" && !submitting;
  const parsedNotional = Number(entryNotional);
  const hasValidNotional = Number.isFinite(parsedNotional) && parsedNotional > 0;
  const maxNotional = sizing?.max_notional_usdt ?? null;
  const canUseMax = canSubmit && maxNotional !== null && maxNotional > 0;
  const estimatedQuantity =
    useMaxEntry && sizing
      ? sizing.max_quantity
      : hasValidNotional && sizing && sizing.mark_price > 0
        ? parsedNotional / sizing.mark_price
        : null;

  const updateSymbol = (nextSymbol: string) => {
    setSymbol(nextSymbol);
    setUseMaxEntry(false);
  };

  const updateSide = (nextSide: "LONG" | "SHORT") => {
    setSide(nextSide);
    setUseMaxEntry(false);
  };

  const updateEntryNotional = (nextValue: string) => {
    setEntryNotional(nextValue);
    setUseMaxEntry(false);
  };

  const fillMaxEntry = () => {
    if (!sizing || sizing.max_notional_usdt <= 0) {
      setError("현재 잔고와 포지션 한도 기준으로 추가 진입 가능한 금액이 없습니다.");
      return;
    }
    setError(null);
    setMessage(null);
    setEntryNotional(formatInputNumber(sizing.max_notional_usdt));
    setUseMaxEntry(true);
  };

  const submit = async (action: "ENTER" | "CLOSE") => {
    setError(null);
    setMessage(null);
    if (action === "ENTER" && !useMaxEntry && !hasValidNotional) {
      setError("진입 금액(USDT)을 0보다 크게 입력해주세요.");
      return;
    }
    if (action === "ENTER" && useMaxEntry && (!sizing || sizing.max_notional_usdt <= 0)) {
      setError("현재 잔고와 포지션 한도 기준으로 추가 진입 가능한 금액이 없습니다.");
      return;
    }
    if (action === "CLOSE" && !hasPosition) {
      setError(`${symbol}에 청산할 포지션이 없습니다.`);
      return;
    }

    const entrySummary = useMaxEntry && sizing
      ? `최대 ${formatNumber(sizing.max_notional_usdt, 2)} USDT`
      : `${formatNumber(parsedNotional, 2)} USDT`;
    const quantitySummary = estimatedQuantity !== null
      ? ` (예상 ${formatNumber(estimatedQuantity)} ${baseAsset(symbol)})`
      : "";
    const confirmText = action === "ENTER"
      ? `${symbol} ${side} 시장가 진입 주문을 전송할까요? 진입 금액: ${entrySummary}${quantitySummary}`
      : `${symbol} 현재 포지션을 시장가로 전체 청산할까요?`;
    if (!window.confirm(confirmText)) return;

    setSubmitting(action);
    try {
      const result = await submitManualLiveOrder(job.job_id, {
        action,
        symbol,
        ...(action === "ENTER"
          ? useMaxEntry
            ? { side, use_max: true }
            : { side, notional_usdt: parsedNotional }
          : {}),
      });
      const resultNotional = result.notional_usdt
        ?? (typeof result.mark_price === "number" ? result.quantity * result.mark_price : null);
      const resultNotionalText = resultNotional !== null
        ? ` · ${formatNumber(resultNotional, 2)} USDT`
        : "";
      setMessage(
        `${action === "ENTER" ? "진입" : "청산"} 주문 전송 완료: ${result.side} ${formatNumber(result.quantity)} ${baseAsset(symbol)}${resultNotionalText}`,
      );
      setEntryNotional("");
      setUseMaxEntry(false);
      await Promise.all([
        mutate(snapshotKey),
        mutate(sizingKey),
        mutate(["trades", job.job_id]),
        mutate(["job", job.job_id]),
      ]);
    } catch (exc) {
      setError(String(exc));
    } finally {
      setSubmitting(null);
    }
  };

  return (
    <section className="mt-4 rounded border border-[#2a2e39] bg-[#1e222d] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-[#d1d4dc]">수동 진입 / 청산</div>
          <p className="mt-1 text-xs text-[#868993]">
            이 LIVE job의 거래 계정으로 Binance USD-M 시장가 주문을 전송합니다.
          </p>
        </div>
        <span className={`rounded px-2 py-1 text-xs ${canSubmit ? "bg-[#26a69a]/20 text-[#26a69a]" : "bg-[#2a2e39] text-[#868993]"}`}>
          {job.status}
        </span>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto_auto]">
        <label className="text-xs text-[#868993]">
          종목
          <select
            className="mt-1 w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
            value={symbol}
            onChange={(event) => updateSymbol(event.target.value)}
          >
            {(symbols.length > 0 ? symbols : [symbol]).map((item) => (
              <option key={item} value={item} className="bg-[#131722]">
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-[#868993]">
          방향
          <select
            className="mt-1 w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
            value={side}
            onChange={(event) => updateSide(event.target.value as "LONG" | "SHORT")}
          >
            <option value="LONG" className="bg-[#131722]">Long 진입</option>
            <option value="SHORT" className="bg-[#131722]">Short 진입</option>
          </select>
        </label>
        <label className="text-xs text-[#868993]">
          <span className="flex items-center justify-between gap-2">
            <span>진입 금액 (USDT)</span>
            <button
              className="rounded border border-[#2962ff]/60 px-2 py-0.5 text-[11px] text-[#9bb5ff] hover:bg-[#2962ff]/10 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!canUseMax}
              onClick={fillMaxEntry}
              type="button"
            >
              Max
            </button>
          </span>
          <input
            className="mt-1 w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
            inputMode="decimal"
            placeholder={maxNotional !== null && maxNotional > 0
              ? `최대 ${formatInputNumber(maxNotional)} USDT`
              : "예: 100"}
            value={entryNotional}
            onChange={(event) => updateEntryNotional(event.target.value)}
          />
        </label>
        <button
          className="self-end rounded border border-[#2962ff] bg-[#2962ff] px-4 py-2 text-sm text-white transition-colors hover:bg-[#1e53d5] disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!canSubmit}
          onClick={() => void submit("ENTER")}
          type="button"
        >
          {submitting === "ENTER" ? "전송 중..." : "수동 진입"}
        </button>
        <button
          className="self-end rounded border border-[#ef5350] bg-[#ef5350] px-4 py-2 text-sm text-white transition-colors hover:bg-[#d32f2f] disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!canSubmit || !hasPosition}
          onClick={() => void submit("CLOSE")}
          type="button"
        >
          {submitting === "CLOSE" ? "전송 중..." : "전체 청산"}
        </button>
      </div>

      <div className="mt-3 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-xs text-[#868993]">
        현재 포지션:{" "}
        {hasPosition && currentPosition ? (
          <span className="text-[#d1d4dc]">
            {positionSize > 0 ? "LONG" : "SHORT"} {formatNumber(Math.abs(positionSize))} {baseAsset(symbol)}
            {" · "}미실현 {formatNumber(currentPosition.unrealized_pnl, 2)} USDT
          </span>
        ) : (
          <span>없음</span>
        )}
      </div>
      <div className="mt-2 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-xs text-[#868993]">
        {sizing ? (
          <>
            <span className="text-[#d1d4dc]">
              진입 가능: 최대 {formatNumber(sizing.max_notional_usdt, 2)} USDT
              {" · "}약 {formatNumber(sizing.max_quantity)} {baseAsset(symbol)}
            </span>
            <span>
              {" · "}잔고 {formatNumber(sizing.available_balance, 2)} USDT
              {" · "}레버리지 {formatNumber(sizing.leverage, 2)}x
              {" · "}최대 포지션 {(sizing.max_position * 100).toFixed(0)}%
            </span>
            {estimatedQuantity !== null ? (
              <span>
                {" · "}입력 환산 약 {formatNumber(estimatedQuantity)} {baseAsset(symbol)}
              </span>
            ) : null}
          </>
        ) : (
          <span>진입 가능 금액 계산 중...</span>
        )}
      </div>
      {message ? <p className="mt-2 text-xs text-[#26a69a]">{message}</p> : null}
      {error ? <p className="mt-2 text-xs text-[#ef5350]">{error}</p> : null}
    </section>
  );
}
