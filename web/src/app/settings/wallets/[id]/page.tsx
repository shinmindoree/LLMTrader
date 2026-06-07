"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  getWalletAccount,
  updateWalletKeys,
  updateWalletMeta,
} from "@/lib/api";
import type { WalletAccount } from "@/lib/types";

function parseIpInput(raw: string): string[] {
  return raw
    .split(/[\s,]+/g)
    .map((s) => s.trim())
    .filter(Boolean);
}

function statusBadgeClasses(status: WalletAccount["status"]): string {
  switch (status) {
    case "active":
      return "border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]";
    case "key_missing":
      return "border-[#F0B90B]/30 bg-[#F0B90B]/10 text-[#F0B90B]";
    case "key_invalid":
    case "binance_missing":
      return "border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]";
    default:
      return "border-[#868993]/30 bg-[#868993]/10 text-[#868993]";
  }
}

function statusLabel(status: WalletAccount["status"]): string {
  switch (status) {
    case "active":
      return "Active";
    case "key_missing":
      return "Key Missing";
    case "key_invalid":
      return "Key Invalid";
    case "binance_missing":
      return "Binance Missing";
    case "disabled":
      return "Disabled";
    default:
      return status;
  }
}

function PermBadge({ on, label }: { on: boolean; label: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        on
          ? "border border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
          : "border border-[#2a2e39] bg-[#131722] text-[#4a4e59]"
      }`}
    >
      {label}
    </span>
  );
}

type Msg = { type: "success" | "error"; text: string } | null;

export default function SubAccountDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const {
    data: wallet,
    isLoading,
    error,
    mutate,
  } = useSWR<WalletAccount>(
    ["walletAccount", id],
    () => getWalletAccount(id),
  );

  if (isLoading) {
    return (
      <main className="w-full max-w-3xl px-6 py-10">
        <BackLink />
        <p className="mt-6 text-sm text-[#868993]">로딩 중…</p>
      </main>
    );
  }

  if (error || !wallet) {
    return (
      <main className="w-full max-w-3xl px-6 py-10">
        <BackLink />
        <p className="mt-6 text-sm text-[#ef5350]">
          서브 계정을 찾을 수 없습니다.
        </p>
      </main>
    );
  }

  return (
    <main className="w-full max-w-3xl px-6 py-10">
      <BackLink />
      <HeaderCard wallet={wallet} />
      <KeyEditor wallet={wallet} onSaved={() => void mutate()} />
      <MetaEditor wallet={wallet} onSaved={() => void mutate()} />
    </main>
  );
}

function BackLink() {
  return (
    <Link
      href="/settings?tab=sub"
      className="inline-flex items-center gap-1 text-xs text-[#868993] hover:text-[#d1d4dc]"
    >
      ← Sub accounts
    </Link>
  );
}

function HeaderCard({ wallet }: { wallet: WalletAccount }): React.JSX.Element {
  const perms = (wallet.enabled_wallets ?? {}) as Record<string, unknown>;
  return (
    <section className="mt-4 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[#d1d4dc]">{wallet.alias}</h1>
          <p className="mt-1 font-mono text-xs text-[#868993]">
            {wallet.sub_account_email ?? "—"}
          </p>
        </div>
        <span
          className={`shrink-0 rounded border px-2 py-1 text-[11px] font-semibold uppercase tracking-wide ${statusBadgeClasses(
            wallet.status,
          )}`}
        >
          {statusLabel(wallet.status)}
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
        <Field label="Env" value={wallet.env} />
        <Field label="Role" value={wallet.role} />
        <Field label="Purpose" value={wallet.purpose} />
        <Field
          label="Updated"
          value={
            wallet.updated_at
              ? new Date(wallet.updated_at).toLocaleString()
              : "—"
          }
        />
      </div>
      <div className="mt-4">
        <p className="mb-2 text-[10px] uppercase tracking-wide text-[#868993]">
          Permissions <span className="normal-case text-[#4a4e59]">(Binance 동기화)</span>
        </p>
        <div className="flex flex-wrap gap-2">
          <PermBadge on={Boolean(perms.spot)} label="Spot" />
          <PermBadge on={Boolean(perms.futures_um)} label="Futures" />
          <PermBadge on={Boolean(perms.margin)} label="Margin" />
          <PermBadge on={Boolean(perms.options)} label="Options" />
        </div>
        <p className="mt-2 text-[10px] leading-snug text-[#4a4e59]">
          권한은 Binance 콘솔에서만 변경할 수 있으며, 5분 주기 동기화 또는 Sub
          accounts 탭의 &ldquo;지금 동기화&rdquo;로 반영됩니다.
        </p>
      </div>
    </section>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-[#868993]">
        {label}
      </div>
      <div className="mt-0.5 text-sm font-medium text-[#d1d4dc]">{value}</div>
    </div>
  );
}

function KeyEditor({
  wallet,
  onSaved,
}: {
  wallet: WalletAccount;
  onSaved: () => void;
}): React.JSX.Element {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [ipInput, setIpInput] = useState(
    (wallet.ip_whitelist ?? []).join(", "),
  );
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    setIpInput((wallet.ip_whitelist ?? []).join(", "));
  }, [wallet.ip_whitelist]);

  const ips = useMemo(() => parseIpInput(ipInput), [ipInput]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      setMsg({ type: "error", text: "API Key와 Secret을 모두 입력하세요." });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await updateWalletKeys(wallet.id, {
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        ip_whitelist: ips.length > 0 ? ips : undefined,
        mark_active: true,
      });
      setApiKey("");
      setApiSecret("");
      setMsg({
        type: "success",
        text: "API Key가 저장되었고 IP Whitelist가 Binance에 등록되었습니다.",
      });
      onSaved();
    } catch (err) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "저장 실패",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="mt-6 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <h2 className="text-base font-semibold text-[#d1d4dc]">거래용 API Key</h2>
      <p className="mt-1 text-xs leading-snug text-[#868993]">
        Binance 콘솔의{" "}
        <strong className="text-[#d1d4dc]">Sub-Account → API Management</strong>{" "}
        에서 발급한 키를 입력합니다. IP Whitelist는 저장 시 마스터 키 권한으로
        해당 서브 계정 API 키에 자동 등록됩니다.
      </p>

      {wallet.api_key_masked && (
        <div className="mt-3 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
          <div className="text-[10px] uppercase tracking-wide text-[#868993]">
            현재 등록된 키
          </div>
          <div className="mt-0.5 font-mono text-xs text-[#d1d4dc]">
            {wallet.api_key_masked}
          </div>
        </div>
      )}

      {msg && (
        <div
          className={`mt-3 rounded px-3 py-2 text-xs ${
            msg.type === "success"
              ? "border border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
              : "border border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]"
          }`}
        >
          {msg.text}
        </div>
      )}

      <form onSubmit={handleSave} className="mt-3 space-y-3">
        <input
          type="password"
          autoComplete="off"
          placeholder="API Key"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <input
          type="password"
          autoComplete="off"
          placeholder="API Secret"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
        />
        <div className="space-y-1.5">
          <label className="text-[11px] uppercase tracking-wide text-[#868993]">
            IP Whitelist
          </label>
          <textarea
            autoComplete="off"
            placeholder="예) 1.2.3.4, 5.6.7.8"
            className="h-20 w-full resize-none rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 font-mono text-xs text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none"
            value={ipInput}
            onChange={(e) => setIpInput(e.target.value)}
          />
          <p className="text-[10px] leading-snug text-[#4a4e59]">
            저장 버튼을 누르면 마스터 키 권한으로 이 서브 API 키에 IP 제한이
            자동 등록됩니다. 비워두면 Binance 콘솔에서 직접 관리하는 상태가
            유지됩니다.
          </p>
        </div>
        <button
          type="submit"
          disabled={saving || !apiKey.trim() || !apiSecret.trim()}
          className="w-full rounded bg-[#2962ff] px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "저장 중..." : "키 저장 + IP 등록"}
        </button>
      </form>
    </section>
  );
}

const PURPOSE_OPTIONS: { value: string; label: string }[] = [
  { value: "generic", label: "Generic" },
  { value: "directional", label: "Directional" },
  { value: "arbitrage", label: "Arbitrage" },
  { value: "derivatives", label: "Derivatives" },
  { value: "earn", label: "Earn" },
];

function MetaEditor({
  wallet,
  onSaved,
}: {
  wallet: WalletAccount;
  onSaved: () => void;
}): React.JSX.Element {
  const [purpose, setPurpose] = useState<string>(wallet.purpose);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);

  useEffect(() => {
    setPurpose(wallet.purpose);
  }, [wallet.purpose]);

  const handleSave = async () => {
    if (purpose === wallet.purpose) return;
    setSaving(true);
    setMsg(null);
    try {
      await updateWalletMeta(wallet.id, { purpose });
      setMsg({ type: "success", text: "용도가 업데이트되었습니다." });
      onSaved();
    } catch (err) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "저장 실패",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="mt-6 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <h2 className="text-base font-semibold text-[#d1d4dc]">전략 용도</h2>
      <p className="mt-1 text-xs leading-snug text-[#868993]">
        이 서브 계정에 어떤 전략군을 매핑할지 표시합니다. 자동 자금 라우팅의
        분배 로직 기본값으로 쓰입니다.
      </p>

      {msg && (
        <div
          className={`mt-3 rounded px-3 py-2 text-xs ${
            msg.type === "success"
              ? "border border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
              : "border border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]"
          }`}
        >
          {msg.text}
        </div>
      )}

      <div className="mt-3 flex items-center gap-2">
        <select
          className="flex-1 rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] focus:border-[#2962ff] focus:outline-none"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
        >
          {PURPOSE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving || purpose === wallet.purpose}
          className="rounded bg-[#2962ff] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "저장 중..." : "저장"}
        </button>
      </div>
    </section>
  );
}
