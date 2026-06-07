"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { WalletSyncCard } from "@/components/wallets/WalletSyncCard";
import { useI18n } from "@/lib/i18n";
import {
  deleteBinanceCredential,
  getUserProfile,
  listBinanceCredentials,
  listWalletAccounts,
  setBinanceCredential,
} from "@/lib/api";
import type {
  BinanceCredential,
  BinanceCredentialEnv,
  UserProfile,
  WalletAccount,
} from "@/lib/types";

type TabId = "main" | "sub";

const ENV_ORDER: BinanceCredentialEnv[] = ["mainnet", "testnet"];

const ENV_LABELS: Record<BinanceCredentialEnv, { title: string; desc: string }> = {
  mainnet: {
    title: "Mainnet",
    desc: "실제 바이낸스 (fapi.binance.com / api.binance.com). 마스터 키이며 서브 계정 관리·자금 이체 권한이 필요합니다.",
  },
  testnet: {
    title: "Testnet (Demo Trading)",
    desc: "바이낸스 데모 트레이딩 (demo.binance.com 발급). 키 한 쌍으로 선물·현물 테스트넷에 모두 사용됩니다.",
  },
};

// ── Main account: master credential editor ───────────────────────────

function parseIpInput(raw: string): string[] {
  return raw
    .split(/[\s,]+/g)
    .map((s) => s.trim())
    .filter(Boolean);
}

