"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  createSubAccount,
  deleteWalletAccount,
  listBinanceCredentials,
  listWalletAccounts,
  setBinanceCredential,
  updateWalletKeys,
  updateWalletStatus,
} from "@/lib/api";
import type {
  BinanceCredential,
  CreateSubAccountInput,
  WalletAccount,
  WalletAccountStatus,
  WalletPurpose,
} from "@/lib/types";
import { WalletSyncCard } from "@/components/wallets/WalletSyncCard";

// ── templates ────────────────────────────────────────────────────────

type SubTemplate = {
  alias: string;
  purpose: WalletPurpose;
  title: string;
  description: string;
  enable_futures: boolean;
  enable_options: boolean;
};

const SUB_TEMPLATES: SubTemplate[] = [
  {
    alias: "directional",
    purpose: "directional",
    title: "Directional Alpha (지향성 알파)",
    description:
      "BTC/ETH 등 USDT-M 선물 디렉셔널 전략 전용. Futures만 활성화하여 다른 전략과 마진/포지션이 섞이지 않습니다.",
    enable_futures: true,
    enable_options: false,
  },
  {
    alias: "arbitrage",
    purpose: "arbitrage",
    title: "Arbitrage (현물-선물 차익)",
    description:
      "Funding-rate, basis, cash-and-carry 차익거래용. Spot + Futures를 모두 사용하므로 두 지갑이 자동으로 활성화됩니다.",
    enable_futures: true,
    enable_options: false,
  },
  {
    alias: "derivatives",
    purpose: "derivatives",
    title: "Derivatives (옵션·복합 파생)",
    description:
      "옵션 + 선물을 결합한 변동성/스킬 전략용. Futures + Options 권한이 필요합니다.",
    enable_futures: true,
    enable_options: true,
  },
  {
    alias: "earn",
    purpose: "earn",
    title: "Earn Reserve (수익형 예비 자금)",
    description:
      "거래 전략에 즉시 투입하지 않는 자금을 Simple Earn에 파킹해 이자를 받기 위한 서브.",
    enable_futures: false,
    enable_options: false,
  },
];

// ── helpers ──────────────────────────────────────────────────────────

