"use client";

import { useState } from "react";
import useSWR from "swr";
import { getWalletSyncStatus, syncWalletAccounts } from "@/lib/api";
import type { WalletSyncSummary } from "@/lib/types";

function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  if (!t || Number.isNaN(t)) return iso;
  const diff = (Date.now() - t) / 1000;
  if (diff < 60) return `${Math.max(1, Math.floor(diff))}초 전`;
  if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}시간 전`;
  return new Date(iso).toLocaleString();
}

function KV({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "ok" | "warn";
}): React.JSX.Element {
  const cls =
    tone === "ok"
      ? "text-[#26a69a]"
      : tone === "warn"
        ? "text-[#F0B90B]"
        : "text-[#d1d4dc]";
  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-[#868993]">
        {label}
      </div>
      <div className={`mt-1 text-sm font-semibold ${cls}`}>{value}</div>
    </div>
  );
}

export function WalletSyncCard({
  onSynced,
  compact = false,
}: {
  onSynced?: () => void | Promise<void>;
  compact?: boolean;
}): React.JSX.Element {
  const { data, mutate, isLoading } = useSWR<WalletSyncSummary | null>(
    "walletSyncStatus",
    () => getWalletSyncStatus(),
    { refreshInterval: 60_000 },
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSync = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const result = await syncWalletAccounts("mainnet");
      await mutate(result, { revalidate: false });
      if (onSynced) await onSynced();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const last = data?.ts ? formatRelative(data.ts) : "기록 없음";
  const drift =
    (data?.marked_missing.length ?? 0) +
    (data?.marked_disabled.length ?? 0) +
    (data?.unmanaged_binance_subs.length ?? 0);

  return (
    <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[#d1d4dc]">
            Binance 동기화
          </h2>
          {!compact ? (
            <p className="mt-1 text-xs text-[#868993]">
              Binance는 서브 계정 삭제 API와 webhook을 제공하지 않습니다. 5분
              주기 polling으로 앱과 거래소를 자동 reconcile하며, 아래 버튼으로
              즉시 동기화할 수도 있습니다.
            </p>
          ) : (
            <p className="mt-1 text-xs text-[#868993]">
              마지막 동기화: <span className="text-[#d1d4dc]">{last}</span>
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={() => void handleSync()}
          disabled={busy || isLoading}
          className="shrink-0 rounded border border-[#2962ff]/30 bg-[#2962ff]/10 px-3 py-1.5 text-xs font-semibold text-[#2962ff] hover:bg-[#2962ff]/20 disabled:opacity-50"
        >
          {busy ? "동기화 중…" : "지금 동기화"}
        </button>
      </div>
      {!compact ? (
        <div className="mt-3 grid grid-cols-2 gap-3 text-xs text-[#d1d4dc] md:grid-cols-4">
          <KV label="마지막 동기화" value={last} />
          <KV label="Binance 서브" value={String(data?.binance_subs ?? "—")} />
          <KV label="앱 서브" value={String(data?.db_subs ?? "—")} />
          <KV
            label="drift"
            value={drift > 0 ? `${drift}건` : "없음"}
            tone={drift > 0 ? "warn" : "ok"}
          />
        </div>
      ) : null}
      {data?.error ? (
        <p className="mt-2 text-xs text-[#ef5350]">동기화 실패: {data.error}</p>
      ) : null}
      {error ? <p className="mt-2 text-xs text-[#ef5350]">{error}</p> : null}
      {data?.unmanaged_binance_subs.length ? (
        <p className="mt-2 text-xs text-[#F0B90B]">
          앱에 등록되지 않은 Binance 서브:{" "}
          <span className="font-mono">
            {data.unmanaged_binance_subs.join(", ")}
          </span>
        </p>
      ) : null}
    </section>
  );
}