function CredentialCard({
  cred,
  onChanged,
}: {
  cred: BinanceCredential;
  onChanged: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [ipWhitelist, setIpWhitelist] = useState(
    (cred.ip_whitelist ?? []).join(", "),
  );
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [msg, setMsg] = useState<{ type: "success" | "error"; text: string } | null>(
    null,
  );
  const info = ENV_LABELS[cred.env];
  const isMainnet = cred.env === "mainnet";

  useEffect(() => {
    setIpWhitelist((cred.ip_whitelist ?? []).join(", "));
  }, [cred.ip_whitelist]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim() || !apiSecret.trim()) {
      setMsg({ type: "error", text: "API Key와 Secret을 입력하세요." });
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await setBinanceCredential(cred.env, {
        api_key: apiKey.trim(),
        api_secret: apiSecret.trim(),
        ip_whitelist: isMainnet ? parseIpInput(ipWhitelist) : undefined,
      });
      setApiKey("");
      setApiSecret("");
      setMsg({ type: "success", text: "저장되었습니다." });
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

  const handleDelete = async () => {
    setDeleting(true);
    setMsg(null);
    try {
      await deleteBinanceCredential(cred.env);
      setShowDeleteConfirm(false);
      setMsg({ type: "success", text: "삭제되었습니다." });
      onChanged();
    } catch (err) {
      setMsg({
        type: "error",
        text: err instanceof Error ? err.message : "삭제 실패",
      });
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-[#d1d4dc]">{info.title}</h3>
          <p className="mt-0.5 text-xs leading-snug text-[#868993]">{info.desc}</p>
        </div>
        {cred.configured && (
          <span className="flex shrink-0 items-center gap-1.5 text-xs text-[#26a69a]">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-[#26a69a]" />
            연결됨
          </span>
        )}
      </div>

      {msg && (
        <div
          className={`mb-3 rounded px-3 py-2 text-xs ${
            msg.type === "success"
              ? "border border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
              : "border border-[#ef5350]/30 bg-[#ef5350]/10 text-[#ef5350]"
          }`}
        >
          {msg.text}
        </div>
      )}

      {cred.configured && (
        <div className="mb-3 flex items-center justify-between rounded border border-[#2a2e39] bg-[#131722] px-3 py-2">
          <span className="font-mono text-xs text-[#868993]">
            {cred.api_key_masked ?? "****"}
          </span>
          {showDeleteConfirm ? (
            <div className="flex items-center gap-2">
              <button
                className="rounded bg-[#ef5350] px-2 py-1 text-xs text-white hover:bg-[#ef5350]/80 disabled:opacity-50"
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? "삭제 중..." : "확인"}
              </button>
              <button
                className="rounded px-2 py-1 text-xs text-[#868993] hover:text-[#d1d4dc]"
                onClick={() => setShowDeleteConfirm(false)}
              >
                취소
              </button>
            </div>
          ) : (
            <button
              className="rounded border border-[#ef5350]/30 px-2 py-1 text-xs text-[#ef5350] hover:bg-[#ef5350]/10"
              onClick={() => setShowDeleteConfirm(true)}
            >
              삭제
            </button>
          )}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-3">
        <input
          type="password"
          autoComplete="off"
          placeholder="API Key"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] transition-colors focus:border-[#2962ff] focus:outline-none"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <input
          type="password"
          autoComplete="off"
          placeholder="API Secret"
          className="w-full rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 text-sm text-[#d1d4dc] placeholder-[#4a4e59] transition-colors focus:border-[#2962ff] focus:outline-none"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
        />
        {isMainnet && (
          <div className="space-y-1.5">
            <label className="text-[11px] uppercase tracking-wide text-[#868993]">
              IP Whitelist <span className="normal-case text-[#4a4e59]">(선택)</span>
            </label>
            <textarea
              autoComplete="off"
              placeholder="예) 1.2.3.4, 5.6.7.8 — 바이낸스에 등록한 IP를 메모합니다"
              className="h-20 w-full resize-none rounded border border-[#2a2e39] bg-[#131722] px-3 py-2 font-mono text-xs text-[#d1d4dc] placeholder-[#4a4e59] transition-colors focus:border-[#2962ff] focus:outline-none"
              value={ipWhitelist}
              onChange={(e) => setIpWhitelist(e.target.value)}
            />
            <p className="text-[10px] leading-snug text-[#4a4e59]">
              실제 IP 제한은 바이낸스 콘솔에서 설정합니다. 여기서는 운영자가
              어떤 IP를 등록했는지 알파위버 안에 메모로 남깁니다.
            </p>
          </div>
        )}
        <button
          type="submit"
          disabled={saving || !apiKey.trim() || !apiSecret.trim()}
          className="w-full rounded bg-[#2962ff] px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-[#2962ff]/80 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "검증 중..." : cred.configured ? "업데이트" : "저장 및 연결 확인"}
        </button>
      </form>
    </div>
  );
}

// ── Sub account list (Binance is source of truth) ────────────────────

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
      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
        on
          ? "border border-[#26a69a]/30 bg-[#26a69a]/10 text-[#26a69a]"
          : "border border-[#2a2e39] bg-[#131722] text-[#4a4e59]"
      }`}
      title={`${label}: ${on ? "enabled" : "disabled"}`}
    >
      {label}
    </span>
  );
}

function SubAccountTab(): React.JSX.Element {
  const {
    data: wallets,
    isLoading,
    mutate,
  } = useSWR<WalletAccount[]>("walletAccounts:mainnet", () =>
    listWalletAccounts("mainnet"),
  );

  const subs = useMemo(
    () =>
      (wallets ?? [])
        .filter((w) => w.role === "sub")
        .sort((a, b) => a.alias.localeCompare(b.alias)),
    [wallets],
  );

  return (
    <div className="space-y-6">
      <WalletSyncCard onSynced={() => void mutate()} />

      <section className="rounded-lg border border-[#2a2e39] bg-[#1e222d] p-5">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-[#d1d4dc]">Sub Accounts</h2>
            <p className="mt-1 text-xs leading-snug text-[#868993]">
              서브 계정은{" "}
              <strong className="text-[#d1d4dc]">Binance 콘솔에서만</strong>{" "}
              생성·삭제할 수 있습니다. 위의 &ldquo;지금 동기화&rdquo; 버튼을 누르면
              Binance의 서브 계정 목록·권한·동결 상태가 알파위버로 자동 동기화됩니다.
            </p>
          </div>
        </div>

        {isLoading ? (
          <div className="py-6 text-center text-xs text-[#868993]">로딩 중…</div>
        ) : subs.length === 0 ? (
          <EmptySubState />
        ) : (
          <div className="overflow-hidden rounded border border-[#2a2e39]">
            <table className="min-w-full divide-y divide-[#2a2e39] text-sm">
              <thead className="bg-[#131722] text-[10px] uppercase tracking-wide text-[#868993]">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Alias</th>
                  <th className="px-3 py-2 text-left font-medium">Email</th>
                  <th className="px-3 py-2 text-left font-medium">Status</th>
                  <th className="px-3 py-2 text-left font-medium">Permissions</th>
                  <th className="px-3 py-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#2a2e39] bg-[#1e222d]">
                {subs.map((w) => {
                  const perms = (w.enabled_wallets ?? {}) as Record<string, unknown>;
                  return (
                    <tr key={w.id} className="hover:bg-[#131722]/60">
                      <td className="px-3 py-2 font-medium text-[#d1d4dc]">
                        {w.alias}
                      </td>
                      <td className="px-3 py-2 font-mono text-[11px] text-[#868993]">
                        {w.sub_account_email ?? "—"}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`inline-flex items-center rounded border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${statusBadgeClasses(
                            w.status,
                          )}`}
                        >
                          {statusLabel(w.status)}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          <PermBadge on={Boolean(perms.spot)} label="Spot" />
                          <PermBadge on={Boolean(perms.futures_um)} label="Futures" />
                          <PermBadge on={Boolean(perms.margin)} label="Margin" />
                          <PermBadge on={Boolean(perms.options)} label="Options" />
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Link
                          href={`/settings/wallets/${encodeURIComponent(w.id)}`}
                          className="rounded border border-[#2a2e39] px-2.5 py-1 text-xs text-[#868993] hover:border-[#2962ff]/40 hover:bg-[#2962ff]/10 hover:text-[#2962ff]"
                        >
                          관리 →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function EmptySubState(): React.JSX.Element {
  return (
    <div className="space-y-3 rounded border border-dashed border-[#2a2e39] bg-[#131722] px-4 py-6 text-center">
      <p className="text-sm text-[#d1d4dc]">아직 동기화된 서브 계정이 없습니다.</p>
      <ol className="mx-auto max-w-md space-y-1.5 text-left text-xs text-[#868993]">
        <li>
          1. Binance 콘솔 →{" "}
          <span className="text-[#d1d4dc]">Account → Sub-Accounts → Create</span>{" "}
          로 서브 계정을 생성합니다.
        </li>
        <li>
          2. 같은 화면에서 해당 서브의{" "}
          <strong className="text-[#d1d4dc]">Futures / Margin / Options</strong>{" "}
          권한을 활성화합니다.
        </li>
        <li>
          3. 위의 <strong className="text-[#d1d4dc]">&ldquo;지금 동기화&rdquo;</strong>{" "}
          버튼을 눌러 알파위버로 가져옵니다.
        </li>
        <li>4. 행을 클릭해 거래용 API Key / IP Whitelist를 등록합니다.</li>
      </ol>
    </div>
  );
}

// ── Page shell ───────────────────────────────────────────────────────

function TabButton({
  id,
  current,
  onSelect,
  children,
}: {
  id: TabId;
  current: TabId;
  onSelect: (id: TabId) => void;
  children: React.ReactNode;
}): React.JSX.Element {
  const active = id === current;
  return (
    <button
      type="button"
      onClick={() => onSelect(id)}
      className={`relative px-4 py-2 text-sm font-medium transition-colors ${
        active ? "text-[#d1d4dc]" : "text-[#868993] hover:text-[#d1d4dc]"
      }`}
    >
      {children}
      {active && (
        <span className="absolute inset-x-2 -bottom-px h-0.5 rounded bg-[#2962ff]" />
      )}
    </button>
  );
}

export default function SettingsPage() {
  const { t } = useI18n();
  const search = useSearchParams();
  const initialTab: TabId = search?.get("tab") === "sub" ? "sub" : "main";
  const [tab, setTab] = useState<TabId>(initialTab);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    if (tab === "main") {
      url.searchParams.delete("tab");
    } else {
      url.searchParams.set("tab", tab);
    }
    window.history.replaceState(null, "", url.toString());
  }, [tab]);

  const { data: profile, isLoading: profileLoading } = useSWR<UserProfile>(
    "userProfile",
    () => getUserProfile(),
  );

  const {
    data: credentials,
    mutate: mutateCredentials,
    isLoading: credsLoading,
  } = useSWR<BinanceCredential[]>("binanceCredentials", () =>
    listBinanceCredentials(),
  );

  const loading = profileLoading || credsLoading;

  const credMap = new Map((credentials ?? []).map((c) => [c.env, c]));
  const allCreds: BinanceCredential[] = ENV_ORDER.map(
    (env) => credMap.get(env) ?? { env, configured: false },
  );

  if (loading) {
    return (
      <main className="w-full px-6 py-10">
        <div className="text-[#868993]">{t.settingsPage.loading}</div>
      </main>
    );
  }

  return (
    <main className="w-full max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-[#d1d4dc]">
        {t.settingsPage.title}
      </h1>
      <p className="mt-2 text-sm text-[#868993]">{t.settingsPage.subtitle}</p>

      {profile && (
        <section className="mt-8 rounded-lg border border-[#2a2e39] bg-[#1e222d] p-6">
          <h2 className="mb-4 text-lg font-semibold text-[#d1d4dc]">
            {t.settingsPage.account}
          </h2>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.email}</span>
              <span className="text-[#d1d4dc]">{profile.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.plan}</span>
              <span
                className={
                  profile.plan === "enterprise"
                    ? "font-semibold uppercase text-[#ff9800]"
                    : profile.plan === "pro"
                      ? "font-semibold uppercase text-[#2962ff]"
                      : "uppercase text-[#868993]"
                }
              >
                {profile.plan}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#868993]">{t.settingsPage.userId}</span>
              <span className="font-mono text-xs text-[#868993]">
                {profile.user_id.slice(0, 12)}...
              </span>
            </div>
          </div>
        </section>
      )}

      <section className="mt-8">
        <div className="mb-4 flex items-center gap-1 border-b border-[#2a2e39]">
          <TabButton id="main" current={tab} onSelect={setTab}>
            Main account
          </TabButton>
          <TabButton id="sub" current={tab} onSelect={setTab}>
            Sub account
          </TabButton>
        </div>

        {tab === "main" ? (
          <div>
            <h2 className="mb-1 text-lg font-semibold text-[#d1d4dc]">
              {t.settingsPage.binanceApiKeys}
            </h2>
            <p className="mb-4 text-xs text-[#868993]">
              {t.settingsPage.keysSecureInfo}
            </p>
            <div className="space-y-4">
              {allCreds.map((cred) => (
                <CredentialCard
                  key={cred.env}
                  cred={cred}
                  onChanged={() => void mutateCredentials()}
                />
              ))}
            </div>

            <div className="mt-4 rounded-lg border border-[#2a2e39] bg-[#131722] p-4">
              <h3 className="mb-2 text-xs font-semibold uppercase text-[#868993]">
                {t.settingsPage.securityInfo}
              </h3>
              <ul className="space-y-1 text-xs text-[#868993]">
                <li>• {t.settingsPage.securityItem1}</li>
                <li>• {t.settingsPage.securityItem2}</li>
                <li>• {t.settingsPage.securityItem3}</li>
                <li>• {t.settingsPage.securityItem4}</li>
              </ul>
            </div>
          </div>
        ) : (
          <SubAccountTab />
        )}
      </section>
    </main>
  );
}