function statusBadge(status: WalletAccountStatus): React.JSX.Element {
  const map: Record<WalletAccountStatus, { label: string; cls: string }> = {
    active: { label: "Active", cls: "border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]" },
    disabled: { label: "Disabled", cls: "border-[#868993]/30 bg-[#868993]/10 text-[#868993]" },
    key_missing: {
      label: "Key Missing",
      cls: "border-[#F0B90B]/30 bg-[#F0B90B]/10 text-[#F0B90B]",
    },
    key_invalid: {
      label: "Key Invalid",
      cls: "border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]",
    },
    binance_missing: {
      label: "Binance Missing",
      cls: "border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]",
    },
  };
  const meta = map[status] ?? map.disabled;
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${meta.cls}`}
    >
      {meta.label}
    </span>
  );
}

function purposeBadge(purpose: WalletPurpose): React.JSX.Element {
  const labels: Record<WalletPurpose, string> = {
    generic: "Generic",
    directional: "Directional",
    arbitrage: "Arbitrage",
    derivatives: "Derivatives",
    earn: "Earn",
    copy_trading: "Copy",
  };
  return (
    <span className="inline-flex items-center rounded bg-[#2a2e39] px-2 py-0.5 text-[10px] font-medium text-[#a0a3ad]">
      {labels[purpose] ?? purpose}
    </span>
  );
}

function MessageBox({
  message,
}: {
  message: { type: "success" | "error"; text: string } | null;
}): React.JSX.Element | null {
  if (!message) return null;
  return (
    <div
      className={`rounded px-3 py-2 text-xs ${
        message.type === "success"
          ? "border border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
          : "border border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]"
      }`}
    >
      {message.text}
    </div>
  );
}

// ── step 1: master key ───────────────────────────────────────────────

function MasterKeyStep({
  mainnetCred,
  onChanged,
}: {
  mainnetCred: BinanceCredential | undefined;
  onChanged: () => void;
}): React.JSX.Element {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const handleSave = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      setMsg({ type: "error", text: "API Key와 Secret을 모두 입력하세요." });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await setBinanceCredential("mainnet", {
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
      });
      setApiKey("");
      setApiSecret("");
      setMsg({ type: "success", text: "마스터 키가 저장되었습니다." });
      onChanged();
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
    <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <header className="mb-4">
        <h2 className="text-base font-semibold text-[#d1d4dc]">
          Step 1 · 마스터 키 등록
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-[#868993]">
          서브 계정 생성과 자금 이동(universal transfer)은 마스터 키로만 가능합니다.
          반드시 IP 화이트리스트가 적용된 키여야 하며, 권한은{" "}
          <code className="text-[#d1d4dc]">Read · Enable Internal Transfer · Enable Universal Transfer</code>{" "}
          만 필요합니다 (거래 권한 비활성 권장).
        </p>
      </header>

      <MessageBox message={msg} />

      {mainnetCred?.configured ? (
        <div className="mt-3 flex items-center justify-between rounded border border-[#26a69a]/30 bg-[#26a69a]/10 px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
            <span className="text-sm text-[#26a69a]">마스터 키 연결됨</span>
            <span className="font-mono text-xs text-[#868993]">
              {mainnetCred.api_key_masked ?? "****"}
            </span>
          </div>
        </div>
      ) : null}

      <form onSubmit={handleSave} className="mt-3 space-y-3">
        <input
          type="password"
          autoComplete="off"
          placeholder="Master API Key"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] transition-colors focus:border-[#2962ff] focus:outline-none"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <input
          type="password"
          autoComplete="off"
          placeholder="Master API Secret"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] transition-colors focus:border-[#2962ff] focus:outline-none"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
        />
        <button
          type="submit"
          disabled={saving || !apiKey.trim() || !apiSecret.trim()}
          className="w-full rounded bg-[#2962ff] px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "저장 중..." : mainnetCred?.configured ? "교체 저장" : "마스터 키 저장"}
        </button>
      </form>
    </section>
  );
}

// ── step 2: create subs ──────────────────────────────────────────────

function CreateSubsStep({
  wallets,
  disabled,
  onChanged,
}: {
  wallets: WalletAccount[];
  disabled: boolean;
  onChanged: () => void;
}): React.JSX.Element {
  const [creating, setCreating] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const subsByAlias = useMemo(() => {
    const map = new Map<string, WalletAccount>();
    for (const w of wallets) {
      if (w.role === "sub") map.set(w.alias.toLowerCase(), w);
    }
    return map;
  }, [wallets]);

  const handleCreate = async (template: SubTemplate): Promise<void> => {
    if (disabled) return;
    setCreating(template.alias);
    setMsg(null);
    try {
      const body: CreateSubAccountInput = {
        alias: template.alias,
        purpose: template.purpose,
        env: "mainnet",
        enable_futures: template.enable_futures,
        enable_options: template.enable_options,
      };
      await createSubAccount(body);
      setMsg({
        type: "success",
        text: `${template.title} 서브 생성 완료. Step 3에서 거래 키를 입력하세요.`,
      });
      onChanged();
    } catch (err) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "서브 생성 실패",
      });
    } finally {
      setCreating(null);
    }
  };

  return (
    <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <header className="mb-4">
        <h2 className="text-base font-semibold text-[#d1d4dc]">
          Step 2 · 전략별 서브 계정 자동 생성
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-[#868993]">
          마스터 키로 virtual sub-account를 생성합니다. 한 alias 당 하나씩만 생성되며,
          이미 생성된 항목은 비활성화됩니다. 모든 전략을 한 번에 시작할 필요는 없으니
          필요한 것만 골라서 생성하세요.
        </p>
      </header>

      <MessageBox message={msg} />

      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
        {SUB_TEMPLATES.map((tpl) => {
          const existing = subsByAlias.get(tpl.alias.toLowerCase());
          return (
            <div
              key={tpl.alias}
              className="rounded border border-[#2a2e39] bg-[#131722] p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold text-[#d1d4dc]">
                    {tpl.title}
                  </h3>
                  <p className="mt-1 text-[11px] leading-snug text-[#868993]">
                    {tpl.description}
                  </p>
                </div>
                {existing ? statusBadge(existing.status) : null}
              </div>
              <div className="mt-3 flex items-center justify-between">
                <div className="flex gap-1.5">
                  {tpl.enable_futures ? (
                    <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#a0a3ad]">
                      Futures
                    </span>
                  ) : null}
                  {tpl.enable_options ? (
                    <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#a0a3ad]">
                      Options
                    </span>
                  ) : null}
                  {!tpl.enable_futures && !tpl.enable_options ? (
                    <span className="rounded bg-[#2a2e39] px-1.5 py-0.5 text-[10px] text-[#a0a3ad]">
                      Spot only
                    </span>
                  ) : null}
                </div>
                {existing ? (
                  <span className="text-[10px] text-[#868993]">
                    {existing.sub_account_email?.split("@")[0] ?? "—"}
                  </span>
                ) : (
                  <button
                    type="button"
                    disabled={disabled || creating !== null}
                    onClick={() => void handleCreate(tpl)}
                    className="rounded bg-[#2962ff] px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-[#2962ff]/80 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {creating === tpl.alias ? "생성 중..." : "생성"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ── step 3: trading keys per sub ─────────────────────────────────────

function SubKeyForm({
  wallet,
  onChanged,
}: {
  wallet: WalletAccount;
  onChanged: () => void;
}): React.JSX.Element {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [ipText, setIpText] = useState((wallet.ip_whitelist ?? []).join(", "));
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const handleSave = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      setMsg({ type: "error", text: "API Key와 Secret을 모두 입력하세요." });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      const ipWhitelist = ipText
        .split(/[\s,]+/)
        .map((x) => x.trim())
        .filter((x) => x.length > 0);
      await updateWalletKeys(wallet.id, {
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        ip_whitelist: ipWhitelist.length > 0 ? ipWhitelist : undefined,
        mark_active: true,
      });
      setApiKey("");
      setApiSecret("");
      setMsg({ type: "success", text: "거래 키가 저장되었습니다." });
      onChanged();
    } catch (err) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "저장 실패",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleToggleStatus = async (
    next: WalletAccountStatus,
  ): Promise<void> => {
    try {
      await updateWalletStatus(wallet.id, next);
      onChanged();
    } catch (err) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "상태 변경 실패",
      });
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (
      !confirm(
        `정말로 ${wallet.alias} 서브 매핑을 삭제하시겠습니까?\n(Binance 측 서브 계정은 유지되며, DB 매핑만 제거됩니다.)`,
      )
    ) {
      return;
    }
    try {
      await deleteWalletAccount(wallet.id);
      onChanged();
    } catch (err) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "삭제 실패",
      });
    }
  };

  return (
    <div className="rounded border border-[#2a2e39] bg-[#131722] p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-[#d1d4dc]">{wallet.alias}</h3>
            {purposeBadge(wallet.purpose)}
            {statusBadge(wallet.status)}
          </div>
          <p className="mt-1 font-mono text-[11px] text-[#868993]">
            {wallet.sub_account_email ?? "—"}
          </p>
          {wallet.api_key_masked ? (
            <p className="mt-0.5 font-mono text-[11px] text-[#868993]">
              key: {wallet.api_key_masked}
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          {wallet.status === "active" ? (
            <button
              type="button"
              className="rounded border border-[#868993]/30 px-2 py-0.5 text-[10px] text-[#868993] hover:bg-[#868993]/10"
              onClick={() => void handleToggleStatus("disabled")}
            >
              Disable
            </button>
          ) : (
            <button
              type="button"
              className="rounded border border-[#26a69a]/30 px-2 py-0.5 text-[10px] text-[#26a69a] hover:bg-[#26a69a]/10"
              onClick={() => void handleToggleStatus("active")}
            >
              Enable
            </button>
          )}
          <button
            type="button"
            className="rounded border border-[#ef5350]/30 px-2 py-0.5 text-[10px] text-[#ef5350] hover:bg-[#ef5350]/10"
            onClick={() => void handleDelete()}
          >
            Delete
          </button>
        </div>
      </div>

      <MessageBox message={msg} />

      <form onSubmit={handleSave} className="mt-3 space-y-2">
        <input
          type="password"
          autoComplete="off"
          placeholder="Sub Trading API Key"
          className="w-full rounded border border-[#2a2e39] bg-[#0d1118] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <input
          type="password"
          autoComplete="off"
          placeholder="Sub Trading API Secret"
          className="w-full rounded border border-[#2a2e39] bg-[#0d1118] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
        />
        <input
          type="text"
          placeholder="IP whitelist (콤마 구분, 예: 1.2.3.4, 5.6.7.8)"
          className="w-full rounded border border-[#2a2e39] bg-[#0d1118] px-3 py-2 text-xs text-[#d1d4dc] placeholder-[#4a4e59] focus:border-[#2962ff] focus:outline-none"
          value={ipText}
          onChange={(e) => setIpText(e.target.value)}
        />
        <button
          type="submit"
          disabled={saving || !apiKey.trim() || !apiSecret.trim()}
          className="w-full rounded bg-[#2962ff] px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-[#2962ff]/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "저장 중..." : wallet.api_key_masked ? "키 교체" : "거래 키 저장"}
        </button>
      </form>
    </div>
  );
}

function TradingKeysStep({
  wallets,
  onChanged,
}: {
  wallets: WalletAccount[];
  onChanged: () => void;
}): React.JSX.Element {
  const subs = wallets.filter((w) => w.role === "sub");

  return (
    <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <header className="mb-4">
        <h2 className="text-base font-semibold text-[#d1d4dc]">
          Step 3 · 서브 계정별 거래 키 등록
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-[#868993]">
          Binance 정책상 서브 계정의 거래(spot/futures) 키는 마스터 권한으로 자동 발급할 수
          없습니다.{" "}
          <a
            href="https://www.binance.com/en/my/security/api-management"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[#2962ff] underline"
          >
            Binance Sub-account API Management
          </a>{" "}
          페이지에서 각 서브 별로 키를 발급한 뒤, 아래 폼에 입력하세요. 저장 시
          IP 화이트리스트가 Binance 쪽에 자동 적용됩니다.
        </p>
      </header>

      {subs.length === 0 ? (
        <p className="text-xs text-[#868993]">
          먼저 Step 2에서 서브 계정을 생성하세요.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {subs.map((w) => (
            <SubKeyForm key={w.id} wallet={w} onChanged={onChanged} />
          ))}
        </div>
      )}
    </section>
  );
}

// ── progress summary ─────────────────────────────────────────────────

function ProgressSummary({
  wallets,
  hasMasterKey,
}: {
  wallets: WalletAccount[];
  hasMasterKey: boolean;
}): React.JSX.Element {
  const subs = wallets.filter((w) => w.role === "sub");
  const active = subs.filter((w) => w.status === "active").length;
  const total = SUB_TEMPLATES.length;

  return (
    <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <h2 className="text-base font-semibold text-[#d1d4dc]">진행 상태</h2>
      <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
        <Indicator label="마스터 키" value={hasMasterKey ? "✓" : "—"} ok={hasMasterKey} />
        <Indicator
          label="서브 생성"
          value={`${subs.length} / ${total}`}
          ok={subs.length === total}
        />
        <Indicator
          label="활성 서브"
          value={`${active} / ${subs.length || total}`}
          ok={active > 0 && active === subs.length}
        />
      </div>
      <p className="mt-3 text-xs text-[#868993]">
        활성 서브가 1개 이상이 되면, Jobs 페이지에서 각 라이브 잡에 서브 지갑과 USDT
        예산을 할당할 수 있습니다. 할당된 잡은 자동으로 Pre-trade Capital Allocator
        게이트가 활성화됩니다.
      </p>
    </section>
  );
}

function Indicator({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok: boolean;
}): React.JSX.Element {
  return (
    <div
      className={`rounded border px-3 py-2 ${
        ok
          ? "border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
          : "border-[#2a2e39] bg-[#131722] text-[#868993]"
      }`}
    >
      <div className="text-[10px] uppercase tracking-wide">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

// ── page ─────────────────────────────────────────────────────────────

export default function WalletOnboardingPage(): React.JSX.Element {
  const {
    data: credentials,
    mutate: mutateCreds,
    isLoading: credsLoading,
  } = useSWR<BinanceCredential[]>("binanceCredentials", () =>
    listBinanceCredentials(),
  );

  const {
    data: wallets,
    mutate: mutateWallets,
    isLoading: walletsLoading,
  } = useSWR<WalletAccount[]>("walletAccounts", () =>
    listWalletAccounts("mainnet"),
  );

  const mainnetCred = (credentials ?? []).find((c) => c.env === "mainnet");
  const hasMasterKey = Boolean(mainnetCred?.configured);

  const handleChanged = async (): Promise<void> => {
    await Promise.all([mutateCreds(), mutateWallets()]);
  };

  if (credsLoading || walletsLoading) {
    return (
      <main className="w-full max-w-4xl px-6 py-10">
        <div className="text-[#868993]">로드 중...</div>
      </main>
    );
  }

  return (
    <main className="w-full max-w-4xl px-6 py-10">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-[#d1d4dc]">
          Sub-account 온보딩
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-[#868993]">
          여러 거래 전략(디렉셔널·차익·파생·Earn)을 병행 운영할 때 자금이 충돌하지
          않도록, 각 전략을 별도의 Binance 서브 계정에 배치합니다. 마스터 키 1개로
          모든 서브 계정을 생성·통제하고, 자금은 Capital Router가 자동으로
          마스터↔서브 사이를 이동시킵니다.{" "}
          <Link href="/settings" className="text-[#2962ff] underline">
            Settings 페이지
          </Link>
          에서 일반 키 관리를 할 수 있습니다.
        </p>
      </header>

      <div className="space-y-5">
        <ProgressSummary wallets={wallets ?? []} hasMasterKey={hasMasterKey} />
        <WalletSyncCard onSynced={() => void handleChanged()} />
        <MasterKeyStep
          mainnetCred={mainnetCred}
          onChanged={() => void handleChanged()}
        />
        <CreateSubsStep
          wallets={wallets ?? []}
          disabled={!hasMasterKey}
          onChanged={() => void handleChanged()}
        />
        <TradingKeysStep
          wallets={wallets ?? []}
          onChanged={() => void handleChanged()}
        />
      </div>
    </main>
  );
}
